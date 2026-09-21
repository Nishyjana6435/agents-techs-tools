from __future__ import annotations

from functools import lru_cache

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver


@lru_cache
def get_checkpointer() -> InMemorySaver:
    """Process-wide checkpointer. Swap for ``AsyncSqliteSaver``/``AsyncPostgresSaver`` in prod."""
    return InMemorySaver()


def summarize_history(messages: list[BaseMessage], keep_last: int) -> tuple[str, list[BaseMessage]]:
    """Deterministic compaction of older turns into a short summary.

    We intentionally avoid an LLM call here: the summary must be cheap, predictable and never a
    failure point. It lists the earlier questions and the first line of each answer, which is what
    the supervisor needs for follow-up resolution ("the second incident you mentioned").
    """
    if len(messages) <= keep_last:
        return "", messages
    older, recent = messages[:-keep_last], messages[-keep_last:]
    lines = []
    for m in older:
        text = m.content if isinstance(m.content, str) else str(m.content)
        first = text.strip().splitlines()[0][:140] if text.strip() else ""
        if isinstance(m, HumanMessage):
            lines.append(f"User asked: {first}")
        elif isinstance(m, AIMessage):
            lines.append(f"Assistant answered: {first}")
    return "\n".join(lines), recent
