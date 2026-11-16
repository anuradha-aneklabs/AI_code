"""Automated Regression & Verification Tests for Discover Module Audit & Fixes.

Covers:
1. Geography matching under city vs All India vs Worldwide scope.
2. Industry & ICP qualification with sector divergence and company-size mismatch.
3. Decision-maker verification preventing role contacts & general inquiries from qualifying.
4. Authoritative BANT scoring invariants (sum == bant_total, 0 for missing evidence).
5. CRM duplicate detection and tab counter consistency.
6. 8 Discovery providers integrity in collectors/discover.py.
7. Website research run persistence and evidence tracking.
"""

import pytest
import sqlite3
from harvey.qualification import (
    ResearchProfile,
    LeadQualifier,
    compute_authoritative_bant,
    is_role_contact,
    is_role_mailbox,
    SampleLeadDiscoveryProvider,
    check_discovered_lead_duplicates,
)
from harvey.collectors.discover import (
    OpenStreetMap,
    TinyFish,
    DataForSEOListings,
    DataForSEOSerp,
    DataForSEOMaps,
    Serper,
    ScavioMaps,
    ScavioSearch,
    DiscoveryQuery,
)


@pytest.fixture
def indore_profile():
    return {
        "id": "prof_dental_in",
        "name": "Dental Clinics & Practices (India)",
        "industry": "Healthcare / Dental",
        "geography": "Indore, Madhya Pradesh, India",
        "geography_scope": "cities",
        "company_size": "10-200 employees",
        "icp_titles": ["Owner", "Dentist", "Dental Surgeon", "Founder", "Director", "Practice Manager"],
    }


@pytest.fixture
def all_india_profile():
    return {
        "id": "prof_dental_in",
        "name": "Dental Clinics & Practices (India)",
        "industry": "Healthcare / Dental",
        "geography": "Indore, Madhya Pradesh, India",
        "geography_scope": "all_india",
        "company_size": "10-200 employees",
        "icp_titles": ["Owner", "Dentist", "Dental Surgeon", "Founder", "Director", "Practice Manager"],
    }


@pytest.fixture
def worldwide_profile():
    return {
        "id": "prof_saas_global",
        "name": "B2B SaaS & High-Growth Tech",
        "industry": "Software / B2B SaaS",
        "geography": "Worldwide",
        "geography_scope": "worldwide",
        "company_size": "20-500 employees",
        "icp_titles": ["VP Sales", "Founder", "CEO"],
    }


class TestGeographyMatching:
    def test_indore_cities_scope_exact_city(self, indore_profile):
        qualifier = LeadQualifier()
        lead = ResearchProfile(
            company_name="Indore Dental Clinic",
            domain="indoredental.com",
            industry="Healthcare / Dental",
            location="Indore, Madhya Pradesh, India",
        )
        res = qualifier.qualify(lead, indore_profile)
        # Primary city match gives +20 pts
        assert any("within primary target 'Indore'" in r for r in res.reasons)
        assert any("+20 pts" in r for r in res.reasons if "geography match" in r.lower())

    def test_indore_cities_scope_regional_proximity(self, indore_profile):
        qualifier = LeadQualifier()
        lead = ResearchProfile(
            company_name="Bhopal Maxillo Clinic",
            domain="bhopaldental.com",
            industry="Healthcare / Dental",
            location="Bhopal, Madhya Pradesh, India",
        )
        res = qualifier.qualify(lead, indore_profile)
        # Regional state proximity gives +8 pts
        assert any("Regional proximity" in r and "+8 pts" in r for r in res.reasons)

    def test_indore_cities_scope_unrelated_city_zero_points(self, indore_profile):
        qualifier = LeadQualifier()
        pune_lead = ResearchProfile(
            company_name="Metro Ortho Care Pune",
            domain="metroorthopune.in",
            industry="Healthcare / Dental",
            location="Pune, Maharashtra, India",
        )
        res_pune = qualifier.qualify(pune_lead, indore_profile)
        assert any("Geography mismatch" in r and "+0 pts" in r for r in res_pune.reasons)

        delhi_lead = ResearchProfile(
            company_name="Apex Global Hospital Network",
            domain="apexglobalhospitals.org",
            industry="Healthcare / Dental",
            location="New Delhi, India",
        )
        res_delhi = qualifier.qualify(delhi_lead, indore_profile)
        assert any("Geography mismatch" in r and "+0 pts" in r for r in res_delhi.reasons)

    def test_all_india_scope_gives_equal_eligibility(self, all_india_profile):
        qualifier = LeadQualifier()
        for loc in ["Indore, Madhya Pradesh, India", "Pune, Maharashtra, India", "New Delhi, India", "Bengaluru, Karnataka, India"]:
            lead = ResearchProfile(
                company_name="Test Clinic",
                domain="testclinic.in",
                industry="Healthcare / Dental",
                location=loc,
            )
            res = qualifier.qualify(lead, all_india_profile)
            assert any("All-India geography match" in r and "+20 pts" in r for r in res.reasons), f"Failed for {loc}"

        # Foreign location gets 0 under all_india
        foreign_lead = ResearchProfile(
            company_name="Harley Street Smile Studio",
            domain="harleystreet.co.uk",
            industry="Healthcare / Dental",
            location="London, Greater London, United Kingdom",
        )
        res_foreign = qualifier.qualify(foreign_lead, all_india_profile)
        assert any("Geography mismatch" in r and "+0 pts" in r for r in res_foreign.reasons)

    def test_worldwide_scope(self, worldwide_profile):
        qualifier = LeadQualifier()
        for loc in ["San Francisco, CA, USA", "London, UK", "Indore, India", "Tokyo, Japan"]:
            lead = ResearchProfile(
                company_name="Global Tech",
                domain="globaltech.io",
                industry="Software / B2B SaaS",
                location=loc,
            )
            res = qualifier.qualify(lead, worldwide_profile)
            assert any("Worldwide geography match" in r and "+20 pts" in r for r in res.reasons)


class TestIndustryAndICPQualification:
    def test_sector_divergence_gives_zero_points(self, indore_profile):
        qualifier = LeadQualifier()
        lead = ResearchProfile(
            company_name="Apex Global Hospital Network",
            domain="apexglobalhospitals.org",
            industry="Tertiary Hospital Care",
            location="New Delhi, India",
            company_size="4500+ employees",
        )
        res = qualifier.qualify(lead, indore_profile)
        assert any("Sector divergence" in r and "+0 pts" in r for r in res.reasons)
        assert res.bant_breakdown["need"]["score"] == 0
        assert res.bant_breakdown["need"]["status"] == "divergent"

    def test_company_size_mismatch_enterprise_zero_points(self, indore_profile):
        qualifier = LeadQualifier()
        lead = ResearchProfile(
            company_name="Mega Enterprise Healthcare",
            domain="megahealth.com",
            industry="Healthcare / Dental",
            location="Indore, Madhya Pradesh, India",
            company_size="5000 employees",
        )
        res = qualifier.qualify(lead, indore_profile)
        assert any("exceeds target" in r and "+0 pts" in r for r in res.reasons)
        assert res.bant_breakdown["budget"]["score"] == 0
        assert res.bant_breakdown["budget"]["status"] == "mismatch"

    def test_missing_evidence_receives_zero_points(self, indore_profile):
        qualifier = LeadQualifier()
        lead = ResearchProfile(
            company_name="Empty Profile Clinic",
            domain="emptyclinic.com",
            industry="Healthcare / Dental",
            location="Indore, Madhya Pradesh, India",
            company_size="unknown",
            potential_needs=[],
            decision_maker={},
        )
        res = qualifier.qualify(lead, indore_profile)
        bant = res.bant_breakdown
        assert bant["budget"]["score"] == 0
        assert bant["authority"]["score"] == 0
        assert bant["timeline"]["score"] == 0


class TestDecisionMakerAndRoleContacts:
    def test_is_role_contact_detection(self):
        assert is_role_contact("Hospital Administration", "General Inquiries") is True
        assert is_role_contact("Front Desk", "Reception") is True
        assert is_role_contact("Customer Support Team", "Support") is True
        assert is_role_contact("Dr. Amit Rawat", "Principal Dentist & Director") is False
        assert is_role_contact("Pooja Sharma", "Practice Manager") is False

    def test_is_role_mailbox_detection(self):
        assert is_role_mailbox("info@apexglobalhospitals.org") is True
        assert is_role_mailbox("contact@clinic.in") is True
        assert is_role_mailbox("admin@domain.com") is True
        assert is_role_mailbox("amit@indoreadvanceddental.com") is False
        assert is_role_mailbox("dr.neha@apexsmiledental.in") is False

    def test_role_contact_scores_zero_authority_and_timeline(self, indore_profile):
        qualifier = LeadQualifier()
        lead = ResearchProfile(
            company_name="Apex Global Hospital Network",
            domain="apexglobalhospitals.org",
            industry="Tertiary Hospital Care",
            location="New Delhi, India",
            company_size="4500+ employees",
            decision_maker={
                "name": "Hospital Administration",
                "title": "General Inquiries",
                "email": "info@apexglobalhospitals.org",
                "email_status": "verified",
            },
        )
        res = qualifier.qualify(lead, indore_profile)
        bant = res.bant_breakdown
        assert bant["authority"]["score"] == 0
        assert bant["timeline"]["score"] == 0
        assert bant["bant_total"] == 0
        assert res.is_qualified is False


class TestAuthoritativeBANTIntegrity:
    def test_bant_total_strictly_equals_sum_of_pillars(self, indore_profile):
        provider = SampleLeadDiscoveryProvider()
        leads = provider._dental_leads(indore_profile)
        for lead in leads:
            bant = compute_authoritative_bant(lead, indore_profile)
            pillar_sum = (
                bant["budget"]["score"]
                + bant["authority"]["score"]
                + bant["need"]["score"]
                + bant["timeline"]["score"]
            )
            assert bant["bant_total"] == pillar_sum, f"Failed for {lead.company_name}"
            for p in ["budget", "authority", "need", "timeline"]:
                assert 0 <= bant[p]["score"] <= 25, f"Pillar {p} out of range in {lead.company_name}"


class TestDiscoveryProvidersAudit:
    def test_all_eight_providers_instantiate_and_estimate(self):
        providers = [
            DataForSEOListings(),
            DataForSEOSerp(),
            DataForSEOMaps(),
            Serper(),
            TinyFish(),
            OpenStreetMap(),
            ScavioMaps(),
            ScavioSearch(),
        ]
        assert len(providers) == 8
        queries = [DiscoveryQuery(term="dentist indore", location="Indore", limit=10)]
        for p in providers:
            est = p.estimate(queries)
            assert isinstance(est, (int, float))
            assert est >= 0.0


class TestWebsiteResearchRunPersistence:
    def test_run_9a6af942eca8_persisted_41_observations(self):
        conn = sqlite3.connect("data/harvey.db")
        c = conn.cursor()
        c.execute("SELECT id, stage, status, records, cost_usd FROM runs WHERE id = '9a6af942eca8'")
        run = c.fetchone()
        assert run is not None
        assert run[1] == "profile"
        assert run[2] == "completed"
        assert run[3] == 41
        assert run[4] == 0.0

        c.execute("SELECT count(*) FROM observations WHERE run_id = '9a6af942eca8'")
        obs_count = c.fetchone()[0]
        assert obs_count == 41

        # Check evidence URLs exist on all observations
        c.execute("SELECT count(*) FROM observations WHERE run_id = '9a6af942eca8' AND evidence_url IS NOT NULL AND evidence_url != ''")
        with_evidence = c.fetchone()[0]
        assert with_evidence == 41


class TestSafetyInvariants:
    def test_live_email_sending_strictly_disabled(self):
        conn = sqlite3.connect("data/harvey.db")
        c = conn.cursor()
        c.execute("SELECT value FROM settings WHERE key = 'sending_paused'")
        row = c.fetchone()
        assert row is not None
        assert "Safety Hold" in row[0]

        c.execute("SELECT count(*) FROM outbox WHERE status = 'sent'")
        sent_count = c.fetchone()[0]
        # Invariant: Autonomous sending disabled; at most 1 explicit operator-authorized test send
        assert sent_count <= 1

    def test_four_original_pilot_draft_statuses_preserved(self):
        conn = sqlite3.connect("data/harvey.db")
        c = conn.cursor()
        expected = {
            "a02fed19ec8d": "pending_review",
            "36dfa015198c": "cancelled",
            "3be7a27f3620": "cancelled",
            "fad4e4984339": "pending_review",
        }
        for did, exp_status in expected.items():
            c.execute("SELECT status FROM outbox WHERE id = ?", (did,))
            row = c.fetchone()
            assert row is not None, f"Draft {did} not found"
            assert row[0] == exp_status, f"Draft {did} status is {row[0]}, expected {exp_status}"


class TestCRMDuplicateCountersAndFilter:
    @pytest.mark.asyncio
    async def test_crm_duplicate_detection_and_counter_filtering(self, tmp_path):
        from harvey.state import StateManager
        from harvey.models.company import Company
        db_path = str(tmp_path / "test_crm.db")
        state = StateManager(db_path)
        await state.init_db()

        # Add existing company to CRM
        comp = Company(
            name="Indore Advanced Dental Care",
            domain="indoreadvanceddental.com",
            website="https://indoreadvanceddental.com",
        )
        await state.add_company(comp)

        raw_leads = [
            {
                "id": "lead_1",
                "company_name": "Indore Advanced Dental Care",
                "domain": "indoreadvanceddental.com",
                "candidate_status": "pending",
                "added_to_crm": 0,
            },
            {
                "id": "lead_2",
                "company_name": "Apex Smile Speciality Center",
                "domain": "apexsmiledental.in",
                "candidate_status": "pending",
                "added_to_crm": 0,
            },
            {
                "id": "lead_3",
                "company_name": "Unrelated Clinic",
                "domain": "unrelatedclinic.in",
                "candidate_status": "rejected",
                "added_to_crm": 0,
            },
        ]

        checked_leads = await check_discovered_lead_duplicates(raw_leads, state)
        assert checked_leads[0]["in_crm"] is True
        assert checked_leads[0]["is_duplicate"] is True
        assert checked_leads[1]["in_crm"] is False
        assert checked_leads[2]["in_crm"] is False

        # Simulate get_discovered_leads counter and filtering logic
        total_count = len(checked_leads)
        in_crm_count = sum(1 for l in checked_leads if l.get("in_crm") or l.get("added_to_crm") or l.get("candidate_status") == "approved")
        rejected_count = sum(1 for l in checked_leads if l.get("candidate_status") in ("rejected", "skipped"))
        pending_count = sum(1 for l in checked_leads if not (l.get("in_crm") or l.get("added_to_crm") or l.get("candidate_status") in ("approved", "rejected", "skipped")))

        assert total_count == 3
        assert in_crm_count == 1
        assert rejected_count == 1
        assert pending_count == 1

        # Pending view must NOT contain lead_1 (already in CRM)
        pending_view = [l for l in checked_leads if not (l.get("in_crm") or l.get("added_to_crm") or l.get("candidate_status") in ("approved", "rejected", "skipped"))]
        assert len(pending_view) == 1
        assert pending_view[0]["id"] == "lead_2"

        # Approved / In CRM view MUST contain lead_1
        approved_view = [l for l in checked_leads if l.get("in_crm") or l.get("added_to_crm") or l.get("candidate_status") == "approved"]
        assert len(approved_view) == 1
        assert approved_view[0]["id"] == "lead_1"
