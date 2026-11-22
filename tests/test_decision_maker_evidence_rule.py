"""Tests for Milestone 3.1 Task A: Decision-maker evidence rule verification.

Rule: A decision-maker may be 'verified' only if evidence_text contains BOTH the full
name AND the title exactly as on the page.
1. For card/heading_list findings, evidence_text is built from the smallest container
   holding both name and title (normalized text, verbatim substring, capped at 300 chars).
2. If no container holds both (or name and title are > 300 chars apart), status is capped at 'unverified'.
3. Property test: every 'verified' finding across any fixture/run has both full_name and title in evidence_text.
"""

import uuid
import pytest
import pytest_asyncio
from harvey.decision_makers import (
    find_decision_makers,
    extract_people_from_cards_and_headings,
    load_roles_config,
    _normalize_ws,
)
from harvey.state import StateManager


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


@pytest_asyncio.fixture
async def temp_state(tmp_path):
    db_file = tmp_path / "test_evidence_rule.db"
    st = StateManager(str(db_file))
    await st.init_db()
    yield st


@pytest.mark.asyncio
async def test_sibling_elements_in_one_card_verified(temp_state):
    """Fixture: Sibling elements inside one card container (e.g. h3 name, p title).
    Smallest container holds both, <= 300 chars -> status is verified, evidence contains both."""
    run_id = _new_id()
    domain = "siblingcorp.example"
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
        <div class="team-grid">
            <article class="leadership-card">
                <div class="avatar" aria-hidden="true"></div>
                <h3 class="member-name">Anton Siribaddana</h3>
                <p class="member-title">Chief Technology Officer</p>
                <div class="bio">Passionate about distributed systems.</div>
            </article>
        </div>
    </body>
    </html>
    """
    raw_text = "Anton Siribaddana Chief Technology Officer Passionate about distributed systems."
    await temp_state.save_source_page(
        page_id=_new_id(),
        run_id=run_id,
        domain=domain,
        url=f"https://{domain}/about",
        page_type="about",
        http_status=200,
        raw_text=raw_text,
        raw_html=page_html,
        extraction_status="fetched",
    )

    people = await find_decision_makers(run_id, state=temp_state)
    assert len(people) == 1
    p = people[0]
    assert p["full_name"] == "Anton Siribaddana"
    assert p["title"] == "Chief Technology Officer"
    assert p["role_category"] == "cto"
    assert p["verification_status"] == "verified"
    assert p["extraction_method"] == "card"
    assert "Anton Siribaddana" in p["evidence_text"]
    assert "Chief Technology Officer" in p["evidence_text"]
    assert len(p["evidence_text"]) <= 300


@pytest.mark.asyncio
async def test_name_and_title_far_apart_unverified(temp_state):
    """Fixture: Name and title are separated by > 300 chars of filler text.
    Must be capped at 'unverified' with reason stating separation."""
    run_id = _new_id()
    domain = "farapartcorp.example"
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

    # 400 chars of filler between name and title inside the card
    filler = "Our organization has pursued comprehensive digital excellence across all operational sectors. " * 5
    page_html = f"""
    <html>
    <body>
        <div class="card">
            <h3>Sarah Connor</h3>
            <p>{filler}</p>
            <p>Chief Executive Officer</p>
        </div>
    </body>
    </html>
    """
    raw_text = f"Sarah Connor {filler} Chief Executive Officer"
    await temp_state.save_source_page(
        page_id=_new_id(),
        run_id=run_id,
        domain=domain,
        url=f"https://{domain}/team",
        page_type="team",
        http_status=200,
        raw_text=raw_text,
        raw_html=page_html,
        extraction_status="fetched",
    )

    people = await find_decision_makers(run_id, state=temp_state)
    assert len(people) == 1
    p = people[0]
    assert p["full_name"] == "Sarah Connor"
    assert p["title"] == "Chief Executive Officer"
    assert p["role_category"] == "ceo"
    # STRICT: Never verified if separated by >300 chars
    assert p["verification_status"] == "unverified"
    assert "separated" in p["reason"].lower() or "300" in p["reason"]


@pytest.mark.asyncio
async def test_property_all_verified_findings_contain_name_and_title(temp_state):
    """Property test: For ANY candidate produced with status == 'verified',
    evidence_text MUST contain BOTH full_name AND title."""
    run_id = _new_id()
    domain = "multitest.example"
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
        <section class="team">
            <article>
                <h3>Elena Rostova</h3>
                <h4>Chief Financial Officer</h4>
            </article>
            <article>
                <h3>David Miller</h3>
                <h4>Director of Product</h4>
            </article>
            <p>Rachel Green - Chief Marketing Officer</p>
        </section>
    </body>
    </html>
    """
    raw_text = "Elena Rostova Chief Financial Officer David Miller Director of Product Rachel Green - Chief Marketing Officer"
    await temp_state.save_source_page(
        page_id=_new_id(),
        run_id=run_id,
        domain=domain,
        url=f"https://{domain}/about",
        page_type="about",
        http_status=200,
        raw_text=raw_text,
        raw_html=page_html,
        extraction_status="fetched",
    )

    people = await find_decision_makers(run_id, state=temp_state)
    assert len(people) >= 2
    for p in people:
        if p["verification_status"] == "verified":
            assert _normalize_ws(p["full_name"]).lower() in _normalize_ws(p["evidence_text"]).lower(), (
                f"Full name {p['full_name']} not in evidence_text {p['evidence_text']}"
            )
            assert _normalize_ws(p["title"]).lower() in _normalize_ws(p["evidence_text"]).lower(), (
                f"Title {p['title']} not in evidence_text {p['evidence_text']}"
            )
            assert len(p["evidence_text"]) <= 300
