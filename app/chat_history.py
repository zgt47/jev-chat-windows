# -*- coding: utf-8 -*-
"""可选的本地聊天历史。默认关闭；只存在程序目录旁，不上传。"""
from __future__ import annotations

import json
import os
import sys
import threading

_ROOT = (
    os.path.dirname(sys.executable)
    if getattr(sys, "frozen", False)
    else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_PATH = os.path.join(_ROOT, "chat_history.json")
_LOCK = threading.Lock()


def _load_all() -> dict:
    try:
        with open(_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load(chat: str, limit: int = 30) -> list[tuple[str, str, str | None]]:
    chat = str(chat or "").strip()
    if not chat:
        return []
    with _LOCK:
        raw = _load_all().get(chat, [])
    out = []
    for item in raw[-max(1, int(limit)):]:
        if not isinstance(item, dict):
            continue
        who = item.get("from")
        if who not in ("her", "me"):
            continue
        out.append((who, str(item.get("text") or ""), item.get("name") or None))
    return out


def save(chat: str, messages: list, max_keep: int = 200) -> None:
    chat = str(chat or "").strip()
    if not chat:
        return
    rows = []
    for item in list(messages)[-max(1, int(max_keep)):]:
        if isinstance(item, dict):
            who, text, name = item.get("from"), item.get("text"), item.get("name")
        else:
            who, text = item[0], item[1]
            name = item[2] if len(item) > 2 else None
        if who in ("her", "me"):
            rows.append({"from": who, "text": str(text), "name": str(name) if name else None})
    with _LOCK:
        data = _load_all()
        data[chat] = rows
        tmp = _PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _PATH)


def clear(chat: str) -> None:
    chat = str(chat or "").strip()
    if not chat:
        return
    with _LOCK:
        data = _load_all()
        data.pop(chat, None)
        tmp = _PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _PATH)
