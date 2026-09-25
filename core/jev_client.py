# -*- coding: utf-8 -*-
"""Jev 判断 API 客户端。

判断层只保留两种底层协议：
- OpenRouter：POST /api/alpha/decisions
- System One：POST /v1/systemone

TypeSafe、博查、Vercel、OpenCode Zen、硅基流动和自定义 System One
全部走同一个 System One 发送函数。engine 不需要知道厂商差异。
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
    import io
    from unittest.mock import patch

    try:
        from .questions import JUDGE_QUESTIONS
    except ImportError:
        from questions import JUDGE_QUESTIONS

    os.environ.pop(JEV_ENV, None)
    os.environ["OPENROUTER_API_KEY"] = "legacy-key"
    assert _api_key(JEV_ENV) == "legacy-key"
    os.environ[JEV_ENV] = "new-key"

    seen = {}

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def _fake_systemone(req, timeout=None):
        seen["url"] = req.full_url
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return _Resp(json.dumps({
            "answers": {"literal_question": {"type": "noul", "noul": 0.9}},
            "usage": {"input_tokens": 12, "output_tokens": 3},
        }).encode("utf-8"))

    with patch.object(urllib.request, "urlopen", _fake_systemone):
        got = ask(
            {"chat": {}},
            dict(JUDGE_QUESTIONS),
            provider="siliconflow_jev",
            model="diffusiongemma",
        )
    assert seen["url"] == "https://api.siliconflow.cn/v1/systemone"
    assert seen["body"]["model"] == "diffusiongemma"
    assert got["answers"]["literal_question"]["noul"] == 0.9

    with patch.object(urllib.request, "urlopen", _fake_systemone):
        ask(
            {"chat": {}},
            dict(JUDGE_QUESTIONS),
            provider="custom_systemone",
            model="my-jev",
            base_url="https://example.com/custom/v1/systemone",
        )
    assert seen["url"] == "https://example.com/custom/v1/systemone"

    assert list_models("zen", "x") == ["jev-1.13", "jev-1.13-free"]
    assert list_models("siliconflow_jev", "x") == ["diffusiongemma", "Kev-4b", "SemIf"]
    assert redact_secrets("new-key legacy-key") == "[REDACTED] [REDACTED]"
    print("jev_client ok")
