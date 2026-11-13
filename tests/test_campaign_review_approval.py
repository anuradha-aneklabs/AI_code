"""Tests for Campaign Review & Approval Workflow.

Verifies:
1. Review queue structure: Step 1 drafts surfaced, later steps kept planned only.
2. Draft approval safety and idempotency (prevents duplicate approvals).
3. Mandatory rejection reason on draft rejection with feedback storage.
4. Draft editing persistence in Outbox with pre-send gate validation.
5. Chronological audit history recording and retrieval.
6. Profile and campaign isolation for draft actions.
7. Strict preservation of safety invariants (require_approval=True, pause hold active, sent_count=0).
"""

import json
import pytest
import aiosqlite
from datetime import datetime, timezone

from harvey.campaign_generator import CampaignGenerator
from harvey.campaign_review import CampaignReviewManager
from harvey.models.campaign import Campaign, EmailStep
from harvey.models.company import Company
from harvey.models.prospect import Prospect
from harvey.state import StateManager


@pytest.fixture
def state(tmp_path):
    db_file = tmp_path / "test_review.db"
    mgr = StateManager(db_path=str(db_file))
    return mgr


@pytest.mark.asyncio
async def test_campaign_review_queue_retrieval(state):
    await state.init_db()
    manager = CampaignReviewManager(state=state)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # 1. Setup profile, company, prospect, and campaign
    profile_id = await state.create_business_profile({
        "name": "Dental Clinics India",
        "industry": "Healthcare & Dental",
        "geography": "India",
        "company_size": "5-25",
        "product_name": "DentCare AI",
        "product_description": "AI receptionist",
    })

    c = Company(
        name="Apex Smiles",
        domain="apexsmiles.in",
        industry="Dental Care",
        location="Indore, India",
    )
    company_id = await state.add_company(c)

    p = Prospect(
        company_id=company_id,
        email="dr.raj@apexsmiles.in",
        first_name="Raj",
        last_name="Sharma",
        title="Chief Dentist",
        company="Apex Smiles",
        source=f"discovery:{profile_id}",
    )
    prospect_id = await state.add_prospect(p)

    campaign_id = "camp-review-test-01"
    steps = [
        EmailStep(step=1, subject="Intro question for Apex Smiles", body="Hello Dr. Raj", delay_days=0),
        EmailStep(step=2, subject="Follow-up on DentCare AI", body="Checking in", delay_days=3),
        EmailStep(step=3, subject="Final check-in", body="Last touch", delay_days=3),
    ]
    camp = Campaign(
        id=campaign_id,
        name="Indore Dental Outreach",
        sequence=steps,
        status="active",
        prospect_ids=[prospect_id],
    )
    await state.add_campaign(camp)

    # Stage ONLY Step 1 into Outbox
    draft_id = await state.add_outbox_item(
        prospect_id=prospect_id,
        campaign_id=campaign_id,
        step=1,
        to_email="dr.raj@apexsmiles.in",
        subject="Intro question for Apex Smiles",
        body="Hello Dr. Raj",
        send_at=now,
        status="pending_review",
        approved_by="",
    )

    # 2. Retrieve review queue
    review = await manager.get_campaign_review(campaign_id)
    assert review["success"] is True
    assert review["campaign"]["name"] == "Indore Dental Outreach"
    assert review["summary"]["total_drafts"] == 1
    assert review["summary"]["pending_count"] == 1
    assert review["summary"]["approved_count"] == 0
    assert review["summary"]["later_steps_in_outbox"] == 0

    # Verify Step 1 drafts
    assert len(review["step_1_drafts"]) == 1
    d = review["step_1_drafts"][0]
    assert d["id"] == draft_id
    assert d["recipient_name"] == "Raj Sharma"
    assert d["status"] == "pending_review"

    # Verify later steps are planned only
    assert len(review["planned_steps"]) == 2
    assert review["planned_steps"][0]["step"] == 2
    assert review["planned_steps"][0]["is_planned_only"] is True
    assert review["planned_steps"][0]["in_outbox"] is False
    assert review["planned_steps"][1]["step"] == 3


@pytest.mark.asyncio
async def test_approve_campaign_draft_safety_and_idempotency(state):
    await state.init_db()
    manager = CampaignReviewManager(state=state)

    campaign_id = "camp-approve-test"
    camp = Campaign(id=campaign_id, name="Test Campaign", status="active", sequence=[])
    await state.add_campaign(camp)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    draft_id = await state.add_outbox_item(
        prospect_id="p-1",
        campaign_id=campaign_id,
        step=1,
        to_email="test@example.com",
        subject="Hello",
        body="World",
        send_at=now,
        status="pending_review",
        approved_by="",
    )

    # 1. Approve draft
    res = await manager.approve_draft(campaign_id=campaign_id, draft_id=draft_id, operator="operator_alice")
    assert res["success"] is True
    assert res["status"] == "approved"
    assert res["previous_status"] == "pending_review"

    item = await state.get_outbox_item(draft_id)
    assert item["status"] == "approved"
    assert item["approved_by"] == "operator_alice"

    # Strict Safety: Verify no actual emails were sent
    async with state._connect() as db:
        async with db.execute("SELECT COUNT(*) FROM outbox WHERE status = 'sent'") as cur:
            sent_count = (await cur.fetchone())[0]
    assert sent_count == 0

    # 2. Idempotency test: approve again
    res2 = await manager.approve_draft(campaign_id=campaign_id, draft_id=draft_id, operator="operator_alice")
    assert res2["success"] is True
    assert res2["already_approved"] is True
    assert res2["status"] == "approved"

    # 3. Verify audit history logged
    actions = await state.get_actions_for_campaign(campaign_id)
    assert len(actions) >= 1
    approved_action = next(a for a in actions if a["action_type"] == "campaign_draft_approved")
    assert approved_action["agent"] == "operator_alice"
    assert approved_action["details"]["previous_status"] == "pending_review"
    assert approved_action["details"]["new_status"] == "approved"
    assert approved_action["details"]["draft_id"] == draft_id


@pytest.mark.asyncio
async def test_reject_campaign_draft_requires_reason_and_audit(state):
    await state.init_db()
    manager = CampaignReviewManager(state=state)

    campaign_id = "camp-reject-test"
    camp = Campaign(id=campaign_id, name="Reject Campaign", status="active", sequence=[])
    await state.add_campaign(camp)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    draft_id = await state.add_outbox_item(
        prospect_id="p-2",
        campaign_id=campaign_id,
        step=1,
        to_email="prospect@example.com",
        subject="Pitch",
        body="Copy",
        send_at=now,
        status="pending_review",
        approved_by="",
    )

    # 1. Rejection without reason must fail
    res_no_reason = await manager.reject_draft(campaign_id=campaign_id, draft_id=draft_id, reason="")
    assert res_no_reason["success"] is False
    assert "rejection reason is required" in res_no_reason["message"].lower()

    # Draft should still be pending_review
    item = await state.get_outbox_item(draft_id)
    assert item["status"] == "pending_review"

    # 2. Rejection with valid reason
    rejection_reason = "Tone is overly aggressive; value proposition needs softening."
    res = await manager.reject_draft(
        campaign_id=campaign_id, draft_id=draft_id, reason=rejection_reason, operator="operator_bob"
    )
    assert res["success"] is True
    assert res["status"] == "rejected"
    assert res["reason"] == rejection_reason

    # Verify Outbox updated
    item = await state.get_outbox_item(draft_id)
    assert item["status"] == "rejected"
    assert item["error"] == rejection_reason

    # Verify feedback table persisted
    feedback = await state.get_feedback(entity_type="campaign_draft", entity_id=draft_id)
    assert len(feedback) == 1
    assert feedback[0]["comment"] == rejection_reason

    # 3. Idempotency test: reject again with same reason
    res2 = await manager.reject_draft(
        campaign_id=campaign_id, draft_id=draft_id, reason=rejection_reason, operator="operator_bob"
    )
    assert res2["success"] is True
    assert res2["already_rejected"] is True

    # 4. Verify audit history logged
    actions = await state.get_actions_for_campaign(campaign_id)
    reject_action = next(a for a in actions if a["action_type"] == "campaign_draft_rejected")
    assert reject_action["agent"] == "operator_bob"
    assert reject_action["details"]["rejection_reason"] == rejection_reason
    assert reject_action["details"]["new_status"] == "rejected"


@pytest.mark.asyncio
async def test_edit_campaign_draft_persistence_and_gate_enforcement(state):
    await state.init_db()
    manager = CampaignReviewManager(state=state)

    campaign_id = "camp-edit-test"
    camp = Campaign(id=campaign_id, name="Edit Campaign", status="active", sequence=[])
    await state.add_campaign(camp)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    draft_id = await state.add_outbox_item(
        prospect_id="p-3",
        campaign_id=campaign_id,
        step=1,
        to_email="clinician@example.com",
        subject="Original subject",
        body="Original body text",
        send_at=now,
        status="pending_review",
        approved_by="",
    )

    # 1. Edit with valid copy
    new_subj = "Quick question on clinic operations"
    new_body = "Hi Doctor,\n\nWe noticed your practice has expanded in Indore. Are you considering AI reception?\n\nBest,\nHarvey"
    res = await manager.edit_draft(
        campaign_id=campaign_id,
        draft_id=draft_id,
        subject=new_subj,
        body=new_body,
        operator="operator_carol",
    )
    assert res["success"] is True
    assert res["subject"] == new_subj
    assert res["body"] == new_body

    # Check database persistence
    item = await state.get_outbox_item(draft_id)
    assert item["subject"] == new_subj
    assert item["body"] == new_body
    assert item["status"] == "pending_review"

    # 2. Pre-send gate enforcement: empty subject/body rejected
    res_empty = await manager.edit_draft(campaign_id=campaign_id, draft_id=draft_id, subject="", body="Some body")
    assert res_empty["success"] is False
    assert "required" in res_empty["message"].lower()

    # Pre-send gate enforcement: excessive subject length rejected
    res_too_long = await manager.edit_draft(
        campaign_id=campaign_id,
        draft_id=draft_id,
        subject="This subject line is way too long and clearly exceeds the maximum eighty character limit easily",
        body="Valid body text",
    )
    assert res_too_long["success"] is False
    assert "pre-send check failed" in res_too_long["message"].lower()

    # 3. Verify audit log entry
    actions = await state.get_actions_for_campaign(campaign_id)
    edit_action = next(a for a in actions if a["action_type"] == "campaign_draft_edited")
    assert edit_action["agent"] == "operator_carol"
    assert edit_action["details"]["previous_subject"] == "Original subject"
    assert edit_action["details"]["new_subject"] == new_subj


@pytest.mark.asyncio
async def test_campaign_review_profile_and_campaign_isolation(state):
    await state.init_db()
    manager = CampaignReviewManager(state=state)

    # Setup two distinct campaigns
    camp1_id = "camp-profile-A"
    camp2_id = "camp-profile-B"
    await state.add_campaign(Campaign(id=camp1_id, name="Campaign A", status="active", sequence=[]))
    await state.add_campaign(Campaign(id=camp2_id, name="Campaign B", status="active", sequence=[]))

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    draft1_id = await state.add_outbox_item(
        prospect_id="p-A",
        campaign_id=camp1_id,
        step=1,
        to_email="leadA@example.com",
        subject="Subject A",
        body="Body A",
        send_at=now,
        status="pending_review",
        approved_by="",
    )

    # Attempting to approve draft1 through Campaign B must fail with isolation error
    res_mismatch = await manager.approve_draft(campaign_id=camp2_id, draft_id=draft1_id, operator="operator_cross")
    assert res_mismatch["success"] is False
    assert "does not belong" in res_mismatch["message"].lower()

    # Draft1 remains untouched in Campaign A
    item = await state.get_outbox_item(draft1_id)
    assert item["status"] == "pending_review"


@pytest.mark.asyncio
async def test_campaign_review_strict_safety_invariants(state):
    await state.init_db()
    manager = CampaignReviewManager(state=state)

    # Confirm initial safety settings
    await state.set_setting("sending_paused", "Pilot Evaluation - Read-Only Safety Hold")
    await state.set_setting("require_approval", "true")

    camp_id = "camp-safety-audit"
    await state.add_campaign(Campaign(id=camp_id, name="Safety Campaign", status="active", sequence=[]))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    draft_id = await state.add_outbox_item(
        prospect_id="p-safe",
        campaign_id=camp_id,
        step=1,
        to_email="safe@example.com",
        subject="Safety Check",
        body="Body",
        send_at=now,
        status="pending_review",
        approved_by="",
    )

    # Perform approve, edit, reject cycle
    await manager.edit_draft(campaign_id=camp_id, draft_id=draft_id, subject="Edited Safety Check", body="Updated body")
    await manager.approve_draft(campaign_id=camp_id, draft_id=draft_id)

    # Strict Safety Checks
    assert await state.get_setting("sending_paused") == "Pilot Evaluation - Read-Only Safety Hold"
    assert await state.get_setting("require_approval") == "true"

    async with state._connect() as db:
        async with db.execute("SELECT COUNT(*) FROM outbox WHERE status = 'sent'") as cur:
            sent_count = (await cur.fetchone())[0]
    assert sent_count == 0
