"""Response agent: grounded, cited final answer. Tokens stream to the UI via the ``messages`` stream."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from assistant.graph.events import emit
from assistant.graph.prompts import RESPONSE_SYSTEM, render_evidence, render_history, render_tool_results
from assistant.graph.resilience import resilient
from assistant.graph.state import AssistantState, user_from_state
from assistant.llm import LLMError, get_llm

DEGRADED_MESSAGE = (
    "I'm sorry - I couldn't generate a full answer right now because the language model is unavailable. "
    "Here is the evidence I found so you can review it directly:\n\n{evidence}"
)


def _fallback(state: AssistantState, exc: Exception) -> dict[str, Any]:
    bullets = (
        "\n".join(
            f"- {e['title']} > {e.get('section') or 'body'} [{i}]"
            for i, e in enumerate(state.get("evidence", [])[:8], start=1)
        )
        or "- (no evidence)"
    )
    return {"draft_answer": DEGRADED_MESSAGE.format(evidence=bullets)}


@resilient("response", _fallback)
async def response_node(state: AssistantState) -> dict[str, Any]:
    user = user_from_state(state)
    evidence = state.get("evidence") or []
    sections = [
        f'<user role="{user.role.value}" name="{user.display_name}" department="{user.department}"/>',
        f"<user_memory>{state.get('memory_context', '')}</user_memory>",
        render_history(state["messages"], state.get("conversation_summary", "")),
        f"## Question\n{state['question']}",
        f"## Supervisor intent\n{state.get('intent', '')} (route={state.get('route')})",
        f"## Evidence\n{render_evidence(evidence)}",
    ]
    if state.get("research_findings"):
        sections.append(f"## Research findings\n{state['research_findings']}")
    if state.get("tool_results"):
        sections.append(
            f"## Tool results\n{render_tool_results(state['tool_results'], first_id=len(evidence) + 1)}"
        )
    if state.get("degraded"):
        sections.append(
            "## Note\nSome components were degraded during this request; be explicit about limitations."
        )

    emit(
        "state",
        "response",
        f"generating answer from {len(evidence)} evidence chunks"
        + (" + research findings" if state.get("research_findings") else "")
        + (f" + {len(state.get('tool_results', []))} tool result(s)" if state.get("tool_results") else ""),
    )
    llm = get_llm("primary")
    messages = [SystemMessage(content=RESPONSE_SYSTEM), HumanMessage(content="\n\n".join(sections))]
    parts: list[str] = []
    try:
        # astream so the API can forward tokens as they arrive (tagged for filtering).
        async for chunk in llm.astream(messages, config={"tags": ["final_answer"]}):
            content = chunk.content
            if isinstance(content, str):
                parts.append(content)
            else:
                parts.extend(p.get("text", "") for p in content if isinstance(p, dict))
    except Exception as exc:
        raise LLMError(f"response generation failed: {exc}") from exc
    draft = "".join(parts).strip()
    emit("state", "response", f"draft answer ready ({len(draft)} chars)")
    return {"draft_answer": draft}
