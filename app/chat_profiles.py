# -*- coding: utf-8 -*-
"""按会话保存关系资料。

这部分故意和 app/settings.py 分开：
- settings.py 只负责全局设置（模型、上下文、开关等）
- chat_profiles.py 只负责每个聊天对象自己的关系和说话风格

文件落在程序目录旁的 chat_profiles.json，不进仓库。
"""
from __future__ import annotations

import json
import os
import sys

_ROOT = (
    os.path.dirname(sys.executable)
    if getattr(sys, "frozen", False)
    else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_PATH = os.path.join(_ROOT, "chat_profiles.json")
_LEGACY_CONFIG = os.path.join(_ROOT, "config.json")
_DEFAULT_RELATIONSHIP = "friends"
_DEFAULT_STYLE = "正常、克制、短句、不装熟"
_DEFAULT_CHAT_TYPE = "auto"
_CHAT_TYPES = {"auto", "private", "group"}


def _load() -> dict:
    try:
        with open(_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _legacy_default() -> tuple[str, str, bool]:
    """兼容 v0.1.12 以前的全局关系设置。

    如果旧 config.json 里明确存过 relationship/style，新会话在尚未单独保存前先沿用它；
    一旦给某个会话保存过，就完全以 chat_profiles.json 为准。
    """
    try:
        with open(_LEGACY_CONFIG, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return _DEFAULT_RELATIONSHIP, "", False
    if not isinstance(data, dict):
        return _DEFAULT_RELATIONSHIP, "", False
    relationship = str(data.get("relationship") or "").strip()
    style = str(data.get("style") or _DEFAULT_STYLE).strip()
    if relationship:
        return relationship, style, True
    return _DEFAULT_RELATIONSHIP, style, False


def get(chat: str) -> dict:
    """返回当前会话资料：relationship / style / saved / legacy。"""
    chat = str(chat or "").strip()
    data = _load()
    item = data.get(chat) if chat else None
    if not isinstance(item, dict) and chat:
        needle = chat.casefold()
        for candidate in data.values():
            if not isinstance(candidate, dict):
                continue
            aliases = [str(x).strip().casefold() for x in (candidate.get("aliases") or [])]
            if needle in aliases:
                item = candidate
                break
    if isinstance(item, dict):
        relationship = str(item.get("relationship") or _DEFAULT_RELATIONSHIP).strip()
        style = str(item.get("style") or _DEFAULT_STYLE).strip()
        notes = str(item.get("notes") or "").strip()
        aliases = [str(x).strip() for x in (item.get("aliases") or []) if str(x).strip()]
        chat_type = str(item.get("chat_type") or _DEFAULT_CHAT_TYPE).strip()
        persona_id = str(item.get("persona_id") or "__default__").strip()
        if chat_type not in _CHAT_TYPES:
            chat_type = _DEFAULT_CHAT_TYPE
        return {
            "relationship": relationship,
            "style": style,
            "notes": notes,
            "aliases": aliases,
            "chat_type": chat_type,
            "persona_id": persona_id,
            "saved": True,
            "legacy": False,
        }

    relationship, style, legacy = _legacy_default()
    return {
        "relationship": relationship,
        "style": style or _DEFAULT_STYLE,
        "notes": "",
        "aliases": [],
        "chat_type": _DEFAULT_CHAT_TYPE,
        "persona_id": "__default__",
        "saved": False,
        "legacy": legacy,
    }


def relationship(chat: str) -> str:
    return get(chat)["relationship"]


def style(chat: str) -> str:
    return get(chat)["style"]


def chat_type(chat: str) -> str:
    return get(chat)["chat_type"]


def save(chat: str, relationship: str, style: str = _DEFAULT_STYLE,
         chat_type: str = _DEFAULT_CHAT_TYPE, notes: str = "",
         aliases: list[str] | None = None, persona_id: str = "__default__") -> None:
    chat = str(chat or "").strip()
    relationship = str(relationship or "").strip()
    style = str(style or _DEFAULT_STYLE).strip()
    notes = str(notes or "").strip()
    aliases = [str(x).strip() for x in (aliases or []) if str(x).strip()]
    chat_type = str(chat_type or _DEFAULT_CHAT_TYPE).strip()
    persona_id = str(persona_id or "__default__").strip()
    if not chat:
        raise ValueError("尚未识别到会话")
    if not relationship:
        raise ValueError("关系不能为空")
    if chat_type not in _CHAT_TYPES:
        raise ValueError("会话类型无效")

    data = _load()
    data[chat] = {
        "relationship": relationship,
        "style": style,
        "notes": notes,
        "aliases": aliases,
        "chat_type": chat_type,
        "persona_id": persona_id,
    }

    tmp = _PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _PATH)


if __name__ == "__main__":
    print("chat_profiles ok")
