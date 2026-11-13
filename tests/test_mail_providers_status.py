"""Unit and API integration tests for Microsoft 365, Precleared Compliance, and Mailbox Providers."""

import pytest
from starlette.testclient import TestClient

from harvey.dashboard import app, token_store
from harvey.integrations.microsoft import TOKEN_FILE


@pytest.fixture
def client():
    token = token_store.get()
    tc = TestClient(app, base_url="http://127.0.0.1:5555")
    tc.headers.update({"Authorization": f"Bearer {token}"})
    return tc


class TestMicrosoftProviderApi:
    def test_microsoft_status(self, client):
        resp = client.get("/api/microsoft/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert "scope" in data
        assert "token_file" in data

    def test_microsoft_mock_connect_and_disconnect(self, client):
        # 1. Connect mock sandbox M365
        resp_conn = client.post("/api/microsoft/mock-connect", json={"email": "pilot.m365@enterprise.example.com"})
        assert resp_conn.status_code == 200
        data_conn = resp_conn.json()
        assert data_conn.get("success") is True
        assert data_conn.get("email") == "pilot.m365@enterprise.example.com"
        assert TOKEN_FILE.exists()

        # 2. Status reflects mock connected
        resp_status = client.get("/api/microsoft/status")
        assert resp_status.status_code == 200
        assert resp_status.json().get("status") == "mock_connected"
        assert resp_status.json().get("email_address") == "pilot.m365@enterprise.example.com"

        # 3. Disconnect
        resp_disc = client.post("/api/microsoft/disconnect")
        assert resp_disc.status_code == 200
        assert resp_disc.json().get("success") is True
        assert not TOKEN_FILE.exists()


class TestPreclearedComplianceApi:
    def test_compliance_status(self, client):
        resp = client.get("/api/compliance/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("success") is True
        assert "jurisdictions_covered" in data
        assert "free_allowance_daily" in data

    def test_compliance_check_standard_us(self, client):
        resp = client.post("/api/compliance/check", json={"email": "marketing.lead@solarsystems.com"})
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("success") is True
        assert data.get("is_cleared") is True
        assert data.get("verdict") == "ok_to_email"
        assert "CAN-SPAM" in data.get("jurisdiction", "")

    def test_compliance_check_eu_b2b(self, client):
        resp = client.post("/api/compliance/check", json={"email": "dr.klaus@berlindent.de"})
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("success") is True
        assert data.get("is_cleared") is True
        assert data.get("verdict") == "ok_to_email_b2b"
        assert "EU" in data.get("jurisdiction", "")

    def test_compliance_check_institutional_prohibited(self, client):
        resp = client.post("/api/compliance/check", json={"email": "researcher@cityhealth.gov"})
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("success") is True
        assert data.get("is_cleared") is False
        assert data.get("verdict") == "do_not_email"

