# -*- coding: utf-8 -*-
"""统一分析服务：主程序只提交“会话 + 消息”，不关心具体模型链和背景拼装。

这是当前原型与未来独立商业实现之间最重要的一层边界：
- 上层：窗口、采集、队列、交互；
- 本层：把产品配置和会话背景整理成一次分析请求；
- 下层：core.engine 当前仍是 Jev + 起草模型实现，未来可整体替换。
"""
from __future__ import annotations

from app import settings
from app.services import context_service
from core.engine import analyze as run_engine


def analyze_conversation(title: str, messages: list, reply_to: str | None = None) -> dict:
    """执行完整分析并补充 UI 需要的来源元数据。"""
    ctx = context_service.build(title, messages)

    result = run_engine(
        messages,
        ctx["relationship"],
        judge_relationship=ctx["judge_relationship"],
        context=settings.context(),
        model=settings.draft_model() or None,
        provider=settings.draft_provider(),
        base_url=settings.draft_base_url() or None,
        reply_to=reply_to,
        style=ctx["style"],
        persona=ctx["persona"],
        thinking=settings.thinking(),
        jev_provider=settings.jev_provider(),
        jev_model=settings.jev_model() or None,
        jev_base_url=settings.jev_base_url() or None,
    )
    result["knowledge_count"] = ctx["knowledge_count"]
    result["persona_skill"] = bool(ctx["persona"])
    result["persona_name"] = ctx["persona_name"]
    return result
