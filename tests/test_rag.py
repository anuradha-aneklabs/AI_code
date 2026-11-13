"""Tests for Harvey's local RAG grounding pipeline."""

import io
from pathlib import Path
import pytest
from pypdf import PdfWriter

from harvey.rag.ingest import (
    DocumentChunk,
    chunk_text,
    ingest_document,
    parse_markdown,
    parse_pdf,
    parse_text,
)
from harvey.rag.store import VectorStore
from harvey.rag.retriever import RAGRetriever
from harvey.rag.embeddings import OllamaEmbeddings


# ── Ingestion & Chunking Unit Tests ──


def test_chunk_text_basic():
    text = "Hello world! This is a test paragraph for chunking text in Harvey RAG."
    chunks = chunk_text(text, chunk_size=30, chunk_overlap=10)
    assert len(chunks) > 1
    # Check that chunks reconstruct the text content
    joined = " ".join(c.text for c in chunks)
    assert "Hello world" in joined
    assert "Harvey RAG" in joined


def test_chunk_text_empty():
    assert chunk_text("", chunk_size=100, chunk_overlap=20) == []
    assert chunk_text("   \n\n  ", chunk_size=100, chunk_overlap=20) == []


def test_ingest_markdown(tmp_path: Path):
    doc_path = tmp_path / "product_features.md"
    doc_path.write_text(
        "# Harvey Overview\n\n"
        "Harvey is an autonomous AI agent for healthcare and dental practices.\n\n"
        "## Key Capabilities\n"
        "- Automated patient inquiry handling\n"
        "- Conservative claims grounded in public clinic details\n"
        "- Zero automatic sending without human approval\n",
        encoding="utf-8",
    )

    chunks = ingest_document(doc_path)
    assert len(chunks) >= 1
    first = chunks[0]
    assert first.source == "product_features.md"
    assert first.file_type == "md"
    assert first.page == 1
    assert first.chunk_id.startswith("product_features.md_p1_c")
    assert "Harvey" in first.text


def test_ingest_txt(tmp_path: Path):
    doc_path = tmp_path / "faq.txt"
    doc_path.write_text(
        "Q: Does Harvey support calendar booking?\n"
        "A: Only appointment request intake is currently implemented.",
        encoding="utf-8",
    )

    chunks = ingest_document(doc_path)
    assert len(chunks) == 1
    assert chunks[0].source == "faq.txt"
    assert chunks[0].file_type == "txt"
    assert chunks[0].page == 1
    assert "appointment request intake" in chunks[0].text


def test_ingest_multipage_pdf(tmp_path: Path):
    # Dynamically build a 2-page PDF
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    writer.add_blank_page(width=300, height=300)

    pdf_path = tmp_path / "clinic_protocol.pdf"
    with open(pdf_path, "wb") as f:
        writer.write(f)

    # Note: Blank pages have no text, so ingest_document should handle without crashing
    chunks = ingest_document(pdf_path)
    assert isinstance(chunks, list)
    assert len(chunks) == 0  # Blank pages have no extractable text


def test_unsupported_file_extension(tmp_path: Path):
    img_path = tmp_path / "logo.png"
    img_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    with pytest.raises(ValueError, match="Unsupported document format"):
        ingest_document(img_path)


# ── Metadata & Formatting Tests ──


def test_document_chunk_metadata():
    chunk = DocumentChunk(
        chunk_id="test_doc_p2_c1",
        source="test_doc.md",
        text="Sample chunk content for dental practice automation.",
        page=2,
        chunk_index=1,
        file_type="md",
        ingested_at="2026-11-02T12:00:00Z",
    )
    meta = chunk.to_metadata()
    assert meta["source"] == "test_doc.md"
    assert meta["page"] == 2
    assert meta["chunk_index"] == 1
    assert meta["file_type"] == "md"
    assert meta["ingested_at"] == "2026-11-02T12:00:00Z"


def test_rag_retriever_empty_prompt_context():
    retriever = RAGRetriever(store=None)
    context = retriever.format_prompt_context([])
    assert "NO LOCAL RAG CHUNKS MATCHED" in context
    assert "Do NOT invent specific product features" in context


def test_rag_retriever_grounded_prompt_context():
    sample_chunks = [
        {
            "chunk_id": "hipaa_spec.pdf_p3_c1",
            "source": "hipaa_spec.pdf",
            "page": 3,
            "text": "Data is stored locally in SQLite with owner-only token permissions.",
            "score": 0.88,
        }
    ]
    retriever = RAGRetriever(store=None)
    context = retriever.format_prompt_context(sample_chunks)
    assert "VERIFIED PRODUCT KNOWLEDGE (GROUNDED RAG CONTEXT)" in context
    assert "hipaa_spec.pdf" in context
    assert "Page: 3" in context
    assert "Chunk: hipaa_spec.pdf_p3_c1" in context
    assert "Data is stored locally in SQLite" in context
    assert "STRICT RAG GROUNDING RULES" in context


# ── Vector Store Operations Tests ──


@pytest.mark.asyncio
async def test_vector_store_lifecycle(tmp_path: Path):
    store = VectorStore(persist_dir=tmp_path / "chroma")
    assert store.count() == 0
    assert store.list_documents() == []

    # Empty search
    empty_res = await store.search("appointment intake")
    assert empty_res == []

    # Add mock chunks
    chunks = [
        DocumentChunk(
            chunk_id="doc1_p1_c1",
            source="doc1.md",
            text="Harvey provides automated patient intake workflows for dental clinics.",
            page=1,
            chunk_index=1,
            file_type="md",
        ),
        DocumentChunk(
            chunk_id="doc1_p1_c2",
            source="doc1.md",
            text="Safety controls require manual approval before any outbound email is sent.",
            page=1,
            chunk_index=2,
            file_type="md",
        ),
        DocumentChunk(
            chunk_id="doc2_p1_c1",
            source="doc2.txt",
            text="Local vector search runs without transmitting internal practice files to third parties.",
            page=1,
            chunk_index=1,
            file_type="txt",
        ),
    ]

    added = await store.add_chunks(chunks)
    assert added == 3
    assert store.count() == 3

    # List documents
    docs = store.list_documents()
    assert len(docs) == 2
    sources = {d["source"] for d in docs}
    assert "doc1.md" in sources
    assert "doc2.txt" in sources

    # Search
    results = await store.search("approval safety email")
    assert len(results) > 0
    assert any("approval" in r["text"].lower() for r in results)

    # Delete single document
    deleted = store.delete_document("doc1.md")
    assert deleted == 2
    assert store.count() == 1
    remaining_docs = store.list_documents()
    assert len(remaining_docs) == 1
    assert remaining_docs[0]["source"] == "doc2.txt"


# ── Dashboard API Endpoints Tests ──


def test_dashboard_rag_endpoints(tmp_path: Path):
    from fastapi.testclient import TestClient
    from harvey.dashboard import app, token_store

    token = token_store.get()
    client = TestClient(app, base_url="http://127.0.0.1:5555")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Status endpoint
    resp = client.get("/api/rag/status", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "status" in data
    assert data["status"]["ready"] is True

    # 2. Documents endpoint
    resp = client.get("/api/rag/documents", headers=headers)
    assert resp.status_code == 200
    assert "documents" in resp.json()

    # 3. Upload endpoint with test markdown
    md_content = "# Verified Product Fact\nHarvey operates locally with zero cloud telemetry."
    payload = {"filename": "test_upload.md", "text": md_content}
    resp = client.post("/api/rag/upload", headers=headers, json=payload)
    assert resp.status_code == 200
    up_data = resp.json()
    assert up_data["success"] is True
    assert up_data["filename"] == "test_upload.md"
    assert up_data["chunks_indexed"] >= 1

    # 4. Query endpoint
    query_payload = {"query": "cloud telemetry", "top_k": 2}
    resp = client.post("/api/rag/query", headers=headers, json=query_payload)
    assert resp.status_code == 200
    q_data = resp.json()
    assert q_data["success"] is True
    assert len(q_data["results"]) >= 1
    assert "telemetry" in q_data["results"][0]["text"].lower()

    # 5. Delete document endpoint
    resp = client.delete("/api/rag/documents/test_upload.md", headers=headers)
    assert resp.status_code == 200
    del_data = resp.json()
    assert del_data["success"] is True
    assert del_data["deleted_source"] == "test_upload.md"


# ── Writer RAG Integration Test ──


@pytest.mark.asyncio
async def test_writer_rag_prompt_injection():
    """Verify that Writer incorporates retrieved RAG chunks into prompt context."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from harvey.agents.writer import Writer
    from harvey.models.prospect import Prospect

    mock_retriever = AsyncMock()
    mock_retriever.retrieve_for_prospect.return_value = [
        {
            "chunk_id": "hipaa_guide.md_p1_c1",
            "source": "hipaa_guide.md",
            "page": 1,
            "text": "Harvey adheres to strict data retention policies and operates locally.",
            "score": 0.85,
        }
    ]
    mock_retriever.format_prompt_context = RAGRetriever.format_prompt_context

    captured_prompts = []

    class MockBrain:
        def load_prompt(self, name, **kwargs):
            return "BASE_WRITER_PROMPT"

        async def think_json(self, prompt, **kwargs):
            captured_prompts.append(prompt)
            return {
                "subject": "Patient intake overview",
                "body": "Hello Dr. Smith,\n\nHarvey operates locally.\n\nBest regards,\nHarvey",
            }

    mock_config = SimpleNamespace(
        product=SimpleNamespace(
            name="Harvey",
            description="Autonomous sales agent",
            key_benefits=["Automated patient intake"],
            pricing="Custom",
        ),
        persona=SimpleNamespace(
            name="Harvey Support",
            company="Harvey AI",
            role="Sales Specialist",
            tone="professional",
        ),
    )

    mock_state = AsyncMock()
    mock_state.funding_round.return_value = None
    mock_state.get_company.return_value = None

    writer = Writer(
        brain=MockBrain(),
        state=mock_state,
        config=mock_config,
        rag=mock_retriever,
    )

    prospect = Prospect(
        id="p_test_123",
        company_id="c_test_123",
        first_name="Jane",
        last_name="Smith",
        email="jane@example.com",
        title="Clinical Director",
        industry="Healthcare",
    )

    draft = await writer._write_personal_email(prospect=prospect)
    assert draft is not None
    assert len(captured_prompts) == 1
    prompt_text = captured_prompts[0]

    # Verify RAG context was injected into the brain prompt
    assert "VERIFIED PRODUCT KNOWLEDGE (GROUNDED RAG CONTEXT)" in prompt_text
    assert "hipaa_guide.md" in prompt_text
    assert "Page: 1" in prompt_text
    assert "Chunk: hipaa_guide.md_p1_c1" in prompt_text
