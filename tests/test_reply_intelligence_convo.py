"""Unit and API integration tests for Reply Intelligence & Suggested Reply Generation."""

import uuid
import pytest
from starlette.testclient import TestClient

from harvey.dashboard import app, token_store, _state
from harvey.models.conversation import Conversation
from harvey.models.prospect import Prospect


@pytest.fixture
def client():
    token = token_store.get()
    tc = TestClient(app, base_url="http://127.0.0.1:5555")
    tc.headers.update({"Authorization": f"Bearer {token}"})
    return tc


@pytest.mark.asyncio
class TestReplyIntelligenceAndSuggestedDrafts:
    async def test_generate_suggested_reply_for_interested_lead(self, client):
        state = _state()
        await state.init_db()

        uid = uuid.uuid4().hex[:8]
        # 1. Create a test prospect
        prospect = Prospect(
            id=f"test-prospect-{uid}",
            first_name="Dr. Vikram",
            last_name="Malhotra",
            email=f"vikram.{uid}@delhidentalgroup.example.com",
            company="Delhi Dental Group",
            title="Managing Director",
            score=85,
        )
        await state.add_prospect(prospect)

        # 2. Create an inbound conversation with interested intent
        convo = Conversation(
            id=f"test-convo-{uid}",
            prospect_id=prospect.id,
            intent="interested",
            stage="initial_outreach",
            status="needs_human",
            thread=[{
                "sender": "prospect",
                "content": "Hi Harvey team, we are very interested in your patient booking triage. How quickly can we get onboarded and what are the pricing tiers?",
                "timestamp": "2026-11-13T10:00:00Z",
            }],
        )
        await state.add_conversation(convo)

        # 3. Call generate-reply endpoint
        resp = client.post("/api/inbox/generate-reply", json={"conversation_id": convo.id})
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("success") is True
        assert "draft_id" in data
        assert "draft_body" in data
        assert "pricing" in data["draft_body"].lower() or "triage" in data["draft_body"].lower() or "harvey" in data["draft_body"].lower()

        # 4. Verify draft was staged into outbox as pending_review
        outbox_items = await state.get_outbox(status="pending_review")
        matching = [item for item in outbox_items if item.get("id") == data["draft_id"] or item.get("conversation_id") == convo.id]
        assert len(matching) > 0
        staged = matching[0]
        assert staged["status"] == "pending_review"
        assert staged["to_email"] == prospect.email
        assert staged["sent_at"] is None

    @pytest.mark.asyncio
    async def test_prevent_reply_generation_for_optout_or_bounce(self, client):
        state = _state()
        await state.init_db()
        uid = uuid.uuid4().hex[:8]
        prospect = Prospect(
            id=f"prospect-opted-{uid}",
            first_name="Dr. Vikram",
            last_name="Malhotra",
            email=f"vikram.{uid}@delhidentalgroup.example.com",
            company="Delhi Dental Group",
            title="Managing Director",
            status="opted_out",
        )
        await state.add_prospect(prospect)
        convo = Conversation(
            id=f"test-optout-{uid}",
            prospect_id=prospect.id,
            intent="unsubscribe",
            stage="initial_outreach",
            status="needs_human",
            thread=[{"sender": "prospect", "content": "Unsubscribe please, remove me", "timestamp": "2026-11-13T10:00:00Z"}],
        )
        await state.add_conversation(convo)

        resp = client.post("/api/inbox/generate-reply", json={"conversation_id": convo.id})
        assert resp.status_code == 400
        assert "safety invariant" in resp.json().get("error", "").lower()

    @pytest.mark.asyncio
    async def test_conversation_status_update(self, client):
        state = _state()
        await state.init_db()
        uid = uuid.uuid4().hex[:8]
        prospect = Prospect(
            id=f"prospect-status-{uid}",
            first_name="Dr. Vikram",
            last_name="Malhotra",
            email=f"vikram.{uid}@delhidentalgroup.example.com",
            company="Delhi Dental Group",
            title="Managing Director",
            status="new",
        )
        await state.add_prospect(prospect)
        convo = Conversation(
            id=f"test-status-{uid}",
            prospect_id=prospect.id,
            intent="general",
            stage="initial_outreach",
            status="open",
            thread=[],
        )
        await state.add_conversation(convo)

        resp = client.post(f"/api/conversations/{convo.id}/status", json={"status": "closed"})
        assert resp.status_code == 200
        assert resp.json().get("success") is True

        resp_reopen = client.post(f"/api/conversations/{convo.id}/status", json={"status": "open"})
        assert resp_reopen.status_code == 200
        assert resp_reopen.json().get("success") is True
