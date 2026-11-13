"""Tests for Harvey Multi-Provider AI system: registry, routing, fallback, and safeguards."""

import pytest
from unittest.mock import AsyncMock, patch

from harvey.config import BrainConfig, load_brain_config
from harvey.providers.base import (
    AIProvider,
    PaidProviderNotApprovedError,
    ProviderResult,
)
from harvey.providers.registry import ModelRegistry, MODEL_CATALOG, DEFAULT_MODELS
from harvey.providers.router import TaskRouter, PROVIDER_CLASSES
from harvey.providers.xai import XAIProvider
from harvey.brain import Brain, PROVIDER_LABELS
from harvey.state import StateManager


def test_all_seven_providers_registered():
    """Verify all 7 requested providers are present in the router and labels."""
    expected = {"ollama", "gemini", "groq", "openrouter", "xai", "claude", "codex"}
    assert set(PROVIDER_CLASSES.keys()) == expected
    assert set(PROVIDER_LABELS.keys()) == expected


def test_free_first_model_registry_defaults():
    """Verify default models for free cloud and local providers."""
    assert DEFAULT_MODELS["ollama"] in ("llama3.2", "qwen2.5:1.5b")
    assert DEFAULT_MODELS["gemini"] == "gemini-2.0-flash"
    assert DEFAULT_MODELS["groq"] == "llama-3.3-70b-versatile"
    assert DEFAULT_MODELS["openrouter"].endswith(":free")

    # Check free flags
    assert ModelRegistry.is_free_model("ollama", "llama3.2") is True
    assert ModelRegistry.is_free_model("gemini", "gemini-2.0-flash") is True
    assert ModelRegistry.is_free_model("groq", "llama-3.3-70b-versatile") is True
    assert ModelRegistry.is_free_model("openrouter", "meta-llama/llama-3.3-70b-instruct:free") is True
    assert ModelRegistry.is_free_model("xai", "grok-2-1212") is False


def test_task_based_routing():
    """Verify routine tasks route to fast models and complex tasks route to capable models."""
    # Routine task on Groq selects fast 8B model
    routine_groq = ModelRegistry.select_model_for_task("groq", task="classify_intent")
    assert routine_groq == "llama-3.1-8b-instant"

    # Complex writing task on Groq selects 70B model
    complex_groq = ModelRegistry.select_model_for_task("groq", task="personal_email")
    assert complex_groq == "llama-3.3-70b-versatile"

    # User configured model overrides task routing
    custom = ModelRegistry.select_model_for_task("groq", task="classify_intent", configured_model="my-custom-model")
    assert custom == "my-custom-model"


@pytest.mark.asyncio
async def test_xai_paid_provider_safeguard():
    """Verify xAI CANNOT be called without explicit xai_approved_paid=True."""
    # 1. Unapproved config
    provider = XAIProvider(config={"xai_approved_paid": False}, env={"XAI_API_KEY": "test-key"})
    preflight = await provider.preflight()
    assert "PAID provider" in preflight
    assert "Paid usage has NOT been approved" in preflight

    # Direct generate call must raise PaidProviderNotApprovedError
    with pytest.raises(PaidProviderNotApprovedError) as exc_info:
        await provider.generate("Hello")
    assert "Call to xAI Grok blocked" in str(exc_info.value)

    # 2. Approved config allows passing preflight when key is present
    approved_provider = XAIProvider(config={"xai_approved_paid": True}, env={"XAI_API_KEY": "test-key"})
    approved_preflight = await approved_provider.preflight()
    assert approved_preflight == ""


@pytest.mark.asyncio
async def test_fallback_does_not_call_unapproved_xai():
    """Verify fallback engine NEVER silently switches to xAI."""
    router = TaskRouter(
        config={
            "provider": "ollama",
            "fallback_providers": ["xai"],
            "xai_approved_paid": False,
        },
        env={"XAI_API_KEY": "test-key"},
    )
    # Mock primary ollama to fail
    router._provider_instances["ollama"] = AsyncMock()
    router._provider_instances["ollama"].generate.return_value = ProviderResult(
        text="", provider="ollama", model="llama3.2", is_error=True, error_message="ConnectError"
    )

    # Mock xAI generate
    mock_xai = AsyncMock()
    router._provider_instances["xai"] = mock_xai

    result = await router.execute("Test prompt")
    # Result must reflect failure, and xAI was never called
    assert result.is_error is True
    assert mock_xai.generate.call_count == 0


@pytest.mark.asyncio
async def test_intelligent_fallback_between_free_providers():
    """Verify fallback from failing primary (ollama) to ready free provider (groq)."""
    router = TaskRouter(
        config={
            "provider": "ollama",
            "fallback_providers": ["groq", "gemini"],
        },
        env={"GROQ_API_KEY": "gsk-mock"},
    )

    # Mock primary ollama failing
    mock_ollama = AsyncMock()
    mock_ollama.generate.return_value = ProviderResult(
        text="", provider="ollama", model="llama3.2", is_error=True, error_message="Ollama offline"
    )
    router._provider_instances["ollama"] = mock_ollama

    # Mock fallback groq succeeding
    mock_groq = AsyncMock()
    mock_groq.preflight.return_value = ""
    mock_groq.generate.return_value = ProviderResult(
        text="Fast Groq response",
        provider="groq",
        model="llama-3.3-70b-versatile",
        input_tokens=15,
        output_tokens=10,
    )
    router._provider_instances["groq"] = mock_groq

    result = await router.execute("Write an intro email", task="personal_email")
    assert result.text == "Fast Groq response"
    assert result.provider == "groq"
    assert result.fallback_used is True
    assert "Ollama offline" in result.fallback_reason


@pytest.mark.asyncio
async def test_brain_integration_with_multi_provider():
    """Verify Brain.think and Brain.think_json work seamlessly with TaskRouter."""
    state = StateManager()
    await state.init_db()

    cfg = BrainConfig(provider="groq", model="llama-3.1-8b-instant")
    brain = Brain(state, cfg)

    # Mock router execute
    brain.router.execute = AsyncMock(
        return_value=ProviderResult(
            text='{"fit_score": 9, "reasoning": "Strong match"}',
            provider="groq",
            model="llama-3.1-8b-instant",
            input_tokens=50,
            output_tokens=20,
        )
    )

    result_json = await brain.think_json("Score this lead", task="score_contacts")
    assert isinstance(result_json, dict)
    assert result_json["fit_score"] == 9
    assert result_json["reasoning"] == "Strong match"
    assert brain.last_reply_empty is False



@pytest.mark.asyncio
async def test_brain_preflight_auto_recovery_from_paused(tmp_path):
    """Verify that when a provider was paused, Brain.preflight() automatically recovers when healthy."""
    import json
    from harvey.brain import BRAIN_STATUS_KEY, parse_brain_status

    db_path = str(tmp_path / "test_recovery.db")
    state = StateManager(db_path)
    await state.init_db()

    # Pre-seed DB with a paused status from a prior outage
    paused_status = json.dumps({
        "provider": "ollama",
        "status": "paused",
        "reason": "Ollama is not running at http://localhost:11434 (ConnectError).",
        "since": "2026-11-02T08:50:55Z",
        "kind": "provider_error"
    })
    await state.set_setting(BRAIN_STATUS_KEY, paused_status)

    cfg = BrainConfig(provider="ollama", model="llama3.2")
    brain = Brain(state, cfg)

    # Mock provider preflight to simulate failure
    mock_provider = AsyncMock()
    mock_provider.preflight.return_value = "Ollama connection refused"
    brain.router._provider_instances["ollama"] = mock_provider

    err = await brain.preflight()
    assert err == "Ollama connection refused"
    assert brain.unavailable == "Ollama connection refused"
    stored = parse_brain_status(await state.get_setting(BRAIN_STATUS_KEY))
    assert stored["status"] == "paused"

    # Now simulate Ollama coming back online (preflight returns "")
    mock_provider.preflight.return_value = ""

    err_recovery = await brain.preflight()
    assert err_recovery == ""
    assert brain.unavailable == ""

    # Verify DB setting was automatically updated to ok without manual intervention
    stored_recovered = parse_brain_status(await state.get_setting(BRAIN_STATUS_KEY))
    assert stored_recovered["status"] == "ok"
    assert stored_recovered["provider"] == "ollama"


@pytest.mark.asyncio
async def test_dashboard_brain_status_auto_recovery(tmp_path, monkeypatch):
    """Verify that dashboard's _brain_status() automatically heals stale paused status for a healthy provider."""
    import json
    from harvey import dashboard as dash
    from harvey.brain import BRAIN_STATUS_KEY
    from harvey.config import BrainConfig

    db_path = str(tmp_path / "test_dash_recovery.db")
    state = StateManager(db_path)
    await state.init_db()

    # Pre-seed DB with stale paused status
    stale_paused = json.dumps({
        "provider": "ollama",
        "status": "paused",
        "reason": "Ollama is not running at http://localhost:11434.",
        "since": "2026-11-02T08:50:55Z",
        "kind": "provider_error"
    })
    await state.set_setting(BRAIN_STATUS_KEY, stale_paused)

    monkeypatch.setattr(dash, "_state", lambda: state)
    monkeypatch.setattr(dash, "query_db", lambda query, params=(): state._query_db_helper(query, params) if hasattr(state, '_query_db_helper') else None)
    monkeypatch.setattr(dash, "_active_provider", lambda: "ollama")
    monkeypatch.setattr(dash, "_active_brain", lambda: BrainConfig(provider="ollama"))

    # Also mock query_db to query the test SQLite db
    import aiosqlite
    async def mock_query_db(query, params=()):
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            await db.commit()
            return [dict(r) for r in rows]
    monkeypatch.setattr(dash, "query_db", mock_query_db)

    # Mock TaskRouter.get_provider to return a ready provider
    mock_prov = AsyncMock()
    mock_prov.preflight.return_value = ""

    with patch("harvey.providers.router.TaskRouter.get_provider", return_value=mock_prov):
        recovered = await dash._brain_status()
        assert recovered.get("status") == "ok"
        assert recovered.get("kind") == "auto_recovered"

        # Verify DB now has status "ok"
        row = await mock_query_db("SELECT value FROM settings WHERE key = ?", (BRAIN_STATUS_KEY,))
        assert "ok" in row[0]["value"]
