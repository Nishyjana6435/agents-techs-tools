"""Input validation for the three untrusted boundaries: user, tool parameters, retrieved content."""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from pydantic import BaseModel, ValidationError as PydanticValidationError

from assistant.config import get_settings


class ValidationError(ValueError):
    """Raised for any validation failure. Message is safe to show to the user."""


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def validate_user_message(text: str) -> str:
    settings = get_settings()
    if text is None:
        raise ValidationError("Message is required.")
    text = unicodedata.normalize("NFKC", text)
    text = _CONTROL.sub("", text).strip()
    if not text:
        raise ValidationError("Message is empty.")
    if len(text) > settings.max_message_chars:
        raise ValidationError(f"Message too long ({len(text)} chars). Limit is {settings.max_message_chars}.")
    return text


def validate_tool_params(schema: type[BaseModel], params: dict[str, Any]) -> BaseModel:
    """Validate LLM-produced tool arguments against the tool's pydantic schema.

    The LLM is an untrusted caller: it may hallucinate parameters, pass the wrong types, or be
    steered by an injection into passing hostile values. Pydantic gives us strict typing, ranges
    and enum constraints in one place, with an error message we can feed back to the model.
    """
    try:
        return schema.model_validate(params or {})
    except PydanticValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        raise ValidationError(f"Invalid tool parameters: {problems}") from exc


MAX_CHUNK_CHARS = 8000


def validate_retrieved_chunk(text: str, metadata: dict[str, Any]) -> tuple[bool, str]:
    """Return ``(ok, reason)``. Retrieved content is untrusted too: it may be oversized,
    binary garbage, or missing attribution metadata we need for citations."""
    if not text or not text.strip():
        return False, "empty chunk"
    if len(text) > MAX_CHUNK_CHARS:
        return False, f"chunk too large ({len(text)} chars)"
    printable_ratio = sum(ch.isprintable() or ch in "\n\t" for ch in text) / len(text)
    if printable_ratio < 0.9:
        return False, "chunk contains too many non-printable characters"
    if not metadata.get("doc_id") or not metadata.get("title"):
        return False, "chunk missing attribution metadata (doc_id/title)"
    return True, "ok"
