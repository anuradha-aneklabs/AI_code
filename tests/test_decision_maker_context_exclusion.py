"""Tests for context-based officer justification and non-officer exclusion.

Verifies that:
- Quote blocks with name+title are excluded (marked unverified with excluded_reason).
- "Trusted by" strips are excluded (marked unverified with excluded_reason).
- JSON-LD founders remain verified and not excluded.
- Real "Our Team" grids placed near testimonial sections remain verified (guard against over-downgrading).
- If all candidates are excluded, find_decision_makers records exactly one "unavailable"
  row with reason "only testimonial/advisor-context names found".
"""

import json
import pytest
from harvey.decision_makers import (
    DecisionMakerCandidate,
    check_officer_context,
    find_decision_makers,
    load_roles_config,
)
from harvey.state import StateManager


@pytest.fixture
def temp_state(tmp_path):
    db_file = tmp_path / "test_context_exclusion.db"
    return StateManager(db_path=str(db_file))


def test_quote_block_with_name_and_title_excluded():
    """Assert quote blocks (e.g. blockquote) with name+title are excluded."""
    cfg = load_roles_config()
    cand = DecisionMakerCandidate(
        full_name="Alex Morgan",
        title="CEO & Founder",
        role_category="ceo",
        source_url="https://example.com/",
        source_page_id="page_1",
        evidence_text="Alex Morgan CEO & Founder",
        confidence=0.9,
        verification_status="verified",
        extraction_method="card",
    )
    html = """
    <html>
        <body>
            <main>
                <blockquote>
                    <p>"This product completely transformed our operations and team efficiency."</p>
                    <footer>Alex Morgan, CEO & Founder</footer>
                </blockquote>
            </main>
        </body>
    </html>
    """
    status, reason = check_officer_context(cand, html, "Alex Morgan CEO & Founder", "example.com", cfg)
    assert status == "unverified"
    assert cand.excluded_reason != ""
    assert "testimonial" in cand.excluded_reason.lower() or "client" in cand.excluded_reason.lower()


def test_trusted_by_strip_excluded():
    """Assert candidates in a 'trusted by' or advisor/investor strip are excluded."""
    cfg = load_roles_config()
    cand = DecisionMakerCandidate(
        full_name="Morgan Reed",
        title="Managing Director",
        role_category="director",
        source_url="https://example.com/",
        source_page_id="page_1",
        evidence_text="Morgan Reed Managing Director",
        confidence=0.9,
        verification_status="verified",
        extraction_method="card",
    )
    html = """
    <html>
        <body>
            <div class="trusted-by-strip">
                <h3>Trusted by industry leaders and top executives</h3>
                <div class="executive-card">
                    <p>Morgan Reed</p>
                    <p>Managing Director</p>
                </div>
            </div>
        </body>
    </html>
    """
    status, reason = check_officer_context(cand, html, "Morgan Reed Managing Director", "example.com", cfg)
    assert status == "unverified"
    assert cand.excluded_reason != ""
    assert "advisor" in cand.excluded_reason.lower() or "investor" in cand.excluded_reason.lower() or "endorsement" in cand.excluded_reason.lower()


def test_jsonld_founders_stay_verified():
    """Assert JSON-LD schema.org Person founders remain verified and not excluded."""
    cfg = load_roles_config()
    cand = DecisionMakerCandidate(
        full_name="Taylor Swift",
        title="Co-Founder & CEO",
        role_category="founder",
        source_url="https://example.com/",
        source_page_id="page_1",
        evidence_text='Taylor Swift", "jobTitle": "Co-Founder & CEO',
        confidence=1.0,
        verification_status="verified",
        extraction_method="jsonld_person",
    )
    status, reason = check_officer_context(cand, "", "", "example.com", cfg)
    assert status == "verified"
    assert cand.excluded_reason == ""


def test_team_grid_near_testimonial_section_stays_verified():
    """Assert genuine team grid placed near a testimonial section is NOT downgraded."""
    cfg = load_roles_config()
    
    # 1. Genuine officer in team grid
    team_cand = DecisionMakerCandidate(
        full_name="Jordan Blake",
        title="Chief Operating Officer",
        role_category="coo",
        source_url="https://example.com/about",
        source_page_id="page_1",
        evidence_text="Jordan Blake Chief Operating Officer",
        confidence=0.9,
        verification_status="verified",
        extraction_method="card",
    )

    # 2. Client testimonial author on the same page
    testimonial_cand = DecisionMakerCandidate(
        full_name="Sam Taylor",
        title="Chief Technology Officer",
        role_category="cto",
        source_url="https://example.com/about",
        source_page_id="page_1",
        evidence_text="Sam Taylor Chief Technology Officer",
        confidence=0.9,
        verification_status="verified",
        extraction_method="card",
    )

    html = """
    <html>
        <body>
            <section class="our-team-section">
                <h2>Our Leadership Team</h2>
                <div class="team-card">
                    <h3>Jordan Blake</h3>
                    <p>Chief Operating Officer</p>
                </div>
            </section>
            <section class="client-testimonials">
                <h2>What Our Clients Say</h2>
                <blockquote>
                    <p>"Outstanding partner to build software solutions."</p>
                    <h4>Sam Taylor</h4>
                    <p>Chief Technology Officer</p>
                </blockquote>
            </section>
        </body>
    </html>
    """

    status_team, _ = check_officer_context(team_cand, html, "", "example.com", cfg)
    status_test, _ = check_officer_context(testimonial_cand, html, "", "example.com", cfg)

    assert status_team == "verified"
    assert team_cand.excluded_reason == "", "Jordan Blake in team section should not be excluded"

    assert status_test == "unverified"
    assert testimonial_cand.excluded_reason != "", "Sam Taylor in testimonials should be excluded"


@pytest.mark.asyncio
async def test_all_excluded_candidates_store_single_unavailable_row(temp_state):
    """Assert that if all candidates are excluded, exactly one unavailable row is stored."""
    await temp_state.init_db()
    run_id = "test_run_all_excluded"
    domain = "clientquotes.test"

    # Setup run and company website
    await temp_state.save_research_run(
        run_id=run_id,
        input_email=f"contact@{domain}",
        normalized_email=f"contact@{domain}",
        domain=domain,
        status="running",
    )
    await temp_state.save_company_website(
        website_id="site_test_1",
        run_id=run_id,
        domain=domain,
        candidate_url=f"https://{domain}/",
        final_url=f"https://{domain}/",
        http_status=200,
        dns_resolved=True,
        verification_status="verified",
        confidence=1.0,
        evidence_text="Reachable",
    )

    # Single page containing only client testimonials
    page_html = """
    <html>
        <body>
            <div class="client-reviews">
                <h2>Client Stories</h2>
                <blockquote>
                    <p>"Amazing experience working with them!"</p>
                    <p>Casey Rivers</p>
                    <p>Chief Executive Officer</p>
                </blockquote>
            </div>
        </body>
    </html>
    """
    await temp_state.save_source_page(
        page_id="page_test_1",
        run_id=run_id,
        domain=domain,
        url=f"https://{domain}/",
        page_type="homepage",
        http_status=200,
        content_type="text/html",
        raw_text="Client Stories Amazing experience working with them! Casey Rivers Chief Executive Officer",
        raw_html=page_html,
    )

    # Run decision maker discovery
    people = await find_decision_makers(run_id, temp_state)

    # Default output should have exactly ONE row, which is 'unavailable'
    assert len(people) == 1
    assert people[0]["full_name"] == "unavailable"
    assert people[0]["verification_status"] == "unavailable"
    assert people[0]["reason"] == "only testimonial/advisor-context names found"

    # Auditing with include_excluded=True returns both the excluded testimonial author and the unavailable row
    all_people = await temp_state.get_research_people(run_id, include_excluded=True)
    assert len(all_people) == 2
    names = {p["full_name"] for p in all_people}
    assert "Casey Rivers" in names
    assert "unavailable" in names
