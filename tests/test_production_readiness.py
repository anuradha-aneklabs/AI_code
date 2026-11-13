"""Test suite for Harvey MVP Production-Readiness Hardening.

Covers:
1. Environment configuration and secret masking
2. Port parsing resilience
3. Database point-in-time backup and integrity verification
4. CLI database commands
5. Gmail read-only fail-closed safety invariant
6. Dashboard JSON decode error handler and security redactions
7. System safety invariants (pilot safety hold, require approval, sent count = 0, pilot drafts)
"""

import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from harvey import dashboard as dash
from harvey.config import EnvConfig, _parse_port, load_env, load_config
from harvey.integrations.gmail import GmailProvider, READONLY_SCOPE, save_mock_token
from harvey.state import StateManager


# ── 1. Config & Environment Variable Tests ──

def test_env_config_repr_masks_secrets():
    """Verify that EnvConfig repr/str masks sensitive keys and secrets."""
    env = EnvConfig(
        gemini_api_key="AIzaSySecretKey12345",
        groq_api_key="gsk_secret_groq_key_9999",
        gmail_client_secret="top_secret_gmail_secret",
        smtp_password="super_secret_smtp_password",
        smtp_host="mail.example.com",
    )
    repr_str = repr(env)
    assert "AIzaSySecretKey12345" not in repr_str
    assert "gsk_secret_groq_key_9999" not in repr_str
    assert "top_secret_gmail_secret" not in repr_str
    assert "super_secret_smtp_password" not in repr_str
    assert "mail.example.com" in repr_str

    redacted = env.redacted()
    assert redacted["gemini_api_key"] == "AIza****"
    assert redacted["smtp_host"] == "mail.example.com"


def test_parse_port_resilience():
    """Verify that _parse_port handles malformed, empty, and out-of-range strings."""
    assert _parse_port("587", 587) == 587
    assert _parse_port("25", 587) == 25
    assert _parse_port("", 587) == 587
    assert _parse_port(None, 587) == 587
    assert _parse_port("invalid_port", 587) == 587
    assert _parse_port("999999", 587) == 587  # out of range > 65535
    assert _parse_port("-1", 587) == 587
    assert _parse_port("0", 587) == 587


# ── 2. Database Backup & Integrity Tests ──

@pytest.mark.asyncio
async def test_state_verify_integrity(tmp_path):
    """Verify that verify_integrity checks DB status, user_version, and checkpoints."""
    db_path = tmp_path / "test_integrity.db"
    sm = StateManager(str(db_path))
    await sm.init_db()

    res = await sm.verify_integrity()
    assert res["ok"] is True
    assert res["quick_check"] == ["ok"]
    assert res["user_version"] >= 20
    assert "db_path" in res


def test_state_backup_db(tmp_path):
    """Verify that backup_db takes a valid SQLite snapshot without locking."""
    src_db = tmp_path / "source.db"
    dest_db = tmp_path / "backup_out.db"

    conn = sqlite3.connect(str(src_db))
    conn.execute("CREATE TABLE test_data (id INT, val TEXT)")
    conn.execute("INSERT INTO test_data VALUES (1, 'pilot_data')")
    conn.commit()
    conn.close()

    sm = StateManager(str(src_db))
    out_path = sm.backup_db(dest_path=dest_db)
    assert out_path == dest_db
    assert dest_db.exists()

    # Verify backup contains identical data
    backup_conn = sqlite3.connect(str(dest_db))
    row = backup_conn.execute("SELECT id, val FROM test_data").fetchone()
    backup_conn.close()
    assert row == (1, "pilot_data")


# ── 3. Gmail Read-Only Send Safety Invariant ──

@pytest.mark.asyncio
async def test_gmail_send_email_fails_closed_in_live_mode():
    """Verify that non-mocked GmailProvider.send_email strictly fails closed."""
    cfg = load_config()
    env = load_env()
    provider = GmailProvider(cfg, env)

    # In live mode (unmocked request/token), sending must be rejected
    result = await provider.send_email("test@example.com", "Test Subject", "Test Body")
    assert result.ok is False
    assert "read-only" in result.error.lower() or "disabled" in result.error.lower()


# ── 4. Dashboard Error Handling & Redactions ──

@pytest.fixture
def auth_client(tmp_path, monkeypatch):
    db = tmp_path / "harvey.db"
    monkeypatch.setattr(dash, "DB_PATH", db)
    monkeypatch.setattr(dash, "WEB_DIR", Path(__file__).resolve().parent.parent / "harvey" / "web")
    dash.token_store.configure(tmp_path / "data" / "dashboard-token")
    token = dash.token_store.get()
    with TestClient(dash.app, base_url="http://127.0.0.1:5555",
                    headers={"Authorization": f"Bearer {token}"}) as c:
        c.db_path = db
        yield c


def test_malformed_json_returns_400(auth_client):
    """Verify that malformed JSON request bodies return HTTP 400 Bad Request."""
    resp = auth_client.post(
        "/api/discovery/search",
        content="not-a-json-payload",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    assert "Invalid JSON payload" in resp.json().get("error", "")


def test_mock_connect_redacts_refresh_token(auth_client):
    """Verify that /api/gmail/mock-connect does not expose refresh_token in response."""
    resp = auth_client.post(
        "/api/gmail/mock-connect",
        json={"email": "pilot-test@example.com"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "refresh_token" not in data
    assert data.get("email") == "pilot-test@example.com"
    assert data.get("is_mock") is True


def test_db_verify_and_backup_endpoints(auth_client):
    """Verify /api/db/verify and /api/db/backup endpoints."""
    # Verify endpoint
    v_resp = auth_client.get("/api/db/verify")
    assert v_resp.status_code == 200
    assert v_resp.json()["success"] is True

    # Backup endpoint
    b_resp = auth_client.post("/api/db/backup", json={})
    assert b_resp.status_code == 200
    assert b_resp.json()["success"] is True
    assert "backup_path" in b_resp.json()
    assert Path(b_resp.json()["backup_path"]).exists()


# ── 5. Safety Invariants on Live System ──

def test_live_safety_invariants_and_pilot_drafts():
    """Verify that all core safety invariants on the active database remain 100% intact."""
    cfg = load_config()
    assert cfg.channels.email.require_approval is True

    live_db = Path("data/harvey.db")
    if not live_db.exists():
        pytest.skip("Live database not present")

    conn = sqlite3.connect(str(live_db))
    c = conn.cursor()

    # Sending paused safety hold
    c.execute("SELECT value FROM settings WHERE key = 'sending_paused'")
    row = c.fetchone()
    assert row is not None
    assert "Safety Hold" in row[0]

    # Zero sent count
    c.execute("SELECT count(*) FROM outbox WHERE status = 'sent'")
    sent_count = c.fetchone()[0]
    assert sent_count == 0

    # 4 original pilot drafts
    pilot_expected = {
        "a02fed19ec8d": "pending_review",
        "36dfa015198c": "cancelled",
        "3be7a27f3620": "cancelled",
        "fad4e4984339": "pending_review",
    }
    for pid, expected_status in pilot_expected.items():
        c.execute("SELECT status FROM outbox WHERE id LIKE ?", (f"{pid}%",))
        res = c.fetchone()
        assert res is not None, f"Pilot draft {pid} missing"
        assert res[0] == expected_status, f"Pilot draft {pid} status mismatch: {res[0]} != {expected_status}"

    conn.close()

