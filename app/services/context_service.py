# -*- coding: utf-8 -*-
"""会话分析上下文服务：集中组装关系、备注、知识库和人格。

这个模块只负责“这轮分析应该带什么背景”，不负责调用模型，也不负责界面。
"""
from __future__ import annotations

from app import chat_profiles, knowledge, persona_skill


def build(title: str, messages: list) -> dict:
    """构造一轮分析所需的业务上下文。

    返回稳定字段：
    - profile: 当前会话资料
    - relationship: 已合并联系人备注 / 知识库的关系背景
    - style: 当前会话回复风格
    - persona_id / persona / persona_name: 人格选择和提示文本
    - knowledge_count: 本轮实际命中的知识库条数
    """
    profile = chat_profiles.get(title)

    relationship = str(profile.get("relationship") or "").strip()
    notes = str(profile.get("notes") or "").strip()
    if notes:
        relationship += "\n联系人备注：" + notes

    matched_notes = knowledge.match(title, messages)
    if matched_notes:
        relationship += "\n知识库背景（只把它当事实，不要编造）：\n" + "\n".join(
            f"- {note['title'] or '笔记'}：{note['content']}" for note in matched_notes
        )

    persona_id = profile.get("persona_id", persona_skill.DEFAULT_PERSONA)
    persona_data = persona_skill.effective(persona_id)
    persona = persona_skill.prompt_text(persona_id)

    return {
        "profile": profile,
        "relationship": relationship,
        "style": str(profile.get("style") or ""),
        "persona_id": persona_id,
        "persona": persona,
        "persona_name": persona_data.get("name") if persona_data else "",
        "knowledge_count": len(matched_notes),
    }
