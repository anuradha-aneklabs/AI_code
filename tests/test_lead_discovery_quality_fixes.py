"""Regression tests for Lead Discovery Data Quality Fixes.

Covers the 4 verified cases:
1. Geography Scoring: Indore (+20 pts), Bhopal (+8 pts), Pune (+0 pts) for Indore, MP target.
2. Duplicate Detection: Discovery query run twice does not duplicate domains in result set;
   existing CRM companies are not imported again.
3. Confidence & Evidence: ICP fit score and data confidence evaluated separately;
   guessed, risky, and missing contacts penalized and disqualified from outreach.
4. Source Provenance: Verifiable source URLs for company name, location, contact,
   employee count, and pain points; unverified facts explicitly marked Unknown / Unverified.
"""

import pytest
from harvey.state import StateManager
from harvey.qualification import (
    LeadQualifier,
    ResearchProfile,
    SampleLeadDiscoveryProvider,
    check_discovered_lead_duplicates,
)
from harvey.models.company import Company
from harvey.models.prospect import Prospect


# ─────────────────────────────────────────────────────────────────────────────
# CASE 1: GEOGRAPHY SCORING (Indore, Bhopal, Pune)
# ─────────────────────────────────────────────────────────────────────────────

def test_geography_scoring_indore_bhopal_pune():
    """Verify exact points awarded for target 'Indore, Madhya Pradesh, India'.

    - Indore: +20 pts (Target geography match within primary city)
    - Bhopal: +8 pts (Regional proximity in target state Madhya Pradesh)
    - Pune: +0 pts (Geography mismatch in Maharashtra; country 'India' never triggers regional proximity)
    """
    qualifier = LeadQualifier()
    profile = {
        "industry": "Healthcare / Dental",
        "geography": "Indore, Madhya Pradesh, India",
        "company_size": "15-30 employees",
        "icp_titles": ["Principal Dentist", "Director"],
    }

    # 1. Lead located in Indore
    lead_indore = ResearchProfile(
        company_name="Indore Smile Care",
        domain="indoresmile.com",
        industry="Healthcare / Dental",
        location="Indore, Madhya Pradesh, India",
        company_size="15-30 employees",
        decision_maker={"name": "Dr. Amit Rawat", "title": "Principal Dentist", "email": "amit@indoresmile.com", "email_status": "verified"},
        potential_needs=["Appointment booking automation"],
    )
    res_indore = qualifier.qualify(lead_indore, profile)
    geo_indore_reasons = [r for r in res_indore.reasons if "geography" in r.lower() or "proximity" in r.lower()]
    assert any("+20 pts" in r for r in geo_indore_reasons), f"Expected +20 pts for Indore, got: {geo_indore_reasons}"
    assert any("within primary target 'indore'" in r.lower() for r in geo_indore_reasons)
    assert res_indore.score == 100

    # 2. Lead located in Bhopal (regional state match)
    lead_bhopal = ResearchProfile(
        company_name="Central MP Dental",
        domain="centralmpdental.com",
        industry="Healthcare / Dental",
        location="Bhopal, Madhya Pradesh, India",
        company_size="15-30 employees",
        decision_maker={"name": "Dr. Vikas Sen", "title": "Principal Dentist", "email": "vikas@centralmpdental.com", "email_status": "verified"},
        potential_needs=["Appointment booking automation"],
    )
    res_bhopal = qualifier.qualify(lead_bhopal, profile)
    geo_bhopal_reasons = [r for r in res_bhopal.reasons if "geography" in r.lower() or "proximity" in r.lower()]
    assert any("+8 pts" in r for r in geo_bhopal_reasons), f"Expected +8 pts for Bhopal, got: {geo_bhopal_reasons}"
    assert any("regional proximity" in r.lower() for r in geo_bhopal_reasons)
    assert any("madhya pradesh" in r.lower() for r in geo_bhopal_reasons)
    assert res_bhopal.score == 88  # 100 - 12 (20 - 8)

    # 3. Lead located in Pune (outside Madhya Pradesh)
    lead_pune = ResearchProfile(
        company_name="Pune Dental Studio",
        domain="punedental.com",
        industry="Healthcare / Dental",
        location="Pune, Maharashtra, India",
        company_size="15-30 employees",
        decision_maker={"name": "Dr. Ramesh Joshi", "title": "Principal Dentist", "email": "ramesh@punedental.com", "email_status": "verified"},
        potential_needs=["Appointment booking automation"],
    )
    res_pune = qualifier.qualify(lead_pune, profile)
    geo_pune_reasons = [r for r in res_pune.reasons if "geography" in r.lower() or "proximity" in r.lower()]
    assert any("+0 pts" in r for r in geo_pune_reasons), f"Expected +0 pts for Pune, got: {geo_pune_reasons}"
    assert any("geography mismatch" in r.lower() for r in geo_pune_reasons)
    assert not any("regional proximity" in r.lower() for r in geo_pune_reasons), "Pune must never receive regional proximity points!"
    assert res_pune.score == 80  # 100 - 20 (0 geo pts)


# ─────────────────────────────────────────────────────────────────────────────
# CASE 2: DUPLICATE DETECTION (Repeated Queries & CRM Re-import Prevention)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_duplicate_detection_prevents_multiple_domain_rows(tmp_path):
    """Running the discovery query twice must not store or return duplicate domains."""
    db_file = tmp_path / "test_dup_query.db"
    state = StateManager(db_path=str(db_file))
    await state.init_db()

    profile = {
        "id": "prof_dental_dedup",
        "industry": "Healthcare / Dental",
        "geography": "Indore, Madhya Pradesh, India",
        "company_size": "15-30 employees",
        "icp_titles": ["Principal Dentist", "Director"],
    }
    provider = SampleLeadDiscoveryProvider()
    qualifier = LeadQualifier()

    # Run query first time
    batch1 = await provider.discover(profile, limit=4)
    for cand in batch1:
        qual = qualifier.qualify(cand, profile)
        d = cand.to_dict()
        d.update({
            "profile_id": profile["id"],
            "score": qual.score,
            "tier": qual.tier,
            "reasons": qual.reasons,
            "confidence": qual.confidence,
            "is_qualified": qual.is_qualified,
        })
        await state.save_discovered_lead(d)

    # Run exact same query second time
    batch2 = await provider.discover(profile, limit=4)
    for cand in batch2:
        qual = qualifier.qualify(cand, profile)
        d = cand.to_dict()
        d.update({
            "profile_id": profile["id"],
            "score": qual.score,
            "tier": qual.tier,
            "reasons": qual.reasons,
            "confidence": qual.confidence,
            "is_qualified": qual.is_qualified,
        })
        await state.save_discovered_lead(d)

    # Verify results in database
    retrieved = await state.get_discovered_leads(profile["id"])
    domains = [l["domain"] for l in retrieved]

    assert len(retrieved) == 4, f"Expected exactly 4 leads without duplicates, got {len(retrieved)}"
    assert len(domains) == len(set(domains)), f"Duplicate domains detected: {domains}"
    assert len(set(domains)) == 4


@pytest.mark.asyncio
async def test_existing_crm_companies_not_reimported(tmp_path):
    """An existing CRM company must not be re-imported or duplicated."""
    db_file = tmp_path / "test_crm_no_reimport.db"
    state = StateManager(db_path=str(db_file))
    await state.init_db()

    # 1. Pre-insert company in CRM
    comp = Company(
        id="comp_indore_01",
        name="Indore Advanced Dental Care",
        domain="indoreadvanceddental.com",
        industry="Healthcare / Dental",
        location="Indore, Madhya Pradesh, India",
    )
    c_id = await state.add_company(comp)

    # 2. Add discovered lead for this exact domain
    lead_id = await state.save_discovered_lead({
        "profile_id": "prof_dental",
        "company_name": "Indore Advanced Dental Care",
        "domain": "indoreadvanceddental.com",
        "website": "https://indoreadvanceddental.com",
        "industry": "Healthcare / Dental",
        "location": "Indore, Madhya Pradesh, India",
        "decision_maker": {
            "name": "Dr. Amit Rawat",
            "title": "Principal Dentist & Director",
            "email": "amit@indoreadvanceddental.com",
            "email_status": "verified",
        },
        "score": 100,
        "tier": "High Fit",
    })

    # 3. Attempt import into CRM
    import_result = await state.import_discovered_lead_to_crm(lead_id, target_stage="New")

    # Assert existing company was reused and not created anew
    assert import_result["created_company"] is False, "Existing CRM company should not be recreated"
    assert import_result["company_id"] == c_id, "Import must reuse the pre-existing company ID"

    # Assert exactly 1 company row exists in the database
    async with state._connect() as db:
        async with db.execute("SELECT COUNT(*) FROM companies WHERE LOWER(domain) = ?", ("indoreadvanceddental.com",)) as cursor:
            row = await cursor.fetchone()
            assert row[0] == 1, "Duplicate company row created in CRM database!"


# ─────────────────────────────────────────────────────────────────────────────
# CASE 3: CONFIDENCE & EVIDENCE (Separation, Penalties, and Qualification Gate)
# ─────────────────────────────────────────────────────────────────────────────

def test_confidence_and_evidence_separation_and_explanations():
    """Verify ICP fit score and data confidence are calculated separately with explicit explanations.

    - Verified Email: 100 ICP, 1.00 Conf (100%), is_qualified = True
    - Guessed Email: 100 ICP, 0.85 Conf (85%), is_qualified = False (unconfirmed email penalty)
    - Risky Email: 100 ICP, 0.90 Conf (90%), is_qualified = False (risky email penalty)
    - No Contact: 75 ICP, 0.60 Conf (60%), is_qualified = False (missing DM penalty)
    """
    qualifier = LeadQualifier()
    profile = {
        "industry": "Healthcare / Dental",
        "geography": "Indore, Madhya Pradesh, India",
        "company_size": "15-30 employees",
        "icp_titles": ["Principal Dentist", "Director"],
    }

    base_args = {
        "company_name": "Test Dental Care",
        "domain": "testdental.com",
        "industry": "Healthcare / Dental",
        "location": "Indore, Madhya Pradesh, India",
        "company_size": "15-30 employees",
        "potential_needs": ["Appointment booking automation"],
    }

    # 1. Verified Email
    res_verified = qualifier.qualify(
        ResearchProfile(**base_args, decision_maker={"name": "Dr. Amit Rawat", "title": "Principal Dentist", "email": "amit@testdental.com", "email_status": "verified"}),
        profile,
    )
    assert res_verified.score == 100
    assert res_verified.confidence == 1.00
    assert res_verified.is_qualified is True
    assert any("verified email available" in r.lower() for r in res_verified.reasons)

    # 2. Guessed Email
    res_guess = qualifier.qualify(
        ResearchProfile(**base_args, decision_maker={"name": "Dr. Amit Rawat", "title": "Principal Dentist", "email": "amit@testdental.com", "email_status": "guess"}),
        profile,
    )
    assert res_guess.score == 100, "ICP fit score must reflect criteria fit regardless of email status"
    assert res_guess.confidence == 0.85, f"Expected 0.85 confidence (15% penalty for guess), got {res_guess.confidence}"
    assert res_guess.is_qualified is False, "Leads with guessed emails must NOT be marked qualified for outreach"
    assert any("unconfirmed guess" in m.lower() for m in res_guess.missing_information)
    assert res_guess.confidence_breakdown["email"] == 0.05

    # 3. Risky Email
    res_risky = qualifier.qualify(
        ResearchProfile(**base_args, decision_maker={"name": "Dr. Amit Rawat", "title": "Principal Dentist", "email": "amit@testdental.com", "email_status": "risky"}),
        profile,
    )
    assert res_risky.score == 100
    assert res_risky.confidence == 0.90, f"Expected 0.90 confidence (10% penalty for risky), got {res_risky.confidence}"
    assert res_risky.is_qualified is False, "Leads with risky emails must NOT be marked qualified for outreach"
    assert any("risky" in m.lower() for m in res_risky.missing_information)
    assert res_risky.confidence_breakdown["email"] == 0.10

    # 4. No Verified Contact (Generic helpdesk / no named executive)
    res_no_contact = qualifier.qualify(
        ResearchProfile(**base_args, decision_maker={}),
        profile,
    )
    assert res_no_contact.score == 75  # 100 - 25 for DM title
    assert res_no_contact.confidence == 0.60  # Domain (0.20) + Location (0.20) + Size (0.10) + Needs (0.10)
    assert res_no_contact.is_qualified is False
    assert any("no named decision maker" in m.lower() for m in res_no_contact.missing_information)
    assert any("direct decision maker email missing" in m.lower() for m in res_no_contact.missing_information)


# ─────────────────────────────────────────────────────────────────────────────
# CASE 4: SOURCE PROVENANCE (Traceable Evidence for Facts, Unknown for Gaps)
# ─────────────────────────────────────────────────────────────────────────────

def test_source_provenance_facts_and_unverified_flagging():
    """Verify evidence/source URLs are provided for key facts, and unverified facts are marked Unknown."""
    provider = SampleLeadDiscoveryProvider()
    leads = provider._dental_leads({})

    # 1. Lead with full verified evidence: Indore Advanced Dental Care
    lead1 = leads[0]
    prov1 = lead1.get_source_provenance()

    for fact_key in ("company_name", "location", "contact_name", "employee_count", "pain_points"):
        fact = prov1[fact_key]
        assert fact["verified"] is True, f"Fact '{fact_key}' should be verified"
        assert fact["status"] == "Verified"
        assert fact["source_url"].startswith("http"), f"Fact '{fact_key}' must have a valid source URL"
        assert fact["source_title"] != ""

    assert prov1["company_name"]["source_title"] == "Ministry of Corporate Affairs (MCA)"
    assert prov1["location"]["source_title"] == "Indore Municipal Corporation Health Register"
    assert "smartcityindore.org" in prov1["location"]["source_url"]
    assert "our-doctors" in prov1["contact_name"]["source_url"]

    # 2. Lead with unverified employee count: Central India Maxillofacial & Dental
    lead3 = leads[2]
    prov3 = lead3.get_source_provenance()
    assert prov3["employee_count"]["verified"] is False
    assert prov3["employee_count"]["status"] == "Unknown"
    assert prov3["employee_count"]["source_url"] == ""

    # 3. Lead with missing attributes
    sparse_lead = ResearchProfile(
        company_name="Indore Dental Hub",
        domain="indoredentalhub.in",
        industry="Healthcare / Dental",
        location="",  # missing
        company_size="",  # missing
        decision_maker={},  # missing
        potential_needs=[],  # missing
        sources=[
            {"field": "company_name", "source_title": "MCA Portal", "source_url": "https://mca.gov.in/indoredentalhub", "verified": True}
        ],
        missing_fields=["location", "employee_count is unverified"],
    )
    prov_sparse = sparse_lead.get_source_provenance()

    assert prov_sparse["company_name"]["verified"] is True
    assert prov_sparse["company_name"]["status"] == "Verified"

    assert prov_sparse["location"]["verified"] is False
    assert prov_sparse["location"]["status"] == "Unverified"
    assert prov_sparse["location"]["value"] == "Unknown"

    assert prov_sparse["contact_name"]["verified"] is False
    assert prov_sparse["contact_name"]["status"] == "Unknown"
    assert prov_sparse["contact_name"]["value"] == "Unknown"

    assert prov_sparse["employee_count"]["verified"] is False
    assert prov_sparse["employee_count"]["status"] == "Unknown"
    assert prov_sparse["employee_count"]["value"] == "Unknown"

    assert prov_sparse["pain_points"]["verified"] is False
    assert prov_sparse["pain_points"]["status"] == "Unknown"
    assert prov_sparse["pain_points"]["value"] == "Unknown"

