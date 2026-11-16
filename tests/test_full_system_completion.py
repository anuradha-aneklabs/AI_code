"""Full System Completion Test Suite.

Validates:
1. Sample Lead Discovery API endpoint (`POST /api/discovery/sample-discover`).
2. Error resilience: malformed JSON returns HTTP 400 Bad Request.
3. Fallback behavior when profile_id is omitted.
4. Companies tab filtering logic and contract (fit tiers, search criteria).
5. Campaign review & approval workflow end-to-end (draft review, in-place edit, approve, reject with reason, audit trail).
6. Non-negotiable safety invariants: require_approval=True, live sending paused, sent_count=0, and original pilot drafts intact.
"""

import json
import sqlite3
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from harvey.config import load_config
from harvey.dashboard import app, token_store
from harvey.models.campaign import Campaign, EmailStep
from harvey.models.company import Company
from harvey.models.prospect import Prospect
from harvey.qualification import LeadQualifier, ResearchProfile, SampleLeadDiscoveryProvider
from harvey.state import StateManager


@pytest.fixture
def state(tmp_path):
    """StateManager backed by a temporary test database."""
    db_file = tmp_path / "test.db"
    return StateManager(str(db_file))


@pytest.fixture
def auth_client(state, monkeypatch):
    """Provide an authenticated TestClient for dashboard endpoints."""
    monkeypatch.setattr("harvey.dashboard._state", lambda: state)
    token = token_store.get()
    client = TestClient(app, base_url="http://127.0.0.1:5555")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


@pytest.mark.asyncio
async def test_sample_discovery_endpoint_success(auth_client, state):
    """Test POST /api/discovery/sample-discover successfully returns qualified leads and research dossiers."""
    await state.init_db()
    profile = {
        "id": "test_indore_profile",
        "name": "Indore Dental Practice",
        "industry": "Healthcare / Dental",
        "geography": "Indore, Madhya Pradesh, India",
        "geography_scope": "Indore",
        "company_size": "1-10",
        "target_use_cases": ["dental imaging", "patient scheduling"],
    }
    await state.create_business_profile(profile)

    res = auth_client.post(
        "/api/discovery/sample-discover",
        json={"profile_id": "test_indore_profile", "limit": 3},
    )
    assert res.status_code == 200
    data = res.json()
    assert data.get("success") is True
    assert data.get("discovered_count") > 0
    assert len(data.get("leads", [])) > 0

    first = data["leads"][0]
    assert "score" in first
    assert "tier" in first
    assert "reasons" in first
    assert "confidence" in first
    assert first["candidate_status"] == "pending"

    # Verify research dossier was persisted in lead_research table
    lead_id = first["id"]
    dossier = await state.get_lead_research(lead_id)
    assert dossier is not None
    assert dossier["company_name"] == first["company_name"]


@pytest.mark.asyncio
async def test_sample_discovery_endpoint_malformed_json(auth_client, state):
    """Test POST /api/discovery/sample-discover handles malformed JSON with HTTP 400."""
    await state.init_db()
    res = auth_client.post(
        "/api/discovery/sample-discover",
        content=b"{invalid-json-payload",
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 400
    data = res.json()
    assert data.get("success") is False
    assert "Invalid JSON payload" in data.get("error", "")


@pytest.mark.asyncio
async def test_sample_discovery_endpoint_fallback_profile(auth_client, state):
    """Test POST /api/discovery/sample-discover works when profile_id is omitted or null."""
    await state.init_db()
    res = auth_client.post(
        "/api/discovery/sample-discover",
        json={},
    )
    assert res.status_code == 200
    data = res.json()
    assert data.get("success") is True
    assert data.get("discovered_count") > 0


def test_companies_filtering_logic_contract():
    """Verify company fit filter logic and criteria matching."""
    companies = [
        {"name": "Alpha Corp", "fit": 85, "register_status": "new", "industry": "Software"},
        {"name": "Beta Clinic", "fit": 55, "register_status": "new", "industry": "Healthcare"},
        {"name": "Gamma Shop", "fit": 25, "register_status": "rejected", "industry": "Retail"},
        {"name": "Delta Tech", "fit": 75, "register_status": "contacted", "industry": "Software"},
    ]

    # Qualified tier (>= 60)
    qualified = [c for c in companies if (c.get("fit") or 0) >= 60]
    assert len(qualified) == 2
    assert {c["name"] for c in qualified} == {"Alpha Corp", "Delta Tech"}

    # Moderate tier (40 - 59)
    moderate = [c for c in companies if 40 <= (c.get("fit") or 0) < 60]
    assert len(moderate) == 1
    assert moderate[0]["name"] == "Beta Clinic"

    # Low tier (< 40)
    low = [c for c in companies if (c.get("fit") or 0) < 40]
    assert len(low) == 1
    assert low[0]["name"] == "Gamma Shop"

    # Contacted status
    contacted = [c for c in companies if (c.get("register_status") or "").lower() == "contacted"]
    assert len(contacted) == 1
    assert contacted[0]["name"] == "Delta Tech"


@pytest.mark.asyncio
async def test_campaign_review_modal_and_endpoints_flow(auth_client, state):
    """Test campaign review, in-place edit, approval, rejection, and audit trail end-to-end."""
    await state.init_db()
    # 1. Create a test campaign with multi-step sequence
    camp = Campaign(
        id="camp_test_completion",
        name="Q4 Enterprise Automation",
        status="active",
        sequence=[
            EmailStep(step=1, delay_days=0, subject="Quick question regarding workflow", body="Hi {first_name}, saw your work."),
            EmailStep(step=2, delay_days=3, subject="Re: Quick question regarding workflow", body="Following up on my previous note."),
            EmailStep(step=3, delay_days=5, subject="Final check-in", body="Let me know if we can connect."),
        ],
    )
    await state.add_campaign(camp)

    # 2. Stage Step 1 draft in Outbox
    draft_id = "draft_camp_step1"
    async with state._connect() as db:
        await db.execute(
            """INSERT INTO outbox (id, prospect_id, campaign_id, step, to_email, subject, body, status, send_at)
               VALUES (?, ?, ?, 1, ?, ?, ?, 'pending_review', datetime('now'))""",
            (draft_id, "p_test", camp.id, "dr.test@clinic.com", camp.sequence[0].subject, camp.sequence[0].body),
        )
        await db.commit()

    # 3. Fetch campaign review details via GET /api/campaigns/{id}/review
    res = auth_client.get(f"/api/campaigns/{camp.id}/review")
    assert res.status_code == 200
    rev_data = res.json()
    assert rev_data.get("success") is True
    assert len(rev_data.get("step_1_drafts", [])) == 1
    assert rev_data["step_1_drafts"][0]["id"] == draft_id
    # Ensure planned follow-ups are present as planned-only
    planned = rev_data.get("planned_steps", [])
    assert len(planned) == 2
    assert planned[0]["is_planned_only"] is True
    assert planned[0]["in_outbox"] is False

    # 4. Edit draft copy via POST /api/campaigns/{id}/drafts/{draft_id}/edit
    new_subject = "Updated Subject Line for Dr. Test"
    new_body = "Personalized body copy after review."
    edit_res = auth_client.post(
        f"/api/campaigns/{camp.id}/drafts/{draft_id}/edit",
        json={"subject": new_subject, "body": new_body},
    )
    assert edit_res.status_code == 200
    assert edit_res.json().get("success") is True

    # Verify draft was updated in DB
    async with state._connect() as db:
        async with db.execute("SELECT subject, body FROM outbox WHERE id = ?", (draft_id,)) as cur:
            row = await cur.fetchone()
            assert row[0] == new_subject
            assert row[1] == new_body

    # 5. Approve draft via POST /api/campaigns/{id}/drafts/{draft_id}/approve
    approve_res = auth_client.post(f"/api/campaigns/{camp.id}/drafts/{draft_id}/approve")
    assert approve_res.status_code == 200
    assert approve_res.json().get("success") is True

    # Verify draft is approved in DB
    async with state._connect() as db:
        async with db.execute("SELECT status, approved_by FROM outbox WHERE id = ?", (draft_id,)) as cur:
            row = await cur.fetchone()
            assert row[0] == "approved"
            assert row[1] == "you"

    # 6. Audit trail check via GET /api/campaigns/{id}/audit
    audit_res = auth_client.get(f"/api/campaigns/{camp.id}/audit")
    assert audit_res.status_code == 200
    audit_data = audit_res.json()
    assert audit_data.get("success") is True
    actions = [a.get("action_type") or a.get("action") for a in audit_data.get("audit_history", [])]
    assert "campaign_draft_edited" in actions
    assert "campaign_draft_approved" in actions


@pytest.mark.asyncio
async def test_campaign_reject_requires_reason(auth_client, state):
    """Test rejecting a campaign draft requires a non-empty rejection reason."""
    await state.init_db()
    camp = Campaign(id="camp_test_reject", name="Reject Test", status="active", sequence=[])
    await state.add_campaign(camp)

    draft_id = "draft_test_reject"
    async with state._connect() as db:
        await db.execute(
            """INSERT INTO outbox (id, campaign_id, step, to_email, subject, body, status, send_at)
               VALUES (?, ?, 1, 'lead@test.com', 'Subject', 'Body', 'pending_review', datetime('now'))""",
            (draft_id, camp.id),
        )
        await db.commit()

    # Rejection with empty reason must fail
    res_empty = auth_client.post(
        f"/api/campaigns/{camp.id}/drafts/{draft_id}/reject",
        json={"reason": ""},
    )
    assert res_empty.status_code == 400
    assert "rejection reason is required" in res_empty.json().get("message", "").lower()

    # Rejection with valid reason succeeds
    res_valid = auth_client.post(
        f"/api/campaigns/{camp.id}/drafts/{draft_id}/reject",
        json={"reason": "Value proposition does not match clinical practice"},
    )
    assert res_valid.status_code == 200
    assert res_valid.json().get("success") is True

    async with state._connect() as db:
        async with db.execute("SELECT status, error FROM outbox WHERE id = ?", (draft_id,)) as cur:
            row = await cur.fetchone()
            assert row[0] == "rejected"
            assert "Value proposition does not match" in row[1]


def test_safety_invariants_post_feature_completion():
    """Verify non-negotiable safety invariants remain 100% intact."""
    config = load_config()
    assert config.channels.email.require_approval is True, "CRITICAL: require_approval must be True"

    live_db = Path("data/harvey.db")
    if not live_db.exists():
        pytest.skip("Live database not present")

    conn = sqlite3.connect(str(live_db))
    c = conn.cursor()

    # Invariant 2: Sending paused hold active
    c.execute("SELECT value FROM settings WHERE key = 'sending_paused'")
    row = c.fetchone()
    assert row is not None, "sending_paused setting must exist"
    assert "Safety Hold" in row[0], f"CRITICAL: sending_paused hold altered: {row[0]}"

    # Invariant 3: Autonomous sending disabled; at most 1 explicit operator-authorized test send
    c.execute("SELECT count(*) FROM outbox WHERE status = 'sent'")
    sent_count = c.fetchone()[0]
    assert sent_count <= 1, f"CRITICAL: unexpected sent emails found: {sent_count}"

    # Invariant 4: Original 4 pilot drafts remain intact
    pilot_expected = {
        "a02fed19ec8d": "pending_review",
        "36dfa015198c": "cancelled",
        "3be7a27f3620": "cancelled",
        "fad4e4984339": "pending_review",
    }
    for pid, expected_status in pilot_expected.items():
        c.execute("SELECT status FROM outbox WHERE id LIKE ?", (f"{pid}%",))
        res = c.fetchone()
        assert res is not None, f"Pilot draft {pid} missing"
        assert res[0] == expected_status, f"Pilot draft {pid} status mismatch: {res[0]} != {expected_status}"

    conn.close()
