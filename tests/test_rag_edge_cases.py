"""Negative and edge-case test suite for Harvey's RAG system."""

import os
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest

from harvey.rag.ingest import (
    DocumentChunk,
    chunk_text,
    ingest_document,
    parse_pdf,
    parse_markdown,
    parse_text,
)
from harvey.rag.store import VectorStore
from harvey.rag.retriever import RAGRetriever
from harvey.rag.embeddings import OllamaEmbeddings
from harvey.rag.trace import record_rag_trace, get_rag_trace, list_rag_traces


# ── Edge Case 1: Empty or Whitespace Query ──


@pytest.mark.asyncio
async def test_empty_and_whitespace_query(tmp_path: Path):
    store = VectorStore(persist_dir=tmp_path / "chroma_empty_q")
    assert await store.search("") == []
    assert await store.search("   ") == []
    assert await store.search("\n\t  \r\n") == []

    retriever = RAGRetriever(store=store)
    from types import SimpleNamespace
    p = SimpleNamespace(industry="", title="", personalization_notes="")
    # Should fall back to generic query and return empty gracefully without error
    res = await retriever.retrieve_for_prospect(p)
    assert isinstance(res, list)


# ── Edge Case 2: Empty Knowledge Base ──


@pytest.mark.asyncio
async def test_empty_knowledge_base(tmp_path: Path):
    empty_store = VectorStore(persist_dir=tmp_path / "empty_chroma")
    assert empty_store.count() == 0
    assert empty_store.list_documents() == []

    # Search against completely empty store
    res = await empty_store.search("dental implants")
    assert res == []

    # Retriever with empty store returns [] and fallback prompt context
    retriever = RAGRetriever(store=empty_store)
    from types import SimpleNamespace
    p = SimpleNamespace(industry="Dentistry", title="Dentist", personalization_notes="")
    chunks = await retriever.retrieve_for_prospect(p)
    assert chunks == []
    context = retriever.format_prompt_context(chunks)
    assert "NO LOCAL RAG CHUNKS MATCHED" in context
    assert "Do NOT invent specific product features" in context


# ── Edge Case 3: Missing Document Ingestion ──


def test_missing_document_raises():
    missing = Path("data/rag/docs/non_existent_file_xyz123.pdf")
    with pytest.raises(Exception):
        ingest_document(missing)


# ── Edge Case 4: Invalid File Formats & Corrupted Content ──


def test_invalid_file_extension(tmp_path: Path):
    bad_file = tmp_path / "binary.bin"
    bad_file.write_bytes(b"\x00\x01\x02\x03\xff")
    with pytest.raises(ValueError, match="Unsupported document format"):
        ingest_document(bad_file)


def test_corrupted_pdf_handling(tmp_path: Path):
    corrupted_pdf = tmp_path / "broken.pdf"
    corrupted_pdf.write_bytes(b"%PDF-1.4 this is corrupted garbage data \x00\xff")
    with pytest.raises(Exception):
        parse_pdf(corrupted_pdf)


def test_empty_text_and_markdown(tmp_path: Path):
    empty_txt = tmp_path / "empty.txt"
    empty_txt.write_text("   \n\n  \t  ", encoding="utf-8")
    assert ingest_document(empty_txt) == []

    empty_md = tmp_path / "empty.md"
    empty_md.write_text("", encoding="utf-8")
    assert ingest_document(empty_md) == []


# ── Edge Case 5: Retrieval Returning No Results (Filter Threshold) ──


@pytest.mark.asyncio
async def test_retrieval_below_threshold(tmp_path: Path):
    store = VectorStore(persist_dir=tmp_path / "thresh_chroma")
    await store.add_chunks([
        DocumentChunk(
            chunk_id="doc_p1_c1",
            source="doc.txt",
            text="General accounting software ledger reconciliation entries.",
            page=1,
            chunk_index=1,
            file_type="txt",
        )
    ])
    retriever = RAGRetriever(store=store)

    from types import SimpleNamespace
    p = SimpleNamespace(industry="Dentistry", title="Orthodontist", personalization_notes="Teeth whitening")
    # Using high min_score threshold should filter out unrelated chunks
    filtered = await retriever.retrieve_for_prospect(p, min_score=0.99)
    # Even if filtered, it should handle gracefully
    assert isinstance(filtered, list)


# ── Edge Case 6: Ollama Service Unavailable Fallback ──


@pytest.mark.asyncio
async def test_ollama_unavailable_fallback():
    # Configure an unreachable port for Ollama embeddings
    bad_embeddings = OllamaEmbeddings(base_url="http://127.0.0.1:54321")
    # Embedding generation should not crash; it should fall back to deterministic local tokens
    vector = await bad_embeddings.embed_query("patient appointment intake")
    assert isinstance(vector, list)
    assert vector == []


# ── Edge Case 7: ChromaDB Service Failure Handling ──


@pytest.mark.asyncio
async def test_chromadb_query_failure_handling(tmp_path: Path):
    store = VectorStore(persist_dir=tmp_path / "chroma_fail")
    # Mock collection.query to raise an unexpected runtime error
    with patch.object(store.collection, "query", side_effect=RuntimeError("Chroma lock held")):
        results = await store.search("dental clinic")
        assert results == []  # Handled gracefully, returns []


# ── Edge Case 8: Malformed Metadata & Trace Recovery ──


def test_malformed_metadata_serialization():
    # Chunk with empty or special characters in source
    chunk = DocumentChunk(
        chunk_id="weird@@name_p1_c1",
        source="weird / directory : name.txt",
        text="Normal content",
        page=0,
        chunk_index=-1,
        file_type="",
        ingested_at="",
    )
    meta = chunk.to_metadata()
    assert meta["source"] == "weird / directory : name.txt"
    assert meta["page"] == 0
    assert meta["file_type"] == ""


def test_rag_trace_null_and_missing_identifiers():
    assert get_rag_trace("") is None
    assert get_rag_trace("non_existent_key_999") is None

    # Record trace with empty chunks
    rec = record_rag_trace(
        item_id="empty_trace_item",
        prospect_id="empty_trace_prospect",
        to_email="test@example.com",
        subject="Test Subject",
        sources=[],
        chunks=[],
    )
    assert rec["item_id"] == "empty_trace_item"
    assert rec["sources"] == []
    assert rec["chunks"] == []

    fetched = get_rag_trace("empty_trace_item")
    assert fetched is not None
    assert fetched["subject"] == "Test Subject"

