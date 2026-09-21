"""Tools node + human-in-the-loop approval node.

``tools_node`` executes the supervisor's tool plan through the registry (RBAC, validation,
timeout). Any step whose tool ``requires_approval`` pauses the graph: the node records
``pending_approval`` and the graph routes to ``approval_node`` which calls ``interrupt()``.
The API surfaces the interrupt to the UI; the user's decision resumes the graph via
``Command(resume="approve"|"reject")`` and we come back here to execute (or skip) the step.
"""

from __future__ import annotations

import asyncio
from typing import Any

from langgraph.types import interrupt

from assistant.graph.events import emit
from assistant.graph.resilience import resilient
from assistant.graph.state import AssistantState, user_from_state
from assistant.tools import get_tool_registry


def _tools_fallback(state: AssistantState, exc: Exception) -> dict[str, Any]:
    return {
        "tool_results": [{"tool": "tools_node", "ok": False, "error": str(exc)[:200]}],
        "pending_approval": None,
    }


@resilient("tools", _tools_fallback)
async def tools_node(state: AssistantState) -> dict[str, Any]:
    user = user_from_state(state)
    registry = await get_tool_registry()
    plan = list(state.get("tool_plan") or [])
    decision = state.get("approval_decision", "")

    # First pass: does any step need approval that we don't have yet?
    for step in plan:
        spec = registry.get(step["tool"])
        if spec and spec.requires_approval and not decision:
            emit(
                "approval",
                "tools",
                f"'{step['tool']}' requires human approval; pausing graph",
                tool=step["tool"],
                params=step.get("params", {}),
            )
            return {
                "pending_approval": {
                    "tool": step["tool"],
                    "params": step.get("params", {}),
                    "reason": step.get("reason", ""),
                }
            }

    async def run(step: dict[str, Any]):
        spec = registry.get(step["tool"])
        if spec and spec.requires_approval and decision != "approve":
            emit("approval", "tools", f"'{step['tool']}' rejected by user; skipped", tool=step["tool"])
            return {
                "tool": step["tool"],
                "ok": False,
                "denied": True,
                "error": "Execution rejected by the user during approval.",
                "params": step.get("params", {}),
            }
        emit(
            "tool_call",
            "tools",
            f"calling {step['tool']}",
            tool=step["tool"],
            params=step.get("params", {}),
            reason=step.get("reason", ""),
        )
        result = await registry.execute(
            step["tool"], step.get("params", {}), user, approved=(decision == "approve")
        )
        emit(
            "tool_result",
            "tools",
            f"{step['tool']} -> {'ok' if result.ok else ('DENIED' if result.denied else 'error')} ({result.duration_ms} ms)",
            tool=step["tool"],
            ok=result.ok,
            denied=result.denied,
            error=result.error,
            duration_ms=result.duration_ms,
            output_preview=str(result.output)[:400] if result.ok else None,
        )
        return result.to_state()

    results = await asyncio.gather(*(run(s) for s in plan))
    # knowledge_search results become citable evidence; everything else stays a tool result.
    evidence = list(state.get("evidence") or [])
    for r in results:
        if r["tool"] == "knowledge_search" and r.get("ok") and isinstance(r.get("output"), dict):
            evidence.extend(r["output"].get("evidence", []))
    return {"tool_results": results, "evidence": evidence, "pending_approval": None}


async def approval_node(state: AssistantState) -> dict[str, Any]:
    """Human-in-the-loop gate. ``interrupt`` raises GraphInterrupt; LangGraph checkpoints and returns
    control to the API. On resume, ``interrupt`` returns the value passed in ``Command(resume=...)``."""
    pending = state.get("pending_approval") or {}
    emit("node_start", "approval", "waiting for human approval", **pending)
    decision = interrupt(
        {
            "type": "tool_approval",
            "tool": pending.get("tool"),
            "params": pending.get("params"),
            "reason": pending.get("reason"),
            "message": f"The agent wants to run administrative tool '{pending.get('tool')}'. Approve?",
        }
    )
    decision = "approve" if str(decision).lower() in ("approve", "approved", "yes", "true") else "reject"
    emit("approval", "approval", f"human decision: {decision}", decision=decision, tool=pending.get("tool"))
    emit("node_end", "approval", "leaving approval")
    return {"approval_decision": decision, "node_path": ["approval"]}
