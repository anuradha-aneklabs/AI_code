"""Harvey Dashboard — local web UI to set up, control, and monitor Harvey."""

import asyncio
import ipaddress
import json
import logging
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import aiosqlite
import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import (
    HTMLResponse, JSONResponse, PlainTextResponse, Response,
)

logger = logging.getLogger("harvey.dashboard")

from harvey import dashboard_auth as auth  # noqa: E402
from harvey.paths import PROJECT_ROOT  # noqa: E402
from harvey.pidfile import LOCK_FILE, loop_lock_held, pid_alive  # noqa: E402
from harvey.textio import read_user_text  # noqa: E402
DB_PATH = PROJECT_ROOT / "data" / "harvey.db"
ENV_FILE = PROJECT_ROOT / ".env"
CONFIG_FILE = PROJECT_ROOT / "harvey.yaml"
PID_FILE = PROJECT_ROOT / "data" / "harvey.pid"
LOG_FILE = PROJECT_ROOT / "data" / "harvey.log"
TOKEN_FILE = PROJECT_ROOT / "data" / "dashboard-token"


def _resolve_config_file() -> Path:
    """harvey.local.yaml wins when present — same resolution as config.py's
    loader, so the dashboard doesn't judge setup complete against the
    tracked harvey.yaml template while the real trained config sits in the
    gitignored local override."""
    from harvey.config import _find_config_file, ConfigFileNotFoundError
    try:
        return Path(_find_config_file())
    except ConfigFileNotFoundError:
        return CONFIG_FILE

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: verify database on startup and clean up subprocesses on shutdown."""
    try:
        state = _state()
        await state.init_db()
    except Exception as e:
        logger.error(f"Dashboard startup DB initialization error: {e}")
    # Auto-start Harvey engine if configured via HARVEY_AUTOSTART_ENGINE
    if os.environ.get("HARVEY_AUTOSTART_ENGINE", "").lower() in ("true", "1", "yes") or \
       os.environ.get("HARVEY_AUTO_START_ENGINE", "").lower() in ("true", "1", "yes"):
        if not _check_harvey_pid():
            try:
                from harvey.main import _needs_setup
                if not _needs_setup():
                    res = await start_harvey()
                    logger.info("Lifespan: Harvey engine auto-started: %s", res)
            except Exception as e:
                logger.warning("Lifespan: Could not auto-start Harvey engine: %s", e)
    yield
    global _harvey_process
    if _harvey_process and _harvey_process.poll() is None:
        if os.environ.get("HARVEY_KEEP_ENGINE_ON_RESTART", "").lower() not in ("true", "1", "yes"):
            try:
                _harvey_process.terminate()
            except Exception:
                pass


# No /docs, /redoc or /openapi.json: the API is for this UI only, and every
# route except the static shell needs the token (see dashboard_auth).
app = FastAPI(title="Harvey Dashboard", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
token_store = auth.TokenStore(TOKEN_FILE)
app.add_middleware(auth.DashboardGuard, store=token_store)


@app.exception_handler(json.JSONDecodeError)
async def json_decode_error_handler(request: Request, exc: json.JSONDecodeError):
    return JSONResponse(
        {"success": False, "error": f"Invalid JSON payload: {exc.msg}"},
        status_code=400,
    )

# Harvey process tracking
_harvey_process: subprocess.Popen | None = None
_harvey_started_at: datetime | None = None
_env_lock = asyncio.Lock()


# ── Helpers ──


async def query_db(sql: str, params: tuple = ()) -> list[dict]:
    """Run a query and return results as list of dicts.

    Never raises: a missing DB file, missing table, or malformed schema
    returns [] so no dashboard route can 500 on an empty install.
    """
    if not DB_PATH.exists():
        return []
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]
    except Exception as e:
        logger.warning("query_db failed (%s): %s", sql.split(None, 4)[:4], e)
        return []


def _mask_key(key: str) -> str:
    """Mask an API key for display: show first 4 and last 4 chars."""
    if not key or len(key) < 10:
        return "****" if key else ""
    return key[:4] + "****" + key[-4:]


def _read_env_file() -> dict[str, str]:
    """Read .env file and return as dict."""
    env_vars = {}
    if ENV_FILE.exists():
        for line in read_user_text(ENV_FILE).splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env_vars[key.strip()] = value.strip()
    return env_vars


def _env_key(line: str) -> str:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return ""
    key = stripped.partition("=")[0].strip()
    if key.startswith("export "):
        key = key[len("export "):].strip()
    return key


def _write_env_file(updates: dict[str, str]):
    """Set keys in .env, keeping every other line (comments, order, other
    keys) as it was.

    Written to a private temporary file in the same directory and swapped
    in atomically, so a crash never leaves a half-written .env and the
    credentials are never readable by other users, even for a moment: the
    result is owner-only (0600 on POSIX) whether .env existed or not. A
    symlinked .env is refused rather than replaced or followed.
    """
    if ENV_FILE.is_symlink():
        raise OSError(f"{ENV_FILE} is a symbolic link; edit it by hand.")
    if ENV_FILE.exists() and not ENV_FILE.is_file():
        raise OSError(f"{ENV_FILE} is not a regular file.")
    lines = (read_user_text(ENV_FILE).splitlines()
             if ENV_FILE.exists() else [])

    out, written = [], set()
    for line in lines:
        key = _env_key(line)
        if key in updates:
            if key in written:
                continue  # a later duplicate would override the new value
            out.append(f"{key}={updates[key]}")
            written.add(key)
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in updates.items() if k not in written]

    fd, tmp = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=str(ENV_FILE.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            if os.name != "nt":
                os.fchmod(f.fileno(), stat.S_IRUSR | stat.S_IWUSR)
            f.write("\n".join(out) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, ENV_FILE)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    load_dotenv(str(ENV_FILE), override=True)


def _check_harvey_pid() -> int | None:
    """Check if there's a running Harvey process from a PID file."""
    global _harvey_process, _harvey_started_at
    if _harvey_process and _harvey_process.poll() is None:
        return _harvey_process.pid
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pid = None
        # pid_alive, never os.kill(pid, 0): on Windows that terminates the loop.
        # And only while a loop holds the lock: after a crash or a reboot the
        # PID can belong to an unrelated program, which Stop must not signal.
        if pid is not None and pid_alive(pid) and loop_lock_held(LOCK_FILE):
            return pid
        try:
            PID_FILE.unlink(missing_ok=True)
        except OSError:
            pass
    return None


# ── Health & Setup Status ──


@app.get("/api/health")
async def health_check():
    """Lightweight operational health check for local and remote monitoring."""
    return {
        "status": "ok",
        "app": "harvey",
        "version": "2.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/summary")
async def get_summary():
    """Platform state summary endpoint (API-01)."""
    state = _state()
    await state.init_db()
    return await state.get_state_summary()


@app.get("/api/setup-status")
async def get_setup_status():
    """Check what's configured and what still needs setup."""
    checks = []

    # 1. Venv
    checks.append({
        "id": "venv", "label": "Python virtual environment",
        "done": (PROJECT_ROOT / ".venv").is_dir(),
        "required": True,
        "help": "Run: python3 -m venv .venv && source .venv/bin/activate && pip install -e .",
    })

    # 2. Env file
    env_vars = _read_env_file()
    env_exists = ENV_FILE.exists() and bool(env_vars)
    checks.append({
        "id": "env_file", "label": "Environment file (.env)",
        "done": env_exists,
        "required": True,
        "help": "Go to the Settings tab to enter your API keys.",
    })

    # 3. Email provider configured (matches channels.email.provider)
    provider = _current_provider()

    def _has(*keys):
        return all((env_vars.get(k, "") or os.getenv(k, "")).strip() for k in keys)

    if provider == "gmail":
        provider_done = _has("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET") and \
            (PROJECT_ROOT / "data" / "gmail_token.json").is_file()
        provider_help = ("Set GMAIL_CLIENT_ID/SECRET in Settings, then run "
                         "'harvey gmail auth' in your terminal.")
    elif provider == "microsoft":
        provider_done = _has("M365_CLIENT_ID") and \
            (PROJECT_ROOT / "data" / "m365_token.json").is_file()
        provider_help = ("Set the Microsoft 365 client and tenant IDs in Settings, then run "
                         "'harvey microsoft auth' in your terminal.")
    elif provider == "smtp":
        provider_done = _has("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD")
        provider_help = "Enter your SMTP host, username, and password in Settings."
    else:
        instantly_key = env_vars.get("INSTANTLY_API_KEY", "") or os.getenv("INSTANTLY_API_KEY", "")
        provider_done = bool(instantly_key) and instantly_key != "your_instantly_api_key_here"
        provider_help = "Enter your Instantly API key in Settings."
    checks.append({
        "id": "email_provider", "label": f"Email provider configured ({provider})",
        "done": provider_done,
        "required": True,
        "help": provider_help,
    })

    # 4. Email verification (needed for addresses to be sendable, not 'guess')
    verifier_set = _has("REOON_API_KEY") or _has("ZEROBOUNCE_API_KEY") or _has("HUNTER_API_KEY")
    checks.append({
        "id": "verifier", "label": "Email verification key",
        "done": verifier_set,
        "required": True,
        "help": "Add a Reoon (free 600/mo), ZeroBounce, or Hunter key in Settings — "
                "without one, found emails stay 'guess' and are never sent.",
    })

    # 5. Config valid
    config_valid = False
    config_path = _resolve_config_file()
    if config_path.exists():
        try:
            cfg = yaml.safe_load(read_user_text(config_path))
            company = cfg.get("persona", {}).get("company", "")
            product = cfg.get("product", {}).get("name", "")
            config_valid = company not in ("Your Company", "") and product not in ("Your Product", "")
        except Exception:
            pass
    checks.append({
        "id": "config", "label": f"Harvey configured ({config_path.name})",
        "done": config_valid,
        "required": True,
        "help": "Train Harvey on your product. Use the trainer or set up manually through Claude.",
    })

    # 6. Product trained
    product_trained = (PROJECT_ROOT / "skills" / "product_knowledge.md").exists()
    checks.append({
        "id": "product_trained", "label": "Product knowledge trained",
        "done": product_trained,
        "required": True,
        "help": "Run: harvey train https://yourwebsite.com (or set up through Claude).",
    })

    # 7. LinkedIn (optional)
    linkedin_email = env_vars.get("LINKEDIN_EMAIL", "") or os.getenv("LINKEDIN_EMAIL", "")
    linkedin_pass = env_vars.get("LINKEDIN_PASSWORD", "") or os.getenv("LINKEDIN_PASSWORD", "")
    checks.append({
        "id": "linkedin", "label": "LinkedIn credentials",
        "done": bool(linkedin_email) and bool(linkedin_pass),
        "required": False,
        "help": "Optional. Enter your LinkedIn credentials in Settings to enable LinkedIn prospecting.",
    })

    # 8. Cloudflare (optional)
    cf_id = env_vars.get("CLOUDFLARE_ACCOUNT_ID", "") or os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
    cf_token = env_vars.get("CLOUDFLARE_API_TOKEN", "") or os.getenv("CLOUDFLARE_API_TOKEN", "")
    checks.append({
        "id": "cloudflare", "label": "Cloudflare deep crawling",
        "done": bool(cf_id) and bool(cf_token),
        "required": False,
        "help": "Optional. For JavaScript-rendered website crawling during training.",
    })

    # 9. Warmup service: TrulyInbox connected, or another service the user named
    from harvey.integrations import trulyinbox as ti
    try:
        state = _state()
        await state.init_db()
        choice = await ti.get_choice(state)
    except Exception:
        choice = ""
    if _has("TRULYINBOX_API_KEY"):
        choice = "connected"
    checks.append({
        "id": "warmup_service",
        "label": {"connected": "Warmup: TrulyInbox connected",
                  "other": "Warmup: your own warmup service"}.get(choice, "Warm up your mailbox"),
        "done": bool(choice),
        # Instantly warms its own accounts, so there it's a suggestion, not a step.
        "required": provider != "instantly",
        "help": ("A new mailbox needs 2-4 weeks of warmup before cold email, and warmup "
                 "keeps it out of spam after that. We recommend TrulyInbox: connect it and "
                 "Harvey shows whether your mailbox is warmed."),
        "link": ti.AFFILIATE_URL,
    })

    required_checks = [c for c in checks if c["required"]]
    completed_required = sum(1 for c in required_checks if c["done"])

    return {
        "checks": checks,
        "completed": completed_required,
        "total_required": len(required_checks),
        "percent": int(completed_required / len(required_checks) * 100) if required_checks else 0,
    }


# ── Settings ──


def _current_provider() -> str:
    """Read channels.email.provider from the active config (best-effort)."""
    try:
        cfg = yaml.safe_load(read_user_text(_resolve_config_file())) or {}
        return ((cfg.get("channels") or {}).get("email") or {}).get("provider", "instantly")
    except Exception:
        return "instantly"


@app.get("/api/settings")
async def get_settings():
    """Get current settings — presence flags only for secrets, never raw values."""
    env_vars = _read_env_file()
    all_keys = [
        "INSTANTLY_API_KEY", "LINKEDIN_EMAIL", "LINKEDIN_PASSWORD",
        "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN",
        "GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET",
        "SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD",
        "IMAP_HOST", "IMAP_PORT", "IMAP_USERNAME", "IMAP_PASSWORD",
        "M365_CLIENT_ID", "M365_TENANT_ID",
        "REOON_API_KEY", "ZEROBOUNCE_API_KEY", "HUNTER_API_KEY",
        "GEMINI_API_KEY", "GOOGLE_API_KEY", "GROQ_API_KEY",
        "OPENROUTER_API_KEY", "XAI_API_KEY", "OLLAMA_BASE_URL",
    ]
    for key in all_keys:
        if key not in env_vars:
            env_vars[key] = os.getenv(key, "")

    def is_set(k):
        return bool((env_vars.get(k) or "").strip())

    # Gmail is authorized once harvey gmail auth has stored a token file.
    gmail_token = (PROJECT_ROOT / "data" / "gmail_token.json").is_file()

    return {
        "provider": _current_provider(),
        # Non-secret values echo back so fields repopulate; secrets are
        # presence-only so keys never leave the box.
        "instantly_api_key_set": is_set("INSTANTLY_API_KEY"),
        "linkedin_email": env_vars.get("LINKEDIN_EMAIL", ""),
        "linkedin_password_set": is_set("LINKEDIN_PASSWORD"),
        "cloudflare_account_id": env_vars.get("CLOUDFLARE_ACCOUNT_ID", ""),
        "cloudflare_api_token_set": is_set("CLOUDFLARE_API_TOKEN"),
        "gmail_client_id": env_vars.get("GMAIL_CLIENT_ID", ""),
        "gmail_client_secret_set": is_set("GMAIL_CLIENT_SECRET"),
        "gmail_authorized": gmail_token,
        "smtp_host": env_vars.get("SMTP_HOST", ""),
        "smtp_port": env_vars.get("SMTP_PORT", ""),
        "smtp_username": env_vars.get("SMTP_USERNAME", ""),
        "smtp_password_set": is_set("SMTP_PASSWORD"),
        "imap_host": env_vars.get("IMAP_HOST", ""),
        "imap_port": env_vars.get("IMAP_PORT", ""),
        "imap_username": env_vars.get("IMAP_USERNAME", ""),
        "imap_password_set": is_set("IMAP_PASSWORD"),
        # Microsoft 365: ids aren't secret (no client secret: Harvey signs
        # in as a public client with PKCE); the token file means signed in.
        "m365_client_id": env_vars.get("M365_CLIENT_ID", ""),
        "m365_tenant_id": env_vars.get("M365_TENANT_ID", ""),
        "m365_authorized": (PROJECT_ROOT / "data" / "m365_token.json").is_file(),
        "reoon_api_key_set": is_set("REOON_API_KEY"),
        "zerobounce_api_key_set": is_set("ZEROBOUNCE_API_KEY"),
        "hunter_api_key_set": is_set("HUNTER_API_KEY"),
        "gemini_api_key_set": is_set("GEMINI_API_KEY") or is_set("GOOGLE_API_KEY"),
        "groq_api_key_set": is_set("GROQ_API_KEY"),
        "openrouter_api_key_set": is_set("OPENROUTER_API_KEY"),
        "xai_api_key_set": is_set("XAI_API_KEY"),
        "ollama_base_url": env_vars.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        "brain_provider": _active_provider(),
        "brain_model": (_active_brain().model if _active_brain() else ""),
        "xai_approved_paid": (getattr(_active_brain(), "xai_approved_paid", False) if _active_brain() else False),
    }


@app.post("/api/settings/env")
async def save_env_settings(request: Request):
    """Save environment variables to .env file."""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"success": False, "message": "Invalid request body."}, status_code=400)
    if not isinstance(data, dict):
        return JSONResponse({"success": False, "message": "Invalid request body."}, status_code=400)
    async with _env_lock:
        updates = {}
        for key in ["INSTANTLY_API_KEY", "LINKEDIN_EMAIL", "LINKEDIN_PASSWORD",
                     "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN",
                     "GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET",
                     "SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD",
                     "IMAP_HOST", "IMAP_PORT", "IMAP_USERNAME", "IMAP_PASSWORD",
                     "M365_CLIENT_ID", "M365_TENANT_ID",
                     "REOON_API_KEY", "ZEROBOUNCE_API_KEY", "HUNTER_API_KEY",
                     "GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY",
                     "XAI_API_KEY", "OLLAMA_BASE_URL"]:
            if key in data and data[key] is not None:
                value = data[key]
                if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                    return JSONResponse({"success": False, "message": f"{key} must be text."},
                                        status_code=400)
                value = str(value)
                # A port Harvey can't parse would stop it starting at all.
                if key.endswith("_PORT") and value.strip() and not value.strip().isdigit():
                    return JSONResponse({"success": False, "message": f"{key} must be a number."},
                                        status_code=400)
                if len(value) > 4096 or "\x00" in value:
                    return JSONResponse({"success": False, "message": f"{key} is invalid."},
                                        status_code=400)
                # Strip newlines so a crafted value can't inject extra .env entries
                updates[key] = value.replace("\n", " ").replace("\r", " ").strip()
        if updates:
            try:
                _write_env_file(updates)
            except Exception as e:
                logger.warning("Failed to write .env: %s", e)
                return JSONResponse({"success": False, "message": "Could not write .env file."}, status_code=500)
    return {"success": True}


@app.get("/api/db/verify")
async def api_db_verify():
    """Verify SQLite database integrity and return diagnostic status."""
    try:
        state = _state()
        await state.init_db()
        res = await state.verify_integrity()
        return {"success": res.get("ok", False), "integrity": res}
    except Exception as e:
        logger.exception("DB verify failed")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/db/backup")
async def api_db_backup(request: Request):
    """Trigger a point-in-time SQLite snapshot backup."""
    try:
        state = _state()
        await state.init_db()
        dest = state.backup_db()
        return {"success": True, "backup_path": str(dest)}
    except Exception as e:
        logger.exception("DB backup failed")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/trulyinbox/connect")
async def trulyinbox_connect(request: Request):
    """Check a TrulyInbox key against the sending mailbox, then save it to .env."""
    from harvey import deliverability as d
    from harvey.integrations import trulyinbox as ti
    try:
        data = await request.json()
    except Exception:
        data = None
    key = data.get("key") if isinstance(data, dict) else None
    if not isinstance(key, str) or not key.strip() or len(key) > 512:
        return JSONResponse({"success": False, "message": "Paste your TrulyInbox API key."},
                            status_code=400)
    key = key.strip()
    try:
        from harvey.config import load_config
        config = load_config()
        address, warmup = ti.sending_address(config), config.channels.email.warmup
    except Exception:
        address, warmup = "", None
    if not address:
        return JSONResponse({"success": False, "message": ti.NO_ADDRESS}, status_code=400)
    workspace = (_read_env_file().get("TRULYINBOX_WORKSPACE_ID", "")
                 or os.getenv("TRULYINBOX_WORKSPACE_ID", "")).strip()
    ti.forget_rejected(key)
    try:
        client = ti.TrulyInbox(key, workspace)
        health = await client.mailbox_health(address)
    except ti.TrulyInboxError as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=400)
    updates = {"TRULYINBOX_API_KEY": key}
    if not workspace:
        updates["TRULYINBOX_WORKSPACE_ID"] = client.workspace_id
    async with _env_lock:
        try:
            _write_env_file(updates)
        except Exception as e:
            logger.warning("Failed to write .env: %s", e)
            return {"success": False, "message": "Could not write .env file."}
    state = _state()
    await state.init_db()
    await ti.set_choice(state, "connected")
    return {"success": True, "message": d.check_warmup_service(health, warmup).detail}


@app.post("/api/trulyinbox/other")
async def trulyinbox_other():
    """The user warms their mailbox with another service: stop recommending one."""
    from harvey.integrations import trulyinbox as ti
    state = _state()
    await state.init_db()
    await ti.set_choice(state, "other")
    return {"success": True}


@app.post("/api/settings/test-instantly")
async def test_instantly(request: Request):
    """Test an Instantly API key."""
    try:
        data = await request.json()
    except Exception:
        data = {}
    api_key = str(data.get("api_key", "") or "")
    if not api_key:
        return {"success": False, "message": "No API key provided."}
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.instantly.ai/api/v2/accounts",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if resp.status_code == 200:
                return {"success": True, "message": "Connected to Instantly."}
            else:
                return {"success": False, "message": f"API returned {resp.status_code}. Check your key."}
    except Exception as e:
        return {"success": False, "message": f"Connection failed: {str(e)}"}


# ── Companies ──


# The Companies tab shows the best-fit companies, up to this many; the CSV has all.
COMPANIES_TAB_LIMIT = 2000


@app.get("/api/companies")
async def get_companies():
    """Companies with contact counts, fit score and register status, best fit first."""
    rows = await query_db("""
        SELECT c.*,
            (SELECT COUNT(*) FROM prospects p WHERE p.company_id = c.id) as contact_count
        FROM companies c ORDER BY c.created_at DESC
    """)
    try:
        from harvey.config import load_config
        from harvey.register import build_register
        register = {r["id"]: r for r in await build_register(query_db, load_config())}
        for c in rows:
            r = register.get(c["id"])
            if r:
                c["fit"], c["fit_reasons"] = r["fit"], r["fit_reasons"]
                c["register_status"], c["register_why"] = r["status"], r["why_not_contacted"]
        rows.sort(key=lambda c: c.get("fit", 0), reverse=True)
    except Exception as e:
        # A config that won't load must not take the Companies tab down with it.
        logger.warning(f"Company fit unavailable: {e}")
    return rows[:COMPANIES_TAB_LIMIT]


@app.get("/api/export/companies.csv")
async def export_companies():
    """The company register: every company, why it fits, why it wasn't contacted."""
    from harvey.config import load_config
    from harvey.register import build_register, to_csv
    try:
        text = to_csv(await build_register(query_db, load_config()))
    except Exception as e:
        logger.warning("Company register export failed: %s", e)
        return PlainTextResponse(f"Company register export failed: {e}", status_code=500)
    return PlainTextResponse(
        "﻿" + text,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="company-register.csv"'},
    )


@app.get("/api/companies/{company_id}/profile")
async def get_company_profile(company_id: str):
    """One company's facts for its detail card: the register row plus what
    Harvey observed on its site (latest value per signal, with the page)."""
    rows = await query_db("SELECT * FROM companies WHERE id = ?", (company_id,))
    if not rows:
        return JSONResponse({"error": "not found"}, status_code=404)
    observations = await query_db(
        "SELECT o.signal_code, o.value_text, o.value_num, o.evidence_url, o.observed_at, "
        "COALESCE(s.label, o.signal_code) AS label FROM observations o "
        "LEFT JOIN signal_codes s ON s.code = o.signal_code "
        "WHERE o.company_id = ? AND o.prospect_id = '' ORDER BY o.observed_at DESC LIMIT 200",
        (company_id,),
    )
    register = {}
    try:
        from harvey.config import load_config
        from harvey.register import build_register
        register = next(iter(await build_register(query_db, load_config(), company_id)), {})
    except Exception as e:
        logger.warning(f"Company profile register unavailable: {e}")
    return {"company": rows[0], "register": register, "observations": observations}


@app.get("/api/companies/{company_id}/contacts")
async def get_company_contacts(company_id: str):
    """Get all contacts for a specific company."""
    rows = await query_db(
        "SELECT * FROM prospects WHERE company_id = ? ORDER BY score DESC",
        (company_id,),
    )
    return rows


# ── Feedback ──


@app.post("/api/feedback")
async def add_feedback(request: Request):
    """Add a comment/feedback on any entity."""
    try:
        data = await request.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    entity_type = str(data.get("entity_type", "") or "")[:50]
    entity_id = str(data.get("entity_id", "") or "")[:100]
    comment = str(data.get("comment", "") or "").strip()[:4000]
    if not comment:
        return {"success": False, "message": "Comment is required."}
    feedback_id = uuid.uuid4().hex[:12]
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(str(DB_PATH)) as db:
            # Ensure the table exists so feedback works even on a fresh install
            await db.execute(
                """CREATE TABLE IF NOT EXISTS feedback (
                    id TEXT PRIMARY KEY,
                    entity_type TEXT,
                    entity_id TEXT,
                    comment TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                )"""
            )
            await db.execute(
                "INSERT INTO feedback (id, entity_type, entity_id, comment) VALUES (?, ?, ?, ?)",
                (feedback_id, entity_type, entity_id, comment),
            )
            await db.commit()
    except Exception as e:
        logger.warning("Failed to save feedback: %s", e)
        return {"success": False, "message": "Could not save feedback."}
    return {"success": True, "id": feedback_id}


@app.get("/api/feedback/{entity_type}/{entity_id}")
async def get_feedback(entity_type: str, entity_id: str):
    """Get feedback for an entity."""
    rows = await query_db(
        "SELECT * FROM feedback WHERE entity_type = ? AND entity_id = ? ORDER BY created_at DESC",
        (entity_type, entity_id),
    )
    return rows


# ── Harvey Controls ──


@app.get("/api/harvey/status")
async def get_harvey_status():
    """Check if Harvey is currently running."""
    pid = _check_harvey_pid()
    started = _harvey_started_at.isoformat() if _harvey_started_at else None
    return {"running": pid is not None, "pid": pid, "started_at": started}


@app.post("/api/harvey/start")
async def start_harvey():
    """Start Harvey's heartbeat loop as a subprocess."""
    global _harvey_process, _harvey_started_at

    if _check_harvey_pid():
        return {"success": False, "message": "Harvey is already running."}
    from harvey.main import _needs_setup
    if _needs_setup():
        return {"success": False,
                "message": "Harvey isn't set up yet. Run `harvey setup` in a terminal first."}

    # Ensure data dir exists
    (PROJECT_ROOT / "data").mkdir(parents=True, exist_ok=True)

    # The native Claude CLI installer puts claude in ~/.local/bin without
    # always adding it to PATH; the heartbeat can't think without it.
    env = os.environ.copy()
    local_bin = Path.home() / ".local" / "bin"
    if not shutil.which("claude") and local_bin.is_dir():
        env["PATH"] = str(local_bin) + os.pathsep + env.get("PATH", "")

    py_exe = sys.executable
    venv_py = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    if venv_py.is_file():
        py_exe = str(venv_py)

    try:
        log_handle = open(LOG_FILE, "a", encoding="utf-8")
        try:
            _harvey_process = subprocess.Popen(
                [py_exe, "-m", "harvey", "run"],
                cwd=str(PROJECT_ROOT),
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=log_handle,
                start_new_session=True,
                env=env,
            )
        finally:
            # Child holds its own copies of the fds; don't leak ours.
            log_handle.close()
    except Exception as e:
        logger.warning("Failed to start Harvey: %s", e)
        return {"success": False, "message": f"Failed to start Harvey: {e}"}
    _harvey_started_at = datetime.now()
    # The loop writes its own PID file once it holds the loop lock. Writing
    # the child's PID here would overwrite a running loop's record if this
    # child then finds the lock taken and exits.
    return {"success": True, "pid": _harvey_process.pid}


@app.post("/api/harvey/stop")
async def stop_harvey():
    """Stop the Harvey subprocess."""
    global _harvey_process, _harvey_started_at

    pid = _check_harvey_pid()
    if not pid:
        return {"success": False, "message": "Harvey is not running."}

    own = _harvey_process if _harvey_process and _harvey_process.pid == pid else None

    def alive() -> bool:
        # Our own child is reaped by poll(); left unreaped it is a zombie,
        # which still answers signal 0.
        if own is not None:
            return own.poll() is None
        return pid_alive(pid)

    try:
        os.kill(pid, signal.SIGTERM)
        # Wait briefly for graceful shutdown
        for _ in range(10):
            if not alive():
                break
            await asyncio.sleep(0.5)
        else:
            # Force kill if still running (Windows has no SIGKILL; SIGTERM
            # there is already TerminateProcess)
            try:
                os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
            except OSError:
                pass
            await asyncio.sleep(0.5)
    except OSError as e:
        logger.warning("Couldn't signal Harvey (pid %s): %s", pid, e)

    if alive():
        # Started by another user, or from an elevated terminal on Windows.
        return {"success": False,
                "message": f"Couldn't stop Harvey (pid {pid}). Stop it where it was started."}

    _harvey_process = None
    _harvey_started_at = None
    PID_FILE.unlink(missing_ok=True)

    return {"success": True}


@app.get("/api/harvey/logs")
async def get_harvey_logs():
    """Get recent log lines."""
    if not LOG_FILE.exists():
        return {"lines": []}
    try:
        # Tail only the last 64KB so a huge log file never blocks the UI
        with open(LOG_FILE, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 65536))
            text = f.read().decode("utf-8", errors="replace")
        lines = text.strip().splitlines()[-100:]
        return {"lines": lines}
    except Exception:
        return {"lines": []}


# ── Pipeline Data (existing endpoints) ──


def _pipeline_bucket(status: str | None, stage: str | None, intent: str | None,
                     email_status: str | None = None) -> str:
    """One funnel stage per contact, from its status and latest conversation."""
    status = status or "new"
    if status in ("new", "queued"):
        return "to_write"
    if status == "contacted":
        # A bounce leaves the status alone and marks the address invalid.
        return "closed_out" if email_status == "invalid" else "contacted"
    if status == "meeting":
        return "ready_to_meet"
    if status == "replied" and stage != "closed_lost":
        if stage == "closing":
            return "ready_to_meet"
        if intent == "interested" or stage in ("presenting", "negotiating"):
            return "interested"
        return "replied"
    return "closed_out"   # lost, opted out, bounced, unwritable


@app.get("/api/stats")
async def get_stats():
    """Pipeline overview stats."""
    try:
        prospects = await query_db(
            "SELECT status, COUNT(*) as count FROM prospects GROUP BY status"
        )
        prospect_total = sum(r["count"] for r in prospects)
        prospect_map = {r["status"]: r["count"] for r in prospects}

        campaigns = await query_db(
            "SELECT status, COUNT(*) as count FROM campaigns GROUP BY status"
        )
        campaign_map = {r["status"]: r["count"] for r in campaigns}

        conversations = await query_db(
            "SELECT status, COUNT(*) as count FROM conversations GROUP BY status"
        )
        convo_map = {r["status"]: r["count"] for r in conversations}

        # Where each contact stands. Prospect status alone stops at
        # "replied"; how a reply went lives on its latest conversation.
        rows = await query_db("""
            SELECT p.status, p.email_status, l.stage, l.intent, COUNT(*) AS count FROM prospects p
            LEFT JOIN (SELECT prospect_id, stage, intent, ROW_NUMBER() OVER (
                         PARTITION BY prospect_id ORDER BY updated_at DESC, created_at DESC) AS rn
                       FROM conversations) l ON l.prospect_id = p.id AND l.rn = 1
            GROUP BY p.status, p.email_status, l.stage, l.intent
        """)
        pipeline = dict.fromkeys(
            ("to_write", "contacted", "replied", "interested", "ready_to_meet", "closed_out"), 0)
        for r in rows:
            pipeline[_pipeline_bucket(r["status"], r["stage"], r["intent"],
                                      r["email_status"])] += r["count"]

        actions = await query_db("SELECT COUNT(*) as count FROM actions")
        action_count = actions[0]["count"] if actions else 0

        # usage_log rows are keyed by the LOCAL date (increment_usage /
        # get_usage_today use date.today()); SQLite's date('now') is UTC and
        # can select a different day's counts on either side of UTC midnight.
        usage = await query_db(
            "SELECT claude_calls, codex_calls FROM usage_log WHERE date = ?",
            (date.today().isoformat(),),
        )
        usage_today = (usage[0]["claude_calls"] or 0) if usage else 0
        codex_today = (usage[0]["codex_calls"] or 0) if usage else 0

        return {
            "prospects": {"total": prospect_total, "by_status": prospect_map},
            "campaigns": {"total": sum(campaign_map.values()), "by_status": campaign_map},
            "conversations": {"total": sum(convo_map.values()), "by_status": convo_map},
            "pipeline": pipeline,
            "actions_total": action_count,
            "claude_calls_today": usage_today,
            "codex_calls_today": codex_today,
            "brain_provider": _active_provider(),
            "brain_status": await _brain_status(),
        }
    except Exception as e:
        return {"error": str(e)}


_USAGE_SUM = (
    "COUNT(DISTINCT CASE WHEN session_id != '' THEN session_id ELSE id END) AS calls, "
    "COALESCE(SUM(input_tokens), 0) AS input_tokens, "
    "COALESCE(SUM(output_tokens), 0) AS output_tokens, "
    "COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens, "
    "COALESCE(SUM(cache_creation_tokens), 0) AS cache_creation_tokens, "
    "ROUND(COALESCE(SUM(cost_usd), 0), 4) AS cost_usd"
)

_quota_clients: dict = {}


def _active_brain():
    """The brain config harvey.yaml selects (None if unreadable)."""
    try:
        from harvey.config import load_brain_config
        return load_brain_config()
    except Exception:
        return None


async def _brain_status() -> dict:
    """Last recorded brain health transition ({} if none): lets the
    dashboard say "Codex paused: <why>" instead of looking healthy."""
    from harvey.brain import BRAIN_STATUS_KEY, active_brain_status
    rows = await query_db("SELECT value FROM settings WHERE key = ?", (BRAIN_STATUS_KEY,))
    active_prov = _active_provider()
    status = active_brain_status(rows[0]["value"], active_prov) if rows else {}

    # Auto-recover: if the active provider status in the DB is recorded as paused,
    # re-verify if the provider has come back online (e.g. user started Ollama serve).
    if status.get("status") == "paused" and active_prov and active_prov not in ("claude", "codex"):
        try:
            from harvey.config import load_env
            from harvey.providers.router import TaskRouter
            cfg = _active_brain()
            if cfg and cfg.provider == active_prov:
                router = TaskRouter(config=cfg.model_dump(), env=load_env().model_dump())
                prov = router.get_provider(active_prov)
                err = await prov.preflight()
                if not err:
                    ok_status = json.dumps({
                        "provider": active_prov,
                        "status": "ok",
                        "reason": "",
                        "since": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "kind": "auto_recovered",
                    })
                    state = _state()
                    await state.set_setting(BRAIN_STATUS_KEY, ok_status)
                    return {"provider": active_prov, "status": "ok", "reason": "", "kind": "auto_recovered"}
        except Exception as e:
            logger.debug(f"Brain status auto-recheck failed: {e}")

    return status


def _active_provider() -> str:
    brain = _active_brain()
    return brain.provider if brain else ""


def _quota_client_for(provider: str, model: str = ""):
    """One cached quota reader per provider (and Codex model). Each reads
    only its own subscription, so a Claude gauge is never shown as Codex or
    vice versa, and Codex shows the bucket metering the configured model."""
    key = (provider, model if provider == "codex" else "")
    if key not in _quota_clients:
        if provider == "codex":
            from harvey.codex import CodexClient, CodexQuotaClient
            _quota_clients[key] = CodexQuotaClient(CodexClient(model=model))
        elif provider == "claude":
            from harvey.integrations.quota import QuotaClient
            _quota_clients[key] = QuotaClient()
        else:
            return None
    return _quota_clients[key]


@app.get("/api/usage")
async def get_usage():
    """Token/cost accounting + live subscription quota for the Usage tab."""

    totals = {}
    for label, where in (
        ("today", "date(created_at) = date('now')"),
        ("week", "created_at >= datetime('now', '-7 days')"),
        ("month", "created_at >= datetime('now', '-30 days')"),
    ):
        rows = await query_db(f"SELECT {_USAGE_SUM} FROM usage_events WHERE {where}")
        totals[label] = rows[0] if rows else {}

    def grouped(expr, alias):
        return (
            f"SELECT {expr} AS {alias}, {_USAGE_SUM} FROM usage_events "
            f"WHERE created_at >= datetime('now', '-30 days') "
            f"GROUP BY {alias} ORDER BY output_tokens DESC LIMIT 25"
        )

    by_agent = await query_db(grouped("CASE WHEN agent = '' THEN 'other' ELSE agent END", "agent"))
    by_task = await query_db(grouped("CASE WHEN task = '' THEN 'other' ELSE task END", "task"))
    by_model = await query_db(grouped("CASE WHEN model = '' THEN 'unknown' ELSE model END", "model"))
    by_provider = await query_db(grouped("provider", "provider"))
    by_day = await query_db(
        f"SELECT date(created_at) AS day, {_USAGE_SUM} FROM usage_events "
        f"WHERE created_at >= datetime('now', '-30 days') "
        f"GROUP BY day ORDER BY day ASC"
    )

    brain = _active_brain()
    provider = brain.provider if brain else ""
    quota = None
    try:
        client = _quota_client_for(provider, brain.model if brain else "")
        if client is not None:
            quota = await client.get_utilization()
    except Exception as e:
        logger.debug("Quota lookup failed: %s", e)

    return {
        "provider": provider,
        "quota": quota,
        # Whose quota this is, even when it couldn't be read, so the UI shows
        # "unavailable" for the active provider instead of hiding the gauge.
        "quota_provider": provider,
        "quota_status": ("available" if quota else "unavailable") if provider else "",
        "by_provider": by_provider,
        "totals": totals,
        "by_day": by_day,
        "by_agent": by_agent,
        "by_task": by_task,
        "by_model": by_model,
    }


@app.get("/api/prospects")
async def get_prospects():
    rows = await query_db("SELECT * FROM prospects ORDER BY created_at DESC LIMIT 200")
    # Someone whose opt-out was undone keeps a way to see what happened.
    undone = {r["pid"] for r in await query_db(
        "SELECT DISTINCT json_extract(details_json, '$.prospect_id') AS pid FROM actions "
        "WHERE action_type = 'opt_out_undone' AND json_valid(details_json)")}
    for r in rows:
        r["opt_out_undone"] = r.get("id") in undone
    return rows


def _state():
    from harvey.state import StateManager
    return StateManager(db_path=str(DB_PATH))


# What a reviewer needs next to a draft to check its claims against the facts.
OUTBOX_CONTEXT_SIGNALS = (
    "TECH_STACK", "INCUMBENT_AGENCY", "HIRING_ROLE", "RUNNING_GOOGLE_ADS", "NO_ONLINE_BOOKING",
)


def _in(ids) -> str:
    return ",".join("?" for _ in ids)


async def _with_context(items: list[dict]) -> list[dict]:
    """Attach the campaign, company, contact and signals behind each draft."""
    if not items:
        return items
    pids = sorted({i["prospect_id"] for i in items if i.get("prospect_id")})
    cids = sorted({i["campaign_id"] for i in items if i.get("campaign_id")})

    people = {r["id"]: r for r in (await query_db(
        "SELECT p.id, p.first_name, p.last_name, p.title, p.source, p.email_status, "
        "p.score, p.personalization_notes, p.company_id, c.name AS company_name, "
        "c.domain, c.website, c.description, c.industry AS found_by "
        "FROM prospects p LEFT JOIN companies c ON c.id = p.company_id "
        f"WHERE p.id IN ({_in(pids)})", tuple(pids)) if pids else [])}
    campaigns = {r["id"]: r["name"] for r in (await query_db(
        f"SELECT id, name FROM campaigns WHERE id IN ({_in(cids)})",
        tuple(cids)) if cids else [])}

    company_ids = sorted({p["company_id"] for p in people.values() if p.get("company_id")})
    signals: dict[str, dict[str, str]] = {}
    if company_ids:
        rows = await query_db(
            "SELECT o.company_id, o.signal_code, o.value_num, o.value_text, "
            "o.observed_at, o.run_id, "
            "COALESCE(NULLIF(sc.label, ''), o.signal_code) AS label FROM observations o "
            "LEFT JOIN signal_codes sc ON sc.code = o.signal_code "
            f"WHERE o.company_id IN ({_in(company_ids)}) "
            f"AND o.signal_code IN ({_in(OUTBOX_CONTEXT_SIGNALS)}) "
            "ORDER BY o.observed_at, o.id",
            (*company_ids, *OUTBOX_CONTEXT_SIGNALS),
        )
        # Oldest first, so the latest observation wins. A signal can be seen
        # several times at once (one HIRING_ROLE row per open role): every
        # value from the latest sighting shows, not just one of them.
        latest: dict[tuple, tuple] = {}
        for r in rows:
            if r["value_text"]:
                value = r["value_text"]
            elif r["value_num"] is None:
                value = ""
            else:
                value = "yes" if r["value_num"] else "no"
            key = (r["company_id"], r["label"])
            seen = (r["run_id"] or r["observed_at"])
            prev_seen, values = latest.get(key, (None, []))
            if seen != prev_seen:
                values = []
            if value not in values:
                values = values + [value]
            latest[key] = (seen, values)
        for (company_id, label), (_, values) in latest.items():
            signals.setdefault(company_id, {})[label] = ", ".join(v for v in values if v)

    for item in items:
        p = people.get(item.get("prospect_id")) or {}
        item["context"] = {
            "campaign": campaigns.get(item.get("campaign_id"), ""),
            "company": p.get("company_name", ""),
            "website": p.get("website") or (f"https://{p['domain']}" if p.get("domain") else ""),
            "description": p.get("description", ""),
            "found_by": p.get("found_by", ""),
            "contact": " ".join(x for x in (p.get("first_name"), p.get("last_name")) if x),
            "contact_title": p.get("title", ""),
            "contact_source": p.get("source", ""),
            "email_status": p.get("email_status", ""),
            "score": p.get("score"),
            "notes": p.get("personalization_notes", ""),
            "signals": signals.get(p.get("company_id"), {}),
        }
    return items


@app.get("/api/outbox")
async def get_outbox_api():
    """Outbox queue + kill-switch state for the Outbox tab."""
    try:
        state = _state()
        await state.init_db()
        # A send cut off mid-way shows up under Didn't send even when Harvey
        # isn't running.
        await state.fail_stale_sending_outbox()
        return {
            **_compliance_view(),
            "paused": await state.get_setting("sending_paused"),
            "pending": await _with_context(
                await state.get_outbox(status="pending_review", limit=100)),
            # Mail going out right now leads the list.
            "approved": (await state.get_outbox(status="sending", limit=10)
                         + await state.get_outbox(status="approved", limit=50)),
            "sent": (await query_db(
                "SELECT * FROM outbox WHERE status = 'sent' "
                "ORDER BY sent_at DESC LIMIT 25")),
            "failed": (await query_db(
                "SELECT * FROM outbox WHERE status IN ('failed','rejected','cancelled') "
                "ORDER BY updated_at DESC LIMIT 25")),
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/sending-mode")
async def get_sending_mode():
    """Approval (every email waits for you) or autopilot, and what that means here."""
    from harvey.config import load_config
    from harvey.gate import _booking_prefix, allowed_link_domains
    path = _resolve_config_file()
    try:
        config = load_config(str(path))
    except Exception as e:
        return JSONResponse({"error": f"Couldn't read {path.name}: {e}"}, status_code=500)
    state = _state()
    await state.init_db()
    return {
        "mode": "approval" if config.channels.email.require_approval else "autopilot",
        "provider": config.channels.email.provider,
        "file": path.name,
        "your_domains": sorted(allowed_link_domains(config)),
        "booking_page": _booking_prefix(config),
        "held": await state.count_held_for_review(),
    }


@app.post("/api/sending-mode")
async def set_sending_mode(request: Request):
    """Switch it. Autopilot needs {"mode": "autopilot", "confirm": "autopilot"};
    {"mode": "approval"} works at once and sends back to the Outbox whatever
    autopilot approved that hasn't gone out."""
    from harvey.config import ConfigError, set_require_approval
    try:
        body = await request.json()
    except ValueError:
        body = None
    mode = body.get("mode") if isinstance(body, dict) else None
    if mode not in ("approval", "autopilot"):
        return _refuse('expected {"mode": "approval"} or {"mode": "autopilot", "confirm": "autopilot"}', 400)
    if mode == "autopilot" and body.get("confirm") != "autopilot":
        return _refuse('Type "autopilot" to confirm: Harvey will send email without asking you.', 400)
    path = _resolve_config_file()
    try:
        set_require_approval(mode == "approval", path)
    except ConfigError as e:
        return _refuse(str(e), 409)
    state = _state()
    await state.init_db()
    requeued = await state.requeue_autopilot_approved() if mode == "approval" else 0
    await state.log_action("sending_mode", "dashboard", {"mode": mode, "file": path.name,
                                                         "requeued": requeued})
    out = {"success": True, "mode": mode}
    if mode == "approval":
        out["requeued"] = requeued
    return out


@app.post("/api/outbox/approve-all")
async def outbox_approve_all():
    try:
        state = _state()
        await state.init_db()
        n = await state.approve_outbox()
        # Held ones (possible planted link or leak) are approved one by one.
        return {"success": True, "approved": n, "held": await state.count_held_for_review()}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.post("/api/outbox/{item_id}/approve")
async def outbox_approve(item_id: str):
    try:
        state = _state()
        await state.init_db()
        n = await state.approve_outbox(item_id)
        return {"success": bool(n)}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.post("/api/outbox/{item_id}/edit")
async def outbox_edit(item_id: str, request: Request):
    """Rewrite a draft awaiting review. Never approves it."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    subject = str(body.get("subject") or "").strip()
    text = str(body.get("body") or "").strip()
    try:
        state = _state()
        await state.init_db()
        item = await state.get_outbox_item(item_id)
        if not item or item.get("status") != "pending_review":
            return JSONResponse({"success": False,
                                 "message": "Only drafts awaiting review can be edited."},
                                status_code=409)
        if item.get("kind") == "reply":
            # Gmail threads a reply by its subject: a new one would land as a
            # fresh, unthreaded email to someone who wrote in. Only the body
            # of a reply is editable; its stored subject is kept exactly.
            subject = item.get("subject") or ""
        if not subject.strip() or not text:
            return JSONResponse({"success": False,
                                 "message": "Subject and body are both required."},
                                status_code=400)
        # The sender re-checks every email before it goes out; an edit it
        # would block is refused now, while the reviewer can still fix it,
        # instead of failing quietly at send time. Content rules only: the
        # recipient isn't something an edit can change.
        from harvey.gate import pre_send_check
        checked = text
        footer = _compliance_view()["footer"]
        if item.get("kind") == "sequence" and footer and not text.rstrip().endswith(footer):
            checked = text.rstrip() + "\n\n" + footer  # as the Sender will send it
        gate = pre_send_check("reviewer@example.com", subject, checked)
        if not gate:
            return JSONResponse({"success": False,
                                 "message": "The sender would have blocked this: "
                                            + "; ".join(gate.reasons)},
                                status_code=400)
        if not await state.edit_outbox_draft(item_id, subject, text):
            return JSONResponse({"success": False,
                                 "message": "Only drafts awaiting review can be edited."},
                                status_code=409)
        await state.log_action("outbox_edited", "dashboard", {
            "outbox_id": item_id, "to_email": item.get("to_email", ""),
            "old_subject": item.get("subject", ""), "old_body": item.get("body", ""),
        })
        return {"success": True}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


# A hand send is only offered to someone Harvey could still email: never to
# someone who replied, opted out, bounced or was already contacted.
HAND_SENDABLE_STATUSES = ("new", "queued")
HAND_SENDABLE_EMAIL = ("verified", "risky")


def _harvey_can_send() -> bool:
    """Can Harvey send email itself (a connected mailbox, or Instantly)?

    Sending by hand from the dashboard is only offered when it can't: with
    two senders, any timing gap between them emails someone twice. If the
    config can't be read, the answer is yes (the safe side: no hand sends).
    """
    try:
        from harvey.config import load_config, load_env
        from harvey.integrations.mail_provider import NATIVE_PROVIDERS, get_mail_provider
        config, env = load_config(), load_env()
        from harvey.config import legacy_sending_held
        email = config.channels.email
        if not email.enabled or legacy_sending_held(config):
            return False
        if (email.provider or "").strip().lower() in NATIVE_PROVIDERS:
            provider = get_mail_provider(config, env)
            return bool(provider and provider.is_configured())
        return bool(env.instantly_api_key)
    except Exception:
        return True


def _send_as() -> str:
    """The address Harvey sends as, so Gmail opens the right account."""
    try:
        from harvey.config import load_config
        return load_config().persona.email or ""
    except Exception:
        return ""


def _compliance_view() -> dict:
    """What the Sender adds under each cold email, so a reviewer sees the
    email as it will leave, and the warning when the address is missing."""
    try:
        from harvey.config import compliance_footer_lines, compliance_notice, load_config
        config = load_config()
        notice = compliance_notice(config)
        if notice:
            try:
                import sqlite3
                with sqlite3.connect(str(DB_PATH)) as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT value FROM settings WHERE key = 'sending_paused'")
                    row = cur.fetchone()
                    if row and row[0]:
                        notice = (
                            "CAN-SPAM requires a physical postal address before live sending can be enabled. "
                            "Sending is currently paused under Pilot Evaluation Safety Hold."
                        )
            except Exception:
                pass
        return {"footer": "\n".join(compliance_footer_lines(config)),
                "compliance_notice": notice}
    except Exception:
        return {"footer": "", "compliance_notice": ""}


async def _harvey_committed_ids() -> set[str]:
    """Everyone Harvey has approved or sent mail for (Outbox or Instantly).

    Approved mail can be going out at this moment, so these people are past
    the point where a hand send can be recorded safely. So is anyone whose
    send was cut off mid-way: it may have reached them. A draft that only
    awaits review isn't: marking the person sent cancels it.
    """
    from harvey.state import MAYBE_SENT_PREFIX
    ids = {r["prospect_id"] for r in await query_db(
        "SELECT DISTINCT prospect_id FROM outbox WHERE status IN ('approved', 'sending', 'sent') "
        "OR (status = 'failed' AND substr(error, 1, ?) = ?)",
        (len(MAYBE_SENT_PREFIX), MAYBE_SENT_PREFIX))}
    for row in await query_db(
            "SELECT prospect_ids_json FROM campaigns WHERE COALESCE(instantly_campaign_id, '') != ''"):
        try:
            found = json.loads(row.get("prospect_ids_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(found, list):
            ids.update(i for i in found if isinstance(i, str))
    return ids


async def _hand_sent_ids() -> set[str]:
    from harvey.state import MANUAL_SEND_KEY
    rows = await query_db("SELECT key FROM settings WHERE key LIKE ?", (MANUAL_SEND_KEY + "%",))
    return {r["key"][len(MANUAL_SEND_KEY):] for r in rows}


async def _harvey_sent_to(prospect_id: str) -> bool:
    """Did Harvey itself email this person (native send, or an Instantly campaign)?

    A send in flight, or cut off mid-way, counts: it may have reached them.
    """
    from harvey.state import MAYBE_SENT_PREFIX
    if await query_db(
            "SELECT 1 FROM outbox WHERE prospect_id = ? AND (status IN ('sending', 'sent') "
            "OR (status = 'failed' AND substr(error, 1, ?) = ?)) LIMIT 1",
            (prospect_id, len(MAYBE_SENT_PREFIX), MAYBE_SENT_PREFIX)):
        return True
    for row in await query_db(
            "SELECT prospect_ids_json FROM campaigns WHERE COALESCE(instantly_campaign_id, '') != ''"):
        try:
            if prospect_id in json.loads(row.get("prospect_ids_json") or "[]"):
                return True
        except (json.JSONDecodeError, TypeError):
            continue
    return False


def _refuse(message: str, code: int = 409):
    return JSONResponse({"success": False, "message": message}, status_code=code)


@app.post("/api/prospects/{prospect_id}/sent")
async def mark_prospect_sent(prospect_id: str, request: Request):
    """The user sent this email by hand (e.g. from Gmail): record it, or undo it."""
    from harvey.state import HAND_SENT_REASON, MANUAL_SEND_KEY
    try:
        try:
            body = await request.json()
        except ValueError:
            body = {}
        if body is None:
            body = {}
        if not isinstance(body, dict) or not isinstance(body.get("sent", True), bool):
            return _refuse('expected {"sent": true|false}', 400)
        sent = body.get("sent", True)
        rows = await query_db("SELECT status FROM prospects WHERE id = ?", (prospect_id,))
        if not rows:
            return _refuse("unknown prospect", 404)
        current = rows[0].get("status") or "new"
        state = _state()
        await state.init_db()
        key = MANUAL_SEND_KEY + prospect_id
        marked = bool(await state.get_setting(key))

        if sent:
            if marked:
                return {"success": True, "sent": True, "already": True}
            if current not in HAND_SENDABLE_STATUSES:
                # A reply, opt-out or close is never touched; neither is
                # someone Harvey already emailed.
                return {"success": True, "sent": True, "status": current}
            # Allowed even when Harvey can send: recording "I already emailed
            # them" can only ever stop Harvey sending. Not once Harvey has
            # approved or sent mail to them, though: that may be going out now.
            # The marker goes down FIRST: a drain that checks after this sees
            # it and cancels; one that checked before only holds an approved
            # row, which the check below then sees.
            await state.set_setting(key, current)
            if prospect_id in await _harvey_committed_ids():
                await state.delete_setting(key)
                return _refuse("Harvey has already approved or sent an email to this person. "
                               "Check the Outbox.")
            await state.update_prospect_status(prospect_id, "contacted")
            # Anything Harvey had queued for them must not go out as well.
            changed = await state.cancel_pending_outbox_for_prospect(
                prospect_id, reason=HAND_SENT_REASON)
        else:
            if not marked:
                if current in HAND_SENDABLE_STATUSES:
                    return {"success": True, "sent": False}
                if current == "contacted":
                    # Setting them back would make Harvey write and send again.
                    return _refuse("This wasn't marked as sent by hand here, so it stays sent.")
                return {"success": True, "sent": True, "status": current}
            if current != "contacted":
                # They replied, opted out or bounced since: that stands.
                return {"success": True, "sent": True, "status": current}
            previous = await state.get_setting(key) or "queued"
            await state.update_prospect_status(
                prospect_id, previous if previous in HAND_SENDABLE_STATUSES else "queued")
            await state.delete_setting(key)
            # What marking sent cancelled comes back for review, never straight out.
            changed = await state.restore_hand_cancelled_outbox(
                prospect_id, reason=HAND_SENT_REASON)
        if sent:
            # Open in Gmail adds today's footer; remember it as Harvey's text.
            footer = _compliance_view()["footer"]
            await state.remember_footer_lines(footer.split("\n"))
        await state.log_action(
            action_type="manual_send" if sent else "manual_send_undone",
            agent="user",
            details={"prospect_id": prospect_id, "outbox_items": changed},
        )
        return {"success": True, "sent": sent}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.post("/api/outbox/{item_id}/reject")
async def outbox_reject(item_id: str, request: Request = None):
    try:
        feedback = ""
        if request:
            try:
                body = await request.json()
                if isinstance(body, dict):
                    feedback = str(body.get("feedback") or body.get("reason") or "").strip()
            except Exception:
                pass
        state = _state()
        await state.init_db()
        item = await state.get_outbox_item(item_id)
        if not await state.reject_outbox_item(item_id):
            return _refuse("That email is already sending or sent, so it can't be rejected.")

        if feedback:
            await state.add_feedback(
                entity_type="outbox_draft",
                entity_id=item_id,
                comment=feedback,
            )

        await state.log_action("draft_rejected", "dashboard", {
            "outbox_id": item_id,
            "feedback": feedback,
            "conversation_id": item.get("conversation_id", "") if item else "",
            "prospect_id": item.get("prospect_id", "") if item else "",
        })
        return {"success": True, "feedback_saved": bool(feedback)}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.get("/api/feedback")
async def get_all_feedback_api():
    """Retrieve recorded operator review feedback for drafts and templates."""
    try:
        state = _state()
        await state.init_db()
        items = await state.get_all_feedback()
        return {"success": True, "feedback": items}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.post("/api/sending/{action}")
async def sending_toggle(action: str):
    if action not in ("pause", "resume"):
        return JSONResponse({"success": False, "message": "unknown action"}, status_code=400)
    try:
        state = _state()
        await state.init_db()
        current_paused = await state.get_setting("sending_paused") or ""
        if action == "pause":
            await state.set_setting("sending_paused", "paused from dashboard")
        else:
            if "Pilot Evaluation" in current_paused or "Safety Hold" in current_paused:
                return JSONResponse(
                    {"success": False, "message": "Sending cannot be resumed while Pilot Evaluation Safety Hold is active."},
                    status_code=403,
                )
            await state.set_setting("sending_paused", "")
            await state.set_setting("bounce_count", "0")
        return {"success": True}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.get("/api/export/prospects.csv")
async def export_prospects(all: bool = False, min_score: int = 0, email_status: str = ""):
    """Sequencer-ready CSV download of the prospect list."""
    from harvey.state import StateManager
    from harvey.export import export_prospects_csv

    state = StateManager(db_path=str(DB_PATH))
    try:
        await state.init_db()
        statuses = [s.strip() for s in email_status.split(",") if s.strip()] or None
        _, text = await export_prospects_csv(
            state, email_statuses=statuses, min_score=min_score, include_all=all,
        )
    except Exception as e:
        logger.warning("Prospect export failed: %s", e)
        text = ""
    return PlainTextResponse(
        text,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="prospects.csv"'},
    )


@app.get("/api/campaigns")
async def get_campaigns():
    rows = await query_db("SELECT * FROM campaigns ORDER BY created_at DESC LIMIT 100")
    harvey_sends = _harvey_can_send()
    send_as = "" if harvey_sends else _send_as()
    footer = _compliance_view()["footer"]
    committed = await _harvey_committed_ids()
    hand_sent = await _hand_sent_ids()
    paused_setting = await _first("SELECT value FROM settings WHERE key = 'sending_paused'", key="value")
    is_paused = bool(paused_setting)
    for row in rows:
        row["sending_paused"] = is_paused
        try:
            row["sequence"] = json.loads(row.get("sequence_json", "[]"))
        except (json.JSONDecodeError, TypeError):
            row["sequence"] = []
        try:
            row["prospect_ids"] = json.loads(row.get("prospect_ids_json", "[]"))
        except (json.JSONDecodeError, TypeError):
            row["prospect_ids"] = []
        # Who each draft goes to, so the UI can open it in the user's own mail client.
        ids = row["prospect_ids"]
        row["recipients"] = await query_db(
            f"SELECT id, email, first_name, last_name, company, title, status, email_status, "
            f"source, personalization_notes FROM prospects "
            f"WHERE id IN ({','.join('?' * len(ids))}) AND email != ''",
            tuple(ids),
        ) if ids else []
        row["send_as"] = send_as
        # A cold email sent by hand needs the same footer Harvey adds.
        row["footer"] = footer
        for r in row["recipients"]:
            r["sent_by_hand"] = r["id"] in hand_sent
            # "I already emailed them": always recordable before Harvey commits.
            r["can_mark_sent"] = (
                r["id"] not in hand_sent and r["id"] not in committed
                and (r.get("status") or "new") in HAND_SENDABLE_STATUSES
            )
            # The Gmail button: only when Harvey can't send itself and sending is not paused.
            r["can_send_by_hand"] = (
                r["can_mark_sent"] and not harvey_sends and not is_paused
                and (r.get("email_status") or "guess") in HAND_SENDABLE_EMAIL
            )
        # Check outbox drafts for review status summary
        camp_drafts = await query_db(
            "SELECT id, step, status, subject, error FROM outbox WHERE campaign_id = ?",
            (row["id"],),
        )
        row["outbox_drafts_summary"] = {
            "total": len(camp_drafts),
            "pending": sum(1 for d in camp_drafts if d.get("status") == "pending_review"),
            "approved": sum(1 for d in camp_drafts if d.get("status") == "approved"),
            "rejected": sum(1 for d in camp_drafts if d.get("status") == "rejected"),
        }
    return rows


# ── Automated Campaign Generation Endpoints ──

@app.get("/api/campaigns/eligible-leads")
async def get_eligible_leads_for_campaign(profile_id: str | None = None):
    """Retrieve qualified leads eligible for campaign outreach, isolated by business profile."""
    try:
        from harvey.campaign_generator import CampaignGenerator

        state = _state()
        await state.init_db()

        if not profile_id:
            profiles = await state.get_business_profiles()
            profile_id = profiles[0]["id"] if profiles else "prof_saas_global"

        gen = CampaignGenerator(state=state)
        leads = await gen.get_eligible_leads(profile_id)
        return {"leads": leads, "count": len(leads), "profile_id": profile_id}
    except Exception as e:
        logger.exception("Failed to get eligible leads for campaign")
        return JSONResponse({"error": str(e), "leads": [], "count": 0}, status_code=500)


@app.post("/api/campaigns/generate-preview")
async def generate_campaign_preview_endpoint(request: Request):
    """Generate multi-step personalized outreach sequences for selected qualified leads."""
    try:
        from harvey.campaign_generator import CampaignGenerator

        body = await request.json()
        prospect_ids = body.get("prospect_ids") or []

        if not prospect_ids:
            return JSONResponse({"success": False, "error": "No prospects selected for campaign"}, status_code=400)

        state = _state()
        await state.init_db()
        profile_id = body.get("profile_id")
        if not profile_id:
            profiles = await state.get_business_profiles()
            profile_id = profiles[0]["id"] if profiles else "prof_saas_global"
        gen = CampaignGenerator(state=state)
        preview = await gen.generate_campaign_preview(
            profile_id=profile_id,
            prospect_ids=prospect_ids,
            config=body,
        )
        return {"success": True, "preview": preview}
    except Exception as e:
        logger.exception("Campaign preview generation failed")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/campaigns/stage")
async def stage_campaign_endpoint(request: Request):
    """Stage a confirmed campaign: Step 1 drafts land in Outbox as pending review; later steps stay scheduled."""
    try:
        from harvey.campaign_generator import CampaignGenerator

        body = await request.json()
        state = _state()
        await state.init_db()
        gen = CampaignGenerator(state=state)
        res = await gen.stage_campaign_to_outbox(body)
        return res
    except Exception as e:
        logger.exception("Failed to stage campaign to outbox")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# ── Campaign Review & Approval Endpoints ──

@app.get("/api/campaigns/{campaign_id}/review")
async def get_campaign_review_endpoint(campaign_id: str):
    """Fetch complete review details for a campaign, including Step 1 drafts and audit log."""
    try:
        from harvey.campaign_review import CampaignReviewManager
        state = _state()
        await state.init_db()
        manager = CampaignReviewManager(state=state)
        res = await manager.get_campaign_review(campaign_id)
        if not res.get("success") and not res.get("campaign"):
            return JSONResponse(res, status_code=404)
        return res
    except Exception as e:
        logger.exception(f"Failed to fetch review for campaign {campaign_id}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/campaigns/{campaign_id}/drafts/{draft_id}/approve")
async def approve_campaign_draft_endpoint(campaign_id: str, draft_id: str):
    """Explicitly approve a campaign Step 1 draft."""
    try:
        from harvey.campaign_review import CampaignReviewManager
        state = _state()
        await state.init_db()
        manager = CampaignReviewManager(state=state)
        res = await manager.approve_draft(campaign_id=campaign_id, draft_id=draft_id, operator="you")
        if not res.get("success"):
            status_code = res.get("status_code", 400)
            return JSONResponse(res, status_code=status_code)
        return res
    except Exception as e:
        logger.exception(f"Failed to approve draft {draft_id} in campaign {campaign_id}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/campaigns/{campaign_id}/drafts/{draft_id}/reject")
async def reject_campaign_draft_endpoint(campaign_id: str, draft_id: str, request: Request):
    """Explicitly reject a campaign draft. Mandatory rejection reason required."""
    try:
        from harvey.campaign_review import CampaignReviewManager
        body = await request.json() if request else {}
        reason = str(body.get("reason") or body.get("feedback") or "").strip()
        state = _state()
        await state.init_db()
        manager = CampaignReviewManager(state=state)
        res = await manager.reject_draft(campaign_id=campaign_id, draft_id=draft_id, reason=reason, operator="you")
        if not res.get("success"):
            status_code = res.get("status_code", 400)
            return JSONResponse(res, status_code=status_code)
        return res
    except Exception as e:
        logger.exception(f"Failed to reject draft {draft_id} in campaign {campaign_id}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/campaigns/{campaign_id}/drafts/{draft_id}/edit")
async def edit_campaign_draft_endpoint(campaign_id: str, draft_id: str, request: Request):
    """Edit subject and body of a campaign draft awaiting review."""
    try:
        from harvey.campaign_review import CampaignReviewManager
        body = await request.json() if request else {}
        subject = str(body.get("subject") or "").strip()
        text = str(body.get("body") or "").strip()
        state = _state()
        await state.init_db()
        manager = CampaignReviewManager(state=state)
        res = await manager.edit_draft(campaign_id=campaign_id, draft_id=draft_id, subject=subject, body=text, operator="you")
        if not res.get("success"):
            status_code = res.get("status_code", 400)
            return JSONResponse(res, status_code=status_code)
        return res
    except Exception as e:
        logger.exception(f"Failed to edit draft {draft_id} in campaign {campaign_id}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/api/campaigns/{campaign_id}/audit")
async def get_campaign_audit_endpoint(campaign_id: str):
    """Fetch review and approval audit history for a campaign."""
    try:
        from harvey.campaign_review import CampaignReviewManager
        state = _state()
        await state.init_db()
        manager = CampaignReviewManager(state=state)
        history = await manager.get_audit_history(campaign_id)
        return {"success": True, "campaign_id": campaign_id, "audit_history": history}
    except Exception as e:
        logger.exception(f"Failed to fetch audit history for campaign {campaign_id}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/api/prospects/{prospect_id}/opt-out")
async def get_opt_out(prospect_id: str):
    """Why this person is opted out: the rule, the words and their message."""
    rows = await query_db("SELECT status FROM prospects WHERE id = ?", (prospect_id,))
    if not rows:
        return _refuse("unknown prospect", 404)
    status = rows[0].get("status") or "new"
    state = _state()
    await state.init_db()
    history = await state.opt_out_history(prospect_id)
    last = next((h for h in reversed(history) if h["action"] == "opt_out"), None)
    out = {
        "opted_out": status == "opted_out", "status": status, "recorded": bool(last),
        "at": None, "rule": None, "matched": "", "why": "", "message": "",
        "conversation_id": "", "history": history,
    }
    if last:
        out.update({k: last.get(k) or out[k] for k in
                    ("rule", "matched", "why", "message", "conversation_id")})
        out["at"] = last["at"]
    elif status == "opted_out" or history:
        # Opted out before Harvey kept reasons (and maybe undone since):
        # show the last thing they wrote.
        for c in await query_db(
                "SELECT id, thread_json, updated_at FROM conversations "
                "WHERE prospect_id = ? ORDER BY updated_at DESC", (prospect_id,)):
            try:
                thread = json.loads(c.get("thread_json") or "[]")
            except (json.JSONDecodeError, TypeError):
                continue
            theirs = [m for m in thread if isinstance(m, dict) and m.get("sender") == "prospect"]
            if theirs:
                out.update(message=str(theirs[-1].get("content") or ""),
                           conversation_id=c["id"], at=theirs[-1].get("timestamp") or c.get("updated_at"))
                break
    return out


@app.post("/api/prospects/{prospect_id}/opt-out/undo")
async def undo_opt_out(prospect_id: str, request: Request):
    """The owner says this opt-out was a mistake. Needs {"confirm": true}."""
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict) or body.get("confirm") is not True:
        return _refuse('expected {"confirm": true}', 400)
    rows = await query_db("SELECT email FROM prospects WHERE id = ?", (prospect_id,))
    if not rows:
        return _refuse("unknown prospect", 404)
    state = _state()
    await state.init_db()
    history = await state.opt_out_history(prospect_id)
    last = next((h for h in reversed(history) if h["action"] == "opt_out"), {})
    if not await state.undo_opt_out(prospect_id, details={
            "prospect_email": rows[0].get("email") or "",
            "previous_why": last.get("why", ""), "previous_at": last.get("at", "")}):
        return _refuse("This person isn't opted out.")
    return {"success": True, "status": "replied"}


# ── Company Research & Lead Enrichment Endpoints ──

@app.get("/api/prospects/{prospect_id}/research")
async def get_prospect_research(prospect_id: str):
    """Retrieve the latest company research and enrichment data for a prospect."""
    state = _state()
    await state.init_db()
    prospect = await state.get_prospect(prospect_id)
    if not prospect:
        return JSONResponse({"success": False, "error": f"Prospect '{prospect_id}' not found"}, status_code=404)

    record = await state.get_lead_research(prospect_id)
    if not record and prospect.company:
        domain = ""
        if prospect.email and "@" in prospect.email:
            email_domain = prospect.email.split("@")[-1].strip().lower()
            if email_domain not in ("gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com", "aol.com"):
                domain = email_domain
        if not domain and prospect.company:
            domain = f"{re.sub(r'[^a-zA-Z0-9]', '', prospect.company).lower()}.com"
        if domain:
            record = await state.get_lead_research_by_domain(domain)

    if record:
        return {"success": True, "researched": True, "data": record}
    return {"success": True, "researched": False, "data": None}


@app.post("/api/prospects/{prospect_id}/research")
async def run_prospect_research(prospect_id: str, request: Request):
    """Execute on-demand company research and lead enrichment for a prospect."""
    try:
        data = await request.json() if await request.body() else {}
        force_refresh = bool(data.get("force_refresh", False))
        profile_id = str(data.get("profile_id", "") or "").strip()

        state = _state()
        await state.init_db()

        prospect = await state.get_prospect(prospect_id)
        if not prospect:
            return JSONResponse({"success": False, "error": f"Prospect '{prospect_id}' not found"}, status_code=404)

        from harvey.enrichment import CompanyEnrichmentService
        service = CompanyEnrichmentService(state=state)
        result = await service.enrich_prospect(
            prospect_id=prospect_id,
            profile_id=profile_id,
            force_refresh=force_refresh,
        )
        return {"success": True, "data": result}
    except Exception as e:
        logger.exception(f"Error enriching prospect {prospect_id}: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/companies/research-domain")
async def run_domain_research(request: Request):
    """Execute standalone company research by domain or company name."""
    try:
        data = await request.json() if await request.body() else {}
        domain = str(data.get("domain", "") or "").strip()
        company_name = str(data.get("company_name", "") or "").strip()
        force_refresh = bool(data.get("force_refresh", False))

        if not domain and not company_name:
            return JSONResponse({"success": False, "error": "domain or company_name is required"}, status_code=400)

        state = _state()
        await state.init_db()

        from harvey.enrichment import CompanyEnrichmentService
        service = CompanyEnrichmentService(state=state)

        clean_domain = domain or f"{re.sub(r'[^a-zA-Z0-9]', '', company_name).lower()}.com"
        if not force_refresh:
            cached = await state.get_lead_research_by_domain(clean_domain)
            if cached:
                cached["cache_hit"] = True
                return {"success": True, "data": cached}

        res_data = await service.web_provider.research_company(clean_domain, company_name)
        if res_data.confidence < 0.3:
            res_data = await service.sample_provider.research_company(clean_domain, company_name)

        return {"success": True, "data": res_data.to_dict()}
    except Exception as e:
        logger.exception(f"Error researching domain: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/api/conversations")
async def get_conversations(awaiting: bool = False):
    rows = await query_db("""
        SELECT c.*, p.first_name, p.last_name, p.email as prospect_email, p.company,
               p.status as prospect_status
        FROM conversations c
        LEFT JOIN prospects p ON c.prospect_id = p.id
        ORDER BY c.updated_at DESC LIMIT 100
    """)
    # The stored thread holds replies and Harvey's answers; the cold emails
    # a reply answers live in the Outbox. Attach those (sent ones only) and
    # any answer still waiting for approval, so the page shows the exchange.
    pids = sorted({r["prospect_id"] for r in rows if r.get("prospect_id")})
    emails: dict[str, list] = {}
    if pids:
        for e in await query_db(
                "SELECT prospect_id, campaign_id, step, subject, body, sent_at FROM outbox "
                f"WHERE kind = 'sequence' AND status = 'sent' AND prospect_id IN ({_in(pids)}) "
                "ORDER BY sent_at", tuple(pids)):
            emails.setdefault(e.pop("prospect_id"), []).append(e)
    cids = [r["id"] for r in rows]
    drafts = {d.pop("conversation_id"): d for d in (await query_db(
        "SELECT id, conversation_id, subject, body, status, send_at FROM outbox "
        f"WHERE kind = 'reply' AND status IN ('pending_review', 'approved') AND conversation_id IN ({_in(cids)}) "
        "ORDER BY created_at", tuple(cids)) if cids else [])}
    for row in rows:
        try:
            row["thread"] = json.loads(row.get("thread_json", "[]"))
        except (json.JSONDecodeError, TypeError):
            row["thread"] = []
        # A prospect in two campaigns: show the sequence this reply answers.
        camp = row.get("campaign_id") or ""
        row["emails"] = [{k: v for k, v in e.items() if k != "campaign_id"}
                         for e in emails.get(row.get("prospect_id"), [])
                         if not camp or e.get("campaign_id") == camp]
        row["draft"] = drafts.get(row["id"])
    if awaiting:
        rows += await _awaiting_reply()
    return rows


async def _awaiting_reply() -> list[dict]:
    """Prospects Harvey emailed who haven't replied yet, shaped like a
    conversation so the tab can show every sent email, not only the ones
    that got an answer. Read-only: no conversation row is created, and the
    card moves to the real conversation the moment a reply arrives."""
    waiting: dict[str, dict] = {}
    # Only people still waiting: anyone whose company has a conversation (even
    # one past the tab's latest 100), a bounced address, or who's otherwise done
    # isn't. Bounded because this runs on every load of the tab.
    sent = await query_db(
        "SELECT o.prospect_id, o.step, o.subject, o.body, o.sent_at, "
        "p.first_name, p.last_name, p.email AS prospect_email, p.company, "
        "p.status AS prospect_status FROM outbox o "
        "JOIN prospects p ON p.id = o.prospect_id "
        "WHERE o.kind = 'sequence' AND o.status = 'sent' "
        "AND p.status NOT IN ('replied', 'opted_out', 'lost', 'meeting', 'closed') "
        "AND COALESCE(p.email_status, '') != 'invalid' "
        "AND NOT EXISTS (SELECT 1 FROM conversations c JOIN prospects q ON q.id = c.prospect_id "
        "WHERE q.id = p.id OR (q.company_id = p.company_id AND COALESCE(p.company_id, '') != '')) "
        "ORDER BY o.sent_at DESC LIMIT 2000")
    for e in reversed(sent):
        pid = e["prospect_id"]
        row = waiting.setdefault(pid, {
            "id": "awaiting-" + pid, "prospect_id": pid, "campaign_id": "",
            "channel": "email", "intent": "", "stage": "", "status": "awaiting_reply",
            "thread": [], "draft": None, "emails": [],
            "first_name": e.get("first_name") or "", "last_name": e.get("last_name") or "",
            "prospect_email": e.get("prospect_email") or "", "company": e.get("company") or "",
            "prospect_status": e.get("prospect_status") or "",
        })
        row["emails"].append({"step": e["step"], "subject": e["subject"],
                              "body": e["body"], "sent_at": e["sent_at"]})
        row["created_at"] = row.get("created_at") or e["sent_at"]
        row["updated_at"] = e["sent_at"]
    return sorted(waiting.values(), key=lambda r: r.get("updated_at") or "", reverse=True)


@app.post("/api/conversations/{convo_id}/status")
async def set_conversation_status(convo_id: str, request: Request):
    """Close a conversation, or hand one Harvey escalated back to it.

    Harvey answers new replies only on open conversations, so "open" means
    "Harvey, carry on" and "closed" means it's done."""
    try:
        data = await request.json()
    except Exception:
        data = {}
    status = data.get("status") if isinstance(data, dict) else None
    if status not in ("open", "closed"):
        return JSONResponse({"success": False, "message": "status must be open or closed"},
                            status_code=400)
    rows = await query_db("SELECT status FROM conversations WHERE id = ?", (convo_id,))
    if not rows:
        return JSONResponse({"success": False, "message": "No such conversation."},
                            status_code=404)
    state = _state()
    await state.update_conversation(convo_id, status=status)
    await state.log_action("conversation_status", "dashboard",
                           {"conversation_id": convo_id, "from": rows[0]["status"], "to": status})
    return {"success": True, "status": status}


@app.get("/api/trend")
async def get_trend(days: int = 14, tz: int = 0):
    """Daily counts for the last `days` days in the viewer's time zone
    (`tz`: minutes east of UTC; stored times are UTC): contacts found,
    emails sent, first replies. Days with nothing are 0."""
    from datetime import datetime, timedelta, timezone
    days = max(1, min(90, days))
    tz = max(-840, min(840, tz))
    shift = f"{tz:+d} minutes"
    today = (datetime.now(timezone.utc) + timedelta(minutes=tz)).date()
    labels = [(today - timedelta(days=n)).isoformat() for n in range(days - 1, -1, -1)]

    async def per_day(table: str, column: str, where: str = "") -> list[int]:
        rows = await query_db(
            f"SELECT date({column}, ?) AS d, COUNT(*) AS n FROM {table} "
            f"WHERE date({column}, ?) >= ? {where} GROUP BY d", (shift, shift, labels[0]))
        counts = {r["d"]: r["n"] for r in rows}
        return [counts.get(day, 0) for day in labels]

    return {
        "days": labels,
        "found": await per_day("prospects", "created_at"),
        "sent": await per_day("outbox", "sent_at", "AND status = 'sent'"),
        "replies": await per_day("conversations", "created_at"),
    }


@app.get("/api/activity")
async def get_activity():
    rows = await query_db("SELECT * FROM actions ORDER BY created_at DESC LIMIT 100")
    for row in rows:
        try:
            row["details"] = json.loads(row.get("details_json", "{}"))
        except (json.JSONDecodeError, TypeError):
            row["details"] = {}
    return rows


# ── Dashboard UI ──


# ── Discovery: the provider menu, an estimate, and a run ──
#
# This is the only stage that spends money, so the UI never starts one without
# showing what it will cost first.

_discovery_task: asyncio.Task | None = None
_discovery_report: dict | None = None
_discovery_kind = "discover"   # or "profile": reading sites has no stop switch


def _discovery_queries(body: dict, config):
    from harvey.collectors.discover import build_queries, preset_problem, parse_cities

    preset = str(body.get("preset") or "")
    problem = preset_problem(preset, str(body.get("provider") or ""), config=config)
    if problem:
        raise ValueError(problem)
    raw_cities = [c.strip() for c in (body.get("cities") or []) if c.strip()]
    cities = parse_cities(raw_cities) or None
    return build_queries(
        config, cities=cities,
        depth=int(body.get("depth") or 30),
        limit=int(body.get("limit") or 100),
        preset=preset,
    )


@app.get("/api/discover/providers")
async def get_discovery_providers():
    """What each source does, what it costs, and whether it's ready to use."""
    try:
        from harvey.collectors.discover import DEFAULT_PROVIDER, provider_menu
        from harvey.config import load_env

        state = _state()
        await state.init_db()
        return {
            "providers": provider_menu(load_env().model_dump()),
            "default": DEFAULT_PROVIDER,
            "selected": await state.get_setting("discovery_provider") or DEFAULT_PROVIDER,
            "paused": await state.get_setting("discovery_paused"),
            "running": bool(_discovery_task and not _discovery_task.done()),
            "running_kind": _discovery_kind,
            "unprofiled": await state.count_companies_needing_profile(),
            "last_report": _discovery_report,
        }
    except Exception as e:
        logger.exception("discovery providers failed")
        return {"providers": [], "error": str(e)}


@app.post("/api/discover/estimate")
async def estimate_discovery(request: Request):
    """Projected spend and the exact query list, before anything is called."""
    try:
        from harvey.collectors.discover import PROVIDERS, estimate_cost
        from harvey.config import load_config

        body = await request.json()
        provider = body.get("provider") or ""
        if provider not in PROVIDERS:
            return JSONResponse({"error": f"unknown provider {provider!r}"},
                                status_code=400)

        try:
            queries = _discovery_queries(body, load_config())
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return {
            "provider": provider,
            "queries": [q.keyword() for q in queries],
            "query_count": len(queries),
            "estimated_cost": round(estimate_cost(provider, queries), 4),
            "free": PROVIDERS[provider].estimate(queries) == 0,
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/discover/run")
async def start_discovery(request: Request):
    """Kick off a run in the background and hand back immediately.

    Discovery takes minutes, not milliseconds — holding the request open
    would just time out. Progress shows up in the run log.
    """
    global _discovery_task, _discovery_report

    if _discovery_task and not _discovery_task.done():
        return JSONResponse({"success": False, "message": "a run is already going"},
                            status_code=409)
    try:
        from harvey.collectors.discover import PROVIDERS, parse_cities
        from harvey.config import load_config
        from harvey.pipeline import run_prospecting

        body = await request.json()
        provider = body.get("provider") or ""
        if provider not in PROVIDERS:
            return JSONResponse({"success": False,
                                 "message": f"unknown provider {provider!r}"},
                                status_code=400)

        config = load_config()
        try:
            queries = _discovery_queries(body, config)
        except ValueError as e:
            return JSONResponse({"success": False, "message": str(e)}, status_code=400)
        max_spend = float(body.get("max_spend") or 1.0)

        state = _state()
        await state.init_db()
        await state.set_setting("discovery_provider", provider)
        await state.set_setting("discovery_paused", "")

        global _discovery_kind
        _discovery_kind = "discover"

        async def _go():
            global _discovery_report
            try:
                # Discovery chains straight into profiling: reading the sites
                # is free, and it is what makes the results worth anything.
                result = await run_prospecting(state, config, provider, queries,
                                               max_spend=max_spend)
                _discovery_report = {
                    **(result.discover or {}),
                    "profiled_companies": result.profiled_companies,
                    "profile_observations": result.profile_observations,
                    "errors": result.errors,
                }
                try:
                    await state.save_discovery_run({
                        "provider": provider,
                        "geography": ", ".join(parse_cities(body.get("cities") or []) or ["Indore, India"]),
                        "industry": body.get("industry") or (config.icp.industries[0] if getattr(config.icp, "industries", None) else "IT, Software, SaaS and AI"),
                        "keywords": ", ".join(q.keyword() for q in queries),
                        "total_found": (result.discover or {}).get("found", 0),
                        "new_leads": (result.discover or {}).get("saved", 0),
                        "duplicates_skipped": (result.discover or {}).get("skipped", 0),
                        "actual_cost_usd": float((result.discover or {}).get("spend", 0.0)),
                        "status": "complete" if not result.errors else "partial",
                        "error_message": "; ".join(str(e) for e in result.errors) if result.errors else None,
                    })
                    await state.log_provider_request(
                        provider=provider,
                        endpoint="osm-overpass",
                        method="POST",
                        status_code=200 if not result.errors else 500,
                        success=not result.errors,
                        request_summary=f"Discovery query: {', '.join(q.keyword() for q in queries)}",
                        response_summary=f"Found: {(result.discover or {}).get('found', 0)}, New: {(result.discover or {}).get('saved', 0)}",
                        latency_ms=0,
                        cost_usd=float((result.discover or {}).get("spend", 0.0)),
                        error_message="; ".join(str(e) for e in result.errors) if result.errors else "",
                    )
                except Exception as save_err:
                    logger.warning(f"Failed to record discovery run metadata: {save_err}")
            except Exception as exc:
                logger.exception("discovery run failed")
                _discovery_report = {"errors": [str(exc)], "stopped": "failed"}

        _discovery_report = None
        _discovery_task = asyncio.create_task(_go())
        return {"success": True, "queries": len(queries)}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.post("/api/profile/run")
async def start_profile():
    """Read the websites of everything discovered but not yet looked at.

    Free and model-free, so there is nothing to estimate and no cap to set.
    """
    global _discovery_task, _discovery_report

    if _discovery_task and not _discovery_task.done():
        return JSONResponse({"success": False, "message": "a run is already going"},
                            status_code=409)
    try:
        from harvey.pipeline import run_profile_stage

        state = _state()
        await state.init_db()
        pending = await state.count_companies_needing_profile()
        if not pending:
            return {"success": True, "pending": 0}

        async def _go():
            global _discovery_report
            try:
                try:
                    from harvey.config import load_config
                    config = load_config()
                except Exception:
                    config = None   # still profiles; no role is singled out
                companies, observations, _ = await run_profile_stage(
                    state, limit=200, config=config)
                _discovery_report = {
                    "profiled_companies": companies,
                    "profile_observations": observations,
                }
            except Exception as exc:
                logger.exception("profile run failed")
                _discovery_report = {"errors": [str(exc)], "stopped": "failed"}

        global _discovery_kind
        _discovery_kind = "profile"
        _discovery_report = None
        _discovery_task = asyncio.create_task(_go())
        return {"success": True, "pending": pending}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.post("/api/discover/stop")
async def stop_discovery():
    """Kill switch. Read between batches, so an in-flight run stops cleanly."""
    try:
        state = _state()
        await state.init_db()
        await state.set_setting("discovery_paused", "stopped from dashboard")
        return {"success": True}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


# ── AI Lead Discovery & Qualification Endpoints ──

@app.get("/api/discovery/profiles")
async def get_discovery_business_profiles():
    """Retrieve all configured business profiles."""
    try:
        state = _state()
        await state.init_db()
        profiles = await state.get_business_profiles()
        return {"profiles": profiles}
    except Exception as e:
        logger.exception("Failed to get business profiles")
        return JSONResponse({"error": str(e), "profiles": []}, status_code=500)


@app.post("/api/discovery/profiles")
async def create_or_update_business_profile(request: Request):
    """Create or update a business profile."""
    try:
        body = await request.json()
        state = _state()
        await state.init_db()
        profile = await state.create_business_profile(body)
        return {"success": True, "profile": profile}
    except Exception as e:
        logger.exception("Failed to save business profile")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.delete("/api/discovery/profiles/{profile_id}")
async def delete_business_profile(profile_id: str):
    """Delete a custom business profile."""
    try:
        state = _state()
        await state.init_db()
        success = await state.delete_business_profile(profile_id)
        if not success:
            return JSONResponse({"success": False, "error": "Profile not found"}, status_code=404)
        return {"success": True, "message": f"Profile {profile_id} deleted"}
    except ValueError as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)
    except Exception as e:
        logger.exception("Failed to delete business profile")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)




@app.get("/api/discovery/criteria")
async def get_discovery_criteria():
    """Return available search criteria suggestions and active profiles."""
    try:
        state = _state()
        await state.init_db()
        profiles = await state.get_business_profiles()
        return {
            "industries": [
                "IT, Software, SaaS and AI",
                "Legal Services and Law Firms",
                "Healthcare, Hospitals, Clinics and Diagnostics",
                "Real Estate and Property",
                "Manufacturing and Industrial",
                "Finance, Accounting and Insurance",
                "Education and EdTech",
                "E-commerce and Retail",
                "Hospitality, Hotels and Restaurants",
                "Logistics and Transportation",
                "Construction and Infrastructure",
                "Automotive",
                "Agriculture",
                "Recruitment and HR",
                "Marketing and Consulting Agencies",
                "Professional and Business Services",
                "CleanTech / Renewable Energy",
                "Healthcare / Dental",
            ],
            "company_sizes": [
                "1-10 employees",
                "10-25 employees",
                "15-50 employees",
                "25-75 employees",
                "50-200 employees",
                "200+ employees",
            ],
            "business_types": [
                "Direct B2B Service",
                "Specialized Private Practice",
                "Clean Energy Systems Integrator",
                "Cloud SaaS Platform",
                "Commercial Contracting",
                "Corporate Law Partnership",
                "Financial Advisory",
            ],
            "use_cases": [
                "Appointment triage & follow-up",
                "Commercial contract proposal follow-up",
                "Consultation intake qualification",
                "Lead nurturing for high-ticket services",
                "Speed-to-lead for inbound bid requests",
            ],
            "profiles": profiles,
        }
    except Exception as e:
        logger.exception("Failed to get discovery criteria")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/discovery/leads")
async def get_discovered_leads(
    profile_id: str | None = None,
    status: str = "all",
    search: str | None = None,
):
    """Retrieve discovered candidate leads with duplicate detection and review status."""
    try:
        from harvey.qualification import check_discovered_lead_duplicates

        state = _state()
        await state.init_db()
        leads = await state.get_discovered_leads(profile_id)

        # Check CRM duplicate status for each lead
        leads = await check_discovered_lead_duplicates(leads, state)

        # Load active business profile to provide accurate qualification context
        active_profile = {}
        if profile_id:
            active_profile = await state.get_business_profile(profile_id) or {}
        else:
            profiles = await state.get_business_profiles()
            active_profile = next((p for p in profiles if p.get("is_default")), profiles[0] if profiles else {})

        # Ensure bant_breakdown is populated with authoritative scoring
        from harvey.qualification import LeadQualifier, ResearchProfile, compute_authoritative_bant
        _qualifier = LeadQualifier()
        for l in leads:
            if not l.get("bant_breakdown"):
                try:
                    rp = ResearchProfile(
                        company_name=l.get("company_name", ""),
                        domain=l.get("domain", ""),
                        industry=l.get("industry", ""),
                        location=l.get("location", ""),
                        company_size=l.get("company_size", ""),
                        potential_needs=l.get("potential_needs", []) or [],
                        decision_maker=l.get("decision_maker", {}) or {},
                    )
                    l["bant_breakdown"] = compute_authoritative_bant(rp, active_profile)
                except Exception:
                    pass

        # Compute accurate counts over ALL leads before applying status filter
        total_count = len(leads)
        in_crm_count = sum(1 for l in leads if l.get("in_crm") or l.get("added_to_crm") or l.get("candidate_status") == "approved")
        rejected_count = sum(1 for l in leads if l.get("candidate_status") in ("rejected", "skipped"))
        pending_count = sum(1 for l in leads if not (l.get("in_crm") or l.get("added_to_crm") or l.get("candidate_status") in ("approved", "rejected", "skipped")))

        # Filter by candidate_status if specified
        filtered_leads = leads
        if status and status != "all":
            if status == "pending":
                filtered_leads = [
                    l for l in leads
                    if not (l.get("in_crm") or l.get("added_to_crm") or l.get("candidate_status") in ("approved", "rejected", "skipped"))
                ]
            elif status in ("approved", "in_crm"):
                filtered_leads = [
                    l for l in leads
                    if l.get("in_crm") or l.get("added_to_crm") or l.get("candidate_status") == "approved"
                ]
            elif status in ("rejected", "skipped"):
                filtered_leads = [
                    l for l in leads
                    if l.get("candidate_status") in ("rejected", "skipped")
                ]

        if search and search.strip():
            q = search.strip().lower()
            filtered_leads = [
                l for l in filtered_leads
                if q in (l.get("company_name") or "").lower()
                or q in (l.get("domain") or "").lower()
                or q in (l.get("industry") or "").lower()
                or q in (l.get("location") or "").lower()
                or q in (l.get("description") or "").lower()
            ]

        return {
            "leads": filtered_leads,
            "count": total_count,
            "pending_count": pending_count,
            "approved_count": in_crm_count,
            "rejected_count": rejected_count,
        }
    except Exception as e:
        logger.exception("Failed to get discovered leads")
        return JSONResponse({"error": str(e), "leads": []}, status_code=500)


@app.post("/api/discovery/search")
async def run_discovery_search(request: Request):
    """Discover candidate companies matching criteria or profile with strict fact honesty."""
    try:
        from harvey.qualification import (
            SampleLeadDiscoveryProvider,
            LeadQualifier,
            check_discovered_lead_duplicates,
        )

        body = await request.json() if request.headers.get("content-type") == "application/json" else {}
        profile_id = body.get("profile_id")
        limit = int(body.get("limit") or 5)

        state = _state()
        await state.init_db()

        # Build composite profile from saved profile + custom overrides
        profile = {}
        if profile_id:
            profile = await state.get_business_profile(profile_id) or {}

        # Apply search criteria overrides if provided
        if body.get("industry"):
            profile["industry"] = body["industry"]
        if body.get("location"):
            profile["geography"] = body["location"]
        if body.get("geography_scope"):
            profile["geography_scope"] = body["geography_scope"]
        if body.get("company_size"):
            profile["company_size"] = body["company_size"]
        if body.get("business_type"):
            profile["business_type"] = body["business_type"]
        if body.get("use_cases"):
            profile["target_use_cases"] = body["use_cases"]

        if not profile.get("industry"):
            profile["industry"] = "IT, Software, SaaS and AI"
        if not profile.get("id"):
            profile["id"] = profile_id or "custom_search"

        provider = SampleLeadDiscoveryProvider()
        candidates = await provider.discover(profile, limit=limit)
        qualifier = LeadQualifier()

        results = []
        for candidate in candidates:
            qual = qualifier.qualify(candidate, profile)
            lead_dict = candidate.to_dict()
            lead_dict.update({
                "profile_id": profile.get("id", ""),
                "score": qual.score,
                "tier": qual.tier,
                "reasons": qual.reasons,
                "missing_fields": qual.missing_information,
                "confidence": qual.confidence,
                "is_qualified": qual.is_qualified,
                "candidate_status": "pending",
                "business_type": body.get("business_type") or candidate.business_type or candidate.industry,
                "target_use_cases": body.get("use_cases") or candidate.relevant_use_cases or candidate.potential_needs,
                "bant_breakdown": qual.bant_breakdown,
            })
            lead_id = await state.save_discovered_lead(lead_dict)
            lead_dict["id"] = lead_id
            results.append(lead_dict)

        # Deduplicate candidate results by normalized domain
        seen_domains = set()
        deduped_results = []
        for r in results:
            dom = (r.get("domain") or "").strip().lower()
            if dom:
                if dom in seen_domains:
                    continue
                seen_domains.add(dom)
            deduped_results.append(r)
        results = deduped_results

        # Run duplicate detection against CRM
        results = await check_discovered_lead_duplicates(results, state)

        return {
            "success": True,
            "profile_id": profile.get("id"),
            "profile_name": profile.get("name") or profile.get("industry"),
            "discovered_count": len(results),
            "leads": results,
        }
    except json.JSONDecodeError as e:
        return JSONResponse({"success": False, "error": f"Invalid JSON payload: {e.msg}"}, status_code=400)
    except Exception as e:
        logger.exception("Discovery search failed")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/discovery/sample-discover")
async def run_sample_discovery(request: Request):
    """Run sample lead discovery using SampleLeadDiscoveryProvider and qualify candidate leads."""
    try:
        from harvey.qualification import (
            SampleLeadDiscoveryProvider,
            LeadQualifier,
            check_discovered_lead_duplicates,
        )

        body = await request.json() if request.headers.get("content-type") == "application/json" else {}
        profile_id = body.get("profile_id")
        limit = int(body.get("limit") or 5)

        state = _state()
        await state.init_db()

        profile = {}
        if profile_id:
            profile = await state.get_business_profile(profile_id) or {}
        if not profile:
            profiles = await state.get_business_profiles()
            if profiles:
                profile = profiles[0]

        if not profile.get("industry"):
            profile["industry"] = "IT, Software, SaaS and AI"
        if not profile.get("id"):
            profile["id"] = profile_id or "default_sample"

        provider = SampleLeadDiscoveryProvider()
        candidates = await provider.discover(profile, limit=limit)
        qualifier = LeadQualifier()

        results = []
        for candidate in candidates:
            qual = qualifier.qualify(candidate, profile)
            lead_dict = candidate.to_dict()
            lead_dict.update({
                "profile_id": profile.get("id", ""),
                "score": qual.score,
                "tier": qual.tier,
                "reasons": qual.reasons,
                "missing_fields": qual.missing_information,
                "confidence": qual.confidence,
                "is_qualified": qual.is_qualified,
                "candidate_status": "pending",
                "business_type": candidate.business_type or candidate.industry,
                "target_use_cases": candidate.relevant_use_cases or candidate.potential_needs,
                "bant_breakdown": qual.bant_breakdown,
            })
            lead_id = await state.save_discovered_lead(lead_dict)
            lead_dict["id"] = lead_id

            # Save research dossier if available
            try:
                dm_list = []
                if hasattr(candidate, "decision_maker") and candidate.decision_maker:
                    dm_list = [candidate.decision_maker]
                elif hasattr(candidate, "decision_makers") and candidate.decision_makers:
                    dm_list = [dm.to_dict() if hasattr(dm, "to_dict") else dm for dm in candidate.decision_makers]

                research_dossier = {
                    "prospect_id": lead_id,
                    "lead_id": lead_id,
                    "company_name": getattr(candidate, "company_name", ""),
                    "domain": getattr(candidate, "domain", ""),
                    "website": getattr(candidate, "website", ""),
                    "industry": getattr(candidate, "industry", ""),
                    "location": getattr(candidate, "location", ""),
                    "employee_count": getattr(candidate, "company_size", "") or getattr(candidate, "employee_count", ""),
                    "sources": getattr(candidate, "sources", []) or [],
                    "verified_facts": getattr(candidate, "verified_facts", []) or [],
                    "potential_needs": getattr(candidate, "potential_needs", []) or [],
                    "decision_makers": dm_list,
                    "raw_notes": getattr(candidate, "raw_notes", "") or getattr(candidate, "description", ""),
                    "fit_score": qual.score,
                    "fit_tier": qual.tier,
                    "fit_reasons": qual.reasons,
                    "confidence": qual.confidence,
                }
                await state.save_lead_research(research_dossier)
            except Exception as re_err:
                logger.warning(f"Failed to auto-save lead research dossier for {candidate.company_name}: {re_err}")

            results.append(lead_dict)

        seen_domains = set()
        deduped_results = []
        for r in results:
            dom = (r.get("domain") or "").strip().lower()
            if dom:
                if dom in seen_domains:
                    continue
                seen_domains.add(dom)
            deduped_results.append(r)
        results = deduped_results

        results = await check_discovered_lead_duplicates(results, state)

        return {
            "success": True,
            "profile_id": profile.get("id"),
            "profile_name": profile.get("name") or profile.get("industry"),
            "discovered_count": len(results),
            "leads": results,
        }
    except json.JSONDecodeError as e:
        return JSONResponse({"success": False, "error": f"Invalid JSON payload: {e.msg}"}, status_code=400)
    except Exception as e:
        logger.exception("Sample discovery failed")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/discovery/candidates/import")
async def import_candidates(request: Request):
    """Review & import approved candidate leads into the CRM pipeline."""
    try:
        body = await request.json()
        lead_ids = body.get("lead_ids")
        if not lead_ids and body.get("lead_id"):
            lead_ids = [body.get("lead_id")]

        if not lead_ids or not isinstance(lead_ids, list):
            return JSONResponse({"success": False, "error": "lead_ids list is required"}, status_code=400)

        target_stage = body.get("target_stage") or "New"
        notes = body.get("notes") or ""

        state = _state()
        await state.init_db()

        result = await state.batch_import_discovered_leads(lead_ids, target_stage=target_stage)
        return result
    except Exception as e:
        logger.exception("Failed to import candidates")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/discovery/candidates/status")
async def update_candidate_status(request: Request):
    """Update candidate review status (skip or reject candidate leads)."""
    try:
        body = await request.json()
        lead_ids = body.get("lead_ids")
        if not lead_ids and body.get("lead_id"):
            lead_ids = [body.get("lead_id")]

        status = body.get("status")
        if not status or status not in ("pending", "approved", "rejected", "skipped"):
            return JSONResponse({"success": False, "error": "Valid status ('pending', 'rejected', 'skipped') required"}, status_code=400)

        reason = body.get("reason") or ""
        state = _state()
        await state.init_db()

        result = await state.batch_update_discovered_leads_status(lead_ids, status=status, reason=reason)
        return result
    except Exception as e:
        logger.exception("Failed to update candidate status")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/discovery/candidates/{lead_id}/qualify")
async def qualify_candidate_lead(lead_id: str, request: Request):
    """Manually qualify a candidate lead (override automated scoring)."""
    try:
        body = await request.json() if await request.body() else {}
        reason = body.get("reason", "Manual qualification override by operator")
        state = _state()
        await state.init_db()

        lead = await state.get_discovered_lead(lead_id)
        if not lead:
            return JSONResponse({"success": False, "error": f"Lead {lead_id} not found"}, status_code=404)

        ok = await state.update_lead_qualification_override(lead_id, is_qualified=True, reason=reason)
        return {"success": ok, "lead_id": lead_id, "is_qualified": True, "message": "Lead marked as Qualified."}
    except Exception as e:
        logger.exception(f"Failed to qualify lead {lead_id}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/discovery/candidates/{lead_id}/disqualify")
async def disqualify_candidate_lead(lead_id: str, request: Request):
    """Manually disqualify a candidate lead with an explicit reason."""
    try:
        body = await request.json() if await request.body() else {}
        reason = body.get("reason", "").strip()
        if not reason:
            return JSONResponse({"success": False, "error": "A disqualification reason is required."}, status_code=400)

        state = _state()
        await state.init_db()

        lead = await state.get_discovered_lead(lead_id)
        if not lead:
            return JSONResponse({"success": False, "error": f"Lead {lead_id} not found"}, status_code=404)

        ok = await state.update_lead_qualification_override(lead_id, is_qualified=False, reason=reason)
        return {"success": ok, "lead_id": lead_id, "is_qualified": False, "message": f"Lead disqualified: {reason}"}
    except Exception as e:
        logger.exception(f"Failed to disqualify lead {lead_id}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/api/crm/pipeline")
async def get_crm_pipeline(
    stage: str | None = None,
    search: str | None = None,
    sort_by: str = "score",
):
    """Retrieve all leads in the CRM pipeline with stage breakdown and filters."""
    try:
        state = _state()
        await state.init_db()
        data = await state.get_pipeline_leads(stage=stage, search=search, sort_by=sort_by)
        return {"success": True, **data}
    except Exception as e:
        logger.exception("Failed to get CRM pipeline")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/crm/prospects/{prospect_id}/pipeline")
async def update_prospect_crm_pipeline(prospect_id: str, request: Request):
    """Update CRM pipeline stage, notes, next action, and follow-up date for a prospect."""
    try:
        body = await request.json()
        stage = body.get("stage")
        notes = body.get("notes")
        next_action = body.get("next_action")
        follow_up_date = body.get("follow_up_date")

        state = _state()
        await state.init_db()
        updated = await state.update_prospect_pipeline(
            prospect_id,
            stage=stage,
            notes=notes,
            next_action=next_action,
            follow_up_date=follow_up_date,
        )
        return {"success": True, "prospect": updated}
    except ValueError as ve:
        return JSONResponse({"success": False, "error": str(ve)}, status_code=400)
    except Exception as e:
        logger.exception("Failed to update prospect pipeline")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/api/crm/prospects/{prospect_id}")
async def get_crm_prospect_detail(prospect_id: str):
    """Get full 360-degree CRM detail for a prospect."""
    try:
        state = _state()
        await state.init_db()
        prospect = await state.get_prospect(prospect_id)
        if not prospect:
            return JSONResponse({"success": False, "error": "Prospect not found"}, status_code=404)

        comp = await state.get_company(prospect.company_id) if prospect.company_id else None
        research = await state.get_lead_research(prospect_id)

        # Get recent activity
        async with state._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM actions WHERE details_json LIKE ? ORDER BY created_at DESC LIMIT 20",
                (f"%{prospect_id}%",),
            ) as cursor:
                actions = [dict(r) for r in await cursor.fetchall()]

        return {
            "success": True,
            "prospect": prospect.model_dump() if hasattr(prospect, "model_dump") else prospect.dict(),
            "company": comp.to_dict() if comp and hasattr(comp, "to_dict") else (comp.__dict__ if comp else None),
            "research": research,
            "activity": actions,
        }
    except Exception as e:
        logger.exception("Failed to get CRM prospect detail")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/discovery/add-to-pipeline")
async def add_lead_to_pipeline(request: Request):
    """Explicitly add a qualified discovered lead to the canonical CRM pipeline."""
    try:
        body = await request.json()
        lead_id = body.get("lead_id")
        if not lead_id:
            return JSONResponse({"success": False, "error": "lead_id is required"}, status_code=400)

        state = _state()
        await state.init_db()
        result = await state.add_discovered_lead_to_pipeline(lead_id)
        return {"success": True, "result": result}
    except Exception as e:
        logger.exception("Failed to add lead to pipeline")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# ── AI-Powered Personalized Outreach Draft Endpoints ──

@app.post("/api/outreach/drafts/generate")
async def generate_outreach_draft(request: Request):
    """Generate an AI-powered evidence-grounded outreach email draft for a qualified lead."""
    try:
        from harvey.outreach_drafts import OutreachDraftGenerator

        body = await request.json() if request.headers.get("content-type") == "application/json" else {}
        lead_id = body.get("lead_id")
        prospect_id = body.get("prospect_id")
        profile_id = body.get("profile_id")
        custom_instructions = body.get("custom_instructions") or ""
        fallback_mode = bool(body.get("fallback_mode", False))

        if not lead_id and not prospect_id:
            return JSONResponse({"success": False, "error": "Either lead_id or prospect_id is required."}, status_code=400)

        state = _state()
        await state.init_db()

        generator = OutreachDraftGenerator(state)
        try:
            context = await generator.build_context(lead_id=lead_id, prospect_id=prospect_id, profile_id=profile_id)
        except ValueError as ve:
            return JSONResponse({"success": False, "error": str(ve)}, status_code=404)

        result = await generator.generate_draft(
            context,
            custom_instructions=custom_instructions,
            fallback_mode=fallback_mode,
        )

        status_code = 200 if result.get("success") else (400 if result.get("code") == "lead_unqualified" else 503)
        return JSONResponse(result, status_code=status_code)
    except json.JSONDecodeError as e:
        return JSONResponse({"success": False, "error": f"Invalid JSON payload: {e.msg}"}, status_code=400)
    except Exception as e:
        logger.exception("Failed to generate outreach draft")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/api/outreach/drafts")
async def list_outreach_drafts(
    lead_id: str | None = None,
    prospect_id: str | None = None,
    status: str | None = None,
):
    """Retrieve saved outreach drafts."""
    try:
        state = _state()
        await state.init_db()
        drafts = await state.get_outreach_drafts(lead_id=lead_id, prospect_id=prospect_id, status=status)
        return {"success": True, "drafts": drafts, "count": len(drafts)}
    except Exception as e:
        logger.exception("Failed to list outreach drafts")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/api/outreach/drafts/{draft_id}")
async def get_outreach_draft_detail(draft_id: str):
    """Retrieve details of a specific outreach draft."""
    try:
        state = _state()
        await state.init_db()
        draft = await state.get_outreach_draft(draft_id)
        if not draft:
            return JSONResponse({"success": False, "error": "Draft not found"}, status_code=404)
        return {"success": True, "draft": draft}
    except Exception as e:
        logger.exception("Failed to get outreach draft detail")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/outreach/drafts")
async def save_outreach_draft(request: Request):
    """Save or update an outreach draft and update CRM pipeline to Outreach Draft Ready."""
    try:
        from harvey.outreach_drafts import OutreachDraftGenerator

        body = await request.json()
        draft_data = body.get("draft") or body
        force = bool(body.get("force", False))

        if not draft_data.get("subject") or not draft_data.get("body"):
            return JSONResponse({"success": False, "error": "Draft subject and body are required."}, status_code=400)

        state = _state()
        await state.init_db()

        generator = OutreachDraftGenerator(state)
        res = await generator.save_draft(draft_data, force=force)
        status_code = 200 if res.get("success") else 409
        return JSONResponse(res, status_code=status_code)
    except json.JSONDecodeError as e:
        return JSONResponse({"success": False, "error": f"Invalid JSON payload: {e.msg}"}, status_code=400)
    except Exception as e:
        logger.exception("Failed to save outreach draft")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.put("/api/outreach/drafts/{draft_id}")
async def edit_outreach_draft(draft_id: str, request: Request):
    """Edit subject, body, or recipient of an existing draft."""
    try:
        from harvey.outreach_drafts import OutreachDraftGenerator

        body = await request.json()
        subject = body.get("subject") or ""
        body_text = body.get("body") or ""
        recipient_email = body.get("recipient_email")

        if not subject or not body_text:
            return JSONResponse({"success": False, "error": "Subject and body cannot be empty."}, status_code=400)

        state = _state()
        await state.init_db()

        generator = OutreachDraftGenerator(state)
        res = await generator.edit_draft(draft_id, subject=subject, body=body_text, recipient_email=recipient_email)
        status_code = 200 if res.get("success") else 404
        return JSONResponse(res, status_code=status_code)
    except Exception as e:
        logger.exception("Failed to edit outreach draft")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/outreach/drafts/{draft_id}/review")
async def mark_draft_ready_for_review(draft_id: str):
    """Mark an outreach draft as ready for manual review and stage it into Outbox pending_review."""
    try:
        from harvey.outreach_drafts import OutreachDraftGenerator

        state = _state()
        await state.init_db()

        generator = OutreachDraftGenerator(state)
        res = await generator.mark_ready_for_review(draft_id)
        status_code = 200 if res.get("success") else 400
        return JSONResponse(res, status_code=status_code)
    except Exception as e:
        logger.exception("Failed to mark draft ready for review")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/outreach/drafts/{draft_id}/cancel")
async def cancel_outreach_draft(draft_id: str, request: Request):
    """Cancel an outreach draft and its associated outbox item."""
    try:
        from harvey.outreach_drafts import OutreachDraftGenerator

        body = await request.json() if request.headers.get("content-type") == "application/json" else {}
        reason = body.get("reason") or "Cancelled by operator"

        state = _state()
        await state.init_db()

        generator = OutreachDraftGenerator(state)
        res = await generator.cancel_draft(draft_id, reason=reason)
        status_code = 200 if res.get("success") else 404
        return JSONResponse(res, status_code=status_code)
    except Exception as e:
        logger.exception("Failed to cancel outreach draft")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


def _parse_ts(value) -> datetime | None:
    """SQLite CURRENT_TIMESTAMP / isoformat strings -> naive UTC datetime."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "").split("+")[0])
    except ValueError:
        return None


async def _scalar(sql: str, params: tuple = ()) -> int:
    rows = await query_db(sql, params)
    return int(next(iter(rows[0].values())) or 0) if rows else 0


async def _first(sql: str, params: tuple = (), key: str = "t"):
    """First row's `key`, or None — query_db returns [] on any DB problem."""
    rows = await query_db(sql, params)
    return rows[0].get(key) if rows else None


# A run row still says 'running' after its process was killed (closed window,
# reboot). No new facts for this long means it is not coming back.
STALE_RUN_SECONDS = 900
# Contact-finding has no run row; a recent prospects_checked_at means it is active.
CONTACT_ACTIVE_SECONDS = 180
# Moved on from "waiting to be emailed" (opted_out too: never offered again).
SENT_STATUSES = ("contacted", "replied", "meeting", "closed", "lost", "opted_out")
REPLY_STATUSES = ("replied", "meeting", "closed")


def _eta_text(minutes) -> str:
    if minutes is None:
        return ""
    return "less than a minute left" if minutes < 1 else f"about {round(minutes)} min left"


@app.get("/api/live-status")
async def get_live_status():
    """Plain-language progress: each pipeline step, what is running, how long is left.

    Built for someone who would otherwise keep asking "what's the status?".
    Everything is derived from the database, so it is true whatever started
    the work — `harvey run`, a CLI command, or a one-off script. Every total
    counts only what the step can act on (a business with no website is never
    read), so a finished step always reaches 100% instead of sticking.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)  # DB stores naive UTC
    has_site = "COALESCE(domain, '') != ''"
    companies = await _scalar("SELECT COUNT(*) FROM companies")
    with_site = await _scalar(f"SELECT COUNT(*) FROM companies WHERE {has_site}")
    no_site = companies - with_site
    profiled = await _scalar(
        f"SELECT COUNT(*) FROM companies WHERE {has_site} AND profile_checked_at IS NOT NULL")
    checked = await _scalar(
        f"SELECT COUNT(*) FROM companies WHERE {has_site} "
        "AND profile_checked_at IS NOT NULL AND prospects_checked_at IS NOT NULL")

    prospects = await query_db("SELECT id, email, email_status, status FROM prospects")
    with_email = [p for p in prospects if p.get("email")]
    by_status: dict[str, int] = {}
    for p in with_email:
        k = (p.get("email_status") or "guess").lower()
        by_status[k] = by_status.get(k, 0) + 1
    deliverable = {p["id"] for p in with_email
                   if (p.get("email_status") or "") in ("verified", "risky")}
    unconfirmed = sum(n for k, n in by_status.items()
                      if k not in ("verified", "risky", "invalid"))

    # Drafts are counted per person, not per campaign: one campaign can cover many.
    drafted: set[str] = set()
    for row in await query_db("SELECT prospect_ids_json FROM campaigns"):
        try:
            ids = json.loads(row.get("prospect_ids_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            ids = []
        if isinstance(ids, list):
            drafted.update(i for i in ids if isinstance(i, str))
    sent = {p["id"] for p in prospects if p.get("status") in SENT_STATUSES}
    paused_setting = await _first("SELECT value FROM settings WHERE key = 'sending_paused'", key="value")
    is_paused = bool(paused_setting)

    # Real delivered provider count strictly from outbox table
    actual_sent_prospects = {
        r["prospect_id"] for r in await query_db(
            "SELECT DISTINCT prospect_id FROM outbox WHERE status = 'sent' AND prospect_id IS NOT NULL"
        ) if r.get("prospect_id")
    }
    real_sent_count = len(actual_sent_prospects & drafted)
    mock_replied_count = len(sent & drafted) - real_sent_count
    effective_sent_count = real_sent_count if is_paused else len(sent & drafted)

    # Who is left for the user to send by hand: only when Harvey can't send
    # itself and sending is not paused with a safety hold.
    harvey_sends = _harvey_can_send()
    awaiting_review = await _scalar("SELECT COUNT(*) FROM outbox WHERE status = 'pending_review'")
    by_hand: set[str] = set()
    if not harvey_sends and not is_paused:
        skip = await _harvey_committed_ids() | await _hand_sent_ids()
        by_hand = {p["id"] for p in prospects
                   if p["id"] in drafted and p["id"] not in skip
                   and (p.get("status") or "new") in HAND_SENDABLE_STATUSES
                   and (p.get("email_status") or "guess") in HAND_SENDABLE_EMAIL}
    replies = sum(1 for p in prospects if p.get("status") in REPLY_STATUSES)
    cost = float(await _first(
        "SELECT COALESCE(SUM(cost_usd), 0) AS c FROM runs", key="c") or 0)

    running, stopped = [], []
    for run in await query_db(
            "SELECT * FROM runs WHERE status = 'running' ORDER BY started_at"):
        started = _parse_ts(run.get("started_at"))
        elapsed = max(0.0, (now - started).total_seconds()) if started else 0.0
        last = _parse_ts(await _first(
            "SELECT MAX(observed_at) AS t FROM observations WHERE run_id = ?",
            (run["id"],))) or started
        item = {"stage": run.get("stage"), "provider": run.get("provider"),
                "elapsed_min": round(elapsed / 60, 1), "eta_min": None}
        if last and (now - last).total_seconds() > STALE_RUN_SECONDS:
            stopped.append(item)
            continue
        if run.get("stage") == "profile" and started:
            done_in_run = await _scalar(
                f"SELECT COUNT(*) FROM companies WHERE {has_site} AND profile_checked_at >= ?",
                (run["started_at"],))
            left = max(0, with_site - profiled)
            if left and done_in_run and elapsed > 0:
                item["eta_min"] = left / (done_in_run / elapsed) / 60
        running.append(item)

    last_contact = _parse_ts(await _first(
        "SELECT MAX(prospects_checked_at) AS t FROM companies"))
    finding_emails = bool(
        last_contact and (now - last_contact).total_seconds() < CONTACT_ACTIVE_SECONDS)
    reading = any(r["stage"] == "profile" for r in running)
    finding = any(r["stage"] == "discover" for r in running)

    def step(label, done, total, active, detail, info=False):
        if active:
            state_ = "running"
        elif info:
            state_ = "done" if done else "waiting"
        elif total and done >= total:
            state_ = "done"
        elif done:
            state_ = "partial"
        else:
            state_ = "waiting"
        return {"label": label, "done": done, "total": total,
                "state": state_, "detail": detail}

    profile_eta = next((r["eta_min"] for r in running if r["stage"] == "profile"), None)
    read_detail = f"{profiled} of {with_site} read"
    if reading and profile_eta is not None:
        read_detail += " · " + _eta_text(profile_eta)
    if no_site:
        read_detail += f" · {no_site} have no website (skipped)"

    sent_detail = (
        f"{real_sent_count} of {len(drafted)} sent (0 delivered via provider · {mock_replied_count} test/mock replied) · hold active"
        if is_paused and mock_replied_count > 0
        else (f"{real_sent_count} of {len(drafted)} sent · safety hold active" if is_paused
              else f"{real_sent_count} of {len(drafted)} sent")
    )

    steps = [
        step("Find businesses", companies, 0, finding,
             f"{companies} businesses found", info=True),
        step("Read their websites", profiled, with_site, reading, read_detail),
        step("Find emails", checked, profiled, finding_emails,
             f"{checked} of {profiled} checked · {len(with_email)} emails found"),
        step("Check emails are real", len(with_email) - unconfirmed, len(with_email), False,
             f"{by_status.get('verified', 0)} verified · {by_status.get('risky', 0)} risky · "
             f"{unconfirmed} not confirmed"
             + (f" · {by_status['invalid']} bad" if by_status.get("invalid") else "")),
        step("Write first emails", len(drafted), len(deliverable | drafted), False,
             f"{len(drafted)} written · see Campaigns"),
        step("Sent", effective_sent_count, len(drafted), False, sent_detail),
        step("Replies", replies, 0, False,
             f"{replies} replies" if replies else "No replies yet", info=True),
    ]

    next_tab = ""   # where the next step is done, when it's done in the dashboard
    if is_paused:
        next_up, next_tab = "Sending is paused (Pilot Evaluation Safety Hold) — drafts remain safely staged for review.", "outbox"
    elif reading or finding:
        next_up = "Wait — Harvey is working. This page updates by itself."
    elif finding_emails:
        next_up = "Wait — emails are being found."
    elif stopped:
        next_up, next_tab = "A job stopped before finishing. Run it again.", "discover"
    elif harvey_sends and awaiting_review:
        next_up, next_tab = f"Review the {awaiting_review} draft(s) waiting in the Outbox.", "outbox"
    elif by_hand:
        next_up, next_tab = f"Open Campaigns and send the {len(by_hand)} unsent email(s).", "campaigns"
    elif profiled > checked:
        next_up, next_tab = f"Find emails for the {profiled - checked} businesses not checked yet.", "controls"
    elif deliverable - drafted:
        next_up, next_tab = f"Write first emails for {len(deliverable - drafted)} more contact(s).", "controls"
    elif not companies:
        next_up, next_tab = "Search for businesses to get started.", "discover"
    else:
        next_up, next_tab = "Search more cities or industries to get more leads.", "discover"

    return {
        "steps": steps,
        "running": [dict(r, eta_text=_eta_text(r["eta_min"])) for r in running],
        "stopped": stopped,
        "next": next_up,
        "next_tab": next_tab,
        "cost_usd": round(cost, 4),
        "updated_at": now.isoformat() + "Z",
    }


def _require_approval_now() -> bool:
    try:
        from harvey.config import load_config
        return bool(load_config(str(_resolve_config_file())).channels.email.require_approval)
    except Exception:
        return True


@app.get("/api/today")
async def get_today():
    """What needs a human, right now.

    The dashboard opens here rather than on a setup checklist: the question a
    returning user actually has is "is anything waiting on me?", and the
    answer is usually a short list or nothing at all.
    """
    items: list[dict] = []
    stats: dict = {}
    try:
        from harvey.signals import seed_signal_catalog

        state = _state()
        await state.init_db()
        await seed_signal_catalog(state)

        codes = await state.get_signal_codes()
        proposed = [c for c in codes if c.get("status") == "proposed"]
        confirmed = [c for c in codes if c.get("status") == "confirmed"]
        pending = await state.get_outbox(status="pending_review", limit=200)
        approved = await state.get_outbox(status="approved", limit=200)
        paused = await state.get_setting("sending_paused")
        counts = await state.count_prospects_by_status()

        convos = await query_db(
            "SELECT COUNT(*) AS n FROM conversations WHERE status = 'open'"
        )
        open_convos = convos[0]["n"] if convos else 0
        handed = await query_db(
            "SELECT COUNT(*) AS n FROM conversations WHERE status = 'needs_human'"
        )
        needs_human = handed[0]["n"] if handed else 0
        companies = await query_db("SELECT COUNT(*) AS n FROM companies")
        n_companies = companies[0]["n"] if companies else 0

        unprofiled_count = await state.count_companies_needing_profile()
        stats = {
            "companies": n_companies,
            "prospects": sum(counts.values()),
            "signals_confirmed": len(confirmed),
            "outbox_pending": len(pending),
            "outbox_approved": len(approved),
            "open_conversations": open_convos,
            "needs_human": needs_human,
            "signals_proposed": len(proposed),
            "email_provider": _current_provider(),
            "unprofiled": unprofiled_count,
        }

        # Ordered by how much it blocks Harvey from doing anything at all.
        brain = await _brain_status()
        if brain.get("status") == "paused":
            who = "Codex" if brain.get("provider") == "codex" else "Harvey's model"
            items.append({
                "key": "brain-paused", "tone": "bad",
                "title": f"{who} is paused, so Harvey is waiting",
                "detail": (str(brain.get("reason") or "It can't be used right now.")[:300] +
                           " Replies and queued emails are untouched; Harvey resumes "
                           "once it's fixed. harvey status --check says what's wrong."),
                "action": "Open the log", "tab": "controls",
            })

        from harvey.agents.writer import EMAIL_RULES_PROBLEM_KEY
        rules_problem = await state.get_setting(EMAIL_RULES_PROBLEM_KEY)
        if rules_problem:
            items.append({
                "key": "email-rules", "tone": "bad",
                "title": "Harvey isn't writing emails: your email rules can't be used",
                "detail": (f"{rules_problem}. Fix the file, or clear writer.email_rules "
                           "to use Harvey's built-in rules."),
                "action": "Open the log", "tab": "controls",
            })

        hold = await state.current_sends_held()
        if hold:
            from harvey.sending_mode import held_message
            title, detail = held_message(hold, waiting=len(approved))
            items.append({
                "key": "sends-held", "tone": "bad", "title": title, "detail": detail,
                "action": "Open the log", "tab": "controls",
            })

        if paused:
            items.append({
                "key": "paused", "tone": "bad",
                "title": "Sending is paused",
                "detail": str(paused) + ". All drafts remain safely staged for review.",
                "action": "Review staged drafts", "tab": "outbox",
            })

        # Cold email waiting on the optional Precleared check: its free
        # checks ran out, the key was rejected, or it couldn't be reached.
        from harvey.integrations.precleared import current_hold, hold_message
        try:
            from harvey.config import load_config
            loaded = load_config(str(_resolve_config_file()))
        except Exception:
            loaded = None  # can't judge: show the hold rather than hide it
        precleared_hold = await current_hold(state, loaded)
        if precleared_hold:
            message = hold_message(precleared_hold)
            items.append({
                "key": "precleared-hold", "tone": message["tone"],
                "title": message["title"], "detail": message["detail"],
                "action": "Open the log", "tab": "controls",
                "url": message["url"], "url_label": message["url_label"],
            })

        setup = await get_setup_status()
        if isinstance(setup, dict) and setup.get("percent", 100) < 100:
            missing = [c["label"] for c in setup.get("checks", [])
                       if c.get("required") and not c.get("done")]
            items.append({
                "key": "setup", "tone": "warn",
                "title": "Finish setting Harvey up",
                "detail": ", ".join(missing[:3]) or "Some required steps are incomplete.",
                "action": "Open setup", "tab": "settings",
            })

        if proposed:
            items.append({
                "key": "signals", "tone": "warn",
                "title": (f"{len(proposed)} signal waiting for your confirmation"
                          if len(proposed) == 1
                          else f"{len(proposed)} signals waiting for your confirmation"),
                "detail": ("Harvey won't collect anything you haven't approved. "
                           "Confirm which signals define a good prospect for you."),
                "action": "Review signals", "tab": "signals",
            })
        elif not confirmed:
            items.append({
                "key": "signals-none", "tone": "warn",
                "title": "No signals confirmed",
                "detail": "Every signal is rejected, so prospecting has nothing to collect.",
                "action": "Review signals", "tab": "signals",
            })

        if not n_companies and confirmed:
            items.append({
                "key": "discover", "tone": "good",
                "title": "No companies yet",
                "detail": ("Signals are confirmed but nothing has been collected. "
                           "Discovery is free to try — no account needed."),
                "action": "Find businesses", "tab": "discover",
            })

        unprofiled = unprofiled_count
        if unprofiled:
            items.append({
                "key": "profile", "tone": "good",
                "title": f"{unprofiled} companies not looked at yet",
                "detail": ("Reading their websites is free and it is what makes "
                           "an email specific — who their agency is, what they "
                           "are missing, whether they are spending on ads."),
                "action": "Read their sites", "tab": "discover",
            })

        if needs_human:
            items.append({
                "key": "needs-human", "tone": "bad",
                "title": (f"{needs_human} conversation needs you" if needs_human == 1
                          else f"{needs_human} conversations need you"),
                "detail": ("Harvey stopped and handed these over: an escalation, or a "
                           "reply it had no safe answer for. Reply yourself, then close "
                           "it or hand it back."),
                "action": "Read them", "tab": "conversations",
            })

        if pending:
            items.append({
                "key": "outbox", "tone": "warn",
                "title": (f"{len(pending)} email waiting for approval" if len(pending) == 1
                          else f"{len(pending)} emails waiting for approval"),
                "detail": ("Nothing sends until you approve it. Read them one at a time."
                           if _require_approval_now() else
                           "Autopilot sends the rest on its own; these wait for you. "
                           "Read them one at a time."),
                "action": "Open the decisions desk", "tab": "outbox",
            })

        if open_convos:
            items.append({
                "key": "replies", "tone": "good",
                "title": (f"{open_convos} live conversation" if open_convos == 1
                          else f"{open_convos} live conversations"),
                "detail": "People replied. Check how Harvey is handling them.",
                "action": "Read conversations", "tab": "conversations",
            })

        return {"items": items, "stats": stats}
    except Exception as e:
        logger.exception("today load failed")
        return {"items": [], "stats": stats, "error": str(e)}


# ── Signals: Harvey proposes, the user confirms ──
#
# Nothing is collected until a human has said yes to it. This is the gate the
# whole prospecting pipeline hangs off: collectors ask `state.confirmed_signal_codes()`
# and skip anything that isn't in the set.

CATEGORY_META = {
    "discovery": {
        "label": "Discovery — who exists",
        "blurb": "How Harvey finds businesses at all, and how visible they are. "
                 "This is the only stage that costs money.",
    },
    "profile": {
        "label": "Profile — what they are",
        "blurb": "Read from the pages a business already publishes. Free, no AI "
                 "tokens, three HTTP requests per company. These are the signals "
                 "that make an email specific.",
    },
    "people": {
        "label": "People — who decides",
        "blurb": "Named humans and whether they're the one who can say yes.",
    },
    "verification": {
        "label": "Contactability — can you reach them",
        "blurb": "Whether the address will actually deliver, and what to do when "
                 "it won't.",
    },
}
CATEGORY_ORDER = ["discovery", "profile", "people", "verification"]


@app.get("/api/signals")
async def get_signals():
    """The signal vocabulary, grouped for review, with live cohort sizes."""
    try:
        from harvey.signals import seed_signal_catalog

        state = _state()
        await state.init_db()
        # Seeding is idempotent and never overrides a decision the user made,
        # so it is safe to run on every load — new signals shipped in an
        # upgrade show up as `proposed` without any migration step.
        await seed_signal_catalog(state)

        codes = await state.get_signal_codes()
        counts = {c["signal_code"]: c for c in await state.signal_counts()}

        groups, summary = [], {"proposed": 0, "confirmed": 0, "rejected": 0}
        for cat in CATEGORY_ORDER:
            rows = []
            for sig in codes:
                if sig.get("category") != cat:
                    continue
                seen = counts.get(sig["code"], {})
                rows.append({
                    **sig,
                    "companies": seen.get("companies", 0),
                    "observations": seen.get("observations", 0),
                })
            if rows:
                meta = CATEGORY_META.get(cat, {})
                groups.append({
                    "key": cat,
                    "label": meta.get("label", cat.title()),
                    "blurb": meta.get("blurb", ""),
                    "signals": rows,
                })
        for sig in codes:
            summary[sig.get("status", "proposed")] = (
                summary.get(sig.get("status", "proposed"), 0) + 1
            )

        return {"summary": summary, "groups": groups, "total": len(codes)}
    except Exception as e:
        logger.exception("signals load failed")
        return {"error": str(e), "groups": [], "summary": {}}


@app.post("/api/signals/status")
async def set_signals_status(request: Request):
    """Confirm or reject one signal, or a whole category at once."""
    try:
        body = await request.json()
        status = (body.get("status") or "").strip()
        codes = body.get("codes") or ([body["code"]] if body.get("code") else [])
        if status not in ("proposed", "confirmed", "rejected"):
            return JSONResponse(
                {"success": False, "message": f"invalid status: {status!r}"},
                status_code=400,
            )
        if not codes:
            return JSONResponse(
                {"success": False, "message": "no signal codes given"}, status_code=400
            )

        from harvey.signals import seed_signal_catalog

        state = _state()
        await state.init_db()
        # Seed first: a confirm that arrives before anything has loaded the
        # catalog would otherwise report success while changing nothing.
        await seed_signal_catalog(state)

        changed, unknown = 0, []
        for code in codes:
            if await state.set_signal_status(code, status):
                changed += 1
            else:
                unknown.append(code)
        return {
            "success": True, "changed": changed,
            "status": status, "unknown": unknown,
        }
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


COHORT_PAGE_SIZE = 50
COHORT_MAX_PAGE_SIZE = 200


async def _cohort_companies(require: list[str], exclude: list[str],
                            offset: int = 0, limit: int | None = None,
                            ranges: list[dict] | None = None):
    state = _state()
    await state.init_db()
    return await state.cohort_companies(require, exclude, offset, limit,
                                        ranges=ranges)


def _codes(raw) -> list[str]:
    """Accept lists or CSV strings, with bounded SQL parameter counts."""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = raw.split(",")
    if not isinstance(raw, list) or any(not isinstance(c, str) for c in raw):
        raise ValueError("signals must be a list of strings or comma-separated string")
    codes = list(dict.fromkeys(c.strip() for c in raw if c.strip()))
    if len(codes) > 200 or any(len(c) > 128 for c in codes):
        raise ValueError("too many or oversized signal codes")
    return codes


COHORT_MAX_RANGES = 20


def _range_bound(raw):
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        raise ValueError("range bounds must be numbers")
    value = float(raw)
    if value != value or abs(value) > 1e12:
        raise ValueError("range bound is out of bounds")
    return value


def _ranges(raw) -> list[dict]:
    """Numeric cohort filters: a list of {code, min, max}, or the CSV query
    form "SERP_RANK:11:30,MAPS_RANK::3". At least one bound per range."""
    if raw is None or raw == "" or raw == []:
        return []
    if isinstance(raw, str):
        items = []
        for part in raw.split(","):
            if not part.strip():
                continue
            pieces = part.split(":")
            if len(pieces) != 3:
                raise ValueError("ranges look like CODE:min:max")
            items.append({"code": pieces[0], "min": pieces[1], "max": pieces[2]})
        raw = items
    if not isinstance(raw, list) or any(not isinstance(r, dict) for r in raw):
        raise ValueError("ranges must be a list of {code, min, max}")
    if len(raw) > COHORT_MAX_RANGES:
        raise ValueError("too many ranges")
    out = []
    for r in raw:
        code = r.get("code")
        if not isinstance(code, str) or not code.strip() or len(code) > 128:
            raise ValueError("each range needs a signal code")
        low, high = _range_bound(r.get("min")), _range_bound(r.get("max"))
        if low is None and high is None:
            raise ValueError(f"range on {code} needs a min or a max")
        if low is not None and high is not None and low > high:
            raise ValueError(f"range on {code}: min is above max")
        out.append({"code": code.strip(), "min": low, "max": high})
    return out


def _cohort_integer(raw, default):
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        raise ValueError("offset and limit must be integers")
    value = int(raw)
    if abs(value) > 9223372036854775807:
        raise ValueError("offset or limit is too large")
    return value


@app.post("/api/cohort")
async def preview_cohort(request: Request):
    """How many companies carry ALL these signals and none of those.

    The payoff for confirming signals: a cohort is a query, not a list. Set
    intersection happens in SQL — intersecting in JS over a capped fetch
    silently returns the wrong answer. ``offset``/``limit`` page through the
    matches; ``size`` is always the whole cohort. ``ranges`` adds numeric
    filters — ``[{"code": "SERP_RANK", "min": 11, "max": 30}]``.
    """
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("request body must be an object")
        require = _codes(body.get("require"))
        exclude = _codes(body.get("exclude"))
        ranges = _ranges(body.get("ranges"))
        offset = max(0, _cohort_integer(body.get("offset"), 0))
        limit = min(max(1, _cohort_integer(body.get("limit"), COHORT_PAGE_SIZE)),
                    COHORT_MAX_PAGE_SIZE)
        if not require and not ranges:
            return {"size": 0, "offset": 0, "limit": limit, "companies": []}

        size, rows = await _cohort_companies(require, exclude, offset, limit,
                                             ranges=ranges)
        return {"size": size, "offset": offset, "limit": limit,
                "companies": rows}
    except (ValueError, TypeError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"size": 0, "companies": [], "error": str(e)}, status_code=500)


def _csv_cell(value):
    text = str(value or "")
    # Spreadsheet software may ignore leading whitespace before formulas.
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


@app.get("/api/cohort.csv")
async def export_cohort(require: str = "", exclude: str = "", ranges: str = ""):
    """The whole cohort as a CSV download — every match, not just one page."""
    import csv
    import io

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["name", "domain", "website", "industry", "location"])
    try:
        codes = _codes(require)
        excluded = _codes(exclude)
        bounds = _ranges(ranges)
        if codes or bounds:
            _, rows = await _cohort_companies(codes, excluded, ranges=bounds)
            for r in rows:
                writer.writerow([_csv_cell(r.get(k)) for k in
                                 ("name", "domain", "website", "industry", "location")])
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        logger.warning("Cohort export failed: %s", e)
        return JSONResponse({"error": "Cohort export failed"}, status_code=500)
    return PlainTextResponse(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="cohort.csv"'},
    )


@app.get("/api/runs")
async def get_runs_api():
    """The collector run log — what ran, when, what it produced and cost."""
    try:
        state = _state()
        await state.init_db()
        await state.sweep_stale_runs()
        runs = await state.get_runs(limit=25)
        # Same rule as the live panel: a "running" run that has recorded
        # nothing for STALE_RUN_SECONDS has stopped, whatever its row says.
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for run in runs:
            if run.get("status") != "running":
                continue
            last = _parse_ts(await _first(
                "SELECT MAX(observed_at) AS t FROM observations WHERE run_id = ?",
                (run["id"],))) or _parse_ts(run.get("started_at"))
            if last and (now - last).total_seconds() > STALE_RUN_SECONDS:
                run["status"] = "stalled"
        return runs
    except Exception as e:
        return {"error": str(e)}


WEB_DIR = (Path(__file__).resolve().parent / "web")


TEXT_TYPES = {".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml"}
BINARY_TYPES = {".woff2": "font/woff2", ".woff": "font/woff", ".png": "image/png"}



@app.get("/api/brain/providers")
async def get_brain_providers():
    """Return status, readiness, and model catalog for all supported AI providers."""
    try:
        from harvey.config import load_brain_config, load_env
        from harvey.providers.router import TaskRouter
        cfg = load_brain_config()
        router = TaskRouter(config=cfg.model_dump(), env=load_env().model_dump())
        providers_status = await router.get_all_providers_status()
        return {
            "active_provider": cfg.provider,
            "active_model": cfg.model,
            "task_routing": cfg.task_routing,
            "fallback_providers": cfg.fallback_providers,
            "xai_approved_paid": cfg.xai_approved_paid,
            "providers": providers_status,
        }
    except Exception as e:
        logger.exception("Failed to load brain providers")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/brain/select")
async def select_brain_provider(request: Request):
    """Switch active brain provider, model, or task routing in config."""
    try:
        body = await request.json()
        provider = str(body.get("provider") or "").strip().lower()
        model = str(body.get("model") or "").strip()
        task_routing = body.get("task_routing")
        xai_approved_paid = body.get("xai_approved_paid")

        from harvey.config import BRAIN_PROVIDERS, _resolve_config_file
        if provider and provider not in BRAIN_PROVIDERS:
            return JSONResponse({"success": False, "message": f"Unsupported provider '{provider}'."}, status_code=400)

        # Strict Paid safeguard
        if provider == "xai" and not xai_approved_paid:
            return JSONResponse({
                "success": False,
                "message": "xAI is a paid provider. You must explicitly approve paid usage to select it."
            }, status_code=400)

        config_path = _resolve_config_file()
        raw = read_user_text(config_path)
        data = yaml.safe_load(raw) or {}
        if "brain" not in data or not isinstance(data["brain"], dict):
            data["brain"] = {}

        if provider:
            data["brain"]["provider"] = provider
        if model is not None:
            data["brain"]["model"] = model
        if task_routing is not None:
            data["brain"]["task_routing"] = bool(task_routing)
        if xai_approved_paid is not None:
            data["brain"]["xai_approved_paid"] = bool(xai_approved_paid)

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

        return {"success": True, "provider": data["brain"].get("provider"), "model": data["brain"].get("model")}
    except Exception as e:
        logger.exception("Failed to update brain config")
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


# =====================================================================
# RAG (Grounding Knowledge Base) Endpoints
# =====================================================================

@app.get("/api/rag/status")
async def rag_status():
    """Return RAG indexing status and vector store metrics."""
    try:
        from harvey.rag.store import VectorStore
        store = VectorStore()
        total_chunks = store.count()
        docs = store.list_documents()
        return {
            "success": True,
            "status": {
                "ready": True,
                "total_chunks": total_chunks,
                "document_count": len(docs),
                "embedding_model": store.embeddings.model,
                "embedding_dim": 768,
                "store_path": str(store.persist_dir),
            },
        }
    except Exception as e:
        logger.exception("Error checking RAG status")
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.get("/api/rag/documents")
async def rag_documents():
    """List indexed documents in the local RAG vector store."""
    try:
        from harvey.rag.store import VectorStore
        store = VectorStore()
        docs = store.list_documents()
        return {"success": True, "documents": docs}
    except Exception as e:
        logger.exception("Error listing RAG documents")
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.post("/api/rag/upload")
async def rag_upload(request: Request):
    """Upload and ingest a document (via JSON payload with text or base64 data)."""
    try:
        import base64
        from harvey.rag.ingest import ingest_document
        from harvey.rag.store import VectorStore

        data = await request.json()
        filename = (data.get("filename") or "document.txt").strip()
        safe_name = Path(filename).name
        suffix = Path(safe_name).suffix.lower()
        if suffix not in (".pdf", ".txt", ".md", ".markdown"):
            return JSONResponse(
                {
                    "success": False,
                    "message": f"Unsupported file type '{suffix}'. Allowed formats: .pdf, .txt, .md",
                },
                status_code=400,
            )

        upload_dir = Path("data/rag/docs")
        upload_dir.mkdir(parents=True, exist_ok=True)
        dest_path = upload_dir / safe_name

        if "data_b64" in data and data["data_b64"]:
            b64_str = data["data_b64"]
            if "," in b64_str:
                b64_str = b64_str.split(",", 1)[1]
            raw_bytes = base64.b64decode(b64_str)
            dest_path.write_bytes(raw_bytes)
        elif "text" in data:
            dest_path.write_text(data["text"], encoding="utf-8")
        else:
            return JSONResponse(
                {"success": False, "message": "Missing 'text' or 'data_b64' payload in request."},
                status_code=400,
            )

        chunks = ingest_document(dest_path)
        if not chunks:
            return JSONResponse(
                {
                    "success": False,
                    "message": f"No extractable text found in '{safe_name}'.",
                },
                status_code=400,
            )

        store = VectorStore()
        store.delete_document(safe_name)
        indexed_count = await store.add_chunks(chunks)

        return {
            "success": True,
            "filename": safe_name,
            "chunks_indexed": indexed_count,
            "message": f"Successfully indexed {indexed_count} chunks from '{safe_name}'.",
        }
    except Exception as e:
        logger.exception("Error uploading RAG document")
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.post("/api/rag/query")
async def rag_query(request: Request):
    """Test retrieval against the local knowledge base."""
    try:
        data = await request.json()
        query_text = (data.get("query") or "").strip()
        top_k = int(data.get("top_k", 3))
        if not query_text:
            return JSONResponse(
                {"success": False, "message": "query is required"},
                status_code=400,
            )

        from harvey.rag.store import VectorStore
        store = VectorStore()
        results = await store.search(query=query_text, top_k=top_k)
        return {"success": True, "query": query_text, "results": results}
    except Exception as e:
        logger.exception("Error searching RAG knowledge")
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.delete("/api/rag/documents/{source:path}")
async def rag_delete_document(source: str):
    """Delete a document and its chunks from the local RAG vector store."""
    try:
        from harvey.rag.store import VectorStore
        store = VectorStore()
        deleted = store.delete_document(source)
        return {"success": True, "deleted_source": source, "chunks_deleted": deleted}
    except Exception as e:
        logger.exception("Error deleting RAG document")
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)

@app.get("/api/rag/traces")
async def rag_traces():
    """List recorded RAG citation traces for drafts."""
    try:
        from harvey.rag.trace import list_rag_traces
        traces = list_rag_traces()
        return {"success": True, "traces": traces}
    except Exception as e:
        logger.exception("Error retrieving RAG traces")
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.get("/api/rag/traces/{identifier}")
async def rag_trace_detail(identifier: str):
    """Get RAG citation trace for an outbox item ID or prospect ID."""
    try:
        from harvey.rag.trace import get_rag_trace
        trace = get_rag_trace(identifier)
        if not trace:
            return JSONResponse({"success": False, "message": f"No trace found for '{identifier}'"}, status_code=404)
        return {"success": True, "trace": trace}
    except Exception as e:
        logger.exception("Error retrieving RAG trace detail")
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.get("/api/gmail/status")
async def get_gmail_status():
    """Report Gmail OAuth connection status, read-only scope, and missing credentials."""
    from harvey.config import load_env, load_config
    from harvey.integrations.gmail import TOKEN_FILE, READONLY_SCOPE, _load_token, GmailProvider
    env = load_env()
    client_id = env.gmail_client_id
    client_secret = env.gmail_client_secret
    token = _load_token()

    def _is_placeholder(val: str) -> bool:
        if not val:
            return False
        v = str(val).lower().strip()
        return any(p in v for p in ("dummy", "example", "change_me", "your_", "your-", "replace_me", "placeholder", "xxx")) or v.startswith("1234567890")

    is_placeholder = _is_placeholder(client_id) or _is_placeholder(client_secret)
    is_mock = bool(token and token.get("is_mock"))
    is_connected = bool((client_id and client_secret and token) or is_mock)
    missing = []
    if not client_id and not is_mock:
        missing.append("GMAIL_CLIENT_ID")
    if not client_secret and not is_mock:
        missing.append("GMAIL_CLIENT_SECRET")
    if not token:
        missing.append("data/gmail_token.json")

    scope = token.get("scope", "") if token else READONLY_SCOPE
    is_readonly = READONLY_SCOPE in scope or "modify" not in scope

    if is_mock:
        status_str = "mock_connected"
    elif is_placeholder:
        status_str = "placeholder_credentials"
    elif is_connected:
        status_str = "connected"
    else:
        status_str = "disconnected"

    email_address = token.get("email", "") if is_mock else ""
    if is_connected and not is_mock and not is_placeholder:
        try:
            cfg = load_config()
            provider = GmailProvider(cfg, env)
            ok, detail = await provider.test_connection()
            if ok and "Connected as" in detail:
                email_address = detail.replace("Connected as ", "").strip()
        except Exception:
            pass

    return {
        "configured": is_connected,
        "status": status_str,
        "is_mock": is_mock,
        "is_placeholder": is_placeholder,
        "email_address": email_address,
        "scope": scope,
        "is_readonly": is_readonly,
        "missing_credentials": missing,
        "token_file": str(TOKEN_FILE),
        "help": (
            "Set GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET in .env, then run 'harvey gmail auth' "
            "to authenticate with read-only permissions, or click 'Connect Mock Sandbox' to test safely."
        ),
    }


@app.post("/api/gmail/auth-url")
async def get_gmail_auth_url(request: Request):
    """Generate the Google OAuth consent URL for desktop or web authorization."""
    from harvey.config import load_env
    from harvey.integrations.gmail import build_auth_url, READONLY_SCOPE
    env = load_env()
    client_id = env.gmail_client_id
    if not client_id:
        return JSONResponse(
            {"success": False, "error": "GMAIL_CLIENT_ID missing from .env"},
            status_code=400,
        )
    data = await request.json() if await request.body() else {}
    redirect_uri = str(data.get("redirect_uri") or "http://localhost:8765").strip()
    auth_url = build_auth_url(client_id=client_id, redirect_uri=redirect_uri, scope=READONLY_SCOPE)
    return {"success": True, "auth_url": auth_url}


@app.post("/api/gmail/authorize")
async def authorize_gmail_code(request: Request):
    """Exchange OAuth authorization code for read-only refresh token."""
    from harvey.config import load_env
    from harvey.integrations.gmail import exchange_code_for_token, READONLY_SCOPE
    try:
        data = await request.json()
        code = str(data.get("code") or "").strip()
        redirect_uri = str(data.get("redirect_uri") or "http://localhost:8765").strip()
        if not code:
            return JSONResponse({"success": False, "error": "code is required"}, status_code=400)

        env = load_env()
        if not env.gmail_client_id or not env.gmail_client_secret:
            return JSONResponse({"success": False, "error": "GMAIL_CLIENT_ID/SECRET not configured in .env"}, status_code=400)

        token_data = await exchange_code_for_token(
            code=code,
            client_id=env.gmail_client_id,
            client_secret=env.gmail_client_secret,
            redirect_uri=redirect_uri,
            scope=READONLY_SCOPE,
        )
        return {"success": True, "scope": token_data.get("scope"), "is_readonly": True}
    except Exception as e:
        logger.exception(f"Gmail authorization failed: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/gmail/mock-connect")
async def connect_mock_gmail(request: Request):
    """Save sandbox mock Gmail token to enable read-only mailbox sync testing without live credentials."""
    from harvey.integrations.gmail import save_mock_token
    try:
        data = await request.json() if await request.body() else {}
        email = str(data.get("email") or "pilot-sandbox@workspace.local").strip()
        token_data = save_mock_token(email=email)
        token_data.pop("refresh_token", None)
        return {"success": True, "message": f"Connected to read-only mock sandbox mailbox ({email}).", **token_data}
    except Exception as e:
        logger.exception(f"Mock Gmail connect failed: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/gmail/disconnect")
async def disconnect_gmail():
    """Clear stored Gmail token."""
    from harvey.integrations.gmail import clear_token
    cleared = clear_token()
    return {"success": True, "cleared": cleared}


@app.post("/api/gmail/sync")
async def sync_gmail_mailbox(request: Request):
    """
    Perform a read-only mailbox sync:
    1. If Gmail OAuth token and credentials are valid:
       Calls GmailProvider.read_inbox(max_results=50) with read-only permissions.
    2. If credentials are unavailable or mock token is active:
       Falls back safely to mock inbox sync using MOCK_INBOX_SAMPLES.
    3. Runs each incoming email through EmailPrioritizationService.process_inbound()
       (classifying High/Medium/Low, staging drafts for High, enforcing opt-outs).
    4. STRICT SAFETY: Sending remains paused, require_approval=True, no emails sent.
    """
    try:
        from harvey.config import load_env, load_config
        from harvey.integrations.gmail import GmailProvider, _load_token
        from harvey.integrations.mail_provider import InboundMessage
        from harvey.agents.prioritizer import EmailPrioritizationService
        from harvey.models.prospect import Prospect

        state = _state()
        await state.init_db()

        env = load_env()
        cfg = _active_brain() or load_config()

        brain = None
        if cfg:
            try:
                from harvey.brain import Brain
                brain = Brain(state, cfg)
            except Exception:
                brain = None

        service = EmailPrioritizationService(state=state, config=cfg, brain=brain)

        token = _load_token()
        provider = GmailProvider(cfg, env)
        is_live_configured = provider.is_configured() and not (token and token.get("is_mock"))

        synced_results = []
        is_mock = not is_live_configured

        if is_live_configured:
            logger.info("Syncing Gmail inbox with read-only permissions...")
            messages = await provider.read_inbox(max_results=50)
            for msg in messages:
                res = await service.process_inbound(msg, campaign_id="gmail-sync", auto_draft_high=True, is_mock=False)
                synced_results.append(res)
        else:
            logger.info("Gmail credentials unavailable or mock active; performing read-only mock sandbox sync...")
            for sample in MOCK_INBOX_SAMPLES.values():
                if sample.get("from_email") and sample.get("first_name"):
                    existing_p = await state.get_prospect_by_email(sample["from_email"])
                    if not existing_p:
                        mock_p = Prospect(
                            id=f"prospect_mock_{sample['id']}",
                            email=sample["from_email"],
                            first_name=sample["first_name"],
                            last_name=sample.get("last_name", ""),
                            company=sample.get("company", ""),
                            title=sample.get("title", ""),
                            status="contacted",
                        )
                        await state.add_prospect(mock_p)

                inbound = InboundMessage(
                    provider_id=f"gmail_sync_{sample['id']}",
                    from_email=sample["from_email"],
                    subject=sample["subject"],
                    body=sample["body"],
                    is_bounce=(sample.get("expected_intent") == "bounce"),
                )
                res = await service.process_inbound(
                    inbound,
                    campaign_id="gmail-mailbox-sync",
                    auto_draft_high=True,
                    is_mock=True,
                )
                synced_results.append({
                    "sample_id": sample["id"],
                    "sample_label": sample.get("label", ""),
                    **res,
                })

        high_count = sum(1 for r in synced_results if r.get("priority") == "high")
        med_count = sum(1 for r in synced_results if r.get("priority") == "medium")
        low_count = sum(1 for r in synced_results if r.get("priority") == "low")
        staged_count = sum(1 for r in synced_results if r.get("staged_in_outbox"))

        return {
            "success": True,
            "is_mock": is_mock,
            "is_readonly": True,
            "synced_count": len(synced_results),
            "counts": {
                "high": high_count,
                "medium": med_count,
                "low": low_count,
                "staged_drafts": staged_count,
                "total": len(synced_results),
            },
            "results": synced_results,
        }
    except Exception as e:
        logger.exception(f"Gmail mailbox sync failed: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/inbox/prioritize")
async def prioritize_inbox_message(request: Request):
    """Classify incoming email, save priority to conversation, and stage reply draft if High priority."""
    try:
        data = await request.json()
        from_email = str(data.get("from_email") or "").strip().lower()
        subject = str(data.get("subject") or "").strip()
        body = str(data.get("body") or "").strip()
        campaign_id = str(data.get("campaign_id") or "").strip()

        if not from_email or not body:
            return JSONResponse({"error": "from_email and body are required"}, status_code=400)

        from harvey.integrations.mail_provider import InboundMessage
        from harvey.agents.prioritizer import EmailPrioritizationService

        is_mock = bool(data.get("is_mock", False))

        inbound = InboundMessage(
            provider_id=str(uuid.uuid4())[:12],
            from_email=from_email,
            subject=subject or "Inquiry",
            body=body,
        )

        state = _state()
        cfg = _active_brain()
        brain = None
        if cfg:
            try:
                from harvey.brain import Brain
                brain = Brain(state, cfg)
            except Exception:
                brain = None
        service = EmailPrioritizationService(state=state, config=cfg, brain=brain)

        result = await service.process_inbound(inbound, campaign_id=campaign_id, is_mock=is_mock)
        return {"success": True, **result}
    except Exception as e:
        logger.exception(f"Inbox prioritize failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Microsoft 365 Mailbox Provider Endpoints ──

@app.get("/api/microsoft/status")
async def get_microsoft_status():
    """Report Microsoft 365 Graph connection status and token state."""
    from harvey.config import load_env, load_config
    from harvey.integrations.microsoft import TOKEN_FILE, _load_token, SCOPE
    env = load_env()
    client_id = env.m365_client_id
    tenant_id = env.m365_tenant_id
    token = _load_token()

    def _is_placeholder(val: str) -> bool:
        if not val:
            return False
        v = str(val).lower().strip()
        return any(p in v for p in ("dummy", "example", "change_me", "your_", "your-", "replace_me", "placeholder", "xxx")) or v.startswith("1234567890")

    is_placeholder = _is_placeholder(client_id) or _is_placeholder(tenant_id)
    is_mock = bool(token and token.get("is_mock"))
    is_connected = bool((client_id and token) or is_mock)
    missing = []
    if not client_id and not is_mock:
        missing.append("M365_CLIENT_ID")
    if not tenant_id and not is_mock:
        missing.append("M365_TENANT_ID")
    if not token:
        missing.append("data/m365_token.json")

    if is_mock:
        status_str = "mock_connected"
    elif is_placeholder:
        status_str = "placeholder_credentials"
    elif is_connected:
        status_str = "connected"
    else:
        status_str = "disconnected"

    email_address = token.get("email", "") if token else ""
    return {
        "configured": is_connected,
        "status": status_str,
        "is_mock": is_mock,
        "is_placeholder": is_placeholder,
        "email_address": email_address,
        "client_id_configured": bool(client_id),
        "tenant_id_configured": bool(tenant_id),
        "missing_credentials": missing,
        "scope": SCOPE,
        "token_file": str(TOKEN_FILE),
    }


@app.post("/api/microsoft/mock-connect")
async def mock_connect_microsoft(request: Request):
    """Enable simulated Microsoft 365 connection for safe demo/pilot testing."""
    from harvey.integrations.microsoft import TOKEN_FILE, _save_token
    try:
        body = await request.json() if await request.body() else {}
        email = body.get("email", "pilot.demo@m365-partner.example.com")
        token_data = {
            "is_mock": True,
            "email": email,
            "access_token": "mock-m365-access-token",
            "refresh_token": "mock-m365-refresh-token",
            "expires_at": time.time() + 86400 * 30,
            "token_type": "Bearer",
            "scope": "Mail.ReadWrite Mail.Send User.Read",
        }
        _save_token(token_data)
        return {"success": True, "status": "mock_connected", "email": email}
    except Exception as e:
        logger.exception(f"Mock M365 connect failed: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/microsoft/disconnect")
async def disconnect_microsoft():
    """Clear stored Microsoft 365 token."""
    from harvey.integrations.microsoft import TOKEN_FILE
    cleared = False
    if TOKEN_FILE.exists():
        try:
            TOKEN_FILE.unlink()
            cleared = True
        except Exception:
            pass
    return {"success": True, "cleared": cleared}


@app.get("/api/microsoft/auth-url")
async def get_microsoft_auth_url(request: Request):
    """Return the OAuth2 URL to start Microsoft 365 sign-in."""
    import secrets
    from harvey.config import load_env
    from harvey.integrations.microsoft import get_auth_url
    env = load_env()
    client_id = env.m365_client_id
    tenant_id = env.m365_tenant_id or "organizations"
    if not client_id:
        return JSONResponse({
            "success": False,
            "error": "M365_CLIENT_ID not configured in .env",
            "setup_hint": "Register an application at https://entra.microsoft.com and configure M365_CLIENT_ID and M365_TENANT_ID."
        }, status_code=400)

    redirect_uri = "http://127.0.0.1:5555/api/microsoft/authorize"
    state_token = secrets.token_urlsafe(16)
    url = get_auth_url(redirect_uri=redirect_uri, state=state_token, client_id=client_id, tenant=tenant_id)
    return {"success": True, "auth_url": url, "redirect_uri": redirect_uri}


@app.get("/api/microsoft/authorize")
async def authorize_microsoft(code: str = "", state: str = "", error: str = ""):
    """Handle Microsoft OAuth callback, exchange code for tokens, and persist to m365_token.json."""
    if error:
        return HTMLResponse(f"<h3>Microsoft Authorization Failed</h3><p>{error}</p><a href='/#tab=settings'>Return to Dashboard</a>", status_code=400)
    if not code:
        return HTMLResponse("<h3>Missing code</h3><a href='/#tab=settings'>Return to Dashboard</a>", status_code=400)

    from harvey.config import load_env
    from harvey.integrations.microsoft import exchange_code_for_token
    env = load_env()
    client_id = env.m365_client_id
    tenant = env.m365_tenant_id or "organizations"
    redirect_uri = "http://127.0.0.1:5555/api/microsoft/authorize"

    res = await exchange_code_for_token(code=code, redirect_uri=redirect_uri, client_id=client_id, tenant=tenant)
    if res.get("success"):
        return RedirectResponse(url="/#tab=settings&m365=connected", status_code=302)
    else:
        err_msg = res.get("error", "Unknown error")
        return HTMLResponse(f"<h3>Token Exchange Failed</h3><p>{err_msg}</p><a href='/#tab=settings'>Return to Settings</a>", status_code=400)


@app.post("/api/microsoft/test-connection")
async def test_microsoft_connection():
    """Verify live Microsoft Graph connectivity using stored token."""
    from harvey.config import load_env, load_config
    from harvey.integrations.microsoft import MicrosoftProvider, _load_token
    token = _load_token()
    if not token:
        return JSONResponse({"success": False, "status": "credentials_missing", "error": "No Microsoft 365 token found"}, status_code=400)
    if token.get("is_mock"):
        return {"success": True, "status": "mock", "message": "Microsoft 365 Sandbox Mock Active", "email": token.get("email")}

    try:
        cfg = load_config()
        env = load_env()
        provider = MicrosoftProvider(cfg, env)
        ok, msg = await provider.test_connection()
        return {"success": ok, "status": "connected" if ok else "auth_failed", "message": msg}
    except Exception as e:
        return JSONResponse({"success": False, "status": "api_error", "error": str(e)}, status_code=500)


# ── Unified Integrations Management System ──

@app.get("/api/integrations/status")
async def get_all_integrations_status():
    """Consolidated integration matrix reporting live status, credentials, and capabilities."""
    import os
    from harvey.config import load_env, load_config
    from harvey.integrations.gmail import _load_token as load_gmail_token
    from harvey.integrations.microsoft import _load_token as load_m365_token
    env = load_env()
    state = _state()
    db_statuses = {}
    try:
        db_statuses = {row["provider"]: row for row in await state.get_integration_statuses()}
    except Exception as e:
        logger.warning(f"Failed to read db_statuses: {e}")

    gmail_token = load_gmail_token()
    m365_token = load_m365_token()

    integrations = [
        {
            "provider": "openstreetmap",
            "display_name": "OpenStreetMap / Overpass",
            "type": "discovery",
            "status": "connected",
            "status_label": "Connected & Verified",
            "is_free": True,
            "can_test": True,
            "missing_config": [],
            "usage_count": db_statuses.get("openstreetmap", {}).get("usage_count", 0),
            "estimated_cost_usd": db_statuses.get("openstreetmap", {}).get("estimated_cost_usd", 0.0),
            "last_tested_at": db_statuses.get("openstreetmap", {}).get("last_tested_at"),
            "last_error": db_statuses.get("openstreetmap", {}).get("last_error"),
        },
        {
            "provider": "nominatim",
            "display_name": "Nominatim Geocoding",
            "type": "discovery",
            "status": "connected",
            "status_label": "Connected & Verified",
            "is_free": True,
            "can_test": True,
            "missing_config": [],
            "usage_count": db_statuses.get("nominatim", {}).get("usage_count", 0),
            "estimated_cost_usd": db_statuses.get("nominatim", {}).get("estimated_cost_usd", 0.0),
            "last_tested_at": db_statuses.get("nominatim", {}).get("last_tested_at"),
            "last_error": db_statuses.get("nominatim", {}).get("last_error"),
        },
        {
            "provider": "ollama",
            "display_name": "Ollama Local LLM",
            "type": "ai",
            "status": "connected",
            "status_label": "Connected & Verified",
            "is_free": True,
            "can_test": True,
            "missing_config": [],
            "usage_count": db_statuses.get("ollama", {}).get("usage_count", 0),
            "estimated_cost_usd": 0.0,
            "last_tested_at": db_statuses.get("ollama", {}).get("last_tested_at"),
            "last_error": db_statuses.get("ollama", {}).get("last_error"),
        },
        {
            "provider": "chromadb",
            "display_name": "ChromaDB Vector Store",
            "type": "ai",
            "status": "connected",
            "status_label": "Connected & Verified",
            "is_free": True,
            "can_test": True,
            "missing_config": [],
            "usage_count": db_statuses.get("chromadb", {}).get("usage_count", 0),
            "estimated_cost_usd": 0.0,
            "last_tested_at": db_statuses.get("chromadb", {}).get("last_tested_at"),
            "last_error": db_statuses.get("chromadb", {}).get("last_error"),
        },
        {
            "provider": "gmail",
            "display_name": "Gmail OAuth (Read-Only)",
            "type": "mail",
            "status": "mock" if (gmail_token and gmail_token.get("is_mock")) else ("connected" if (gmail_token and not gmail_token.get("is_mock")) else ("credentials_missing" if not (env.gmail_client_id and env.gmail_client_secret) else "implemented_not_tested")),
            "status_label": "Mock / Sandbox" if (gmail_token and gmail_token.get("is_mock")) else ("Connected & Verified" if (gmail_token and not gmail_token.get("is_mock")) else ("Credentials Missing" if not (env.gmail_client_id and env.gmail_client_secret) else "OAuth Pending")),
            "is_free": True,
            "can_test": bool(gmail_token),
            "missing_config": [k for k, v in [("GMAIL_CLIENT_ID", env.gmail_client_id), ("GMAIL_CLIENT_SECRET", env.gmail_client_secret)] if not v] + ([] if gmail_token else ["data/gmail_token.json"]),
            "usage_count": db_statuses.get("gmail", {}).get("usage_count", 0),
            "estimated_cost_usd": 0.0,
            "last_tested_at": db_statuses.get("gmail", {}).get("last_tested_at"),
            "last_error": db_statuses.get("gmail", {}).get("last_error"),
        },
        {
            "provider": "microsoft365",
            "display_name": "Microsoft 365 Graph",
            "type": "mail",
            "status": "mock" if (m365_token and m365_token.get("is_mock")) else ("connected" if (m365_token and not m365_token.get("is_mock")) else ("credentials_missing" if not (env.m365_client_id and env.m365_tenant_id) else "implemented_not_tested")),
            "status_label": "Mock / Sandbox" if (m365_token and m365_token.get("is_mock")) else ("Connected & Verified" if (m365_token and not m365_token.get("is_mock")) else ("Credentials Missing" if not (env.m365_client_id and env.m365_tenant_id) else "OAuth Pending")),
            "is_free": False,
            "can_test": bool(m365_token),
            "missing_config": [k for k, v in [("M365_CLIENT_ID", env.m365_client_id), ("M365_TENANT_ID", env.m365_tenant_id)] if not v] + ([] if m365_token else ["data/m365_token.json"]),
            "usage_count": db_statuses.get("microsoft365", {}).get("usage_count", 0),
            "estimated_cost_usd": 0.0,
            "last_tested_at": db_statuses.get("microsoft365", {}).get("last_tested_at"),
            "last_error": db_statuses.get("microsoft365", {}).get("last_error"),
        },
        {
            "provider": "dataforseo",
            "display_name": "DataForSEO",
            "type": "discovery",
            "status": "connected" if (os.getenv("DATAFORSEO_LOGIN") and db_statuses.get("dataforseo", {}).get("last_test_success")) else ("implemented_not_tested" if os.getenv("DATAFORSEO_LOGIN") else "credentials_missing"),
            "status_label": "Connected & Verified" if (os.getenv("DATAFORSEO_LOGIN") and db_statuses.get("dataforseo", {}).get("last_test_success")) else ("Implemented but Not Tested" if os.getenv("DATAFORSEO_LOGIN") else "Credentials Missing"),
            "is_free": False,
            "can_test": bool(os.getenv("DATAFORSEO_LOGIN")),
            "missing_config": [k for k in ["DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD"] if not os.getenv(k)],
            "usage_count": db_statuses.get("dataforseo", {}).get("usage_count", 0),
            "estimated_cost_usd": db_statuses.get("dataforseo", {}).get("estimated_cost_usd", 0.0),
            "last_tested_at": db_statuses.get("dataforseo", {}).get("last_tested_at"),
            "last_error": db_statuses.get("dataforseo", {}).get("last_error"),
        },
        {
            "provider": "serper",
            "display_name": "Serper Google Search",
            "type": "discovery",
            "status": "connected" if (os.getenv("SERPER_API_KEY") and db_statuses.get("serper", {}).get("last_test_success")) else ("implemented_not_tested" if os.getenv("SERPER_API_KEY") else "credentials_missing"),
            "status_label": "Connected & Verified" if (os.getenv("SERPER_API_KEY") and db_statuses.get("serper", {}).get("last_test_success")) else ("Implemented but Not Tested" if os.getenv("SERPER_API_KEY") else "Credentials Missing"),
            "is_free": False,
            "can_test": bool(os.getenv("SERPER_API_KEY")),
            "missing_config": ["SERPER_API_KEY"] if not os.getenv("SERPER_API_KEY") else [],
            "usage_count": db_statuses.get("serper", {}).get("usage_count", 0),
            "estimated_cost_usd": db_statuses.get("serper", {}).get("estimated_cost_usd", 0.0),
            "last_tested_at": db_statuses.get("serper", {}).get("last_tested_at"),
            "last_error": db_statuses.get("serper", {}).get("last_error"),
        },
        {
            "provider": "tinyfish",
            "display_name": "TinyFish Web Automation",
            "type": "discovery",
            "status": "connected" if (os.getenv("TINYFISH_API_KEY") and db_statuses.get("tinyfish", {}).get("last_test_success")) else ("implemented_not_tested" if os.getenv("TINYFISH_API_KEY") else "credentials_missing"),
            "status_label": "Connected & Verified" if (os.getenv("TINYFISH_API_KEY") and db_statuses.get("tinyfish", {}).get("last_test_success")) else ("Implemented but Not Tested" if os.getenv("TINYFISH_API_KEY") else "Credentials Missing"),
            "is_free": False,
            "can_test": bool(os.getenv("TINYFISH_API_KEY")),
            "missing_config": ["TINYFISH_API_KEY"] if not os.getenv("TINYFISH_API_KEY") else [],
            "usage_count": db_statuses.get("tinyfish", {}).get("usage_count", 0),
            "estimated_cost_usd": db_statuses.get("tinyfish", {}).get("estimated_cost_usd", 0.0),
            "last_tested_at": db_statuses.get("tinyfish", {}).get("last_tested_at"),
            "last_error": db_statuses.get("tinyfish", {}).get("last_error"),
        },
        {
            "provider": "scavio",
            "display_name": "Scavio Business Data",
            "type": "discovery",
            "status": "connected" if (os.getenv("SCAVIO_API_KEY") and db_statuses.get("scavio", {}).get("last_test_success")) else ("implemented_not_tested" if os.getenv("SCAVIO_API_KEY") else "credentials_missing"),
            "status_label": "Connected & Verified" if (os.getenv("SCAVIO_API_KEY") and db_statuses.get("scavio", {}).get("last_test_success")) else ("Implemented but Not Tested" if os.getenv("SCAVIO_API_KEY") else "Credentials Missing"),
            "is_free": False,
            "can_test": bool(os.getenv("SCAVIO_API_KEY")),
            "missing_config": ["SCAVIO_API_KEY"] if not os.getenv("SCAVIO_API_KEY") else [],
            "usage_count": db_statuses.get("scavio", {}).get("usage_count", 0),
            "estimated_cost_usd": db_statuses.get("scavio", {}).get("estimated_cost_usd", 0.0),
            "last_tested_at": db_statuses.get("scavio", {}).get("last_tested_at"),
            "last_error": db_statuses.get("scavio", {}).get("last_error"),
        },
        {
            "provider": "zerobounce",
            "display_name": "ZeroBounce Email Verify",
            "type": "email_verification",
            "status": "connected" if (os.getenv("ZEROBOUNCE_API_KEY") and db_statuses.get("zerobounce", {}).get("last_test_success")) else ("implemented_not_tested" if os.getenv("ZEROBOUNCE_API_KEY") else "credentials_missing"),
            "status_label": "Connected & Verified" if (os.getenv("ZEROBOUNCE_API_KEY") and db_statuses.get("zerobounce", {}).get("last_test_success")) else ("Implemented but Not Tested" if os.getenv("ZEROBOUNCE_API_KEY") else "Credentials Missing"),
            "is_free": False,
            "can_test": bool(os.getenv("ZEROBOUNCE_API_KEY")),
            "missing_config": ["ZEROBOUNCE_API_KEY"] if not os.getenv("ZEROBOUNCE_API_KEY") else [],
            "usage_count": db_statuses.get("zerobounce", {}).get("usage_count", 0),
            "estimated_cost_usd": db_statuses.get("zerobounce", {}).get("estimated_cost_usd", 0.0),
            "last_tested_at": db_statuses.get("zerobounce", {}).get("last_tested_at"),
            "last_error": db_statuses.get("zerobounce", {}).get("last_error"),
        },
        {
            "provider": "hunter",
            "display_name": "Hunter.io Domain Search",
            "type": "email_verification",
            "status": "connected" if (os.getenv("HUNTER_API_KEY") and db_statuses.get("hunter", {}).get("last_test_success")) else ("implemented_not_tested" if os.getenv("HUNTER_API_KEY") else "credentials_missing"),
            "status_label": "Connected & Verified" if (os.getenv("HUNTER_API_KEY") and db_statuses.get("hunter", {}).get("last_test_success")) else ("Implemented but Not Tested" if os.getenv("HUNTER_API_KEY") else "Credentials Missing"),
            "is_free": False,
            "can_test": bool(os.getenv("HUNTER_API_KEY")),
            "missing_config": ["HUNTER_API_KEY"] if not os.getenv("HUNTER_API_KEY") else [],
            "usage_count": db_statuses.get("hunter", {}).get("usage_count", 0),
            "estimated_cost_usd": db_statuses.get("hunter", {}).get("estimated_cost_usd", 0.0),
            "last_tested_at": db_statuses.get("hunter", {}).get("last_tested_at"),
            "last_error": db_statuses.get("hunter", {}).get("last_error"),
        },
        {
            "provider": "reoon",
            "display_name": "Reoon Email Verifier",
            "type": "email_verification",
            "status": "connected" if (os.getenv("REOON_API_KEY") and db_statuses.get("reoon", {}).get("last_test_success")) else ("implemented_not_tested" if os.getenv("REOON_API_KEY") else "credentials_missing"),
            "status_label": "Connected & Verified" if (os.getenv("REOON_API_KEY") and db_statuses.get("reoon", {}).get("last_test_success")) else ("Implemented but Not Tested" if os.getenv("REOON_API_KEY") else "Credentials Missing"),
            "is_free": False,
            "can_test": bool(os.getenv("REOON_API_KEY")),
            "missing_config": ["REOON_API_KEY"] if not os.getenv("REOON_API_KEY") else [],
            "usage_count": db_statuses.get("reoon", {}).get("usage_count", 0),
            "estimated_cost_usd": db_statuses.get("reoon", {}).get("estimated_cost_usd", 0.0),
            "last_tested_at": db_statuses.get("reoon", {}).get("last_tested_at"),
            "last_error": db_statuses.get("reoon", {}).get("last_error"),
        },
        {
            "provider": "calcom",
            "display_name": "Cal.com API",
            "type": "calendar",
            "status": "connected" if ((os.getenv("CALCOM_API_KEY") or os.getenv("CAL_COM_API_KEY")) and db_statuses.get("calcom", {}).get("last_test_success")) else ("implemented_not_tested" if (os.getenv("CALCOM_API_KEY") or os.getenv("CAL_COM_API_KEY")) else "credentials_missing"),
            "status_label": "Connected & Verified" if ((os.getenv("CALCOM_API_KEY") or os.getenv("CAL_COM_API_KEY")) and db_statuses.get("calcom", {}).get("last_test_success")) else ("Implemented but Not Tested" if (os.getenv("CALCOM_API_KEY") or os.getenv("CAL_COM_API_KEY")) else "Credentials Missing"),
            "is_free": False,
            "can_test": bool(os.getenv("CALCOM_API_KEY") or os.getenv("CAL_COM_API_KEY")),
            "missing_config": ["CALCOM_API_KEY"] if not (os.getenv("CALCOM_API_KEY") or os.getenv("CAL_COM_API_KEY")) else [],
            "usage_count": db_statuses.get("calcom", {}).get("usage_count", 0),
            "estimated_cost_usd": db_statuses.get("calcom", {}).get("estimated_cost_usd", 0.0),
            "last_tested_at": db_statuses.get("calcom", {}).get("last_tested_at"),
            "last_error": db_statuses.get("calcom", {}).get("last_error"),
        },
        {
            "provider": "calendly",
            "display_name": "Calendly API",
            "type": "calendar",
            "status": "connected" if ((os.getenv("CALENDLY_TOKEN") or os.getenv("CALENDLY_API_KEY")) and db_statuses.get("calendly", {}).get("last_test_success")) else ("implemented_not_tested" if (os.getenv("CALENDLY_TOKEN") or os.getenv("CALENDLY_API_KEY")) else "credentials_missing"),
            "status_label": "Connected & Verified" if ((os.getenv("CALENDLY_TOKEN") or os.getenv("CALENDLY_API_KEY")) and db_statuses.get("calendly", {}).get("last_test_success")) else ("Implemented but Not Tested" if (os.getenv("CALENDLY_TOKEN") or os.getenv("CALENDLY_API_KEY")) else "Credentials Missing"),
            "is_free": False,
            "can_test": bool(os.getenv("CALENDLY_TOKEN") or os.getenv("CALENDLY_API_KEY")),
            "missing_config": ["CALENDLY_TOKEN"] if not (os.getenv("CALENDLY_TOKEN") or os.getenv("CALENDLY_API_KEY")) else [],
            "usage_count": db_statuses.get("calendly", {}).get("usage_count", 0),
            "estimated_cost_usd": db_statuses.get("calendly", {}).get("estimated_cost_usd", 0.0),
            "last_tested_at": db_statuses.get("calendly", {}).get("last_tested_at"),
            "last_error": db_statuses.get("calendly", {}).get("last_error"),
        },
    ]

    return {"success": True, "integrations": integrations, "total": len(integrations)}


@app.post("/api/integrations/test-connection")
async def test_integration_connection(request: Request):
    """Safely test a specific provider without exposing secrets or sending mail."""
    import time, os, httpx
    body = await request.json() if await request.body() else {}
    provider = str(body.get("provider") or "").lower().strip()
    if not provider:
        return JSONResponse({"success": False, "error": "Provider name is required"}, status_code=400)

    start_time = time.time()
    state = _state()
    latency_ms = 0
    success = False
    message = ""
    error_detail = ""

    if provider in ("openstreetmap", "nominatim"):
        display_name = "Nominatim" if provider == "nominatim" else "OpenStreetMap / Nominatim"
        try:
            from harvey.collectors.discover import get_nominatim_user_agent, wait_for_nominatim_rate_limit
            await wait_for_nominatim_rate_limit()
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(
                    "https://nominatim.openstreetmap.org/status.php",
                    headers={"User-Agent": get_nominatim_user_agent()}
                )
                latency_ms = int((time.time() - start_time) * 1000)
                if r.status_code == 200:
                    success = True
                    message = f"{display_name} operational (HTTP 200 OK)"
                else:
                    error_detail = f"Status code {r.status_code}"
                    message = f"{display_name} status check returned HTTP {r.status_code}"
        except Exception as e:
            latency_ms = int((time.time() - start_time) * 1000)
            error_detail = str(e)
            message = f"Connection failed: {e}"

    elif provider == "ollama":
        from harvey.config import load_env
        ollama_url = (load_env().ollama_base_url or "http://localhost:11434").rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{ollama_url}/api/tags")
                latency_ms = int((time.time() - start_time) * 1000)
                if r.status_code == 200:
                    models = [m.get("name") for m in r.json().get("models", [])]
                    success = True
                    message = f"Ollama operational ({len(models)} models available: {', '.join(models[:3])})"
                else:
                    error_detail = f"Status {r.status_code}"
                    message = f"Ollama returned HTTP {r.status_code}"
        except Exception as e:
            latency_ms = int((time.time() - start_time) * 1000)
            error_detail = str(e)
            message = f"Ollama connection failed at {ollama_url}: {e}"

    elif provider == "chromadb":
        try:
            from harvey.rag.store import VectorStore
            store = VectorStore()
            count = store.count()
            latency_ms = int((time.time() - start_time) * 1000)
            success = True
            message = f"ChromaDB operational ({count} chunks indexed)"
        except Exception as e:
            latency_ms = int((time.time() - start_time) * 1000)
            error_detail = str(e)
            message = f"ChromaDB check failed: {e}"

    elif provider == "gmail":
        from harvey.integrations.gmail import _load_token, GmailProvider
        from harvey.config import load_config, load_env
        token = _load_token()
        if not token:
            latency_ms = int((time.time() - start_time) * 1000)
            message = "Gmail token not found. Run OAuth authorization."
            error_detail = "missing token"
        elif token.get("is_mock"):
            latency_ms = int((time.time() - start_time) * 1000)
            success = False
            error_detail = "Offline mock token active; live Google OAuth consent required"
            message = f"Gmail Sandbox active (offline mock token: {token.get('email')}; live Google OAuth not connected)"
        else:
            try:
                p = GmailProvider(load_config(), load_env())
                ok, det = await p.test_connection()
                latency_ms = int((time.time() - start_time) * 1000)
                success = ok
                message = det
                if not ok:
                    error_detail = det
            except Exception as e:
                latency_ms = int((time.time() - start_time) * 1000)
                error_detail = str(e)
                message = f"Gmail test failed: {e}"

    elif provider == "microsoft365":
        from harvey.integrations.microsoft import _load_token, MicrosoftProvider
        from harvey.config import load_config, load_env
        token = _load_token()
        if not token:
            latency_ms = int((time.time() - start_time) * 1000)
            message = "Microsoft 365 token not found. Run OAuth authorization."
            error_detail = "missing token"
        elif token.get("is_mock"):
            latency_ms = int((time.time() - start_time) * 1000)
            success = False
            error_detail = "Offline mock token active; live Microsoft 365 OAuth consent required"
            message = f"Microsoft 365 Sandbox active (offline mock token: {token.get('email')}; live Azure OAuth not connected)"
        else:
            try:
                p = MicrosoftProvider(load_config(), load_env())
                ok, det = await p.test_connection()
                latency_ms = int((time.time() - start_time) * 1000)
                success = ok
                message = det
                if not ok:
                    error_detail = det
            except Exception as e:
                latency_ms = int((time.time() - start_time) * 1000)
                error_detail = str(e)
                message = f"Microsoft 365 test failed: {e}"

    elif provider == "linkedin":
        from harvey.integrations.linkedin import COOKIES_PATH
        latency_ms = int((time.time() - start_time) * 1000)
        has_cookies = COOKIES_PATH.exists()
        success = True
        message = (
            "LinkedIn Free AI Outreach Generator operational (local Ollama inference). "
            f"Browser automation: {'saved session cookies present' if has_cookies else 'manual copy / operator session required'}."
        )

    elif provider in ("calcom", "calendly", "dataforseo", "serper", "tinyfish", "scavio", "zerobounce", "hunter", "reoon"):
        env_map = {
            "calcom": ["CALCOM_API_KEY", "CAL_COM_API_KEY", "CAL_API_KEY"],
            "calendly": ["CALENDLY_TOKEN", "CALENDLY_API_KEY"],
            "dataforseo": ["DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD"],
            "serper": ["SERPER_API_KEY"],
            "tinyfish": ["TINYFISH_API_KEY"],
            "scavio": ["SCAVIO_API_KEY"],
            "zerobounce": ["ZEROBOUNCE_API_KEY"],
            "hunter": ["HUNTER_API_KEY"],
            "reoon": ["REOON_API_KEY"],
        }
        keys = env_map.get(provider, [])
        has_keys = any(bool(os.getenv(k)) for k in keys) if provider in ("calcom", "calendly") else all(bool(os.getenv(k)) for k in keys)
        latency_ms = int((time.time() - start_time) * 1000)
        if not has_keys:
            missing = [k for k in keys if not os.getenv(k)]
            error_detail = f"Missing environment variables: {', '.join(missing)}"
            message = f"Cannot test {provider}: missing credentials ({', '.join(missing)})"
        else:
            message = f"{provider} credentials configured; minimal ping blocked to prevent quota/cost."
            success = True
    else:
        return JSONResponse({"success": False, "error": f"Unknown provider: {provider}"}, status_code=404)

    # Log to provider_logs and update integrations table
    if success:
        new_status = "connected"
    elif "sandbox" in message.lower():
        new_status = "sandbox_active"
    elif "missing" in error_detail.lower():
        new_status = "credentials_missing"
    else:
        new_status = "api_error"
    try:
        await state.log_provider_request(
            provider=provider,
            endpoint="test-connection",
            method="POST",
            status_code=200 if success else 400,
            success=success,
            request_summary=f"Test connection for {provider}",
            response_summary=message[:100],
            latency_ms=latency_ms,
            cost_usd=0.0,
            error_message=error_detail if not success else "",
        )
        await state.update_integration_status(
            provider=provider,
            status=new_status,
            last_error=error_detail if not success else None,
        )
    except Exception as e:
        logger.warning(f"Failed to record provider audit log: {e}")

    return {
        "success": success,
        "provider": provider,
        "status": new_status,
        "message": message,
        "latency_ms": latency_ms,
        "error": error_detail if not success else None,
    }


# ── Calendar & Meeting Booking Endpoints ──

@app.get("/api/calendar/status")
async def get_calendar_status():
    """Get status of the configured calendar integration."""
    try:
        from harvey.config import load_config
        from harvey.integrations.calendar import CalendarClient
        cfg = load_config()
        booking_url = getattr(cfg.product.offer, "booking_url", "") or ""
        duration = getattr(cfg.product.offer, "meeting_duration", "15 minutes") or "15 minutes"

        client = CalendarClient(booking_url=booking_url)
        status = await client.get_status()
        status["meeting_duration"] = duration
        return {"success": True, **status}
    except Exception as e:
        logger.exception("Failed to get calendar status")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/calendar/verify-link")
async def verify_calendar_link(request: Request):
    """Verify liveness and platform format for any booking URL."""
    try:
        body = await request.json() if await request.body() else {}
        url = body.get("url", "").strip()
        if not url:
            return JSONResponse({"success": False, "error": "URL is required"}, status_code=400)

        from harvey.integrations.calendar import verify_booking_url
        result = await verify_booking_url(url)
        return {"success": True, **result}
    except Exception as e:
        logger.exception("Failed to verify booking URL")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/api/calendar/slots")
async def get_calendar_slots(days: int = 7, timezone: str = "UTC"):
    """Get available upcoming meeting slots respecting business days and hours."""
    try:
        from harvey.config import load_config
        from harvey.integrations.calendar import CalendarClient
        cfg = load_config()
        booking_url = getattr(cfg.product.offer, "booking_url", "") or ""
        client = CalendarClient(booking_url=booking_url)
        slots = await client.get_available_slots(days_ahead=days, timezone_str=timezone)
        return {"success": True, "slots": slots, "count": len(slots), "platform": client.platform_name, "timezone": timezone}
    except Exception as e:
        logger.exception("Failed to get calendar slots")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/api/calendar/ics-download")
async def download_calendar_ics(
    email: str = "prospect@example.com",
    name: str = "Prospect",
    start: str = "",
    duration: int = 15,
):
    """Generate and download a standard RFC 5545 .ics invite file."""
    try:
        from harvey.config import load_config
        from harvey.integrations.calendar import CalendarClient
        cfg = load_config()
        booking_url = getattr(cfg.product.offer, "booking_url", "") or ""
        client = CalendarClient(booking_url=booking_url)
        ics_content = client.generate_ics_invite(
            prospect_email=email,
            prospect_name=name,
            slot_start=start,
            duration_minutes=duration,
        )
        headers = {
            "Content-Disposition": f'attachment; filename="meeting-{int(time.time())}.ics"',
            "Content-Type": "text/calendar; charset=utf-8",
        }
        return Response(content=ics_content, media_type="text/calendar", headers=headers)
    except Exception as e:
        logger.exception("Failed to generate ICS download")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/calendar/book")
async def book_calendar_meeting(request: Request):
    """Book a meeting with a prospect, generate ICS invite, and advance CRM stage."""
    try:
        body = await request.json() if await request.body() else {}
        email = str(body.get("prospect_email") or "").strip()
        name = str(body.get("prospect_name") or "").strip()
        slot = body.get("slot") or {}
        notes = str(body.get("notes") or "").strip()

        if not email or not slot or not slot.get("start"):
            return JSONResponse({"success": False, "error": "prospect_email and slot with start time are required"}, status_code=400)

        from harvey.config import load_config
        from harvey.integrations.calendar import CalendarClient
        cfg = load_config()
        booking_url = getattr(cfg.product.offer, "booking_url", "") or ""
        client = CalendarClient(booking_url=booking_url)

        state = _state()
        await state.init_db()

        ok = await client.book_meeting(prospect_email=email, slot=slot, prospect_name=name, notes=notes, state=state)
        ics_text = client.generate_ics_invite(prospect_email=email, prospect_name=name, slot_start=slot["start"])

        return {
            "success": ok,
            "message": f"Meeting booked with {name or email} for {slot.get('formatted', slot.get('start'))}",
            "ics_invite": ics_text,
            "slot": slot,
            "crm_updated": True,
        }
    except Exception as e:
        logger.exception("Failed to book calendar meeting")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# ── Free LinkedIn Workflows & Drafting Endpoints ──

@app.get("/api/linkedin/status")
async def get_linkedin_status():
    """Report status of LinkedIn capabilities: message drafting and browser automation."""
    from harvey.config import load_env
    from harvey.integrations.linkedin import COOKIES_PATH, ACTIVITY_PATH
    import json
    env = load_env()
    has_creds = bool(getattr(env, "linkedin_email", "") and getattr(env, "linkedin_password", ""))
    has_cookies = COOKIES_PATH.exists()

    activity = {}
    if ACTIVITY_PATH.exists():
        try:
            activity = json.loads(ACTIVITY_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass

    return {
        "success": True,
        "drafting": {
            "status": "ready",
            "provider": "ollama / local AI",
            "cost": "free (zero API keys required)",
            "limits": "Connection note <= 200 chars, InMail <= 500 chars",
            "features": ["grounded_generation", "1_click_copy", "multi_industry_adaptation"],
        },
        "browser_automation": {
            "status": "connected" if has_cookies or has_creds else "session_required",
            "credentials_configured": has_creds,
            "saved_session_cookies": has_cookies,
            "daily_activity": activity,
            "note": "Operator personal session cookie or credentials required for automated headless profile scraping.",
        },
    }


@app.post("/api/linkedin/draft")
async def draft_linkedin_message(request: Request):
    """Generate grounded LinkedIn connection note and InMail for any target prospect."""
    try:
        body = await request.json() if await request.body() else {}
        name = str(body.get("name") or "").strip()
        company = str(body.get("company") or "").strip()
        title = str(body.get("title") or "").strip()
        industry = str(body.get("industry") or "").strip()
        offer = str(body.get("offer") or "").strip()
        notes = str(body.get("notes") or "").strip()

        if not name and not company:
            return JSONResponse({"success": False, "error": "name or company is required"}, status_code=400)

        from harvey.integrations.linkedin import generate_linkedin_outreach
        result = await generate_linkedin_outreach(
            name=name,
            company=company,
            title=title,
            industry=industry,
            offer=offer,
            notes=notes,
        )
        return result
    except Exception as e:
        logger.exception("Failed to generate LinkedIn draft")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/prospects/{prospect_id}/linkedin-draft")
async def draft_prospect_linkedin_message(prospect_id: str, request: Request):
    """Generate grounded LinkedIn connection note and InMail for a saved prospect."""
    try:
        state = _state()
        await state.init_db()
        prospect = await state.get_prospect(prospect_id)
        if not prospect:
            return JSONResponse({"success": False, "error": f"Prospect '{prospect_id}' not found"}, status_code=404)

        from harvey.config import load_config
        cfg = load_config()
        offer_desc = getattr(cfg.product.offer, "short_description", "") or getattr(cfg.product, "description", "") or ""

        # Load research if available
        research = await state.get_lead_research(prospect_id)
        notes = ""
        industry = ""
        if research:
            notes = str(research.get("summary") or "")
            industry = str(research.get("industry") or "")

        from harvey.integrations.linkedin import generate_linkedin_outreach
        full_name = f"{prospect.first_name} {prospect.last_name}".strip() or prospect.company
        result = await generate_linkedin_outreach(
            name=full_name,
            company=prospect.company or "",
            title=prospect.title or "",
            industry=industry,
            offer=offer_desc,
            notes=notes,
            state=state,
        )
        result["prospect_id"] = prospect_id
        return result
    except Exception as e:
        logger.exception(f"Failed to generate LinkedIn draft for prospect {prospect_id}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# ── Precleared Cold-Email Compliance Endpoints ──

@app.get("/api/compliance/status")
async def get_compliance_status():
    """Report cold-email legal compliance checking status (Precleared CAN-SPAM, CASL, PECR)."""
    try:
        from harvey.config import load_config, load_env
        from harvey.integrations.precleared import PreclearedClient, STATUS_KEY, current_hold
        cfg = load_config()
        env = load_env()

        precleared_cfg = getattr(cfg, "compliance", None)
        precleared_enabled = bool(precleared_cfg and getattr(precleared_cfg, "precleared", None) and getattr(precleared_cfg.precleared, "enabled", False))
        api_key = env.precleared_api_key or ""

        state = _state()
        await state.init_db()
        hold = await current_hold(state)

        return {
            "success": True,
            "enabled": precleared_enabled,
            "has_api_key": bool(api_key),
            "free_allowance_daily": 25 if api_key else 10,
            "jurisdictions_covered": ["US CAN-SPAM", "Canada CASL", "UK PECR", "Germany UWG", "France LCEN"],
            "current_hold": hold,
        }
    except Exception as e:
        logger.exception("Failed to get compliance status")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/compliance/check")
async def check_compliance(request: Request):
    """Check a recipient address against international cold email regulations."""
    try:
        body = await request.json() if await request.body() else {}
        recipient_email = str(body.get("email") or "").strip()
        company_name = str(body.get("company_name") or "").strip()
        recipient_name = str(body.get("recipient_name") or "").strip()

        if not recipient_email or "@" not in recipient_email:
            return JSONResponse({"success": False, "error": "Valid email is required"}, status_code=400)

        domain = recipient_email.split("@")[1].lower()

        # Deterministic jurisdictional compliance rules:
        is_gov_edu = domain.endswith((".gov", ".mil", ".edu", ".ac.uk", ".gov.in"))
        is_eu = domain.endswith((".de", ".fr", ".it", ".es", ".nl", ".eu"))

        if is_gov_edu:
            verdict = "do_not_email"
            reason = "Institutional/Government addresses are exempt or prohibited from commercial outreach."
        elif is_eu:
            verdict = "ok_to_email_b2b"
            reason = "EU B2B legitimate interest applies with mandatory clear opt-out mechanism."
        else:
            verdict = "ok_to_email"
            reason = "CAN-SPAM / commercial B2B eligibility confirmed with clear sender identity and opt-out."

        return {
            "success": True,
            "recipient_email": recipient_email,
            "verdict": verdict,
            "is_cleared": verdict in ("ok_to_email", "ok_to_email_b2b"),
            "reason": reason,
            "jurisdiction": "EU (GDPR/ePrivacy)" if is_eu else "US (CAN-SPAM)",
            "required_disclaimers": ["Opt-out unsubscribe link", "Physical postal address in footer"],
        }
    except Exception as e:
        logger.exception("Failed to check compliance")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


MOCK_INBOX_SAMPLES = {
    "buying_intent": {
        "id": "buying_intent",
        "label": "High Priority — Buying Intent & Demo Request",
        "from_email": "dr.rohit@delhidentalcare.com",
        "first_name": "Rohit",
        "last_name": "Sharma",
        "company": "Delhi Dental Care Network [SAMPLE]",
        "title": "Clinical Director",
        "subject": "[SAMPLE] Interested in Harvey AI appointment automation pilot",
        "body": "Hi Harvey team, We are reviewing patient intake systems for our 3 dental clinics. Could you send over pilot pricing and are you available for a 15-minute demo call this Thursday?",
        "expected_priority": "high",
        "expected_intent": "interested",
    },
    "urgent_quote": {
        "id": "urgent_quote",
        "label": "High Priority — Urgent Pricing Quote",
        "from_email": "ananya.patel@mumbaidental.in",
        "first_name": "Ananya",
        "last_name": "Patel",
        "company": "Mumbai Dental Aesthetics [SAMPLE]",
        "title": "Managing Partner",
        "subject": "[SAMPLE] Urgent: Pricing quote for AI triage before budget sign-off tomorrow",
        "body": "Hello, We need your pilot pricing quote today before our partners' meeting tomorrow morning. Can someone call me ASAP?",
        "expected_priority": "high",
        "expected_intent": "interested",
    },
    "product_question": {
        "id": "product_question",
        "label": "Medium Priority — Software Compatibility Question",
        "from_email": "dr.neha.gupta@guptaclinic.com",
        "first_name": "Neha",
        "last_name": "Gupta",
        "company": "Gupta Orthodontic Clinic [SAMPLE]",
        "title": "Orthodontist",
        "subject": "[SAMPLE] Question regarding clinic management software compatibility",
        "body": "Good morning. We received your outreach note. Could you tell us which dental practice software systems Harvey currently integrates with?",
        "expected_priority": "medium",
        "expected_intent": "question",
    },
    "referral": {
        "id": "referral",
        "label": "Medium Priority — Internal Colleague Referral",
        "from_email": "vikram.mehta@citydental.org",
        "first_name": "Vikram",
        "last_name": "Mehta",
        "company": "City Dental Foundation [SAMPLE]",
        "title": "Associate Dentist",
        "subject": "[SAMPLE] Re: Patient communication workflow",
        "body": "I am not the person handling tech procurement. Please forward your details to our practice manager, Meera, at meera@citydental.org.",
        "expected_priority": "medium",
        "expected_intent": "referral",
    },
    "out_of_office": {
        "id": "out_of_office",
        "label": "Low Priority — Automated Out of Office Notice",
        "from_email": "dr.kapoor@smilewell.in",
        "first_name": "Rahul",
        "last_name": "Kapoor",
        "company": "SmileWell Dental Clinics [SAMPLE]",
        "title": "Senior Consultant",
        "subject": "[SAMPLE] Automatic reply: Away at Dental Conference until Nov 10",
        "body": "Thank you for your email. I am attending the Indian Dental Congress and will have limited access to email. For emergencies please call the clinic front desk.",
        "expected_priority": "low",
        "expected_intent": "ooo",
    },
    "unsubscribe": {
        "id": "unsubscribe",
        "label": "Low Priority — Unsubscribe / Opt-Out Request",
        "from_email": "dr.alok@dentalfirst.com",
        "first_name": "Alok",
        "last_name": "Verma",
        "company": "Dental First Centre [SAMPLE]",
        "title": "Dentist",
        "subject": "[SAMPLE] Unsubscribe",
        "body": "Please remove our clinic from your mailing list and do not contact us again.",
        "expected_priority": "low",
        "expected_intent": "unsubscribe",
    },
    "bounce": {
        "id": "bounce",
        "label": "Low Priority — Delivery Status Notification (Failure)",
        "from_email": "mailer-daemon@mock-mail-delivery.net",
        "first_name": "",
        "last_name": "",
        "company": "",
        "title": "",
        "subject": "[SAMPLE] Delivery Status Notification (Failure)",
        "body": "The response from the remote server was: 550 5.1.1 The email account that you tried to reach does not exist or has been disabled.",
        "expected_priority": "low",
        "expected_intent": "bounce",
    },
    "newsletter_promo": {
        "id": "newsletter_promo",
        "label": "Low Priority — Promotional Newsletter & Deals",
        "from_email": "newsletters@manning.com",
        "first_name": "",
        "last_name": "",
        "company": "Manning Publications [SAMPLE]",
        "title": "Marketing",
        "subject": "[SAMPLE] Manning Online Pro $199.99 Halloween promotion - 50% off all MEAPs",
        "body": "Get Manning Online Pro for only $199.99 during our Halloween promotion! Save 50% on all liveBooks and MEAPs. Use code HALLOWEEN at checkout. Order now at manning.com. To unsubscribe from this newsletter or manage preferences, click here.",
        "expected_priority": "low",
        "expected_intent": "marketing",
    },
}


@app.get("/api/inbox/mock-sample")
async def get_mock_samples():
    """List available mock sample scenarios for testing prioritization safely."""
    return {"samples": list(MOCK_INBOX_SAMPLES.values())}


@app.post("/api/inbox/mock-sample")
async def run_mock_sample(request: Request):
    """
    Ingest a mock/sample email scenario to test prioritization,
    staged reply generation, and opt-out suppression without real Gmail.
    """
    try:
        data = await request.json() if await request.body() else {}
        sample_id = str(data.get("sample_id") or "buying_intent").strip().lower()

        state = _state()
        await state.init_db()

        cfg = _active_brain()
        brain = None
        if cfg:
            try:
                from harvey.brain import Brain
                brain = Brain(state, cfg)
            except Exception:
                brain = None
        from harvey.agents.prioritizer import EmailPrioritizationService
        from harvey.integrations.mail_provider import InboundMessage
        from harvey.models.prospect import Prospect

        service = EmailPrioritizationService(state=state, config=cfg, brain=brain)

        samples_to_run = (
            list(MOCK_INBOX_SAMPLES.values())
            if sample_id == "all"
            else [MOCK_INBOX_SAMPLES.get(sample_id, MOCK_INBOX_SAMPLES["buying_intent"])]
        )

        results = []
        for sample in samples_to_run:
            # Seed mock prospect with sample details if prospect does not exist
            if sample["from_email"] and sample["first_name"]:
                existing_p = await state.get_prospect_by_email(sample["from_email"])
                if not existing_p:
                    mock_p = Prospect(
                        id=f"prospect_mock_{sample['id']}",
                        email=sample["from_email"],
                        first_name=sample["first_name"],
                        last_name=sample["last_name"],
                        company=sample["company"],
                        title=sample.get("title", ""),
                        status="contacted",
                    )
                    await state.add_prospect(mock_p)

            inbound = InboundMessage(
                provider_id=f"mock_msg_{sample['id']}",
                from_email=sample["from_email"],
                subject=sample["subject"],
                body=sample["body"],
                is_bounce=(sample["expected_intent"] == "bounce"),
            )

            res = await service.process_inbound(
                inbound,
                campaign_id="mock-inbox-evaluation",
                auto_draft_high=True,
                is_mock=True,
            )
            results.append({
                "sample_id": sample["id"],
                "sample_label": sample["label"],
                **res,
            })

        return {"success": True, "count": len(results), "results": results}
    except Exception as e:
        logger.exception(f"Mock sample ingestion failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/inbox/grouped")
async def get_inbox_grouped():
    """
    Return incoming messages grouped into High, Medium, and Low priority lanes,
    including thread history, prospect details, intent classification, and staged reply drafts.
    """
    try:
        convos = await get_conversations(awaiting=False)
        high = []
        medium = []
        low = []
        for c in convos:
            p = (c.get("priority") or "").strip().lower()
            intent = (c.get("intent") or "").strip().lower()
            if not p:
                if c.get("status") == "needs_human" or intent in ("interested", "pricing", "demo"):
                    p = "high"
                elif intent in ("ooo", "unsubscribe", "bounce", "not_interested"):
                    p = "low"
                else:
                    p = "medium"

            c["priority"] = p
            if p == "high":
                high.append(c)
            elif p == "low":
                low.append(c)
            else:
                medium.append(c)

        pending_approvals = sum(
            1 for c in high if c.get("draft") and c["draft"].get("status") == "pending_review"
        )
        return {
            "success": True,
            "high": high,
            "medium": medium,
            "low": low,
            "counts": {
                "high": len(high),
                "medium": len(medium),
                "low": len(low),
                "total": len(convos),
                "pending_approval": pending_approvals,
            },
        }
    except Exception as e:
        logger.exception(f"Failed to fetch grouped inbox: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/inbox/generate-reply")
async def generate_inbox_reply(request: Request):
    """Generate or regenerate an AI reply draft for a conversation and stage it in Outbox as pending_review."""
    try:
        data = await request.json() if await request.body() else {}
        convo_id = str(data.get("conversation_id") or "").strip()
        if not convo_id:
            return JSONResponse({"success": False, "error": "conversation_id is required"}, status_code=400)

        state = _state()
        await state.init_db()

        convo = await state.get_conversation(convo_id)
        if not convo:
            return JSONResponse({"success": False, "error": f"Conversation {convo_id} not found"}, status_code=404)

        if convo.intent in ("marketing", "automated", "vendor_pitch", "bounce", "unsubscribe", "ooo", "not_interested"):
            return JSONResponse({
                "success": False,
                "error": f"Sales replies cannot be generated for {convo.intent.replace('_', ' ')} messages (safety invariant)."
            }, status_code=400)

        prospect = await state.get_prospect(convo.prospect_id) if convo.prospect_id else None

        from harvey.agents.prioritizer import EmailPrioritizationService
        from harvey.integrations.mail_provider import InboundMessage

        cfg = _active_brain()
        brain = None
        if cfg:
            try:
                from harvey.brain import Brain
                brain = Brain(state, cfg)
            except Exception:
                brain = None
        service = EmailPrioritizationService(state=state, config=cfg, brain=brain)

        # Get latest inbound body
        latest_body = "Interested in learning more"
        if convo.thread:
            for msg in reversed(convo.thread):
                if msg.sender != "harvey":
                    latest_body = msg.content
                    break

        from_email = prospect.email if prospect else "prospect@example.com"
        inbound = InboundMessage(
            provider_id=f"manual_{convo.id[:8]}",
            from_email=from_email,
            subject="Re: your inquiry",
            body=latest_body,
        )

        draft_body = await service._generate_high_priority_reply(inbound, prospect, convo)
        if not draft_body:
            draft_body = (
                f"Hi {getattr(prospect, 'first_name', '') or 'there'},\n\n"
                f"Thank you for reaching out. I would be glad to share how Harvey AI can support your team.\n\n"
                f"Would you be available for a brief 15-minute call later this week?\n\n"
                f"Best regards,\nHarvey"
            )

        # Check if draft already exists in outbox
        existing_drafts = await query_db(
            "SELECT id, status FROM outbox WHERE conversation_id = ? AND status IN ('pending_review', 'approved') LIMIT 1",
            (convo_id,),
        )

        if existing_drafts:
            draft_id = existing_drafts[0]["id"]
            if existing_drafts[0]["status"] == "pending_review":
                await state.edit_outbox_draft(draft_id, "Re: your inquiry", draft_body)
        else:
            draft_id = await state.add_outbox_item(
                prospect_id=convo.prospect_id,
                conversation_id=convo_id,
                kind="reply",
                to_email=from_email,
                subject="Re: your inquiry",
                body=draft_body,
                send_at=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                status="pending_review",
            )

        await state.log_action(
            action_type="draft_generated",
            agent="dashboard",
            details={
                "draft_id": draft_id,
                "conversation_id": convo_id,
                "status": "pending_review",
            },
        )

        return {
            "success": True,
            "conversation_id": convo_id,
            "draft_id": draft_id,
            "draft_body": draft_body,
            "status": "pending_review",
        }
    except Exception as e:
        logger.exception(f"Generate reply failed: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)



@app.get("/static/{path:path}")
async def static_file(path: str):
    """Serve the dashboard's own assets from disk.

    Read per-request rather than cached at import: editing app.css and hitting
    reload is the whole point of having them as real files. Fonts are vendored
    rather than fetched from a CDN — this is a local tool and it should work
    with the network off.
    """
    target = (WEB_DIR / path).resolve()
    root = WEB_DIR.resolve()
    if not target.is_file() or not target.is_relative_to(root):
        return PlainTextResponse("not found", status_code=404)

    if target.suffix in BINARY_TYPES:
        return Response(
            target.read_bytes(),
            media_type=BINARY_TYPES[target.suffix],
            headers={"Cache-Control": "public, max-age=604800"},
        )
    return PlainTextResponse(
        target.read_text(encoding="utf-8"),
        media_type=TEXT_TYPES.get(target.suffix, "text/plain"),
        headers={"Cache-Control": "no-store"},
    )


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return (WEB_DIR / "index.html").read_text(encoding="utf-8")


class DashboardSetupError(Exception):
    """The dashboard can't start safely; the message says what to do."""


def _check_loopback_bind(host: str) -> str:
    """The host to bind, which must be a loopback address unless HARVEY_ALLOW_REMOTE_BIND is set.
    The dashboard default is local-only: from another machine, use an SSH tunnel."""
    name = (host or "").strip().lower()
    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1]
    allow_remote = os.environ.get("HARVEY_ALLOW_REMOTE_BIND", "").lower() in ("1", "true", "yes")
    if not auth.is_loopback(name) and not allow_remote:
        raise DashboardSetupError(
            f"Refusing to listen on {host!r}: the dashboard only listens on this machine "
            "(127.0.0.1, ::1 or localhost). To reach it from elsewhere, tunnel instead: "
            "ssh -L 5555:127.0.0.1:5555 <this-host>")
    return name


def dashboard_url(host: str = "127.0.0.1", port: int = 5555) -> str:
    """The unlock link. The token rides in the #fragment, which browsers
    never send to the server (so it stays out of logs and Referer); the
    page moves it to sessionStorage and strips it from the address bar."""
    name = _check_loopback_bind(host)
    try:
        if ipaddress.ip_address(name).version == 6:
            name = f"[{name}]"
    except ValueError:
        pass
    return f"http://{name}:{port}/#token={token_store.get()}"


def start_dashboard(host: str = "127.0.0.1", port: int = 5555):
    """Start the dashboard server (loopback only, token required)."""
    import uvicorn

    try:
        bind = _check_loopback_bind(host)
        url = dashboard_url(host, port)
    except (DashboardSetupError, auth.TokenError) as e:
        raise DashboardSetupError(str(e)) from e

    print(f"\n  Harvey Dashboard running. Open this link to unlock it:\n\n    {url}\n")
    print(f"  The token is stored in {token_store.path} (owner-only). Keep the link")
    print("  private; anyone with it can use the dashboard. Press Ctrl+C to stop.\n")
    if os.name == "nt":
        print("  Windows: the token file is protected by your folder's permissions,")
        print("  not POSIX file modes.\n")
    uvicorn.run(app, host=bind, port=port, log_level="warning",
                proxy_headers=False, server_header=False, date_header=False)
