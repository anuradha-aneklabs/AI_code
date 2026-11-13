"""Unit and API integration tests for BANT Lead Qualification & Manual Override."""

import pytest
from starlette.testclient import TestClient

from harvey.qualification import (
    LeadQualifier,
    ResearchProfile,
    SampleLeadDiscoveryProvider,
)
from harvey.dashboard import app, token_store


@pytest.fixture
def client():
    token = token_store.get()
    tc = TestClient(app, base_url="http://127.0.0.1:5555")
    tc.headers.update({"Authorization": f"Bearer {token}"})
    return tc


class TestBantEvaluation:
    def test_bant_breakdown_structure(self):
        qualifier = LeadQualifier()
        research = ResearchProfile(
            company_name="Apex Care Center",
            domain="apexcare.example.com",
            industry="Dentist",
            location="Indore, Madhya Pradesh, India",
            company_size="15-30 employees",
            decision_maker={
                "name": "Dr. Amit Sharma",
                "title": "Clinical Director & Founder",
                "email": "amit@apexcare.example.com",
                "email_status": "verified",
            },
            potential_needs=["Automated appointment triage", "Reducing patient no-shows"],
        )
        profile = {"industry": "Dentist", "geography": "Indore", "company_size": "10-200"}
        result = qualifier.qualify(research, profile)

        assert "bant_breakdown" in result.to_dict()
        bant = result.bant_breakdown
        assert bant["budget"]["status"] == "adequate"
        assert bant["budget"]["score"] == 25
        assert bant["authority"]["status"] == "decision_maker"
        assert bant["authority"]["score"] == 25
        assert bant["need"]["status"] == "high"
        assert bant["need"]["score"] == 25
        assert bant["timeline"]["status"] == "active"
        assert bant["timeline"]["score"] == 25
        assert bant["bant_total"] == 100
        assert bant["bant_qualification"] == "Qualified"

    def test_bant_constrained_budget_and_influencer(self):
        qualifier = LeadQualifier()
        research = ResearchProfile(
            company_name="Solo Dental Practice",
            domain="solodental.example.com",
            industry="Dentist",
            location="Indore, India",
            company_size="1-2 employees",
            decision_maker={
                "name": "Priya Singh",
                "title": "Front Desk Receptionist",
                "email": "reception@solodental.example.com",
                "email_status": "risky",
            },
            potential_needs=[],
        )
        profile = {"industry": "Dentist", "geography": "Indore"}
        result = qualifier.qualify(research, profile)

        bant = result.bant_breakdown
        assert bant["budget"]["status"] == "constrained"
        assert bant["authority"]["status"] == "influencer"
        assert bant["need"]["status"] == "moderate"
        assert bant["timeline"]["status"] == "planned"
        assert bant["bant_total"] < 70
        assert bant["bant_qualification"] in ("Moderate", "Disqualified")


class TestLeadQualificationOverrideApi:
    @pytest.mark.asyncio
    async def test_qualify_and_disqualify_candidate_lead(self, client):
        # 1. First trigger sample discovery to have candidate leads in DB
        resp_disc = client.post("/api/discovery/sample-discover", json={"industry": "Dentist", "limit": 2})
        assert resp_disc.status_code == 200
        leads = resp_disc.json().get("leads", [])
        assert len(leads) > 0

        target_lead = leads[0]
        lead_id = target_lead["id"]

        # 2. Test manual disqualification
        resp_disq = client.post(
            f"/api/discovery/candidates/{lead_id}/disqualify",
            json={"reason": "Practice is closing next month / out of market"}
        )
        assert resp_disq.status_code == 200
        data_disq = resp_disq.json()
        assert data_disq["success"] is True
        assert data_disq["is_qualified"] is False

        # Verify state in DB
        from harvey.dashboard import _state
        state = _state()
        await state.init_db()
        lead_record = await state.get_discovered_lead(lead_id)
        assert lead_record["is_qualified"] == 0
        assert lead_record["candidate_status"] == "rejected"
        assert "closing next month" in lead_record["rejection_reason"]

        # 3. Test manual qualification override
        resp_qual = client.post(
            f"/api/discovery/candidates/{lead_id}/qualify",
            json={"reason": "Verified direct owner authority via phone consultation"}
        )
        assert resp_qual.status_code == 200
        data_qual = resp_qual.json()
        assert data_qual["success"] is True
        assert data_qual["is_qualified"] is True

        lead_record_qual = await state.get_discovered_lead(lead_id)
        assert lead_record_qual["is_qualified"] == 1
        assert lead_record_qual["candidate_status"] == "approved"
        assert lead_record_qual["tier"] == "High Fit"

    def test_disqualify_missing_reason_rejected(self, client):
        resp = client.post("/api/discovery/candidates/fake-lead-id/disqualify", json={"reason": ""})
        assert resp.status_code == 400
        assert "reason is required" in resp.json().get("error", "").lower()
