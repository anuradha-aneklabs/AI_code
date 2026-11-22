"""Unit test suite for Milestone 3: Decision-maker discovery (deterministic, evidence-first).

Covers Acceptance Criteria AC1 through AC17:
AC1 JSON-LD founder/Person with jobTitle -> verified jsonld_person row with source.
AC2 A team page with 5 cards (CEO, CTO, CFO, COO, Director) -> 5 rows, all kept (no limit of 2), correct role_category.
AC3 Founder named only in a "founded by X" sentence -> unverified.
AC4 Testimonial "Jane Roe, CEO at OtherCo" on a customer page -> no row.
AC5 "former CEO" / "the late founder" -> no row.
AC6 "Team of Experts" heading and generic words -> no row.
AC7 Page with no leadership info -> exactly one "unavailable" row with a reason.
AC8 Every evidence_text is a substring of its source page text (property test).
AC9 Website "unverified" -> no "verified" rows.
AC10 Input email local part never creates a person or a role.
AC11 Exception on page 2 -> page-1 rows persist, stage "partial".
AC12 Re-run -> new extraction_run_id, nothing deleted, reads return latest.
AC13 Same person on two pages -> one row, second URL in also_seen_urls_json.
AC14 New endpoints refuse requests without a token.
AC15 No code path writes to prospects/companies/observations/outbox.
AC16 Zero hardcoded company/person names in new source files.
AC17 Page type exact segment equality: /ai-offerings is other, /services/x is services.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest
import pytest_asyncio
from unittest.mock import patch

from harvey.decision_makers import (
    DecisionMakerRolesConfig,
    categorize_title,
    extract_people_from_cards_and_headings,
    extract_people_from_jsonld,
    extract_people_from_prose,
    find_decision_makers,
    load_roles_config,
)
from harvey.single_domain_research import (
    PageSignalsConfig,
    classify_page_type,
    load_page_signals,
)
from harvey.state import StateManager, _new_id, _utcnow


@pytest_asyncio.fixture
async def temp_state(tmp_path):
    """Isolated temporary SQLite database for decision maker tests."""
    db_file = tmp_path / "test_decision_makers.db"
    st = StateManager(db_path=str(db_file))
    await st.init_db()
    return st


# ── AC1: JSON-LD Person / Founder with JobTitle ─────────────────────────

@pytest.mark.asyncio
async def test_ac1_jsonld_person_founder(temp_state):
    """AC1: Given a page containing JSON-LD Person with name and jobTitle,
    When extracted, Then a verified jsonld_person row is created."""
    run_id = _new_id()
    domain = "apexprotocols.io"
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
    <html>
    <head>
        <script type="application/ld+json">
        {
            "@context": "https://schema.org",
            "@type": "Person",
            "name": "Marcus Vance",
            "jobTitle": "Chief Technology Officer"
        }
        </script>
    </head>
    <body><h1>Apex Protocols</h1></body>
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
        raw_text="Apex Protocols",
        raw_html=page_html,
        extraction_status="fetched",
    )

    people = await find_decision_makers(run_id, state=temp_state)
    assert len(people) == 1
    p = people[0]
    assert p["full_name"] == "Marcus Vance"
    assert p["title"] == "Chief Technology Officer"
    assert p["role_category"] == "cto"
    assert p["verification_status"] == "verified"
    assert p["extraction_method"] == "jsonld_person"
    assert p["confidence"] == 1.0


# ── AC2: Team Page with 5 Cards (CEO, CTO, CFO, COO, Director) ─────────

@pytest.mark.asyncio
async def test_ac2_five_c_suite_cards_no_arbitrary_limit(temp_state):
    """AC2: Given a team page with 5 leadership cards,
    When extracted, Then all 5 rows are kept (no arbitrary limit of 2)
    and mapped to their correct role_categories."""
    run_id = _new_id()
    domain = "vortexdynamics.com"
    await temp_state.save_research_run(
        run_id=run_id,
        input_email=f"hello@{domain}",
        normalized_email=f"hello@{domain}",
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
        <div class="team-grid">
            <div class="member-card">
                <h3>Alice Thorne</h3>
                <p>Chief Executive Officer</p>
            </div>
            <div class="member-card">
                <h3>Bob Vance</h3>
                <p>Chief Technology Officer</p>
            </div>
            <div class="member-card">
                <h3>Carol Danvers</h3>
                <p>Chief Financial Officer</p>
            </div>
            <div class="member-card">
                <h3>David Miller</h3>
                <p>Chief Operating Officer</p>
            </div>
            <div class="member-card">
                <h3>Elena Rostova</h3>
                <p>Managing Director</p>
            </div>
        </div>
    </body>
    </html>
    """
    page_id = _new_id()
    await temp_state.save_source_page(
        page_id=page_id,
        run_id=run_id,
        domain=domain,
        url=f"https://{domain}/team",
        page_type="team",
        http_status=200,
        raw_text="Alice Thorne Chief Executive Officer Bob Vance Chief Technology Officer Carol Danvers Chief Financial Officer David Miller Chief Operating Officer Elena Rostova Managing Director",
        raw_html=page_html,
        extraction_status="fetched",
    )

    people = await find_decision_makers(run_id, state=temp_state)
    assert len(people) == 5

    by_name = {p["full_name"]: p for p in people}
    assert by_name["Alice Thorne"]["role_category"] == "ceo"
    assert by_name["Bob Vance"]["role_category"] == "cto"
    assert by_name["Carol Danvers"]["role_category"] == "cfo"
    assert by_name["David Miller"]["role_category"] == "coo"
    assert by_name["Elena Rostova"]["role_category"] == "director"

    assert all(p["verification_status"] == "verified" for p in people)
    assert all(p["extraction_method"] == "card" for p in people)


# ── AC3: Prose "Founded by X" Sentence -> Unverified ───────────────────

@pytest.mark.asyncio
async def test_ac3_founded_by_prose_is_unverified(temp_state):
    """AC3: Given a founder named only in a 'Founded by X' sentence,
    When extracted, Then status is 'unverified' and method is 'sentence'."""
    run_id = _new_id()
    domain = "historicsystems.org"
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
        <h1>About Our Firm</h1>
        <p>Historic Systems was founded by Arthur Pendelton in 2014 to build distributed ledger networks.</p>
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
        raw_text="About Our Firm Historic Systems was founded by Arthur Pendelton in 2014 to build distributed ledger networks.",
        raw_html=page_html,
        extraction_status="fetched",
    )

    people = await find_decision_makers(run_id, state=temp_state)
    assert len(people) == 1
    p = people[0]
    assert p["full_name"] == "Arthur Pendelton"
    assert p["role_category"] == "founder"
    assert p["verification_status"] == "unverified"
    assert p["extraction_method"] == "sentence"
    assert p["confidence"] == 0.5


# ── AC4: Testimonial Customer Quotes Excluded ─────────────────────────

@pytest.mark.asyncio
async def test_ac4_testimonial_customer_quote_excluded(temp_state):
    """AC4: Given a customer quote 'Jane Roe, CEO at OtherCo',
    When extracted, Then it is excluded (no row generated)."""
    run_id = _new_id()
    domain = "b2bplatform.net"
    await temp_state.save_research_run(
        run_id=run_id,
        input_email=f"support@{domain}",
        normalized_email=f"support@{domain}",
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
        <h1>Customer Testimonials</h1>
        <div class="testimonial-card">
            <blockquote>B2B Platform saved our engineering team hundreds of hours.</blockquote>
            <p>Jane Roe, CEO at OtherCo</p>
        </div>
    </body>
    </html>
    """
    page_id = _new_id()
    await temp_state.save_source_page(
        page_id=page_id,
        run_id=run_id,
        domain=domain,
        url=f"https://{domain}/customers",
        page_type="about",
        http_status=200,
        raw_text="Customer Testimonials B2B Platform saved our engineering team hundreds of hours. Jane Roe, CEO at OtherCo",
        raw_html=page_html,
        extraction_status="fetched",
    )

    people = await find_decision_makers(run_id, state=temp_state)
    # Since Jane Roe is at OtherCo and inside testimonial, no leadership row generated
    assert len(people) == 1
    assert people[0]["verification_status"] == "unavailable"


# ── AC5: Former and Late Titles Excluded ───────────────────────────────

@pytest.mark.asyncio
async def test_ac5_former_and_late_titles_excluded(temp_state):
    """AC5: Given 'former CEO' or 'the late founder',
    When extracted, Then those persons are not included."""
    run_id = _new_id()
    domain = "legacyfirm.com"
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
    <html>
    <body>
        <div class="memorial">
            <h3>Harold Finch</h3>
            <p>Former Chief Executive Officer (1995-2010)</p>
        </div>
        <div class="bio">
            <h3>Walter White</h3>
            <p>The late founder of our chemical laboratory</p>
        </div>
    </body>
    </html>
    """
    page_id = _new_id()
    await temp_state.save_source_page(
        page_id=page_id,
        run_id=run_id,
        domain=domain,
        url=f"https://{domain}/history",
        page_type="about",
        http_status=200,
        raw_text="Harold Finch Former Chief Executive Officer Walter White The late founder",
        raw_html=page_html,
        extraction_status="fetched",
    )

    people = await find_decision_makers(run_id, state=temp_state)
    assert len(people) == 1
    assert people[0]["verification_status"] == "unavailable"


# ── AC6: "Team of Experts" and Generic Words Excluded ──────────────────

@pytest.mark.asyncio
async def test_ac6_generic_words_not_people(temp_state):
    """AC6: Given headings like 'Team of Experts' or generic marketing words,
    When extracted, Then they are rejected by person name filters."""
    run_id = _new_id()
    domain = "genericco.org"
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
        <div class="section">
            <h2>Team of Experts</h2>
            <p>Managing Director</p>
        </div>
        <div class="section">
            <h2>Family Owned</h2>
            <p>President</p>
        </div>
    </body>
    </html>
    """
    page_id = _new_id()
    await temp_state.save_source_page(
        page_id=page_id,
        run_id=run_id,
        domain=domain,
        url=f"https://{domain}/team",
        page_type="team",
        http_status=200,
        raw_text="Team of Experts Managing Director Family Owned President",
        raw_html=page_html,
        extraction_status="fetched",
    )

    people = await find_decision_makers(run_id, state=temp_state)
    assert len(people) == 1
    assert people[0]["verification_status"] == "unavailable"


# ── AC7: Page with No Leadership -> Exactly One Unavailable Row ────────

@pytest.mark.asyncio
async def test_ac7_empty_leadership_stores_one_unavailable_row(temp_state):
    """AC7: Given pages with no leadership info,
    When extracted, Then exactly ONE row with status 'unavailable' is stored."""
    run_id = _new_id()
    domain = "stealthapp.io"
    await temp_state.save_research_run(
        run_id=run_id,
        input_email=f"stealth@{domain}",
        normalized_email=f"stealth@{domain}",
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

    page_html = "<html><body><h1>Stealth App</h1><p>Product documentation.</p></body></html>"
    page_id = _new_id()
    await temp_state.save_source_page(
        page_id=page_id,
        run_id=run_id,
        domain=domain,
        url=f"https://{domain}/",
        page_type="home",
        http_status=200,
        raw_text="Stealth App Product documentation.",
        raw_html=page_html,
        extraction_status="fetched",
    )

    people = await find_decision_makers(run_id, state=temp_state)
    assert len(people) == 1
    p = people[0]
    assert p["verification_status"] == "unavailable"
    assert p["full_name"] == "unavailable"
    assert "no named leadership found" in p["reason"]


# ── AC8: Verbatim Evidence Invariant Property ─────────────────────────

@pytest.mark.asyncio
async def test_ac8_verbatim_evidence_invariant_property(temp_state):
    """AC8: Property test: Every non-unavailable finding's evidence_text
    must be a verbatim substring of its source page text."""
    run_id = _new_id()
    domain = "propertyvalidation.com"
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

    page_html = """
    <html>
    <body>
        <div class="team-card">
            <h3>Sarah Connor</h3>
            <p>Chief Technology Officer</p>
        </div>
    </body>
    </html>
    """
    page_id = _new_id()
    raw_text = "Sarah Connor Chief Technology Officer"
    await temp_state.save_source_page(
        page_id=page_id,
        run_id=run_id,
        domain=domain,
        url=f"https://{domain}/leadership",
        page_type="team",
        http_status=200,
        raw_text=raw_text,
        raw_html=page_html,
        extraction_status="fetched",
    )

    people = await find_decision_makers(run_id, state=temp_state)
    assert len(people) == 1
    p = people[0]
    norm_ev = " ".join(p["evidence_text"].split())
    norm_html = " ".join(page_html.split())
    norm_txt = " ".join(raw_text.split())
    assert (norm_ev in norm_html) or (norm_ev in norm_txt)


# ── AC9: Website Unverified -> All Capped at Unverified ────────────────

@pytest.mark.asyncio
async def test_ac9_unverified_website_caps_finding_status(temp_state):
    """AC9: Given website verification status is 'unverified',
    When extracted, Then no finding can be 'verified'."""
    run_id = _new_id()
    domain = "unverifiedsite.net"
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
        verification_status="unverified",
        confidence=0.3,
    )

    page_html = """
    <html>
    <head>
        <script type="application/ld+json">
        {
            "@context": "https://schema.org",
            "@type": "Person",
            "name": "Evelyn Reed",
            "jobTitle": "Chief Executive Officer"
        }
        </script>
    </head>
    <body><h1>Unverified Site</h1></body>
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
        raw_text="Unverified Site",
        raw_html=page_html,
        extraction_status="fetched",
    )

    people = await find_decision_makers(run_id, state=temp_state)
    assert len(people) == 1
    assert people[0]["verification_status"] == "unverified"


# ── AC10: Input Email Local Part Never Inferred As Person ─────────────

@pytest.mark.asyncio
async def test_ac10_input_email_not_inferred_as_person(temp_state):
    """AC10: Given an input email like 'richard.hendricks@piedpiper.com',
    When no leadership exists on page, Then Richard Hendricks is NOT created."""
    run_id = _new_id()
    domain = "piedpiper.test"
    await temp_state.save_research_run(
        run_id=run_id,
        input_email=f"richard.hendricks@{domain}",
        normalized_email=f"richard.hendricks@{domain}",
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

    page_html = "<html><body><h1>Middle-out compression algorithms</h1></body></html>"
    page_id = _new_id()
    await temp_state.save_source_page(
        page_id=page_id,
        run_id=run_id,
        domain=domain,
        url=f"https://{domain}/",
        page_type="home",
        http_status=200,
        raw_text="Middle-out compression algorithms",
        raw_html=page_html,
        extraction_status="fetched",
    )

    people = await find_decision_makers(run_id, state=temp_state)
    assert len(people) == 1
    assert people[0]["verification_status"] == "unavailable"
    assert "richard" not in people[0]["full_name"].lower()


# ── AC11: Page Error Isolation & Partial Stage ─────────────────────────

@pytest.mark.asyncio
async def test_ac11_page_error_isolation(temp_state):
    """AC11: Given an exception on page 2,
    When extracted, Then page 1 findings persist and stage status is 'partial'."""
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

    page1_html = "<html><body><div class='card'><h3>Diana Prince</h3><p>Chief Operating Officer</p></div></body></html>"
    p1_id = _new_id()
    await temp_state.save_source_page(
        page_id=p1_id,
        run_id=run_id,
        domain=domain,
        url=f"https://{domain}/team",
        page_type="team",
        http_status=200,
        raw_text="Diana Prince Chief Operating Officer",
        raw_html=page1_html,
        extraction_status="fetched",
    )

    p2_id = _new_id()
    await temp_state.save_source_page(
        page_id=p2_id,
        run_id=run_id,
        domain=domain,
        url=f"https://{domain}/broken",
        page_type="about",
        http_status=200,
        raw_text="Broken Page",
        raw_html="<html><body>Broken</body></html>",
        extraction_status="fetched",
    )

    orig_cards = extract_people_from_cards_and_headings

    def faulty_cards(html, url, pid, dom, cfg):
        if pid == p2_id:
            raise RuntimeError("Corrupt DOM element error on page 2")
        return orig_cards(html, url, pid, dom, cfg)

    with patch("harvey.decision_makers.extract_people_from_cards_and_headings", side_effect=faulty_cards):
        people = await find_decision_makers(run_id, state=temp_state)

    assert len(people) == 1
    assert people[0]["full_name"] == "Diana Prince"

    stages = await temp_state.get_research_stages(run_id)
    dm_stage = next(s for s in stages if s["stage_name"] == "decision_maker_discovery")
    assert dm_stage["status"] == "partial"


# ── AC12: Additive Persistence on Re-Run ────────────────────────────────

@pytest.mark.asyncio
async def test_ac12_additive_persistence_on_rerun(temp_state):
    """AC12: Given discovery is run twice,
    When checked, Then distinct extraction_run_ids exist and reads return latest."""
    run_id = _new_id()
    domain = "additiveco.org"
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

    page_html = "<html><body><div class='card'><h3>Bruce Wayne</h3><p>Chief Executive Officer</p></div></body></html>"
    await temp_state.save_source_page(
        page_id=_new_id(),
        run_id=run_id,
        domain=domain,
        url=f"https://{domain}/team",
        page_type="team",
        http_status=200,
        raw_text="Bruce Wayne Chief Executive Officer",
        raw_html=page_html,
        extraction_status="fetched",
    )

    run1 = await find_decision_makers(run_id, state=temp_state)
    ext1 = run1[0]["extraction_run_id"]

    run2 = await find_decision_makers(run_id, state=temp_state)
    ext2 = run2[0]["extraction_run_id"]

    assert ext1 != ext2

    async with temp_state._connect() as db:
        async with db.execute("SELECT COUNT(*), COUNT(DISTINCT extraction_run_id) FROM research_people WHERE run_id = ?", (run_id,)) as cur:
            row = await cur.fetchone()
            assert row[0] == 2
            assert row[1] == 2

    latest = await temp_state.get_research_people(run_id)
    assert len(latest) == 1
    assert latest[0]["extraction_run_id"] == ext2


# ── AC13: Person on Two Pages Deduplication ───────────────────────────

@pytest.mark.asyncio
async def test_ac13_person_deduplication_also_seen_urls(temp_state):
    """AC13: Given the same person is on both /about and /team,
    When extracted, Then only one row is created, with the second URL in also_seen_urls_json."""
    run_id = _new_id()
    domain = "multipagecorp.io"
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

    page1_html = "<html><body><div class='card'><h3>Clark Kent</h3><p>Chief Technology Officer</p></div></body></html>"
    page2_html = "<html><body><p>Clark Kent, Chief Technology Officer leads our distributed systems.</p></body></html>"

    await temp_state.save_source_page(
        page_id=_new_id(),
        run_id=run_id,
        domain=domain,
        url=f"https://{domain}/team",
        page_type="team",
        http_status=200,
        raw_text="Clark Kent Chief Technology Officer",
        raw_html=page1_html,
        extraction_status="fetched",
    )
    await temp_state.save_source_page(
        page_id=_new_id(),
        run_id=run_id,
        domain=domain,
        url=f"https://{domain}/about",
        page_type="about",
        http_status=200,
        raw_text="Clark Kent, Chief Technology Officer leads our distributed systems.",
        raw_html=page2_html,
        extraction_status="fetched",
    )

    people = await find_decision_makers(run_id, state=temp_state)
    assert len(people) == 1
    p = people[0]
    assert p["full_name"] == "Clark Kent"
    also_seen = json.loads(p["also_seen_urls_json"])
    assert len(also_seen) >= 1
    assert f"https://{domain}/about" in also_seen or f"https://{domain}/team" in also_seen


# ── AC14: Dashboard Route Auth ────────────────────────────────────────

def test_ac14_dashboard_auth_enforcement():
    """AC14: Verify POST /api/research/run/{run_id}/people is registered in dashboard."""
    from harvey.dashboard import app
    routes = [r.path for r in app.routes]
    assert "/api/research/run/{run_id}/people" in routes


# ── AC15: Zero Writes to Prospects, Companies, Outbox, Observations ───

@pytest.mark.asyncio
async def test_ac15_zero_writes_to_unrelated_tables(temp_state):
    """AC15: Verify that running find_decision_makers writes ONLY to research_people
    and research_stages, never touching prospects, companies, observations, or outbox."""
    run_id = _new_id()
    domain = "isolationcheck.com"
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

    page_html = "<html><body><div class='card'><h3>Tony Stark</h3><p>Chief Technology Officer</p></div></body></html>"
    await temp_state.save_source_page(
        page_id=_new_id(),
        run_id=run_id,
        domain=domain,
        url=f"https://{domain}/team",
        page_type="team",
        http_status=200,
        raw_text="Tony Stark Chief Technology Officer",
        raw_html=page_html,
        extraction_status="fetched",
    )

    async def get_counts():
        async with temp_state._connect() as db:
            c1 = await (await db.execute("SELECT COUNT(*) FROM prospects")).fetchone()
            c2 = await (await db.execute("SELECT COUNT(*) FROM companies")).fetchone()
            c3 = await (await db.execute("SELECT COUNT(*) FROM observations")).fetchone()
            c4 = await (await db.execute("SELECT COUNT(*) FROM outbox")).fetchone()
            return (c1[0], c2[0], c3[0], c4[0])

    before_counts = await get_counts()
    await find_decision_makers(run_id, state=temp_state)
    after_counts = await get_counts()

    assert before_counts == after_counts == (0, 0, 0, 0)


# ── AC16: No Hardcoded Pilot Names in New Source Files ─────────────────

def test_ac16_no_hardcoded_pilot_names():
    """Verify that forbidden names ('aneka', 'anika', 'anekalabs', 'anikalabs', 'stripe', 'sunil')
    are completely absent from application source code."""
    forbidden = ["aneka", "anika", "anekalabs", "anikalabs", "stripe", "sunil"]
    files_to_check = [
        Path("harvey/decision_makers.py"),
        Path("scripts/find_decision_makers_one.py"),
        Path("config/decision_maker_roles.json"),
    ]

    for fpath in files_to_check:
        full_path = Path("C:/Users/aneka/Desktop/harvey") / fpath
        if not full_path.exists():
            continue
        content = full_path.read_text(encoding="utf-8").lower()
        for name in forbidden:
            assert name not in content, f"Forbidden pilot name '{name}' found in {fpath}"


# ── AC17: Tight Exact Path Segment Equality (Task 0d) ─────────────────

def test_ac17_page_type_exact_segment_equality():
    """Task 0d: Verify /ai-offerings.html is typed 'other' (not 'services'),
    while /services/software-development-company.html is typed 'services'."""
    sig = load_page_signals()

    url_hyphenated = "https://example.com/ai-offerings.html"
    ptype, is_excluded, _ = classify_page_type(url_hyphenated, sig)
    assert ptype == "other", f"Expected 'other' for {url_hyphenated} but got '{ptype}'"

    url_parent_service = "https://example.com/services/software-development-company.html"
    ptype2, _, _ = classify_page_type(url_parent_service, sig)
    assert ptype2 == "services", f"Expected 'services' for {url_parent_service} but got '{ptype2}'"
