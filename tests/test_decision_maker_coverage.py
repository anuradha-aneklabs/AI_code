"""Unit tests for expanded decision-maker leadership coverage (Task 5).

Covers:
- HTML tables (<tr><td>Name</td><td>Title</td></tr>)
- Definition lists (<dl><dt>Name</dt><dd>Title</dd></dl>)
- Heading-sibling patterns (<h3>Name</h3><p>Title</p>)
- Prefix title patterns (<h3>Commissioner Amanda Lefton</h3>)
- Non-startup role categories: public_official, association_leader, higher_ed_leader, executive
- Strict verbatim evidence check.
"""

from __future__ import annotations

import pytest

from harvey.decision_makers import (
    DecisionMakerRolesConfig,
    categorize_title,
    extract_people_from_cards_and_headings,
    extract_people_from_definition_lists,
    extract_people_from_heading_siblings,
    extract_people_from_tables,
    load_roles_config,
)


def test_expanded_role_categories_and_mappings():
    """Verify non-startup roles are mapped to their intended categories."""
    cfg = load_roles_config()

    # Public official
    assert categorize_title("Commissioner", cfg) == "public_official"
    assert categorize_title("Deputy Commissioner", cfg) == "public_official"
    assert categorize_title("Executive Deputy Commissioner", cfg) == "public_official"
    assert categorize_title("Secretary of State", cfg) == "public_official"
    assert categorize_title("Governor", cfg) == "public_official"
    assert categorize_title("EPA Administrator", cfg) == "public_official"

    # Association leader
    assert categorize_title("Executive Director", cfg) == "association_leader"
    assert categorize_title("Board Chair", cfg) == "association_leader"
    assert categorize_title("Vice Chair", cfg) == "association_leader"
    assert categorize_title("Treasurer", cfg) == "association_leader"

    # Higher ed leader
    assert categorize_title("Chancellor", cfg) == "higher_ed_leader"
    assert categorize_title("Vice Chancellor", cfg) == "higher_ed_leader"
    assert categorize_title("Provost", cfg) == "higher_ed_leader"
    assert categorize_title("Dean of Engineering", cfg) == "higher_ed_leader"

    # Executive
    assert categorize_title("President", cfg) == "executive"
    assert categorize_title("President & CEO", cfg) == "ceo"
    assert categorize_title("Chief Marketing Officer", cfg) == "cxo"
    assert categorize_title("Executive Vice President and Chief Marketing Officer", cfg) == "cxo"

    # Subordinate rejection preserved
    assert categorize_title("Assistant to the Director", cfg) is None
    assert categorize_title("Former Chief Executive Officer", cfg) is None


def test_table_row_extraction():
    """Verify <tr><td>Name</td><td>Title</td></tr> extraction."""
    cfg = load_roles_config()
    html = """
    <html><body>
    <table>
      <thead><tr><th>Name</th><th>Role</th></tr></thead>
      <tbody>
        <tr><td>Amanda Lefton</td><td>Commissioner</td></tr>
        <tr><td>Michelle Marchello</td><td>Executive Deputy Commissioner</td></tr>
        <tr><td>Robert Vance</td><td>Assistant to Commissioner</td></tr>
      </tbody>
    </table>
    </body></html>
    """
    candidates = extract_people_from_tables(html, "https://agency.gov/leadership", "page1", "agency.gov", cfg)
    assert len(candidates) == 2
    by_name = {c.full_name: c for c in candidates}

    assert "Amanda Lefton" in by_name
    assert by_name["Amanda Lefton"].title == "Commissioner"
    assert by_name["Amanda Lefton"].role_category == "public_official"
    assert by_name["Amanda Lefton"].verification_status == "verified"
    assert by_name["Amanda Lefton"].extraction_method == "table_row"

    assert "Michelle Marchello" in by_name
    assert by_name["Michelle Marchello"].title == "Executive Deputy Commissioner"
    assert by_name["Michelle Marchello"].role_category == "public_official"


def test_definition_list_extraction():
    """Verify <dl><dt>Name</dt><dd>Title</dd></dl> extraction."""
    cfg = load_roles_config()
    html = """
    <html><body>
    <dl>
      <dt>Elena Rostova</dt>
      <dd>Executive Director</dd>
      <dt>Marcus Brody</dt>
      <dd>Vice Chair</dd>
    </dl>
    </body></html>
    """
    candidates = extract_people_from_definition_lists(html, "https://association.org/board", "page2", "association.org", cfg)
    assert len(candidates) == 2
    by_name = {c.full_name: c for c in candidates}

    assert "Elena Rostova" in by_name
    assert by_name["Elena Rostova"].title == "Executive Director"
    assert by_name["Elena Rostova"].role_category == "association_leader"
    assert by_name["Elena Rostova"].verification_status == "verified"
    assert by_name["Elena Rostova"].extraction_method == "definition_list"

    assert "Marcus Brody" in by_name
    assert by_name["Marcus Brody"].title == "Vice Chair"
    assert by_name["Marcus Brody"].role_category == "association_leader"


def test_heading_sibling_extraction():
    """Verify <h3>Name</h3><p>Title</p> extraction."""
    cfg = load_roles_config()
    html = """
    <html><body>
    <div class="leader-profile">
      <h3>Dr. Gene Block</h3>
      <p>Chancellor</p>
    </div>
    <div class="leader-profile">
      <h3>Sarah Jenkins</h3>
      <p>Provost and Vice Chancellor</p>
    </div>
    </body></html>
    """
    candidates = extract_people_from_heading_siblings(html, "https://university.edu/leadership", "page3", "university.edu", cfg)
    assert len(candidates) == 2
    by_name = {c.full_name: c for c in candidates}

    assert "Gene Block" in by_name or "Dr. Gene Block" in by_name
    key = "Gene Block" if "Gene Block" in by_name else "Dr. Gene Block"
    assert by_name[key].title == "Chancellor"
    assert by_name[key].role_category == "higher_ed_leader"
    assert by_name[key].verification_status == "verified"
    assert by_name[key].extraction_method == "heading_sibling"

    assert "Sarah Jenkins" in by_name
    assert by_name["Sarah Jenkins"].role_category == "higher_ed_leader"


def test_prefix_title_and_delimiter_extraction():
    """Verify prefix title in heading (<h3>Commissioner Amanda Lefton</h3>) and delimiter with & (Katie Farmer - President & CEO)."""
    cfg = load_roles_config()
    html = """
    <html><body>
      <h3>Commissioner Amanda Lefton</h3>
      <p>Executive Deputy Commissioner Michelle Marchello</p>
      <li>Katie Farmer – President & CEO</li>
      <li>Tom Williams – Executive Vice President and Chief Marketing Officer</li>
    </body></html>
    """
    candidates = extract_people_from_cards_and_headings(html, "https://example.com/team", "page4", "example.com", cfg)
    by_name = {c.full_name: c for c in candidates}

    assert "Amanda Lefton" in by_name
    assert by_name["Amanda Lefton"].title == "Commissioner"
    assert by_name["Amanda Lefton"].role_category == "public_official"

    assert "Michelle Marchello" in by_name
    assert by_name["Michelle Marchello"].title == "Executive Deputy Commissioner"
    assert by_name["Michelle Marchello"].role_category == "public_official"

    assert "Katie Farmer" in by_name
    assert "CEO" in by_name["Katie Farmer"].title
    assert by_name["Katie Farmer"].role_category == "ceo"

    assert "Tom Williams" in by_name
    assert by_name["Tom Williams"].role_category == "cxo"
