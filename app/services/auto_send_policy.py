# -*- coding: utf-8 -*-
"""自动发送策略。

这里只回答两件事：
1. 一轮分析结果现在是否允许进入自动发送倒计时；
2. 倒计时结束时，发送前状态是否仍然有效。

不依赖 Qt、不点击窗口、不调用模型。真正的计时和发送由上层运行时负责。
"""
from __future__ import annotations

from dataclasses import dataclass
import re


_EXTERNAL_FILE_REQUEST = re.compile(
    r"(?:"
    r"(?:发|传|拍|截|提供|出示).{0,8}(?:报告|截图|照片|图片|视频|文件|附件|维保记录|出险记录)"
    r"|(?:报告|截图|照片|图片|视频|文件|附件|维保记录|出险记录).{0,10}"
    r"(?:发|传|给我|给我看|看看|看下|看一下|拍|截|提供|出示)"
    r")",
    re.I,
)


@dataclass(frozen=True)
class AutoSendPlan:
    allowed: bool
    text: str = ""
    status: str = ""
    status_kind: str = "idle"

    @classmethod
    def skip(cls) -> "AutoSendPlan":
        return cls(False)

    @classmethod
    def block(cls, status: str, kind: str = "warning") -> "AutoSendPlan":
        return cls(False, status=status, status_kind=kind)

    @classmethod
    def ready(cls, text: str) -> "AutoSendPlan":
        return cls(True, text=text)


def evaluate(
    *,
    enabled: bool,
    title: str,
    active_title: str,
    visible_title: str,
    chat_state: dict,
    profile: dict,
    result: dict,
) -> AutoSendPlan:
    """分析完成时执行一次安全门判断。"""
    if not enabled:
        return AutoSendPlan.skip()
    if title != active_title or title != visible_title:
        return AutoSendPlan.skip()

    history = chat_state.get("history") or []
    if not history or history[-1][0] != "her":
        return AutoSendPlan.skip()

    latest_text = str(history[-1][1] if len(history[-1]) > 1 else "")
    if _EXTERNAL_FILE_REQUEST.search(latest_text):
        return AutoSendPlan.block(
            "自动发送已暂停：对方要求报告、截图或附件等 Jev 当前不能自动执行的动作，需要人工处理。"
        )

    # 群聊默认不自动发。senders 非空意味着自动识别出了群成员，也按群聊处理。
    if profile.get("chat_type") == "group" or chat_state.get("senders"):
        return AutoSendPlan.block("自动发送未执行：群聊默认需要人工确认。")

    answers = result.get("answers") or {}
    danger = (answers.get("danger_level") or {}).get("score")
    best_choice = (answers.get("best_reply") or {}).get("choice")

    errors = result.get("analysis_errors") or []
    if not isinstance(danger, (int, float)):
        detail = errors[0] if errors else "没有完成危险度判断"
        return AutoSendPlan.block(f"自动发送未执行：{detail}。")
    if best_choice not in ("reply_a", "reply_b", "reply_c"):
        detail = next((x for x in errors if x.startswith("排序失败：")), "没有完成候选排序")
        return AutoSendPlan.block(f"自动发送未执行：{detail}。")
    if danger >= 6:
        return AutoSendPlan.block(
            f"自动发送已暂停：当前危险度 {danger:.0f}/9，需要人工确认。"
        )

    text = str(result.get("best_reply") or "").strip()
    return AutoSendPlan.ready(text) if text else AutoSendPlan.skip()


def still_valid(
    *,
    enabled: bool,
    title: str,
    active_title: str,
    visible_title: str,
    revision: int,
    current_revision: int,
    history,
    hwnd_available: bool,
    area_available: bool,
) -> AutoSendPlan:
    """倒计时结束后再次检查；任何上下文变化都不得继续发送。"""
    if not enabled:
        return AutoSendPlan.skip()
    if title != active_title or title != visible_title:
        return AutoSendPlan.skip()
    if current_revision != revision:
        return AutoSendPlan.skip()
    if not history or history[-1][0] != "her":
        return AutoSendPlan.skip()
    if not hwnd_available or not area_available:
        return AutoSendPlan.block("自动发送取消：当前聊天窗口不可用。")
    return AutoSendPlan.ready("")
