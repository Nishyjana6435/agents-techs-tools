"""Retrieval layer: hybrid (dense + sparse) search with metadata filtering, attribution and reranking.

Flow: ``chunker`` -> ``embeddings`` -> (``vector_store`` [Pinecone | in-memory] + ``sparse`` [BM25])
-> ``hybrid`` (reciprocal rank fusion + access filtering) -> ``reranker`` -> RetrievedChunk list.

See docs/ARCHITECTURE.md (Retrieval) for the rationale behind each decision.
"""
from assistant.retrieval.index import KnowledgeIndex, get_knowledge_index
from assistant.retrieval.models import Chunk, RetrievedChunk, SearchFilters

__all__ = ["Chunk", "RetrievedChunk", "SearchFilters", "KnowledgeIndex", "get_knowledge_index"]
