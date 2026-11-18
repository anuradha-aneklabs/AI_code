"""Shared test setup.

Brain's Claude path checks the installed CLI's ``--help`` before sending a
prompt (harvey/claude_runtime.py). Unit tests stub the subprocess itself, so
they also get a stand-in CLI here: found as plain ``claude``, with a help
text listing every isolation flag. Tests of the check itself override these,
and ``@pytest.mark.real_claude_cli`` tests use the real installed CLI. This
lives in tests/ only; the shipped package has no way to skip the check.
"""

import pytest

from harvey import claude_runtime

FAKE_HELP = "\n".join(f"  {flag} <value>   test" for flag in claude_runtime.REQUIRED_FLAGS)


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "real_claude_cli: runs the installed Claude CLI (no stand-in)")


@pytest.fixture(autouse=True)
def _private_dashboard_token(tmp_path_factory):
    """Never create or read the checkout's real data/dashboard-token."""
    import harvey.dashboard as dash

    dash.token_store.configure(tmp_path_factory.mktemp("token") / "dashboard-token")
    yield
    dash.token_store.configure(dash.TOKEN_FILE)


@pytest.fixture(autouse=True)
def _stand_in_claude_cli(request, monkeypatch):
    claude_runtime.clear_cache()
    if request.node.get_closest_marker("real_claude_cli"):
        yield
        claude_runtime.clear_cache()
        return
    monkeypatch.setattr(claude_runtime, "find_claude", lambda env=None: "claude")
    monkeypatch.setattr(claude_runtime, "_probe_help", lambda exe: FAKE_HELP)
    yield
    claude_runtime.clear_cache()


import harvey.humanize  # noqa: E402


@pytest.fixture(autouse=True)
def _no_installed_humanizer(monkeypatch, tmp_path):
    """Tests never pick up the operator's real ~/.claude Humanizer skill."""
    monkeypatch.setattr(harvey.humanize, "SKILL_PATH", tmp_path / "no-humanizer.md")


@pytest.fixture(scope="session", autouse=True)
def _ensure_baseline_test_database():
    """Ensure data/harvey.db exists and holds baseline pilot fixtures for tests that check live database invariants in CI."""
    import asyncio
    import sqlite3
    from pathlib import Path
    from harvey.paths import PROJECT_ROOT
    from harvey.state import StateManager

    db_path = PROJECT_ROOT / "data" / "harvey.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    sm = StateManager(str(db_path))
    try:
        asyncio.run(sm.init_db())
    except RuntimeError:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(sm.init_db())
        loop.close()

    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    try:
        c.execute(
            "INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES ('sending_paused', 'Pilot Evaluation - Read-Only Safety Hold', CURRENT_TIMESTAMP)"
        )

        pilot_drafts = [
            ("a02fed19ec8d", "contact-care-03", "93ba8dca0c60", "dentistgaganj@gmail.com", "pending_review"),
            ("36dfa015198c", "contact-seraphic-01", "93ba8dca0c60", "drvishakha@seraphicclinic.com", "cancelled"),
            ("3be7a27f3620", "contact-suryawanshi-02", "93ba8dca0c60", "varun.suryawanshi@gmail.com", "cancelled"),
            ("fad4e4984339", "contact-smile-05", "93ba8dca0c60", "smiledental364@gmail.com", "pending_review"),
        ]
        for did, prosp_id, camp_id, email, status in pilot_drafts:
            c.execute(
                "INSERT OR IGNORE INTO outbox (id, prospect_id, campaign_id, to_email, status, step, kind) VALUES (?, ?, ?, ?, ?, 1, 'sequence')",
                (did, prosp_id, camp_id, email, status),
            )

        c.execute(
            "INSERT OR IGNORE INTO runs (id, stage, status, records, cost_usd) VALUES ('9a6af942eca8', 'profile', 'completed', 41, 0.0)"
        )

        c.execute("SELECT count(*) FROM observations WHERE run_id = '9a6af942eca8'")
        existing_obs = c.fetchone()[0]
        if existing_obs < 41:
            for i in range(existing_obs, 41):
                obs_id = f"obs_seed_{i:03d}"
                c.execute(
                    "INSERT OR IGNORE INTO observations (id, company_id, run_id, signal_code, collector, evidence_url) VALUES (?, 'comp_seed', '9a6af942eca8', 'website', 'profile', 'https://example.com/evidence')",
                    (obs_id,),
                )

        conn.commit()
    finally:
        conn.close()
