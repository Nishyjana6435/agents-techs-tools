"""FastAPI entrypoint. Run with: ``uvicorn assistant.api.main:app --reload``."""
from __future__ import annotations

import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from assistant.api.routes import auth, chat, feedback, system
from assistant.config import get_settings
from assistant.graph import get_graph
from assistant.logging import bind_request_context, clear_request_context, configure_logging, get_logger
from assistant.retrieval import get_knowledge_index
from assistant.tools import get_tool_registry

log = get_logger(__name__)


def configure_langsmith() -> None:
    """LangSmith is enabled purely via environment variables read by langchain-core."""
    s = get_settings()
    if s.langsmith_enabled:
        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGSMITH_API_KEY"] = s.langsmith_api_key or ""
        os.environ["LANGSMITH_PROJECT"] = s.langsmith_project
        log.info("langsmith_tracing_enabled", project=s.langsmith_project)
    else:
        os.environ["LANGSMITH_TRACING"] = "false"
        log.warning("langsmith_tracing_disabled", reason="LANGSMITH_API_KEY not set")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    configure_langsmith()
    s = get_settings()
    log.info("startup", llm=s.resolved_llm_provider, embeddings=s.resolved_embedding_provider, pinecone=s.pinecone_enabled)
    # Warm everything so the first user request is fast and startup failures are visible in logs.
    await get_knowledge_index()
    await get_tool_registry()
    get_graph()
    log.info("ready")
    yield
    log.info("shutdown")


def create_app() -> FastAPI:
    app = FastAPI(title=get_settings().app_name, version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        bind_request_context(request_id=request_id, path=request.url.path, method=request.method)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:  # noqa: BLE001
            log.exception("unhandled_error")
            clear_request_context()
            return JSONResponse({"error": "internal_error", "request_id": request_id}, status_code=500)
        response.headers["X-Request-ID"] = request_id
        log.info("request", status=response.status_code, duration_ms=int((time.perf_counter() - started) * 1000))
        clear_request_context()
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": exc.detail}
        return JSONResponse({"error": exc.status_code, **detail}, status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse({"error": 422, "message": "Invalid request", "details": exc.errors()}, status_code=422)

    app.include_router(auth.router)
    app.include_router(chat.router)
    app.include_router(feedback.router)
    app.include_router(system.router)
    return app


app = create_app()
