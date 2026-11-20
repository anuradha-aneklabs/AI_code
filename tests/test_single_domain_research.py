"""Unit tests for Milestone 1: Single-domain research foundation.

Covers AC1 through AC10:
AC1: Valid company email -> run row, 5 stage rows, company_websites row, source_pages rows.
AC2: Invalid syntax email -> run "failed", email_validation "failed", zero network requests.
AC3: Free-mail address -> domain stage "skipped", no website guessed, nothing fetched.
AC4: DNS failure -> verification_status "unavailable", later stages "skipped", earlier results persisted.
AC5: Homepage redirects to different domain -> verification_status "unverified", redirect recorded.
AC6: Page-fetch error on one page -> that page "failed", other pages stored, earlier stages not rolled back.
AC7: robots.txt disallows a path -> page "blocked_by_robots", no request made to it.
AC8: Same email run twice -> no duplicate corruption, clean idempotency.
AC9: Source code grepped for "aneka" and "anika" -> zero matches in application code.
AC10: Stage failure -> no sample or fallback data written anywhere.
"""

import asyncio
import json
import os
import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from harvey.public_http import UnsafeURLError
from harvey.single_domain_research import (
    DomainRateLimiter,
    ResearchConfig,
    classify_page_type,
    discover_pages,
    extract_domain,
    get_registrable_domain,
    is_same_registrable_domain,
    load_free_email_providers,
    load_page_signals,
    run_single_email_research,
    validate_and_normalize_email,
)
from harvey.state import StateManager


@pytest.fixture
def temp_state(tmp_path):
    """Provide a fresh, isolated temporary database for testing."""
    db_file = tmp_path / "test_research.db"
    return StateManager(db_path=str(db_file))


@pytest.fixture
def test_config(tmp_path):
    """Provide a test config with a custom free email providers file."""
    free_file = tmp_path / "test_free_providers.txt"
    free_file.write_text("gmail.com\nyahoo.com\nhotmail.com\noutlook.com\n", encoding="utf-8")
    return ResearchConfig(
        max_pages=8,
        timeout_seconds=5.0,
        min_interval_seconds=0.0,  # Fast tests
        max_retries=1,
        free_providers_file=str(free_file),
    )


# ── AC1: Valid Company Email End-to-End Mocked ─────────────────────────

@pytest.mark.asyncio
async def test_ac1_valid_company_email_success(temp_state, test_config):
    """AC1: Given a valid company email, When the pipeline runs (HTTP mocked),
    Then a run row, 5 stage rows, 1 company_websites row and source_pages rows exist with correct statuses."""
    domain = "acmecorp.com"
    email = "contact@acmecorp.com"

    homepage_html = """
    <!DOCTYPE html>
    <html>
    <head><title>Acme Corp</title></head>
    <body>
        <h1>Welcome to Acme Corp</h1>
        <p>We are a high-technology industrial automation company serving global enterprises.</p>
        <a href="/about">About Us</a>
        <a href="/team">Our Team</a>
        <a href="/contact">Contact Support</a>
        <a href="https://external-partner.com">Partner Site</a>
    </body>
    </html>
    """

    about_html = "<html><body><h1>About Acme Corp</h1><p>Founded in 2018 with world-class engineering standards.</p></body></html>"
    team_html = "<html><body><h1>Our Leadership</h1><p>Meet our executive leadership and engineering teams.</p></body></html>"
    contact_html = "<html><body><h1>Contact Us</h1><p>Reach out to our operations desk at headquarters.</p></body></html>"

    def mock_transport_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if url_str.endswith("/robots.txt"):
            return httpx.Response(200, text="User-agent: *\nAllow: /\n", headers={"content-type": "text/plain"})
        if url_str in ("https://acmecorp.com", "https://acmecorp.com/", "http://acmecorp.com/"):
            return httpx.Response(200, text=homepage_html, headers={"content-type": "text/html"})
        if url_str == "https://acmecorp.com/about":
            return httpx.Response(200, text=about_html, headers={"content-type": "text/html"})
        if url_str == "https://acmecorp.com/team":
            return httpx.Response(200, text=team_html, headers={"content-type": "text/html"})
        if url_str == "https://acmecorp.com/contact":
            return httpx.Response(200, text=contact_html, headers={"content-type": "text/html"})
        return httpx.Response(404, text="Not Found")

    transport = httpx.MockTransport(mock_transport_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with patch("harvey.single_domain_research._check_dns", new=AsyncMock(return_value=(True, ""))):
            result = await run_single_email_research(
                email,
                state=temp_state,
                config=test_config,
                client=client,
            )

    run_id = result["id"]
    assert result["status"] == "completed"
    assert result["domain"] == domain
    assert result["input_email"] == email

    # 1. Verify 5 stage rows
    stages = await temp_state.get_research_stages(run_id)
    assert len(stages) == 5
    stage_names = [s["stage_name"] for s in stages]
    assert stage_names == ["email_validation", "domain_extraction", "website_verification", "page_discovery", "page_fetch"]
    for s in stages:
        assert s["status"] == "completed", f"Stage {s['stage_name']} failed with error {s.get('error_message')}"

    # 2. Verify company_websites row
    site = await temp_state.get_company_website(run_id)
    assert site is not None
    assert site["domain"] == domain
    assert site["verification_status"] == "verified"
    assert site["dns_resolved"] == 1
    assert site["confidence"] == 1.0
    assert site["http_status"] == 200

    # 3. Verify source_pages rows (home, about, team, contact)
    pages = await temp_state.get_source_pages(run_id)
    assert len(pages) == 4
    page_types = {p["page_type"] for p in pages}
    assert "home" in page_types
    assert "about" in page_types
    assert "team" in page_types
    assert "contact" in page_types
    for p in pages:
        assert p["extraction_status"] == "fetched"
        assert p["http_status"] == 200
        assert len(p["raw_text"]) > 10
        assert len(p["content_hash"]) == 64  # SHA-256


# ── AC2: Invalid Syntax Email ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_ac2_invalid_syntax_email(temp_state, test_config):
    """AC2: Given an invalid-syntax email, When the pipeline runs,
    Then the run is 'failed', email_validation is 'failed', and no network request is made."""
    called_network = False

    def mock_transport_handler(request: httpx.Request) -> httpx.Response:
        nonlocal called_network
        called_network = True
        return httpx.Response(200, text="OK")

    transport = httpx.MockTransport(mock_transport_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await run_single_email_research(
            "not-a-valid-email-at-all",
            state=temp_state,
            config=test_config,
            client=client,
        )

    assert not called_network, "Network request was made for invalid syntax email!"
    assert result["status"] == "failed"
    run_id = result["id"]

    stages = await temp_state.get_research_stages(run_id)
    assert len(stages) == 5
    st1 = next(s for s in stages if s["stage_name"] == "email_validation")
    assert st1["status"] == "failed"
    assert "syntax" in st1["error_message"].lower()

    for rem in ("domain_extraction", "website_verification", "page_discovery", "page_fetch"):
        s = next(st for st in stages if st["stage_name"] == rem)
        assert s["status"] == "skipped"

    # Verify no website or pages rows were created
    site = await temp_state.get_company_website(run_id)
    assert site is None
    pages = await temp_state.get_source_pages(run_id)
    assert len(pages) == 0


# ── AC3: Free-Mail / Consumer Mail Address ────────────────────────────

@pytest.mark.asyncio
async def test_ac3_consumer_free_mail_skipped(temp_state, test_config):
    """AC3: Given a free-mail address such as one at a consumer provider,
    When the pipeline runs, Then the domain stage is 'skipped', no website is guessed,
    and nothing is fetched."""
    called_network = False

    def mock_transport_handler(request: httpx.Request) -> httpx.Response:
        nonlocal called_network
        called_network = True
        return httpx.Response(200, text="OK")

    transport = httpx.MockTransport(mock_transport_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await run_single_email_research(
            "founder.candidate@gmail.com",
            state=temp_state,
            config=test_config,
            client=client,
        )

    assert not called_network, "Network request was made for consumer free-mail address!"
    assert result["status"] == "completed"
    run_id = result["id"]

    stages = await temp_state.get_research_stages(run_id)
    st1 = next(s for s in stages if s["stage_name"] == "email_validation")
    assert st1["status"] == "completed"

    st2 = next(s for s in stages if s["stage_name"] == "domain_extraction")
    assert st2["status"] == "skipped"
    assert ("consumer email provider" in st2["error_message"].lower() or "not a company domain" in st2["error_message"].lower())

    for rem in ("website_verification", "page_discovery", "page_fetch"):
        s = next(st for st in stages if st["stage_name"] == rem)
        assert s["status"] == "skipped"

    # Confirm zero website or page rows created
    site = await temp_state.get_company_website(run_id)
    assert site is None
    pages = await temp_state.get_source_pages(run_id)
    assert len(pages) == 0


# ── AC4: Domain with DNS Failure ───────────────────────────────────────

@pytest.mark.asyncio
async def test_ac4_dns_failure_handling(temp_state, test_config):
    """AC4: Given a domain with DNS failure, When the pipeline runs,
    Then verification_status is 'unavailable' with a reason, later stages are 'skipped',
    and the earlier stage results remain persisted."""
    email = "contact@nonexistent-nxdomain-unregistered.io"

    with patch("harvey.single_domain_research._check_dns", new=AsyncMock(return_value=(False, "Name or service not known"))):
        result = await run_single_email_research(
            email,
            state=temp_state,
            config=test_config,
        )

    run_id = result["id"]
    assert result["status"] == "partial"

    # Stages 1 and 2 completed
    stages = await temp_state.get_research_stages(run_id)
    st1 = next(s for s in stages if s["stage_name"] == "email_validation")
    assert st1["status"] == "completed"

    st2 = next(s for s in stages if s["stage_name"] == "domain_extraction")
    assert st2["status"] == "completed"

    st3 = next(s for s in stages if s["stage_name"] == "website_verification")
    assert st3["status"] == "failed"
    assert "dns" in st3["error_message"].lower()

    # Stages 4 and 5 skipped
    st4 = next(s for s in stages if s["stage_name"] == "page_discovery")
    st5 = next(s for s in stages if s["stage_name"] == "page_fetch")
    assert st4["status"] == "skipped"
    assert st5["status"] == "skipped"

    # Company website recorded as unavailable
    site = await temp_state.get_company_website(run_id)
    assert site is not None
    assert site["verification_status"] == "unavailable"
    assert site["dns_resolved"] == 0
    assert "DNS" in site["evidence_text"]


# ── AC5: Redirect to Different Registrable Domain ──────────────────────

@pytest.mark.asyncio
async def test_ac5_redirect_to_different_domain(temp_state, test_config):
    """AC5: Given a domain whose homepage redirects to a different registrable domain,
    When verification runs, Then verification_status is 'unverified' with the redirect recorded as evidence."""
    email = "info@oldbrand.com"

    def mock_transport_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "oldbrand.com" in url_str:
            # Emulate 301 redirect to completely different domain
            return httpx.Response(301, headers={"location": "https://newunrelatedcorp.com/welcome"})
        if "newunrelatedcorp.com" in url_str:
            return httpx.Response(200, text="<html><body>Welcome to New Unrelated Corp</body></html>", headers={"content-type": "text/html"})
        return httpx.Response(404, text="Not Found")

    transport = httpx.MockTransport(mock_transport_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with patch("harvey.single_domain_research._check_dns", new=AsyncMock(return_value=(True, ""))):
            result = await run_single_email_research(
                email,
                state=temp_state,
                config=test_config,
                client=client,
            )

    run_id = result["id"]
    site = await temp_state.get_company_website(run_id)
    assert site is not None
    assert site["verification_status"] == "unverified"
    assert site["confidence"] < 0.5
    assert "different registrable domain" in site["evidence_text"].lower()
    assert "newunrelatedcorp.com" in site["evidence_text"].lower()


# ── AC6: Page Fetch Error On One Page ──────────────────────────────────

@pytest.mark.asyncio
async def test_ac6_page_fetch_partial_error(temp_state, test_config):
    """AC6: Given the page-fetch stage raises an error on one page,
    When the pipeline runs, Then that page is 'failed', the other pages are stored,
    and earlier stages are not rolled back."""
    email = "team@innovatetech.org"

    homepage_html = """
    <html>
    <body>
        <h1>InnovateTech</h1>
        <p>A software laboratory advancing open protocols and decentralized networking.</p>
        <a href="/about">About Us</a>
        <a href="/team-broken">Broken Team Page</a>
    </body>
    </html>
    """

    def mock_transport_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if url_str.endswith("/robots.txt"):
            return httpx.Response(200, text="User-agent: *\nAllow: /\n", headers={"content-type": "text/plain"})
        if url_str in ("https://innovatetech.org", "https://innovatetech.org/"):
            return httpx.Response(200, text=homepage_html, headers={"content-type": "text/html"})
        if url_str == "https://innovatetech.org/about":
            return httpx.Response(200, text="<html><body><h1>About Us</h1><p>Our story and team.</p></body></html>", headers={"content-type": "text/html"})
        if url_str == "https://innovatetech.org/team-broken":
            return httpx.Response(500, text="Server Internal Error")
        return httpx.Response(404, text="Not Found")

    transport = httpx.MockTransport(mock_transport_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with patch("harvey.single_domain_research._check_dns", new=AsyncMock(return_value=(True, ""))):
            result = await run_single_email_research(
                email,
                state=temp_state,
                config=test_config,
                client=client,
            )

    run_id = result["id"]
    pages = await temp_state.get_source_pages(run_id)
    assert len(pages) == 3  # home, about, broken team

    home_pg = next(p for p in pages if p["page_type"] == "home")
    assert home_pg["extraction_status"] == "fetched"

    about_pg = next(p for p in pages if p["url"].endswith("/about"))
    assert about_pg["extraction_status"] == "fetched"

    broken_pg = next(p for p in pages if p["url"].endswith("/team-broken"))
    assert broken_pg["extraction_status"] == "failed"
    assert "HTTP 500" in broken_pg["error_message"]

    # Ensure website and earlier stages remain completely intact
    site = await temp_state.get_company_website(run_id)
    assert site["verification_status"] == "verified"
    stages = await temp_state.get_research_stages(run_id)
    assert all(s["status"] in ("completed", "partial") for s in stages)


# ── AC7: robots.txt Disallow Handling ─────────────────────────────────

@pytest.mark.asyncio
async def test_ac7_robots_txt_disallow(temp_state, test_config):
    """AC7: Given robots.txt disallows a path, When fetching,
    Then the page is 'blocked_by_robots' and no request is made to it."""
    email = "hq@politedomain.com"

    homepage_html = """
    <html>
    <body>
        <h1>Polite Domain</h1>
        <p>A corporate enterprise platform with strict robotic indexing boundaries.</p>
        <a href="/public-about">About Us</a>
        <a href="/private-team">Private Team</a>
    </body>
    </html>
    """

    robots_txt = """
    User-agent: *
    Disallow: /private-team
    Allow: /
    """

    visited_urls = set()

    def mock_transport_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        visited_urls.add(url_str)
        if url_str.endswith("/robots.txt"):
            return httpx.Response(200, text=robots_txt, headers={"content-type": "text/plain"})
        if url_str in ("https://politedomain.com", "https://politedomain.com/"):
            return httpx.Response(200, text=homepage_html, headers={"content-type": "text/html"})
        if url_str == "https://politedomain.com/public-about":
            return httpx.Response(200, text="<html><body><h1>About</h1><p>Public info.</p></body></html>", headers={"content-type": "text/html"})
        if url_str == "https://politedomain.com/private-team":
            # Should NOT be requested
            return httpx.Response(200, text="Secret Team", headers={"content-type": "text/html"})
        return httpx.Response(404, text="Not Found")

    transport = httpx.MockTransport(mock_transport_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with patch("harvey.single_domain_research._check_dns", new=AsyncMock(return_value=(True, ""))):
            result = await run_single_email_research(
                email,
                state=temp_state,
                config=test_config,
                client=client,
            )

    run_id = result["id"]
    # Check that private-team was NOT sent over network
    assert "https://politedomain.com/private-team" not in visited_urls

    pages = await temp_state.get_source_pages(run_id)
    private_pg = next(p for p in pages if p["url"].endswith("/private-team"))
    assert private_pg["extraction_status"] == "blocked_by_robots"
    assert "robots.txt" in private_pg["error_message"].lower()


# ── AC8: Idempotency & Re-running ─────────────────────────────────────

@pytest.mark.asyncio
async def test_ac8_idempotency_and_rerun(temp_state, test_config):
    """AC8: Given the same email is run twice, When both runs finish,
    Then there are no duplicate-corrupted rows and behavior matches the documented idempotency rule."""
    email = "exec@cloudinfra.io"

    homepage_html = "<html><body><h1>Cloud Infra</h1><p>Cloud compute platform services.</p></body></html>"

    def mock_transport_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if url_str.endswith("/robots.txt"):
            return httpx.Response(200, text="User-agent: *\nAllow: /\n", headers={"content-type": "text/plain"})
        return httpx.Response(200, text=homepage_html, headers={"content-type": "text/html"})

    transport = httpx.MockTransport(mock_transport_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with patch("harvey.single_domain_research._check_dns", new=AsyncMock(return_value=(True, ""))):
            run1 = await run_single_email_research(email, state=temp_state, config=test_config, client=client)
            run2 = await run_single_email_research(email, state=temp_state, config=test_config, client=client)

    # Two distinct, audit-traceable run IDs
    assert run1["id"] != run2["id"]
    assert run1["status"] == "completed"
    assert run2["status"] == "completed"

    # Stages for run 1 and run 2 are properly isolated
    stages_1 = await temp_state.get_research_stages(run1["id"])
    stages_2 = await temp_state.get_research_stages(run2["id"])
    assert len(stages_1) == 5
    assert len(stages_2) == 5

    # Pages are cleanly recorded without cross-run leakage
    pages_1 = await temp_state.get_source_pages(run1["id"])
    pages_2 = await temp_state.get_source_pages(run2["id"])
    assert len(pages_1) >= 1
    assert len(pages_2) >= 1


# ── AC9: Automated Grep Test for Hardcoded Forbidden Company Names ────

def test_ac9_no_hardcoded_company_names_in_source():
    """AC9: Given the application source, When grepped (case-insensitive) for 'aneka' and 'anika',
    Then there are zero matches in application code."""
    project_root = Path(__file__).resolve().parent.parent
    harvey_dir = project_root / "harvey"

    forbidden_pattern = re.compile(r"\b(aneka|anika|anekalabs|anikalabs)\b", re.IGNORECASE)

    violations = []
    for py_file in harvey_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8", errors="ignore")
        for line_num, line in enumerate(content.splitlines(), start=1):
            if forbidden_pattern.search(line):
                violations.append(f"{py_file.name}:{line_num} -> {line.strip()}")

    assert len(violations) == 0, f"Found hardcoded company names in source files:\n" + "\n".join(violations)


# ── AC10: Provider Failure -> No Fake/Sample Fallback Data ─────────────

@pytest.mark.asyncio
async def test_ac10_no_sample_or_fallback_data_on_failure(temp_state, test_config):
    """AC10: Given a provider failure, When any stage fails,
    Then no sample/fallback data is written anywhere."""
    email = "billing@failingcorp.org"

    def mock_transport_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable")

    transport = httpx.MockTransport(mock_transport_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with patch("harvey.single_domain_research._check_dns", new=AsyncMock(return_value=(True, ""))):
            result = await run_single_email_research(
                email,
                state=temp_state,
                config=test_config,
                client=client,
            )

    run_id = result["id"]
    site = await temp_state.get_company_website(run_id)
    assert site is not None
    assert site["verification_status"] == "unavailable"

    # Strictly verify that no fake data or sample company profile was injected
    assert site["domain"] == "failingcorp.org"
    pages = await temp_state.get_source_pages(run_id)
    assert len(pages) == 0


# ── Additional Helper & API Route Tests ───────────────────────────────

def test_registrable_domain_parsing():
    """Verify registrable domain parsing handles subdomains, ports, and two-part ccTLDs."""
    assert get_registrable_domain("sub.example.com") == "example.com"
    assert get_registrable_domain("www.example.com") == "example.com"
    assert get_registrable_domain("sub.deep.example.co.uk") == "example.co.uk"
    assert get_registrable_domain("portal.service.co.in:8080") == "service.co.in"
    assert is_same_registrable_domain("https://example.com", "http://www.example.com") is True
    assert is_same_registrable_domain("app.example.com", "api.example.com") is True
    assert is_same_registrable_domain("example.com", "other.com") is False


@pytest.mark.asyncio
async def test_api_research_endpoints(tmp_path, temp_state):
    """Verify POST /api/research/run and GET /api/research/run/{run_id} API routes."""
    import harvey.dashboard as dash
    dash.token_store.configure(tmp_path / "dashboard-token")
    token = dash.token_store.get()

    headers = {"Authorization": f"Bearer {token}"}
    transport = httpx.ASGITransport(app=dash.app)

    with patch("harvey.dashboard._state", return_value=temp_state):
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:5555") as client:
            # 1. Invalid payload: missing email
            r_bad = await client.post("/api/research/run", json={}, headers=headers)
            assert r_bad.status_code == 400

            # 2. Valid request
            with patch("harvey.single_domain_research.run_single_email_research", new=AsyncMock(return_value={
                "id": "run_test_123",
                "status": "completed",
                "domain": "testcompany.io",
                "stages": {},
                "website": {"verification_status": "verified"},
                "pages": [],
            })):
                r_post = await client.post("/api/research/run", json={"email": "lead@testcompany.io"}, headers=headers)
                assert r_post.status_code == 200
                data = r_post.json()
                assert data["success"] is True
                assert data["run"]["id"] == "run_test_123"

            # 3. GET run by id: non-existent -> 404
            r_get_missing = await client.get("/api/research/run/non_existent_id", headers=headers)
            assert r_get_missing.status_code == 404

            # 4. Save a real run in temp DB and retrieve via GET
            await temp_state.save_research_run(
                run_id="run_persisted_999",
                input_email="cto@techfirm.com",
                normalized_email="cto@techfirm.com",
                domain="techfirm.com",
                status="completed",
            )
            r_get = await client.get("/api/research/run/run_persisted_999", headers=headers)
            assert r_get.status_code == 200
            get_data = r_get.json()
            assert get_data["success"] is True
            assert get_data["run"]["id"] == "run_persisted_999"
            assert get_data["run"]["domain"] == "techfirm.com"


# ── TASK B: Fetcher SSRF Safety Tests ─────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("bad_target", [
    "admin@127.0.0.1",
    "root@localhost",
    "sec@169.254.169.254",
    "dev@10.0.0.1",
    "lan@192.168.1.100",
    "corp@172.16.5.10",
    "v6@[::1]",
])
async def test_task_b_ssrf_rejects_non_public_destinations(temp_state, test_config, bad_target):
    """TASK B: Prove loopback, private, link-local, and metadata addresses are rejected
    without making any network request."""
    network_called = False

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal network_called
        network_called = True
        return httpx.Response(200, text="Should never reach here")

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await run_single_email_research(
            bad_target,
            state=temp_state,
            config=test_config,
            client=client,
        )

    assert not network_called, f"Network was contacted for unsafe SSRF target: {bad_target}"
    run_id = res["id"]
    stages = await temp_state.get_research_stages(run_id)
    # The run must fail or set website unavailable
    site = await temp_state.get_company_website(run_id)
    if site:
        assert site["verification_status"] == "unavailable"
        assert "ssrf" in site["evidence_text"].lower() or "dns" in site["evidence_text"].lower()


@pytest.mark.asyncio
async def test_task_b_ssrf_rejects_private_dns_answers(temp_state, test_config):
    """TASK B: Prove domain whose DNS answers with a private IP is rejected without HTTP requests."""
    network_called = False

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal network_called
        network_called = True
        return httpx.Response(200, text="Should not be called")

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with patch("harvey.single_domain_research._check_dns", new=AsyncMock(return_value=(False, "SSRF blocked: domain resolves to non-public destination (10.0.0.5)"))):
            res = await run_single_email_research(
                "hello@internal-dns-leak.com",
                state=temp_state,
                config=test_config,
                client=client,
            )

    assert not network_called
    assert res["status"] == "partial"
    site = await temp_state.get_company_website(res["id"])
    assert site["verification_status"] == "unavailable"
    assert "SSRF blocked" in site["evidence_text"]


@pytest.mark.asyncio
async def test_task_b_ssrf_rejects_redirect_to_metadata(temp_state, test_config):
    """TASK B: Prove redirect to cloud metadata is intercepted and fails closed."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        if "meta-data" in str(request.url) or "169.254" in str(request.url):
            raise UnsafeURLError("Website resolves to a non-public destination", request=request)
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with patch("harvey.single_domain_research._check_dns", new=AsyncMock(return_value=(True, ""))):
            res = await run_single_email_research(
                "contact@redirect-attacker.com",
                state=temp_state,
                config=test_config,
                client=client,
            )

    site = await temp_state.get_company_website(res["id"])
    assert site["verification_status"] == "unavailable"
    assert "ssrf blocked" in site["evidence_text"].lower()


# ── TASK C: Registrable Domain Tests ──────────────────────────────────

def test_task_c_registrable_domain_multi_part():
    """TASK C: Multi-part suffix parsing tests:
    sub.example.com -> example.com
    www.example.co.uk -> example.co.uk
    a.b.example.co.in -> example.co.in
    """
    assert get_registrable_domain("sub.example.com") == "example.com"
    assert get_registrable_domain("www.example.co.uk") == "example.co.uk"
    assert get_registrable_domain("a.b.example.co.in") == "example.co.in"
    assert get_registrable_domain("sub.deep.portal.example.com.au") == "example.com.au"
    assert get_registrable_domain("https://example.com.br/path") == "example.com.br"


@pytest.mark.asyncio
async def test_task_c_redirect_from_co_uk_to_com_is_unverified(temp_state, test_config):
    """TASK C: A redirect from example.co.uk to example.com must be 'unverified'."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "example.co.uk" in url_str:
            return httpx.Response(301, headers={"location": "https://example.com/welcome"})
        if "example.com" in url_str:
            return httpx.Response(200, text="<html><body>Welcome to .com</body></html>", headers={"content-type": "text/html"})
        return httpx.Response(404, text="Not Found")

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with patch("harvey.single_domain_research._check_dns", new=AsyncMock(return_value=(True, ""))):
            res = await run_single_email_research(
                "office@example.co.uk",
                state=temp_state,
                config=test_config,
                client=client,
            )

    site = await temp_state.get_company_website(res["id"])
    assert site["verification_status"] == "unverified"
    assert site["confidence"] < 0.5
    assert "different registrable domain" in site["evidence_text"].lower()
    assert "example.com" in site["evidence_text"]


# ── TASK D: Page Discovery Fixture Tests ───────────────────────────────

@pytest.mark.asyncio
async def test_task_d_page_discovery_stripe_style_fixture(temp_state, test_config):
    """TASK D: Model the real-world failure:
    Nav links include /in/customers/x, /in/guides/y, /in/money-management/z,
    plus real /in/about and /in/company/leadership.
    Assert:
    - Case studies and guides are classified 'other' and NOT fetched as about/team.
    - Excluded pages do not take slots in the fetch cap.
    - The real /in/about and /in/company/leadership are selected and fetched.
    """
    domain = "globalfintech.com"
    email = "press@globalfintech.com"

    homepage_html = """
    <!DOCTYPE html>
    <html>
    <head><title>Global FinTech</title></head>
    <body>
        <nav>
            <a href="/in/customers/hertz">Hertz Customer Story</a>
            <a href="/in/guides/introduction-to-risk-management">Risk Management Guide</a>
            <a href="/in/money-management/availability">Money Management Availability</a>
            <a href="/in/about">About Our Mission</a>
            <a href="/in/company/leadership">Executive Leadership</a>
        </nav>
        <main>
            <h1>Global FinTech Platform</h1>
            <p>Infrastructure for the internet economy.</p>
        </main>
    </body>
    </html>
    """

    about_html = "<html><body><h1>About Global FinTech</h1><p>Our founding story.</p></body></html>"
    leadership_html = "<html><body><h1>Executive Leadership</h1><p>Meet our board and founders.</p></body></html>"

    def mock_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if url_str.endswith("/robots.txt"):
            return httpx.Response(200, text="User-agent: *\nAllow: /\n", headers={"content-type": "text/plain"})
        if url_str in ("https://globalfintech.com", "https://globalfintech.com/"):
            return httpx.Response(200, text=homepage_html, headers={"content-type": "text/html"})
        if url_str == "https://globalfintech.com/in/about":
            return httpx.Response(200, text=about_html, headers={"content-type": "text/html"})
        if url_str == "https://globalfintech.com/in/company/leadership":
            return httpx.Response(200, text=leadership_html, headers={"content-type": "text/html"})
        # Excluded URLs should never be requested
        if any(seg in url_str for seg in ("customers", "guides", "money-management")):
            return httpx.Response(200, text="<html><body>Customer page</body></html>", headers={"content-type": "text/html"})
        return httpx.Response(404, text="Not Found")

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with patch("harvey.single_domain_research._check_dns", new=AsyncMock(return_value=(True, ""))):
            result = await run_single_email_research(
                email,
                state=temp_state,
                config=test_config,
                client=client,
            )

    run_id = result["id"]
    assert result["status"] == "completed"

    pages = await temp_state.get_source_pages(run_id)
    page_urls = [p["url"] for p in pages]
    page_types = {p["url"]: p["page_type"] for p in pages}

    # 1. Real about and leadership pages were fetched
    assert "https://globalfintech.com/in/about" in page_urls
    assert page_types["https://globalfintech.com/in/about"] == "about"

    assert "https://globalfintech.com/in/company/leadership" in page_urls
    assert page_types["https://globalfintech.com/in/company/leadership"] == "team"

    # 2. Case studies, guides, and non-genuine pages were NOT fetched
    for url in page_urls:
        assert "/customers/" not in url, f"Customer case study fetched: {url}"
        assert "/guides/" not in url, f"Guide page fetched: {url}"
        assert "/money-management/" not in url, f"Money management page fetched: {url}"

    # Total pages fetched = 3 (home, about, leadership)
    assert len(pages) == 3


@pytest.mark.asyncio
async def test_task_d_page_discovery_no_about_team_honest_reporting(temp_state, test_config):
    """TASK D: Add a fixture with NO about/team link.
    Assert:
    - Honest 'no about/team page found' recorded in details_json.
    - Zero about/team pages fetched (no padding with unrelated pages).
    - Stage completed cleanly.
    """
    domain = "stealthfirm.io"
    email = "inquiries@stealthfirm.io"

    homepage_html = """
    <!DOCTYPE html>
    <html>
    <head><title>Stealth Firm</title></head>
    <body>
        <nav>
            <a href="/in/customers/alpha">Customer Alpha</a>
            <a href="/in/pricing">Pricing Plans</a>
            <a href="/in/docs">Developer Documentation</a>
            <a href="/in/careers">Open Positions</a>
        </nav>
        <main>
            <h1>Stealth Platform</h1>
            <p>Private corporate infrastructure.</p>
        </main>
    </body>
    </html>
    """

    def mock_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if url_str.endswith("/robots.txt"):
            return httpx.Response(200, text="User-agent: *\nAllow: /\n", headers={"content-type": "text/plain"})
        if url_str in ("https://stealthfirm.io", "https://stealthfirm.io/"):
            return httpx.Response(200, text=homepage_html, headers={"content-type": "text/html"})
        return httpx.Response(404, text="Not Found")

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with patch("harvey.single_domain_research._check_dns", new=AsyncMock(return_value=(True, ""))):
            result = await run_single_email_research(
                email,
                state=temp_state,
                config=test_config,
                client=client,
            )

    run_id = result["id"]
    assert result["status"] == "completed"

    stages = await temp_state.get_research_stages(run_id)
    st4 = next(s for s in stages if s["stage_name"] == "page_discovery")
    assert st4["status"] == "completed"
    details = json.loads(st4["details_json"])
    assert details["genuine_count"] == 0
    assert details["message"] == "no about/team page found"

    # Only homepage was fetched - zero unrelated pages were padded!
    pages = await temp_state.get_source_pages(run_id)
    assert len(pages) == 1
    assert pages[0]["page_type"] == "home"

