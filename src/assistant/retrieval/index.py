"""Builds and owns the knowledge index (chunks + embeddings + BM25 + vector store).

Embeddings are cached on disk (``data/index_cache``) keyed by a corpus/provider fingerprint so a
restart does not re-embed unchanged documents and does not re-upsert into Pinecone.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import numpy as np

from assistant.config import Settings, get_settings
from assistant.logging import get_logger
from assistant.retrieval.chunker import load_corpus
from assistant.retrieval.embeddings import build_embedder
from assistant.retrieval.hybrid import HybridRetriever
from assistant.retrieval.models import Chunk
from assistant.retrieval.sparse import BM25Index
from assistant.retrieval.vector_store import build_vector_store

log = get_logger(__name__)
METADATA_SCHEMA_VERSION = 2  # bump when chunk metadata changes so the vector store is re-upserted


class KnowledgeIndex:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.embedder = build_embedder(self.settings)
        self.store = build_vector_store(self.embedder.dim, self.settings)
        self.chunks: list[Chunk] = []
        self.retriever: HybridRetriever | None = None
        self.ready = False

    # ---------------------------------------------------------------------------------------
    def _fingerprint(self) -> str:
        h = hashlib.sha256()
        h.update(
            f"schema:{METADATA_SCHEMA_VERSION}:{self.embedder.name}:{self.embedder.dim}:{self.settings.chunk_size_chars}:{self.settings.chunk_overlap_chars}".encode()
        )
        for path in sorted(Path(self.settings.docs_dir).glob("*.md")):
            h.update(path.name.encode())
            h.update(path.read_bytes())
        return h.hexdigest()[:16]

    async def build(self, force: bool = False) -> None:
        self.chunks = load_corpus(
            Path(self.settings.docs_dir), self.settings.chunk_size_chars, self.settings.chunk_overlap_chars
        )
        if not self.chunks:
            log.error("no_documents_found", docs_dir=str(self.settings.docs_dir))
        cache_dir = Path(self.settings.index_cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        fp = self._fingerprint()
        vec_path, meta_path = cache_dir / f"{fp}.npy", cache_dir / f"{fp}.json"

        vectors: list[list[float]]
        if vec_path.exists() and meta_path.exists() and not force:
            vectors = np.load(vec_path).tolist()
            cached = json.loads(meta_path.read_text())
            log.info("embeddings_cache_hit", fingerprint=fp, chunks=len(vectors), provider=self.embedder.name)
            already_upserted = cached.get("upserted_to") == self.store.name and self.store.name == "pinecone"
        else:
            log.info("embedding_corpus", chunks=len(self.chunks), provider=self.embedder.name)
            vectors = await self.embedder.embed([f"{c.title}\n{c.section}\n{c.text}" for c in self.chunks])
            np.save(vec_path, np.array(vectors, dtype=np.float32))
            meta_path.write_text(json.dumps({"provider": self.embedder.name, "chunks": len(self.chunks)}))
            already_upserted = False

        if not already_upserted:
            await self.store.upsert(self.chunks, vectors)
            if self.store.name == "pinecone":
                meta_path.write_text(
                    json.dumps(
                        {
                            "provider": self.embedder.name,
                            "chunks": len(self.chunks),
                            "upserted_to": "pinecone",
                        }
                    )
                )

        bm25 = await asyncio.to_thread(BM25Index, self.chunks)
        self.retriever = HybridRetriever(
            self.embedder, self.store, bm25, {c.chunk_id: c for c in self.chunks}
        )
        self.ready = True
        log.info(
            "knowledge_index_ready",
            documents=len({c.doc_id for c in self.chunks}),
            chunks=len(self.chunks),
            namespaces=sorted({c.namespace for c in self.chunks}),
            store=self.store.name,
            embedder=self.embedder.name,
        )

    def status(self) -> dict:
        return {
            "ready": self.ready,
            "documents": len({c.doc_id for c in self.chunks}),
            "chunks": len(self.chunks),
            "namespaces": sorted({c.namespace for c in self.chunks}),
            "vector_store": self.store.name,
            "embedding_provider": self.embedder.name,
        }


_index: KnowledgeIndex | None = None
_lock = asyncio.Lock()


async def get_knowledge_index() -> KnowledgeIndex:
    global _index
    async with _lock:
        if _index is None:
            _index = KnowledgeIndex()
            await _index.build()
    return _index
