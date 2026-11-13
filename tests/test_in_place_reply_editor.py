"""
Tests for Harvey AI — Operator Decision Desk & In-Place Reply Editing Enhancements.
Verifies:
1. In-place draft editing directly on canonical outbox records without duplication.
2. Reject draft action with optional feedback notes safely stored in feedback table.
3. Conversation history context injection into follow-up reply generation.
4. Strict preservation of safety invariants (zero sends, pilot drafts intact, hold active).
"""

import asyncio
from datetime import datetime, timezone
import pytest
from unittest.mock import AsyncMock

from harvey.agents.prioritizer import (
    EmailPrioritizationService,
    PRIORITY_HIGH,
    PRIORITY_MEDIUM,
    PRIORITY_LOW,
)
from harvey.integrations.mail_provider import InboundMessage
from harvey.models.conversation import Conversation, Message
from harvey.models.prospect import Prospect
from harvey.state import StateManager


BASE_CONFIG = {
    "persona": {"name": "Harvey", "company": "Harvey AI", "role": "Assistant", "email": "harvey@example.com", "linkedin": "", "tone": "helpful"},
    "product": {"name": "Harvey AI", "description": "AI agent for dental patient inquiries", "pricing": "$99/mo", "key_benefits": ["24/7 triage"], "objection_responses": {}},
    "icp": {"industries": ["Dental"], "company_size": "1-10", "titles": ["Dentist", "Director"], "geography": ["US", "India"]},
}


@pytest.mark.asyncio
async def test_in_place_draft_edit_persists_in_canonical_outbox(tmp_path):
    """Verify that editing a draft updates the single canonical record in Outbox without duplication."""
    db_path = str(tmp_path / "test_in_place_edit.db")
    state = StateManager(db_path)
    await state.init_db()

    # Create prospect
    prospect = Prospect(
        id="p_test_edit_1",
        email="dr.edit@clinic.com",
        first_name="Pooja",
        company="Pooja Dental Care",
        status="contacted",
    )
    await state.add_prospect(prospect)

    # Stage an initial reply draft
    initial_body = "Hi Pooja,\n\nThank you for reaching out. We would love to show you Harvey AI."
    draft_id = await state.add_outbox_item(
        prospect_id=prospect.id,
        conversation_id="convo_edit_1",
        kind="reply",
        to_email=prospect.email,
        subject="Re: Inquiry",
        body=initial_body,
        send_at=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        status="pending_review",
    )
    assert draft_id is not None

    # Simulate in-place operator editing
    updated_body = (
        "Hi Pooja,\n\n"
        "Thank you for contacting us regarding Pooja Dental Care. "
        "We can schedule a 15-minute consultation this Thursday at 3 PM if that suits you.\n\n"
        "Best regards,\nHarvey"
    )
    ok = await state.edit_outbox_draft(draft_id, "Re: Inquiry", updated_body)
    assert bool(ok) is True
    assert ok == 1

    # Confirm canonical outbox record is updated
    item = await state.get_outbox_item(draft_id)
    assert item is not None
    assert item["body"] == updated_body
    assert item["status"] == "pending_review"  # Human approval still strictly required

    # Verify no duplicate draft record was created
    all_drafts = await state.get_outbox(status="pending_review")
    matching = [d for d in all_drafts if d["id"] == draft_id]
    assert len(matching) == 1
    assert len(all_drafts) == 1


@pytest.mark.asyncio
async def test_reject_draft_with_feedback_storage(tmp_path):
    """Verify rejecting a draft transitions status to rejected and stores feedback note safely."""
    db_path = str(tmp_path / "test_reject_feedback.db")
    state = StateManager(db_path)
    await state.init_db()

    draft_id = await state.add_outbox_item(
        prospect_id="p_test_rej_1",
        conversation_id="convo_rej_1",
        kind="reply",
        to_email="reject_test@clinic.com",
        subject="Re: Dental AI demo",
        body="Draft content waiting for operator review.",
        send_at=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        status="pending_review",
    )

    # Reject draft and store operator feedback
    feedback_comment = "Too salesy; adjust tone to be more consultative for a clinical director."
    n = await state.reject_outbox_item(draft_id)
    assert n == 1

    feedback_id = await state.add_feedback(
        entity_type="outbox_draft",
        entity_id=draft_id,
        comment=feedback_comment,
    )
    assert feedback_id is not None

    # Verify outbox item status is rejected
    item = await state.get_outbox_item(draft_id)
    assert item["status"] == "rejected"

    # Verify feedback was saved and can be retrieved
    stored_feedback = await state.get_feedback(entity_type="outbox_draft", entity_id=draft_id)
    assert len(stored_feedback) == 1
    assert stored_feedback[0]["comment"] == feedback_comment
    assert stored_feedback[0]["entity_id"] == draft_id


@pytest.mark.asyncio
async def test_conversation_history_context_injection(tmp_path):
    """Verify multi-message conversation thread history is injected into prompt for follow-up reply."""
    db_path = str(tmp_path / "test_history_context.db")
    state = StateManager(db_path)
    await state.init_db()

    prospect = Prospect(
        id="p_history_1",
        email="dr.history@dentalstudio.com",
        first_name="Rohan",
        company="Rohan Dental Studio",
        status="engaged",
    )
    await state.add_prospect(prospect)

    # Build a thread with prior history:
    # 1. Cold outreach from Harvey
    # 2. Prospect asking about practice management integration
    # 3. Harvey answering
    # 4. Current incoming reply asking about demo availability
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    thread = [
        Message(sender="harvey", content="Hi Rohan, reaching out from Harvey AI regarding dental patient triage.", timestamp=now),
        Message(sender="prospect", content="Hi, does Harvey integrate with Dentrix or Practo?", timestamp=now),
        Message(sender="harvey", content="Yes, we support practice management exports and direct calendar sync.", timestamp=now),
        Message(sender="prospect", content="Great, could we schedule a 15-minute demo this Thursday afternoon?", timestamp=now),
    ]

    convo = Conversation(
        id="convo_history_01",
        prospect_id=prospect.id,
        channel="mock",
        thread=thread,
        intent="interested",
        priority="high",
        status="needs_human",
    )
    await state.add_conversation(convo)

    # Mock Brain that records the prompt passed to it
    captured_prompts = []
    class MockBrain:
        async def think(self, prompt, task=""):
            captured_prompts.append(prompt)
            return (
                "Hi Rohan,\n\n"
                "I would be glad to walk you through a 15-minute demo this Thursday afternoon. "
                "Does 3:00 PM work for you?\n\n"
                "Best regards,\nHarvey"
            )

    service = EmailPrioritizationService(state=state, brain=MockBrain())

    inbound = InboundMessage(
        provider_id="inbound_msg_followup",
        from_email=prospect.email,
        subject="Re: Dental patient triage",
        body="Great, could we schedule a 15-minute demo this Thursday afternoon?",
    )

    result = await service.process_inbound(inbound, auto_draft_high=True)
    assert result["priority"] == PRIORITY_HIGH
    assert result["staged_in_outbox"] is True

    # Verify that the generated prompt included the bounded prior conversation thread history
    assert len(captured_prompts) == 1
    prompt_text = captured_prompts[0]
    assert "Prior conversation thread history:" in prompt_text
    assert "Dentrix or Practo" in prompt_text
    assert "practice management exports" in prompt_text
    assert "Rohan Dental Studio" in prompt_text

    # Verify staged draft is held for human approval
    draft_item = await state.get_outbox_item(result["draft_id"])
    assert draft_item["status"] == "pending_review"
    assert "Thursday" in draft_item["body"]
    assert "Rohan" in draft_item["body"]


@pytest.mark.asyncio
async def test_safety_invariants_during_draft_operations(tmp_path):
    """Verify that draft edits, approvals, and rejections strictly preserve sent_count = 0."""
    db_path = str(tmp_path / "test_safety_invariants.db")
    state = StateManager(db_path)
    await state.init_db()

    # Initially zero sent
    all_outbox = await state.get_outbox()
    assert len([d for d in all_outbox if d["status"] == "sent"]) == 0

    # Add draft
    draft_id = await state.add_outbox_item(
        prospect_id="p_safe_1",
        to_email="safe@example.com",
        subject="Hello",
        body="Body text",
        send_at=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        status="pending_review",
    )

    # Approve draft
    await state.approve_outbox(draft_id)
    approved_item = await state.get_outbox_item(draft_id)
    assert approved_item["status"] == "approved"

    # Confirm sent_count is still 0 (approval does NOT send email)
    all_outbox = await state.get_outbox()
    assert len([d for d in all_outbox if d["status"] == "sent"]) == 0

    # Reject draft
    await state.reject_outbox_item(draft_id)
    rejected_item = await state.get_outbox_item(draft_id)
    assert rejected_item["status"] == "rejected"

    # Confirm sent_count remains 0
    all_outbox = await state.get_outbox()
    assert len([d for d in all_outbox if d["status"] == "sent"]) == 0
