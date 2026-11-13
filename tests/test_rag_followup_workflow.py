"""Tests for Harvey's RAG-powered campaign follow-up sequence (Step 2 and Step 3).

Verifies sequence timing, linkage, due-time gating, duplicate prevention,
RAG/citation cleanliness, stop-rules (replied, meeting, lost, closed, opted_out),
and permanent opt-out enforcement.
"""

from datetime import datetime, timedelta, timezone
import json
import pytest
import pytest_asyncio

from harvey.agents.sender import Sender, STOP_STATUSES, MIN_FOLLOW_UP_GAP
from harvey.config import load_config
from harvey.models.campaign import Campaign, EmailStep
from harvey.state import Prospect, Company, StateManager
from tests.test_outbox_native import FakeProvider, make_sender, state  # noqa: F401


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture
def test_campaign_sequence():
    """Configured sequence matching [TEST] RAG Grounded Dental Outreach."""
    return [
        EmailStep(
            step=1,
            delay_days=0,
            subject="Personalized practice inquiry triage & patient communication for {{company}}",
            body="Hi {{first_name}},\n\nI noticed {{company}}'s specialized clinical services in Indore.\n\nHarvey AI assists dental practices with automated initial inquiry triage and structured follow-ups.\n\nBest regards,\nHarvey AI Team\n\nNot interested? Reply 'no thanks' and I won't email again.",
        ),
        EmailStep(
            step=2,
            delay_days=3,
            subject="Follow-up: inquiry triage workflow for {{company}}",
            body="Hi {{first_name}},\n\nFollowing up on my previous note. Many dental practices spend significant front-desk hours on routine inquiry classification.\n\nHarvey AI stages grounded drafts for review and keeps all patient communication data local.\n\nLet me know if you would like to review a quick summary.\n\nBest regards,\nHarvey AI Team\n\nNot interested? Reply 'no thanks' and I won't email again.",
        ),
        EmailStep(
            step=3,
            delay_days=4,
            subject="Closing the loop - {{company}}",
            body="Hi {{first_name}},\n\nIf patient communication workflows are not a current priority for {{company}}, I will step back and not follow up further.\n\nThank you for your time.\n\nBest regards,\nHarvey AI Team\n\nNot interested? Reply 'no thanks' and I won't email again.",
        ),
    ]


# ── 1. Sequence Timing Actually Configured ──


@pytest.mark.asyncio
async def test_sequence_timing_configured(state, test_campaign_sequence):
    """Verify configured delays for Step 1 (0d), Step 2 (3d), and Step 3 (4d)."""
    camp = Campaign(
        id="c_timing_test",
        name="[TEST] Timing Campaign",
        sequence=test_campaign_sequence,
        status="draft",
    )
    await state.add_campaign(camp)

    step1_delay = await state.get_step_delay_days("c_timing_test", 1)
    step2_delay = await state.get_step_delay_days("c_timing_test", 2)
    step3_delay = await state.get_step_delay_days("c_timing_test", 3)

    assert step1_delay == 0, f"Expected Step 1 delay=0, got {step1_delay}"
    assert step2_delay == 3, f"Expected Step 2 delay=3, got {step2_delay}"
    assert step3_delay == 4, f"Expected Step 3 delay=4, got {step3_delay}"


# ── 2. Linkage to Previous Steps ──


@pytest.mark.asyncio
async def test_followup_step_linkage(state):
    """Verify that get_previous_step links Step 2 -> Step 1, and Step 3 -> Step 2."""
    pid = "p_linkage_01"
    cid = "c_linkage_01"

    # Stage Step 1
    s1_id = await state.add_outbox_item(
        prospect_id=pid, campaign_id=cid, step=1, to_email="dentist@test.com",
        subject="Step 1 Subject", body="Step 1 Body", send_at=_now().isoformat(),
        status="pending_review",
    )
    # Stage Step 2
    s2_id = await state.add_outbox_item(
        prospect_id=pid, campaign_id=cid, step=2, to_email="dentist@test.com",
        subject="Step 2 Subject", body="Step 2 Body", send_at=(_now() + timedelta(days=3)).isoformat(),
        status="pending_review",
    )
    # Stage Step 3
    s3_id = await state.add_outbox_item(
        prospect_id=pid, campaign_id=cid, step=3, to_email="dentist@test.com",
        subject="Step 3 Subject", body="Step 3 Body", send_at=(_now() + timedelta(days=7)).isoformat(),
        status="pending_review",
    )

    item2 = await state.get_outbox_item(s2_id)
    prev_for_2 = await state.get_previous_step(item2)
    assert prev_for_2 is not None
    assert prev_for_2["id"] == s1_id
    assert prev_for_2["step"] == 1

    item3 = await state.get_outbox_item(s3_id)
    prev_for_3 = await state.get_previous_step(item3)
    assert prev_for_3 is not None
    assert prev_for_3["id"] == s2_id
    assert prev_for_3["step"] == 2


# ── 3. Step 2 & Step 3 Due Timing & Gating ──


@pytest.mark.asyncio
async def test_step2_waits_until_step1_is_sent(state, test_campaign_sequence):
    """Step 2 must return 'wait' while Step 1 is pending_review, approved, or sending."""
    cid = "c_order_01"
    pid = "p_order_01"
    camp = Campaign(id=cid, name="Timing", sequence=test_campaign_sequence, status="draft")
    await state.add_campaign(camp)

    sender = make_sender(state, FakeProvider())

    s1_id = await state.add_outbox_item(
        prospect_id=pid, campaign_id=cid, step=1, to_email="d@clinic.com",
        subject="S1", body="B1", send_at=_now().isoformat(), status="pending_review",
    )
    s2_id = await state.add_outbox_item(
        prospect_id=pid, campaign_id=cid, step=2, to_email="d@clinic.com",
        subject="S2", body="B2", send_at=_now().isoformat(), status="approved",
    )
    item2 = await state.get_outbox_item(s2_id)

    # Step 1 is pending_review -> Step 2 must wait
    verdict = await sender._follow_up_order(item2)
    assert verdict == "wait"

    # Step 1 is approved -> Step 2 must still wait
    await state.update_outbox_item(s1_id, status="approved")
    item2 = await state.get_outbox_item(s2_id)
    assert await sender._follow_up_order(item2) == "wait"

    # Step 1 is sending -> Step 2 must still wait
    await state.update_outbox_item(s1_id, status="sending")
    item2 = await state.get_outbox_item(s2_id)
    assert await sender._follow_up_order(item2) == "wait"


@pytest.mark.asyncio
async def test_step2_due_time_gating_after_step1_sent(state, test_campaign_sequence):
    """Step 2 requires 3 days to elapse after Step 1 was sent before it can be sent."""
    cid = "c_timing_02"
    pid = "p_timing_02"
    camp = Campaign(id=cid, name="Timing", sequence=test_campaign_sequence, status="draft")
    await state.add_campaign(camp)

    sender = make_sender(state, FakeProvider())

    s1_id = await state.add_outbox_item(
        prospect_id=pid, campaign_id=cid, step=1, to_email="d@clinic.com",
        subject="S1", body="B1", send_at=_now().isoformat(), status="sent",
    )
    # Simulate Step 1 was sent 2 days ago (less than the 3-day required delay)
    two_days_ago = _now() - timedelta(days=2)
    await state.update_outbox_item(s1_id, sent_at=two_days_ago.isoformat())

    s2_id = await state.add_outbox_item(
        prospect_id=pid, campaign_id=cid, step=2, to_email="d@clinic.com",
        subject="S2", body="B2", send_at=_now().isoformat(), status="approved",
    )
    item2 = await state.get_outbox_item(s2_id)

    # Only 2 days passed -> must wait and reschedule
    verdict = await sender._follow_up_order(item2)
    assert verdict == "wait"

    # Now simulate Step 1 was sent 3.5 days ago (configured 3 days passed)
    three_point_five_days_ago = _now() - timedelta(days=3.5)
    await state.update_outbox_item(s1_id, sent_at=three_point_five_days_ago.isoformat())
    item2 = await state.get_outbox_item(s2_id)

    # 3 days passed -> ready to send (returns None)
    assert await sender._follow_up_order(item2) is None


@pytest.mark.asyncio
async def test_step3_due_time_gating_after_step2_sent(state, test_campaign_sequence):
    """Step 3 requires 4 days to elapse after Step 2 was sent before it can be sent."""
    cid = "c_timing_03"
    pid = "p_timing_03"
    camp = Campaign(id=cid, name="Timing", sequence=test_campaign_sequence, status="draft")
    await state.add_campaign(camp)

    sender = make_sender(state, FakeProvider())

    # Step 1 was sent 8 days ago
    s1_id = await state.add_outbox_item(
        prospect_id=pid, campaign_id=cid, step=1, to_email="d@clinic.com",
        subject="S1", body="B1", send_at=_now().isoformat(), status="sent",
    )
    await state.update_outbox_item(s1_id, sent_at=(_now() - timedelta(days=8)).isoformat())

    # Step 2 was sent 3 days ago (configured delay is 4 days)
    s2_id = await state.add_outbox_item(
        prospect_id=pid, campaign_id=cid, step=2, to_email="d@clinic.com",
        subject="S2", body="B2", send_at=_now().isoformat(), status="sent",
    )
    await state.update_outbox_item(s2_id, sent_at=(_now() - timedelta(days=3)).isoformat())

    # Step 3 staged
    s3_id = await state.add_outbox_item(
        prospect_id=pid, campaign_id=cid, step=3, to_email="d@clinic.com",
        subject="S3", body="B3", send_at=_now().isoformat(), status="approved",
    )
    item3 = await state.get_outbox_item(s3_id)

    # Only 3 days passed since Step 2 -> must wait
    assert await sender._follow_up_order(item3) == "wait"

    # Step 2 sent 4.5 days ago -> 4-day delay satisfied -> ready to send
    await state.update_outbox_item(s2_id, sent_at=(_now() - timedelta(days=4.5)).isoformat())
    item3 = await state.get_outbox_item(s3_id)
    assert await sender._follow_up_order(item3) is None


# ── 4. Duplicate Follow-Up Prevention ──


@pytest.mark.asyncio
async def test_duplicate_followup_prevention(state):
    """The unique constraint uq_outbox_campaign_step must block duplicate follow-up staging."""
    pid = "p_dup_01"
    cid = "c_dup_01"

    # Stage Step 2
    item1 = await state.add_outbox_item(
        prospect_id=pid, campaign_id=cid, step=2, to_email="doc@example.com",
        subject="Step 2", body="Body 2", send_at=_now().isoformat(),
        status="pending_review",
    )
    assert item1 is not None

    # Attempt duplicate Stage Step 2 for same prospect & campaign
    item2 = await state.add_outbox_item(
        prospect_id=pid, campaign_id=cid, step=2, to_email="doc@example.com",
        subject="Step 2 Duplicate", body="Body 2 Duplicate", send_at=_now().isoformat(),
        status="pending_review",
    )
    assert item2 is None, "Duplicate follow-up was NOT prevented!"


# ── 5. Stop-Rule Suppression for All 5 Statuses ──


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["replied", "meeting", "lost", "closed", "opted_out"])
async def test_all_stop_statuses_suppress_followups(state, status):
    """Sender._blocked must suppress follow-ups for all STOP_STATUSES."""
    assert status in STOP_STATUSES

    pid = f"p_stop_{status}"
    prospect = Prospect(
        id=pid,
        first_name="Dr.",
        last_name="Test",
        company="Dental Clinic",
        email="test@dental.com",
        status=status,
    )
    await state.add_prospect(prospect)

    sender = make_sender(state, FakeProvider())
    item = {
        "id": "item_stop_test",
        "kind": "sequence",
        "step": 2,
        "prospect_id": pid,
        "to_email": "test@dental.com",
        "subject": "Follow-up",
        "body": "Follow-up message body.",
    }

    result = await sender._blocked(item, prospect, allow_risky=False)
    assert result is not None
    action, reason = result
    assert action == "cancelled"
    assert f"prospect status '{status}'" in reason


@pytest.mark.asyncio
async def test_opted_out_prospect_cannot_be_reentered(state):
    """An opted-out prospect cannot have their status updated back to queued/new."""
    pid = "p_optout_permanent"
    prospect = Prospect(
        id=pid,
        first_name="Dr. Opt",
        last_name="Out",
        company="Safe Clinic",
        email="optout@clinic.com",
        status="queued",
    )
    await state.add_prospect(prospect)

    # Opt out prospect
    await state.update_prospect_status(pid, "opted_out")
    p = await state.get_prospect(pid)
    assert p.status == "opted_out"

    # Attempt normal status update back to queued (simulating accidental re-entry)
    await state.update_prospect_status(pid, "queued")
    p_after = await state.get_prospect(pid)
    assert p_after.status == "opted_out", "Opt-out invariant violated: prospect was moved off opted_out!"


# ── 6. RAG Grounding & Citation Cleanliness on Rendered Follow-ups ──


def test_followup_content_cleanliness(test_campaign_sequence):
    """Rendered follow-ups must have zero technical citation tags or pricing hallucinations."""
    prospect = Prospect(
        id="p_render_01",
        first_name="Vishakha",
        last_name="Jain",
        company="Seraphic Dental",
        title="Dental Surgeon",
        email="drvishakha@seraphic.com",
    )

    sender = make_sender(None, FakeProvider())

    for step in test_campaign_sequence:
        rendered_subject = sender._render(step.subject, prospect)
        rendered_body = sender._render(step.body, prospect)

        # 1. No leftover template tags
        assert "{{" not in rendered_subject
        assert "{{" not in rendered_body

        # 2. Correct replacement
        assert "Seraphic Dental" in rendered_subject or "Seraphic Dental" in rendered_body

        # 3. No internal RAG citation tags
        assert "[Fact Source:" not in rendered_body
        assert "[Doc:" not in rendered_body
        assert "[Chunk:" not in rendered_body
        assert "chunk_id" not in rendered_body

        # 4. No hallucinated pricing ($49/mo) or live EHR booking claims
        assert "$49" not in rendered_body
        assert "EHR live" not in rendered_body
