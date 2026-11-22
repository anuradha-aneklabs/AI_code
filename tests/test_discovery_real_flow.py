"""Unit tests for Part A1 fixes: Discovery criteria passthrough, config overlay, and sample exclusion.
"""

import tempfile
import pytest
import pytest_asyncio
import yaml
from pathlib import Path

from harvey.collectors.discover import build_queries, DiscoveryQuery, run_discovery
from harvey.config import load_config, HarveyConfig
from harvey.state import StateManager
from harvey.signals import seed_signal_catalog, SIGNAL_CATALOG


@pytest_asyncio.fixture
async def temp_state(tmp_path):
    db_file = tmp_path / "test_discovery_flow.db"
    st = StateManager(str(db_file))
    await st.init_db()
    yield st


def test_criteria_passthrough():
    """Verify that custom industry and location pass directly into DiscoveryQuery."""
    config = load_config()
    queries = build_queries(
        config,
        cities=["Indore, Madhya Pradesh, India"],
        industries=["SaaS"],
        depth=30,
        limit=50,
    )
    assert len(queries) == 1
    q = queries[0]
    assert q.term == "SaaS"
    assert q.location == "Indore, Madhya Pradesh, India"
    assert q.keyword() == "SaaS Indore, Madhya Pradesh, India"


def test_harvey_local_yaml_overlay(tmp_path):
    """Verify that harvey.local.yaml with only an icp block cleanly overlays base harvey.yaml."""
    base_yaml = tmp_path / "harvey.yaml"
    base_data = {
        "persona": {
            "name": "Harvey",
            "company": "Harvey AI",
            "role": "Business Development",
            "email": "harvey@example.com",
            "linkedin": "linkedin.com/in/harvey",
            "tone": "consultative",
        },
        "product": {
            "name": "Harvey AI",
            "description": "B2B sales agent",
            "pricing": "Contact",
            "key_benefits": ["automation"],
            "objection_responses": {},
        },
        "icp": {
            "industries": ["dentist"],
            "company_size": "10-50",
            "titles": ["Owner"],
            "geography": ["Austin, TX"],
        },
    }
    base_yaml.write_text(yaml.safe_dump(base_data), encoding="utf-8")

    local_yaml = tmp_path / "harvey.local.yaml"
    local_data = {
        "icp": {
            "industries": ["SaaS"],
            "geography": ["Indore, Madhya Pradesh, India"],
        }
    }
    local_yaml.write_text(yaml.safe_dump(local_data), encoding="utf-8")

    cfg = load_config(str(local_yaml))
    assert cfg.icp.industries == ["SaaS"]
    assert cfg.icp.geography == ["Indore, Madhya Pradesh, India"]
    assert cfg.persona.name == "Harvey"
    assert cfg.product.name == "Harvey AI"


@pytest.mark.asyncio
async def test_signal_gate_stops_discovery_when_zero_confirmed(temp_state):
    """Verify that run_discovery halts with an explicit error when 0 signals are confirmed."""
    config = load_config()
    query = DiscoveryQuery(term="SaaS", location="Indore", coordinate="22.72,75.86,40")
    report = await run_discovery(temp_state, config, "osm", [query], dry_run=False)

    assert report.stopped == "no confirmed signals"
    assert any("no signals confirmed" in err.lower() or "0% confirmed" in err.lower() for err in report.errors)


@pytest.mark.asyncio
async def test_signal_confirmation_persistence(temp_state):
    """Verify that confirming free signals updates status and persists."""
    await seed_signal_catalog(temp_state)
    codes_before = await temp_state.confirmed_signal_codes()
    assert len(codes_before) == 0

    rows = await temp_state.get_signal_codes()
    free_codes = [r["code"] for r in rows if (r.get("cost_note") or "").lower().startswith(("free", "included"))]
    assert len(free_codes) > 0

    for c in free_codes:
        await temp_state.set_signal_status(c, "confirmed")

    codes_after = await temp_state.confirmed_signal_codes()
    assert len(codes_after) == len(free_codes)
    for c in free_codes:
        assert c in codes_after
