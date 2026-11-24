"""Tests for Task 1: Strict Real-DB write guard.

Verifies:
1. Pytest isolation: Under pytest, get_db_path() and StateManager() redirect to temp copy when HARVEY_DB_PATH is unset.
2. CLI authorized: Production CLI entry points setting HARVEY_ALLOW_REAL_DB_WRITE=1 are permitted.
3. Script without authorization: Attempting to write to production data/harvey.db outside pytest raises a clear
   RuntimeError naming HARVEY_ALLOW_REAL_DB_WRITE=1, and zero bytes are written.
4. HARVEY_DB_PATH honored: Setting HARVEY_DB_PATH directs all operations to the specified copy/temp database.
5. SHA-256 invariant: Baseline hash of data/harvey.db is byte-for-byte identical before and after.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
import pytest

from harvey.paths import (
    _REAL_PROJECT_ROOT,
    get_db_path,
    check_real_db_write_permission,
    is_real_db_write_allowed,
)
from harvey.state import StateManager


BASELINE_SHA256 = "DDFEAE896B4D34260B6061D61BCCC91873E9BF06B05F5728B846DFE90FEC4D47"


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().upper()


def test_real_db_baseline_integrity():
    real_db = (_REAL_PROJECT_ROOT / "data" / "harvey.db").resolve()
    assert real_db.exists(), f"Production DB missing at {real_db}"
    current_hash = _hash_file(real_db)
    assert current_hash == BASELINE_SHA256, (
        f"Production DB hash altered! Current: {current_hash}, Expected: {BASELINE_SHA256}"
    )


def test_real_db_path_resolved_correctly(monkeypatch):
    """When HARVEY_DB_PATH is unset, get_db_path() returns canonical data/harvey.db without silent redirect."""
    monkeypatch.delenv("HARVEY_DB_PATH", raising=False)
    monkeypatch.delenv("HARVEY_ALLOW_REAL_DB_WRITE", raising=False)

    db_p = get_db_path()
    real_db = (_REAL_PROJECT_ROOT / "data" / "harvey.db").resolve()
    assert db_p == real_db, "get_db_path must return canonical data/harvey.db path"


def test_cli_authorized(monkeypatch):
    """When HARVEY_ALLOW_REAL_DB_WRITE=1 is set (as in harvey CLI / dashboard), check passes."""
    monkeypatch.setenv("HARVEY_ALLOW_REAL_DB_WRITE", "1")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("HARVEY_GUARD_REAL_DB", raising=False)

    assert is_real_db_write_allowed() is True
    # Should not raise
    real_db = (_REAL_PROJECT_ROOT / "data" / "harvey.db").resolve()
    check_real_db_write_permission(real_db)


@pytest.mark.asyncio
async def test_script_without_authorization_raises_clear_error(monkeypatch):
    """Outside pytest, attempting an unauthorized write to real DB raises RuntimeError naming HARVEY_ALLOW_REAL_DB_WRITE=1."""
    monkeypatch.delenv("HARVEY_DB_PATH", raising=False)
    monkeypatch.delenv("HARVEY_ALLOW_REAL_DB_WRITE", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("HARVEY_GUARD_REAL_DB", raising=False)
    monkeypatch.setattr("harvey.paths.is_under_pytest", lambda: False)
    monkeypatch.setattr("harvey.state.is_under_pytest", lambda: False)

    real_db = (_REAL_PROJECT_ROOT / "data" / "harvey.db").resolve()
    sha_before = _hash_file(real_db)
    size_before = real_db.stat().st_size

    # Direct permission check
    with pytest.raises(RuntimeError) as exc_info:
        check_real_db_write_permission(real_db)
    err_msg = str(exc_info.value)
    assert "HARVEY_ALLOW_REAL_DB_WRITE=1" in err_msg, f"Error must name variable to set: {err_msg}"
    assert "HARVEY_DB_PATH" in err_msg

    # StateManager init_db write attempt
    state = StateManager(db_path=str(real_db))
    with pytest.raises(RuntimeError) as exc_info2:
        await state.init_db()
    assert "HARVEY_ALLOW_REAL_DB_WRITE=1" in str(exc_info2.value)

    # Invariant: real DB byte-for-byte identical, 0 bytes written
    sha_after = _hash_file(real_db)
    size_after = real_db.stat().st_size
    assert sha_before == sha_after
    assert size_before == size_after


def test_subprocess_script_without_authorization():
    """An external script executed outside pytest fails with clear error and writes zero bytes."""
    import subprocess
    import sys

    real_db = (_REAL_PROJECT_ROOT / "data" / "harvey.db").resolve()
    sha_before = _hash_file(real_db)

    cmd = [
        sys.executable,
        "-c",
        (
            "import asyncio\n"
            "from harvey.state import StateManager\n"
            "async def run():\n"
            "    state = StateManager()\n"
            "    await state.init_db()\n"
            "asyncio.run(run())\n"
        )
    ]
    env = os.environ.copy()
    env.pop("HARVEY_DB_PATH", None)
    env.pop("HARVEY_ALLOW_REAL_DB_WRITE", None)
    env.pop("PYTEST_CURRENT_TEST", None)
    env.pop("HARVEY_GUARD_REAL_DB", None)

    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    assert proc.returncode != 0
    assert "HARVEY_ALLOW_REAL_DB_WRITE=1" in proc.stderr
    assert "HARVEY_DB_PATH" in proc.stderr

    sha_after = _hash_file(real_db)
    assert sha_before == sha_after


@pytest.mark.asyncio
async def test_harvey_db_path_honored(monkeypatch, tmp_path):
    """Setting HARVEY_DB_PATH is always honored for reads and writes."""
    temp_db = tmp_path / "custom_test.db"
    real_db = (_REAL_PROJECT_ROOT / "data" / "harvey.db").resolve()
    shutil.copy2(str(real_db), str(temp_db))

    monkeypatch.setenv("HARVEY_DB_PATH", str(temp_db))
    monkeypatch.delenv("HARVEY_ALLOW_REAL_DB_WRITE", raising=False)

    sha_real_before = _hash_file(real_db)

    # get_db_path resolves to custom DB
    assert get_db_path().resolve() == temp_db.resolve()

    # StateManager writes succeed on custom DB
    state = StateManager()
    assert Path(state.db_path).resolve() == temp_db.resolve()
    await state.init_db()

    # Invariant: real DB untouched
    sha_real_after = _hash_file(real_db)
    assert sha_real_before == sha_real_after
