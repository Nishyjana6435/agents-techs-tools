"""Turn a LangGraph run into Server-Sent Events.

Event types sent to the client (all JSON payloads):
  meta       - thread_id, run_id (LangSmith trace id), user
  activity   - ActivityEvent from any node (Agent Activity Panel)
  token      - text delta from the response agent (streaming answer)
  interrupt  - human approval requested; client must call /chat/resume
  answer     - final validated answer + evidence + validation report + trace url
  error      - terminal error (graceful: still followed by done)
  done       - end of stream
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from assistant.auth import UserContext
from assistant.config import get_settings
from assistant.graph import get_graph
from assistant.graph.state import user_to_state
from assistant.logging import bind_request_context, clear_request_context, get_logger

log = get_logger(__name__)


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


_project_url_cache: dict[str, str] = {}


async def _trace_url(run_id: str) -> str | None:
    """Build the LangSmith run URL locally.

    Traces are ingested asynchronously, so reading the run right after the turn returns 404. The
    URL only needs the tenant and project ids, which we look up once and cache; LangSmith's UI polls
    until the run arrives (``?poll=true``).
    """
    settings = get_settings()
    if not settings.langsmith_enabled:
        return None
    try:
        if settings.langsmith_project not in _project_url_cache:
            from langsmith import Client

            def lookup() -> str:
                client = Client(api_key=settings.langsmith_api_key)
                project = client.read_project(project_name=settings.langsmith_project)
                return f"{client._host_url}/o/{client._get_tenant_id()}/projects/p/{project.id}"

            _project_url_cache[settings.langsmith_project] = await asyncio.wait_for(
                asyncio.to_thread(lookup), timeout=8
            )
        return f"{_project_url_cache[settings.langsmith_project]}/r/{run_id}?poll=true"
    except Exception as exc:
        log.warning("trace_url_unavailable", error=str(exc)[:120])
        return None


def _answer_payload(state: dict[str, Any], run_id: str, trace_url: str | None) -> dict[str, Any]:
    evidence = state.get("evidence") or []
    return {
        "answer": state.get("final_answer") or state.get("draft_answer") or "",
        "route": state.get("route"),
        "intent": state.get("intent"),
        "supervisor_rationale": state.get("supervisor_rationale"),
        "evidence": [
            {
                "id": i + 1,
                "title": e["title"],
                "section": e.get("section"),
                "doc_id": e["doc_id"],
                "document_type": e.get("document_type"),
                "department": e.get("department"),
                "access_level": e.get("access_level"),
                "created_date": e.get("created_date"),
                "score": e.get("score"),
                "why": e.get("explanation"),
                "excerpt": e["text"][:400],
            }
            for i, e in enumerate(evidence)
        ],
        "validation": state.get("validation") or {},
        "tool_results": [
            {k: v for k, v in r.items() if k != "output"}
            | {"citation_id": len(evidence) + i + 1, "output_preview": str(r.get("output"))[:300]}
            for i, r in enumerate(state.get("tool_results") or [])
        ],
        "rlm_trace": state.get("rlm_trace") or [],
        "memory_updates": state.get("memory_updates") or [],
        "node_path": state.get("node_path") or [],
        "errors": state.get("errors") or [],
        "degraded": bool(state.get("degraded")),
        "blocked": state.get("route") == "blocked",
        "run_id": run_id,
        "trace_url": trace_url,
        "langsmith_project": get_settings().langsmith_project if get_settings().langsmith_enabled else None,
    }


async def stream_chat(
    user: UserContext, thread_id: str, message: str | None = None, resume: str | None = None
) -> AsyncIterator[str]:
    graph = get_graph()
    run_id = str(uuid.uuid4())
    config = {
        "configurable": {"thread_id": thread_id},
        "run_id": run_id,
        "run_name": "chat_turn",
        "tags": [f"user:{user.username}", f"role:{user.role.value}"],
        "metadata": {"thread_id": thread_id, "user": user.username, "role": user.role.value},
    }
    bind_request_context(thread_id=thread_id, user=user.username, run_id=run_id)
    yield sse(
        "meta", {"thread_id": thread_id, "run_id": run_id, "user": user.username, "role": user.role.value}
    )

    if resume is not None:
        graph_input: Any = Command(resume=resume)
    else:
        graph_input = {
            "messages": [HumanMessage(content=message or "")],
            "user": user_to_state(user),
            "thread_id": thread_id,
        }

    interrupted = False
    try:
        async for mode, payload in graph.astream(
            graph_input, config=config, stream_mode=["custom", "messages", "updates"]
        ):
            if mode == "custom":
                yield sse("activity", payload)
            elif mode == "messages":
                chunk, meta = payload
                if meta.get("langgraph_node") == "response" and "final_answer" in (meta.get("tags") or []):
                    text = (
                        chunk.content
                        if isinstance(chunk.content, str)
                        else "".join(p.get("text", "") for p in chunk.content if isinstance(p, dict))
                    )
                    if text:
                        yield sse("token", {"text": text})
            elif mode == "updates" and isinstance(payload, dict) and "__interrupt__" in payload:
                interrupted = True
                for intr in payload["__interrupt__"]:
                    yield sse(
                        "interrupt",
                        {
                            "thread_id": thread_id,
                            **(intr.value if isinstance(intr.value, dict) else {"message": str(intr.value)}),
                        },
                    )
        if not interrupted:
            state = graph.get_state(config).values
            trace_url = await _trace_url(run_id)
            yield sse("answer", _answer_payload(state, run_id, trace_url))
    except Exception as exc:
        log.exception("chat_stream_failed")
        yield sse(
            "error",
            {
                "message": "The assistant hit an unexpected error. Please try again.",
                "detail": f"{exc.__class__.__name__}: {str(exc)[:200]}",
                "run_id": run_id,
            },
        )
    finally:
        yield sse("done", {"run_id": run_id, "interrupted": interrupted})
        clear_request_context()
