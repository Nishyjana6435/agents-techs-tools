"""Assemble the LangGraph.

START -> guard -> memory_load -> supervisor -> [retrieval | research | tools | direct]
retrieval/research/direct -> response -> validator -> (rewrite -> validator)* -> memory_update -> END
tools -> (approval -> tools)? -> response
guard(blocked) -> END
"""

from __future__ import annotations

from functools import lru_cache

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from assistant.graph.nodes.guard import guard_node
from assistant.graph.nodes.memory import memory_load_node, memory_update_node
from assistant.graph.nodes.research import research_node
from assistant.graph.nodes.response import response_node
from assistant.graph.nodes.retrieval import retrieval_node
from assistant.graph.nodes.supervisor import supervisor_node
from assistant.graph.nodes.tools import approval_node, tools_node
from assistant.graph.nodes.validator import MAX_REWRITES, rewrite_node, validator_node
from assistant.graph.state import AssistantState
from assistant.memory import get_checkpointer


def _after_guard(state: AssistantState) -> str:
    return END if state.get("route") == "blocked" else "memory_load"


def _after_supervisor(state: AssistantState) -> str:
    route = state.get("route", "retrieval")
    return {"retrieval": "retrieval", "research": "research", "tools": "tools", "direct": "response"}.get(
        route, "retrieval"
    )


def _after_tools(state: AssistantState) -> str:
    return "approval" if state.get("pending_approval") else "response"


def _after_validator(state: AssistantState) -> str:
    validation = state.get("validation") or {}
    if validation.get("passed") or validation.get("gave_up") or state.get("final_answer"):
        return "memory_update"
    if state.get("rewrite_count", 0) >= MAX_REWRITES:
        return "memory_update"
    return "rewrite"


def build_graph(checkpointer=None) -> CompiledStateGraph:
    g = StateGraph(AssistantState)
    g.add_node("guard", guard_node)
    g.add_node("memory_load", memory_load_node)
    g.add_node("supervisor", supervisor_node)
    g.add_node("retrieval", retrieval_node)
    g.add_node("research", research_node)
    g.add_node("tools", tools_node)
    g.add_node("approval", approval_node)
    g.add_node("response", response_node)
    g.add_node("validator", validator_node)
    g.add_node("rewrite", rewrite_node)
    g.add_node("memory_update", memory_update_node)

    g.add_edge(START, "guard")
    g.add_conditional_edges("guard", _after_guard, {END: END, "memory_load": "memory_load"})
    g.add_edge("memory_load", "supervisor")
    g.add_conditional_edges(
        "supervisor",
        _after_supervisor,
        {"retrieval": "retrieval", "research": "research", "tools": "tools", "response": "response"},
    )
    g.add_edge("retrieval", "response")
    g.add_edge("research", "response")
    g.add_conditional_edges("tools", _after_tools, {"approval": "approval", "response": "response"})
    g.add_edge("approval", "tools")
    g.add_edge("response", "validator")
    g.add_conditional_edges(
        "validator", _after_validator, {"rewrite": "rewrite", "memory_update": "memory_update"}
    )
    g.add_edge("rewrite", "validator")
    g.add_edge("memory_update", END)
    return g.compile(checkpointer=checkpointer or get_checkpointer())


@lru_cache
def get_graph() -> CompiledStateGraph:
    return build_graph()
