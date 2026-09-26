# -*- coding: utf-8 -*-
"""设置持久化。

密钥硬约束：
- 只进 Windows 用户环境变量；
- 绝不写进 config.json。

全程只有两把 key：
- 判断：JEV_API_KEY
- 起草：LLM_API_KEY
"""
from __future__ import annotations

import ctypes
import json
import os
import sys

from core.providers import (
    CUSTOM,
    DRAFT_PROVIDERS,
    JEV_CUSTOM,
    JEV_ENV,
    JEV_PROVIDERS,
    LEGACY,
    LLM_ENV,
)

_ROOT = (
    os.path.dirname(sys.executable)
    if getattr(sys, "frozen", False)
    else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_CONFIG = os.path.join(_ROOT, "config.json")
_DEFAULT_RELATIONSHIP = "romantic partners"
_DEFAULT_CONTEXT = 10
_DEFAULT_JEV = "openrouter"
_DEFAULT_DRAFT = "deepseek"
_DEFAULT_ALWAYS_ON_TOP = True
_DEFAULT_AUTO_ANALYZE = True
_DEFAULT_AUTO_SEND = False
_DEFAULT_AUTO_SEND_DELAY = 2
_DEFAULT_TRANSPARENCY = 0
_DEFAULT_RECORD_HISTORY = False
_DEFAULT_HISTORY_LIMIT = 30


def _read(name: str, default=None):
    try:
        with open(_CONFIG, encoding="utf-8") as f:
            value = json.load(f).get(name)
    except (OSError, ValueError):
        return default
    return default if value is None else value


def relationship() -> str:
    return str(_read("relationship") or _DEFAULT_RELATIONSHIP)


def context() -> int:
    try:
        n = int(_read("context", _DEFAULT_CONTEXT))
    except (TypeError, ValueError):
        return _DEFAULT_CONTEXT
    return max(3, min(30, n))


def style() -> str:
    return str(_read("style") or "")


def jev_provider() -> str:
    v = _read("jev_provider")
    return v if v in JEV_PROVIDERS else _DEFAULT_JEV


def jev_model() -> str:
    return str(_read("jev_model") or "") or JEV_PROVIDERS[jev_provider()].default


def jev_custom_base_url() -> str:
    """自定义 System One 上次保存的地址；即使当前切到预设来源也保留。"""
    return str(_read("jev_base_url") or "").strip()


def jev_base_url() -> str:
    """当前判断服务实际使用的地址。"""
    provider = jev_provider()
    if provider in JEV_CUSTOM:
        return jev_custom_base_url()
    return str(JEV_PROVIDERS[provider].base or "").strip()


def draft_provider() -> str:
    v = _read("draft_provider")
    return v if v in DRAFT_PROVIDERS else _DEFAULT_DRAFT


def draft_provider_name() -> str:
    return DRAFT_PROVIDERS[draft_provider()].name


def draft_model() -> str:
    return str(_read("draft_model") or "") or DRAFT_PROVIDERS[draft_provider()].default


def draft_base_url() -> str:
    return str(_read("draft_base_url") or "") if draft_provider() in CUSTOM else ""


def reply_target() -> bool:
    return bool(_read("reply_target", False))


def thinking() -> bool:
    return bool(_read("thinking", False))


def check_update() -> bool:
    return bool(_read("check_update", True))


def debug_view() -> bool:
    return bool(_read("debug_view", False))


def always_on_top() -> bool:
    return bool(_read("always_on_top", _DEFAULT_ALWAYS_ON_TOP))


def auto_analyze() -> bool:
    return bool(_read("auto_analyze", _DEFAULT_AUTO_ANALYZE))


def auto_send() -> bool:
    return bool(_read("auto_send", _DEFAULT_AUTO_SEND))


def auto_send_delay() -> int:
    try:
        n = int(_read("auto_send_delay", _DEFAULT_AUTO_SEND_DELAY))
    except (TypeError, ValueError):
        n = _DEFAULT_AUTO_SEND_DELAY
    return max(1, min(10, n))


def whitelist() -> list[str]:
    value = _read("whitelist", [])
    if isinstance(value, str):
        value = value.splitlines()
    if not isinstance(value, list):
        return []
    return [str(x).strip() for x in value if str(x).strip()]


def chat_allowed(title: str) -> bool:
    keys = whitelist()
    if not keys:
        return True
    title = str(title or "").strip().lower()
    return any(k.lower() in title for k in keys)


def transparency() -> int:
    """界面透明度：0=完全不透明，40=最多 40% 透明。

    新版不继承旧的“不透明度”设置，避免升级后界面一启动就过于透明。
    """
    try:
        n = int(_read("overlay_transparency", _DEFAULT_TRANSPARENCY))
    except (TypeError, ValueError):
        n = _DEFAULT_TRANSPARENCY
    return max(0, min(40, n))


def record_history() -> bool:
    return bool(_read("record_history", _DEFAULT_RECORD_HISTORY))


def history_limit() -> int:
    try:
        n = int(_read("history_limit", _DEFAULT_HISTORY_LIMIT))
    except (TypeError, ValueError):
        n = _DEFAULT_HISTORY_LIMIT
    return max(10, min(100, n))


def window_state() -> dict:
    value = _read("window_state", {})
    return value if isinstance(value, dict) else {}


def bubble_state() -> dict:
    value = _read("bubble_state", {})
    return value if isinstance(value, dict) else {}


def save_bubble_state(x: int, y: int) -> None:
    """悬浮球位置和主窗口几何分开保存。"""
    try:
        with open(_CONFIG, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data["bubble_state"] = {"x": int(x), "y": int(y)}
    with open(_CONFIG, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def save_window_state(x: int, y: int, w: int, h: int) -> None:
    """只更新窗口几何，不碰密钥和其它设置。"""
    try:
        with open(_CONFIG, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data["window_state"] = {"x": int(x), "y": int(y), "w": int(w), "h": int(h)}
    with open(_CONFIG, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _read_env(env_name: str) -> str:
    v = os.environ.get(env_name, "").strip()
    if not v:
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                v = str(winreg.QueryValueEx(k, env_name)[0]).strip()
        except Exception:
            v = ""
        if v:
            os.environ[env_name] = v
    return v


def _get_key(env_name: str) -> str:
    return _read_env(env_name) or _read_env(LEGACY[env_name])


def _set_key(env_name: str, value: str) -> None:
    os.environ[env_name] = value
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            "Environment",
            0,
            winreg.KEY_SET_VALUE,
        ) as k:
            winreg.SetValueEx(k, env_name, 0, winreg.REG_SZ, value)
    except Exception:
        pass


def _notify_env() -> None:
    try:
        fn = ctypes.windll.user32.SendNotifyMessageW
        fn.argtypes = (
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_size_t,
            ctypes.c_wchar_p,
        )
        fn.restype = ctypes.c_int
        fn(0xFFFF, 0x001A, 0, "Environment")
    except Exception:
        pass


def jev_key() -> str:
    return _get_key(JEV_ENV)


def has_jev_key() -> bool:
    return bool(jev_key())


def llm_key() -> str:
    return _get_key(LLM_ENV)


def has_llm_key() -> bool:
    return bool(llm_key())


has_key = has_jev_key


def save(
    relationship_text: str | None = None,
    context_n: int | None = None,
    *,
    jev_provider_text: str | None = None,
    jev_key_text: str | None = None,
    jev_model_text: str | None = None,
    jev_base_url_text: str | None = None,
    draft_provider_text: str | None = None,
    llm_key_text: str | None = None,
    draft_model_text: str | None = None,
    draft_base_url_text: str | None = None,
    reply_target_on: bool | None = None,
    style_text: str | None = None,
    thinking_on: bool | None = None,
    check_update_on: bool | None = None,
    debug_view_on: bool | None = None,
    always_on_top_on: bool | None = None,
    auto_analyze_on: bool | None = None,
    auto_send_on: bool | None = None,
    auto_send_delay_n: int | None = None,
    whitelist_items: list[str] | None = None,
    transparency_n: int | None = None,
    record_history_on: bool | None = None,
    history_limit_n: int | None = None,
) -> None:
    """保存设置。空 key = 保留原 key；模型和 Base URL 可以显式传空串清掉。"""
    jev = (
        jev_provider_text
        if jev_provider_text in JEV_PROVIDERS
        else jev_provider()
    )
    draft = (
        draft_provider_text
        if draft_provider_text in DRAFT_PROVIDERS
        else draft_provider()
    )

    wrote_key = False
    for env, typed in ((JEV_ENV, jev_key_text), (LLM_ENV, llm_key_text)):
        value = typed or ("" if _read_env(env) else _get_key(env))
        if value:
            _set_key(env, value)
            wrote_key = True
    if wrote_key:
        _notify_env()

    n = context() if context_n is None else max(3, min(30, int(context_n)))

    def keep(new, name):
        return str(_read(name) or "") if new is None else str(new).strip()

    def flag(new, now):
        return now() if new is None else bool(new)

    transparency_value = transparency() if transparency_n is None else max(0, min(40, int(transparency_n)))
    history_n = history_limit() if history_limit_n is None else max(10, min(100, int(history_limit_n)))
    send_delay = auto_send_delay() if auto_send_delay_n is None else max(1, min(10, int(auto_send_delay_n)))
    wl = whitelist() if whitelist_items is None else [str(x).strip() for x in whitelist_items if str(x).strip()]

    data = {
        "relationship": relationship_text or relationship(),
        "context": n,
        "style": keep(style_text, "style"),
        "jev_provider": jev,
        "jev_model": keep(jev_model_text, "jev_model"),
        "jev_base_url": keep(jev_base_url_text, "jev_base_url"),
        "draft_provider": draft,
        "draft_model": keep(draft_model_text, "draft_model"),
        "draft_base_url": keep(draft_base_url_text, "draft_base_url"),
        "reply_target": flag(reply_target_on, reply_target),
        "thinking": flag(thinking_on, thinking),
        "check_update": flag(check_update_on, check_update),
        "debug_view": flag(debug_view_on, debug_view),
        "always_on_top": flag(always_on_top_on, always_on_top),
        "auto_analyze": flag(auto_analyze_on, auto_analyze),
        "auto_send": flag(auto_send_on, auto_send),
        "auto_send_delay": send_delay,
        "whitelist": wl,
        "overlay_transparency": transparency_value,
        "record_history": flag(record_history_on, record_history),
        "history_limit": history_n,
        "window_state": window_state(),
        "bubble_state": bubble_state(),
    }

    with open(_CONFIG, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
