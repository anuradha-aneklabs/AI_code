"""Tests for HTML demo report generation.

Uses FAKE data only.
"""

from __future__ import annotations

import sqlite3
import pytest

from harvey.batch_research import create_batch_from_file
from harvey.state import StateManager
from scripts.make_demo_report import generate_html_report, mask_email, mask_phone


@pytest.fixture
def temp_state(tmp_path):
    db_file = tmp_path / "test_demo_report.db"
    return StateManager(db_path=str(db_file))


def test_masking_helpers():
    """Verify email and phone masking logic."""
    assert mask_email("alice.wonderland@example.com") == "al***@example.com"
    assert mask_email("b@short.org") == "b***@short.org"
    assert mask_phone("1-555-432-1098") == "***-***-**98"
    assert mask_phone("") == ""


@pytest.mark.asyncio
async def test_generate_html_report_masked_and_unmasked(temp_state, tmp_path):
    """Verify HTML demo report renders cards, masks PII by default, and unmasks with flag."""
    await temp_state.init_db()

    csv_text = (
        "EntityName,FirstName,LastName,Phone,EmailAddress,EmailValid\n"
        "Frontier Wireless LLC,Robert,Taylor,555-8877,robert@frontierfake.test,1\n"
    )
    batch_res = await create_batch_from_file(csv_text.encode("utf-8"), "frontier.csv", temp_state, max_domains=5)
    batch_id = batch_res["batch_id"]

    run_id = "run_frontier_1"
    domain = "frontierfake.test"

    await temp_state.save_research_run(
        run_id=run_id,
        input_email="robert@frontierfake.test",
        normalized_email="robert@frontierfake.test",
        domain=domain,
        status="completed",
    )
    await temp_state.save_company_website(
        website_id="site_frontier",
        run_id=run_id,
        domain=domain,
        final_url="https://frontierfake.test/",
        verification_status="verified",
        confidence=1.0,
    )
    await temp_state.save_research_finding(
        finding_id="f_ov_front",
        run_id=run_id,
        extraction_run_id="ext_f",
        domain=domain,
        finding_type="overview",
        value_text="Frontier Wireless delivers commercial spectrum solutions.",
        verification_status="verified",
        confidence=0.9,
    )
    await temp_state.save_research_person(
        person_id="p_front_ceo",
        run_id=run_id,
        extraction_run_id="ext_p_f",
        domain=domain,
        full_name="Samantha Ray",
        title="Chief Executive Officer",
        role_category="c_level",
        verification_status="verified",
        evidence_text="Samantha Ray serves as CEO of Frontier Wireless.",
        source_url="https://frontierfake.test/about",
    )
    await temp_state.update_import_domain_status(
        batch_id,
        domain,
        status="completed",
        run_id=run_id,
        entity_check="match",
        entity_check_evidence="Frontier Wireless delivers commercial spectrum",
    )

    # 1. Masked Report
    masked_out = tmp_path / "report_masked.html"
    res_path = generate_html_report(
        db_path=temp_state.db_path,
        batch_id=batch_id,
        output_path=masked_out,
        unmask=False,
    )
    assert masked_out.exists()

    with open(masked_out, "r", encoding="utf-8") as f:
        html_content = f.read()
        # Contact PII masked
        assert "ro***@frontierfake.test" in html_content
        assert "***-***-**77" in html_content
        assert "[NAME]" in html_content
        # Entity and domain visible
        assert "Frontier Wireless LLC" in html_content
        assert "frontierfake.test" in html_content
        assert "Entity Match" in html_content
        assert "Chief Executive Officer" in html_content

    # 2. Unmasked Report
    unmasked_out = tmp_path / "report_unmasked.html"
    generate_html_report(
        db_path=temp_state.db_path,
        batch_id=batch_id,
        output_path=unmasked_out,
        unmask=True,
    )
    assert unmasked_out.exists()

    with open(unmasked_out, "r", encoding="utf-8") as f:
        unmasked_content = f.read()
        assert "robert@frontierfake.test" in unmasked_content
        assert "555-8877" in unmasked_content
        assert "Samantha Ray" in unmasked_content
