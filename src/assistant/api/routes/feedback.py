"""Answer-quality feedback loop: stored in long-term memory and forwarded to LangSmith."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from assistant.api.deps import get_current_user
from assistant.auth import UserContext
from assistant.config import get_settings
from assistant.logging import get_logger
from assistant.memory import get_long_term_memory

router = APIRouter(tags=["feedback"])
log = get_logger(__name__)


class FeedbackRequest(BaseModel):
    thread_id: str = Field(max_length=64)
    run_id: str | None = Field(default=None, max_length=64)
    score: int = Field(ge=-1, le=1, description="1 = helpful, -1 = not helpful, 0 = neutral")
    comment: str = Field(default="", max_length=500)


@router.post("/feedback")
async def feedback(body: FeedbackRequest, user: UserContext = Depends(get_current_user)) -> dict:
    await get_long_term_memory().record_feedback(user.username, body.thread_id, body.run_id, body.score, body.comment)
    forwarded = False
    settings = get_settings()
    if settings.langsmith_enabled and body.run_id:
        try:
            from langsmith import Client

            def send() -> None:
                Client(api_key=settings.langsmith_api_key).create_feedback(
                    run_id=body.run_id, key="user_score", score=body.score, comment=body.comment or None
                )

            await asyncio.wait_for(asyncio.to_thread(send), timeout=8)
            forwarded = True
        except Exception as exc:  # noqa: BLE001
            log.warning("langsmith_feedback_failed", error=str(exc)[:120])
    log.info("feedback_recorded", user=user.username, score=body.score, run_id=body.run_id, forwarded=forwarded)
    return {"status": "recorded", "forwarded_to_langsmith": forwarded}
