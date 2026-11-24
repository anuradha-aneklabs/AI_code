"""Canonical filesystem locations for the Harvey checkout.

Harvey is repo-centric: config (harvey.yaml, .env), knowledge (prompts/,
skills/), and state (data/) all live next to the package. ``resolve()``
matters — when the package is reached through a symlink (some editable
installs), a bare ``Path(__file__).parent`` would point into
site-packages and Harvey would silently read/write the wrong files.
"""

import os
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


def get_db_path() -> Path:
    """Return the database path, honoring the HARVEY_DB_PATH environment variable override.

    If HARVEY_DB_PATH is set in os.environ, returns Path(os.environ["HARVEY_DB_PATH"]).resolve().
    Otherwise, if real DB writes are not explicitly authorized by production CLI, returns an isolated
    guarded copy to protect data/harvey.db from unintended test or script writes.
    """
    env_path = os.environ.get("HARVEY_DB_PATH")
    if env_path and env_path.strip():
        return Path(env_path.strip()).resolve()

    real_db = (_REAL_PROJECT_ROOT / "data" / "harvey.db").resolve()
    is_prod_cli = os.environ.get("HARVEY_ALLOW_REAL_DB_WRITE") == "1"
    is_guarded = bool(
        os.environ.get("PYTEST_CURRENT_TEST")
        or os.environ.get("HARVEY_GUARD_REAL_DB") == "1"
        or not is_prod_cli
    )
    if is_guarded and real_db.exists():
        import tempfile
        import shutil
        tmp_dir = Path(tempfile.gettempdir())
        guarded_copy = tmp_dir / "harvey_guarded_test.db"
        if not guarded_copy.exists() or guarded_copy.stat().st_size != real_db.stat().st_size:
            shutil.copy2(str(real_db), str(guarded_copy))
        return guarded_copy

    return real_db
