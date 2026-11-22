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
    Otherwise, returns PROJECT_ROOT / "data" / "harvey.db".
    """
    env_path = os.environ.get("HARVEY_DB_PATH")
    if env_path and env_path.strip():
        return Path(env_path.strip()).resolve()
    return _REAL_PROJECT_ROOT / "data" / "harvey.db"
