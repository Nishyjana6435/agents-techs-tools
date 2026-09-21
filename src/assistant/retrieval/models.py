from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from assistant.retrieval.dates import date_to_int


class Chunk(BaseModel):
    """A retrievable unit. ``metadata`` is exactly what is stored alongside the vector in Pinecone."""

    chunk_id: str
    doc_id: str
    title: str
    section: str = ""
    text: str
    namespace: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def access_level(self) -> str:
        return str(self.metadata.get("access_level", "internal"))


class RetrievedChunk(BaseModel):
    chunk: Chunk
    dense_score: float = 0.0
    sparse_score: float = 0.0
    fused_score: float = 0.0
    rerank_score: float | None = None
    rank: int = 0
    explanation: str = ""  # human readable: why this chunk was selected

    @property
    def score(self) -> float:
        return self.rerank_score if self.rerank_score is not None else self.fused_score


class SearchFilters(BaseModel):
    """Metadata filters expressed in Pinecone's filter language subset we support on both stores.

    ``department`` maps to a namespace (query fan-out), everything else becomes a metadata filter.
    """

    department: str | None = None
    document_types: list[str] | None = None
    created_after: str | None = None  # ISO date, inclusive
    created_before: str | None = None  # ISO date, inclusive

    def to_metadata_filter(self, readable_levels: list[str]) -> dict[str, Any]:
        clauses: list[dict[str, Any]] = [{"access_level": {"$in": readable_levels}}]
        if self.document_types:
            clauses.append({"document_type": {"$in": self.document_types}})
        # Range filters use the numeric mirror of created_date (Pinecone requires numbers for $gte/$lte).
        if self.created_after:
            clauses.append({"created_ts": {"$gte": date_to_int(self.created_after)}})
        if self.created_before:
            clauses.append({"created_ts": {"$lte": date_to_int(self.created_before)}})
        return clauses[0] if len(clauses) == 1 else {"$and": clauses}

    def describe(self) -> str:
        parts = []
        if self.department:
            parts.append(f"department={self.department}")
        if self.document_types:
            parts.append(f"types={','.join(self.document_types)}")
        if self.created_after:
            parts.append(f"after={self.created_after}")
        if self.created_before:
            parts.append(f"before={self.created_before}")
        return ", ".join(parts) or "none"
