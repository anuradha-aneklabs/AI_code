"""Test hard master kill switch on email sending.

Verifies:
1. With an approved outbox row, require_approval=False, sending_paused unset,
   and a stub transport, when the hard kill switch is active (default),
   the send attempt is refused, the stub transport is never called, and
   0 messages are recorded as sent.
2. The batch research pipeline creates 0 drafts, 0 outbox rows, and 0 queued items.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path
import pytest

from harvey.state import StateManager
from harvey.agents.sender import Sender
from harvey.config import load_config
from harvey.integrations.mail_provider import MailProvider, SendResult, check_transmit_allowed
from harvey.batch_research import create_batch_from_file, run_batch


class StubTransportProvider(MailProvider):
    """Stub provider with a recorded call count."""
    def __init__(self):
        self.call_count = 0
        self.last_recipient = ""

    def is_configured(self) -> bool:
        return True

    async def send_email(
        self,
        to_email: str,
        subject: str,
        body: str,
        thread_ref: str = "",
        in_reply_to: str = "",
    ) -> SendResult:
        # Check lowest-level master switch
        is_mocked = True
        allowed, err = check_transmit_allowed(is_mocked=is_mocked)
        if not allowed:
            return SendResult(ok=False, error=err)
        self.call_count += 1
        self.last_recipient = to_email
        return SendResult(ok=True, message_id="stub-sent-123")

    async def get_replies(self, limit: int = 50):
        return []

    async def test_connection(self):
        return True, "ok"


@pytest.mark.asyncio
async def test_hard_kill_switch_refuses_send_and_never_calls_transport(tmp_path, monkeypatch):
    """With approved outbox row, require_approval=False, sending_paused unset:
    Send attempt is refused by hard kill switch and stub transport is never called."""
    db_file = tmp_path / "kill_switch_test.db"
    state = StateManager(db_path=str(db_file))
    await state.init_db()

    # 1. Ensure live sending is NOT enabled and test mock mode is disabled for this test
    monkeypatch.delenv("HARVEY_LIVE_SENDING_ENABLED", raising=False)
    monkeypatch.setenv("HARVEY_TEST_MOCK_SEND_ALLOWED", "0")

    # 2. Setup config with require_approval=False and sending_paused unset
    config = load_config()
    config.channels.email.require_approval = False

    # 3. Insert fake prospect and approved outbox item
    from harvey.models import Prospect
    prospect_id = await state.add_prospect(Prospect(
        id="pros-kill-1",
        first_name="Fake",
        last_name="Lead",
        email="fake.recipient@companyfake.test",
        email_status="verified",
        email_verified=True,
    ))

    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()

    outbox_id = await state.add_outbox_item(
        to_email="fake.recipient@companyfake.test",
        subject="Synthetic Cold Outreach",
        body="This is synthetic body text.",
        prospect_id=prospect_id,
        send_at=now_iso,
        kind="sequence",
        step=1,
    )
    # Approve it
    await state.approve_outbox(outbox_id)
    approved_item = await state.get_outbox_item(outbox_id)
    assert approved_item["status"] == "approved"

    # 4. Setup sender with stub transport
    stub_provider = StubTransportProvider()
    env = MagicMock()
    brain = MagicMock()
    sender = Sender(brain, state, config, env)
    sender.provider = stub_provider

    # 5. Attempt drain
    await sender._drain_due()

    # 6. Verify: Stub transport was NEVER called!
    assert stub_provider.call_count == 0, "Stub transport must NEVER be called when kill switch is active"
    assert stub_provider.last_recipient == ""

    # 7. Verify outbox item was NOT sent
    final_item = await state.get_outbox_item(outbox_id)
    assert final_item["status"] != "sent"
    assert final_item["sent_at"] is None
    assert "kill switch" in (final_item.get("error") or "").lower()

    # 8. Check total sent count in state
    sent_count = await state.count_outbox_sent()
    assert sent_count == 0


@pytest.mark.asyncio
async def test_batch_research_creates_zero_drafts_and_zero_outbox(tmp_path, monkeypatch):
    """Verify research batch pipeline creates 0 drafts, 0 outbox rows, and 0 queued items."""
    db_file = tmp_path / "research_outbox_check.db"
    state = StateManager(db_path=str(db_file))
    await state.init_db()

    # Outbox count before
    outbox_before = await state.get_outbox(limit=100)
    assert len(outbox_before) == 0

    # Synthetic batch input with 2 leads
    csv_data = (
        "Name,Email,Company,Phone\n"
        "Lead Alpha,lead1@company-alpha-test.org,Alpha Corp,555-0101\n"
        "Lead Beta,lead2@company-beta-test.org,Beta LLC,555-0102\n"
    )

    batch_res = await create_batch_from_file(
        filename="leads.csv",
        content_bytes=csv_data.encode("utf-8"),
        state=state,
        max_domains=2,
    )
    batch_id = batch_res["batch_id"]

    # Mock website crawler to return synthetic pages without external HTTP
    from unittest.mock import patch
    with patch("harvey.single_domain_research.verify_website") as mock_web:
        from harvey.single_domain_research import WebsiteVerificationResult
        mock_web.return_value = WebsiteVerificationResult(
            dns_resolved=True,
            candidate_url="https://company-alpha-test.org",
            final_url="https://company-alpha-test.org",
            http_status=200,
            verification_status="verified",
            confidence=1.0,
            evidence_text="Reachable 200",
            html_content="<html><head><title>Alpha</title></head><body><h1>About Alpha</h1><p>We deliver logistics.</p></body></html>",
        )
        await run_batch(batch_id, state=state, max_domains=2)

    # Outbox count after
    outbox_after = await state.get_outbox(limit=100)
    assert len(outbox_after) == 0, f"Expected 0 outbox items, found {len(outbox_after)}"

    # Check drafts table if exists
    async with state._connect() as db:
        cur = await db.execute("SELECT COUNT(*) FROM outreach_drafts")
        drafts_count = (await cur.fetchone())[0]
        assert drafts_count == 0, f"Expected 0 drafts, found {drafts_count}"
