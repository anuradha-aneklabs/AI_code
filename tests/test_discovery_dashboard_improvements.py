"""Tests for discovery endpoint improvements:
1. Template criteria refusal gate
2. Confirming free signals via API
3. Software product vs services classification in discovery results

Uses FAKE data and isolated temporary database only.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from harvey.dashboard import app, _state
from harvey.state import StateManager
from harvey.signals import seed_signal_catalog
from harvey.software_classifier import classify_software_business
from harvey.models import Company


@pytest.fixture
def temp_state(tmp_path: Path):
    test_db = tmp_path / "test_discovery_imp.db"
    return StateManager(str(test_db))


@pytest.mark.asyncio
async def test_template_criteria_refusal(temp_state, monkeypatch):
    await temp_state.init_db()
    await seed_signal_catalog(temp_state)

    monkeypatch.setattr("harvey.dashboard._state", lambda: temp_state)
    import harvey.dashboard as dash
    dash.state = temp_state
    dash.DB_PATH = str(temp_state.db_path)
    monkeypatch.setattr(dash.token_store, "get", lambda: "test-auth-token")

    with TestClient(app) as client:
        headers = {
            "Authorization": "Bearer test-auth-token",
            "Host": "127.0.0.1:5555",
            "Sec-Fetch-Site": "same-origin",
            "Content-Type": "application/json",
        }
        # Passing template criteria ('dentist')
        payload = {"industry": "dentist", "location": "Indore, Madhya Pradesh, India"}
        resp = client.post("/api/discovery/search", json=payload, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["status"] == "template_criteria_refused"
        assert "Refusing live discovery" in data["message"]


@pytest.mark.asyncio
async def test_confirm_free_signals_api(temp_state, monkeypatch):
    await temp_state.init_db()
    await seed_signal_catalog(temp_state)

    monkeypatch.setattr("harvey.dashboard._state", lambda: temp_state)
    import harvey.dashboard as dash
    dash.state = temp_state
    dash.DB_PATH = str(temp_state.db_path)
    monkeypatch.setattr(dash.token_store, "get", lambda: "test-auth-token")

    # Unconfirm all first
    async with temp_state._connect() as db:
        await db.execute("UPDATE signal_codes SET status = 'proposed'")
        await db.commit()

    with TestClient(app) as client:
        headers = {
            "Authorization": "Bearer test-auth-token",
            "Host": "127.0.0.1:5555",
            "Sec-Fetch-Site": "same-origin",
            "Content-Type": "application/json",
        }
        resp = client.post("/api/signals/status", json={"status": "confirmed", "codes": ["free"]}, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["changed"] >= 20

    confirmed = await temp_state.confirmed_signal_codes()
    assert len(confirmed) >= 20


@pytest.mark.asyncio
async def test_software_product_classification_persistence(temp_state):
    await temp_state.init_db()

    comp = Company(
        name="NovaAnalytics Cloud",
        domain="novaanalyticsfake.test",
        website="https://novaanalyticsfake.test",
        industry="SaaS",
        location="Indore, India",
    )
    comp_id = await temp_state.add_company(comp)

    html = "<html><body><h1>Cloud SaaS Platform</h1><p>Start your free trial today. View our pricing plans.</p></body></html>"
    p_class, p_ev = classify_software_business(html, title="NovaAnalytics Cloud")
    assert p_class == "software_product"

    await temp_state.update_company_product_class(comp_id, p_class, p_ev)

    saved_comp = await temp_state.get_company(comp_id)
    assert saved_comp is not None
    assert saved_comp.product_class == "software_product"
    assert "free trial" in saved_comp.product_class_evidence.lower() or "pricing" in saved_comp.product_class_evidence.lower()


def test_query_expansion_count_and_depth():
    from harvey.config import load_config
    from harvey.collectors.discover import build_queries

    config = load_config()
    queries = build_queries(
        config,
        cities=["Indore"],
        industries=["SaaS"],
        depth=30,
        limit=100,
        expand_queries=True,
    )
    # Verification: query expansion must produce 4 queries with depth 30
    assert len(queries) == 4
    for q in queries:
        assert q.depth == 30
        assert "indore" in q.location.lower()
    keywords = [q.keyword() for q in queries]
    assert any("saas company in indore" in kw.lower() for kw in keywords)
    assert any("saas startup" in kw.lower() for kw in keywords)
    assert any("software company in indore" in kw.lower() for kw in keywords)
