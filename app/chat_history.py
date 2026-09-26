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



def training_corpus(max_messages: int = 600, max_chars: int = 36000) -> tuple[str, dict]:
    """给“个人客服 Skill”蒸馏使用，只读取本机 chat_history.json。"""
    with _LOCK:
        data = _load_all()

    blocks = []
    used_messages = 0
    used_me = 0
    used_chats = 0
    chars = 0

    for chat, raw in reversed(list(data.items())):
        if not isinstance(raw, list):
            continue
        rows = [x for x in raw[-120:] if isinstance(x, dict) and x.get("from") in ("her", "me")]
        if not any(x.get("from") == "me" and str(x.get("text") or "").strip() for x in rows):
            continue

        lines = [f"【会话：{chat}】"]
        block_messages = 0
        block_me = 0
        for item in rows:
            if used_messages + block_messages >= max_messages:
                break
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            who = item.get("from")
            speaker = "我" if who == "me" else (str(item.get("name") or "").strip() or "对方")
            line = f"{speaker}: {text}"
            projected = chars + sum(len(x) + 1 for x in lines) + len(line)
            if projected > max_chars:
                break
            lines.append(line)
            block_messages += 1
            if who == "me":
                block_me += 1

        if block_me:
            block = "\n".join(lines)
            blocks.append(block)
            chars += len(block) + 2
            used_messages += block_messages
            used_me += block_me
            used_chats += 1
        if used_messages >= max_messages or chars >= max_chars:
            break

    blocks.reverse()
    return "\n\n".join(blocks), {
        "chats": used_chats,
        "messages": used_messages,
        "my_messages": used_me,
    }
