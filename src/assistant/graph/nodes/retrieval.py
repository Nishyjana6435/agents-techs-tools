"""Retrieval agent: one hybrid search through the RBAC'd knowledge_search tool, with adaptive retry."""
from __future__ import annotations

from typing import Any

from assistant.graph.events import emit
from assistant.graph.resilience import resilient
from assistant.graph.state import AssistantState, user_from_state
from assistant.tools import get_tool_registry


def _fallback(state: AssistantState, exc: Exception) -> dict[str, Any]:
    return {"evidence": [], "retrieval_summary": f"retrieval unavailable: {exc.__class__.__name__}", "degraded": True}


@resilient("retrieval", _fallback)
async def retrieval_node(state: AssistantState) -> dict[str, Any]:
    user = user_from_state(state)
    registry = await get_tool_registry()
    filters = dict(state.get("filters") or {})
    params = {"query": state["question"], **filters, "top_k": 8}
    emit("retrieval", "retrieval", f"hybrid search: '{state['question'][:80]}' filters={filters or 'none'}", params=params)
    emit("tool_call", "retrieval", "knowledge_search", tool="knowledge_search", params=params)
    result = await registry.execute("knowledge_search", params, user)
    if not result.ok:
        raise RuntimeError(result.error or "knowledge_search failed")
    output = result.output
    evidence = output["evidence"]
    notes = list(output.get("notes", []))

    if not evidence and filters:
        # Adaptive retrieval: filters inferred by the supervisor may be too strict. Retry once wide open.
        emit("retrieval", "retrieval", "no results with filters; retrying without filters")
        result = await registry.execute("knowledge_search", {"query": state["question"], "top_k": 8}, user)
        if result.ok:
            output = result.output
            evidence = output["evidence"]
            notes += ["filters relaxed after empty result", *output.get("notes", [])]

    for q in output.get("quarantined", []):
        emit("security", "retrieval", f"quarantined chunk with injection patterns: {q}")
    emit(
        "retrieval", "retrieval", output["summary"],
        results=[{"rank": i + 1, "title": e["title"], "section": e["section"], "score": e["score"], "why": e["explanation"]} for i, e in enumerate(evidence)],
        notes=notes, degraded=output.get("degraded", False),
    )
    emit("tool_result", "retrieval", f"knowledge_search -> {len(evidence)} evidence chunks", tool="knowledge_search", ok=True, count=len(evidence))
    return {"evidence": evidence, "retrieval_summary": output["summary"] + (f"; notes: {'; '.join(notes)}" if notes else ""), "degraded": bool(output.get("degraded"))}
