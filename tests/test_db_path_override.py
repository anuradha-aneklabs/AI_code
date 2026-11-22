"""Tests for HARVEY_DB_PATH environment variable override (Part 1c)."""

import os
from pathlib import Path
import pytest

from harvey.paths import PROJECT_ROOT, _REAL_PROJECT_ROOT, get_db_path
from harvey.state import StateManager


def test_default_db_path_when_env_not_set(monkeypatch):
    """When HARVEY_DB_PATH is absent, get_db_path returns PROJECT_ROOT / 'data' / 'harvey.db'."""
    monkeypatch.delenv("HARVEY_DB_PATH", raising=False)
    expected = PROJECT_ROOT / "data" / "harvey.db"
    assert get_db_path() == expected

    sm = StateManager()
    assert Path(sm.db_path) == expected


def test_db_path_override_honored(tmp_path, monkeypatch, capsys):
    """When HARVEY_DB_PATH is set, get_db_path and StateManager use the override and print notice."""
    custom_db = tmp_path / "custom_test.db"
    monkeypatch.setenv("HARVEY_DB_PATH", str(custom_db))

    assert get_db_path() == custom_db.resolve()

    sm = StateManager()
    assert Path(sm.db_path) == custom_db.resolve()

    captured = capsys.readouterr()
    assert "[DB] Using database override:" in captured.out
    assert str(custom_db.resolve()) in captured.out


@pytest.mark.asyncio
async def test_state_manager_works_on_override_db(tmp_path, monkeypatch):
    """StateManager successfully initializes and operates on the overridden database."""
    custom_db = tmp_path / "sub" / "override.db"
    monkeypatch.setenv("HARVEY_DB_PATH", str(custom_db))

    sm = StateManager()
    await sm.init_db()
    assert custom_db.exists()

    # Save and read back research run
    run_id = "test_run_123"
    await sm.save_research_run(
        run_id=run_id,
        input_email="test@example.com",
        normalized_email="test@example.com",
        domain="example.com",
        status="running",
        started_at="2026-11-20T00:00:00",
    )
    run = await sm.get_research_run(run_id)
    assert run is not None
    assert run["domain"] == "example.com"


def test_redirecting_path_non_db_behavior_unchanged_when_env_not_set(monkeypatch):
    """When HARVEY_DB_PATH is not set, PROJECT_ROOT paths for config, prompts, etc.
    resolve exactly to their standard filesystem paths without alteration."""
    monkeypatch.delenv("HARVEY_DB_PATH", raising=False)

    assert (PROJECT_ROOT / "config").resolve() == (_REAL_PROJECT_ROOT / "config").resolve()
    assert (PROJECT_ROOT / "harvey.yaml").resolve() == (_REAL_PROJECT_ROOT / "harvey.yaml").resolve()
    assert (PROJECT_ROOT / ".env").resolve() == (_REAL_PROJECT_ROOT / ".env").resolve()
    assert (PROJECT_ROOT / "data" / "harvey.db").resolve() == (_REAL_PROJECT_ROOT / "data" / "harvey.db").resolve()
    assert (PROJECT_ROOT / "data" / "harvey.pid").resolve() == (_REAL_PROJECT_ROOT / "data" / "harvey.pid").resolve()

