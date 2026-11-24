"""Test proving that tests and scripts cannot write to data/harvey.db when HARVEY_DB_PATH is unset.

Demonstrates that invoking the OpenStreetMap test-connection endpoint with the real DB path
guarded leaves data/harvey.db byte-for-byte identical (SHA-256 before == SHA-256 after).
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from harvey.dashboard import app, token_store


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().upper()


def test_osm_test_connection_guards_real_db(monkeypatch):
    real_db_path = Path("data/harvey.db").resolve()
    assert real_db_path.exists(), "data/harvey.db must exist for this invariant test"

    # Explicitly pop HARVEY_DB_PATH to test the unset condition
    monkeypatch.delenv("HARVEY_DB_PATH", raising=False)
    monkeypatch.delenv("HARVEY_ALLOW_REAL_DB_WRITE", raising=False)
    monkeypatch.setenv("HARVEY_GUARD_REAL_DB", "1")

    # Record SHA-256 and provider_logs count before
    sha_before = _hash_file(real_db_path)
    con = sqlite3.connect(f"file:{real_db_path}?mode=ro", uri=True)
    cur = con.cursor()
    cur.execute("SELECT count(*) FROM provider_logs")
    logs_count_before = cur.fetchone()[0]
    con.close()

    # Call OpenStreetMap test-connection endpoint
    token = token_store.get()
    client = TestClient(app, base_url="http://127.0.0.1:5566")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    resp = client.post("/api/integrations/test-connection", json={"provider": "openstreetmap"}, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("success") is True
    assert "operational" in data.get("message", "").lower() or data.get("status") == "connected"

    # Record SHA-256 and provider_logs count after
    sha_after = _hash_file(real_db_path)
    con = sqlite3.connect(f"file:{real_db_path}?mode=ro", uri=True)
    cur = con.cursor()
    cur.execute("SELECT count(*) FROM provider_logs")
    logs_count_after = cur.fetchone()[0]
    con.close()

    # Invariants verification: real data/harvey.db is 100% untouched
    assert sha_before == sha_after, (
        f"Real DB hash modified during test-connection! Before: {sha_before}, After: {sha_after}"
    )
    assert logs_count_before == logs_count_after, (
        f"New row added to real DB provider_logs! Count before: {logs_count_before}, after: {logs_count_after}"
    )
