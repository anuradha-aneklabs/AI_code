"""Unit and API integration tests for Calendar & Meeting Booking integration."""

import pytest
from starlette.testclient import TestClient

from harvey.integrations.calendar import (
    CalendarClient,
    parse_booking_url,
    verify_booking_url,
)
from harvey.dashboard import app, token_store


@pytest.fixture
def client():
    token = token_store.get()
    tc = TestClient(app, base_url="http://127.0.0.1:5555")
    tc.headers.update({"Authorization": f"Bearer {token}"})
    return tc


class TestCalendarUrlParsing:
    def test_cal_com_url(self):
        info = parse_booking_url("https://cal.com/harvey-sales/15min")
        assert info["valid"] is True
        assert info["platform"] == "Cal.com"
        assert info["username"] == "harvey-sales"
        assert info["event_type"] == "15min"
        assert info["duration_minutes"] == 15

    def test_calendly_url(self):
        info = parse_booking_url("https://calendly.com/founder-jane/30min-discovery")
        assert info["valid"] is True
        assert info["platform"] == "Calendly"
        assert info["username"] == "founder-jane"
        assert info["duration_minutes"] == 30

    def test_google_calendar_url(self):
        info = parse_booking_url("https://calendar.google.com/calendar/appointments/schedules/AcZssZ123")
        assert info["valid"] is True
        assert info["platform"] == "Google Calendar"

    def test_empty_or_invalid_url(self):
        info = parse_booking_url("")
        assert info["valid"] is False


class TestCalendarSlotsAndBooking:
    @pytest.mark.asyncio
    async def test_get_available_slots(self):
        client = CalendarClient(booking_url="https://cal.com/harvey/15min")
        slots = await client.get_available_slots(days_ahead=5)
        assert len(slots) > 0
        for s in slots:
            assert "start" in s
            assert "end" in s
            assert "formatted" in s
            assert s["duration_minutes"] == 15
            assert s["available"] is True

    def test_generate_ics_invite(self):
        client = CalendarClient(booking_url="https://cal.com/harvey/15min")
        ics = client.generate_ics_invite(
            prospect_email="dr.amit@careclinic.example.com",
            prospect_name="Dr. Amit Rawat",
            slot_start="2026-11-16T10:00:00Z",
            duration_minutes=15,
        )
        assert "BEGIN:VCALENDAR" in ics
        assert "BEGIN:VEVENT" in ics
        assert "Dr. Amit Rawat" in ics
        assert "dr.amit@careclinic.example.com" in ics
        assert "END:VCALENDAR" in ics


class TestCalendarApiEndpoints:
    def test_calendar_status_endpoint(self, client):
        resp = client.get("/api/calendar/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("success") is True
        assert "platform" in data
        assert "meeting_duration" in data

    def test_calendar_verify_link_endpoint(self, client):
        resp = client.post("/api/calendar/verify-link", json={"url": "https://cal.com/demo/15m"})
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("success") is True
        assert data.get("platform") == "Cal.com"

    def test_calendar_slots_endpoint(self, client):
        resp = client.get("/api/calendar/slots?days=3")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("success") is True
        assert "slots" in data
        assert data.get("count", 0) > 0

    def test_calendar_book_endpoint(self, client):
        payload = {
            "prospect_email": "booking.test@dentalpilot.example.com",
            "prospect_name": "Dr. Booking Test",
            "slot": {
                "start": "2026-11-17T11:30:00Z",
                "end": "2026-11-17T11:45:00Z",
                "formatted": "Tuesday, Nov 17 at 11:30 AM UTC",
                "duration_minutes": 15,
            },
            "notes": "Testing calendar booking endpoint",
        }
        resp = client.post("/api/calendar/book", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("success") is True
        assert "ics_invite" in data
        assert "BEGIN:VCALENDAR" in data["ics_invite"]

    def test_free_google_and_outlook_calendar_links(self):
        from datetime import datetime, timezone
        from harvey.integrations.calendar import (
            generate_google_calendar_url,
            generate_outlook_calendar_url,
        )

        dt = datetime(2026, 11, 20, 14, 0, tzinfo=timezone.utc)
        g_url = generate_google_calendar_url("Harvey AI Discovery", dt, 15, "Intro discussion")
        assert "calendar.google.com/calendar/render" in g_url
        assert "action=TEMPLATE" in g_url
        assert "Harvey+AI+Discovery" in g_url or "Harvey%20AI%20Discovery" in g_url

        o_url = generate_outlook_calendar_url("Harvey AI Discovery", dt, 15, "Intro discussion")
        assert "outlook.live.com/calendar/0/deeplink/compose" in o_url
        assert "Harvey+AI+Discovery" in o_url or "Harvey%20AI%20Discovery" in o_url

        client = CalendarClient()
        links = client.generate_free_meeting_links("Demo Call", dt, 20)
        assert "google_calendar_url" in links
        assert "outlook_calendar_url" in links
        assert "ics_invite" in links
        assert "BEGIN:VCALENDAR" in links["ics_invite"]
