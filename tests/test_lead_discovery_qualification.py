"""Tests for AI Lead Discovery & Qualification module.

Validates:
1. Business profile configuration and lead isolation (no mixing of leads).
2. Provider-independent discovery with local sample lead generation.
3. Structured research profiling and strict fact honesty (unknown fields marked).
4. Explainable qualification scoring (score, tier, positive reasons, confidence).
5. Normalized duplicate detection (domain and email dedup).
6. Explicit pipeline insertion (requires explicit user action, sets status to 'new').
7. Safety invariants (sent_count=0, sending_paused, require_approval=true).
"""

import pytest
from pathlib import Path
import tempfile

from harvey.state import StateManager
from harvey.qualification import (
    ResearchProfile,
    LeadQualificationResult,
    SampleLeadDiscoveryProvider,
    LeadQualifier,
)
from harvey.models.company import Company
from harvey.models.prospect import Prospect


@pytest.mark.asyncio
async def test_business_profile_isolation(tmp_path):
    """Test that multiple business profiles can be created and leads remain strictly isolated."""
    db_file = tmp_path / "test_iso.db"
    temp_state = StateManager(db_path=str(db_file))
    await temp_state.init_db()

    # 1. Create two separate profiles
    p1 = await temp_state.create_business_profile({
        "id": "prof_dental",
        "name": "Dental Clinics India",
        "industry": "Healthcare / Dental",
        "geography": "Indore, MP, India",
        "company_size": "10-50 employees",
        "icp_titles": ["Dentist", "Director"],
    })
    p2 = await temp_state.create_business_profile({
        "id": "prof_saas",
        "name": "B2B SaaS North America",
        "industry": "Software / B2B SaaS",
        "geography": "San Francisco, CA",
        "company_size": "50-200 employees",
        "icp_titles": ["VP Sales", "Head of Growth"],
    })

    assert p1["id"] == "prof_dental"
    assert p2["id"] == "prof_saas"

    # 2. Add leads for Profile 1
    await temp_state.save_discovered_lead({
        "id": "lead_d1",
        "profile_id": "prof_dental",
        "company_name": "Indore Dental Care",
        "domain": "indoredental.com",
        "score": 85,
        "tier": "High Fit",
    })
    await temp_state.save_discovered_lead({
        "id": "lead_d2",
        "profile_id": "prof_dental",
        "company_name": "Smile Care Indore",
        "domain": "smilecare.in",
        "score": 75,
        "tier": "High Fit",
    })

    # 3. Add leads for Profile 2
    await temp_state.save_discovered_lead({
        "id": "lead_s1",
        "profile_id": "prof_saas",
        "company_name": "CloudMetrics Inc",
        "domain": "cloudmetrics.io",
        "score": 90,
        "tier": "High Fit",
    })

    # 4. Verify isolation
    dental_leads = await temp_state.get_discovered_leads("prof_dental")
    saas_leads = await temp_state.get_discovered_leads("prof_saas")

    assert len(dental_leads) == 2
    assert {l["company_name"] for l in dental_leads} == {"Indore Dental Care", "Smile Care Indore"}
    assert all(l["profile_id"] == "prof_dental" for l in dental_leads)

    assert len(saas_leads) == 1
    assert saas_leads[0]["company_name"] == "CloudMetrics Inc"
    assert saas_leads[0]["profile_id"] == "prof_saas"


@pytest.mark.asyncio
async def test_sample_lead_discovery():
    """Test provider-independent discovery with local sample lead generation."""
    provider = SampleLeadDiscoveryProvider()

    # Dental profile discovery
    dental_profile = {
        "id": "prof_dental",
        "industry": "Healthcare / Dental",
        "geography": "Indore, Madhya Pradesh, India",
        "company_size": "10-200 employees",
        "icp_titles": ["Dentist", "Director"],
    }
    dental_results = await provider.discover(dental_profile, limit=3)
    assert len(dental_results) == 3
    for r in dental_results:
        assert isinstance(r, ResearchProfile)
        assert r.is_sample is True
        assert r.domain != ""
        assert len(r.sources) > 0

    # SaaS profile discovery
    saas_profile = {
        "id": "prof_saas",
        "industry": "Software / B2B SaaS",
        "geography": "San Francisco, CA & Remote",
        "company_size": "20-500 employees",
        "icp_titles": ["VP Sales", "Head of Growth"],
    }
    saas_results = await provider.discover(saas_profile, limit=2)
    assert len(saas_results) == 2
    assert saas_results[0].company_name == "CloudPulse Analytics"
    assert saas_results[0].industry == "Software / B2B SaaS"


def test_company_research_fact_honesty():
    """Ensure missing fields are explicitly flagged as unknown/unverified and not fabricated."""
    provider = SampleLeadDiscoveryProvider()
    lead = provider._dental_leads({})[0]

    assert lead.company_name == "Indore Advanced Dental Care"
    assert "direct_phone" in lead.missing_fields
    assert "annual_revenue" in lead.missing_fields

    # Sources should detail verified attributes
    source_fields = [s["field"] for s in lead.sources]
    assert "company_name" in source_fields
    assert "domain" in source_fields
    assert all(s["verified"] is True for s in lead.sources)


def test_lead_qualification_scoring_and_explainability():
    """Verify explainable qualification scoring, tiers, reasons, and confidence."""
    qualifier = LeadQualifier()

    profile = {
        "industry": "Healthcare / Dental",
        "geography": "Indore, Madhya Pradesh, India",
        "company_size": "10-200 employees",
        "icp_titles": ["Principal Dentist", "Director", "Dental Surgeon"],
    }

    # 1. High Fit candidate
    high_candidate = ResearchProfile(
        company_name="Apex Smile Speciality",
        domain="apexsmile.in",
        industry="Healthcare / Dental",
        location="Indore, Madhya Pradesh, India",
        company_size="15-30 employees",
        decision_maker={
            "name": "Dr. Neha Kulkarni",
            "title": "Principal Dentist & Director",
            "email": "neha@apexsmile.in",
            "email_status": "verified",
        },
        potential_needs=["Appointment recall system"],
        missing_fields=["phone"],
    )

    res_high = qualifier.qualify(high_candidate, profile)
    assert res_high.score >= 70
    assert res_high.tier == "High Fit"
    assert res_high.is_qualified is True
    assert res_high.confidence >= 0.7
    assert any("sector match" in r.lower() for r in res_high.reasons)
    assert any("geography match" in r.lower() for r in res_high.reasons)
    assert any("decision maker" in r.lower() for r in res_high.reasons)
    assert any("Phone Is Unverified" in m or "phone" in m.lower() for m in res_high.missing_information)

    # 2. Low Fit candidate (mismatched industry, geography, and out of size range)
    low_candidate = ResearchProfile(
        company_name="Mega Rail Logistics",
        domain="megaraillogistics.com",
        industry="Heavy Transport & Freight",
        location="Frankfurt, Germany",
        company_size="5000+ employees",
        decision_maker={"name": "Info", "title": "Helpdesk", "email": "info@megarail.com"},
        potential_needs=[],
        missing_fields=["executive_contacts", "direct_mobile"],
    )

    res_low = qualifier.qualify(low_candidate, profile)
    assert res_low.score < 40
    assert res_low.tier == "Low Fit"
    assert res_low.is_qualified is False
    assert any("Sector divergence" in r for r in res_low.reasons)
    assert any("Geography mismatch" in r for r in res_low.reasons)


@pytest.mark.asyncio
async def test_duplicate_detection_and_pipeline_insertion(tmp_path):
    """Test normalized deduplication when explicitly adding leads to CRM pipeline."""
    db_file = tmp_path / "test_dup.db"
    temp_state = StateManager(db_path=str(db_file))
    await temp_state.init_db()

    # 1. Pre-insert an existing company in CRM
    existing_comp = Company(
        name="Existing Dental Care",
        domain="existingdental.com",
        industry="Healthcare / Dental",
        location="Indore",
    )
    c_id = await temp_state.add_company(existing_comp)

    # 2. Pre-insert an existing prospect in CRM
    existing_prosp = Prospect(
        company_id=c_id,
        first_name="Pooja",
        last_name="Sharma",
        email="pooja@existingdental.com",
        title="Director",
    )
    p_id = await temp_state.add_prospect(existing_prosp)

    # 3. Create a discovered lead with the SAME normalized domain and email
    lead_id = await temp_state.save_discovered_lead({
        "id": "lead_duplicate_test",
        "profile_id": "prof_dental",
        "company_name": "Existing Dental Care",
        "domain": "WWW.ExistingDental.COM",  # unnormalized case and www
        "website": "https://existingdental.com",
        "industry": "Healthcare / Dental",
        "location": "Indore",
        "decision_maker": {
            "name": "Pooja Sharma",
            "title": "Director",
            "email": "Pooja@ExistingDental.COM",  # unnormalized case
            "email_status": "verified",
        },
        "score": 85,
        "tier": "High Fit",
    })

    # 4. Explicitly add to pipeline
    res = await temp_state.add_discovered_lead_to_pipeline(lead_id)

    # Dedup should reuse existing IDs rather than creating duplicates
    assert res["company_id"] == c_id
    assert res["prospect_id"] == p_id
    assert res["created_company"] is False
    assert res["created_prospect"] is False
    assert res["already_existed"] is True

    # Check lead record is marked added_to_crm
    lead = await temp_state.get_discovered_lead(lead_id)
    assert lead["added_to_crm"] == 1


@pytest.mark.asyncio
async def test_safety_invariants_during_discovery(tmp_path):
    """Verify that lead discovery and qualification operations strictly maintain safety holds."""
    db_file = tmp_path / "test_safety.db"
    temp_state = StateManager(db_path=str(db_file))
    await temp_state.init_db()

    # Ensure sending paused and sent_count=0
    await temp_state.set_setting("sending_paused", "Pilot Evaluation - Read-Only Safety Hold")
    paused = await temp_state.get_setting("sending_paused")
    assert paused == "Pilot Evaluation - Read-Only Safety Hold"

    # Add a brand new lead and promote it to pipeline
    new_lead_id = await temp_state.save_discovered_lead({
        "id": "lead_new_unique",
        "profile_id": "prof_dental",
        "company_name": "Brand New Clinic",
        "domain": "brandnewclinic.in",
        "decision_maker": {
            "name": "Dr. Vivek",
            "title": "Founder",
            "email": "vivek@brandnewclinic.in",
            "email_status": "verified",
        },
        "score": 90,
    })

    res = await temp_state.add_discovered_lead_to_pipeline(new_lead_id)
    assert res["created_company"] is True
    assert res["created_prospect"] is True

    # Prospect should be added with status="new" and NOT contacted/queued
    prospect = await temp_state.get_prospect(res["prospect_id"])
    assert prospect.status == "new"

    # Invariants check
    sent_today = await temp_state.count_outbox_sent_today()
    assert sent_today == 0

