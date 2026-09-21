"""Deterministic offline chat model.

The graph's prompts each start with a ``# task: <name>`` line. The mock reads that line and
produces a plausible, data-driven response for the task (routing JSON, batch findings, a cited
answer). This lets the full pipeline - including guardrails, citations and the activity panel -
run and be tested with no network access. It is never used when a real API key is configured.
"""
from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

TASK_RE = re.compile(r"#\s*task:\s*([a-z_]+)")
EVIDENCE_RE = re.compile(r'<evidence id="(\d+)" title="([^"]*)" section="([^"]*)"[^>]*>(.*?)</evidence>', re.DOTALL)
ROOT_CAUSE_RE = re.compile(r"\*\*([^*]+)\*\*")


def _text(m: BaseMessage) -> str:
    return m.content if isinstance(m.content, str) else " ".join(
        p.get("text", "") if isinstance(p, dict) else str(p) for p in m.content
    )


class MockChatModel(BaseChatModel):
    tier: str = "primary"

    @property
    def _llm_type(self) -> str:
        return "mock-chat"

    # ---------------------------------------------------------------------------------------
    def _respond(self, messages: list[BaseMessage]) -> str:
        system = _text(messages[0]) if messages else ""
        user = _text(messages[-1]) if messages else ""
        task_match = TASK_RE.search(system) or TASK_RE.search(user)
        task = task_match.group(1) if task_match else "chat"
        handler = getattr(self, f"_task_{task}", self._task_chat)
        return handler(system, user, messages)

    def _task_supervisor(self, system: str, user: str, _: list[BaseMessage]) -> str:
        question = user.split("User question:", 1)[-1].strip() if "User question:" in user else user
        q = question.lower()
        research_markers = ("all ", "summarize", "summarise", "recurring", "across", "compare", "trend", "every ")
        tool_markers = ("employee", "who is", "service catalog", "owner of", "owner", "on-call", "on call", "directory", "incident records", "open incidents", "escalate", "reindex", "list services")
        route = "retrieval"
        tool_plan: list[dict[str, Any]] = []
        if any(m in q for m in tool_markers):
            route = "tools"
            svc = re.search(r"\b([a-z]+(?:-[a-z]+)+)\b", q)
            if ("owner" in q or "service" in q) and svc:
                tool_plan.append({"tool": "mcp.get_service", "params": {"name": svc.group(1)}, "reason": "service ownership lookup"})
            if "on-call" in q or "on call" in q:
                tool_plan.append({"tool": "mcp.who_is_on_call", "params": {}, "reason": "on-call roster"})
            if "employee" in q or "who is" in q:
                name = re.search(r"who is ([A-Za-z ]+?)(\?|$| and)", question)
                tool_plan.append({"tool": "mcp.lookup_employee", "params": {"query": (name.group(1).strip() if name else question[:40])}, "reason": "directory lookup"})
            if "incident records" in q or "open incidents" in q:
                tool_plan.append({"tool": "mcp.search_incidents", "params": {"status": "open"} if "open" in q else {}, "reason": "incident records"})
            if "list services" in q:
                tool_plan.append({"tool": "mcp.list_services", "params": {"department": "payments"} if "payment" in q else {}, "reason": "service catalog"})
            if "escalate" in q:
                inc = re.search(r"INC-\d{4}-\d{4}", question, re.I)
                tool_plan.append({"tool": "escalate_incident", "params": {"incident_id": (inc.group(0).upper() if inc else "INC-2025-0419"), "note": question[:200]}, "reason": "user asked to escalate"})
            if "reindex" in q:
                tool_plan.append({"tool": "reindex_knowledge_base", "params": {"reason": question[:100]}, "reason": "user asked to reindex"})
        elif any(m in q for m in research_markers):
            route = "research"
        elif q.strip().rstrip("?!.") in {"hi", "hello", "hey", "thanks", "thank you"}:
            route = "direct"
        filters: dict[str, Any] = {}
        if "payment" in q:
            filters["department"] = "payments"
        if "incident" in q or "outage" in q:
            filters["document_types"] = ["incident"]
        if "last year" in q or "2025" in q:
            filters["created_after"] = "2025-01-01"
        return json.dumps(
            {
                "intent": f"{route} request about: {question[:80]}",
                "route": route,
                "filters": filters,
                "sub_questions": ["What incidents occurred?", "What were the root causes?", "Which root causes recur?"]
                if route == "research"
                else [],
                "tool_plan": tool_plan,
                "rationale": "mock supervisor: keyword heuristics selected the route",
            }
        )

    def _task_rerank(self, system: str, user: str, _: list[BaseMessage]) -> str:
        n = len(EVIDENCE_RE.findall(user)) or user.count("<evidence")
        return json.dumps({"scores": [max(1, 9 - i) for i in range(n)]})

    def _task_rlm_plan(self, system: str, user: str, _: list[BaseMessage]) -> str:
        return (
            "plan = []\n"
            "for q in sub_questions or [question]:\n"
            "    plan.append({'query': q, 'document_types': ['incident'], 'department': 'payments', 'created_after': '2025-01-01'})\n"
            "plan.append({'query': question, 'document_types': ['meeting_notes', 'architecture']})\n"
        )

    def _task_rlm_batch(self, system: str, user: str, _: list[BaseMessage]) -> str:
        findings = []
        for cid, title, section, body in EVIDENCE_RE.findall(user):
            causes = [c for c in ROOT_CAUSE_RE.findall(body) if len(c) < 120 and not c.rstrip().endswith(":")]
            if causes:
                findings.append(f"- {title} [{cid}]: root cause - {causes[0].rstrip('.')}")
            else:
                findings.append(f"- {title} [{cid}]: {body.strip().splitlines()[0][:100]}")
        return "Batch findings:\n" + "\n".join(findings)

    def _task_rlm_aggregate(self, system: str, user: str, _: list[BaseMessage]) -> str:
        lines = [ln for ln in user.splitlines() if ln.startswith("- ")]
        themes: dict[str, list[str]] = {}
        for ln in lines:
            key = "connection pool / stale connections" if "pool" in ln.lower() or "stale" in ln.lower() else (
                "certificates" if "certificate" in ln.lower() else (
                    "database failover / idempotency" if "failover" in ln.lower() or "idempotency" in ln.lower() else "third-party dependency"
                )
            )
            themes.setdefault(key, []).append(ln)
        out = ["Aggregated findings across batches:"]
        for theme, items in themes.items():
            out.append(f"\n**{theme}** ({len(items)} incident(s))")
            out.extend(items)
        return "\n".join(out)

    def _task_response(self, system: str, user: str, _: list[BaseMessage]) -> str:
        if "## Research findings" in user:
            findings = user.split("## Research findings", 1)[1].split("\n## ", 1)[0].strip()
            return "Summary of the research across the retrieved incident reports:\n\n" + findings + "\n\n(Offline mock answer - configure an LLM API key for a real synthesis.)"
        if "## Tool results" in user and "<evidence" not in user:
            tools = user.split("## Tool results", 1)[1].split("\n## ", 1)[0].strip()
            return "Here is what the enterprise systems returned:\n\n" + tools[:1500] + "\n\n(Offline mock answer.)"
        evidence = EVIDENCE_RE.findall(user)
        if not evidence:
            return (
                "I could not find supporting documents for that question in the knowledge base. "
                "Please rephrase or check with the document owner."
            )
        parts = ["Based on the retrieved documents:"]
        for cid, title, section, body in evidence[:4]:
            first = body.strip().splitlines()[0][:160]
            parts.append(f"- {first} [{cid}]")
        parts.append("\n(Offline mock answer - configure an LLM API key for a real synthesis.)")
        return "\n".join(parts)

    def _task_rewrite(self, system: str, user: str, _: list[BaseMessage]) -> str:
        return re.sub(r"\[(\d+)\]", "", user.split("DRAFT:", 1)[-1]).strip() or "Revised answer."

    def _task_chat(self, system: str, user: str, _: list[BaseMessage]) -> str:
        return "Hello! I am the Meridian Knowledge Assistant (offline mock). Ask me about policies, incidents, runbooks or specs."

    # ---------------------------------------------------------------------------------------
    def _generate(self, messages, stop=None, run_manager: CallbackManagerForLLMRun | None = None, **kwargs) -> ChatResult:
        text = self._respond(messages)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

    async def _agenerate(self, messages, stop=None, run_manager: AsyncCallbackManagerForLLMRun | None = None, **kwargs) -> ChatResult:
        return self._generate(messages, stop, None, **kwargs)

    async def _astream(self, messages, stop=None, run_manager: AsyncCallbackManagerForLLMRun | None = None, **kwargs) -> AsyncIterator[ChatGenerationChunk]:
        text = self._respond(messages)
        for i, word in enumerate(text.split(" ")):
            piece = word if i == 0 else " " + word
            chunk = ChatGenerationChunk(message=AIMessageChunk(content=piece))
            if run_manager:
                await run_manager.on_llm_new_token(piece, chunk=chunk)
            yield chunk
