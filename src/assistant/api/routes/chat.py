from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from assistant.api.deps import get_current_user, rate_limited
from assistant.api.streaming import stream_chat
from assistant.auth import UserContext
from assistant.config import get_settings
from assistant.graph import get_graph
from assistant.security import ValidationError, validate_user_message

router = APIRouter(tags=["chat"])
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=get_settings().max_message_chars + 100)
    thread_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]*$")


class ResumeRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    decision: str = Field(pattern=r"^(approve|reject)$")


def _thread_owner_check(thread_id: str, user: UserContext) -> None:
    """Threads are private to the user who created them (prevents cross-user memory leakage)."""
    state = get_graph().get_state({"configurable": {"thread_id": thread_id}})
    owner = (state.values or {}).get("user", {}).get("username") if state and state.values else None
    if owner and owner != user.username:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This conversation belongs to another user")


@router.post("/chat")
async def chat(body: ChatRequest, user: UserContext = Depends(rate_limited)) -> StreamingResponse:
    try:
        message = validate_user_message(body.message)
    except ValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    thread_id = body.thread_id or f"{user.username}-{uuid.uuid4().hex[:10]}"
    _thread_owner_check(thread_id, user)
    return StreamingResponse(stream_chat(user, thread_id, message=message), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/chat/resume")
async def resume(body: ResumeRequest, user: UserContext = Depends(rate_limited)) -> StreamingResponse:
    _thread_owner_check(body.thread_id, user)
    state = get_graph().get_state({"configurable": {"thread_id": body.thread_id}})
    if not state.tasks or not any(getattr(t, "interrupts", None) for t in state.tasks):
        raise HTTPException(status.HTTP_409_CONFLICT, "No pending approval on this thread")
    return StreamingResponse(stream_chat(user, body.thread_id, resume=body.decision), media_type="text/event-stream", headers=SSE_HEADERS)


@router.get("/threads/{thread_id}")
async def thread_state(thread_id: str, user: UserContext = Depends(get_current_user)) -> dict:
    _thread_owner_check(thread_id, user)
    snapshot = get_graph().get_state({"configurable": {"thread_id": thread_id}})
    values = snapshot.values or {}
    return {
        "thread_id": thread_id,
        "messages": [{"role": m.type, "content": m.content if isinstance(m.content, str) else str(m.content)} for m in values.get("messages", [])],
        "conversation_summary": values.get("conversation_summary", ""),
        "route": values.get("route"),
        "node_path": values.get("node_path", []),
        "pending_approval": values.get("pending_approval"),
        "next": list(snapshot.next or []),
    }
