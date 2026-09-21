"""Graph state.

One flat ``TypedDict`` (LangGraph's idiom) with reducers only where nodes append rather than
replace. Keeping the state explicit makes the checkpoint human-readable in LangSmith and in
``/threads/{id}/state``, which is the point of an observable agent.
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

Route = Literal["retrieval", "research", "tools", "direct", "blocked"]


class ToolCallPlan(TypedDict, total=False):
    tool: str
    params: dict[str, Any]
    reason: str


class AssistantState(TypedDict, total=False):
    # --- conversation ------------------------------------------------------------------------
    messages: Annotated[list[BaseMessage], add_messages]
    conversation_summary: str
    user: dict[str, Any]  # serialised UserContext
    thread_id: str
    question: str  # validated current user message

    # --- guard --------------------------------------------------------------------------------
    injection: dict[str, Any]
    blocked_reason: str

    # --- memory -------------------------------------------------------------------------------
    memory_context: str
    memory_updates: list[str]

    # --- supervisor ---------------------------------------------------------------------------
    intent: str
    route: Route
    filters: dict[str, Any]
    sub_questions: list[str]
    tool_plan: list[ToolCallPlan]
    supervisor_rationale: str

    # --- evidence & tools ----------------------------------------------------------------------
    evidence: list[dict[str, Any]]  # replaced per turn by retrieval/research
    retrieval_summary: str
    research_findings: str
    rlm_trace: Annotated[list[dict[str, Any]], operator.add]
    tool_results: Annotated[list[dict[str, Any]], operator.add]
    pending_approval: dict[str, Any] | None
    approval_decision: str

    # --- answer -----------------------------------------------------------------------------------
    draft_answer: str
    final_answer: str
    validation: dict[str, Any]
    rewrite_count: int

    # --- health ---------------------------------------------------------------------------------------
    errors: Annotated[list[str], operator.add]
    degraded: bool
    node_path: Annotated[list[str], operator.add]


def user_from_state(state: AssistantState):
    from assistant.auth.models import UserContext

    return UserContext.model_validate(state["user"])


def user_to_state(user) -> dict[str, Any]:
    """Always JSON-mode so enums become plain strings in checkpoints."""
    return user.model_dump(mode="json")
