"""Supervisor agent: intent understanding, task decomposition and routing."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from assistant.graph.events import emit
from assistant.graph.prompts import SUPERVISOR_SYSTEM, render_history
from assistant.graph.resilience import resilient
from assistant.graph.state import AssistantState, user_from_state
from assistant.llm import ainvoke_json, get_llm
from assistant.retrieval import get_knowledge_index
from assistant.tools import get_tool_registry


class Filters(BaseModel):
    department: str | None = None
    document_types: list[str] | None = None
    created_after: str | None = None
    created_before: str | None = None


class ToolStep(BaseModel):
    tool: str
    params: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class SupervisorDecision(BaseModel):
    intent: str = ""
    route: str = "retrieval"
    filters: Filters = Field(default_factory=Filters)
    sub_questions: list[str] = Field(default_factory=list)
    tool_plan: list[ToolStep] = Field(default_factory=list)
    rationale: str = ""


def _fallback(state: AssistantState, exc: Exception) -> dict[str, Any]:
    """If the supervisor LLM fails we still answer: plain retrieval with no filters."""
    return {"intent": "unknown (supervisor unavailable)", "route": "retrieval", "filters": {}, "sub_questions": [], "tool_plan": [], "supervisor_rationale": f"fallback route after supervisor failure: {exc.__class__.__name__}"}


@resilient("supervisor", _fallback)
async def supervisor_node(state: AssistantState) -> dict[str, Any]:
    user = user_from_state(state)
    registry = await get_tool_registry()
    index = await get_knowledge_index()
    namespaces = index.status()["namespaces"]

    system = SUPERVISOR_SYSTEM.replace("{namespaces}", ", ".join(namespaces)).replace("{today}", datetime.now(UTC).date().isoformat())
    prompt = (
        f"<user role=\"{user.role.value}\" department=\"{user.department}\" clearance=\"{user.clearance}\"/>\n"
        f"<user_memory>{state.get('memory_context', '')}</user_memory>\n"
        f"{render_history(state['messages'], state.get('conversation_summary', ''))}\n\n"
        f"Tools available to this user:\n{registry.render_for(user)}\n\n"
        f"User question: {state['question']}"
    )
    emit("state", "supervisor", "analysing intent and choosing a route", tools_visible=[t.name for t in registry.available_for(user)])
    decision = await ainvoke_json(get_llm("primary"), [SystemMessage(content=system), HumanMessage(content=prompt)], SupervisorDecision)

    notes: list[str] = []
    # --- Validate the model's plan against hard controls. The LLM proposes; the code disposes. -----
    if decision.route not in ("retrieval", "research", "tools", "direct"):
        notes.append(f"unknown route '{decision.route}' -> retrieval")
        decision.route = "retrieval"
    if decision.filters.department and decision.filters.department not in namespaces:
        notes.append(f"dropped unknown department filter '{decision.filters.department}'")
        decision.filters.department = None
    allowed_plan: list[dict[str, Any]] = []
    for step in decision.tool_plan:
        if registry.get(step.tool) is None:
            notes.append(f"dropped unknown tool '{step.tool}'")
        elif not registry.is_allowed(user, step.tool):
            notes.append(f"tool '{step.tool}' requested but role '{user.role.value}' is not permitted -> dropped")
            emit("security", "supervisor", f"RBAC: supervisor proposed '{step.tool}' which role '{user.role.value}' cannot use; removed from plan")
        else:
            allowed_plan.append(step.model_dump())
    if decision.route == "tools" and not allowed_plan:
        notes.append("no permitted tools in plan -> falling back to retrieval")
        decision.route = "retrieval"

    rationale = decision.rationale + (" | " + "; ".join(notes) if notes else "")
    emit(
        "state", "supervisor", f"route={decision.route} intent={decision.intent[:80]}",
        route=decision.route, intent=decision.intent, filters=decision.filters.model_dump(exclude_none=True),
        sub_questions=decision.sub_questions, tool_plan=[s["tool"] for s in allowed_plan], rationale=rationale,
    )
    return {
        "intent": decision.intent,
        "route": decision.route,
        "filters": decision.filters.model_dump(exclude_none=True),
        "sub_questions": decision.sub_questions[:6],
        "tool_plan": allowed_plan,
        "supervisor_rationale": rationale,
    }
