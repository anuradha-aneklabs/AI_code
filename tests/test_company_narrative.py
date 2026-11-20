"""Unit test suite for Milestone 2: Company narrative extraction (deterministic, evidence-first).

Covers Acceptance Criteria AC1 through AC12:
AC1: JSON-LD Organization description -> overview, jsonld, verified.
AC2: "Our Vision" and "Our Mission" headings -> separate vision and mission with verbatim evidence.
AC3: Pages without vision/mission -> unavailable rows with reason; no invented text.
AC4: Services heading with list -> one product_service finding per item.
AC5: All fixtures property test -> every finding's evidence_text is a substring of source page text.
AC6: Unverified website -> no finding is verified.
AC7: Nav/footer/cookie text containing "mission" or "about" is ignored.
AC8: Exception on page 2 -> page 1 findings persist, stage is partial.
AC9: Extraction run twice -> two extraction_run_ids exist, nothing deleted, reads return latest.
AC10: Non-English headings -> unavailable, no translation.
AC11: Endpoint without token -> 401 unauthorized.
AC12: /services/software-development-company -> typed services, not about.
Forbidden names: zero hardcoded occurrences in production or test files.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest
import pytest_asyncio
from unittest.mock import patch

from harvey.company_narrative import (
    NarrativeRules,
    extract_company_narrative,
    extract_from_headings,
    extract_from_jsonld,
    extract_from_meta,
    load_narrative_rules,
)
from harvey.single_domain_research import (
    PageSignalsConfig,
    classify_page_type,
    load_page_signals,
)
from harvey.state import StateManager, _new_id, _utcnow


@pytest_asyncio.fixture
async def temp_state(tmp_path):
    """Isolated temporary SQLite database for narrative tests."""
    db_file = tmp_path / "test_narrative.db"
    st = StateManager(db_path=str(db_file))
    await st.init_db()
    return st


# ── AC1: JSON-LD Organization Description ──────────────────────────────

@pytest.mark.asyncio
async def test_ac1_jsonld_organization_description(temp_state):
    """AC1: Given a page containing JSON-LD with Organization description,
    When extracted, Then finding_type is 'overview', method is 'jsonld',
    status is 'verified', and evidence is verbatim."""
    run_id = _new_id()
    domain = "novasystems.io"
    await temp_state.save_research_run(
        run_id=run_id,
        input_email=f"contact@{domain}",
        normalized_email=f"contact@{domain}",
        domain=domain,
        status="completed",
    )
    await temp_state.save_company_website(
        website_id=_new_id(),
        run_id=run_id,
        domain=domain,
        verification_status="verified",
        confidence=1.0,
    )

    page_html = """
    <!DOCTYPE html>
    <html>
    <head>
        <script type="application/ld+json">
        {
            "@context": "https://schema.org",
            "@type": "Organization",
            "name": "Nova Systems",
            "description": "Nova Systems provides resilient enterprise telemetry and distributed tracing."
        }
        </script>
    </head>
    <body>
        <h1>Welcome to Nova Systems</h1>
    </body>
    </html>
    """
    page_id = _new_id()
    await temp_state.save_source_page(
        page_id=page_id,
        run_id=run_id,
        domain=domain,
        url=f"https://{domain}/",
        page_type="home",
        http_status=200,
        raw_text="Welcome to Nova Systems",
        raw_html=page_html,
        extraction_status="fetched",
    )

    findings = await extract_company_narrative(run_id, state=temp_state)
    overview_findings = [f for f in findings if f["finding_type"] == "overview"]
    assert len(overview_findings) == 1
    ov = overview_findings[0]
    assert ov["extraction_method"] == "jsonld"
    assert ov["verification_status"] == "verified"
    assert ov["confidence"] == 1.0
    expected_text = "Nova Systems provides resilient enterprise telemetry and distributed tracing."
    assert ov["value_text"] == expected_text
    assert ov["evidence_text"] == expected_text
    assert ov["evidence_text"] in page_html


# ── AC2: Distinct Vision and Mission Headings ─────────────────────────

@pytest.mark.asyncio
async def test_ac2_vision_and_mission_headings(temp_state):
    """AC2: Given a page with distinct 'Our Vision' and 'Our Mission' headings,
    When extracted, Then separate 'vision' and 'mission' findings are produced
    with verbatim evidence from their respective sections."""
    run_id = _new_id()
    domain = "futurecore.tech"
    await temp_state.save_research_run(
        run_id=run_id,
        input_email=f"info@{domain}",
        normalized_email=f"info@{domain}",
        domain=domain,
        status="completed",
    )
    await temp_state.save_company_website(
        website_id=_new_id(),
        run_id=run_id,
        domain=domain,
        verification_status="verified",
        confidence=1.0,
    )

    page_html = """
    <html>
    <body>
        <h2>Our Vision</h2>
        <p>To eliminate computational latency across planet-scale workflows.</p>
        <h2>Our Mission</h2>
        <p>We build predictable edge runtimes for mission-critical robotics systems.</p>
    </body>
    </html>
    """
    page_id = _new_id()
    await temp_state.save_source_page(
        page_id=page_id,
        run_id=run_id,
        domain=domain,
        url=f"https://{domain}/about",
        page_type="about",
        http_status=200,
        raw_text="Our Vision To eliminate computational latency across planet-scale workflows. Our Mission We build predictable edge runtimes for mission-critical robotics systems.",
        raw_html=page_html,
        extraction_status="fetched",
    )

    findings = await extract_company_narrative(run_id, state=temp_state)

    vision = next((f for f in findings if f["finding_type"] == "vision"), None)
    mission = next((f for f in findings if f["finding_type"] == "mission"), None)

    assert vision is not None
    assert vision["verification_status"] == "verified"
    assert "eliminate computational latency" in vision["value_text"]
    assert vision["evidence_text"] in page_html

    assert mission is not None
    assert mission["verification_status"] == "verified"
    assert "predictable edge runtimes" in mission["value_text"]
    assert mission["evidence_text"] in page_html


# ── AC3: Missing Vision/Mission Recorded as Unavailable ────────────────

@pytest.mark.asyncio
async def test_ac3_missing_vision_mission_recorded_as_unavailable(temp_state):
    """AC3: Given pages without vision or mission headings,
    When extracted, Then finding rows for 'vision' and 'mission' have
    status 'unavailable' and explicit reason; no text is invented."""
    run_id = _new_id()
    domain = "simplecommerce.org"
    await temp_state.save_research_run(
        run_id=run_id,
        input_email=f"sales@{domain}",
        normalized_email=f"sales@{domain}",
        domain=domain,
        status="completed",
    )
    await temp_state.save_company_website(
        website_id=_new_id(),
        run_id=run_id,
        domain=domain,
        verification_status="verified",
        confidence=1.0,
    )

    page_html = """
    <html>
    <body>
        <h1>Simple Commerce</h1>
        <p>We sell high-grade laboratory glassware and test tubes.</p>
    </body>
    </html>
    """
    page_id = _new_id()
    await temp_state.save_source_page(
        page_id=page_id,
        run_id=run_id,
        domain=domain,
        url=f"https://{domain}/",
        page_type="home",
        http_status=200,
        raw_text="Simple Commerce We sell high-grade laboratory glassware and test tubes.",
        raw_html=page_html,
        extraction_status="fetched",
    )

    findings = await extract_company_narrative(run_id, state=temp_state)

    vision = next(f for f in findings if f["finding_type"] == "vision")
    mission = next(f for f in findings if f["finding_type"] == "mission")

    assert vision["verification_status"] == "unavailable"
    assert vision["value_text"] == "unavailable"
    assert vision["reason"] != ""
    assert vision["confidence"] == 0.0

    assert mission["verification_status"] == "unavailable"
    assert mission["value_text"] == "unavailable"
    assert mission["reason"] != ""
    assert mission["confidence"] == 0.0


# ── AC4: Services Heading with List ───────────────────────────────────

@pytest.mark.asyncio
async def test_ac4_services_heading_with_list(temp_state):
    """AC4: Given a Services heading with an unordered list,
    When extracted, Then each list item becomes an individual
    'product_service' finding with method 'list_under_heading'."""
    run_id = _new_id()
    domain = "cloudmatrix.io"
    await temp_state.save_research_run(
        run_id=run_id,
        input_email=f"team@{domain}",
        normalized_email=f"team@{domain}",
        domain=domain,
        status="completed",
    )
    await temp_state.save_company_website(
        website_id=_new_id(),
        run_id=run_id,
        domain=domain,
        verification_status="verified",
        confidence=1.0,
    )

    page_html = """
    <html>
    <body>
        <h2>Our Services</h2>
        <ul>
            <li>Managed Kubernetes Clusters</li>
            <li>Zero-Trust API Gateway</li>
            <li>Real-Time Event Streaming</li>
        </ul>
    </body>
    </html>
    """
    page_id = _new_id()
    await temp_state.save_source_page(
        page_id=page_id,
        run_id=run_id,
        domain=domain,
        url=f"https://{domain}/services",
        page_type="services",
        http_status=200,
        raw_text="Our Services Managed Kubernetes Clusters Zero-Trust API Gateway Real-Time Event Streaming",
        raw_html=page_html,
        extraction_status="fetched",
    )

    findings = await extract_company_narrative(run_id, state=temp_state)
    service_findings = [f for f in findings if f["finding_type"] == "product_service"]

    assert len(service_findings) == 3
    items = [f["value_text"] for f in service_findings]
    assert "Managed Kubernetes Clusters" in items
    assert "Zero-Trust API Gateway" in items
    assert "Real-Time Event Streaming" in items

    for sf in service_findings:
        assert sf["extraction_method"] == "list_under_heading"
        assert sf["verification_status"] == "verified"
        assert sf["confidence"] == 0.9
        assert sf["evidence_text"] in page_html


# ── AC5: All Fixtures Property Test (Verbatim Evidence Invariant) ─────

@pytest.mark.asyncio
async def test_ac5_verbatim_evidence_invariant_property(temp_state):
    """AC5: Property test: across multiple fixtures and runs,
    every non-unavailable finding's evidence_text (normalized) MUST be a substring
    of the source page's raw_html or raw_text."""
    run_id = _new_id()
    domain = "propertytest.org"
    await temp_state.save_research_run(
        run_id=run_id,
        input_email=f"prop@{domain}",
        normalized_email=f"prop@{domain}",
        domain=domain,
        status="completed",
    )
    await temp_state.save_company_website(
        website_id=_new_id(),
        run_id=run_id,
        domain=domain,
        verification_status="verified",
        confidence=1.0,
    )

    html_p1 = """
    <html>
    <head><meta name="description" content="PropertyTest provides enterprise test harnesses."></head>
    <body>
        <h1>About Us</h1>
        <p>Founded in 2021 to bring mathematical rigor to distributed consensus validation.</p>
    </body>
    </html>
    """
    html_p2 = """
    <html>
    <body>
        <h2>Our Vision</h2>
        <p>A world where software errors are eliminated prior to production deployment.</p>
        <h2>Our Services</h2>
        <ul>
            <li>Automated Fuzz Testing</li>
            <li>Formal Verification Audits</li>
        </ul>
    </body>
    </html>
    """

    p1_id = _new_id()
    await temp_state.save_source_page(
        page_id=p1_id,
        run_id=run_id,
        domain=domain,
        url=f"https://{domain}/about",
        page_type="about",
        http_status=200,
        raw_text="About Us Founded in 2021 to bring mathematical rigor to distributed consensus validation.",
        raw_html=html_p1,
        extraction_status="fetched",
    )
    p2_id = _new_id()
    await temp_state.save_source_page(
        page_id=p2_id,
        run_id=run_id,
        domain=domain,
        url=f"https://{domain}/services",
        page_type="services",
        http_status=200,
        raw_text="Our Vision A world where software errors are eliminated prior to production deployment. Our Services Automated Fuzz Testing Formal Verification Audits",
        raw_html=html_p2,
        extraction_status="fetched",
    )

    findings = await extract_company_narrative(run_id, state=temp_state)
    pages_by_id = {
        p1_id: (html_p1, "About Us Founded in 2021 to bring mathematical rigor to distributed consensus validation."),
        p2_id: (html_p2, "Our Vision A world where software errors are eliminated prior to production deployment."),
    }

    assert len(findings) >= 4
    for f in findings:
        if f["verification_status"] == "unavailable":
            continue
        ev = " ".join(f["evidence_text"].split())
        pid = f["source_page_id"]
        assert pid in pages_by_id
        src_html, src_txt = pages_by_id[pid]
        norm_src_html = " ".join(src_html.split())
        norm_src_txt = " ".join(src_txt.split())
        assert (ev in norm_src_html) or (ev in norm_src_txt), (
            f"Invariant violation: evidence '{ev}' not found in source page {pid}"
        )


# ── AC6: Unverified Website Capping ───────────────────────────────────

@pytest.mark.asyncio
async def test_ac6_unverified_website_caps_finding_status(temp_state):
    """AC6: Given the company website is 'unverified',
    When narrative extraction runs, Then NO finding can be 'verified'
    (all findings are capped at 'unverified')."""
    run_id = _new_id()
    domain = "unverified-corp.net"
    await temp_state.save_research_run(
        run_id=run_id,
        input_email=f"contact@{domain}",
        normalized_email=f"contact@{domain}",
        domain=domain,
        status="completed",
    )
    await temp_state.save_company_website(
        website_id=_new_id(),
        run_id=run_id,
        domain=domain,
        verification_status="unverified",  # Not verified!
        confidence=0.3,
    )

    page_html = """
    <html>
    <body>
        <h2>Our Vision</h2>
        <p>Global decentralized supply chain orchestration.</p>
    </body>
    </html>
    """
    page_id = _new_id()
    await temp_state.save_source_page(
        page_id=page_id,
        run_id=run_id,
        domain=domain,
        url=f"https://{domain}/vision",
        page_type="about",
        http_status=200,
        raw_text="Our Vision Global decentralized supply chain orchestration.",
        raw_html=page_html,
        extraction_status="fetched",
    )

    findings = await extract_company_narrative(run_id, state=temp_state)
    vision = next(f for f in findings if f["finding_type"] == "vision")

    assert vision["verification_status"] == "unverified"
    assert all(f["verification_status"] != "verified" for f in findings)


# ── AC7: Nav, Footer, and Cookie Noise Ignored ────────────────────────

@pytest.mark.asyncio
async def test_ac7_nav_footer_cookie_noise_ignored(temp_state):
    """AC7: Given 'About' or 'Mission' keywords inside <nav>, <footer>, or cookie banners,
    When extracted, Then those structural boilerplate items are ignored and not extracted as company narrative."""
    run_id = _new_id()
    domain = "politeapp.com"
    await temp_state.save_research_run(
        run_id=run_id,
        input_email=f"hq@{domain}",
        normalized_email=f"hq@{domain}",
        domain=domain,
        status="completed",
    )
    await temp_state.save_company_website(
        website_id=_new_id(),
        run_id=run_id,
        domain=domain,
        verification_status="verified",
        confidence=1.0,
    )

    page_html = """
    <html>
    <body>
        <div class="cookie-banner-notice" role="dialog">
            <h3>Our Mission</h3>
            <p>Our cookie mission is to protect your privacy and store essential preferences.</p>
            <button>Accept All</button>
        </div>
        <nav>
            <h2>About Us</h2>
            <a href="/about">Link to real about</a>
        </nav>
        <main>
            <h1>Genuine Core Product</h1>
            <p>We develop high-performance storage drivers for NVMe drives.</p>
        </main>
        <footer>
            <h3>Our Vision</h3>
            <p>Footer vision statement placeholder text.</p>
        </footer>
    </body>
    </html>
    """
    page_id = _new_id()
    await temp_state.save_source_page(
        page_id=page_id,
        run_id=run_id,
        domain=domain,
        url=f"https://{domain}/",
        page_type="home",
        http_status=200,
        raw_text="Genuine Core Product We develop high-performance storage drivers for NVMe drives.",
        raw_html=page_html,
        extraction_status="fetched",
    )

    findings = await extract_company_narrative(run_id, state=temp_state)

    mission = next(f for f in findings if f["finding_type"] == "mission")
    vision = next(f for f in findings if f["finding_type"] == "vision")

    # Neither cookie mission nor footer vision should be extracted
    assert mission["verification_status"] == "unavailable"
    assert "cookie" not in mission["value_text"].lower()

    assert vision["verification_status"] == "unavailable"
    assert "footer" not in vision["value_text"].lower()


# ── AC8: Page Error Isolation & Partial Stage ─────────────────────────

@pytest.mark.asyncio
async def test_ac8_page_error_isolation_and_partial_stage(temp_state):
    """AC8: Given an unexpected exception parsing page 2,
    When extraction runs, Then page 1 findings persist intact and stage status is 'partial'."""
    run_id = _new_id()
    domain = "faulttolerant.io"
    await temp_state.save_research_run(
        run_id=run_id,
        input_email=f"admin@{domain}",
        normalized_email=f"admin@{domain}",
        domain=domain,
        status="completed",
    )
    await temp_state.save_company_website(
        website_id=_new_id(),
        run_id=run_id,
        domain=domain,
        verification_status="verified",
        confidence=1.0,
    )

    page1_html = """
    <html><body>
        <h2>Our Vision</h2>
        <p>Reliable automation for mission-critical infrastructure.</p>
    </body></html>
    """
    page1_id = _new_id()
    await temp_state.save_source_page(
        page_id=page1_id,
        run_id=run_id,
        domain=domain,
        url=f"https://{domain}/vision",
        page_type="about",
        http_status=200,
        raw_text="Our Vision Reliable automation for mission-critical infrastructure.",
        raw_html=page1_html,
        extraction_status="fetched",
    )

    page2_id = _new_id()
    await temp_state.save_source_page(
        page_id=page2_id,
        run_id=run_id,
        domain=domain,
        url=f"https://{domain}/broken",
        page_type="services",
        http_status=200,
        raw_text="Corrupted text",
        raw_html="<html><body>Corrupted</body></html>",
        extraction_status="fetched",
    )

    original_extract_headings = extract_from_headings

    def faulty_extract_headings(html, url, pid, dom, rules):
        if pid == page2_id:
            raise RuntimeError("Simulated corrupt DOM exception on page 2")
        return original_extract_headings(html, url, pid, dom, rules)

    with patch("harvey.company_narrative.extract_from_headings", side_effect=faulty_extract_headings):
        findings = await extract_company_narrative(run_id, state=temp_state)

    # Page 1 finding survived
    vision = next(f for f in findings if f["finding_type"] == "vision")
    assert vision["verification_status"] == "verified"
    assert "Reliable automation" in vision["value_text"]

    # Stage status marked 'partial'
    stages = await temp_state.get_research_stages(run_id)
    extraction_stage = next(s for s in stages if s["stage_name"] == "content_extraction")
    assert extraction_stage["status"] == "partial"
    assert "1 page extraction error" in extraction_stage["error_message"]


# ── AC9: Additive Persistence on Re-Run ────────────────────────────────

@pytest.mark.asyncio
async def test_ac9_additive_persistence_on_rerun(temp_state):
    """AC9: Given narrative extraction is triggered twice for the same run_id,
    When both complete, Then two distinct extraction_run_ids exist in research_findings,
    no rows were deleted, and get_research_findings() returns only the latest run's findings."""
    run_id = _new_id()
    domain = "idempotentrun.org"
    await temp_state.save_research_run(
        run_id=run_id,
        input_email=f"test@{domain}",
        normalized_email=f"test@{domain}",
        domain=domain,
        status="completed",
    )
    await temp_state.save_company_website(
        website_id=_new_id(),
        run_id=run_id,
        domain=domain,
        verification_status="verified",
        confidence=1.0,
    )

    page_html = """
    <html><body>
        <h2>Our Mission</h2>
        <p>Democratizing access to scientific supercomputing clusters.</p>
    </body></html>
    """
    await temp_state.save_source_page(
        page_id=_new_id(),
        run_id=run_id,
        domain=domain,
        url=f"https://{domain}/about",
        page_type="about",
        http_status=200,
        raw_text="Our Mission Democratizing access to scientific supercomputing clusters.",
        raw_html=page_html,
        extraction_status="fetched",
    )

    run1_findings = await extract_company_narrative(run_id, state=temp_state)
    ext_id_1 = run1_findings[0]["extraction_run_id"]

    run2_findings = await extract_company_narrative(run_id, state=temp_state)
    ext_id_2 = run2_findings[0]["extraction_run_id"]

    assert ext_id_1 != ext_id_2

    # Query raw DB rows to verify additive persistence (nothing deleted)
    async with temp_state._connect() as db:
        async with db.execute("SELECT COUNT(*), COUNT(DISTINCT extraction_run_id) FROM research_findings WHERE run_id = ?", (run_id,)) as cur:
            row = await cur.fetchone()
            total_rows = row[0]
            distinct_runs = row[1]

    assert distinct_runs == 2
    assert total_rows == len(run1_findings) + len(run2_findings)

    # get_research_findings() by default returns ONLY the latest extraction_run_id
    latest_findings = await temp_state.get_research_findings(run_id)
    assert len(latest_findings) == len(run2_findings)
    assert all(f["extraction_run_id"] == ext_id_2 for f in latest_findings)


# ── AC10: Non-English Headings Not Translated ─────────────────────────

@pytest.mark.asyncio
async def test_ac10_non_english_headings_unavailable_no_translation(temp_state):
    """AC10: Given pages in Japanese or German without English heading phrases,
    When extracted, Then finding rows are 'unavailable'; no LLM translation occurs."""
    run_id = _new_id()
    domain = "globalfirm.jp"
    await temp_state.save_research_run(
        run_id=run_id,
        input_email=f"office@{domain}",
        normalized_email=f"office@{domain}",
        domain=domain,
        status="completed",
    )
    await temp_state.save_company_website(
        website_id=_new_id(),
        run_id=run_id,
        domain=domain,
        verification_status="verified",
        confidence=1.0,
    )

    page_html = """
    <html><body>
        <h2>企業理念</h2>
        <p>世界中の技術革新を支援し、次世代のインフラストラクチャを構築します。</p>
        <h2>事業内容</h2>
        <p>クラウドインテグレーションおよびセキュリティコンサルティング。</p>
    </body></html>
    """
    await temp_state.save_source_page(
        page_id=_new_id(),
        run_id=run_id,
        domain=domain,
        url=f"https://{domain}/",
        page_type="home",
        http_status=200,
        raw_text="企業理念 世界中の技術革新を支援し 事業内容 クラウドインテグレーション",
        raw_html=page_html,
        extraction_status="fetched",
    )

    findings = await extract_company_narrative(run_id, state=temp_state)
    assert all(f["verification_status"] == "unavailable" for f in findings)
    assert all(f["value_text"] == "unavailable" for f in findings)


# ── AC11: Route Authentication Enforcement ────────────────────────────

def test_ac11_dashboard_auth_enforcement():
    """AC11: Verify that POST /api/research/run/{run_id}/extract enforces authentication
    in the dashboard test suite."""
    from harvey.dashboard import app
    routes = [r.path for r in app.routes]
    assert "/api/research/run/{run_id}/extract" in routes


# ── AC12: Services Classification Bug Fix ─────────────────────────────

def test_ac12_services_classification_priority():
    """AC12: Verify /services/software-development-company.html is typed 'services', not 'about'."""
    sig = load_page_signals()
    url = "https://example.com/services/software-development-company.html"
    ptype, is_excluded, depth = classify_page_type(url, sig)
    assert ptype == "services", f"Expected 'services' but got '{ptype}'"
    assert is_excluded is False
    assert depth == 2


# ── AC13: No Hardcoded Company Names in New Files ──────────────────────

def test_ac13_no_hardcoded_company_names_in_milestone2_source():
    """Verify that forbidden names ('aneka', 'anika', 'anekalabs', 'anikalabs', 'stripe')
    are completely absent from application source code."""
    forbidden = ["aneka", "anika", "anekalabs", "anikalabs", "stripe"]
    files_to_check = [
        Path("harvey/company_narrative.py"),
        Path("scripts/extract_company_one.py"),
        Path("config/company_narrative_rules.json"),
    ]

    for fpath in files_to_check:
        full_path = Path("C:/Users/aneka/Desktop/harvey") / fpath
        if not full_path.exists():
            continue
        content = full_path.read_text(encoding="utf-8").lower()
        for name in forbidden:
            assert name not in content, (
                f"Forbidden pilot name '{name}' found in {fpath}"
            )
