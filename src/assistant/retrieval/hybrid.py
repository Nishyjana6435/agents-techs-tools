"""Hybrid retrieval: dense (vector store) + sparse (BM25) fused with weighted reciprocal rank fusion.

Why RRF instead of score interpolation: cosine similarities and BM25 scores live on different
scales and distributions; RRF only needs ranks, is robust, and has one intuitive knob
(``hybrid_dense_weight``) that we expose in settings.

Access control is enforced *inside* retrieval, not after: the dense query carries an
``access_level $in readable_levels`` metadata filter and the BM25 path applies the same predicate,
so a chunk above the user's clearance never even becomes a candidate.

Namespaces: one Pinecone namespace per department. If the supervisor inferred a department we
query only that namespace; otherwise we fan out to all namespaces concurrently with
``asyncio.gather`` and merge.

Failure modes handled here:
* embedding provider failure  -> sparse-only search, ``degraded=True``
* vector store failure/timeout -> sparse-only search, ``degraded=True``
* both fail                    -> empty result with an explanatory message (caller tells the user)
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from assistant.auth.models import UserContext
from assistant.config import get_settings
from assistant.logging import get_logger
from assistant.retrieval.embeddings import Embedder, embed_with_timeout
from assistant.retrieval.models import Chunk, RetrievedChunk, SearchFilters
from assistant.retrieval.reranker import rerank
from assistant.retrieval.sparse import BM25Index
from assistant.retrieval.vector_store import VectorStore, VectorStoreError, matches_filter
from assistant.security.injection import scan_prompt_injection
from assistant.security.validation import validate_retrieved_chunk

log = get_logger(__name__)


@dataclass
class SearchResult:
    query: str
    results: list[RetrievedChunk]
    dense_hits: int = 0
    sparse_hits: int = 0
    namespaces_queried: list[str] = field(default_factory=list)
    filters: str = "none"
    rerank_method: str = "none"
    degraded: bool = False
    notes: list[str] = field(default_factory=list)
    quarantined: list[str] = field(default_factory=list)  # chunk ids removed by injection screening
    duration_ms: int = 0

    def summary(self) -> str:
        return (
            f"{len(self.results)} results (dense={self.dense_hits}, sparse={self.sparse_hits}, "
            f"namespaces={','.join(self.namespaces_queried) or '-'}, filters=[{self.filters}], "
            f"rerank={self.rerank_method}{', DEGRADED' if self.degraded else ''})"
        )


class HybridRetriever:
    def __init__(
        self, embedder: Embedder, store: VectorStore, bm25: BM25Index, chunks_by_id: dict[str, Chunk]
    ) -> None:
        self.embedder = embedder
        self.store = store
        self.bm25 = bm25
        self.chunks_by_id = chunks_by_id
        self.settings = get_settings()

    # ---------------------------------------------------------------------------------------
    async def _dense(
        self, query: str, namespaces: list[str], metadata_filter: dict, k: int
    ) -> tuple[dict[str, float], list[str]]:
        notes: list[str] = []
        try:
            vector = (await embed_with_timeout(self.embedder, [query], timeout=20))[0]
        except Exception as exc:
            log.warning("dense_search_embedding_failed", error=f"{exc.__class__.__name__}: {str(exc)[:200]}")
            notes.append(f"embedding failed ({exc.__class__.__name__}: {str(exc)[:80]}); dense search skipped")
            return {}, notes

        async def one(ns: str):
            try:
                return await self.store.query(vector, k, ns, metadata_filter)
            except VectorStoreError as exc:
                log.warning("dense_search_store_failed", namespace=ns, error=str(exc)[:200])
                notes.append(f"vector store error in namespace {ns}: {exc}")
                return []

        per_ns = await asyncio.gather(*(one(ns) for ns in namespaces))
        merged: dict[str, float] = {}
        for hits in per_ns:
            for chunk_id, score, meta in hits:
                # Make sure we can attribute the hit even if the local chunk cache is stale.
                if chunk_id not in self.chunks_by_id and meta.get("text"):
                    self.chunks_by_id[chunk_id] = Chunk(
                        chunk_id=chunk_id,
                        doc_id=meta.get("doc_id", "unknown"),
                        title=meta.get("title", "Untitled"),
                        section=meta.get("section", ""),
                        text=meta["text"],
                        namespace=meta.get("department", "corp"),
                        metadata=meta,
                    )
                merged[chunk_id] = max(score, merged.get(chunk_id, -1))
        return merged, notes

    def _sparse(self, query: str, namespaces: set[str], metadata_filter: dict, k: int) -> dict[str, float]:
        def allow(chunk: Chunk) -> bool:
            return chunk.namespace in namespaces and matches_filter(chunk.metadata, metadata_filter)

        return {c.chunk_id: s for c, s in self.bm25.search(query, k, allow)}

    @staticmethod
    def _rrf(
        dense: dict[str, float], sparse: dict[str, float], dense_weight: float, k: int = 60
    ) -> dict[str, float]:
        fused: dict[str, float] = {}
        for rank, cid in enumerate(sorted(dense, key=dense.get, reverse=True), start=1):
            fused[cid] = fused.get(cid, 0.0) + dense_weight / (k + rank)
        for rank, cid in enumerate(sorted(sparse, key=sparse.get, reverse=True), start=1):
            fused[cid] = fused.get(cid, 0.0) + (1 - dense_weight) / (k + rank)
        return fused

    # ---------------------------------------------------------------------------------------
    async def search(
        self, query: str, user: UserContext, filters: SearchFilters | None = None, top_k: int | None = None
    ) -> SearchResult:
        started = time.perf_counter()
        filters = filters or SearchFilters()
        top_k = top_k or self.settings.retrieval_top_k
        all_namespaces = sorted({c.namespace for c in self.chunks_by_id.values()}) or [
            self.settings.pinecone_namespace_default
        ]
        namespaces = (
            [filters.department]
            if filters.department and filters.department in all_namespaces
            else all_namespaces
        )
        metadata_filter = filters.to_metadata_filter(user.readable_levels)

        # Dense is async I/O; BM25 is CPU-bound and tiny, so run it in a thread while dense is in flight.
        dense_task = self._dense(query, namespaces, metadata_filter, self.settings.dense_candidates)
        sparse_task = asyncio.to_thread(
            self._sparse, query, set(namespaces), metadata_filter, self.settings.sparse_candidates
        )
        (dense, notes), sparse = await asyncio.gather(dense_task, sparse_task)

        degraded = bool(notes)
        fused = self._rrf(dense, sparse, self.settings.hybrid_dense_weight)

        candidates: list[RetrievedChunk] = []
        quarantined: list[str] = []
        for cid in sorted(fused, key=fused.get, reverse=True):
            chunk = self.chunks_by_id.get(cid)
            if chunk is None:
                continue
            # Belt and braces: the filter already excluded these, but never trust a single control.
            if not user.can_read_level(chunk.access_level):
                notes.append(f"dropped {cid}: above clearance")
                continue
            ok, reason = validate_retrieved_chunk(chunk.text, chunk.metadata)
            if not ok:
                notes.append(f"dropped {cid}: {reason}")
                continue
            verdict = scan_prompt_injection(chunk.text)
            if verdict.suspicious:
                quarantined.append(cid)
                notes.append(f"quarantined {chunk.title} ({verdict.explain()})")
                continue
            candidates.append(
                RetrievedChunk(
                    chunk=chunk,
                    dense_score=round(dense.get(cid, 0.0), 4),
                    sparse_score=round(sparse.get(cid, 0.0), 4),
                    fused_score=round(fused[cid], 5),
                    explanation=f"dense={'yes' if cid in dense else 'no'} sparse={'yes' if cid in sparse else 'no'} rrf={fused[cid]:.4f}",
                )
            )
            if len(candidates) >= max(top_k * 2, top_k):
                break

        rerank_method = "disabled"
        if self.settings.rerank_enabled and candidates:
            results, rerank_method = await rerank(query, candidates, top_k)
        else:
            results = candidates[:top_k]
            for i, c in enumerate(results, start=1):
                c.rank = i

        result = SearchResult(
            query=query,
            results=results,
            dense_hits=len(dense),
            sparse_hits=len(sparse),
            namespaces_queried=namespaces,
            filters=filters.describe(),
            rerank_method=rerank_method,
            degraded=degraded,
            notes=notes,
            quarantined=quarantined,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        log.info(
            "hybrid_search",
            query=query[:80],
            **{k: v for k, v in result.__dict__.items() if k not in ("results", "query")},
        )
        return result
