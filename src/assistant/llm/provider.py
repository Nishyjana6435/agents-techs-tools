from __future__ import annotations

import asyncio
import json
import re
from functools import lru_cache
from typing import Any, Literal, TypeVar

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from pydantic import BaseModel, ValidationError

from assistant.config import Settings, get_settings
from assistant.logging import get_logger

log = get_logger(__name__)
T = TypeVar("T", bound=BaseModel)
Tier = Literal["primary", "worker"]


class LLMError(RuntimeError):
    """Raised when the model call fails after retries/timeouts. Safe to surface as a degraded answer."""


@lru_cache(maxsize=4)
def _build(provider: str, tier: Tier, settings_key: str) -> BaseChatModel:
    settings = get_settings()
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        model = settings.llm_model if tier == "primary" else settings.llm_worker_model
        return ChatAnthropic(
            model=model,
            max_tokens=settings.llm_max_tokens,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
            api_key=settings.anthropic_api_key,
        )
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        model = settings.openai_model if tier == "primary" else settings.openai_worker_model
        return ChatOpenAI(
            model=model,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
            api_key=settings.openai_api_key,
        )
    from assistant.llm.mock import MockChatModel

    return MockChatModel(tier=tier)


def get_llm(tier: Tier = "primary", settings: Settings | None = None) -> BaseChatModel:
    settings = settings or get_settings()
    provider = settings.resolved_llm_provider
    return _build(provider, tier, f"{provider}:{tier}")


def model_name(tier: Tier = "primary") -> str:
    settings = get_settings()
    provider = settings.resolved_llm_provider
    if provider == "anthropic":
        return settings.llm_model if tier == "primary" else settings.llm_worker_model
    if provider == "openai":
        return settings.openai_model if tier == "primary" else settings.openai_worker_model
    return f"mock-{tier}"


async def llm_call(llm: BaseChatModel, messages: list[BaseMessage], *, timeout: float | None = None, **kwargs: Any):
    """Invoke with an outer timeout and convert every failure into ``LLMError``.

    LangChain already retries transient HTTP errors; the outer ``wait_for`` protects against a
    provider that accepts the connection and then stalls.
    """
    timeout = timeout or get_settings().llm_timeout_seconds * 1.5
    try:
        return await asyncio.wait_for(llm.ainvoke(messages, **kwargs), timeout=timeout)
    except TimeoutError as exc:
        raise LLMError("LLM call timed out") from exc
    except Exception as exc:  # noqa: BLE001
        raise LLMError(f"LLM call failed: {exc.__class__.__name__}: {exc}") from exc


_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = _JSON_BLOCK.search(text)
        if not m:
            raise
        return json.loads(m.group(0))


async def ainvoke_json(llm: BaseChatModel, messages: list[BaseMessage], schema: type[T], *, default: T | None = None) -> T:
    """Ask the model for JSON and validate it against ``schema``.

    We deliberately parse ourselves instead of relying on provider-specific structured-output
    features: it keeps every provider (and the mock) on one code path and gives us a single place
    to log and recover from malformed output. On failure we return ``default`` if provided.
    """
    try:
        response = await llm_call(llm, messages)
        content = response.content if isinstance(response.content, str) else "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in response.content
        )
        return schema.model_validate(extract_json(content))
    except (LLMError, ValidationError, json.JSONDecodeError, ValueError) as exc:
        log.warning("structured_output_failed", schema=schema.__name__, error=str(exc)[:200])
        if default is not None:
            return default
        raise LLMError(f"Could not obtain valid {schema.__name__} from model: {exc}") from exc


def message_text(message: BaseMessage) -> str:
    """Anthropic returns content as a list of blocks when thinking is on; flatten to text."""
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
