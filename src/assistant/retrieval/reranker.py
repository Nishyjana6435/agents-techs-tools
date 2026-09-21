"""Reranking layer (bonus requirement).

After hybrid fusion we have ~2x``top_k`` candidates ordered by RRF. Rank fusion is good at
recall, less good at precision. The reranker rescoring step asks the *worker* LLM to grade each
candidate's relevance to the query on a 0-10 scale in a single listwise call (cheap: one call
per query, candidates are short). If the LLM is unavailable we fall back to a lexical-overlap
rerank so the pipeline never blocks on this optional stage.

Why listwise LLM rather than a cross-encoder: no extra model download, provider-agnostic, and the
judgement is visible in the LangSmith trace (explainability).
"""

from __future__ import annotations

import re

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from assistant.llm import LLMError, ainvoke_json, get_llm
from assistant.logging import get_logger
from assistant.retrieval.models import RetrievedChunk
from assistant.retrieval.sparse import tokenize

log = get_logger(__name__)


class RerankScores(BaseModel):
    scores: list[float] = Field(default_factory=list)


RERANK_SYSTEM = """# task: rerank
You grade how useful each evidence passage is for answering the user's query.
Return JSON only: {"scores": [s1, s2, ...]} with one number from 0 (irrelevant) to 10 (directly answers)
per passage, in the same order as given. Do not add commentary."""


def _lexical_scores(query: str, candidates: list[RetrievedChunk]) -> list[float]:
    q = set(tokenize(query))
    out = []
    for c in candidates:
        toks = set(tokenize(f"{c.chunk.title} {c.chunk.section} {c.chunk.text}"))
        out.append(10.0 * len(q & toks) / max(1, len(q)))
    return out


async def rerank(
    query: str, candidates: list[RetrievedChunk], top_k: int
) -> tuple[list[RetrievedChunk], str]:
    """Return ``(reranked_top_k, method)`` where method is ``llm`` or ``lexical-fallback``."""
    if not candidates:
        return [], "none"
    method = "llm"
    scores: list[float]
    try:
        passages = "\n".join(
            f'<evidence id="{i + 1}" title="{c.chunk.title}" section="{c.chunk.section}">\n{c.chunk.text[:700]}\n</evidence>'
            for i, c in enumerate(candidates)
        )
        result = await ainvoke_json(
            get_llm("worker"),
            [SystemMessage(content=RERANK_SYSTEM), HumanMessage(content=f"Query: {query}\n\n{passages}")],
            RerankScores,
        )
        scores = list(result.scores)
        if abs(len(scores) - len(candidates)) > 2:
            raise LLMError(f"rerank returned {len(scores)} scores for {len(candidates)} candidates")
        if len(scores) != len(candidates):
            # Models occasionally drop or duplicate one entry; pad with a neutral grade / truncate rather
            # than discarding the whole rerank.
            log.warning("rerank_count_mismatch", scores=len(scores), candidates=len(candidates))
            scores = (scores + [5.0] * len(candidates))[: len(candidates)]
    except LLMError as exc:
        log.warning("rerank_fallback_lexical", error=str(exc)[:160])
        method = "lexical-fallback"
        scores = _lexical_scores(query, candidates)

    for c, s in zip(candidates, scores, strict=True):
        # Blend: rerank dominates but fusion rank breaks ties so we never fully discard hybrid signal.
        c.rerank_score = round(float(s) + c.fused_score, 4)
        c.explanation += f"; rerank({method})={float(s):.1f}"
    ranked = sorted(candidates, key=lambda c: c.rerank_score or 0.0, reverse=True)[:top_k]
    for i, c in enumerate(ranked, start=1):
        c.rank = i
    return ranked, method


def truncate_for_prompt(text: str, limit: int = 1200) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " ..."
