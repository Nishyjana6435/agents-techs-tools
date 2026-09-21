"""LLM provider factory.

Model selection rationale (also in docs/ARCHITECTURE.md):

* **Primary** ``claude-opus-5`` - supervisor routing, RLM aggregation and final answer generation.
  These steps carry the reasoning load (intent decomposition, synthesising evidence into a grounded,
  cited answer) and are the ones users judge quality on. Adaptive thinking is on by default.
* **Worker** ``claude-haiku-4-5`` - RLM batch workers, reranking and other high-volume/low-stakes
  steps. Cheap and fast, so recursive decomposition into many sub-calls stays affordable.
* ``openai`` is supported as a drop-in alternative; ``mock`` is a deterministic offline provider used
  for tests and for running the platform without credentials.

Every model is built with an explicit timeout and bounded retries so a hung provider degrades to a
handled ``LLMError`` instead of a hanging request.
"""
from assistant.llm.provider import LLMError, ainvoke_json, get_llm, llm_call

__all__ = ["LLMError", "ainvoke_json", "get_llm", "llm_call"]
