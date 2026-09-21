"""Memory nodes: load long-term profile + compact history; persist learnings after the answer."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

from assistant.config import get_settings
from assistant.graph.events import emit
from assistant.graph.resilience import resilient
from assistant.graph.state import AssistantState, user_from_state
from assistant.memory import get_long_term_memory, summarize_history


def _load_fallback(state: AssistantState, exc: Exception) -> dict[str, Any]:
    return {"memory_context": "No prior history available (memory store unavailable)."}


@resilient("memory_load", _load_fallback)
async def memory_load_node(state: AssistantState) -> dict[str, Any]:
    user = user_from_state(state)
    profile = await get_long_term_memory().get(user.username)
    context = get_long_term_memory().render(profile)
    update: dict[str, Any] = {"memory_context": context}

    settings = get_settings()
    messages = state["messages"]
    turns_kept = settings.max_history_turns
    if len(messages) > turns_kept:
        summary, _ = summarize_history(messages, keep_last=turns_kept)
        previous = state.get("conversation_summary", "")
        update["conversation_summary"] = (previous + "\n" + summary).strip() if previous else summary
        emit(
            "memory",
            "memory_load",
            f"compacted {len(messages) - turns_kept} older messages into rolling summary",
            summary_chars=len(update["conversation_summary"]),
        )
    emit(
        "memory",
        "memory_load",
        f"long-term profile loaded: {context[:160]}",
        turns=profile.get("turns", 0),
        topics=profile.get("topics", {}),
    )
    return update


def _update_fallback(state: AssistantState, exc: Exception) -> dict[str, Any]:
    answer = state.get("final_answer") or state.get("draft_answer") or ""
    return {"messages": [AIMessage(content=answer)], "memory_updates": ["memory write failed"]}


@resilient("memory_update", _update_fallback)
async def memory_update_node(state: AssistantState) -> dict[str, Any]:
    user = user_from_state(state)
    answer = state.get("final_answer") or state.get("draft_answer") or ""
    departments = sorted({e.get("department") for e in state.get("evidence", []) if e.get("department")})
    style = "concise" if len(state.get("question", "")) < 60 else None
    result = await get_long_term_memory().record_turn(
        user.username, state.get("question", ""), departments, answer_style=style
    )
    for line in result["updates"]:
        emit("memory", "memory_update", f"long-term memory: {line}")
    emit(
        "memory",
        "memory_update",
        "conversation checkpoint saved (short-term memory)",
        thread_id=state.get("thread_id"),
    )
    return {"messages": [AIMessage(content=answer)], "memory_updates": result["updates"]}
