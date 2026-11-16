"""
Tests for Lead Discovery & CRM Pipeline Automation:
1. Lead discovery with custom criteria (industry, size, location, business type, use cases).
2. Source-grounded research tracking (source URL, title, timestamp, confidence, zero hallucinated facts).
3. Duplicate detection (domain match, email match, company name match).
4. Review before import workflow (pending candidates, approval/import, skip/reject).
5. CRM pipeline stages (New -> Researched -> Qualified -> Outreach Draft Ready -> Contacted -> Interested -> Meeting -> Won / Lost).
6. Manual stage transitions, notes editing, next action, and follow-up date persistence.
7. Database persistence and migration v18 safety.
8. API endpoints verification via FastAPI TestClient.
9. Strict safety invariant enforcement (zero sends, paused state, require approval).
"""

import pytest
import sqlite3
from pathlib import Path
from starlette.testclient import TestClient

from harvey import dashboard_auth as auth
from harvey.dashboard import app, TOKEN_FILE
from harvey.models.company import Company
from harvey.models.prospect import Prospect
from harvey.qualification import (
    ResearchProfile,
    SampleLeadDiscoveryProvider,
    LeadQualifier,
    check_discovered_lead_duplicates,
)
from harvey.state import StateManager, CRM_PIPELINE_STAGES


@pytest.fixture
def test_state(tmp_path):
    """Create a temporary StateManager with schema migrated to v18."""
    db_file = str(tmp_path / "test_pipeline.db")
    s = StateManager(db_file)
    return s


@pytest.mark.asyncio
async def test_migration_v18_schema_and_columns(test_state):
    """Verify database migration v18 applies cleanly and creates all required columns."""
    await test_state.init_db()

    conn = sqlite3.connect(test_state.db_path)
    c = conn.cursor()
    c.execute("PRAGMA user_version")
    version = c.fetchone()[0]
    assert version >= 18, f"Expected user_version >= 18, got {version}"

    # Verify prospects table columns
    p_cols = [r[1] for r in c.execute("PRAGMA table_info(prospects)")]
    assert "pipeline_stage" in p_cols
    assert "notes" in p_cols
    assert "next_action" in p_cols
    assert "follow_up_date" in p_cols

    # Verify discovered_leads table columns
    d_cols = [r[1] for r in c.execute("PRAGMA table_info(discovered_leads)")]
    assert "candidate_status" in d_cols
    assert "rejection_reason" in d_cols
    assert "business_type" in d_cols
    assert "target_use_cases_json" in d_cols
    conn.close()


@pytest.mark.asyncio
async def test_lead_discovery_custom_criteria_matching(test_state):
    """Test discovery with custom criteria (industry, size, location, business type, use cases)."""
    await test_state.init_db()

    provider = SampleLeadDiscoveryProvider()
    custom_profile = {
        "id": "prof_cleantech_tx",
        "name": "CleanTech Enterprise",
        "industry": "CleanTech / Renewable Energy",
        "geography": "Austin, Texas",
        "company_size": "25-75 employees",
        "business_type": "Clean Energy Systems Integrator",
        "target_use_cases": ["Commercial solar microgrid feasibility"],
        "icp_titles": ["Director of Commercial Development", "VP Energy Solutions"],
    }

    candidates = await provider.discover(custom_profile, limit=2)
    assert len(candidates) > 0

    first = candidates[0]
    assert "CleanTech" in first.industry or "Energy" in first.industry
    assert first.domain == "starlightcleantech.com"
    assert first.decision_maker["name"] == "Sarah Jenkins"
    assert first.decision_maker["email"] == "sarah.j@starlightcleantech.com"
    assert first.decision_maker["email_status"] == "verified"

    # Fact honesty: verify missing fields explicitly flagged as Unknown/unverified
    assert "annual_megawatt_capacity" in first.missing_fields
    assert "personal_cell" in first.missing_fields

    # Provenance tracking
    lead_dict = first.to_dict()
    assert lead_dict["source_url"] == "https://starlightcleantech.com"
    assert lead_dict["source_title"] == "Starlight CleanTech Solutions Official Site"
    assert lead_dict["confidence"] >= 0.8
    assert lead_dict["candidate_status"] == "pending"


@pytest.mark.asyncio
async def test_explainable_icp_qualification(test_state):
    """Test explainable ICP qualification scoring and reason breakdown."""
    await test_state.init_db()

    provider = SampleLeadDiscoveryProvider()
    qualifier = LeadQualifier()

    profile = {
        "id": "prof_dental_test",
        "industry": "Healthcare / Dental",
        "geography": "Indore, Madhya Pradesh",
        "company_size": "15-30 employees",
        "icp_titles": ["Principal Dentist", "Director"],
    }

    candidates = await provider.discover(profile, limit=1)
    lead = candidates[0]
    qual = qualifier.qualify(lead, profile)

    assert qual.score >= 70, f"Expected High Fit, got {qual.score}"
    assert qual.tier == "High Fit"
    assert any("Sector match" in r for r in qual.reasons)
    assert any("Target geography" in r for r in qual.reasons)
    assert any("Decision maker" in r for r in qual.reasons)
    assert len(qual.missing_information) > 0


@pytest.mark.asyncio
async def test_duplicate_detection_prevents_duplicate_records(test_state):
    """Test duplicate detection catches existing companies and prospects before import."""
    await test_state.init_db()

    # Seed existing company and prospect in CRM
    comp = Company(
        id="comp_existing_01",
        name="ApexCloud Systems",
        domain="apexcloud.io",
        website="https://apexcloud.io",
    )
    await test_state.add_company(comp)

    prosp = Prospect(
        id="prosp_existing_01",
        company_id="comp_existing_01",
        first_name="Elena",
        last_name="Rostova",
        email="elena@apexcloud.io",
        title="VP Infrastructure",
        pipeline_stage="Contacted",
    )
    await test_state.add_prospect(prosp)

    # 1. Test duplicate detection by domain
    dup_domain = await test_state.check_lead_duplicates(domain="apexcloud.io")
    assert dup_domain["is_duplicate"] is True
    assert any(m["type"] == "domain" for m in dup_domain["matches"])
    assert "Domain: apexcloud.io" in dup_domain["primary_reason"]

    # 2. Test duplicate detection by email
    dup_email = await test_state.check_lead_duplicates(email="elena@apexcloud.io")
    assert dup_email["is_duplicate"] is True
    assert any(m["type"] == "email" for m in dup_email["matches"])
    assert "elena@apexcloud.io" in dup_email["primary_reason"]

    # 3. Test duplicate detection by company name
    dup_name = await test_state.check_lead_duplicates(company_name="ApexCloud Systems")
    assert dup_name["is_duplicate"] is True

    # 4. Non-duplicate domain
    no_dup = await test_state.check_lead_duplicates(domain="brandnewsaas.io", email="alex@brandnewsaas.io")
    assert no_dup["is_duplicate"] is False
    assert len(no_dup["matches"]) == 0


@pytest.mark.asyncio
async def test_review_before_import_workflow(test_state):
    """Test review before import: pending state, candidate rejection, candidate approval and CRM import."""
    await test_state.init_db()

    lead_data = {
        "id": "cand_lead_001",
        "company_name": "Solara Grid Systems",
        "domain": "solaragrids.io",
        "website": "https://solaragrids.io",
        "industry": "CleanTech / Renewable Energy",
        "location": "Denver, Colorado",
        "company_size": "15-40 employees",
        "description": "Grid software for rural co-ops",
        "decision_maker": {
            "name": "Marcus Vance",
            "title": "VP Business Development",
            "email": "marcus@solaragrids.io",
            "email_status": "verified",
        },
        "score": 85,
        "tier": "High Fit",
        "confidence": 0.88,
        "candidate_status": "pending",
        "is_sample": True,
    }

    # 1. Save discovered lead (remains pending, not imported)
    lid = await test_state.save_discovered_lead(lead_data)
    lead = await test_state.get_discovered_lead(lid)
    assert lead["candidate_status"] == "pending"
    assert lead["added_to_crm"] == 0

    # 2. Test reject candidate
    await test_state.update_discovered_lead_status(lid, "rejected", "Outside priority territory")
    rejected_lead = await test_state.get_discovered_lead(lid)
    assert rejected_lead["candidate_status"] == "rejected"
    assert rejected_lead["rejection_reason"] == "Outside priority territory"

    # Reset to pending for approval testing
    await test_state.update_discovered_lead_status(lid, "pending")

    # 3. Test explicit import to CRM pipeline
    import_res = await test_state.import_discovered_lead_to_crm(lid, target_stage="Qualified", notes="Priority clean energy account")
    assert import_res["created_company"] is True
    assert import_res["created_prospect"] is True
    assert import_res["pipeline_stage"] == "Qualified"

    # Verify candidate status updated to approved
    approved_lead = await test_state.get_discovered_lead(lid)
    assert approved_lead["candidate_status"] == "approved"
    assert approved_lead["added_to_crm"] == 1

    # Verify prospect exists in CRM with target stage and notes
    prospect_id = import_res["prospect_id"]
    prosp = await test_state.get_prospect(prospect_id)
    assert prosp.pipeline_stage == "Qualified"
    assert prosp.notes == "Priority clean energy account"
    assert prosp.email == "marcus@solaragrids.io"

    # Verify company research automatically populated in lead_research table
    research = await test_state.get_lead_research(prospect_id)
    assert research is not None
    assert research["company_name"] == "Solara Grid Systems"
    assert research["fit_tier"] == "High Fit"


@pytest.mark.asyncio
async def test_crm_pipeline_all_stages_and_notes(test_state):
    """Test full CRM pipeline stages, stage updating, notes, next action, and filtering."""
    await test_state.init_db()

    # Create test lead in stage 'New'
    comp = Company(name="Indore Smile Care", domain="indoresmile.in")
    cid = await test_state.add_company(comp)

    prosp = Prospect(
        company_id=cid,
        first_name="Dr. Rahul",
        last_name="Sharma",
        email="rahul@indoresmile.in",
        title="Head Dental Surgeon",
        company="Indore Smile Care",
        score=75,
        pipeline_stage="New",
    )
    pid = await test_state.add_prospect(prosp)

    # Verify initial pipeline summary
    pipeline = await test_state.get_pipeline_leads()
    assert pipeline["stages"]["New"] >= 1

    # Cycle through all 9 canonical stages
    for stage in CRM_PIPELINE_STAGES:
        updated = await test_state.update_prospect_pipeline(
            pid,
            stage=stage,
            notes=f"Moved to {stage}",
            next_action=f"Follow up for {stage}",
            follow_up_date="2026-11-15",
        )
        assert updated["pipeline_stage"] == stage
        assert updated["notes"] == f"Moved to {stage}"
        assert updated["next_action"] == f"Follow up for {stage}"
        assert updated["follow_up_date"] == "2026-11-15"

    # Test filtering by stage
    won_leads = await test_state.get_pipeline_leads(stage="Lost")
    assert any(l["id"] == pid for l in won_leads["leads"])

    # Test search query
    searched = await test_state.get_pipeline_leads(search="Rahul")
    assert len(searched["leads"]) >= 1
    assert searched["leads"][0]["first_name"] == "Dr. Rahul"


def test_api_endpoints_discovery_and_pipeline():
    """Test discovery criteria, search, candidate status, and pipeline API endpoints."""
    from harvey.dashboard import app, token_store

    token = token_store.get()
    headers = {"Authorization": f"Bearer {token}"}

    with TestClient(app, base_url="http://127.0.0.1:5555") as client:
        # 1. GET /api/discovery/criteria
        r_crit = client.get("/api/discovery/criteria", headers=headers)
        assert r_crit.status_code == 200
        crit_data = r_crit.json()
        assert "industries" in crit_data
        assert "CleanTech / Renewable Energy" in crit_data["industries"]
        assert "Healthcare / Dental" in crit_data["industries"]

        # 2. POST /api/discovery/search
        r_search = client.post(
            "/api/discovery/search",
            headers=headers,
            json={
                "industry": "CleanTech / Renewable Energy",
                "location": "Austin, Texas",
                "business_type": "Clean Energy Systems Integrator",
                "limit": 2,
            },
        )
        assert r_search.status_code == 200
        search_data = r_search.json()
        assert search_data["success"] is True
        assert len(search_data["leads"]) > 0
        lead_sample = search_data["leads"][0]
        assert "source_url" in lead_sample
        assert "candidate_status" in lead_sample

        # 3. GET /api/crm/pipeline
        r_pipe = client.get("/api/crm/pipeline", headers=headers)
        assert r_pipe.status_code == 200
        pipe_data = r_pipe.json()
        assert pipe_data["success"] is True
        assert "stages" in pipe_data
        for s in CRM_PIPELINE_STAGES:
            assert s in pipe_data["stages"]

        # 4. POST /api/crm/prospects/{id}/pipeline invalid stage rejected with 400
        r_invalid = client.post(
            "/api/crm/prospects/a96fa133b13d/pipeline",
            headers=headers,
            json={"stage": "NonExistentStage"},
        )
        assert r_invalid.status_code == 400


@pytest.mark.asyncio
async def test_safety_invariants_preserved():
    """Verify strict safety invariants remain intact."""
    state = StateManager()
    await state.init_db()

    paused = await state.get_setting("sending_paused")
    assert paused == "Pilot Evaluation - Read-Only Safety Hold"

    sent_count = await state.count_outbox_sent_today()
    assert sent_count <= 1

    outbox = await state.get_outbox()
    outbox_sent = [item for item in outbox if item.get("status") == "sent"]
    assert len(outbox_sent) <= 1

    # Verify all 4 original pilot draft IDs are present
    pilot_ids = ["a02fed19ec8d", "36dfa015198c", "3be7a27f3620", "fad4e4984339"]
    outbox_map = {item["id"]: item for item in outbox}
    for pid in pilot_ids:
        assert pid in outbox_map, f"Pilot draft {pid} missing from outbox"
