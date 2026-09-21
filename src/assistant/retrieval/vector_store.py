"""Dense vector stores.

``PineconeStore`` is the production path (serverless index, one namespace per department,
metadata filtering). ``InMemoryStore`` implements the same interface with numpy and a small
interpreter for the Pinecone filter language (``$eq``, ``$in``, ``$gte``, ``$lte``, ``$and``),
so the retrieval code is identical on both.

Failure handling: every Pinecone call is wrapped with a timeout and converted into
``VectorStoreError``; the hybrid retriever catches that and degrades to sparse-only search
while flagging ``degraded=True`` for the activity panel.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

import numpy as np

from assistant.config import Settings, get_settings
from assistant.logging import get_logger
from assistant.retrieval.models import Chunk

log = get_logger(__name__)


class VectorStoreError(RuntimeError):
    pass


class VectorStore(Protocol):
    name: str

    async def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None: ...

    async def query(
        self, vector: list[float], k: int, namespace: str, metadata_filter: dict[str, Any] | None
    ) -> list[tuple[str, float, dict[str, Any]]]: ...

    async def namespaces(self) -> list[str]: ...

    async def count(self) -> int: ...


# --------------------------------------------------------------------------------------------
# Filter interpreter shared by the in-memory store (and used in tests to validate filters).
# --------------------------------------------------------------------------------------------
def matches_filter(metadata: dict[str, Any], flt: dict[str, Any] | None) -> bool:
    if not flt:
        return True
    for key, cond in flt.items():
        if key == "$and":
            if not all(matches_filter(metadata, sub) for sub in cond):
                return False
            continue
        if key == "$or":
            if not any(matches_filter(metadata, sub) for sub in cond):
                return False
            continue
        value = metadata.get(key)
        if not isinstance(cond, dict):
            if value != cond:
                return False
            continue
        for op, operand in cond.items():
            if op == "$eq" and value != operand:
                return False
            if op == "$ne" and value == operand:
                return False
            if op == "$in" and value not in operand:
                return False
            if op == "$nin" and value in operand:
                return False
            if op == "$gte" and not (value is not None and str(value) >= str(operand)):
                return False
            if op == "$lte" and not (value is not None and str(value) <= str(operand)):
                return False
    return True


class InMemoryStore:
    name = "in-memory"

    def __init__(self) -> None:
        self._ids: dict[str, list[str]] = {}
        self._vectors: dict[str, np.ndarray] = {}
        self._meta: dict[str, list[dict[str, Any]]] = {}

    async def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        by_ns: dict[str, list[tuple[Chunk, list[float]]]] = {}
        for c, v in zip(chunks, vectors, strict=True):
            by_ns.setdefault(c.namespace, []).append((c, v))
        for ns, items in by_ns.items():
            self._ids[ns] = [c.chunk_id for c, _ in items]
            self._vectors[ns] = np.array([v for _, v in items], dtype=np.float32)
            self._meta[ns] = [c.metadata for c, _ in items]

    async def query(self, vector, k, namespace, metadata_filter=None):
        if namespace not in self._vectors:
            return []
        scores = self._vectors[namespace] @ np.array(vector, dtype=np.float32)
        order = np.argsort(-scores)
        out: list[tuple[str, float, dict[str, Any]]] = []
        for i in order:
            meta = self._meta[namespace][i]
            if not matches_filter(meta, metadata_filter):
                continue
            out.append((self._ids[namespace][i], float(scores[i]), meta))
            if len(out) >= k:
                break
        return out

    async def namespaces(self) -> list[str]:
        return sorted(self._vectors)

    async def count(self) -> int:
        return sum(len(v) for v in self._ids.values())


class PineconeStore:
    name = "pinecone"

    def __init__(self, settings: Settings, dim: int) -> None:
        from pinecone import Pinecone, ServerlessSpec

        self._settings = settings
        self._pc = Pinecone(api_key=settings.pinecone_api_key)
        self._dim = dim
        if not self._pc.has_index(settings.pinecone_index):
            log.info("pinecone_create_index", index=settings.pinecone_index, dim=dim)
            self._pc.create_index(
                name=settings.pinecone_index,
                dimension=dim,
                metric="cosine",
                spec=ServerlessSpec(cloud=settings.pinecone_cloud, region=settings.pinecone_region),
            )
        self._index = self._pc.Index(settings.pinecone_index)
        self._timeout = 20.0

    async def _call(self, fn, *args, **kwargs):
        """Run a blocking Pinecone SDK call off the event loop with a timeout."""
        try:
            return await asyncio.wait_for(asyncio.to_thread(fn, *args, **kwargs), timeout=self._timeout)
        except TimeoutError as exc:
            raise VectorStoreError("Pinecone call timed out") from exc
        except Exception as exc:
            raise VectorStoreError(f"Pinecone error: {exc.__class__.__name__}: {exc}") from exc

    async def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        by_ns: dict[str, list[dict[str, Any]]] = {}
        for c, v in zip(chunks, vectors, strict=True):
            by_ns.setdefault(c.namespace, []).append({"id": c.chunk_id, "values": v, "metadata": c.metadata})
        for ns, items in by_ns.items():
            for i in range(0, len(items), 100):
                await self._call(self._index.upsert, vectors=items[i : i + 100], namespace=ns)
        log.info("pinecone_upsert_complete", namespaces=list(by_ns), vectors=len(chunks))

    async def query(self, vector, k, namespace, metadata_filter=None):
        res = await self._call(
            self._index.query,
            vector=vector,
            top_k=k,
            namespace=namespace,
            filter=metadata_filter,
            include_metadata=True,
        )
        matches = res.get("matches", []) if isinstance(res, dict) else getattr(res, "matches", [])
        out = []
        for m in matches:
            mid = m["id"] if isinstance(m, dict) else m.id
            score = m["score"] if isinstance(m, dict) else m.score
            meta = (m.get("metadata") if isinstance(m, dict) else m.metadata) or {}
            out.append((mid, float(score), dict(meta)))
        return out

    async def namespaces(self) -> list[str]:
        stats = await self._call(self._index.describe_index_stats)
        ns = stats.get("namespaces", {}) if isinstance(stats, dict) else getattr(stats, "namespaces", {})
        return sorted(ns.keys())

    async def count(self) -> int:
        stats = await self._call(self._index.describe_index_stats)
        total = (
            stats.get("total_vector_count", 0)
            if isinstance(stats, dict)
            else getattr(stats, "total_vector_count", 0)
        )
        return int(total)


def build_vector_store(dim: int, settings: Settings | None = None) -> VectorStore:
    settings = settings or get_settings()
    if settings.pinecone_enabled:
        try:
            return PineconeStore(settings, dim)
        except Exception as exc:
            log.error("pinecone_unavailable_falling_back", error=str(exc))
    else:
        log.warning("pinecone_not_configured", using="in-memory")
    return InMemoryStore()
