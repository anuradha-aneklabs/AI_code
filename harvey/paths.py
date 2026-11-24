"""Canonical filesystem locations for the Harvey checkout.

Harvey is repo-centric: config (harvey.yaml, .env), knowledge (prompts/,
skills/), and state (data/) all live next to the package. ``resolve()``
matters — when the package is reached through a symlink (some editable
installs), a bare ``Path(__file__).parent`` would point into
site-packages and Harvey would silently read/write the wrong files.
"""

import os
import sys
from pathlib import Path

_REAL_PROJECT_ROOT = Path(__file__).resolve().parent.parent

_BasePath = type(_REAL_PROJECT_ROOT)


class _RedirectingPath(_BasePath):
    def __truediv__(self, other):
        res = super().__truediv__(other)
        env_db = os.environ.get("HARVEY_DB_PATH")
        if env_db and env_db.strip():
            default_db = (_REAL_PROJECT_ROOT / "data" / "harvey.db").resolve()
            try:
                if res.resolve() == default_db:
                    return Path(env_db.strip()).resolve()
            except Exception:
                pass
        return res


PROJECT_ROOT = _RedirectingPath(_REAL_PROJECT_ROOT)


def is_under_pytest() -> bool:
    """Check if execution is currently running under pytest or explicit test guard."""
    return bool(
        os.environ.get("PYTEST_CURRENT_TEST")
        or os.environ.get("HARVEY_GUARD_REAL_DB") == "1"
        or "pytest" in sys.modules
    )


def is_real_db_write_allowed() -> bool:
    """Check if real DB writes have been explicitly authorized by production CLI or dashboard."""
    return os.environ.get("HARVEY_ALLOW_REAL_DB_WRITE") == "1"


def check_real_db_write_permission(path: str | Path | None = None) -> None:
    """Raise RuntimeError if attempting an unauthorized write to production data/harvey.db outside pytest."""
    if is_under_pytest():
        return
    if is_real_db_write_allowed():
        return
    real_db = (_REAL_PROJECT_ROOT / "data" / "harvey.db").resolve()
    target = Path(path).resolve() if path else get_db_path().resolve()
    try:
        if target.resolve() == real_db:
            raise RuntimeError(
                "Direct write to production data/harvey.db is blocked. Set "
                "HARVEY_ALLOW_REAL_DB_WRITE=1 to authorize, or set "
                "HARVEY_DB_PATH to a copy database."
            )
    except Exception as e:
        if isinstance(e, RuntimeError):
            raise


def get_db_path() -> Path:
    """Return the database path, honoring the HARVEY_DB_PATH environment variable override.

    If HARVEY_DB_PATH is set in os.environ, returns Path(os.environ["HARVEY_DB_PATH"]).resolve().
    Otherwise, returns the canonical project database path.
    Direct unauthorized writes to production data/harvey.db are blocked by StateManager.
    """
    env_path = os.environ.get("HARVEY_DB_PATH")
    if env_path and env_path.strip():
        return Path(env_path.strip()).resolve()

    return (_REAL_PROJECT_ROOT / "data" / "harvey.db").resolve()

