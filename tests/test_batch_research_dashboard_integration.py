"""Tests for batch research dashboard integration and entity matching relaxation.

Uses FAKE data only and temporary databases.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from harvey.entity_cross_check import check_entity_match, extract_domain_sld
from harvey.state import StateManager


@pytest.fixture
def temp_state(tmp_path):
    db_file = tmp_path / "test_research_dashboard.db"
    return StateManager(db_path=str(db_file))


def test_sld_extraction():
    """Verify domain SLD extraction across variations."""
    assert extract_domain_sld("cargill.com") == "cargill"
    assert extract_domain_sld("www.cargill.com") == "cargill"
    assert extract_domain_sld("https://www.cargill.com/about") == "cargill"
    assert extract_domain_sld("subdomain.company.org") == "subdomain"


def test_entity_cross_check_cargill_sld_match():
    """Verify CARGILL FOODS INC. matches cargill.com via domain SLD."""
    hint = "CARGILL FOODS INC."
    status, evidence = check_entity_match(hint, domain="cargill.com")
    assert status == "match"
    assert "cargill" in evidence.lower()


def test_entity_cross_check_brand_in_corpus():
    """Verify 2-token company matches when primary brand token exists on website."""
    hint = "Beacon Telecom Inc."
    pages = [
        {
            "title": "Welcome to Beacon",
            "readable_text": "Beacon provides enterprise network solutions.",
        }
    ]
    status, evidence = check_entity_match(hint, pages=pages, domain="beacon-solutions.net")
    assert status == "match"
    assert "beacon" in evidence.lower()


@pytest.mark.asyncio
async def test_dashboard_research_endpoints(temp_state, tmp_path, monkeypatch):
    """Verify dashboard research API endpoints: upload, batches, details, run, and export."""
    await temp_state.init_db()

    # Import app and auth
    import harvey.dashboard as dash
    monkeypatch.setattr(dash, "_state", lambda: temp_state)
    monkeypatch.setattr(dash, "DB_PATH", Path(temp_state.db_path))

    # Mock token store to return test-token
    monkeypatch.setattr(dash.token_store, "get", lambda: "test-token")

    # Test client with headers matching dashboard auth requirements
    client = TestClient(
        dash.app,
        base_url="http://127.0.0.1:5555",
        headers={
            "Authorization": "Bearer test-token",
            "Host": "127.0.0.1:5555",
            "Sec-Fetch-Site": "same-origin",
        },
    )

    csv_content = (
        "EntityName,FirstName,LastName,EmailAddress,Phone\n"
        "Cargill Foods Inc.,John,Doe,jdoe@cargillfake.test,555-0100\n"
        "Apex Systems LLC,Jane,Smith,jsmith@apexsystemsfake.test,555-0200\n"
    )

    # 1. POST /api/research/batch upload
    resp = client.post(
        "/api/research/batch",
        json={"filename": "contacts.csv", "content": csv_content, "max_domains": 5},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    batch_id = data["batch"]["batch_id"]
    assert data["batch"]["total_rows"] == 2
    assert data["batch"]["unique_domains"] == 2

    # 2. GET /api/research/batches
    b_resp = client.get("/api/research/batches")
    assert b_resp.status_code == 200
    b_data = b_resp.json()
    assert b_data["success"] is True
    assert any(b["id"] == batch_id for b in b_data["batches"])

    # 3. GET /api/research/batch/{batch_id}/details
    d_resp = client.get(f"/api/research/batch/{batch_id}/details")
    assert d_resp.status_code == 200
    d_data = d_resp.json()
    assert d_data["success"] is True
    assert d_data["metrics"]["total_contacts"] == 2
    assert d_data["metrics"]["valid_contacts"] == 2
    assert d_data["metrics"]["unique_domains"] == 2
    # Verify PII masking in API output
    assert "jdoe@cargillfake.test" not in str(d_data["rows"])
    assert "jd***@cargillfake.test" in str(d_data["rows"]) or "j***@cargillfake.test" in str(d_data["rows"])

    # 4. POST /api/research/batch/{batch_id}/run
    run_resp = client.post(f"/api/research/batch/{batch_id}/run", json={"resume": False})
    assert run_resp.status_code == 200
    run_data = run_resp.json()
    assert run_data["success"] is True

    # 5. GET /api/research/batch/{batch_id}/export (CSV)
    csv_resp = client.get(f"/api/research/batch/{batch_id}/export?format=csv")
    assert csv_resp.status_code == 200
    assert "text/csv" in csv_resp.headers["content-type"]

    # 6. GET /api/research/batch/{batch_id}/export?format=html
    html_resp = client.get(f"/api/research/batch/{batch_id}/export?format=html")
    assert html_resp.status_code == 200
    assert "text/html" in html_resp.headers["content-type"]
    assert "<!DOCTYPE html>" in html_resp.text or "<html" in html_resp.text
