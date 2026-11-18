"""Safe Dashboard Token Rotation Utility for Harvey AI Sales Agent.

Rotates the token in data/dashboard-token using cryptographically secure
random generation (secrets.token_urlsafe(32)).

Security rules:
- Does NOT print the new token to stdout, stderr, logs, or reports.
- Writes with strict owner-only file permissions.
- Verifies the new token format before replacing.
"""

from __future__ import annotations

import os
import re
import secrets
import stat
import sys
from pathlib import Path

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
TOKEN_FILE = DATA_DIR / "dashboard-token"


def rotate_token() -> bool:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Generate cryptographically secure token (32 bytes = ~43 chars URL-safe base64)
    new_token = secrets.token_urlsafe(32)
    if not TOKEN_RE.fullmatch(new_token):
        print("[ERROR] Token generation validation failed.")
        return False

    # Publish securely
    tmp_file = DATA_DIR / f".dashboard-token.{secrets.token_hex(8)}.tmp"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_BINARY", 0)
    )

    try:
        fd = os.open(tmp_file, flags, 0o600)
        try:
            if os.name != "nt":
                os.fchmod(fd, 0o600)
            os.write(fd, (new_token + "\n").encode("ascii"))
            os.fsync(fd)
        finally:
            os.close(fd)

        # Atomic replace or rename
        if TOKEN_FILE.exists():
            TOKEN_FILE.unlink()
        os.rename(tmp_file, TOKEN_FILE)

        # Verify readability
        read_back = TOKEN_FILE.read_text(encoding="ascii").strip()
        if read_back != new_token:
            print("[ERROR] Token verification failed after writing.")
            return False

        print("=" * 65)
        print(" [SUCCESS] Dashboard token rotated successfully.")
        print("=" * 65)
        print(f" Saved to: {TOKEN_FILE}")
        print(" Security: New token was created with 256-bit cryptographic entropy.")
        print(" Notice:   Token value is hidden for security.")
        print("           Open data/dashboard-token locally to copy your token.")
        print("=" * 65)
        return True

    except Exception as e:
        print(f"[ERROR] Failed to rotate dashboard token: {e}")
        try:
            if tmp_file.exists():
                tmp_file.unlink()
        except Exception:
            pass
        return False


if __name__ == "__main__":
    success = rotate_token()
    sys.exit(0 if success else 1)
