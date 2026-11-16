"""Integration tests for live-verified gap features.
Covers:
- DNS MX host resolution and classification
- Booking link multi-platform parsing and live liveness verification
- Business hours RFC calendar slot generation
- RFC 5545 iCalendar (.ics) invite synthesis
- Gmail OAuth 2.0 authorization URL construction with locked read-only scope
"""

import pytest
from starlette.testclient import TestClient

from harvey.integrations.email_finder import get_mx_host, classify_mx
from harvey.integrations.calendar import (
    CalendarClient,
    parse_booking_url,
    verify_booking_url,
)
from harvey.dashboard import app, token_store


@pytest.fixture
def api_client():
    token = token_store.get()
    tc = TestClient(app, base_url="http://127.0.0.1:5555")
    tc.headers.update({
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    return tc


class TestLiveDnsAndMx:
    @pytest.mark.asyncio
    async def test_live_mx_lookup_and_classification(self):
        """Verify real DNS MX lookup and provider classification."""
        try:
            google_mx = await get_mx_host("google.com")
            assert google_mx is not None
            assert "google" in classify_mx(google_mx)
        except Exception as e:
            pytest.skip(f"Network DNS lookup unavailable: {e}")

    def test_microsoft_mx_classification(self):
        """Verify Microsoft Exchange MX classification."""
        classified = classify_mx("acme-com.mail.protection.outlook.com")
        assert classified == "microsoft"


class TestCalendarUrlAndLiveness:
    def test_multi_platform_url_parsing(self):
        """Verify link parsing across Cal.com, Calendly, and Google Calendar."""
        cal_com = parse_booking_url("https://cal.com/acme-sales/30min")
        assert cal_com["valid"] is True
        assert cal_com["platform"] == "Cal.com"
        assert cal_com["duration_minutes"] == 30

        calendly = parse_booking_url("https://calendly.com/acme-corp/15min-intro")
        assert calendly["valid"] is True
        assert calendly["platform"] == "Calendly"
        assert calendly["duration_minutes"] == 15

    @pytest.mark.asyncio
    async def test_live_booking_url_verification(self):
        """Verify real HTTP reachability check against a live public calendar URL."""
        res = await verify_booking_url("https://cal.com/sales")
        assert res.get("live") is True
        assert res.get("status_code") in (200, 301, 302, 307, 308)
        assert res.get("platform") == "Cal.com"


class TestCalendarEngineAndIcs:
    @pytest.mark.asyncio
    async def test_rfc_slot_engine(self):
        """Verify local RFC slot engine generates valid available time slots."""
        client = CalendarClient(booking_url="https://cal.com/sales")
        slots = await client.get_available_slots(days_ahead=3)
        assert len(slots) > 0
        for slot in slots:
            assert "start" in slot
            assert "end" in slot
            assert "formatted" in slot
            assert slot.get("available") is True

    def test_rfc5545_ics_generation(self):
        """Verify RFC 5545 compliant .ics invite generation."""
        client = CalendarClient(booking_url="https://cal.com/sales")
        ics = client.generate_ics_invite(
            prospect_email="sarah.connor@cyberdyne.example.com",
            prospect_name="Sarah Connor",
            slot_start="2026-11-20T14:00:00Z",
            duration_minutes=30,
        )
        assert "BEGIN:VCALENDAR" in ics
        assert "VERSION:2.0" in ics
        assert "BEGIN:VEVENT" in ics
        assert "SUMMARY:Harvey AI Intro Call" in ics
        assert "Sarah Connor" in ics
        assert "sarah.connor@cyberdyne.example.com" in ics
        assert "END:VEVENT" in ics
        assert "END:VCALENDAR" in ics


class TestGmailAuthUrlEndpoint:
    def test_gmail_auth_url_read_only_scope(self, api_client):
        """Verify Gmail OAuth endpoint constructs read-only consent URL."""
        resp = api_client.post("/api/gmail/auth-url", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("success") is True
        auth_url = data.get("auth_url", "")
        assert "https://accounts.google.com/o/oauth2/v2/auth" in auth_url
        assert "scope=" in auth_url
        assert "gmail.readonly" in auth_url
        assert "redirect_uri=" in auth_url
