# -*- coding: utf-8 -*-
"""本地知识库：知识名称 / 触发词 / 知识内容 / 使用方式 / 启用。只存程序目录旁。"""
from __future__ import annotations

import json
import os
import sys
import uuid

_ROOT = (
    os.path.dirname(sys.executable)
    if getattr(sys, "frozen", False)
    else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_PATH = os.path.join(_ROOT, "knowledge.json")


def _load() -> list[dict]:
    try:
        with open(_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []


def _save(items: list[dict]) -> None:
    tmp = _PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _PATH)


def notes() -> list[dict]:
    out = []
    for item in _load():
        out.append({
            "id": str(item.get("id") or ""),
            "title": str(item.get("title") or "").strip(),
            "content": str(item.get("content") or "").strip(),
            "tags": [str(x).strip() for x in (item.get("tags") or []) if str(x).strip()],
            "always_on": bool(item.get("always_on", False)),
            "enabled": bool(item.get("enabled", True)),
        })
    return out


def save_note(title: str, content: str, tags: list[str], always_on: bool,
              enabled: bool, note_id: str | None = None) -> str:
    title = str(title or "").strip()
    content = str(content or "").strip()
    if not title:
        raise ValueError("知识名称不能为空")
    if not content:
        raise ValueError("知识内容不能为空")
    if not always_on and not tags:
        raise ValueError("按触发词使用时至少填写一个触发词")
    note_id = str(note_id or uuid.uuid4().hex)
    items = notes()
    row = {
        "id": note_id,
        "title": title,
        "content": content,
        "tags": [str(x).strip() for x in tags if str(x).strip()],
        "always_on": bool(always_on),
        "enabled": bool(enabled),
    }
    for i, item in enumerate(items):
        if item["id"] == note_id:
            items[i] = row
            break
    else:
        items.append(row)
    _save(items)
    return note_id


def delete_note(note_id: str) -> None:
    _save([x for x in notes() if x["id"] != str(note_id or "")])


def match(chat: str, messages: list, limit: int = 5) -> list[dict]:
    """每次都使用的知识直接带入；其它知识仅在触发词命中会话标题/最近 6 条消息时带入。"""
    recent = []
    for item in list(messages)[-6:]:
        text = item.get("text") if isinstance(item, dict) else item[1]
        recent.append(str(text or ""))
    hay = (str(chat or "") + "\n" + "\n".join(recent)).casefold()
    hit = []
    for note in notes():
        if not note["enabled"]:
            continue
        matched = note["always_on"] or any(
            word and word.casefold() in hay for word in note["tags"]
        )
        if matched:
            hit.append(note)
        if len(hit) >= max(1, int(limit)):
            break
    return hit
