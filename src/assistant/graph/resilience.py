"""Failure containment for graph nodes.

In a multi-agent graph one failing node can cascade ("butterfly effect"): a supervisor crash
means no route, a retrieval timeout means the response agent hallucinates, etc. ``resilient``
wraps a node so that any exception is converted into (a) an ``error`` activity event, (b) an
entry in ``state.errors`` and (c) a node-specific *fallback state update* that keeps the graph
moving on a safe path (e.g. supervisor -> plain retrieval; retrieval -> empty evidence flagged
degraded; response -> honest "I could not generate an answer" message).

Together with the bounded rewrite loop and tool-level timeouts, this makes every failure mode
in the brief (LLM, vector DB, MCP, tool timeout, invalid request) observable and non-fatal.
"""
from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.errors import GraphInterrupt

from assistant.graph.events import emit
from assistant.graph.state import AssistantState
from assistant.logging import get_logger

log = get_logger(__name__)

NodeFn = Callable[[AssistantState], Awaitable[dict[str, Any]]]


def resilient(name: str, fallback: Callable[[AssistantState, Exception], dict[str, Any]]) -> Callable[[NodeFn], NodeFn]:
    def decorator(fn: NodeFn) -> NodeFn:
        @functools.wraps(fn)
        async def wrapper(state: AssistantState) -> dict[str, Any]:
            emit("node_start", name, f"entering {name}")
            try:
                update = await fn(state)
            except GraphInterrupt:
                raise  # human-in-the-loop pauses must propagate untouched
            except Exception as exc:  # noqa: BLE001 - contain everything else
                log.exception("node_failed", node=name)
                message = f"{exc.__class__.__name__}: {str(exc)[:200]}"
                emit("error", name, f"{name} failed, applying fallback: {message}")
                update = fallback(state, exc)
                update.setdefault("errors", [])
                update["errors"] = [*update["errors"], f"{name}: {message}"]
                update["degraded"] = True
            update.setdefault("node_path", [])
            update["node_path"] = [*update["node_path"], name]
            emit("node_end", name, f"leaving {name}")
            return update

        return wrapper

    return decorator
