"""Root conftest.py — runs before tests/conftest.py.

When HARVEY_DB_PATH is not already set, copies data/harvey.db to a
per-session temp file and sets HARVEY_DB_PATH to that copy.  This
guarantees that the full test suite (including tests/conftest.py's
_ensure_baseline_test_database fixture, test_universal_multi_industry.py's
StateManager(str(DB_PATH)), and any other test that implicitly opens the
default DB path) never writes to the real data/harvey.db.

Mechanism
---------
pytest collects conftest.py files in directory order: root first, then
sub-directories.  Setting os.environ["HARVEY_DB_PATH"] here — before any
test module or tests/conftest.py fixture runs — means every later
StateManager() and get_db_path() call in the session picks up the override
automatically, including those that receive the literal string
"data/harvey.db" (because harvey/state.py's __init__ compares resolved
paths and redirects matching ones to the override).

The temp file is intentionally NOT deleted at shutdown: it lets a failing
run be inspected post-mortem.  Pytest's tmp_path_factory cleans it on the
next run.
"""

import os
import shutil
import tempfile
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent
_REAL_DB = _PROJECT_ROOT / "data" / "harvey.db"


def pytest_configure(config):
    """Called once, before any fixture or test, before tests/conftest.py."""
    # Under pytest, set test-mode flag so tests with mock/stub transports run
    os.environ.setdefault("HARVEY_TEST_MODE", "1")
    os.environ.setdefault("HARVEY_TEST_MOCK_SEND_ALLOWED", "1")
    if os.environ.get("HARVEY_DB_PATH"):
        # Already set by the caller — honour it; don't double-redirect.
        return

    if not _REAL_DB.exists():
        # CI: no live DB yet; tests/conftest.py will create a minimal one.
        # Let it run against a temp path so the fixture's own creation is
        # still isolated.
        tmpdir = Path(tempfile.mkdtemp(prefix="harvey_pytest_"))
        copy_path = tmpdir / "harvey_test.db"
    else:
        tmpdir = Path(tempfile.mkdtemp(prefix="harvey_pytest_"))
        copy_path = tmpdir / "harvey_test.db"
        shutil.copy2(str(_REAL_DB), str(copy_path))

    os.environ["HARVEY_DB_PATH"] = str(copy_path)
    # Print once so the log shows what happened — not secret, just a path.
    print(
        f"\n[conftest] HARVEY_DB_PATH not set → using temp copy: {copy_path}",
        flush=True,
    )
