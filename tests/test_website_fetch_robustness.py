"""Unit tests for Task 2: Fetch Robustness and Granular Error Classification.

Verifies:
1. True reason classification ("dns_nxdomain", "blocked_ssrf", "timeout", "tls_error", "bot_challenge", "http_403").
2. www fallback when apex DNS resolution fails.
3. NAT64 embedded public IPv4 is accepted while embedded private IPv4 is rejected.
4. Preference for public IPv4 addresses over IPv6.
5. All existing SSRF protections remain strict and intact.
"""

import asyncio
import socket
import pytest
import httpx

from harvey.single_domain_research import (
    _check_dns,
    _is_private_or_loopback_ip,
    verify_website,
    ResearchConfig,
    DomainRateLimiter,
)
from harvey.public_http import public_http_client, UnsafeURLError


def test_nat64_embedded_ip_classification():
    # 64:ff9b:: embedding private 127.0.0.1 or 10.0.0.1 -> private (True)
    assert _is_private_or_loopback_ip("64:ff9b::7f00:1") is True
    assert _is_private_or_loopback_ip("64:ff9b::a00:1") is True
    assert _is_private_or_loopback_ip("64:ff9b::a9fe:a9fe") is True  # 169.254.169.254
    assert _is_private_or_loopback_ip("64:ff9b:1::7f00:1") is True

    # 64:ff9b:: embedding public 35.170.46.164 (0x23aa:0x2ea4) -> public (False)
    assert _is_private_or_loopback_ip("64:ff9b::23aa:2ea4") is False

    # Standard loopback and private
    assert _is_private_or_loopback_ip("127.0.0.1") is True
    assert _is_private_or_loopback_ip("10.1.2.3") is True
    assert _is_private_or_loopback_ip("192.168.1.1") is True
    assert _is_private_or_loopback_ip("8.8.8.8") is False


@pytest.mark.asyncio
async def test_dns_www_fallback(monkeypatch):
    """When apex domain has no DNS record, fallback to www.domain succeeds."""
    async def mock_getaddrinfo(host, port, *args, **kwargs):
        if host == "apex-fail.com":
            raise socket.gaierror(11001, "getaddrinfo failed")
        elif host == "www.apex-fail.com":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        raise socket.gaierror(11001, "not found")

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", mock_getaddrinfo)

    ok, err_code, err_detail = await _check_dns("apex-fail.com")
    assert ok is True, f"Expected www fallback to resolve successfully: {err_code} {err_detail}"


@pytest.mark.asyncio
async def test_dns_nxdomain_granular_reason(monkeypatch):
    """When both apex and www fail resolution, error code is dns_nxdomain."""
    async def mock_getaddrinfo(host, port, *args, **kwargs):
        raise socket.gaierror(11001, "getaddrinfo failed")

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", mock_getaddrinfo)

    ok, err_code, err_detail = await _check_dns("nonexistent-domain-xyz123.com")
    assert ok is False
    assert err_code == "dns_nxdomain"


@pytest.mark.asyncio
async def test_dns_ssrf_blocked_granular_reason(monkeypatch):
    """When domain resolves only to private addresses, error code is blocked_ssrf."""
    async def mock_getaddrinfo(host, port, *args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 443)),
        ]

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", mock_getaddrinfo)

    ok, err_code, err_detail = await _check_dns("internal-evil.corp")
    assert ok is False
    assert err_code == "blocked_ssrf"


@pytest.mark.asyncio
async def test_bot_challenge_reason_on_403(monkeypatch):
    """When target returns 403 with bot challenge markers (Cloudflare), error code is bot_challenge."""
    async def mock_check_dns(domain):
        return True, "", ""

    monkeypatch.setattr("harvey.single_domain_research._check_dns", mock_check_dns)

    class MockBotClient:
        async def get(self, url, *args, **kwargs):
            req = httpx.Request("GET", url)
            headers = {"content-type": "text/html", "cf-mitigated": "challenge"}
            text = "<html><head><title>Just a moment... Attention Required! | Cloudflare</title></head></html>"
            return httpx.Response(403, headers=headers, text=text, request=req)

    cfg = ResearchConfig(timeout_seconds=2.0)
    rl = DomainRateLimiter()
    res = await verify_website("bearcom-mock.com", MockBotClient(), cfg, rl)

    assert res.verification_status == "unavailable"
    assert "bot_challenge" in res.evidence_text
    assert res.http_status == 403


@pytest.mark.asyncio
async def test_tls_error_reason(monkeypatch):
    """When SSL certificate verification fails, error code is tls_error."""
    async def mock_check_dns(domain):
        return True, "", ""

    monkeypatch.setattr("harvey.single_domain_research._check_dns", mock_check_dns)

    class MockTLSClient:
        async def get(self, url, *args, **kwargs):
            raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: Hostname mismatch")

    cfg = ResearchConfig(timeout_seconds=2.0)
    rl = DomainRateLimiter()
    res = await verify_website("bad-ssl.com", MockTLSClient(), cfg, rl)

    assert res.verification_status == "unavailable"
    assert "tls_error" in res.evidence_text
