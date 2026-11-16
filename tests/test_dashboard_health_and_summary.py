"""Tests for dashboard health, summary, and honest integration connection checks."""

import pytest
from starlette.testclient import TestClient

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


def test_api_health_endpoint(api_client):
    """Verify GET /api/health returns operational status."""
    resp = api_client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") == "ok"
    assert data.get("app") == "harvey"
    assert data.get("version") == "2.0.0"
    assert "timestamp" in data


def test_api_summary_endpoint(api_client):
    """Verify GET /api/summary (API-01) returns complete platform state."""
    resp = api_client.get("/api/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert "prospects" in data
    assert "sending_paused" in data
    assert data.get("sending_paused") is True  # Safety invariant


def test_honest_provider_connection_reporting(api_client):
    """Verify test-connection endpoint never reports sandbox mocks as live connections."""
    # 1. Local / open services should succeed
    for prov in ("openstreetmap", "chromadb", "ollama"):
        resp = api_client.post("/api/integrations/test-connection", json={"provider": prov})
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("success") is True, f"Expected {prov} to be connected"
        assert data.get("status") == "connected"

    # 2. Gmail connection reporting: if live token present, reports connected; if mock token, reports sandbox_active
    resp = api_client.post("/api/integrations/test-connection", json={"provider": "gmail"})
    assert resp.status_code == 200
    data = resp.json()
    if data.get("status") == "connected":
        assert data.get("success") is True
        assert "connected as" in data.get("message", "").lower()
    else:
        assert data.get("success") is False, "Mock token must not be reported as live success"
        assert data.get("status") == "sandbox_active"
        assert "sandbox" in data.get("message", "").lower()

    # 3. Missing credentials should report credentials_missing
    for prov in ("microsoft365", "dataforseo", "serper", "hunter", "zerobounce"):
        resp = api_client.post("/api/integrations/test-connection", json={"provider": prov})
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("success") is False
        assert data.get("status") == "credentials_missing"

