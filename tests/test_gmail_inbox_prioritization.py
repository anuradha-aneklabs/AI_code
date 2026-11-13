"""
Tests for Harvey AI — Gmail Read-Only Integration, Priority Classification,
High-Priority Draft Staging, and Approval Safety.
"""

import asyncio
from datetime import datetime, timezone
import json
import pytest
from unittest.mock import AsyncMock, patch

from harvey.agents.prioritizer import (
    PRIORITY_HIGH,
    PRIORITY_MEDIUM,
    PRIORITY_LOW,
    classify_email_priority,
    EmailPrioritizationService,
)
from harvey.integrations.gmail import (
    GmailProvider,
    READONLY_SCOPE,
    MODIFY_SCOPE,
    DEFAULT_SCOPE,
)
from harvey.integrations.mail_provider import InboundMessage
from harvey.models.conversation import Conversation
from harvey.models.prospect import Prospect
from harvey.state import StateManager


BASE_CONFIG = {
    "persona": {"name": "Harvey", "company": "Harvey AI", "role": "Assistant", "email": "harvey@example.com", "linkedin": "", "tone": "helpful"},
    "product": {"name": "Harvey AI", "description": "AI agent", "pricing": "$99", "key_benefits": [], "objection_responses": {}},
    "icp": {"industries": ["Dental"], "company_size": "1-10", "titles": ["Dentist"], "geography": ["US"]},
}

# =====================================================================
# 1. GMAIL READ-ONLY SCOPE & INBOX TESTS
# =====================================================================

def test_gmail_scope_defaults_to_readonly():
    """Verify Gmail integration uses readonly scope by default for safety."""
    assert DEFAULT_SCOPE == READONLY_SCOPE
    assert READONLY_SCOPE == "https://www.googleapis.com/auth/gmail.readonly"
    assert "readonly" in READONLY_SCOPE


@pytest.mark.asyncio
async def test_gmail_readonly_token_blocks_live_sending(monkeypatch):
    """Verify that a token with gmail.readonly scope strictly refuses send_email."""
    from harvey.config import HarveyConfig, BrainConfig

    # Mock token loading to return a readonly token
    monkeypatch.setattr(
        "harvey.integrations.gmail._load_token",
        lambda: {
            "refresh_token": "mock-refresh-token",
            "scope": READONLY_SCOPE,
            "obtained_at": 1700000000,
        },
    )

    cfg = HarveyConfig(**BASE_CONFIG)
    env = AsyncMock()
    env.gmail_client_id = "test-client-id"
    env.gmail_client_secret = "test-client-secret"

    provider = GmailProvider(cfg, env)
    result = await provider.send_email(
        to_email="prospect@example.com",
        subject="Hello",
        body="Test message body",
    )

    assert result.ok is False
    assert "read-only" in result.error.lower()
    assert "disabled" in result.error.lower()


@pytest.mark.asyncio
async def test_gmail_read_inbox_parses_messages(monkeypatch):
    """Verify read_inbox retrieves and parses messages into InboundMessage objects."""
    from harvey.config import HarveyConfig

    cfg = HarveyConfig(**BASE_CONFIG)
    env = AsyncMock()
    provider = GmailProvider(cfg, env)

    # Mock _request to return a list of message stubs
    async def mock_request(method, path, params=None, **kwargs):
        if path == "/messages":
            return {
                "messages": [
                    {"id": "msg_001", "threadId": "thread_001"},
                    {"id": "msg_002", "threadId": "thread_002"},
                ]
            }
        return None

    # Mock _fetch to return parsed InboundMessages
    async def mock_fetch(msg_id, context=""):
        if msg_id == "msg_001":
            return (
                InboundMessage(
                    provider_id="msg_001",
                    from_email="dr.dentist@example.com",
                    subject="Interested in dental outreach pilot",
                    body="Hi, could you share more details and pricing for your clinic workflow?",
                    message_id="<msg1@example.com>",
                    thread_ref="thread_001",
                ),
                frozenset({"INBOX"}),
            )
        elif msg_id == "msg_002":
            return (
                InboundMessage(
                    provider_id="msg_002",
                    from_email="admin@clinic.com",
                    subject="Out of office",
                    body="I am out of the office on annual leave until next week.",
                    message_id="<msg2@example.com>",
                    thread_ref="thread_002",
                ),
                frozenset({"INBOX"}),
            )
        return (None, frozenset())

    monkeypatch.setattr(provider, "_request", mock_request)
    monkeypatch.setattr(provider, "_fetch", mock_fetch)

    inbox_messages = await provider.read_inbox(max_results=10)
    assert len(inbox_messages) == 2
    assert inbox_messages[0].from_email == "dr.dentist@example.com"
    assert "pricing" in inbox_messages[0].body
    assert inbox_messages[1].from_email == "admin@clinic.com"
    assert "out of the office" in inbox_messages[1].body.lower()


# =====================================================================
# 2. PRIORITY CLASSIFICATION TESTS
# =====================================================================

def test_classify_high_priority_buying_intent():
    """Verify positive interest and pricing inquiries are classified as HIGH priority."""
    msg = InboundMessage(
        provider_id="1",
        from_email="lead@practice.com",
        subject="Re: Practice inquiry workflow",
        body="Hi Harvey, this sounds very interesting. What is your pricing and can we schedule a demo call?",
    )
    classified = classify_email_priority(msg)
    assert classified.priority == PRIORITY_HIGH
    assert classified.intent in ("interested", "demo_request", "pricing_inquiry")
    assert classified.urgency == "high"
    assert classified.should_draft_reply is True


def test_classify_high_priority_urgency_call_request():
    """Verify meeting requests and calls are classified as HIGH priority."""
    msg = InboundMessage(
        provider_id="2",
        from_email="partner@dental.com",
        subject="Let's connect",
        body="Can we speak tomorrow afternoon? Please send your calendar window or availability.",
    )
    classified = classify_email_priority(msg)
    assert classified.priority == PRIORITY_HIGH
    assert classified.should_draft_reply is True


def test_classify_medium_priority_clarification_question():
    """Verify general questions and competitor comparisons are classified as MEDIUM priority."""
    msg = InboundMessage(
        provider_id="3",
        from_email="info@clinic.org",
        subject="Question about Harvey",
        body="We currently use another service. How does your tool compare and where is your team based?",
    )
    classified = classify_email_priority(msg)
    assert classified.priority == PRIORITY_MEDIUM
    assert classified.intent == "question"
    assert classified.should_draft_reply is False


def test_classify_medium_priority_referral():
    """Verify colleague referrals are classified as MEDIUM priority."""
    msg = InboundMessage(
        provider_id="4",
        from_email="dr.smith@healthcare.org",
        subject="Re: Outreach",
        body="Please contact my colleague Dr. Sarah Jenkins, our practice manager, regarding this.",
    )
    classified = classify_email_priority(msg)
    assert classified.priority == PRIORITY_MEDIUM
    assert classified.should_draft_reply is False


def test_classify_low_priority_out_of_office():
    """Verify out-of-office auto-replies are classified as LOW priority."""
    msg = InboundMessage(
        provider_id="5",
        from_email="away@office.com",
        subject="Automatic reply: Away on vacation",
        body="Thank you for your email. I am out of the office on annual leave until Monday.",
    )
    classified = classify_email_priority(msg)
    assert classified.priority == PRIORITY_LOW
    assert classified.intent == "ooo"
    assert classified.should_draft_reply is False


def test_classify_low_priority_unsubscribe_opt_out():
    """Verify opt-out and unsubscribe requests are classified as LOW priority."""
    msg = InboundMessage(
        provider_id="6",
        from_email="optout@business.com",
        subject="Unsubscribe",
        body="Please unsubscribe and remove me from your mailing list.",
    )
    classified = classify_email_priority(msg)
    assert classified.priority == PRIORITY_LOW
    assert classified.intent == "unsubscribe"
    assert classified.should_draft_reply is False


def test_classify_low_priority_bounce():
    """Verify delivery failures and bounces are classified as LOW priority."""
    msg = InboundMessage(
        provider_id="7",
        from_email="mailer-daemon@googlemail.com",
        subject="Mail delivery failed: returning message to sender",
        body="The recipient server did not accept our connection.",
        is_bounce=True,
    )
    classified = classify_email_priority(msg)
    assert classified.priority == PRIORITY_LOW
    assert classified.intent == "bounce"
    assert classified.should_draft_reply is False


# =====================================================================
# 3. HIGH-PRIORITY DRAFT GENERATION & STAGING SAFETY
# =====================================================================

@pytest.mark.asyncio
async def test_high_priority_email_stages_draft_in_outbox(tmp_path):
    """Verify High-priority email triggers grounded reply draft staged as pending_review."""
    db_path = str(tmp_path / "test_prioritization.db")
    state = StateManager(db_path)
    await state.init_db()

    # Create a prospect
    prospect = Prospect(
        id="prospect_test_01",
        email="dr.vishakha@seraphicclinic.com",
        first_name="Vishakha",
        last_name="Agarwal",
        company="Seraphic Cosmesis Dentistry Clinic",
        status="contacted",
    )
    await state.add_prospect(prospect)

    # Create an incoming High-Priority email
    inbound = InboundMessage(
        provider_id="gmail_msg_100",
        from_email="dr.vishakha@seraphicclinic.com",
        subject="Interested in Harvey AI appointment workflow",
        body="Hi Harvey, I would like to know more about the pilot and pricing for our dentistry clinic. Are you available for a brief call?",
    )

    # Initialize service
    service = EmailPrioritizationService(state=state, brain=None)
    result = await service.process_inbound(inbound, auto_draft_high=True)

    assert result["priority"] == PRIORITY_HIGH
    assert result["intent"] in ("interested", "pricing_inquiry")
    assert result["staged_in_outbox"] is True
    assert result["draft_id"] is not None

    # Verify outbox item was stored with status='pending_review' (SAFETY INVARIANT)
    outbox_item = await state.get_outbox_item(result["draft_id"])
    assert outbox_item is not None
    assert outbox_item["status"] == "pending_review"  # MUST NOT be 'sent' or 'approved'
    assert outbox_item["to_email"] == "dr.vishakha@seraphicclinic.com"
    assert "Vishakha" in outbox_item["body"]
    assert "Seraphic Cosmesis" in outbox_item["body"]

    # Verify conversation was saved with priority='high'
    convo = await state.get_conversation(result["conversation_id"])
    assert convo is not None
    assert convo.priority == PRIORITY_HIGH
    assert convo.intent in ("interested", "pricing_inquiry")


@pytest.mark.asyncio
async def test_low_priority_unsubscribe_enforces_opt_out(tmp_path):
    """Verify Low-priority unsubscribe sets prospect status to opted_out and cancels sequence."""
    db_path = str(tmp_path / "test_optout_safety.db")
    state = StateManager(db_path)
    await state.init_db()

    prospect = Prospect(
        id="prospect_test_optout",
        email="optout_user@example.com",
        first_name="Alex",
        company="Sample Practice",
        status="contacted",
    )
    await state.add_prospect(prospect)

    # Add a pending cold email sequence in outbox
    seq_id = await state.add_outbox_item(
        prospect_id=prospect.id,
        kind="sequence",
        to_email=prospect.email,
        subject="Follow-up note",
        body="Checking in...",
        send_at=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        status="pending_review",
    )

    inbound = InboundMessage(
        provider_id="gmail_msg_optout",
        from_email="optout_user@example.com",
        subject="Stop emailing me",
        body="Please unsubscribe and remove me from your list immediately.",
    )

    service = EmailPrioritizationService(state=state, brain=None)
    result = await service.process_inbound(inbound, auto_draft_high=True)

    assert result["priority"] == PRIORITY_LOW
    assert result["intent"] == "unsubscribe"
    assert result["staged_in_outbox"] is False  # No reply draft staged for opt-out

    # Verify prospect was marked opted_out
    updated_p = await state.get_prospect(prospect.id)
    assert updated_p.status == "opted_out"

    # Verify sequence draft was cancelled
    cancelled_item = await state.get_outbox_item(seq_id)
    assert cancelled_item["status"] == "cancelled"


# =====================================================================
# 4. MULTI-FACTOR EVALUATION & MOCK WORKFLOW TESTS
# =====================================================================

def test_multi_factor_sender_decision_maker_detection():
    """Verify decision-maker roles (Director, Owner, Doctor, Chief) elevate sender factor."""
    prospect_exec = Prospect(
        id="p_exec_01",
        email="dr.director@premierdental.com",
        first_name="Sanjay",
        last_name="Verma",
        company="Premier Dental Group",
        title="Managing Director & Chief Dentist",
        status="contacted",
    )
    inbound = InboundMessage(
        provider_id="msg_exec_1",
        from_email="dr.director@premierdental.com",
        subject="Regarding your dental AI overview",
        body="Can you send over details on how this works for our practice network?",
    )
    classified = classify_email_priority(inbound, prospect=prospect_exec)
    assert classified.sender_type == "decision_maker"
    assert classified.factors["sender"]["is_decision_maker"] is True
    assert classified.factors["sender"]["email"] == "dr.director@premierdental.com"


def test_multi_factor_automated_daemon_detection():
    """Verify daemon and automated system addresses are flagged as automated and low priority."""
    inbound = InboundMessage(
        provider_id="daemon_1",
        from_email="mailer-daemon@mock-exchange.net",
        subject="Undelivered Mail Returned to Sender",
        body="This is the mail system at host mail.example.com. User unknown.",
    )
    classified = classify_email_priority(inbound)
    assert classified.priority == PRIORITY_LOW
    assert classified.intent == "bounce"
    assert classified.sender_type == "automated"
    assert classified.should_draft_reply is False


def test_multi_factor_urgency_breakdown():
    """Verify urgency indicators in subject and body are parsed into factors breakdown."""
    inbound = InboundMessage(
        provider_id="urgent_1",
        from_email="partner@dentalsmile.com",
        subject="Urgent: Need pricing proposal today",
        body="We have an executive board meeting tomorrow morning and need your pilot packages immediately.",
    )
    classified = classify_email_priority(inbound)
    assert classified.priority == PRIORITY_HIGH
    assert classified.urgency == "high"
    assert classified.factors["subject"]["has_urgency"] is True
    assert classified.factors["content"]["has_urgency"] is True
    assert classified.factors["business_relevance"] == "high"


@pytest.mark.asyncio
async def test_mock_sample_catalog_and_ingestion(tmp_path):
    """Verify mock sample emails run safely with is_mock=True, staging drafts only for High priority."""
    db_path = str(tmp_path / "test_mock_inbox.db")
    state = StateManager(db_path)
    await state.init_db()

    # 1. High Priority Mock Sample
    high_mock = InboundMessage(
        provider_id="mock_high_01",
        from_email="dr.rohit@delhidentalcare.com",
        subject="[SAMPLE] Interested in Harvey AI appointment automation pilot",
        body="We are reviewing patient intake systems. Could you send pricing and schedule a demo call?",
    )
    service = EmailPrioritizationService(state=state, brain=None)
    res_high = await service.process_inbound(high_mock, is_mock=True)

    assert res_high["is_mock"] is True
    assert res_high["priority"] == PRIORITY_HIGH
    assert res_high["staged_in_outbox"] is True
    assert res_high["draft_id"] is not None

    # Verify drafted outbox item has status pending_review
    draft = await state.get_outbox_item(res_high["draft_id"])
    assert draft["status"] == "pending_review"

    # Verify conversation channel is set to 'mock'
    convo = await state.get_conversation(res_high["conversation_id"])
    assert convo.channel == "mock"
    assert convo.priority == PRIORITY_HIGH

    # 2. Low Priority OOO Mock Sample
    ooo_mock = InboundMessage(
        provider_id="mock_ooo_02",
        from_email="dr.kapoor@smilewell.in",
        subject="[SAMPLE] Automatic reply: Away at Dental Conference",
        body="I am out of the clinic attending a conference. Will be back on Monday.",
    )
    res_ooo = await service.process_inbound(ooo_mock, is_mock=True)
    assert res_ooo["priority"] == PRIORITY_LOW
    assert res_ooo["intent"] == "ooo"
    assert res_ooo["staged_in_outbox"] is False


def test_sorting_ranking_priority_order():
    """Verify ranking order: Needs human/drafts first, then High before Medium, then Low."""
    def pRank(priority):
        p = (priority or '').lower()
        return 0 if p == 'high' else 1 if p == 'medium' else 2

    def rank(c):
        statusRank = 0 if c.get('status') == 'needs_human' else (1 if c.get('draft') else 2)
        return statusRank * 10 + pRank(c.get('priority'))

    convos = [
        {"id": "c_low", "status": "open", "priority": "low"},
        {"id": "c_med", "status": "open", "priority": "medium"},
        {"id": "c_high_open", "status": "open", "priority": "high"},
        {"id": "c_high_needs", "status": "needs_human", "priority": "high", "draft": {"id": "d1"}},
    ]
    sorted_convos = sorted(convos, key=rank)
    assert [c["id"] for c in sorted_convos] == ["c_high_needs", "c_high_open", "c_med", "c_low"]


@pytest.mark.asyncio
async def test_inbox_grouped_and_reply_generation(tmp_path, monkeypatch):
    """Verify GET /api/inbox/grouped and POST /api/inbox/generate-reply endpoints."""
    from harvey.dashboard import app, get_inbox_grouped, generate_inbox_reply
    from starlette.requests import Request

    db_path = str(tmp_path / "test_grouped.db")
    state = StateManager(db_path=db_path)
    await state.init_db()

    monkeypatch.setattr("harvey.dashboard._state", lambda: state)
    monkeypatch.setattr("harvey.dashboard._active_brain", lambda: BASE_CONFIG)

    # Ingest 3 sample messages: 1 high, 1 medium, 1 low
    service = EmailPrioritizationService(state=state, config=BASE_CONFIG, brain=None)

    # High
    inbound_high = InboundMessage(
        provider_id="prov_h",
        from_email="director@dentalhq.com",
        subject="Demo and pricing request",
        body="We want to see a demo tomorrow and need pilot pricing.",
    )
    res_h = await service.process_inbound(inbound_high, auto_draft_high=True)

    # Medium
    inbound_med = InboundMessage(
        provider_id="prov_m",
        from_email="dr.ortho@clinic.com",
        subject="Question on software compatibility",
        body="Can you tell me which software your system integrates with?",
    )
    res_m = await service.process_inbound(inbound_med, auto_draft_high=False)

    # Low
    inbound_low = InboundMessage(
        provider_id="prov_l",
        from_email="frontdesk@smile.com",
        subject="Automatic reply: Out of office",
        body="I am out of the office until next week.",
    )
    res_l = await service.process_inbound(inbound_low, auto_draft_high=False)

    # Call get_inbox_grouped()
    grouped = await get_inbox_grouped()
    assert grouped["success"] is True
    assert len(grouped["high"]) >= 1
    assert len(grouped["medium"]) >= 1
    assert len(grouped["low"]) >= 1
    assert grouped["counts"]["high"] >= 1
    assert grouped["counts"]["medium"] >= 1
    assert grouped["counts"]["low"] >= 1
    assert grouped["counts"]["total"] >= 3
    assert grouped["counts"]["pending_approval"] >= 1

    # Call generate_inbox_reply for the medium conversation
    class DummyRequest:
        def __init__(self, data):
            self._data = data
        async def body(self):
            return b"dummy"
        async def json(self):
            return self._data

    req = DummyRequest({"conversation_id": res_m["conversation_id"]})
    reply_res = await generate_inbox_reply(req)
    assert reply_res["success"] is True
    assert reply_res["draft_id"] is not None
    assert reply_res["status"] == "pending_review"

    # Verify draft in outbox
    draft = await state.get_outbox_item(reply_res["draft_id"])
    assert draft["status"] == "pending_review"
    assert "Harvey" in draft["body"]




@pytest.mark.asyncio
async def test_gmail_oauth_helpers_and_sync_endpoint(tmp_path, monkeypatch):
    """Verify Gmail OAuth helper methods, mock sandbox token, and sync endpoint."""
    from harvey.integrations.gmail import (
        build_auth_url,
        save_mock_token,
        clear_token,
        _load_token,
        TOKEN_FILE,
        READONLY_SCOPE,
    )
    from harvey.dashboard import get_gmail_status, sync_gmail_mailbox
    from starlette.requests import Request

    # 1. build_auth_url
    url = build_auth_url("mock_client_id", "http://localhost:8765", scope=READONLY_SCOPE)
    assert "mock_client_id" in url
    assert "gmail.readonly" in url
    assert "response_type=code" in url

    # 2. Mock token file in isolated tmp directory
    mock_token_path = tmp_path / "gmail_token.json"
    monkeypatch.setattr("harvey.integrations.gmail.TOKEN_FILE", mock_token_path)

    token_data = save_mock_token(email="test-sandbox@clinic.local")
    assert token_data["is_mock"] is True
    assert token_data["scope"] == READONLY_SCOPE

    loaded = _load_token()
    assert loaded is not None
    assert loaded["email"] == "test-sandbox@clinic.local"

    # 3. get_gmail_status
    state = StateManager(db_path=str(tmp_path / "test_gmail_state.db"))
    await state.init_db()
    monkeypatch.setattr("harvey.dashboard._state", lambda: state)
    monkeypatch.setattr("harvey.dashboard._active_brain", lambda: BASE_CONFIG)

    status = await get_gmail_status()
    assert status["configured"] is True
    assert status["status"] == "mock_connected"
    assert status["is_readonly"] is True
    assert status["email_address"] == "test-sandbox@clinic.local"

    # 4. sync_gmail_mailbox
    class DummyRequest:
        async def body(self):
            return b""
        async def json(self):
            return {}

    sync_result = await sync_gmail_mailbox(DummyRequest())
    assert sync_result["success"] is True
    assert sync_result["is_readonly"] is True
    assert sync_result["synced_count"] >= 1
    assert "counts" in sync_result

    # 5. clear_token
    assert clear_token() is True
    assert not mock_token_path.exists()


# =====================================================================
# 5. REGRESSION TESTS: NEWSLETTERS, PROMOTIONS & PROFILE GROUNDING
# =====================================================================

def test_classify_low_priority_manning_promotional_newsletter():
    """Verify Manning Online Pro $199.99 promotional newsletter is classified as LOW priority / marketing."""
    msg = InboundMessage(
        provider_id="manning_promo_01",
        from_email="newsletters@manning.com",
        subject="Manning Online Pro $199.99 Halloween promotion - 50% off all MEAPs!",
        body=(
            "Get Manning Online Pro for only $199.99 during our Halloween promotion!\n"
            "Save 50% on all liveBooks, MEAPs, and audiobooks. Use coupon code HALLOWEEN24 at checkout.\n"
            "Order now at manning.com. To unsubscribe from this newsletter or manage preferences, click here.\n"
            "Manning Publications Co., 20 Baldwin Road, Shelter Island, NY 11964. All rights reserved."
        ),
        headers={"list-unsubscribe": "<https://manning.com/unsub>", "list-id": "manning-news.manning.com"},
    )
    classified = classify_email_priority(msg)
    assert classified.priority == PRIORITY_LOW
    assert classified.intent == "marketing"
    assert classified.should_draft_reply is False
    assert classified.sender_type == "newsletter"
    assert "promotional" in classified.reasoning.lower() or "marketing" in classified.reasoning.lower()


def test_classify_low_priority_discount_coupon_offer():
    """Verify retail discounts, flash sales, and promo codes are classified as LOW priority."""
    msg = InboundMessage(
        provider_id="promo_discount_02",
        from_email="deals@softwarestore.com",
        subject="Flash Sale: 40% discount on annual subscriptions this week!",
        body="Limited time deal: Save 40% when you buy today. Use promo code FLASH40 at checkout. Shop now!",
    )
    classified = classify_email_priority(msg)
    assert classified.priority == PRIORITY_LOW
    assert classified.intent == "marketing"
    assert classified.should_draft_reply is False


def test_classify_low_priority_automated_system_notification():
    """Verify automated system alerts, receipts, and 2FA codes are classified as LOW priority."""
    msg = InboundMessage(
        provider_id="notify_03",
        from_email="notifications@cloudservice.com",
        subject="Security alert: New sign-in detected on your account",
        body="A new login was recorded from Chrome on Windows. If this was you, no action is needed. This is an automated notification, please do not reply.",
    )
    classified = classify_email_priority(msg)
    assert classified.priority == PRIORITY_LOW
    assert classified.intent == "automated"
    assert classified.should_draft_reply is False


@pytest.mark.asyncio
async def test_manning_newsletter_never_stages_sales_reply(tmp_path):
    """Verify that processing the Manning promotional email NEVER stages a sales reply in Outbox."""
    db_path = str(tmp_path / "test_manning_no_reply.db")
    state = StateManager(db_path)
    await state.init_db()

    manning_msg = InboundMessage(
        provider_id="manning_halloween_99",
        from_email="newsletters@manning.com",
        subject="Manning Online Pro $199.99 Halloween promotion",
        body="Manning Online Pro is $199.99 for a limited time. 50% discount on courses. Order now!",
    )

    service = EmailPrioritizationService(state=state, brain=None)
    result = await service.process_inbound(manning_msg, auto_draft_high=True)

    assert result["priority"] == PRIORITY_LOW
    assert result["intent"] == "marketing"
    assert result["staged_in_outbox"] is False
    assert result["draft_id"] is None
    assert result["draft_body"] == ""

    # Verify outbox has zero staged items
    outbox_items = await state.get_outbox()
    assert len(outbox_items) == 0


@pytest.mark.asyncio
async def test_reply_grounding_non_clinic_prospect_never_invents_clinic_or_patient_context(tmp_path):
    """Verify AI reply for non-clinic prospect grounds in recipient's domain and never mentions clinic/patient."""
    db_path = str(tmp_path / "test_non_clinic_grounding.db")
    state = StateManager(db_path)
    await state.init_db()

    # Create a non-clinic prospect (e.g., CleanTech or SaaS company)
    prospect = Prospect(
        id="prospect_tech_01",
        email="marcus@starlightcleantech.com",
        first_name="Marcus",
        last_name="Vance",
        company="Starlight CleanTech Solutions",
        title="VP Operations",
        status="contacted",
    )
    await state.add_prospect(prospect)

    inbound = InboundMessage(
        provider_id="msg_tech_101",
        from_email="marcus@starlightcleantech.com",
        subject="Re: Automated B2B outreach workflows",
        body="Hi Harvey, this looks interesting for our commercial solar operations. What is your pricing and can we schedule a demo call?",
    )

    service = EmailPrioritizationService(state=state, brain=None)
    result = await service.process_inbound(inbound, auto_draft_high=True)

    assert result["priority"] == PRIORITY_HIGH
    assert result["staged_in_outbox"] is True
    assert result["draft_id"] is not None

    draft = await state.get_outbox_item(result["draft_id"])
    draft_body = draft["body"]

    # Grounding verifications:
    assert "Marcus" in draft_body
    assert "Starlight CleanTech" in draft_body
    # MUST NOT invent clinic / patient / dental context
    assert "clinic" not in draft_body.lower()
    assert "patient" not in draft_body.lower()
    assert "practice" not in draft_body.lower()
    assert "dental" not in draft_body.lower()


@pytest.mark.asyncio
async def test_reply_grounding_clinic_prospect_uses_clinic_context(tmp_path):
    """Verify AI reply for genuine dental clinic prospect uses clinic context appropriately."""
    db_path = str(tmp_path / "test_clinic_grounding.db")
    state = StateManager(db_path)
    await state.init_db()

    prospect = Prospect(
        id="prospect_dental_01",
        email="dr.gagan@caredental.in",
        first_name="Gagan",
        last_name="Jain",
        company="Care Dental Clinic",
        title="Chief Dentist",
        status="contacted",
    )
    await state.add_prospect(prospect)

    inbound = InboundMessage(
        provider_id="msg_dental_202",
        from_email="dr.gagan@caredental.in",
        subject="Re: Patient communication workflow",
        body="We are interested in your pilot. Can you send pricing and are you available for a 15-minute call?",
    )

    service = EmailPrioritizationService(state=state, brain=None)
    result = await service.process_inbound(inbound, auto_draft_high=True)

    assert result["priority"] == PRIORITY_HIGH
    assert result["staged_in_outbox"] is True

    draft = await state.get_outbox_item(result["draft_id"])
    draft_body = draft["body"]
    assert "Gagan" in draft_body
    assert "Care Dental Clinic" in draft_body
    assert "patient" in draft_body.lower() or "clinic" in draft_body.lower()


# =====================================================================
# 6. EXTENDED REGRESSION TESTS: PROSPECT QUALIFICATION & REPLY QUALITY
# =====================================================================

@pytest.mark.asyncio
async def test_genuine_demo_request_qualification_and_intent_tailored_reply(tmp_path):
    """Verify SaaS VP Sales requesting demo is qualified as High Fit with demo_request intent and 15-min walkthrough offer."""
    db_path = str(tmp_path / "test_demo_req.db")
    state = StateManager(db_path)
    await state.init_db()

    inbound = InboundMessage(
        provider_id="demo_req_01",
        from_email="sarah.connor@apexcloud.io",
        subject="Demo request for our SDR team",
        body=(
            "Hi Harvey,\n\n"
            "I saw your B2B sales automation platform and would love to see an interactive walkthrough of Harvey. "
            "Can we schedule a demo call with our VP Sales this week?\n\n"
            "Best regards,\nSarah Connor"
        ),
    )

    service = EmailPrioritizationService(state=state, brain=None)
    result = await service.process_inbound(inbound, auto_draft_high=True)

    assert result["priority"] == PRIORITY_HIGH
    assert result["intent"] == "demo_request"
    assert result["staged_in_outbox"] is True
    assert result["draft_id"] is not None

    qual = result["qualification"]
    assert qual["company"] == "Apexcloud"
    assert "Sarah Connor" in qual["contact_name"]
    assert qual["fit_tier"] == "High Fit"
    assert qual["is_actionable"] is True
    assert qual["opportunity_score"] >= 70

    draft = await state.get_outbox_item(result["draft_id"])
    draft_body = draft["body"]
    assert "Sarah" in draft_body
    assert "demo" in draft_body.lower() or "walk you through" in draft_body.lower()
    assert "clinic" not in draft_body.lower()
    assert "patient" not in draft_body.lower()


@pytest.mark.asyncio
async def test_genuine_pricing_inquiry_qualification_and_reply(tmp_path):
    """Verify commercial HVAC GM pricing inquiry qualifies as High Fit and drafts flexible pilot packages reply without clinic context."""
    db_path = str(tmp_path / "test_pricing_req.db")
    state = StateManager(db_path)
    await state.init_db()

    prospect = Prospect(
        id="prospect_hvac_01",
        email="dave.miller@summitmechanical.com",
        first_name="Dave",
        last_name="Miller",
        company="Summit Mechanical Services",
        title="General Manager",
        industry="Commercial Services / HVAC",
        status="contacted",
    )
    await state.add_prospect(prospect)

    inbound = InboundMessage(
        provider_id="pricing_req_02",
        from_email="dave.miller@summitmechanical.com",
        subject="Re: Quote follow-up and proposal acceleration",
        body=(
            "Hi Harvey,\n\n"
            "We are interested in your pilot pricing for our team of 30 technicians. "
            "What is your pricing and cost for onboarding this quarter?\n\n"
            "Thanks,\nDave Miller\nGeneral Manager"
        ),
    )

    service = EmailPrioritizationService(state=state, brain=None)
    result = await service.process_inbound(inbound, auto_draft_high=True)

    assert result["priority"] == PRIORITY_HIGH
    assert result["intent"] == "pricing_inquiry"
    assert result["staged_in_outbox"] is True

    qual = result["qualification"]
    assert qual["company"] == "Summit Mechanical Services"
    assert qual["contact_name"] == "Dave Miller"
    assert qual["title"] == "General Manager"
    assert qual["fit_tier"] == "High Fit"
    assert qual["is_actionable"] is True

    draft = await state.get_outbox_item(result["draft_id"])
    draft_body = draft["body"]
    assert "Dave" in draft_body
    assert "Summit Mechanical" in draft_body
    assert "pricing" in draft_body.lower() or "pilot packages" in draft_body.lower()
    # Zero clinic hallucination
    assert "clinic" not in draft_body.lower()
    assert "patient" not in draft_body.lower()


@pytest.mark.asyncio
async def test_genuine_meeting_scheduling_request(tmp_path):
    """Verify meeting scheduling request is classified as meeting_request and drafts availability offer."""
    db_path = str(tmp_path / "test_meeting_req.db")
    state = StateManager(db_path)
    await state.init_db()

    inbound = InboundMessage(
        provider_id="meeting_req_03",
        from_email="rachel.green@ralphlaurencorp.com",
        subject="Let's schedule a call",
        body=(
            "Hi Harvey,\n\n"
            "I reviewed your note with our team. Can we schedule a meeting this Thursday or Friday to discuss further? "
            "Please send over your availability.\n\n"
            "Best,\nRachel Green"
        ),
    )

    service = EmailPrioritizationService(state=state, brain=None)
    result = await service.process_inbound(inbound, auto_draft_high=True)

    assert result["priority"] == PRIORITY_HIGH
    assert result["intent"] == "meeting_request"
    assert result["staged_in_outbox"] is True

    draft = await state.get_outbox_item(result["draft_id"])
    draft_body = draft["body"]
    assert "Rachel" in draft_body
    assert "availability" in draft_body.lower() or "connect" in draft_body.lower()


@pytest.mark.asyncio
async def test_vendor_pitch_seo_agency_rejected_as_low_priority(tmp_path):
    """Verify third-party vendor pitches (SEO services, offshore dev) are classified as LOW / vendor_pitch with no reply draft."""
    db_path = str(tmp_path / "test_vendor_pitch.db")
    state = StateManager(db_path)
    await state.init_db()

    inbound = InboundMessage(
        provider_id="vendor_seo_01",
        from_email="alex@growthrankingsagency.com",
        subject="Quick question regarding your Google ranking and backlinks",
        body=(
            "Hi there,\n\n"
            "I noticed Harvey AI could improve its organic traffic growth. Our agency helps software companies "
            "with top-tier SEO services, backlinks, and guest posts to rank on Google page 1.\n"
            "Can we schedule a 10-minute demo call this week to show our portfolio?\n\n"
            "Regards,\nAlex"
        ),
    )

    classified = classify_email_priority(inbound)
    assert classified.priority == PRIORITY_LOW
    assert classified.intent == "vendor_pitch"
    assert classified.should_draft_reply is False

    service = EmailPrioritizationService(state=state, brain=None)
    result = await service.process_inbound(inbound, auto_draft_high=True)
    assert result["priority"] == PRIORITY_LOW
    assert result["intent"] == "vendor_pitch"
    assert result["staged_in_outbox"] is False
    assert result["draft_id"] is None

    outbox = await state.get_outbox()
    assert len(outbox) == 0


@pytest.mark.asyncio
async def test_insufficient_context_vague_message_skips_draft(tmp_path):
    """Verify trivial acknowledgment message without clear intent skips generating a misleading draft."""
    db_path = str(tmp_path / "test_vague_msg.db")
    state = StateManager(db_path)
    await state.init_db()

    inbound = InboundMessage(
        provider_id="vague_01",
        from_email="user@clientcompany.com",
        subject="Re: Update",
        body="Ok thanks",
    )

    classified = classify_email_priority(inbound)
    # Trivial text should not be marked HIGH priority
    assert classified.priority != PRIORITY_HIGH
    assert classified.should_draft_reply is False

    service = EmailPrioritizationService(state=state, brain=None)
    result = await service.process_inbound(inbound, auto_draft_high=True)
    assert result["staged_in_outbox"] is False
    assert result["draft_id"] is None


def test_factual_qualification_strict_honesty_unverified_fields():
    """Verify that unknown details (e.g. generic personal email with no title or CRM record) are marked Unknown/Unspecified."""
    inbound = InboundMessage(
        provider_id="unknown_lead_01",
        from_email="alex1985@gmail.com",
        subject="Hello",
        body="Can you tell me more about what your tool does?",
    )

    classified = classify_email_priority(inbound)
    qual = classified.qualification
    assert qual is not None
    # Generic personal email domain: company is Unknown
    assert qual.company == "Unknown"
    # No title mentioned: title is Unknown
    assert qual.title == "Unknown"
    assert qual.industry == "Unknown"
    assert any("unverified" in s.lower() for s in qual.unknown_signals)
    assert qual.fit_tier in ("Needs Discovery", "Moderate Fit")
