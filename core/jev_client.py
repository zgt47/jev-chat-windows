# -*- coding: utf-8 -*-
"""Jev 判断 API 客户端：OpenRouter 或 TypeSafe 直连。

TypeSafe 直连走官方 `typesafe_sdk`；OpenRouter 这条是唯一自己拼 HTTP 的路——
SDK 把路径写死成 `/v1/systemone`，打不到 OpenRouter 的 `/api/alpha/decisions`。
两条路返回同一个 dict 形状，engine 不关心跑的是哪条。key 只从环境变量读，绝不打进日志。
"""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from typing import NoReturn

try:  # 当模块导入 / 当脚本直接跑 都能用
    from .providers import (ENV_VARS, JEV_ENV, JEV_PROVIDERS, LEGACY,
                            OPENROUTER_DECISIONS, OPENROUTER_KEY_URL, TYPESAFE_BASE)
except ImportError:
    from providers import (ENV_VARS, JEV_ENV, JEV_PROVIDERS, LEGACY,
                           OPENROUTER_DECISIONS, OPENROUTER_KEY_URL, TYPESAFE_BASE)

MAX_RETRIES = 3


class JevError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def redact_secrets(text: str) -> str:
    """Strip every live key from any string before print or disk write."""
    if not isinstance(text, str):
        text = str(text)
    for env in ENV_VARS:
        key = os.environ.get(env) or ""
        if key:
            text = text.replace(key, "[REDACTED]")
    return text


def _status_of(exc: Exception) -> int | None:
    """各家 SDK 放 HTTP 状态码的属性名不一样：openai/anthropic 是 status_code，
    google-genai 是 code（它的 status 是 'NOT_FOUND' 这种字符串），typesafe 是 status。"""
    for name in ("status_code", "code", "status"):
        value = getattr(exc, name, None)
        if isinstance(value, int):
            return value
    return None


def _fail(exc: Exception, what: str) -> NoReturn:
    """SDK 抛的异常 → 一句人话的 JevError。消息过脱敏，绝不把 key 带出来。"""
    if isinstance(exc, JevError):
        raise exc
    status = _status_of(exc)
    hint = {401: "密钥被拒", 403: "没有权限", 404: "模型或地址不对", 422: "请求被拒",
            429: "被限流", 529: "服务过载"}.get(status, "")
    detail = redact_secrets(str(exc)).strip()[:300]
    head = f"{what} HTTP {status}" if status else f"{what}失败"
    raise JevError(f"{head}: {hint or detail or type(exc).__name__}", status) from None


def _api_key(env: str = JEV_ENV) -> str:
    """两把 key 之一（JEV_API_KEY / LLM_API_KEY）。新名字空着就退回老名字，老用户不用重填。"""
    key = ((os.environ.get(env) or "").strip()
           or (os.environ.get(LEGACY.get(env, "")) or "").strip())
    if not key:
        raise JevError(
            f"{env} is not set. Export it in the environment; "
            "do not put the key in a file."
        )
    return key


def _error_body(exc: urllib.error.HTTPError) -> str:
    try:
        raw = exc.read().decode("utf-8", errors="replace")
    except Exception:
        raw = ""
    return redact_secrets(raw)[:800]


def _systemone_url(base_url: str) -> str:
    """用户既可以填服务根地址，也可以直接填完整的 /v1/systemone 地址。"""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise JevError("System One Base URL 为空")
    if base.endswith("/v1/systemone") or base.endswith("/systemone"):
        return base
    if base.endswith("/v1"):
        return base + "/systemone"
    return base + "/v1/systemone"


def _post_json(url: str, payload: dict, key: str, timeout: float, what: str) -> dict:
    """统一的 Bearer + JSON POST；429/529/5xx 做有限退避重试。"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last_status: int | None = None
    last_body = ""

    for attempt in range(MAX_RETRIES + 1):
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
                "User-Agent": "jev-chat-windows",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                out = json.loads(raw)
                if not isinstance(out, dict):
                    raise JevError(f"{what}返回格式不正确")
                return out
        except urllib.error.HTTPError as exc:
            last_status = exc.code
            last_body = _error_body(exc)
            retryable = last_status in (408, 429, 529) or 500 <= last_status <= 599
            if retryable and attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
                continue
            hint = {
                400: "请求格式不兼容",
                401: "密钥被拒",
                403: "没有权限",
                404: "模型或地址不对",
                422: "请求被拒",
                429: "被限流",
                529: "服务过载",
            }.get(last_status, last_body or "请求失败")
            raise JevError(f"{what} HTTP {last_status}: {hint}", last_status) from None
        except (TimeoutError, socket.timeout) as exc:
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
                continue
            raise JevError(f"{what}请求超时（{timeout}s）") from exc
        except urllib.error.URLError as exc:
            reason = redact_secrets(getattr(exc, "reason", exc))
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
                continue
            raise JevError(f"{what}连接失败: {reason}") from None
        except json.JSONDecodeError:
            raise JevError(f"{what}返回的不是有效 JSON") from None

    raise JevError(f"{what} HTTP {last_status}: {last_body}", last_status)


def ask(
    state: dict,
    questions: dict,
    timeout: float = 20,
    provider: str = "openrouter",
    model: str | None = None,
    base_url: str | None = None,
) -> dict:
    """问 Jev 一轮判断，统一返回 {"answers": {...}, "usage": {...}}。"""
    spec = JEV_PROVIDERS.get(provider) or JEV_PROVIDERS["openrouter"]
    key = _api_key(JEV_ENV)
    model = (model or spec.default or "").strip()
    if not model:
        raise JevError("判断模型名称为空")
    payload = {"model": model, "state": state, "questions": questions}
    if spec.protocol == "openrouter":
        return _post_json(OPENROUTER_DECISIONS, payload, key, timeout, "Jev 判断")
    base = (base_url or spec.base or "").strip()
    return _post_json(_systemone_url(base), payload, key, timeout, "Jev 判断")


def _check_openrouter_key(key: str, timeout: float) -> None:
    req = urllib.request.Request(
        OPENROUTER_KEY_URL,
        headers={"Authorization": f"Bearer {key}", "User-Agent": "jev-chat-windows"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        hint = {401: "密钥被拒", 403: "没有权限"}.get(exc.code, _error_body(exc)[:200])
        raise JevError(f"取模型列表 HTTP {exc.code}: {hint}") from None
    except (TimeoutError, socket.timeout):
        raise JevError(f"取模型列表请求超时（{timeout}s）") from None
    except urllib.error.URLError as exc:
        raise JevError(f"取模型列表失败: {redact_secrets(getattr(exc, 'reason', exc))}") from None


def list_models(
    provider: str,
    key: str,
    timeout: float = 10,
    base_url: str | None = None,
) -> list[str]:
    spec = JEV_PROVIDERS.get(provider)
    if not spec:
        raise JevError("未知的判断来源")
    if provider == "openrouter":
        _check_openrouter_key(key, timeout)
        return list(spec.models or ())
    if spec.models:
        return list(spec.models)
    if provider == "typesafe":
        import typesafe_sdk
        try:
            with typesafe_sdk.TypeSafeClient(api_key=key, base_url=TYPESAFE_BASE, timeout=timeout) as client:
                return sorted({m.name for m in client.models.list().models})
        except Exception as exc:
            _fail(exc, "取模型列表")
    if provider == "custom_systemone":
        if not (base_url or "").strip():
            raise JevError("先填 Base URL")
        raise JevError("自定义 System One 无法自动判断模型列表，请直接手动输入模型名称")
    return []


if __name__ == "__main__":
    # ponytail: 不联网。两条路各测一次：SDK 那条在 typesafe_sdk 边界换成假客户端，
    # urllib 那条 mock urlopen。会坏的地方就一个——答案对象 → dict 的映射得跟 JSON 那条一模一样。
    import io
    import types as _t
    from unittest.mock import patch

    import typesafe_sdk

    try:
        from .questions import JUDGE_QUESTIONS, build_rank_question
    except ImportError:
        from questions import JUDGE_QUESTIONS, build_rank_question

    os.environ.pop(JEV_ENV, None)
    os.environ["OPENROUTER_API_KEY"] = "or-key"  # 老名字：新名字没设时该退回它
    assert _api_key(JEV_ENV) == "or-key"
    os.environ[JEV_ENV] = "ts-key"  # 新名字在就用新的，两家来源共用这一把
    questions = dict(JUDGE_QUESTIONS)
    questions.update(build_rank_question(["甲", "乙", "丙"]))
    seen: dict = {}

    class _FakeClient:
        def __init__(self, **kw):
            seen["init"] = kw

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def system_one(self, state, qs, **kw):
            seen["state"], seen["questions"], seen["kw"] = state, qs, kw
            return _t.SimpleNamespace(
                answers={
                    "literal_question": _t.SimpleNamespace(type="noul", noul=0.9),
                    "best_reply": _t.SimpleNamespace(
                        type="choice", choice="reply_b", confidence=0.7,
                        probabilities={"reply_a": 0.2, "reply_b": 0.7, "reply_c": 0.1}),
                    "danger_level": _t.SimpleNamespace(
                        type="score", score=4.0, confidence=0.6, probabilities={4: 0.6, 5: 0.4}),
                },
                usage=_t.SimpleNamespace(input_tokens=11, output_tokens=22))

        @property
        def models(self):
            return _t.SimpleNamespace(list=lambda: _t.SimpleNamespace(models=(
                _t.SimpleNamespace(name="jev-preview"), _t.SimpleNamespace(name="jev-latest"))))

    with patch.object(typesafe_sdk, "TypeSafeClient", _FakeClient):
        got = ask({"chat": {}}, questions, timeout=15, provider="typesafe", model="jev-1.13.0")
        ask_init = seen["init"]
        assert list_models("typesafe", "ts-key") == ["jev-latest", "jev-preview"]
        assert seen["init"] == {"api_key": "ts-key", "base_url": TYPESAFE_BASE, "timeout": 10}
    assert ask_init == {"api_key": "ts-key", "base_url": TYPESAFE_BASE,
                        "model": "jev-1.13.0", "timeout": 15}
    assert seen["kw"] == {"model": "jev-1.13.0"}
    # 题目原样进 SDK：它们本身就是 NoulModel / ChoiceModel / ScoreModel，不用再包一层
    assert seen["questions"] is questions
    assert seen["questions"]["danger_level"]["type"] == "score"
    assert isinstance(seen["questions"]["danger_level"]["criteria"], list)
    assert seen["questions"]["best_reply"]["criteria"] == {
        "reply_a": "甲", "reply_b": "乙", "reply_c": "丙"}
    # 映射出来的形状跟 OpenRouter 那条路的 JSON 必须一致（engine 不关心跑的是哪条）
    assert got["answers"]["literal_question"] == {"type": "noul", "noul": 0.9}
    assert got["answers"]["best_reply"] == {
        "type": "choice", "choice": "reply_b", "confidence": 0.7,
        "probabilities": {"reply_a": 0.2, "reply_b": 0.7, "reply_c": 0.1}}
    assert got["answers"]["danger_level"] == {
        "type": "score", "score": 4.0, "confidence": 0.6,
        "probabilities": {"4": 0.6, "5": 0.4}}  # score 的概率 key 转回字符串
    assert got["usage"] == {"input_tokens": 11, "output_tokens": 22}

    class _Boom(Exception):
        status = 429

    with patch.object(typesafe_sdk, "TypeSafeClient", lambda **kw: (_ for _ in ()).throw(_Boom("x"))):
        try:
            ask({"chat": {}}, questions, provider="typesafe")
            raise SystemExit("应当抛错")
        except JevError as e:
            assert e.status == 429 and "被限流" in str(e)

    # OpenRouter 那条没动：还是自己拼 body、打 /api/alpha/decisions
    body = {"answers": {"best_reply": {"type": "choice", "choice": "reply_a"}}, "usage": {}}

    def _fake_urlopen(req, timeout=None):
        seen["url"], seen["body"] = req.full_url, json.loads(req.data.decode("utf-8"))
        return io.BytesIO(json.dumps(body).encode("utf-8"))

    with patch.object(urllib.request, "urlopen", _fake_urlopen):
        assert ask({"chat": {}}, questions) == body
    assert seen["url"] == OPENROUTER_DECISIONS
    assert seen["body"]["model"] == "typesafe/jev-1.13" and seen["body"]["questions"] == questions

    # OpenRouter 路的列表是写死的，但 key 要过 auth/key 探测：mock urlopen 验两头
    def _fake_key_ok(req, timeout=None):
        seen["key_url"] = req.full_url
        assert req.headers["Authorization"] == "Bearer or-key"
        return io.BytesIO(b'{"data":{}}')

    with patch.object(urllib.request, "urlopen", _fake_key_ok):
        assert list_models("openrouter", "or-key") == [
            "~typesafe/jev-latest", "typesafe/jev-1.13"]
    assert seen["key_url"] == OPENROUTER_KEY_URL

    def _fake_key_rejected(req, timeout=None):
        raise urllib.error.HTTPError(OPENROUTER_KEY_URL, 401, "Unauthorized", {},
                                     io.BytesIO(b'{"error":{"message":"bad key or-key"}}'))

    with patch.object(urllib.request, "urlopen", _fake_key_rejected):
        try:
            list_models("openrouter", "or-key")
            raise SystemExit("应当抛错")
        except JevError as e:
            assert e.status is None and "密钥被拒" in str(e) and "or-key" not in str(e)

    # 401/403 以外的状态码要把响应体带出来（别提前 read 把流吃空）
    def _fake_key_429(req, timeout=None):
        raise urllib.error.HTTPError(OPENROUTER_KEY_URL, 429, "Too Many", {},
                                     io.BytesIO(b'{"error":{"message":"rate limited"}}'))

    with patch.object(urllib.request, "urlopen", _fake_key_429):
        try:
            list_models("openrouter", "or-key")
            raise SystemExit("应当抛错")
        except JevError as e:
            assert "HTTP 429" in str(e) and "rate limited" in str(e)

    assert redact_secrets("key=ts-key or-key") == "key=[REDACTED] [REDACTED]"
    print("jev_client ok")
