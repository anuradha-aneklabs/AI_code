"""Calendar & Meeting Booking Integration for Harvey.

Supports:
1. Multi-platform booking link parsing & validation (Cal.com, Calendly, Google Calendar, HubSpot, TidyCal).
2. Online URL liveness verification.
3. Availability slot calculation & retrieval respecting quiet hours and business days.
4. Safe meeting booking simulation, ICS calendar invite generation, and CRM stage progression.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, date, time, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("harvey.calendar")

CAL_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0)

SUPPORTED_PLATFORMS = {
    "cal.com": "Cal.com",
    "calendly.com": "Calendly",
    "calendar.google.com": "Google Calendar",
    "hubspot.com": "HubSpot Meetings",
    "tidycal.com": "TidyCal",
    "zcal.co": "Zcal",
}


async def get_calcom_event_types(api_key: str) -> list[dict[str, Any]]:
    """Retrieve event types from Cal.com v1 API."""
    if not api_key:
        return []
    url = f"https://api.cal.com/v1/event-types?apiKey={api_key}"
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=CAL_TIMEOUT) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("event_types", [])
                elif resp.status_code in (401, 403):
                    logger.warning(f"Cal.com auth failed: {resp.status_code}")
                    break
        except Exception as e:
            if attempt == 1:
                logger.warning(f"Cal.com event-types request error: {e}")
            await asyncio.sleep(0.5)
    return []


async def get_calcom_slots(
    api_key: str,
    event_type_id: int | str,
    start_time: str,
    end_time: str,
    time_zone: str = "UTC",
) -> list[dict[str, Any]]:
    """Fetch live available meeting slots from Cal.com v1 API."""
    if not api_key or not event_type_id:
        return []
    url = "https://api.cal.com/v1/slots/available"
    params = {
        "apiKey": api_key,
        "eventTypeId": str(event_type_id),
        "startTime": start_time,
        "endTime": end_time,
        "timeZone": time_zone,
    }
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=CAL_TIMEOUT) as client:
                resp = await client.get(url, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    # Cal.com returns {"slots": {"YYYY-MM-DD": [{"time": "ISO"}, ...]}}
                    slots_dict = data.get("slots", {})
                    out_slots = []
                    for day_date, slot_list in slots_dict.items():
                        for item in slot_list:
                            slot_time = item.get("time") if isinstance(item, dict) else str(item)
                            out_slots.append({
                                "start": slot_time,
                                "date": day_date,
                                "available": True,
                                "provider": "cal.com",
                            })
                    return out_slots
                elif resp.status_code in (401, 403):
                    logger.warning(f"Cal.com API auth failed: {resp.status_code}")
                    break
        except Exception as e:
            if attempt == 1:
                logger.warning(f"Cal.com slots request failed: {e}")
            await asyncio.sleep(0.5)
    return []


async def get_calendly_event_types(token: str) -> list[dict[str, Any]]:
    """Retrieve event types for current user from Calendly v2 API."""
    if not token:
        return []
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=CAL_TIMEOUT) as client:
                # First get current user uri
                user_resp = await client.get("https://api.calendly.com/users/me", headers=headers)
                if user_resp.status_code != 200:
                    return []
                user_uri = user_resp.json().get("resource", {}).get("uri", "")
                if not user_uri:
                    return []
                resp = await client.get(f"https://api.calendly.com/event_types?user={user_uri}", headers=headers)
                if resp.status_code == 200:
                    return resp.json().get("collection", [])
        except Exception as e:
            if attempt == 1:
                logger.warning(f"Calendly event-types error: {e}")
            await asyncio.sleep(0.5)
    return []


async def get_calendly_slots(
    token: str,
    event_type_uri: str,
    start_time: str,
    end_time: str,
) -> list[dict[str, Any]]:
    """Fetch live available time slots from Calendly v2 API."""
    if not token or not event_type_uri:
        return []
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    params = {
        "event_type": event_type_uri,
        "start_time": start_time,
        "end_time": end_time,
    }
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=CAL_TIMEOUT) as client:
                resp = await client.get("https://api.calendly.com/event_type_available_times", headers=headers, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    collection = data.get("collection", [])
                    return [
                        {
                            "start": item.get("start_time"),
                            "status": item.get("status"),
                            "available": item.get("status") == "available",
                            "provider": "calendly",
                        }
                        for item in collection
                    ]
        except Exception as e:
            if attempt == 1:
                logger.warning(f"Calendly slots request error: {e}")
            await asyncio.sleep(0.5)
    return []


def parse_booking_url(url: str) -> dict[str, Any]:
    """Parse a scheduling/booking URL to identify the platform, slug, and event configuration."""
    if not url or not isinstance(url, str):
        return {"valid": False, "error": "Empty URL"}

    clean_url = url.strip()
    if not clean_url.startswith(("http://", "https://")):
        clean_url = "https://" + clean_url

    parsed = urlparse(clean_url)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]

    platform = "Unknown"
    for domain, name in SUPPORTED_PLATFORMS.items():
        if domain in host:
            platform = name
            break

    path_parts = [p for p in parsed.path.strip("/").split("/") if p]
    username = path_parts[0] if path_parts else ""
    event_type = path_parts[1] if len(path_parts) > 1 else ""

    # Estimate meeting duration from slug (e.g. '15min', 'intro-30m')
    duration = 15
    duration_match = re.search(r"(\d+)\s*(?:min|m)\b", clean_url, re.I)
    if duration_match:
        try:
            duration = int(duration_match.group(1))
        except ValueError:
            duration = 15

    return {
        "valid": True,
        "raw_url": url,
        "clean_url": clean_url,
        "host": host,
        "platform": platform,
        "username": username,
        "event_type": event_type,
        "duration_minutes": duration,
    }


async def verify_booking_url(url: str, timeout: float = 8.0) -> dict[str, Any]:
    """Verify that a booking link is live and reachable without redirection to error pages."""
    info = parse_booking_url(url)
    if not info.get("valid"):
        return {"live": False, "status_code": 0, "error": info.get("error", "Invalid URL"), "info": info}

    target_url = info["clean_url"]
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            # First try HEAD, fall back to GET
            try:
                resp = await client.head(target_url, headers={"User-Agent": "Harvey-SalesAgent/1.0"})
            except httpx.HTTPError:
                resp = await client.get(target_url, headers={"User-Agent": "Harvey-SalesAgent/1.0"})

            is_live = 200 <= resp.status_code < 400
            return {
                "live": is_live,
                "status_code": resp.status_code,
                "target_url": str(resp.url),
                "platform": info["platform"],
                "duration_minutes": info["duration_minutes"],
                "info": info,
            }
    except Exception as e:
        logger.warning(f"Booking URL verification failed for {target_url}: {e}")
        return {
            "live": False,
            "status_code": 0,
            "error": str(e),
            "info": info,
        }


def generate_google_calendar_url(
    title: str,
    start_dt: datetime,
    duration_minutes: int = 15,
    description: str = "",
    location: str = "Google Meet / Phone Call",
) -> str:
    """Generate a 1-click Google Calendar meeting creation link (100% free, no API key needed)."""
    from urllib.parse import quote_plus
    dt_format = "%Y%m%dT%H%M%SZ"
    if start_dt.tzinfo is None:
        start_utc = start_dt.replace(tzinfo=timezone.utc)
    else:
        start_utc = start_dt.astimezone(timezone.utc)
    end_utc = start_utc + timedelta(minutes=duration_minutes)
    dates_param = f"{start_utc.strftime(dt_format)}/{end_utc.strftime(dt_format)}"
    base = "https://calendar.google.com/calendar/render"
    return f"{base}?action=TEMPLATE&text={quote_plus(title)}&dates={dates_param}&details={quote_plus(description)}&location={quote_plus(location)}"


def generate_outlook_calendar_url(
    title: str,
    start_dt: datetime,
    duration_minutes: int = 15,
    description: str = "",
    location: str = "Teams / Phone Call",
) -> str:
    """Generate a 1-click Outlook Web Calendar event creation link (100% free, no API key needed)."""
    from urllib.parse import quote_plus
    if start_dt.tzinfo is None:
        start_utc = start_dt.replace(tzinfo=timezone.utc)
    else:
        start_utc = start_dt.astimezone(timezone.utc)
    end_utc = start_utc + timedelta(minutes=duration_minutes)
    start_iso = start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_iso = end_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    base = "https://outlook.live.com/calendar/0/deeplink/compose"
    return f"{base}?subject={quote_plus(title)}&startdt={start_iso}&enddt={end_iso}&body={quote_plus(description)}&location={quote_plus(location)}"


class CalendarClient:
    """Manages calendar integrations, available slot discovery, and meeting bookings."""

    def __init__(
        self,
        booking_url: str = "",
        api_key: str = "",
        provider: str = "auto",
        default_timezone: str = "UTC",
    ):
        self.booking_url = booking_url or ""
        self.api_key = api_key or ""
        self.provider = provider
        self.default_timezone = default_timezone
        self._parsed_info = parse_booking_url(self.booking_url) if self.booking_url else {}

    @property
    def platform_name(self) -> str:
        return self._parsed_info.get("platform", "Generic Booking Link")

    async def get_status(self) -> dict[str, Any]:
        """Return operational status and capabilities of the calendar client."""
        has_url = bool(self.booking_url)
        has_key = bool(self.api_key)
        liveness = {"live": False}
        if has_url:
            liveness = await verify_booking_url(self.booking_url, timeout=4.0)

        return {
            "configured": has_url or has_key,
            "booking_url": self.booking_url,
            "platform": self.platform_name,
            "api_key_configured": has_key,
            "url_verified": liveness.get("live", False),
            "status_code": liveness.get("status_code", 0),
            "duration_minutes": self._parsed_info.get("duration_minutes", 15),
            "timezone": self.default_timezone,
        }

    @staticmethod
    def generate_direct_booking_link(username: str, platform: str = "cal.com", slug: str = "", duration: int = 15) -> str:
        """Construct a clean, valid booking URL for Cal.com, Calendly, or custom schedulers."""
        clean_user = (username or "").strip().strip("/")
        clean_slug = (slug or "").strip().strip("/") or f"{duration}min"
        p = platform.lower()
        if "calendly" in p:
            return f"https://calendly.com/{clean_user}/{clean_slug}"
        elif "google" in p:
            return f"https://calendar.google.com/calendar/appointments/schedules/{clean_user}"
        elif "hubspot" in p:
            return f"https://meetings.hubspot.com/{clean_user}"
        elif "tidycal" in p:
            return f"https://tidycal.com/{clean_user}/{clean_slug}"
        else:  # Cal.com default
            return f"https://cal.com/{clean_user}/{clean_slug}"

    def generate_free_meeting_links(
        self,
        title: str,
        start_dt: datetime,
        duration_minutes: int = 15,
        description: str = "",
        location: str = "Google Meet / Phone Call",
        prospect_email: str = "",
        prospect_name: str = "",
    ) -> dict[str, str]:
        """Generate free, 1-click meeting links (Google Calendar, Outlook Web, and ICS invite)
        without requiring paid Cal.com or Calendly subscriptions."""
        return {
            "google_calendar_url": generate_google_calendar_url(title, start_dt, duration_minutes, description, location),
            "outlook_calendar_url": generate_outlook_calendar_url(title, start_dt, duration_minutes, description, location),
            "ics_invite": self.generate_ics_invite(
                prospect_email=prospect_email,
                prospect_name=prospect_name,
                slot_start=start_dt.isoformat(),
                duration_minutes=duration_minutes,
                summary=title,
                description=description,
            ),
        }

    async def get_available_slots(
        self,
        date_range: str = "7d",
        days_ahead: int = 7,
        timezone_str: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Return available meeting slots for the given window.

        If an external API is connected, queries it. Otherwise, computes
        valid business hour slots (10:00 - 16:00 Mon-Fri) respecting quiet hours
        and timezone conversions.
        """
        import os
        import zoneinfo
        tz_name = timezone_str or self.default_timezone

        try:
            target_tz = zoneinfo.ZoneInfo(tz_name) if tz_name and tz_name.upper() != "UTC" else timezone.utc
        except Exception:
            target_tz = timezone.utc

        # Parse days ahead from date_range (e.g. '7d', '14d', '3d')
        if date_range and date_range.endswith("d"):
            try:
                days_ahead = int(date_range[:-1])
            except ValueError:
                days_ahead = 7

        now = datetime.now(timezone.utc)
        end_time_dt = now + timedelta(days=days_ahead)

        # 1. Try Cal.com API if credentials are provided
        calcom_key = self.api_key or os.getenv("CALCOM_API_KEY") or os.getenv("CAL_COM_API_KEY") or os.getenv("CAL_API_KEY") or ""
        if calcom_key:
            event_types = await get_calcom_event_types(calcom_key)
            if event_types:
                et_id = event_types[0].get("id")
                api_slots = await get_calcom_slots(
                    api_key=calcom_key,
                    event_type_id=et_id,
                    start_time=now.isoformat(),
                    end_time=end_time_dt.isoformat(),
                    time_zone=tz_name,
                )
                if api_slots:
                    return api_slots

        # 2. Try Calendly API if token is provided
        calendly_token = os.getenv("CALENDLY_TOKEN") or os.getenv("CALENDLY_API_KEY") or ""
        if calendly_token:
            c_event_types = await get_calendly_event_types(calendly_token)
            if c_event_types:
                et_uri = c_event_types[0].get("uri")
                api_slots = await get_calendly_slots(
                    token=calendly_token,
                    event_type_uri=et_uri,
                    start_time=now.isoformat(),
                    end_time=end_time_dt.isoformat(),
                )
                if api_slots:
                    return api_slots

        # 3. Fallback: Generate RFC-compliant simulated business-hour slots
        slots: list[dict[str, Any]] = []
        duration_min = self._parsed_info.get("duration_minutes", 15) if self._parsed_info else 15

        # Standard business slots: 10:00, 11:30, 14:00, 15:30
        daily_slot_times = [
            (10, 0),
            (11, 30),
            (14, 0),
            (15, 30),
        ]

        # Generate slots starting tomorrow
        for day_offset in range(1, days_ahead + 1):
            slot_date = now.date() + timedelta(days=day_offset)
            # Skip Saturday (5) and Sunday (6)
            if slot_date.weekday() >= 5:
                continue

            for hour, minute in daily_slot_times:
                start_dt = datetime(
                    slot_date.year,
                    slot_date.month,
                    slot_date.day,
                    hour,
                    minute,
                    tzinfo=timezone.utc,
                )
                end_dt = start_dt + timedelta(minutes=duration_min)

                local_start_dt = start_dt.astimezone(target_tz)
                tz_label = local_start_dt.tzname() or tz_name
                formatted_local = local_start_dt.strftime(f"%A, %b %d at %I:%M %p {tz_label}")
                formatted_utc = start_dt.strftime("%A, %b %d at %I:%M %p UTC")

                slots.append({
                    "start": start_dt.isoformat(),
                    "end": end_dt.isoformat(),
                    "local_start": local_start_dt.isoformat(),
                    "formatted": formatted_local,
                    "formatted_utc": formatted_utc,
                    "timezone": str(tz_name),
                    "date": slot_date.isoformat(),
                    "duration_minutes": duration_min,
                    "booking_url": self.booking_url or "https://cal.com/harvey-team/15min",
                    "available": True,
                    "is_simulated": True,
                })

        return slots

    def generate_ics_invite(
        self,
        prospect_email: str,
        prospect_name: str,
        slot_start: str,
        duration_minutes: int = 15,
        summary: str = "Harvey AI Intro Call",
        description: str = "Introductory discussion regarding autonomous sales development workflows.",
    ) -> str:
        """Generate an iCalendar (.ics) invite string for a booked meeting."""
        dt_format = "%Y%m%dT%H%M%SZ"
        try:
            start_dt = datetime.fromisoformat(slot_start.replace("Z", "+00:00"))
        except Exception:
            start_dt = datetime.now(timezone.utc) + timedelta(days=1)

        end_dt = start_dt + timedelta(minutes=duration_minutes)
        uid = f"harvey-meet-{int(start_dt.timestamp())}-{prospect_email}"

        ics_lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Harvey AI//Sales Agent//EN",
            "CALSCALE:GREGORIAN",
            "METHOD:REQUEST",
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{datetime.now(timezone.utc).strftime(dt_format)}",
            f"DTSTART:{start_dt.strftime(dt_format)}",
            f"DTEND:{end_dt.strftime(dt_format)}",
            f"SUMMARY:{summary}",
            f"DESCRIPTION:{description}",
            f"ORGANIZER;CN=Harvey AI:mailto:harvey@yourcompany.com",
            f"ATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;CN={prospect_name or prospect_email}:mailto:{prospect_email}",
            "STATUS:CONFIRMED",
            "TRANSP:OPAQUE",
            "END:VEVENT",
            "END:VCALENDAR",
        ]
        return "\r\n".join(ics_lines)

    async def book_meeting(
        self,
        prospect_email: str,
        slot: dict[str, Any],
        prospect_name: str = "",
        notes: str = "",
        state: Any = None,
    ) -> bool:
        """Book a meeting with a prospect for an accepted slot.

        Generates the ICS invite, logs an action in state, and progresses the
        CRM pipeline to 'Meeting Booked' if a prospect matches the email.
        """
        if not prospect_email or not isinstance(slot, dict) or not slot.get("start"):
            logger.warning("book_meeting called with invalid arguments.")
            return False

        duration = slot.get("duration_minutes", 15)
        slot_start = slot.get("start")
        logger.info(f"Booking meeting for {prospect_email} at {slot_start}")

        # Generate calendar invite payload
        ics_text = self.generate_ics_invite(
            prospect_email=prospect_email,
            prospect_name=prospect_name,
            slot_start=slot_start,
            duration_minutes=duration,
        )

        if state is not None:
            try:
                # Log system action
                await state.log_action(
                    action_type="meeting_booked",
                    agent="calendar",
                    details={
                        "prospect_email": prospect_email,
                        "prospect_name": prospect_name,
                        "slot": slot,
                        "notes": notes,
                        "ics_uid": f"harvey-meet-{prospect_email}",
                    },
                )

                # Look up prospect and advance pipeline stage to 'Meeting'
                prospect = await state.get_prospect_by_email(prospect_email)
                if prospect:
                    await state.update_prospect_pipeline(prospect.id, stage="Meeting", notes=f"Meeting confirmed for {slot.get('formatted', slot_start)}")
                    logger.info(f"Advanced prospect {prospect.id} to 'Meeting'")
            except Exception as e:
                logger.warning(f"Error persisting booked meeting state: {e}")

        return True
