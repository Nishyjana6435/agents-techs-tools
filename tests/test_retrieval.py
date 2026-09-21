from pathlib import Path

from assistant.config import get_settings
from assistant.retrieval import SearchFilters, get_knowledge_index
from assistant.retrieval.chunker import load_corpus, split_sections, window
from assistant.retrieval.vector_store import matches_filter


def test_filter_interpreter():
    meta = {
        "access_level": "confidential",
        "document_type": "incident",
        "created_date": "2025-04-19",
        "created_ts": 20250419,
    }
    assert matches_filter(meta, {"access_level": {"$in": ["internal", "confidential"]}})
    assert not matches_filter(meta, {"access_level": {"$in": ["public", "internal"]}})
    assert matches_filter(
        meta, {"$and": [{"document_type": {"$eq": "incident"}}, {"created_ts": {"$gte": 20250101}}]}
    )
    assert not matches_filter(meta, {"created_ts": {"$lte": 20250101}})


def test_search_filters_to_pinecone_syntax():
    f = SearchFilters(document_types=["incident"], created_after="2025-01-01")
    flt = f.to_metadata_filter(["public", "internal"])
    assert "$and" in flt and {"access_level": {"$in": ["public", "internal"]}} in flt["$and"]
    assert {"created_ts": {"$gte": 20250101}} in flt["$and"]  # Pinecone range operators need numbers


def test_chunker_splits_sections_and_windows():
    sections = split_sections("# Title\n\nintro\n\n## A\ntext a\n\n## B\ntext b")
    assert [s[0] for s in sections] == ["Title", "A", "B"]
    pieces = window("word " * 500, size=400, overlap=50)
    assert len(pieces) > 1 and all(len(p) <= 400 for p in pieces)
    chunks = load_corpus(Path(get_settings().docs_dir), 1200, 150)
    assert chunks and all(c.metadata["doc_id"] and c.metadata["title"] for c in chunks)


async def test_hybrid_search_respects_clearance(viewer, admin):
    index = await get_knowledge_index()
    q = "PROJECT HARBOUR insider watchlist"
    viewer_titles = [r.chunk.title for r in (await index.retriever.search(q, viewer, top_k=5)).results]
    admin_titles = [r.chunk.title for r in (await index.retriever.search(q, admin, top_k=5)).results]
    assert "Insider Trading Watchlist Procedure" not in viewer_titles
    assert "Insider Trading Watchlist Procedure" in admin_titles


async def test_hybrid_search_quarantines_injected_document(admin):
    index = await get_knowledge_index()
    result = await index.retriever.search("observability vendor demo pricing", admin, top_k=5)
    assert any("meeting-notes-vendor-demo" in q for q in result.quarantined)
    assert all(r.chunk.doc_id != "meeting-notes-vendor-demo" for r in result.results)


async def test_metadata_filters_and_namespace(analyst):
    index = await get_knowledge_index()
    result = await index.retriever.search(
        "outage", analyst, SearchFilters(department="payments", document_types=["incident"]), top_k=8
    )
    assert result.namespaces_queried == ["payments"]
    assert all(r.chunk.metadata["document_type"] == "incident" for r in result.results)
    assert all(r.explanation for r in result.results)
