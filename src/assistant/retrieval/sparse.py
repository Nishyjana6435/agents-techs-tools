"""Sparse (keyword) retrieval with BM25.

BM25 is kept in-process: the corpus is small and a Python index rebuilds in milliseconds.
Sparse retrieval matters for exact identifiers (``INC-2025-0419``, ``PAY-2211``, ``RB-PAY-001``)
where dense embeddings are notoriously weak.

Trade-off: Pinecone also supports sparse vectors natively (dotproduct indexes); we chose local
BM25 so hybrid search works identically with the in-memory fallback and does not require a
server-side sparse encoder. Swapping to Pinecone sparse vectors is a contained change here.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from rank_bm25 import BM25Okapi

from assistant.retrieval.models import Chunk

TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9\-\.]*")
STOPWORDS = frozenset(
    [
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "for",
        "on",
        "at",
        "by",
        "with",
        "from",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "as",
        "into",
        "over",
        "under",
        "about",
        "all",
        "any",
    ]
)


def tokenize(text: str) -> list[str]:
    tokens = TOKEN_RE.findall(text.lower())
    out: list[str] = []
    for t in tokens:
        t = t.strip(".-")
        if not t or t in STOPWORDS:
            continue
        out.append(t)
        # Also index identifier fragments, e.g. "inc-2025-0419" -> "inc", "2025", "0419".
        if "-" in t:
            out.extend(p for p in t.split("-") if p and p not in STOPWORDS)
    return out


class BM25Index:
    def __init__(self, chunks: list[Chunk]) -> None:
        self.chunks = chunks
        self._bm25 = (
            BM25Okapi([tokenize(f"{c.title} {c.section} {c.text}") for c in chunks]) if chunks else None
        )

    def search(
        self, query: str, k: int, allow: Callable[[Chunk], bool] | None = None
    ) -> list[tuple[Chunk, float]]:
        if not self._bm25:
            return []
        scores = self._bm25.get_scores(tokenize(query))
        ranked = sorted(range(len(self.chunks)), key=lambda i: scores[i], reverse=True)
        results: list[tuple[Chunk, float]] = []
        for i in ranked:
            if scores[i] <= 0:
                break
            chunk = self.chunks[i]
            if allow and not allow(chunk):
                continue
            results.append((chunk, float(scores[i])))
            if len(results) >= k:
                break
        return results
