"""Structured logging.

We use ``structlog`` so every log line is a key/value event. In dev the console renderer is
human friendly; in prod (``LOG_JSON=true``) lines are JSON, ready for Loki/Datadog/CloudWatch.

Request/conversation correlation ids are bound via ``bind_request_context`` (contextvars) so
every log emitted while a request is being processed, including inside LangGraph nodes and tool
calls, carries ``request_id``, ``user`` and ``thread_id`` without threading them through
function signatures.
"""

from __future__ import annotations

import logging
import sys

import structlog

from assistant.config import get_settings


def configure_logging() -> None:
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]
    renderer = structlog.processors.JSONRenderer() if settings.log_json else structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[*shared_processors, structlog.processors.format_exc_info, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(level=level, stream=sys.stdout, format="%(message)s")
    # Quieten noisy libraries; our own events are what matter.
    for noisy in ("httpx", "httpcore", "uvicorn.access", "pinecone", "anthropic", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


def bind_request_context(**kwargs: object) -> None:
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_request_context() -> None:
    structlog.contextvars.clear_contextvars()
