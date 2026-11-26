"""Integration and unit tests for MX validation skip on upload and resolution during research (fake data only)."""

import json
from unittest.mock import patch
import httpx
import pytest

from harvey.single_domain_research import (
    validate_and_normalize_email,
    _lookup_mx_records,
    run_single_email_research,
    ResearchConfig,
)
from harvey.state import StateManager


def test_mx_check_skip_on_upload():
    """Verify validate_and_normalize_email skips DNS lookup when lookup_mx=False."""
    with patch("harvey.single_domain_research._lookup_mx_records") as mock_lookup:
        res = validate_and_normalize_email(
            "user@fakesamplecorp.com",
            free_mail_domains=set(),
            lookup_mx=False,
        )
        assert res.valid_syntax is True
        assert res.domain == "fakesamplecorp.com"
        assert res.mx_info is None
        mock_lookup.assert_not_called()


def test_mx_check_executed_and_reports_missing_reason():
    """Verify validate_and_normalize_email executes MX lookup and reports reason when absent."""
    with patch("dns.resolver.resolve", side_effect=Exception("NXDOMAIN or timeout")):
        res = validate_and_normalize_email(
            "user@fakenomxdomain999.xyz",
            free_mail_domains=set(),
            lookup_mx=True,
        )
        assert res.valid_syntax is True
        assert res.mx_info is not None
        assert res.mx_info["status"] == "none_found"
        assert "DNS MX lookup failed" in res.mx_info["reason"]


@pytest.mark.asyncio
async def test_single_domain_research_persists_mx_details(tmp_path):
    """Verify run_single_email_research executes MX resolution and persists reason in stage details."""
    db_file = tmp_path / "test_mx_research.db"
    state = StateManager(db_path=str(db_file))
    await state.init_db()

    cfg = ResearchConfig(
        max_pages=1,
        timeout_seconds=1.0,
    )

    def mock_transport(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><head><title>Fake</title></head><body><h1>Fake</h1></body></html>")

    transport = httpx.MockTransport(mock_transport)
    client = httpx.AsyncClient(transport=transport)

    with patch("dns.resolver.resolve", side_effect=Exception("NoAnswer")):
        result = await run_single_email_research(
            email="contact@faketestwithoutmx.org",
            state=state,
            config=cfg,
            client=client,
        )

        assert result["id"] != ""
        # Check stages in database
        stages = await state.get_research_stages(result["id"])
        email_stage = next((s for s in stages if s["stage_name"] == "email_validation"), None)
        assert email_stage is not None
        assert email_stage["status"] == "completed"

        details = json.loads(email_stage["details_json"])
        assert "mx_info" in details
        assert details["mx_info"]["status"] == "none_found"
        assert "DNS MX lookup failed" in details["mx_info"]["reason"]
