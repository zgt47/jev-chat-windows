# -*- coding: utf-8 -*-
"""编辑页未保存状态跟踪。

纯状态模块，不依赖 Qt。UI 只负责询问“保存 / 退出”。
"""
from __future__ import annotations


class EditGuard:
    def __init__(self):
        self._clean = {}

    def mark_clean(self, key: str, state) -> None:
        self._clean[str(key)] = state

    def is_dirty(self, key: str, state) -> bool:
        key = str(key)
        if key not in self._clean:
            return False
        return self._clean[key] != state

    def forget(self, key: str) -> None:
        self._clean.pop(str(key), None)
