"""Verify real CRUD operations and SQLite/Vector database persistence across business modules.

Tests Lead Discovery, CRM Pipeline, Conversations, and RAG Knowledge.
Uses isolated test database copy and temporary directory.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from harvey.state import StateManager
from harvey.models import Conversation, Message
from harvey.rag.ingest import chunk_text
from harvey.rag.store import VectorStore


@pytest.mark.asyncio
async def test_all_business_workflows_persistence(tmp_path: Path):
    db_file = tmp_path / "test_business_crud.db"
    state = StateManager(db_path=str(db_file))
    await state.init_db()

    results = {}

    # 1. Lead Discovery Persistence
    raw_lead = {
        "id": "test_lead_001",
        "company_name": "Apex Solar Systems",
        "domain": "apexsolartest.com",
        "website": "https://apexsolartest.com",
        "industry": "CleanTech & Energy Solutions",
        "source": "google_places",
        "score": 85,
        "is_qualified": True,
        "decision_maker": {
            "name": "Alex River",
            "email": "alex@apexsolartest.com",
            "title": "VP Engineering",
            "email_status": "verified",
        },
        "reasons": ["High rating", "Target industry match"],
    }
    lead_id = await state.save_discovered_lead(raw_lead)
    
    # Verify persistence
    lead = await state.get_discovered_lead(lead_id)
    assert lead is not None
    assert lead["company_name"] == "Apex Solar Systems"
    assert lead["is_qualified"] == 1
    results["lead_discovery_persistence"] = "PASS"

    # 2. CRM Pipeline Stage Persistence
    # Import discovered lead to CRM
    crm_lead = await state.import_discovered_lead_to_crm(lead_id, target_stage="New", notes="Initial candidate import")
    assert crm_lead is not None
    company_id = crm_lead["company_id"]
    prospect_id = crm_lead["prospect_id"]
    assert prospect_id != ""

    # Verify initial stage
    prosp = await state.get_prospect(prospect_id)
    assert prosp is not None
    assert prosp.pipeline_stage == "New"

    # Verify stage transition to Contacted
    await state.update_prospect_pipeline(prospect_id, stage="Contacted", notes="Outreach initial contact logged")
    prosp_updated = await state.get_prospect(prospect_id)
    assert prosp_updated is not None
    assert prosp_updated.pipeline_stage == "Contacted"
    assert prosp_updated.notes == "Outreach initial contact logged"

    # Advance stage to Meeting
    await state.update_prospect_pipeline(prospect_id, stage="Meeting", notes="Demo call scheduled for Thursday")
    prosp_advanced = await state.get_prospect(prospect_id)
    assert prosp_advanced is not None
    assert prosp_advanced.pipeline_stage == "Meeting"
    assert prosp_advanced.notes == "Demo call scheduled for Thursday"
    results["crm_pipeline_stage_persistence"] = "PASS"

    # 3. Conversation & Message State Persistence
    conv_id = "test_conv_001"
    convo = Conversation(
        id=conv_id,
        prospect_id=prospect_id,
        status="needs_human",
        priority="high",
        channel="email",
    )
    await state.add_conversation(convo)
    conv = await state.get_conversation(conv_id)
    assert conv is not None
    assert conv.status == "needs_human"
    assert conv.priority == "high"

    # Update conversation status
    await state.update_conversation(conv_id, status="closed")
    conv2 = await state.get_conversation(conv_id)
    assert conv2.status == "closed"
    results["conversation_status_persistence"] = "PASS"

    # 4. RAG Knowledge Ingestion & Vector Persistence
    faq_text = (
        "Harvey AI is an autonomous growth engine and SDR platform.\n\n"
        "Enterprise tier includes unlimited domain research, automated decision maker discovery, "
        "and human-in-the-loop email review workflows.\n\n"
        "Security guarantees: zero unauthorized sends, strict PII masking on emails and phones, "
        "and local encrypted storage for all database records."
    )
    chunks = chunk_text(faq_text, source="pricing_faq.md", file_type="markdown", chunk_size=200)
    assert len(chunks) >= 2
    assert chunks[0].source == "pricing_faq.md"
    assert chunks[0].file_type == "markdown"
    assert "Harvey AI" in chunks[0].text
    meta = chunks[0].to_metadata()
    assert meta["source"] == "pricing_faq.md"
    assert "chunk_index" in meta

    class MockEmbeddings:
        model = "nomic-embed-text"
        async def embed_documents(self, texts):
            return [[0.05 * i for i in range(768)] for _ in texts]
        async def embed_query(self, text):
            return [0.05 * i for i in range(768)]

    rag_dir = tmp_path / "rag_chroma"
    store = VectorStore(persist_dir=rag_dir, embeddings=MockEmbeddings())
    added = await store.add_chunks(chunks)
    assert added == len(chunks)
    assert store.count() == len(chunks)
    docs = store.list_documents()
    assert any(d["source"] == "pricing_faq.md" for d in docs)
    results["knowledge_rag_persistence"] = "PASS"

    return results


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        res = asyncio.run(test_all_business_workflows_persistence(Path(td)))
        print(json.dumps(res, indent=2))
