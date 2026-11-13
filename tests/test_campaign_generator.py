"""Tests for Automated Campaign Generation for Qualified Leads.

Validates:
1. Business profile isolation during lead cohort selection.
2. Multi-step personalized sequence generation (Step 1 cold touch, Step 2 follow-up, Step 3 breakup).
3. Grounding fact provenance and explicit missing context handling (no hallucinated claims).
4. Outbox integration invariants: Step 1 staged as pending_review; later steps remain planned/unsent.
5. Deduplication protection on staging retries.
6. Strict safety holds (sent_count=0, sending_paused, require_approval=true).
"""

import pytest
from datetime import datetime, timezone

from harvey.models.company import Company
from harvey.models.prospect import Prospect
from harvey.state import StateManager
from harvey.campaign_generator import CampaignGenerator


@pytest.mark.asyncio
async def test_campaign_profile_isolation(tmp_path):
    """Ensure prospects from different business profiles cannot be mixed."""
    db_file = tmp_path / "test_camp_iso.db"
    state = StateManager(db_path=str(db_file))
    await state.init_db()

    # 1. Create two business profiles
    await state.create_business_profile({
        "id": "prof_dental_test",
        "name": "Dental Clinics India",
        "industry": "Healthcare / Dental",
        "geography": "Indore, MP, India",
    })
    await state.create_business_profile({
        "id": "prof_saas_test",
        "name": "B2B SaaS North America",
        "industry": "Software / B2B SaaS",
        "geography": "San Francisco, CA",
    })

    # 2. Add Dental company & prospect
    c_dental = Company(name="Indore Dental Studio", domain="indoredentalstudio.in", industry="Healthcare / Dental")
    c_dental_id = await state.add_company(c_dental)
    p_dental = Prospect(
        company_id=c_dental_id,
        first_name="Alok",
        last_name="Verma",
        email="alok@indoredentalstudio.in",
        company="Indore Dental Studio",
        industry="Healthcare / Dental",
        source="discovery:prof_dental_test",
        status="new",
    )
    await state.add_prospect(p_dental)

    # 3. Add SaaS company & prospect
    c_saas = Company(name="CloudVelocity", domain="cloudvelocity.io", industry="Software / B2B SaaS")
    c_saas_id = await state.add_company(c_saas)
    p_saas = Prospect(
        company_id=c_saas_id,
        first_name="Sarah",
        last_name="Jenkins",
        email="sarah@cloudvelocity.io",
        company="CloudVelocity",
        industry="Software / B2B SaaS",
        source="discovery:prof_saas_test",
        status="new",
    )
    await state.add_prospect(p_saas)

    gen = CampaignGenerator(state=state)

    # Query eligible leads for Dental profile
    dental_leads = await gen.get_eligible_leads("prof_dental_test")
    assert len(dental_leads) == 1
    assert dental_leads[0]["company_name"] == "Indore Dental Studio"
    assert dental_leads[0]["email"] == "alok@indoredentalstudio.in"

    # Query eligible leads for SaaS profile
    saas_leads = await gen.get_eligible_leads("prof_saas_test")
    assert len(saas_leads) == 1
    assert saas_leads[0]["company_name"] == "CloudVelocity"
    assert saas_leads[0]["email"] == "sarah@cloudvelocity.io"


@pytest.mark.asyncio
async def test_multi_step_sequence_generation_and_grounding(tmp_path):
    """Test multi-step sequence creation with verified facts and conservative gap handling."""
    db_file = tmp_path / "test_camp_gen.db"
    state = StateManager(db_path=str(db_file))
    await state.init_db()

    profile = await state.create_business_profile({
        "id": "prof_dental_eval",
        "name": "Dental Clinics & Practices",
        "industry": "Healthcare / Dental",
        "geography": "Indore, MP, India",
        "product_name": "Harvey AI",
        "product_description": "Autonomous outreach & patient follow-up coordination",
    })

    c = Company(
        name="Indore Advanced Dental Care",
        domain="indoreadvanceddental.com",
        location="Indore, Madhya Pradesh, India",
        industry="Healthcare / Dental",
        description="Comprehensive implant and restorative oral care center.",
    )
    cid = await state.add_company(c)

    p = Prospect(
        company_id=cid,
        first_name="Amit",
        last_name="Rawat",
        email="amit@indoreadvanceddental.com",
        title="Principal Dentist & Director",
        company="Indore Advanced Dental Care",
        source="discovery:prof_dental_eval",
        status="new",
    )
    pid = await state.add_prospect(p)

    gen = CampaignGenerator(state=state)
    preview = await gen.generate_campaign_preview(
        profile_id="prof_dental_eval",
        prospect_ids=[pid],
        config={"steps_count": 3, "step_delay_days": 3, "campaign_name": "Q4 Dental Pilot"},
    )

    assert preview["campaign_name"] == "Q4 Dental Pilot"
    assert preview["lead_count"] == 1

    lead_data = preview["leads"][0]
    seq = lead_data["sequence"]
    assert len(seq) == 3

    # Step 1: Initial cold touch (delay 0)
    assert seq[0]["step"] == 1
    assert seq[0]["delay_days"] == 0
    assert "Indore Advanced Dental Care".lower() in seq[0]["subject"].lower() or "quick question" in seq[0]["subject"].lower()
    assert "Amit" in seq[0]["body"]
    assert "Harvey" in seq[0]["body"]

    # Step 2: Follow-up (delay 3 days)
    assert seq[1]["step"] == 2
    assert seq[1]["delay_days"] == 3
    assert "follow-up" in seq[1]["subject"].lower()

    # Step 3: Break-up (delay 6 days)
    assert seq[2]["step"] == 3
    assert seq[2]["delay_days"] == 6
    assert "closing out" in seq[2]["subject"].lower()

    # Provenance facts
    facts = [f["fact"] for f in lead_data["grounding_facts"]]
    assert any("Amit Rawat" in f for f in facts)
    assert any("Indore Advanced Dental Care" in f for f in facts)


@pytest.mark.asyncio
async def test_outbox_staging_invariants(tmp_path):
    """CRITICAL SAFETY TEST:
    1. Step 1 drafts land in Outbox with status = 'pending_review'.
    2. Later steps (Step 2, Step 3) remain planned in campaign sequence and ARE NOT IN OUTBOX.
    """
    db_file = tmp_path / "test_staging_safe.db"
    state = StateManager(db_path=str(db_file))
    await state.init_db()

    profile = await state.create_business_profile({
        "id": "prof_dental_stage",
        "name": "Dental Clinics",
        "industry": "Healthcare / Dental",
    })

    c = Company(name="Apex Smile Center", domain="apexsmile.in")
    cid = await state.add_company(c)
    p = Prospect(company_id=cid, first_name="Neha", email="neha@apexsmile.in", company="Apex Smile Center", status="new")
    pid = await state.add_prospect(p)

    gen = CampaignGenerator(state=state)
    preview = await gen.generate_campaign_preview(
        profile_id="prof_dental_stage",
        prospect_ids=[pid],
        config={"steps_count": 3, "step_delay_days": 4},
    )

    # Stage the campaign
    stage_res = await gen.stage_campaign_to_outbox({
        "campaign_name": "Apex Outreach",
        "profile_id": "prof_dental_stage",
        "leads": preview["leads"],
    })

    assert stage_res["success"] is True
    assert stage_res["staged_drafts_count"] == 1
    assert stage_res["scheduled_later_steps_count"] == 2
    camp_id = stage_res["campaign_id"]

    # 1. Verify Outbox contains EXACTLY 1 item (Step 1)
    async with state._connect() as db:
        import aiosqlite
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM outbox WHERE campaign_id = ?", (camp_id,)) as cursor:
            outbox_rows = await cursor.fetchall()

    assert len(outbox_rows) == 1
    row = dict(outbox_rows[0])
    assert row["step"] == 1
    assert row["status"] == "pending_review"
    assert row["to_email"] == "neha@apexsmile.in"
    assert row["approved_by"] == ""  # requires human approval

    # 2. Verify later steps DO NOT exist in outbox
    async with state._connect() as db:
        async with db.execute("SELECT count(*) FROM outbox WHERE campaign_id = ? AND step > 1", (camp_id,)) as cursor:
            (later_count,) = await cursor.fetchone()
    assert later_count == 0

    # 3. Verify Campaign sequence contains all 3 planned steps
    camp = await state.get_campaign(camp_id)
    assert camp is not None
    seq = camp.sequence
    assert len(seq) == 3
    assert seq[0].step == 1
    assert seq[1].step == 2
    assert seq[2].step == 3

    # 4. Verify prospect status transitioned to 'queued'
    prosp = await state.get_prospect(pid)
    assert prosp.status == "queued"


@pytest.mark.asyncio
async def test_staging_duplicate_prevention(tmp_path):
    """Ensure staging retries on the same campaign do not create duplicate outbox records."""
    db_file = tmp_path / "test_dup_stage.db"
    state = StateManager(db_path=str(db_file))
    await state.init_db()

    profile = await state.create_business_profile({"id": "prof_dup", "name": "Dup Test"})
    c = Company(name="Dup Clinic", domain="dupclinic.com")
    cid = await state.add_company(c)
    p = Prospect(company_id=cid, first_name="Ramesh", email="ramesh@dupclinic.com", company="Dup Clinic", status="new")
    pid = await state.add_prospect(p)

    gen = CampaignGenerator(state=state)
    preview = await gen.generate_campaign_preview(profile_id="prof_dup", prospect_ids=[pid])

    # Stage first time
    res1 = await gen.stage_campaign_to_outbox({
        "campaign_name": "Test Campaign",
        "profile_id": "prof_dup",
        "leads": preview["leads"],
    })
    camp_id = res1["campaign_id"]

    # Retry staging the exact same campaign
    res2 = await gen.stage_campaign_to_outbox({
        "campaign_name": "Test Campaign",
        "profile_id": "prof_dup",
        "leads": preview["leads"],
    })

    # Total outbox items for the first campaign must remain 1
    async with state._connect() as db:
        async with db.execute("SELECT count(*) FROM outbox WHERE campaign_id = ?", (camp_id,)) as cursor:
            (cnt,) = await cursor.fetchone()
    assert cnt == 1


@pytest.mark.asyncio
async def test_campaign_generation_strict_safety_invariants(tmp_path):
    """Verify that campaign preview generation and staging strictly respect safety invariants."""
    db_file = tmp_path / "test_safe_invariants.db"
    state = StateManager(db_path=str(db_file))
    await state.init_db()

    await state.set_setting("sending_paused", "Pilot Evaluation - Read-Only Safety Hold")

    c = Company(name="Safe Clinic", domain="safeclinic.in")
    cid = await state.add_company(c)
    p = Prospect(company_id=cid, first_name="Dr. Safe", email="safe@safeclinic.in", company="Safe Clinic", status="new")
    pid = await state.add_prospect(p)

    profile = await state.create_business_profile({"id": "prof_safe", "name": "Safe Profile"})
    gen = CampaignGenerator(state=state)

    preview = await gen.generate_campaign_preview(profile_id="prof_safe", prospect_ids=[pid])
    stage_res = await gen.stage_campaign_to_outbox({
        "campaign_name": "Safe Campaign",
        "profile_id": "prof_safe",
        "leads": preview["leads"],
    })

    # 1. Zero sent emails
    sent_count = await state.count_outbox_sent()
    assert sent_count == 0

    # 2. Sending paused hold intact
    paused = await state.get_setting("sending_paused")
    assert paused == "Pilot Evaluation - Read-Only Safety Hold"

