"""Built-in tools: knowledge search, python analysis, and admin actions."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from assistant.auth.models import UserContext
from assistant.auth.rbac import Permission
from assistant.config import get_settings
from assistant.logging import get_logger
from assistant.retrieval import SearchFilters, get_knowledge_index
from assistant.tools.registry import ToolRegistry, ToolSpec
from assistant.tools.safe_python import run_analysis_subprocess

log = get_logger(__name__)


# ------------------------------------------------------------------------------- knowledge search
class KnowledgeSearchParams(BaseModel):
    query: str = Field(min_length=2, max_length=500, description="Natural language search query")
    department: str | None = Field(
        default=None, max_length=50, description="Restrict to one department namespace"
    )
    document_types: list[str] | None = Field(
        default=None,
        description="e.g. ['incident','runbook','policy','architecture','product_spec','meeting_notes']",
    )
    created_after: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    created_before: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    top_k: int = Field(default=8, ge=1, le=20)


def evidence_from_results(results) -> list[dict[str, Any]]:
    """Normalise retrieved chunks into the evidence records stored in graph state."""
    evidence = []
    for r in results:
        evidence.append(
            {
                "chunk_id": r.chunk.chunk_id,
                "doc_id": r.chunk.doc_id,
                "title": r.chunk.title,
                "section": r.chunk.section,
                "text": r.chunk.text,
                "department": r.chunk.metadata.get("department"),
                "document_type": r.chunk.metadata.get("document_type"),
                "access_level": r.chunk.access_level,
                "created_date": r.chunk.metadata.get("created_date"),
                "score": round(r.score, 4),
                "explanation": r.explanation,
            }
        )
    return evidence


async def knowledge_search(params: KnowledgeSearchParams, user: UserContext) -> dict[str, Any]:
    index = await get_knowledge_index()
    if not index.ready or index.retriever is None:
        raise RuntimeError("knowledge index is not ready")
    filters = SearchFilters(
        department=params.department,
        document_types=params.document_types,
        created_after=params.created_after,
        created_before=params.created_before,
    )
    result = await index.retriever.search(params.query, user, filters, top_k=params.top_k)
    return {
        "summary": result.summary(),
        "degraded": result.degraded,
        "notes": result.notes,
        "quarantined": result.quarantined,
        "evidence": evidence_from_results(result.results),
    }


# ------------------------------------------------------------------------------- python analysis
class PythonAnalysisParams(BaseModel):
    code: str = Field(
        min_length=1,
        max_length=6000,
        description="Python code. Input is available as `data`; assign the output to `result`.",
    )
    data: Any = Field(default=None, description="JSON-serialisable input data made available as `data`")
    purpose: str = Field(
        default="",
        max_length=200,
        description="One line explaining what the analysis computes (for the audit trail)",
    )


async def python_analysis(params: PythonAnalysisParams, user: UserContext) -> dict[str, Any]:
    settings = get_settings()
    # UnsafeCodeError is a ValueError: the registry reports it as a clean rejection, not a crash.
    out = await run_analysis_subprocess(
        params.code, params.data, timeout=settings.python_tool_timeout_seconds
    )
    return {"purpose": params.purpose, **out}


# ------------------------------------------------------------------------------- admin tools (HITL)
class ReindexParams(BaseModel):
    reason: str = Field(min_length=3, max_length=200)


async def reindex_knowledge_base(params: ReindexParams, user: UserContext) -> dict[str, Any]:
    index = await get_knowledge_index()
    await index.build(force=True)
    return {"status": "reindexed", "reason": params.reason, **index.status()}


class EscalateIncidentParams(BaseModel):
    incident_id: str = Field(pattern=r"^INC-\d{4}-\d{4}$")
    note: str = Field(min_length=3, max_length=500)


ESCALATIONS: list[dict[str, Any]] = []  # mock side-effect store, visible on /admin/audit


async def escalate_incident(params: EscalateIncidentParams, user: UserContext) -> dict[str, Any]:
    record = {
        "incident_id": params.incident_id,
        "note": params.note,
        "escalated_by": user.username,
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "status": "escalated-to-reliability-review",
    }
    ESCALATIONS.append(record)
    await asyncio.sleep(0.05)  # simulate an external call
    return record


def register_builtin_tools(reg: ToolRegistry) -> None:
    reg.register(
        ToolSpec(
            name="knowledge_search",
            description="Hybrid search over indexed enterprise documents with metadata filters. Returns cited evidence.",
            permission=Permission.KNOWLEDGE_SEARCH,
            params_schema=KnowledgeSearchParams,
            handler=knowledge_search,
            # Includes embedding (with rate-limit backoff), vector fan-out and an LLM rerank: needs more than the default.
            timeout_seconds=45,
            category="retrieval",
        )
    )
    reg.register(
        ToolSpec(
            name="python_analysis",
            description="Run sandboxed Python over structured data (counts, grouping, trends). Input in `data`, output in `result`.",
            permission=Permission.PYTHON_ANALYSIS,
            params_schema=PythonAnalysisParams,
            handler=python_analysis,
            timeout_seconds=get_settings().python_tool_timeout_seconds + 2,
            category="analysis",
        )
    )
    reg.register(
        ToolSpec(
            name="reindex_knowledge_base",
            description="Rebuild the document index (administrative).",
            permission=Permission.ADMIN_TOOLS,
            params_schema=ReindexParams,
            handler=reindex_knowledge_base,
            requires_approval=True,
            timeout_seconds=120,
            category="admin",
        )
    )
    reg.register(
        ToolSpec(
            name="escalate_incident",
            description="Escalate an incident to the quarterly reliability review (administrative, audited).",
            permission=Permission.ADMIN_TOOLS,
            params_schema=EscalateIncidentParams,
            handler=escalate_incident,
            requires_approval=True,
            category="admin",
        )
    )
