"""Unit and security verification tests for email send kill switch and bypass prevention (fake data only)."""

import os
from unittest.mock import MagicMock, AsyncMock, patch
import pytest

from harvey.integrations.mail_provider import check_transmit_allowed
from harvey.integrations.gmail import GmailProvider
from harvey.integrations.smtp_mail import SmtpImapProvider
from harvey.integrations.microsoft import MicrosoftProvider
from harvey.integrations.instantly import InstantlyClient


def test_kill_switch_blocks_mock_outside_test_mode(monkeypatch):
    """Prove that setting HARVEY_TEST_MOCK_SEND_ALLOWED=1 outside test mode CANNOT permit sending."""
    # Simulate production environment (test mode disabled)
    monkeypatch.delenv("HARVEY_TEST_MODE", raising=False)
    monkeypatch.delenv("HARVEY_LIVE_SENDING_ENABLED", raising=False)
    monkeypatch.setenv("HARVEY_TEST_MOCK_SEND_ALLOWED", "1")

    allowed, err = check_transmit_allowed(is_mocked=True, provider_name="test")
    assert allowed is False
    assert "Hard master kill switch active" in err

    allowed_real, err_real = check_transmit_allowed(is_mocked=False, provider_name="gmail")
    assert allowed_real is False
    assert "Hard master kill switch active" in err_real


def test_kill_switch_blocks_live_unmocked_even_in_test_mode(monkeypatch):
    """Prove that under test mode, live unmocked sends are still strictly blocked."""
    monkeypatch.setenv("HARVEY_TEST_MODE", "1")
    monkeypatch.delenv("HARVEY_LIVE_SENDING_ENABLED", raising=False)
    monkeypatch.setenv("HARVEY_TEST_MOCK_SEND_ALLOWED", "1")

    # Real unmocked provider in test mode
    allowed, err = check_transmit_allowed(is_mocked=False, provider_name="gmail")
    assert allowed is False
    assert "Hard master kill switch active" in err


def test_harvey_live_sending_enabled_is_unset_in_config():
    """Verify that HARVEY_LIVE_SENDING_ENABLED is not enabled by default in environment or config files."""
    assert os.environ.get("HARVEY_LIVE_SENDING_ENABLED") not in ("1", "true", "yes")

    for fpath in [".env", "harvey.yaml", "config/default.yaml"]:
        if os.path.exists(fpath):
            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            # Must not have HARVEY_LIVE_SENDING_ENABLED=1 or true
            assert "HARVEY_LIVE_SENDING_ENABLED=1" not in content
            assert "HARVEY_LIVE_SENDING_ENABLED=true" not in content


@pytest.mark.asyncio
async def test_gmail_kill_switch_blocks_send(monkeypatch):
    """Verify GmailProvider blocks email sending via kill switch or read-only token."""
    monkeypatch.delenv("HARVEY_LIVE_SENDING_ENABLED", raising=False)
    monkeypatch.delenv("HARVEY_TEST_MODE", raising=False)

    config = MagicMock()
    config.channels.email.require_approval = False
    config.channels.email.signature.logo = ""
    env = MagicMock()

    # 1. Test mock provider hits hard kill switch when test mode is disabled
    provider_mock = GmailProvider(config, env)
    provider_mock._token = MagicMock()
    result_mock = await provider_mock.send_email(
        to_email="recipient@example.com",
        subject="Test",
        body="Test message",
    )
    assert result_mock.ok is False
    assert "Hard master kill switch active" in result_mock.error

    # 2. Test unmocked provider without send scope is rejected by read-only guard
    provider_real = GmailProvider(config, env)
    result_real = await provider_real.send_email(
        to_email="recipient@example.com",
        subject="Test",
        body="Test message",
    )
    assert result_real.ok is False
    assert "read-only scope" in result_real.error


@pytest.mark.asyncio
async def test_smtp_kill_switch_blocks_send(monkeypatch):
    """Verify SmtpImapProvider blocks email sending when kill switch is active."""
    monkeypatch.delenv("HARVEY_LIVE_SENDING_ENABLED", raising=False)
    monkeypatch.delenv("HARVEY_TEST_MODE", raising=False)

    config = MagicMock()
    config.channels.email.signature.logo = ""
    env = MagicMock()

    provider = SmtpImapProvider(config, env)
    result = await provider.send_email(
        to_email="recipient@example.com",
        subject="Test",
        body="Test message",
    )
    assert result.ok is False
    assert "Hard master kill switch active" in result.error


@pytest.mark.asyncio
async def test_instantly_kill_switch_blocks_reply(monkeypatch):
    """Verify InstantlyClient blocks send_reply when kill switch is active."""
    monkeypatch.delenv("HARVEY_LIVE_SENDING_ENABLED", raising=False)
    monkeypatch.delenv("HARVEY_TEST_MODE", raising=False)

    client = InstantlyClient(api_key="fake-key")
    result = await client.send_reply(
        reply_to_uuid="fake-uuid-123",
        body="Hello",
    )
    assert result is None
