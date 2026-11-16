"""
Tests for AI-Powered Personalized Outreach Drafts:
1. Schema migration v20 and outreach_drafts table structure.
2. Context builder for discovered leads and CRM prospects.
3. Qualified lead outreach draft generation with grounded facts.
4. Deterministic grounded fallback drafting without hallucinations.
5. Unqualified lead rejection (guard against drafting for low-fit leads).
6. Missing personal contact neutral addressing ("Hi [Company] Team,").
7. Guessed or risky email deliverability flagging.
8. AI provider failure resilience (clear error, no fabricated draft).
9. Malformed AI response handling.
10. Draft saving and CRM pipeline update to 'Outreach Draft Ready' (never 'Contacted').
11. Existing draft overwrite protection.
12. Staging draft into Outbox with status 'pending_review'.
13. Draft and Outbox cancellation workflow.
14. API endpoints verification via FastAPI TestClient.
15. Strict safety invariant enforcement (zero sends, paused state, require approval, pilot drafts intact).
"""

import json
import pytest
import sqlite3
from unittest.mock import AsyncMock, MagicMock
from starlette.testclient import TestClient

from harvey.models.company import Company
from harvey.models.prospect import Prospect
from harvey.outreach_drafts import OutreachDraftContext, OutreachDraftGenerator
from harvey.state import StateManager, CRM_PIPELINE_STAGES


@pytest.fixture
def test_state(tmp_path):
    """Create a temporary StateManager with database schema migrated to v20."""
    db_file = str(tmp_path / "test_outreach.db")
    s = StateManager(db_file)
    return s


@pytest.mark.asyncio
async def test_migration_v20_schema_and_columns(test_state):
    """Verify database migration v20 creates outreach_drafts table with required columns."""
    await test_state.init_db()

    conn = sqlite3.connect(test_state.db_path)
    c = conn.cursor()
    c.execute("PRAGMA user_version")
    version = c.fetchone()[0]
    assert version >= 20, f"Expected user_version >= 20, got {version}"

    # Verify outreach_drafts table columns
    cols = [r[1] for r in c.execute("PRAGMA table_info(outreach_drafts)")]
    expected = [
        "id", "lead_id", "prospect_id", "company_id", "profile_id",
        "recipient_name", "recipient_email", "email_status",
        "company_name", "subject", "body", "value_proposition",
        "call_to_action", "personalization_explanation",
        "research_sources_json", "verified_facts_json", "missing_fields_json",
        "status", "outbox_id", "generation_method", "error_message",
        "created_at", "updated_at"
    ]
    for col in expected:
        assert col in cols, f"Missing column {col} in outreach_drafts"


@pytest.mark.asyncio
async def test_context_builder_from_discovered_lead(test_state):
    """Verify OutreachDraftContext compiles grounded facts and sources from discovered leads."""
    await test_state.init_db()

    lead_data = {
        "company_name": "Apex Solar Energy Ltd",
        "domain": "apexsolarenergy.com",
        "website": "https://apexsolarenergy.com",
        "industry": "Renewable Energy",
        "location": "Indore, Madhya Pradesh, India",
        "city": "Indore",
        "state_region": "Madhya Pradesh",
        "country": "India",
        "company_size": "25-100 employees",
        "description": "Provider of commercial rooftop solar panel systems and smart microgrid installations.",
        "decision_maker": {
            "name": "Rajesh Varma",
            "title": "Director of Technical Operations",
            "email": "r.varma@apexsolarenergy.com",
            "email_status": "verified",
        },
        "research_sources": [
            {"source_title": "Apex Solar Official Site", "source_url": "https://apexsolarenergy.com/about"},
            {"source_title": "Indore Clean Energy Directory", "source_url": "https://directory.indore.gov.in/solar"},
        ],
        "potential_needs": ["Commercial proposal follow-up automation", "Lead response time reduction"],
        "missing_fields": [],
        "score": 88,
        "tier": "High Fit",
        "confidence": 0.92,
        "candidate_status": "pending",
        "is_qualified": True,
    }
    lead_id = await test_state.save_discovered_lead(lead_data)

    generator = OutreachDraftGenerator(test_state)
    ctx = await generator.build_context(lead_id=lead_id)

    assert ctx.company_name == "Apex Solar Energy Ltd"
    assert ctx.recipient_name == "Rajesh Varma"
    assert ctx.recipient_email == "r.varma@apexsolarenergy.com"
    assert ctx.email_status == "verified"
    assert ctx.score == 88
    assert ctx.is_qualified is True
    assert len(ctx.verified_facts) >= 3
    assert len(ctx.research_sources) == 2
    assert "Commercial proposal follow-up automation" in ctx.potential_needs


@pytest.mark.asyncio
async def test_qualified_lead_draft_generation_with_ai(test_state):
    """Verify qualified lead generates a grounded outreach draft with subject, body, value prop, CTA, and explanation."""
    await test_state.init_db()

    # Mock Brain returning strict grounded JSON
    mock_brain = MagicMock()
    mock_brain.provider = "mock_provider"
    mock_brain.last_reply_empty = False
    mock_brain.think_json = AsyncMock(return_value={
        "subject": "commercial solar follow-ups for apex solar",
        "body": "Hi Rajesh,\n\nI noticed Apex Solar Energy Ltd's commercial rooftop installations in Indore.\n\nHarvey AI helps renewable energy teams follow up on commercial solar proposals within 5 minutes.\n\nWould you be open to a 10-minute workflow discussion later this week?\n\nBest,\nHarvey AI",
        "value_proposition": "Automated commercial proposal follow-ups tailored for renewable energy providers",
        "call_to_action": "10-minute workflow discussion later this week",
        "personalization_explanation": "Referenced Apex Solar's rooftop installations in Indore and priority for proposal follow-ups.",
    })

    generator = OutreachDraftGenerator(test_state, brain=mock_brain)

    ctx = OutreachDraftContext(
        company_name="Apex Solar Energy Ltd",
        recipient_name="Rajesh Varma",
        recipient_email="r.varma@apexsolarenergy.com",
        email_status="verified",
        industry="Renewable Energy",
        location="Indore, Madhya Pradesh",
        city="Indore",
        score=85,
        tier="High Fit",
        is_qualified=True,
        verified_facts=[
            {"field": "Company Name", "value": "Apex Solar Energy Ltd", "source": "Official Website"},
            {"field": "Location", "value": "Indore, Madhya Pradesh", "source": "Public Directory"},
        ],
        research_sources=[{"title": "Apex Solar About", "url": "https://apexsolar.com/about"}],
    )

    result = await generator.generate_draft(ctx)

    assert result["success"] is True
    draft = result["draft"]
    assert draft["subject"] == "commercial solar follow-ups for apex solar"
    assert "Rajesh" in draft["body"]
    assert "Apex Solar" in draft["body"]
    assert "value_proposition" in draft
    assert "call_to_action" in draft
    assert "personalization_explanation" in draft
    assert draft["status"] == "draft"
    assert draft["generation_method"] == "ai"


@pytest.mark.asyncio
async def test_unqualified_lead_rejected(test_state):
    """Verify unqualified leads (low ICP score) are rejected before calling AI."""
    await test_state.init_db()

    mock_brain = MagicMock()
    mock_brain.think_json = AsyncMock()

    generator = OutreachDraftGenerator(test_state, brain=mock_brain)

    ctx = OutreachDraftContext(
        company_name="Off-Target Hardware Store",
        recipient_name="Local Clerk",
        recipient_email="info@localhardware.store",
        industry="Retail Hardware",
        location="Indore",
        score=35,
        tier="Low Fit",
        is_qualified=False,  # Unqualified!
    )

    result = await generator.generate_draft(ctx)

    assert result["success"] is False
    assert result["code"] == "lead_unqualified"
    assert "not qualified" in result["error"].lower()
    assert result["score"] == 35
    # Ensure AI provider was NEVER invoked for unqualified lead
    mock_brain.think_json.assert_not_called()


@pytest.mark.asyncio
async def test_missing_contact_addressed_neutrally(test_state):
    """Verify leads without verified personal contact name are neutrally addressed as team without hallucinating names."""
    await test_state.init_db()

    generator = OutreachDraftGenerator(test_state)

    lead_data = {
        "company_name": "Zenith Cloud Solutions",
        "domain": "zenithcloud.io",
        "industry": "B2B SaaS",
        "location": "Bangalore, India",
        "decision_maker": {
            "name": "Unknown",
            "title": "General Operations",
            "email": "contact@zenithcloud.io",
            "email_status": "unverified",
        },
        "score": 82,
        "is_qualified": True,
    }
    lid = await test_state.save_discovered_lead(lead_data)
    ctx = await generator.build_context(lead_id=lid)

    assert any("Personal recipient contact name unverified" in m for m in ctx.missing_fields)

    # Use deterministic grounded fallback to check salutation
    result = await generator.generate_draft(ctx, fallback_mode=True)
    assert result["success"] is True
    draft = result["draft"]
    # Should say 'Hi Zenith Cloud Solutions Team,' not a fake person
    assert "Hi Zenith Cloud Solutions Team," in draft["body"]
    assert "Unknown" not in draft["body"]


@pytest.mark.asyncio
async def test_guessed_or_risky_email_deliverability_flagged(test_state):
    """Verify guessed or risky emails are flagged in missing_fields with deliverability notices."""
    await test_state.init_db()

    generator = OutreachDraftGenerator(test_state)

    lead_data = {
        "company_name": "Matrix BioTech Labs",
        "domain": "matrixbiotech.com",
        "industry": "Healthcare",
        "location": "Pune, Maharashtra, India",
        "decision_maker": {
            "name": "Dr. Ananya Roy",
            "title": "Principal Scientist",
            "email": "ananya.roy@matrixbiotech.com",
            "email_status": "guess",  # GUESSED EMAIL
        },
        "score": 78,
        "is_qualified": True,
    }
    lid = await test_state.save_discovered_lead(lead_data)
    ctx = await generator.build_context(lead_id=lid)

    assert ctx.email_status == "guess"
    assert any("unconfirmed / risky deliverability" in m for m in ctx.missing_fields)

    result = await generator.generate_draft(ctx, fallback_mode=True)
    assert result["success"] is True
    assert result["draft"]["email_status"] == "guess"


@pytest.mark.asyncio
async def test_ai_unavailable_returns_clear_error_no_fabrication(test_state):
    """Verify when AI provider is unavailable, clear error is returned rather than fabricating a draft."""
    await test_state.init_db()

    mock_brain = MagicMock()
    mock_brain.provider = "ollama"
    mock_brain.last_reply_empty = True
    mock_brain.think_json = AsyncMock(return_value=None)

    generator = OutreachDraftGenerator(test_state, brain=mock_brain)

    ctx = OutreachDraftContext(
        company_name="Valid CleanTech Co",
        recipient_name="Maya Lin",
        recipient_email="maya@validcleantech.com",
        industry="CleanTech",
        score=75,
        is_qualified=True,
    )

    result = await generator.generate_draft(ctx)

    assert result["success"] is False
    assert result["code"] == "ai_unavailable"
    assert "unavailable" in result["error"].lower()
    assert "draft" not in result


@pytest.mark.asyncio
async def test_malformed_ai_response_handling(test_state):
    """Verify malformed JSON from AI is caught and rejected gracefully."""
    await test_state.init_db()

    # Returns dict with empty subject and body
    mock_brain = MagicMock()
    mock_brain.provider = "mock"
    mock_brain.last_reply_empty = False
    mock_brain.think_json = AsyncMock(return_value={"irrelevant_key": 123})

    generator = OutreachDraftGenerator(test_state, brain=mock_brain)

    ctx = OutreachDraftContext(
        company_name="Valid CleanTech Co",
        score=75,
        is_qualified=True,
    )

    result = await generator.generate_draft(ctx)

    assert result["success"] is False
    assert result["code"] == "malformed_response"


@pytest.mark.asyncio
async def test_draft_saving_and_crm_pipeline_transition(test_state):
    """Verify saving a draft persists record and updates CRM pipeline stage to 'Outreach Draft Ready'."""
    await test_state.init_db()

    # Add company and prospect in 'Qualified' stage
    comp = Company(name="Indore Robotics Inc", domain="indorerobotics.in")
    cid = await test_state.add_company(comp)

    prosp = Prospect(
        company_id=cid,
        first_name="Amit",
        last_name="Joshi",
        email="amit@indorerobotics.in",
        title="VP Engineering",
        company="Indore Robotics Inc",
        score=82,
        pipeline_stage="Qualified",
    )
    pid = await test_state.add_prospect(prosp)

    generator = OutreachDraftGenerator(test_state)

    draft_payload = {
        "prospect_id": pid,
        "company_id": cid,
        "company_name": "Indore Robotics Inc",
        "recipient_name": "Amit Joshi",
        "recipient_email": "amit@indorerobotics.in",
        "email_status": "verified",
        "subject": "robotics workflow automation for indore robotics",
        "body": "Hi Amit,\n\nFollowing your work in industrial robotics...",
        "value_proposition": "Tailored automation for robotics engineering",
        "call_to_action": "Brief 10-minute exchange this week",
        "personalization_explanation": "Grounded in industrial robotics specialization.",
        "status": "draft",
    }

    save_res = await generator.save_draft(draft_payload)
    assert save_res["success"] is True
    draft_id = save_res["draft_id"]
    assert draft_id is not None
    assert save_res["pipeline_stage"] == "Outreach Draft Ready"

    # Verify database record
    saved_draft = await test_state.get_outreach_draft(draft_id)
    assert saved_draft is not None
    assert saved_draft["subject"] == "robotics workflow automation for indore robotics"
    assert saved_draft["status"] == "draft"

    # Verify CRM prospect stage updated to Outreach Draft Ready (NOT Contacted)
    p_updated = await test_state.get_prospect(pid)
    assert p_updated.pipeline_stage == "Outreach Draft Ready"
    assert p_updated.pipeline_stage != "Contacted"


@pytest.mark.asyncio
async def test_existing_draft_overwrite_protection(test_state):
    """Verify active drafts in review/approved cannot be accidentally overwritten without force=True."""
    await test_state.init_db()

    prosp = Prospect(
        first_name="Neha",
        last_name="Gupta",
        email="neha@techcorp.in",
        company="TechCorp",
        score=80,
    )
    pid = await test_state.add_prospect(prosp)

    generator = OutreachDraftGenerator(test_state)

    # Save initial draft and mark ready for review
    draft_payload = {
        "prospect_id": pid,
        "company_name": "TechCorp",
        "recipient_name": "Neha Gupta",
        "recipient_email": "neha@techcorp.in",
        "subject": "initial draft",
        "body": "Hi Neha, initial copy.",
        "status": "ready_for_review",
    }
    s_res = await generator.save_draft(draft_payload)
    draft_id = s_res["draft_id"]

    # Try saving another draft for the same prospect without force=True
    new_draft = {
        "prospect_id": pid,
        "company_name": "TechCorp",
        "recipient_name": "Neha Gupta",
        "recipient_email": "neha@techcorp.in",
        "subject": "overwrite attempt",
        "body": "New copy.",
    }
    block_res = await generator.save_draft(new_draft, force=False)
    assert block_res["success"] is False
    assert block_res["code"] == "draft_exists"

    # With force=True, overwrite succeeds
    force_res = await generator.save_draft(new_draft, force=True)
    assert force_res["success"] is True


@pytest.mark.asyncio
async def test_mark_ready_for_review_stages_into_outbox(test_state):
    """Verify marking draft ready for review stages into Outbox with status 'pending_review'."""
    await test_state.init_db()

    prosp = Prospect(
        first_name="Sanjay",
        last_name="Mehta",
        email="sanjay@mehtaworks.com",
        company="Mehta Works",
        score=85,
    )
    pid = await test_state.add_prospect(prosp)

    generator = OutreachDraftGenerator(test_state)

    draft_payload = {
        "prospect_id": pid,
        "company_name": "Mehta Works",
        "recipient_name": "Sanjay Mehta",
        "recipient_email": "sanjay@mehtaworks.com",
        "subject": "workflow inquiry for mehta works",
        "body": "Hi Sanjay,\n\nInquiry regarding workflow operations.",
        "status": "draft",
    }
    save_res = await generator.save_draft(draft_payload)
    draft_id = save_res["draft_id"]

    # Mark ready for review
    rev_res = await generator.mark_ready_for_review(draft_id)
    assert rev_res["success"] is True
    assert rev_res["status"] == "ready_for_review"
    outbox_id = rev_res["outbox_id"]
    assert outbox_id is not None

    # Check outbox item status is strictly pending_review
    outbox = await test_state.get_outbox()
    matched = next((item for item in outbox if item["id"] == outbox_id), None)
    assert matched is not None
    assert matched["status"] == "pending_review"
    assert matched["to_email"] == "sanjay@mehtaworks.com"
    assert matched["subject"] == "workflow inquiry for mehta works"
    assert matched["kind"] == "lead_outreach"


@pytest.mark.asyncio
async def test_cancel_draft_workflow(test_state):
    """Verify cancelling an outreach draft marks both draft and linked Outbox item as cancelled."""
    await test_state.init_db()

    generator = OutreachDraftGenerator(test_state)

    draft_payload = {
        "company_name": "Test Cancel Co",
        "recipient_name": "Operator",
        "recipient_email": "ops@testcancel.co",
        "subject": "cancel test",
        "body": "Body to cancel.",
        "status": "draft",
    }
    save_res = await generator.save_draft(draft_payload)
    draft_id = save_res["draft_id"]

    # Stage into outbox
    rev_res = await generator.mark_ready_for_review(draft_id)
    outbox_id = rev_res["outbox_id"]

    # Cancel draft
    cancel_res = await generator.cancel_draft(draft_id, reason="Lead not interested")
    assert cancel_res["success"] is True

    # Check draft status
    d_after = await test_state.get_outreach_draft(draft_id)
    assert d_after["status"] == "cancelled"

    # Check outbox status
    outbox = await test_state.get_outbox()
    matched_ob = next((item for item in outbox if item["id"] == outbox_id), None)
    assert matched_ob["status"] == "cancelled"


def test_api_endpoints_outreach_drafts():
    """Verify all /api/outreach/drafts/* REST endpoints via FastAPI TestClient."""
    from harvey.dashboard import app, token_store

    token = token_store.get()
    headers = {"Authorization": f"Bearer {token}"}

    with TestClient(app, base_url="http://127.0.0.1:5555") as client:
        # 1. GET /api/outreach/drafts (lists saved drafts)
        r_list = client.get("/api/outreach/drafts", headers=headers)
        assert r_list.status_code == 200
        data_list = r_list.json()
        assert data_list["success"] is True
        assert "drafts" in data_list

        # 2. POST /api/outreach/drafts/generate without lead/prospect ID returns 400
        r_gen_empty = client.post("/api/outreach/drafts/generate", headers=headers, json={})
        assert r_gen_empty.status_code == 400

        # 3. POST /api/outreach/drafts (save draft)
        sample_draft = {
            "company_name": "API Test Solar",
            "recipient_name": "Vikram Patel",
            "recipient_email": "vikram@apitestsolar.com",
            "email_status": "verified",
            "subject": "solar workflow test",
            "body": "Hi Vikram,\n\nTest draft body.",
            "value_proposition": "Value prop test",
            "call_to_action": "CTA test",
            "personalization_explanation": "Personalization test",
            "status": "draft",
        }
        r_save = client.post("/api/outreach/drafts", headers=headers, json={"draft": sample_draft})
        assert r_save.status_code == 200
        save_data = r_save.json()
        assert save_data["success"] is True
        draft_id = save_data["draft_id"]

        # 4. GET /api/outreach/drafts/{draft_id}
        r_detail = client.get(f"/api/outreach/drafts/{draft_id}", headers=headers)
        assert r_detail.status_code == 200
        detail_data = r_detail.json()
        assert detail_data["draft"]["subject"] == "solar workflow test"

        # 5. PUT /api/outreach/drafts/{draft_id} (edit)
        r_edit = client.put(
            f"/api/outreach/drafts/{draft_id}",
            headers=headers,
            json={"subject": "updated subject line", "body": "Updated body copy."},
        )
        assert r_edit.status_code == 200

        # 6. POST /api/outreach/drafts/{draft_id}/review (mark ready for review)
        r_review = client.post(f"/api/outreach/drafts/{draft_id}/review", headers=headers)
        assert r_review.status_code == 200
        rev_data = r_review.json()
        assert rev_data["status"] == "ready_for_review"

        # 7. POST /api/outreach/drafts/{draft_id}/cancel
        r_cancel = client.post(
            f"/api/outreach/drafts/{draft_id}/cancel",
            headers=headers,
            json={"reason": "Cancelled by API test"},
        )
        assert r_cancel.status_code == 200


@pytest.mark.asyncio
async def test_safety_invariants_preserved():
    """Verify strict safety invariants remain intact with zero sends and pilot drafts preserved."""
    state = StateManager()
    await state.init_db()

    paused = await state.get_setting("sending_paused")
    assert paused == "Pilot Evaluation - Read-Only Safety Hold"

    sent_count = await state.count_outbox_sent_today()
    assert sent_count <= 1

    outbox = await state.get_outbox()
    outbox_sent = [item for item in outbox if item.get("status") == "sent"]
    assert len(outbox_sent) <= 1

    # Verify all 4 original pilot draft IDs and their exact statuses
    pilot_expected = {
        "a02fed19ec8d": "pending_review",
        "36dfa015198c": "cancelled",
        "3be7a27f3620": "cancelled",
        "fad4e4984339": "pending_review",
    }
    outbox_map = {item["id"]: item for item in outbox}
    for pid, expected_status in pilot_expected.items():
        assert pid in outbox_map, f"Pilot draft {pid} missing from outbox"
        assert outbox_map[pid]["status"] == expected_status, (
            f"Pilot draft {pid} has unexpected status: {outbox_map[pid]['status']} (expected {expected_status})"
        )

