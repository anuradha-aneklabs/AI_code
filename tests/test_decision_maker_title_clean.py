"""Tests for decision maker title cleanliness and non-person nav filtering (Part 1a & 1b)."""

import pytest
from bs4 import BeautifulSoup

from harvey.decision_makers import (
    DecisionMakerCandidate,
    DecisionMakerRolesConfig,
    _is_valid_person_name,
    categorize_title,
    extract_people_from_cards_and_headings,
    load_roles_config,
)


def test_is_valid_person_name_rejects_delimiter_chars():
    """Names containing |, /, \\, @ must be rejected."""
    assert not _is_valid_person_name("Landing Pages | CRO")
    assert not _is_valid_person_name("Jane Doe / Manager")
    assert not _is_valid_person_name("John Doe \\ Founder")
    assert not _is_valid_person_name("contact@example.com")
    assert not _is_valid_person_name("John Doe <CTO>")


def test_is_valid_person_name_rejects_marketing_and_nav_words():
    """Names containing marketing or nav words loaded from config must be rejected."""
    config = load_roles_config()
    assert not _is_valid_person_name("Landing Pages", config)
    assert not _is_valid_person_name("Lead Gen", config)
    assert not _is_valid_person_name("Cookie Policy", config)
    assert not _is_valid_person_name("Privacy Terms", config)
    assert not _is_valid_person_name("Case Study", config)


def test_is_valid_person_name_accepts_real_names():
    """Real human names pass validation."""
    config = load_roles_config()
    assert _is_valid_person_name("Amar Mokashi", config)
    assert _is_valid_person_name("Pradnya Rekha", config)
    assert _is_valid_person_name("Hansanika Sulakshi", config)
    assert _is_valid_person_name("Anton Siribaddana", config)


def test_card_title_does_not_contain_name():
    """Card finding title must be strictly the job title, NEVER the person's name."""
    html = """
    <div class="team-card">
      <div class="dataWrap">
        <h2>Amar Mokashi</h2>
        <h4>Co-founder & CFO</h4>
      </div>
    </div>
    """
    config = load_roles_config()
    candidates = extract_people_from_cards_and_headings(
        raw_html=html,
        source_url="https://example.com/team",
        page_id="page_1",
        domain="example.com",
        config=config,
    )
    assert len(candidates) >= 1
    cand = candidates[0]
    assert cand.full_name == "Amar Mokashi"
    assert cand.title == "Co-founder & CFO"
    assert not cand.title.lower().startswith(cand.full_name.lower())
    assert "Amar Mokashi" in cand.evidence_text
    assert "Co-founder & CFO" in cand.evidence_text
    assert cand.verification_status == "verified"


def test_nav_fixture_landing_pages_cro_rejected():
    """Nav fixture 'Landing Pages | CRO' must not produce a decision maker."""
    html = """
    <nav class="site-nav">
      <div class="nav-item">
        <h3>Landing Pages | CRO</h3>
        <p>CRO</p>
      </div>
    </nav>
    """
    config = load_roles_config()
    candidates = extract_people_from_cards_and_headings(
        raw_html=html,
        source_url="https://example.com",
        page_id="page_nav",
        domain="example.com",
        config=config,
    )
    assert len(candidates) == 0


def test_property_title_never_starts_with_full_name():
    """Property test: For every candidate row, title does not start with full_name, and verified evidence contains both."""
    html = """
    <section class="leadership">
      <div class="member">
        <h3>Jane Smith</h3>
        <p class="role">Jane Smith - Chief Executive Officer</p>
      </div>
      <div class="member">
        <h3>John Doe</h3>
        <p class="role">Chief Technology Officer</p>
      </div>
    </section>
    """
    config = load_roles_config()
    candidates = extract_people_from_cards_and_headings(
        raw_html=html,
        source_url="https://example.com/about",
        page_id="page_lead",
        domain="example.com",
        config=config,
    )
    assert len(candidates) >= 2
    for c in candidates:
        assert not c.title.lower().startswith(c.full_name.lower()), f"Title '{c.title}' started with name '{c.full_name}'"
        if c.verification_status == "verified":
            assert c.full_name.lower() in c.evidence_text.lower()
            assert c.title.lower() in c.evidence_text.lower()
