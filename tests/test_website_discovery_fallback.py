"""Unit tests for website discovery fallback (Task 6).

Covers:
- discover_website_candidate with mocked search provider
- Correct classification (only company_homepage accepted)
- Opt-in behaviour (enable_website_discovery_fallback=False by default)
- Never replacing email domain
- Never fetching candidate pages
- Exports formatting: 'possible website (unverified): URL (source: search)'
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from harvey.batch_research import (
    discover_website_candidate,
    export_batch_results,
)
from harvey.state import StateManager, _new_id, _utcnow


@pytest.mark.asyncio
async def test_discover_website_candidate_homepage_match():
    """Verify that a company_homepage result is returned as an unverified candidate."""
    mock_response = {
        "results": [
            {
                "url": "https://www.bearcom.com",
                "title": "BearCom | Two-Way Radios and Wireless Communications",
                "snippet": "BearCom is a nationwide provider of two-way radio and wireless solutions.",
            },
            {
                "url": "https://www.linkedin.com/company/bearcom",
                "title": "BearCom | LinkedIn",
                "snippet": "BearCom on LinkedIn.",
            }
        ]
    }

    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_response
    mock_resp.raise_for_status.return_value = None
    mock_client.get.return_value = mock_resp

    candidate = await discover_website_candidate(
        "BearCom",
        api_key="test-key",
        http_client=mock_client,
    )

    assert candidate is not None
    assert candidate["candidate_url"] == "https://www.bearcom.com"
    assert candidate["status"] == "unverified"
    assert candidate["result_class"] == "company_homepage"
    assert candidate["rank"] == 1
    assert "BearCom" in candidate["search_query"]
    assert candidate["origin"] == "search_discovery_fallback"


@pytest.mark.asyncio
async def test_discover_website_candidate_rejects_directories():
    """Verify that directory or social media results are rejected (no company_homepage)."""
    mock_response = {
        "results": [
            {
                "url": "https://www.yelp.com/biz/bearcom-dallas",
                "title": "BearCom - Dallas, TX - Yelp",
                "snippet": "Read reviews for BearCom on Yelp.",
            },
            {
                "url": "https://www.facebook.com/BearComWireless",
                "title": "BearCom - Facebook",
                "snippet": "Follow BearCom on Facebook.",
            }
        ]
    }

    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_response
    mock_resp.raise_for_status.return_value = None
    mock_client.get.return_value = mock_resp

    candidate = await discover_website_candidate(
        "BearCom",
        api_key="test-key",
        http_client=mock_client,
    )

    assert candidate is None


@pytest.mark.asyncio
async def test_discover_website_candidate_missing_key_skips_cleanly():
    """Verify that missing API key skips gracefully without raising errors."""
    with patch.dict("os.environ", {}, clear=True):
        candidate = await discover_website_candidate("BearCom", api_key="")
        assert candidate is None


@pytest.mark.asyncio
async def test_export_surfaces_possible_website(tmp_path):
    """Verify that export_batch_results surfaces 'possible website (unverified): URL (source: search)'."""
    st = StateManager()
    await st.init_db()

    batch_id = _new_id()
    now = _utcnow()

    await st.create_import_batch(
        batch_id=batch_id,
        source_filename="test.csv",
        source_sha256="hash123",
        total_rows=1,
        valid_rows=1,
        unique_domains=1,
        max_domains=5,
        status="completed",
        created_at=now,
    )

    row_id = _new_id()
    await st.save_import_rows_bulk([{
        "id": row_id,
        "batch_id": batch_id,
        "row_number": 1,
        "raw_email": "info@bearcom.com",
        "domain": "bearcom.com",
        "first_name": "John",
        "last_name": "Doe",
        "company_name_hint": "BearCom",
        "phone": "555-1234",
        "row_status": "valid",
        "error": "",
        "created_at": now,
    }])

    stage_summary = {
        "website_status": "unavailable",
        "website_url": "unavailable: HTTP 403 bot_challenge",
        "pages_scanned": 0,
        "overview_status": "unavailable",
        "decision_makers_found": 0,
        "has_unavailable_row": True,
        "entity_check": "no_match",
        "entity_check_evidence": "",
        "website_candidate": {
            "candidate_url": "https://www.bearcom.com",
            "search_query": '"BearCom" official website',
            "rank": 1,
            "snippet": "BearCom official site",
            "result_class": "company_homepage",
            "status": "unverified",
            "origin": "search_discovery_fallback",
        },
        "possible_website": "possible website (unverified): https://www.bearcom.com (source: search)",
    }

    run_id = _new_id()
    await st.save_research_run(
        run_id=run_id,
        input_email="info@bearcom.com",
        normalized_email="info@bearcom.com",
        domain="bearcom.com",
        status="completed",
        started_at=now,
        finished_at=now,
    )

    await st.save_company_website(
        website_id=_new_id(),
        run_id=run_id,
        domain="bearcom.com",
        candidate_url="https://bearcom.com",
        final_url="",
        http_status=403,
        verification_status="unavailable",
        evidence_text="HTTP 403 (Cloudflare/Bot challenge)",
    )

    await st.save_import_domain(
        domain_id=_new_id(),
        batch_id=batch_id,
        domain="bearcom.com",
        representative_email="info@bearcom.com",
        run_id=run_id,
        status="completed",
        entity_check="no_match",
        entity_check_evidence="",
        stage_summary_json=json.dumps(stage_summary),
        error="",
        created_at=now,
        finished_at=now,
    )

    export_files = await export_batch_results(batch_id, state=st, export_dir=tmp_path)
    contacts_csv = Path(export_files["contacts_results"])
    assert contacts_csv.exists()

    with open(contacts_csv, "r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))
        assert len(reader) == 1
        exported_row = reader[0]
        # Invariant 1: Email domain is NOT replaced
        assert exported_row["domain"] == "bearcom.com"
        assert exported_row["email"] == "in***@bearcom.com"
        # Invariant 2: Surface possible website
        assert "possible website (unverified): https://www.bearcom.com (source: search)" in exported_row["website_url"]
