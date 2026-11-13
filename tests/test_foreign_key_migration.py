"""Regression tests for Database Migration 22 & Nullable Foreign Keys."""

import pytest
import sqlite3
from harvey.models.prospect import Prospect
from harvey.models.conversation import Conversation
from harvey.state import StateManager


@pytest.mark.asyncio
async def test_migration_22_applied_and_clean_fk(tmp_path):
    db_path = tmp_path / "test_mig.db"
    state = StateManager(str(db_path))
    await state.init_db()

    integrity = await state.verify_integrity()
    assert integrity["ok"] is True
    assert integrity["user_version"] >= 22

    # 1. Insert prospect with no company_id (empty string or None)
    p = Prospect(
        first_name="Jane",
        last_name="Doe",
        email="jane.doe@example.com",
        title="Owner",
        company_id="",  # validator turns this to None
    )
    assert p.company_id is None
    pid = await state.add_prospect(p)

    # 2. Insert conversation with no prospect_id (None)
    c = Conversation(
        id="convo-inbound-01",
        prospect_id=None,
        channel="email",
    )
    assert c.prospect_id is None
    cid = await state.add_conversation(c)

    # 3. Check SQLite low-level foreign keys
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("PRAGMA integrity_check")
    assert cur.fetchall() == [("ok",)]

    cur.execute("PRAGMA foreign_key_check")
    violations = cur.fetchall()
    assert violations == [], f"Unexpected foreign key violations: {violations}"

    # Verify company_id is NULL in table
    cur.execute("SELECT company_id FROM prospects WHERE id = ?", (pid,))
    row = cur.fetchone()
    assert row[0] is None, f"Expected company_id to be NULL, got {row[0]!r}"

    # Verify prospect_id is NULL in table
    cur.execute("SELECT prospect_id FROM conversations WHERE id = ?", (cid,))
    row_c = cur.fetchone()
    assert row_c[0] is None, f"Expected prospect_id to be NULL, got {row_c[0]!r}"
    conn.close()


@pytest.mark.asyncio
async def test_live_database_zero_fk_violations():
    """Verify live project database harvey.db has zero integrity or FK violations."""
    state = StateManager()
    await state.init_db()
    conn = sqlite3.connect(state.db_path)
    cur = conn.cursor()
    cur.execute("PRAGMA integrity_check")
    assert cur.fetchall() == [("ok",)]

    cur.execute("PRAGMA foreign_key_check")
    violations = cur.fetchall()
    assert violations == [], f"Live database foreign key violations found: {violations}"
    conn.close()
