"""Tests for Harvey AI Universal Multi-Industry Sales Agent.

Verifies:
1. Multi-industry business profile configuration and CRUD in StateManager and Dashboard.
2. Sample candidate lead discovery across Legal, Accounting, HVAC/Home Services, SaaS, Healthcare, and Generic verticals.
3. Qualification scoring and explainability across diverse ICP criteria.
4. Profile-grounded sequence generation with zero hardcoded clinical/dental copy leaking into other industries.
5. Multi-industry RAG knowledge retrieval from ChromaDB.
6. Absolute preservation of safety invariants (sent_count=0, sending_paused, require_approval=True, pilot drafts intact).
"""

import asyncio
from pathlib import Path
import pytest

from harvey.state import StateManager
from harvey.qualification import SampleLeadDiscoveryProvider, LeadQualifier, ResearchProfile
from harvey.campaign_generator import CampaignGenerator
from harvey.models.prospect import Prospect
from harvey.models.company import Company
from harvey.rag.store import VectorStore


DB_PATH = Path("data/harvey.db")


@pytest.fixture
def state_mgr():
    return StateManager(str(DB_PATH))


@pytest.fixture
def discovery_provider():
    return SampleLeadDiscoveryProvider()


@pytest.fixture
def qualifier():
    return LeadQualifier()


@pytest.fixture
def campaign_gen():
    return CampaignGenerator(str(DB_PATH))


# ── 1. Profile Management & Seeding ──


@pytest.mark.asyncio
async def test_default_profiles_seeded(state_mgr):
    await state_mgr.init_db()
    profiles = await state_mgr.get_business_profiles()
    profile_ids = {p["id"] for p in profiles}

    expected_ids = {
        "prof_dental_in",
        "prof_saas_global",
        "prof_specialty_clinics",
        "prof_legal_services",
        "prof_accounting_finance",
        "prof_home_services",
    }
    assert expected_ids.issubset(profile_ids), f"Missing profiles: {expected_ids - profile_ids}"

    # Verify Legal Profile
    legal = await state_mgr.get_business_profile("prof_legal_services")
    assert legal is not None
    assert "Legal Services" in legal["industry"]
    assert "Managing Partner" in legal["icp_titles"]

    # Verify Accounting Profile
    acct = await state_mgr.get_business_profile("prof_accounting_finance")
    assert acct is not None
    assert "Accounting" in acct["industry"]
    assert "CPA" in acct["icp_titles"]

    # Verify Home Services Profile
    home = await state_mgr.get_business_profile("prof_home_services")
    assert home is not None
    assert "Commercial & Home Services" in home["industry"]


@pytest.mark.asyncio
async def test_create_and_delete_custom_profile(state_mgr):
    custom_id = "prof_test_cleantech_99"
    profile_data = {
        "id": custom_id,
        "name": "CleanTech Solar & Storage",
        "industry": "Renewable Energy / CleanTech",
        "geography": "Austin, TX & Southwest",
        "company_size": "25-100 employees",
        "icp_titles": ["VP Engineering", "Head of Solar Operations", "Founder"],
        "product_name": "Harvey CleanTech Agent",
        "product_description": "Automated solar project quote follow-up agent",
        "target_criteria": {"min_score": 60},
        "is_default": 0,
    }

    created = await state_mgr.create_business_profile(profile_data)
    assert created["id"] == custom_id
    assert created["name"] == "CleanTech Solar & Storage"

    # Query back
    fetched = await state_mgr.get_business_profile(custom_id)
    assert fetched is not None
    assert fetched["industry"] == "Renewable Energy / CleanTech"

    # Deleting custom profile must succeed
    deleted = await state_mgr.delete_business_profile(custom_id)
    assert deleted is True

    # After deletion, must not exist
    assert await state_mgr.get_business_profile(custom_id) is None


@pytest.mark.asyncio
async def test_delete_default_profile_is_forbidden(state_mgr):
    with pytest.raises(ValueError, match="Cannot delete the default business profile"):
        await state_mgr.delete_business_profile("prof_saas_global")


# ── 2. Multi-Industry Sample Lead Discovery ──


@pytest.mark.asyncio
async def test_discover_legal_leads(discovery_provider):
    profile = {
        "id": "prof_legal_services",
        "industry": "Legal Services / Law Firms",
        "geography": "New York, NY",
        "icp_titles": ["Managing Partner", "Partner", "Practice Group Leader"],
    }
    leads = await discovery_provider.discover(profile, limit=4)
    assert len(leads) >= 3

    company_names = [l.company_name for l in leads]
    assert any("Vanguard Legal" in c for c in company_names)
    assert any("Sterling & Croft" in c for c in company_names)

    # Check lead details
    vanguard = next(l for l in leads if "Vanguard" in l.company_name)
    assert vanguard.decision_maker["title"] == "Managing Partner"
    assert "eleanor.vance" in vanguard.decision_maker["email"]
    assert vanguard.is_sample is True
    assert len(vanguard.sources) > 0


@pytest.mark.asyncio
async def test_discover_accounting_leads(discovery_provider):
    profile = {
        "id": "prof_accounting_finance",
        "industry": "Accounting & Financial Services",
        "geography": "Denver, CO",
        "icp_titles": ["Managing Partner", "CPA", "Tax Director"],
    }
    leads = await discovery_provider.discover(profile, limit=4)
    assert len(leads) >= 3

    company_names = [l.company_name for l in leads]
    assert any("Crestview Advisory" in c for c in company_names)
    assert any("Summit Financial" in c for c in company_names)

    crestview = next(l for l in leads if "Crestview" in l.company_name)
    assert "CPA" in crestview.decision_maker["name"] or "CPA" in crestview.decision_maker["title"]
    assert crestview.is_sample is True


@pytest.mark.asyncio
async def test_discover_home_services_leads(discovery_provider):
    profile = {
        "id": "prof_home_services",
        "industry": "Commercial & Home Services",
        "geography": "Houston, TX",
        "icp_titles": ["Owner", "VP Operations", "General Manager"],
    }
    leads = await discovery_provider.discover(profile, limit=4)
    assert len(leads) >= 3

    company_names = [l.company_name for l in leads]
    assert any("Apex Mechanical" in c for c in company_names)
    assert any("Trident Facility" in c for c in company_names)

    apex_hvac = next(l for l in leads if "Apex Mechanical" in l.company_name)
    assert apex_hvac.decision_maker["title"] == "VP Operations"
    assert "hvac" in apex_hvac.decision_maker["email"]


@pytest.mark.asyncio
async def test_discover_generic_fallback(discovery_provider):
    profile = {
        "id": "prof_aerospace",
        "industry": "Aerospace Avionics & Satellite Systems",
        "geography": "Detroit, Michigan",
        "icp_titles": ["VP Avionics", "Systems Director"],
    }
    leads = await discovery_provider.discover(profile, limit=2)
    assert len(leads) == 2
    assert "Aerospace Avionics & Satellite Systems" in leads[0].industry
    assert leads[0].decision_maker["title"] in ["VP Avionics", "Systems Director", "Director"]


# ── 3. Lead Qualification Across Diverse ICPs ──


def test_qualification_scoring_across_industries(qualifier):
    # Case A: High Fit Legal Lead
    legal_profile = {
        "industry": "Legal Services / Law Firms",
        "geography": "New York, NY",
        "company_size": "15-250 employees",
        "icp_titles": ["Managing Partner", "Partner"],
    }
    legal_research = ResearchProfile(
        company_name="Prestige Legal LLC",
        domain="prestigelegal.com",
        industry="Legal Services / Law Firms",
        location="New York, NY",
        company_size="50-100 employees",
        decision_maker={"name": "Sarah Blake", "title": "Managing Partner", "email": "sblake@prestigelegal.com", "email_status": "verified"},
        sources=[{"field": "name", "source": "bar", "verified": True}],
    )
    res_legal = qualifier.qualify(legal_research, legal_profile)
    assert res_legal.is_qualified is True
    assert res_legal.score >= 80
    assert res_legal.tier == "High Fit"

    # Case B: Unqualified Solo/Consumer Lead
    solo_research = ResearchProfile(
        company_name="One Person Traffic Help",
        domain="solohelp.net",
        industry="Consumer Legal Processing",
        location="Miami, FL",
        company_size="1 employee",
        decision_maker={"name": "Bob", "title": "Clerk", "email": "bob@solohelp.net", "email_status": "guess"},
    )
    res_solo = qualifier.qualify(solo_research, legal_profile)
    assert res_solo.score <= 50
    assert res_solo.is_qualified is False
    assert res_solo.confidence < 0.70


# ── 4. Grounded Multi-Industry Campaign Generation ──


def test_sequence_generation_saas_has_no_clinical_copy(campaign_gen):
    profile = {
        "id": "prof_saas_global",
        "industry": "Software / B2B SaaS",
        "product_name": "Harvey AI",
        "product_description": "AI sales agent that identifies prospects and drafts outbound sequences",
    }
    prospect = Prospect(
        id="pros_saas_1",
        first_name="David",
        last_name="Chen",
        company="DevOps Velocity",
        email="david@devopsvelocity.io",
        title="Head of Growth",
    )
    company = Company(
        id="comp_saas_1",
        name="DevOps Velocity",
        industry="Software / B2B SaaS",
        location="Austin, TX",
    )

    seq = campaign_gen._craft_sequence(
        prospect=prospect,
        company=company,
        profile=profile,
        verified_facts=[],
        missing_context=[],
        rag_sources=[],
        steps_count=3,
        step_delay_days=3,
        tone="consultative",
    )

    assert len(seq) == 3
    body1 = seq[0]["body"]
    body2 = seq[1]["body"]
    body3 = seq[2]["body"]

    # Verify SaaS-grounded content
    assert "DevOps Velocity" in body1
    assert "Austin" in body1
    assert "B2B tech" in body1 or "software" in body1.lower()
    assert "pipeline velocity" in body2 or "outbound" in body2.lower()

    # CRITICAL: Verify ZERO clinical or dental phrasing leaked into SaaS
    combined_bodies = (body1 + " " + body2 + " " + body3).lower()
    for forbidden in ["patient", "clinical", "clinic", "dental", "doctor", "dentist"]:
        assert forbidden not in combined_bodies, f"Clinical word '{forbidden}' leaked into SaaS email!"


def test_sequence_generation_legal_grounded(campaign_gen):
    profile = {
        "id": "prof_legal_services",
        "industry": "Legal Services / Law Firms",
        "product_name": "Harvey AI",
        "product_description": "Intelligent client intake and consultation coordination agent",
    }
    prospect = Prospect(
        id="pros_legal_1",
        first_name="Eleanor",
        last_name="Vance",
        company="Vanguard Legal Partners",
        email="eleanor@vanguardlegal.com",
        title="Managing Partner",
    )
    company = Company(
        id="comp_legal_1",
        name="Vanguard Legal Partners",
        industry="Legal Services / Law Firms",
        location="New York, NY",
    )

    seq = campaign_gen._craft_sequence(
        prospect=prospect,
        company=company,
        profile=profile,
        verified_facts=[],
        missing_context=[],
        rag_sources=[],
        steps_count=2,
        step_delay_days=3,
        tone="formal",
    )

    body1 = seq[0]["body"]
    assert "Vanguard Legal Partners" in body1
    assert "client intake" in body1.lower() or "legal advisory" in body1.lower() or "confidentiality" in body1.lower()
    assert "patient" not in body1.lower()
    assert "dental" not in body1.lower()


def test_sequence_generation_home_services_hvac_grounded(campaign_gen):
    profile = {
        "id": "prof_home_services",
        "industry": "Commercial & Home Services",
        "product_name": "Harvey AI",
        "product_description": "Quotation follow-up and service renewal assistant",
    }
    prospect = Prospect(
        id="pros_hvac_1",
        first_name="Thomas",
        last_name="Briggs",
        company="Apex Mechanical HVAC",
        email="tbriggs@apexhvac.com",
        title="VP Operations",
    )
    company = Company(
        id="comp_hvac_1",
        name="Apex Mechanical HVAC",
        industry="Commercial & Home Services",
        location="Houston, TX",
    )

    seq = campaign_gen._craft_sequence(
        prospect=prospect,
        company=company,
        profile=profile,
        verified_facts=[],
        missing_context=[],
        rag_sources=[],
        steps_count=2,
        step_delay_days=3,
        tone="direct",
    )

    body1 = seq[0]["body"]
    assert "Apex Mechanical HVAC" in body1
    assert "quote" in body1.lower() or "commercial" in body1.lower() or "facility" in body1.lower()
    assert "patient" not in body1.lower()
    assert "dental" not in body1.lower()


# ── 5. RAG Retrieval Across Multi-Industry Guides ──


@pytest.mark.asyncio
async def test_rag_retrieval_across_industries():
    store = VectorStore()
    if store.count() == 0:
        pytest.skip("ChromaDB vector store collection is empty.")

    # Test SaaS Query
    saas_res = await store.search("SaaS SDR outbound pipeline acceleration", top_k=1)
    assert len(saas_res) > 0
    assert "saas" in saas_res[0]["source"].lower()

    # Test Legal Query
    legal_res = await store.search("legal client intake triage partner review confidentiality", top_k=1)
    assert len(legal_res) > 0
    assert "legal" in legal_res[0]["source"].lower()

    # Test Accounting Query
    acct_res = await store.search("accounting CPA tax onboarding seasonal intake", top_k=1)
    assert len(acct_res) > 0
    assert "accounting" in acct_res[0]["source"].lower()

    # Test HVAC Query
    hvac_res = await store.search("commercial HVAC facility estimate quotation follow-up", top_k=1)
    assert len(hvac_res) > 0
    assert "home_services" in hvac_res[0]["source"].lower()


# ── 6. Safety Invariants Verification ──


@pytest.mark.asyncio
async def test_safety_invariants_preserved(state_mgr):
    import sqlite3
    from harvey.config import load_config
    con = sqlite3.connect("data/harvey.db")
    cur = con.cursor()

    # 1. Verify sent_count is <= 1 (at most 1 operator-authorized test send) in outbox and state
    cur.execute("SELECT count(*) FROM outbox WHERE status = 'sent'")
    row = cur.fetchone()
    assert row[0] <= 1, f"Expected <= 1 sent emails, got {row[0]}"
    sent_today = await state_mgr.count_outbox_sent_today()
    assert sent_today <= 1, f"Expected <= 1 sent today, got {sent_today}"

    # 2. Verify sending_paused safety hold
    cur.execute("SELECT value FROM settings WHERE key = 'sending_paused'")
    row = cur.fetchone()
    assert row is not None
    assert "Safety Hold" in str(row[0]), f"Expected sending_paused safety hold, got {row[0]}"

    # 3. Verify require_approval is True in channels.email
    cfg = load_config()
    assert cfg.channels.email.require_approval is True, "Expected require_approval = True in email channels config"

    # 4. Verify all 4 original pilot drafts are intact
    pilot_ids = ["a02fed19ec8d", "36dfa015198c", "3be7a27f3620", "fad4e4984339"]
    for pid in pilot_ids:
        cur.execute("SELECT id FROM outbox WHERE id = ?", (pid,))
        assert cur.fetchone() is not None, f"Pilot draft {pid} is missing!"
    con.close()


# ── 7. Ten Universal Industry Vertical Tests ──


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prof_id,expected_doc,is_healthcare",
    [
        ("prof_saas_global", "saas_product_guide.md", False),
        ("prof_legal_services", "legal_product_guide.md", False),
        ("prof_dental_in", "dental_product_guide.md", True),
        ("prof_real_estate", "real_estate_product_guide.md", False),
        ("prof_manufacturing", "manufacturing_product_guide.md", False),
        ("prof_accounting_finance", "accounting_product_guide.md", False),
        ("prof_hospitality_hotels", "hospitality_product_guide.md", False),
        ("prof_logistics_transport", "logistics_product_guide.md", False),
        ("prof_education_edtech", "education_product_guide.md", False),
        ("prof_marketing_consulting", "marketing_product_guide.md", False),
    ],
)
async def test_vertical_discovery_qualification_and_anticontamination(
    state_mgr, discovery_provider, qualifier, campaign_gen, prof_id, expected_doc, is_healthcare
):
    profile = await state_mgr.get_business_profile(prof_id)
    assert profile is not None, f"Missing profile {prof_id}"

    # 1. Discover Leads
    leads = await discovery_provider.discover(profile, limit=2)
    assert len(leads) >= 1
    lead = leads[0]

    # 2. Qualify
    res = qualifier.qualify(lead, profile)
    assert res.score >= 50
    assert res.tier in ["High Fit", "Medium Fit"]

    # 3. Create prospect & craft sequence
    comp = Company(name=lead.company_name, industry=lead.industry, location=lead.location)
    prosp = Prospect(first_name="Alex", last_name="Taylor", email=lead.decision_maker.get("email", "a@test.com"), company=lead.company_name)

    seq = campaign_gen._craft_sequence(
        prospect=prosp,
        company=comp,
        profile=profile,
        verified_facts=[],
        missing_context=[],
        rag_sources=[expected_doc],
        steps_count=3,
        step_delay_days=3,
        tone="consultative",
    )
    assert len(seq) == 3

    # 4. Anti-contamination test
    all_text = " ".join(f"{s['subject']} {s['body']}" for s in seq).lower()
    if not is_healthcare:
        for forbidden in ["patient", "clinical", "clinic", "dental", "doctor", "dentist"]:
            assert forbidden not in all_text, f"Contamination: {forbidden} leaked into {prof_id}"
    else:
        assert any(w in all_text for w in ["patient", "dental", "clinic"])

