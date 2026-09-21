from __future__ import annotations

from fastapi import APIRouter, Depends

from assistant.api.deps import get_current_user, get_rate_limiter, require_permission
from assistant.auth import Permission, UserContext
from assistant.config import get_settings
from assistant.llm.provider import model_name
from assistant.retrieval import get_knowledge_index
from assistant.tools import get_tool_registry
from assistant.tools.builtin import ESCALATIONS

router = APIRouter(tags=["system"])


@router.get("/health")
async def health() -> dict:
    s = get_settings()
    index = await get_knowledge_index()
    registry = await get_tool_registry()
    return {
        "status": "ok" if index.ready else "degraded",
        "llm": {"provider": s.resolved_llm_provider, "primary_model": model_name("primary"), "worker_model": model_name("worker")},
        "embeddings": index.embedder.name,
        "vector_store": index.store.name,
        "index": index.status(),
        "tools": [t.name for t in registry.all()],
        "mcp_mode": "http" if s.mcp_server_url else "in-process",
        "langsmith": {"enabled": s.langsmith_enabled, "project": s.langsmith_project if s.langsmith_enabled else None},
        "rate_limit": {"capacity": s.rate_limit_capacity, "refill_per_second": s.rate_limit_refill_per_second},
    }


@router.get("/tools")
async def tools(user: UserContext = Depends(get_current_user)) -> dict:
    registry = await get_tool_registry()
    return {
        "role": user.role.value,
        "tools": [
            {"name": t.name, "description": t.description, "permission": t.permission.value, "requires_approval": t.requires_approval, "category": t.category, "allowed": registry.is_allowed(user, t.name)}
            for t in registry.all()
        ],
    }


@router.get("/rate-limit")
async def rate_limit(user: UserContext = Depends(get_current_user)) -> dict:
    return get_rate_limiter().snapshot(user.username)


@router.get("/admin/audit")
async def audit(user: UserContext = Depends(require_permission(Permission.ADMIN_TOOLS))) -> dict:
    registry = await get_tool_registry()
    return {"tool_audit_log": registry.audit_log[-200:], "escalations": ESCALATIONS}
