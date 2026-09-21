"""Validator + rewrite nodes: output guardrails with a bounded repair loop."""

from __future__ import annotations

import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from assistant.graph.events import emit
from assistant.graph.prompts import REWRITE_SYSTEM, citation_ids, render_evidence, render_tool_results
from assistant.graph.resilience import resilient
from assistant.graph.state import AssistantState
from assistant.llm import get_llm, llm_call
from assistant.llm.provider import message_text
from assistant.security import check_response

MAX_REWRITES = 2


def _validator_fallback(state: AssistantState, exc: Exception) -> dict[str, Any]:
    return {
        "final_answer": state.get("draft_answer", ""),
        "validation": {"passed": False, "issues": [f"validator error: {exc}"]},
    }


@resilient("validator", _validator_fallback)
async def validator_node(state: AssistantState) -> dict[str, Any]:
    evidence = state.get("evidence") or []
    allowed_ids = citation_ids(evidence, state.get("tool_results") or [])
    evidence_required = state.get("route") in ("retrieval", "research") and bool(evidence)
    report = check_response(state.get("draft_answer", ""), allowed_ids, evidence_required)
    rewrite_count = state.get("rewrite_count", 0)
    validation = {
        "passed": report.passed,
        "issues": report.issues,
        "redactions": report.redactions,
        "invalid_citations": report.invalid_citations,
        "attempt": rewrite_count + 1,
    }
    emit(
        "validation",
        "validator",
        f"guardrails {'passed' if report.passed else 'FAILED'}: {report.summary()}",
        **validation,
    )

    if report.passed:
        return {"final_answer": report.cleaned_text, "validation": validation}
    if rewrite_count >= MAX_REWRITES:
        # Give up repairing: strip unverifiable citations and be transparent.
        cleaned = report.cleaned_text
        for bad in report.invalid_citations:
            cleaned = re.sub(rf"\s?\[{bad}\]", "", cleaned)
        cleaned += "\n\n_Note: parts of this answer could not be fully verified against retrieved sources._"
        emit(
            "validation", "validator", "rewrite budget exhausted; delivering sanitised answer with disclaimer"
        )
        return {"final_answer": cleaned, "validation": {**validation, "gave_up": True}}
    return {"validation": validation, "draft_answer": report.cleaned_text}


def _rewrite_fallback(state: AssistantState, exc: Exception) -> dict[str, Any]:
    return {"rewrite_count": state.get("rewrite_count", 0) + 1}


@resilient("rewrite", _rewrite_fallback)
async def rewrite_node(state: AssistantState) -> dict[str, Any]:
    issues = state.get("validation", {}).get("issues", [])
    emit(
        "validation",
        "rewrite",
        f"rewriting draft to fix: {'; '.join(issues)}",
        attempt=state.get("rewrite_count", 0) + 1,
    )
    prompt = (
        "Problems found:\n- " + "\n- ".join(issues) + "\n\n"
        f"Valid citation ids: 1..{len(citation_ids(state.get('evidence') or [], state.get('tool_results') or []))}\n\n"
        f"{render_evidence(state.get('evidence') or [], max_chars_each=500)}\n"
        f"{render_tool_results(state.get('tool_results') or [], max_chars=600, first_id=len(state.get('evidence') or []) + 1)}\n\n"
        f"DRAFT:\n{state.get('draft_answer', '')}"
    )
    msg = await llm_call(
        get_llm("primary"), [SystemMessage(content=REWRITE_SYSTEM), HumanMessage(content=prompt)]
    )
    return {"draft_answer": message_text(msg).strip(), "rewrite_count": state.get("rewrite_count", 0) + 1}
