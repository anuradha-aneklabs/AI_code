"""Tests for Dashboard discovery guard and sample exclusion (Part 1d)."""

import os
import pytest
from starlette.testclient import TestClient

from harvey.dashboard import app, token_store
from harvey.state import StateManager


@pytest.fixture
def auth_client(tmp_path, monkeypatch):
    """Configure temporary database and authorized test client for dashboard tests."""
    temp_db = tmp_path / "dashboard_test.db"
    monkeypatch.setenv("HARVEY_DB_PATH", str(temp_db))
    import harvey.dashboard as dash
    monkeypatch.setattr(dash, "DB_PATH", temp_db)
    sm = StateManager(str(temp_db))
    import asyncio
    asyncio.run(sm.init_db())

    client = TestClient(app, base_url="http://127.0.0.1:5555")
    token = token_store.get()
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


def test_search_without_provider_key_returns_unavailable_and_zero_rows(auth_client, monkeypatch):
    """With no provider key configured and demo_mode false, search returns status 'unavailable' and 0 rows."""
    monkeypatch.delenv("TINYFISH_API_KEY", raising=False)
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    from harvey import config as cfg
    monkeypatch.setattr(cfg, "load_env", lambda: cfg.EnvConfig(tinyfish_api_key="", serper_api_key=""))

    response = auth_client.post("/api/discovery/search", json={"industry": "SaaS", "location": "Indore"})
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert data["status"] == "unavailable"
    assert "unavailable:" in data["message"]
    assert len(data["leads"]) == 0
    assert data["count"] == 0


def test_search_with_demo_mode_true_returns_labeled_sample_fixtures(auth_client):
    """When demo_mode=True, search returns sample fixtures visibly marked with is_sample=1."""
    response = auth_client.post(
        "/api/discovery/search",
        json={"industry": "SaaS", "location": "Indore", "demo_mode": True, "limit": 2}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["demo_mode"] is True
    assert data["label"] == "DEMO SAMPLE FIXTURE"
    assert len(data["leads"]) > 0
    for lead in data["leads"]:
        assert lead["is_sample"] == 1


def test_get_discovered_leads_excludes_sample_rows_by_default(auth_client):
    """By default, GET /api/discovery/leads filters out is_sample=1 rows from real lead lists and counts."""
    # 1. Insert sample leads via demo_mode
    resp_demo = auth_client.post("/api/discovery/search", json={"demo_mode": True, "limit": 3})
    assert resp_demo.status_code == 200
    assert len(resp_demo.json()["leads"]) > 0

    # 2. Query default leads: must return 0 leads because all existing are is_sample=1
    resp_default = auth_client.get("/api/discovery/leads")
    assert resp_default.status_code == 200
    data_default = resp_default.json()
    assert data_default["count"] == 0
    assert len(data_default["leads"]) == 0

    # 3. Query with demo_mode=true or include_sample=true: returns the sample leads
    resp_sample = auth_client.get("/api/discovery/leads?demo_mode=true")
    assert resp_sample.status_code == 200
    data_sample = resp_sample.json()
    assert data_sample["count"] > 0
    assert len(data_sample["leads"]) > 0


def test_csv_export_excludes_sample_leads_by_default(auth_client):
    """CSV export /api/discovery/export.csv excludes is_sample=1 rows."""
    # Insert sample leads
    auth_client.post("/api/discovery/search", json={"demo_mode": True, "limit": 2})

    # Default export
    resp = auth_client.get("/api/discovery/export.csv")
    assert resp.status_code == 200
    lines = resp.text.strip().splitlines()
    # Only header should be present (no sample leads)
    assert len(lines) == 1
    assert "company_name" in lines[0]
