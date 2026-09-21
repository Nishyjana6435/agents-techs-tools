"""Conversational memory.

Design (see docs/ARCHITECTURE.md > Memory for the full rationale):

* **Short-term / working memory** - the LangGraph checkpointer. Every turn runs under a
  ``thread_id``; the checkpointer persists the whole graph state (messages, evidence, summary) so
  the next turn resumes with full context. It also powers human-in-the-loop interrupts (the graph
  pauses and resumes from the checkpoint). We use ``InMemorySaver`` in the POC; the interface is
  identical for ``SqliteSaver``/``PostgresSaver`` in production.
* **Rolling summary** - once a thread exceeds ``max_history_turns`` messages, older turns are
  compacted into ``conversation_summary`` so prompts stay bounded while context survives.
* **Long-term memory** - a per-user profile (``LongTermMemory``) holding durable facts extracted
  after each turn: topics of interest, departments queried, preferred answer style, and the last
  few questions. Persisted as JSON on disk, keyed by username, injected into the supervisor and
  response prompts as "What we know about this user". It survives sessions and restarts.
* **Feedback** - thumbs up/down per answer are stored alongside the profile and forwarded to
  LangSmith as run feedback, closing the loop for answer-quality evaluation.
"""
from assistant.memory.long_term import LongTermMemory, get_long_term_memory
from assistant.memory.short_term import get_checkpointer, summarize_history

__all__ = ["LongTermMemory", "get_long_term_memory", "get_checkpointer", "summarize_history"]
