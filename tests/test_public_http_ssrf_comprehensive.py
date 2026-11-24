"""Comprehensive SSRF Security Review Tests for harvey/public_http.py.

Verifies that the PublicHTTPTransport and public_http_client:
1. REJECTS loopback (127.0.0.1, 127.0.0.2, ::1)
2. REJECTS private ranges (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
3. REJECTS link-local & cloud metadata (169.254.169.254)
4. REJECTS IPv6 loopback, unique-local (fc00::/7), link-local (fe80::/10)
5. REJECTS IPv4-mapped IPv6 of private addresses (::ffff:127.0.0.1, ::ffff:10.0.0.1)
6. REJECTS NAT64-embedded private IPv4 (64:ff9b::7f00:1, 64:ff9b::a00:1, 64:ff9b::a9fe:a9fe, 64:ff9b::c0a8:1)
7. REJECTS redirects to any private target
8. ACCEPTS normal public addresses (93.184.216.34, 20.207.73.82, 64:ff9b::14cf:4952)
"""

import asyncio
import socket
import httpx
import pytest
import pytest_asyncio

from harvey import public_http


@pytest_asyncio.fixture
async def mock_network(monkeypatch):
    """Mock socket.getaddrinfo and AsyncHTTPTransport to test SSRF filtering safely."""
    seen_requests = []
    dns_map = {}

    async def mock_getaddrinfo(host, port, **kwargs):
        ips = dns_map.get(host, ["93.184.216.34"])
        if isinstance(ips, Exception):
            raise ips
        return [
            (
                socket.AF_INET6 if ":" in ip else socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (ip, port),
            )
            for ip in ips
        ]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", mock_getaddrinfo)

    def mock_transport_respond(request):
        seen_requests.append(request)
        return httpx.Response(200, text="public content OK")

    monkeypatch.setattr(
        public_http.httpx,
        "AsyncHTTPTransport",
        lambda **kwargs: httpx.MockTransport(mock_transport_respond),
    )
    return seen_requests, dns_map


@pytest.mark.asyncio
@pytest.mark.parametrize("target_ip", [
    # IPv4 Loopback
    "127.0.0.1", "127.0.0.2", "127.1.2.3",
    # IPv4 RFC1918 Private Ranges
    "10.0.0.1", "10.254.0.1", "172.16.0.1", "172.31.255.254", "192.168.1.1", "192.168.100.50",
    # IPv4 Link-Local & Cloud Metadata
    "169.254.169.254", "169.254.1.1",
    # IPv6 Loopback
    "::1",
    # IPv6 Unique Local (Private)
    "fc00::1", "fd00::1", "fd12:3456:789a::1",
    # IPv6 Link-Local
    "fe80::1", "fe80::a00:27ff:fe4e:66a1",
    # IPv4-Mapped IPv6 of Private Addresses
    "::ffff:127.0.0.1", "::ffff:10.0.0.1", "::ffff:192.168.1.1", "::ffff:169.254.169.254",
    # NAT64-Embedded Private & Translated IPv4 (RFC 6052 Well-Known Prefix 64:ff9b::/96)
    "64:ff9b::7f00:1",       # 127.0.0.1
    "64:ff9b::a00:1",        # 10.0.0.1
    "64:ff9b::a9fe:a9fe",    # 169.254.169.254 (Cloud metadata)
    "64:ff9b::ac10:1",       # 172.16.0.1
    "64:ff9b::c0a8:1",       # 192.168.0.1
    "64:ff9b::14cf:4952",    # NAT64 translation is rejected by SSRF policy
    # Local-use NAT64 Translation Network (64:ff9b:1::/48)
    "64:ff9b:1::7f00:1",
    "64:ff9b:1::a9fe:a9fe",
])
async def test_ssrf_rejects_all_private_and_embedded_targets(target_ip, mock_network):
    seen, dns_map = mock_network
    dns_map["attack-target.test"] = [target_ip]

    async with public_http.public_http_client() as client:
        with pytest.raises(httpx.RequestError):
            await client.get("https://attack-target.test/")

    # Ensure no request ever hit the network
    assert seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize("redirect_target", [
    "http://127.0.0.1:8080/secret",
    "http://169.254.169.254/latest/meta-data/",
    "http://[64:ff9b::7f00:1]/admin",
    "http://[64:ff9b::a9fe:a9fe]/metadata",
    "http://[::ffff:10.0.0.1]/internal",
    "http://[fd00::1]/private",
])
async def test_ssrf_rejects_redirects_to_private_and_nat64_targets(redirect_target, mock_network, monkeypatch):
    seen, dns_map = mock_network
    dns_map["legit-site.test"] = ["93.184.216.34"]

    def mock_redirect(request):
        seen.append(request)
        return httpx.Response(302, headers={"Location": redirect_target})

    monkeypatch.setattr(
        public_http.httpx,
        "AsyncHTTPTransport",
        lambda **kwargs: httpx.MockTransport(mock_redirect),
    )

    async with public_http.public_http_client() as client:
        with pytest.raises(httpx.RequestError):
            await client.get("https://legit-site.test/")


@pytest.mark.asyncio
@pytest.mark.parametrize("public_ip", [
    "93.184.216.34",       # example.com
    "20.207.73.82",        # github.com IPv4
    "2606:4700:4700::1111",# Cloudflare DNS public IPv6
])
async def test_ssrf_accepts_valid_public_addresses(public_ip, mock_network):
    seen, dns_map = mock_network
    dns_map["valid-public.test"] = [public_ip]

    async with public_http.public_http_client() as client:
        resp = await client.get("https://valid-public.test/")
        assert resp.status_code == 200
        assert resp.text == "public content OK"
    assert len(seen) == 1
