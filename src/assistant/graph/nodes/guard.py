"""Guard node: validate the incoming message and screen it for prompt injection."""
from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from assistant.graph.events import emit
from assistant.graph.resilience import resilient
from assistant.graph.state import AssistantState
from assistant.security import ValidationError, scan_prompt_injection, validate_user_message

BLOCKED_MESSAGE = (
    "I can't help with that request. It looks like an attempt to override my instructions, extract "
    "internal configuration or misuse tools, which the Acceptable Use of AI Assistants Policy treats as a "
    "security event. If this is a mistake, please rephrase your question about our documents or systems."
)


def _fallback(state: AssistantState, exc: Exception) -> dict[str, Any]:
    return {"route": "blocked", "blocked_reason": "guard failure", "final_answer": "Sorry, I could not process that message.", "question": ""}


@resilient("guard", _fallback)
async def guard_node(state: AssistantState) -> dict[str, Any]:
    last_human = next((m for m in reversed(state["messages"]) if isinstance(m, HumanMessage)), None)
    raw = last_human.content if last_human and isinstance(last_human.content, str) else ""
    try:
        question = validate_user_message(raw)
    except ValidationError as exc:
        emit("security", "guard", f"input rejected: {exc}")
        return {"route": "blocked", "blocked_reason": str(exc), "final_answer": f"Invalid request: {exc}", "messages": [AIMessage(content=f"Invalid request: {exc}")], "question": ""}

    verdict = scan_prompt_injection(question)
    injection = {"risk": verdict.risk, "rules": verdict.matched_rules, "classes": verdict.attack_classes, "blocked": verdict.blocked}
    if verdict.blocked:
        emit("security", "guard", f"BLOCKED prompt-injection attempt: {verdict.explain()}", **injection)
        return {
            "question": question,
            "injection": injection,
            "route": "blocked",
            "blocked_reason": verdict.explain(),
            "final_answer": BLOCKED_MESSAGE,
            "messages": [AIMessage(content=BLOCKED_MESSAGE)],
        }
    if verdict.suspicious:
        emit("security", "guard", f"suspicious input flagged (continuing with caution): {verdict.explain()}", **injection)
    else:
        emit("security", "guard", "input validated; no injection patterns", risk=verdict.risk)
    return {"question": question, "injection": injection, "blocked_reason": "", "pending_approval": None, "approval_decision": "", "tool_plan": [], "evidence": [], "research_findings": "", "draft_answer": "", "final_answer": "", "rewrite_count": 0, "validation": {}}
