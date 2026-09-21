"""Embedding providers behind one tiny async interface.

* ``voyage``  - Voyage AI (Anthropic's recommended embedding partner), called via httpx.
* ``openai``  - ``text-embedding-3-small`` via langchain-openai.
* ``local``   - deterministic hashed n-gram embeddings (no network, no model download). Quality
                is far below a neural model, but combined with BM25 in the hybrid ranker it is
                good enough to run the full system offline and in CI. The provider name is
                reported on ``/health`` so nobody mistakes it for production quality.

All providers return L2-normalised vectors so cosine similarity == dot product.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from typing import Protocol

import httpx
import numpy as np

from assistant.config import Settings, get_settings
from assistant.logging import get_logger

log = get_logger(__name__)


class Embedder(Protocol):
    name: str
    dim: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


def _normalise(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


class LocalHashEmbedder:
    """Feature-hashed word + bigram embedding. Deterministic and dependency-free."""

    name = "local-hash"

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim
        self._token_re = re.compile(r"[a-z0-9]+")

    def _embed_one(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        tokens = self._token_re.findall(text.lower())
        grams = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
        for g in grams:
            h = int(hashlib.blake2b(g.encode(), digest_size=8).hexdigest(), 16)
            idx = h % self.dim
            sign = 1.0 if (h >> 63) & 1 else -1.0
            vec[idx] += sign
        return vec

    async def embed(self, texts: list[str]) -> list[list[float]]:
        matrix = np.stack([self._embed_one(t) for t in texts]) if texts else np.zeros((0, self.dim))
        return _normalise(matrix).tolist()


class VoyageEmbedder:
    name = "voyage"

    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model
        self.dim = 512 if "lite" in model else 1024

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        out: list[list[float]] = []
        async with httpx.AsyncClient(timeout=30) as client:
            for i in range(0, len(texts), 64):
                batch = texts[i : i + 64]
                resp = await client.post(
                    "https://api.voyageai.com/v1/embeddings",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={"input": batch, "model": self._model},
                )
                resp.raise_for_status()
                data = resp.json()["data"]
                out.extend(item["embedding"] for item in sorted(data, key=lambda d: d["index"]))
        return _normalise(np.array(out, dtype=np.float32)).tolist()


class OpenAIEmbedder:
    name = "openai"

    def __init__(self, api_key: str, model: str) -> None:
        from langchain_openai import OpenAIEmbeddings

        self._client = OpenAIEmbeddings(model=model, api_key=api_key)
        self.dim = 1536 if "small" in model else 3072

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = await self._client.aembed_documents(texts)
        return _normalise(np.array(vectors, dtype=np.float32)).tolist()


def build_embedder(settings: Settings | None = None) -> Embedder:
    settings = settings or get_settings()
    provider = settings.resolved_embedding_provider
    if provider == "voyage" and settings.voyage_api_key:
        return VoyageEmbedder(settings.voyage_api_key, settings.voyage_model)
    if provider == "openai" and settings.openai_api_key:
        return OpenAIEmbedder(settings.openai_api_key, settings.openai_embedding_model)
    if provider != "local":
        log.warning("embedding_provider_fallback", requested=provider, using="local-hash")
    return LocalHashEmbedder(settings.local_embedding_dim)


async def embed_with_timeout(embedder: Embedder, texts: list[str], timeout: float = 30.0) -> list[list[float]]:
    return await asyncio.wait_for(embedder.embed(texts), timeout=timeout)
