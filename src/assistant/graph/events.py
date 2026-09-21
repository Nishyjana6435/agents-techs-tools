"""Activity events streamed to the UI's Agent Activity Panel.

Nodes call ``emit(...)``; LangGraph's ``get_stream_writer`` forwards the payload on the
``custom`` stream, which the API turns into SSE ``activity`` events. When a node runs outside
a graph (unit tests) the writer is a no-op, so nodes never need to special-case it.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from langgraph.config import get_stream_writer
from pydantic import BaseModel, Field

EventKind = Literal[
    "node_start", "node_end", "state", "tool_call", "tool_result", "retrieval", "memory",
    "validation", "security", "rlm", "approval", "error", "info",
]


class ActivityEvent(BaseModel):
    kind: EventKind
    node: str
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
    ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="milliseconds"))


def emit(kind: EventKind, node: str, message: str, **data: Any) -> ActivityEvent:
    event = ActivityEvent(kind=kind, node=node, message=message, data=data)
    try:
        writer = get_stream_writer()
    except Exception:  # noqa: BLE001 - not inside a graph run
        writer = None
    if writer:
        writer(event.model_dump())
    return event
