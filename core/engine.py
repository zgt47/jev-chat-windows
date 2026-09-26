# -*- coding: utf-8 -*-
"""整条链的唯一入口：对话 → Jev 判断 → 带着判断起草 3 条 → Jev 排序 → 结构化结果。

平台无关。SSE 消费者、悬浮窗、命令行 demo 都只调 analyze()。
"""
from __future__ import annotations

try:
    from .draft import draft_candidates
    from .jev_client import JevError, ask
    from .questions import JUDGE_QUESTIONS, build_rank_question, build_state, guidance_text
except ImportError:
    from draft import draft_candidates
    from jev_client import JevError, ask
    from questions import JUDGE_QUESTIONS, build_rank_question, build_state, guidance_text

_REPLY_IDX = {"reply_a": 0, "reply_b": 1, "reply_c": 2}


def _add_usage(total: dict, one: dict | None) -> None:
    """两次 Jev 调用的 usage 相加；非数字字段后来的覆盖前面的。"""
    for k, v in (one or {}).items():
        total[k] = total.get(k, 0) + v if isinstance(v, (int, float)) else v


def analyze(
    messages: list,
    relationship: str,
    model: str | None = None,
    timeout: float = 30,
    context: int = 10,
    provider: str = "deepseek",
    base_url: str | None = None,
    reply_to: str | None = None,
    style: str = "",
    persona: str = "",
    thinking: bool = False,
    jev_provider: str = "openrouter",
    jev_model: str | None = None,
    jev_base_url: str | None = None,
) -> dict:
    """messages: [(from, text)]，from ∈ {her, me}，最新一条在最后。

    provider / model / base_url：起草模型。
    jev_provider / jev_model / jev_base_url：判断与排序模型。

    三段式：
    1. 先让 Jev 回答 7 道判断题；
    2. 把判断结果作为参考交给起草模型写候选；
    3. 再让 Jev 给候选排序。

    容错：
    - 第一次判断失败：照样盲起草；
    - 第二次判断/排序失败：照样返回已经生成的候选，只是不做有效排序；
    - 起草本身失败或候选被过滤光：才真正失败。
    """
    state = build_state(messages, relationship, keep=context, reply_to=reply_to)
    usage: dict = {}
    answers: dict = {}
    judged = False

    try:
        first = ask(
            state,
            dict(JUDGE_QUESTIONS),
            timeout=timeout,
            provider=jev_provider,
            model=jev_model,
            base_url=jev_base_url,
        )
        answers = first.get("answers") or {}
        _add_usage(usage, first.get("usage"))
        judged = True
    except JevError:
        pass

    candidates = draft_candidates(
        messages,
        relationship,
        provider=provider,
        model=model,
        base_url=base_url,
        timeout=timeout,
        keep=context,
        reply_to=reply_to,
        style=style,
        persona=persona,
        thinking=thinking,
        guidance=guidance_text(answers) if judged else None,
    )
    if not candidates:
        raise JevError("起草结果没有可用候选回复")

    questions = {} if judged else dict(JUDGE_QUESTIONS)
    if len(candidates) >= 2:
        questions.update(build_rank_question(candidates, persona))

    if questions:
        try:
            second = ask(
                state,
                questions,
                timeout=timeout,
                provider=jev_provider,
                model=jev_model,
                base_url=jev_base_url,
            )
        except JevError:
            second = {}
        answers = {**answers, **(second.get("answers") or {})}
        _add_usage(usage, second.get("usage"))

    best_key = (answers.get("best_reply") or {}).get("choice")
    best_index = _REPLY_IDX.get(best_key, 0)
    if best_index >= len(candidates):
        best_index = 0

    probabilities = (answers.get("best_reply") or {}).get("probabilities") or {}
    scores = [0.0, 0.0, 0.0]
    for key, idx in _REPLY_IDX.items():
        try:
            value = float(probabilities.get(key, 0.0))
            if 1.0 < value <= 100.0:
                value /= 100.0
            scores[idx] = max(0.0, min(1.0, value))
        except (TypeError, ValueError):
            scores[idx] = 0.0

    return {
        "candidates": candidates,
        "best_index": best_index,
        "best_reply": candidates[best_index],
        "scores": scores,
        "answers": answers,
        "usage": usage,
        "reply_to": reply_to,
    }


if __name__ == "__main__":
    from unittest.mock import patch

    with patch("__main__.ask", return_value={"answers": {}, "usage": {}}), \
         patch("__main__.draft_candidates", return_value=[]):
        try:
            analyze([("her", "hello")], "friends")
            raise SystemExit("应当抛错")
        except JevError as e:
            assert "没有可用候选" in str(e)

    with patch("__main__.ask", side_effect=JevError("down")), \
         patch("__main__.draft_candidates", return_value=["甲", "乙", "丙"]):
        got = analyze([("her", "hello")], "friends")
        assert got["candidates"] == ["甲", "乙", "丙"]
        assert got["best_index"] == 0

    print("engine ok")
