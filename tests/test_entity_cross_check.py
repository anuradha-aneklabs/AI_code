"""Tests for entity cross-checking logic and batch export warnings.

Uses FAKE data only.
"""

from __future__ import annotations

import csv
import pytest

from harvey.batch_research import create_batch_from_file, export_batch_results
from harvey.entity_cross_check import check_entity_match
from harvey.state import StateManager


@pytest.fixture
def temp_state(tmp_path):
    db_file = tmp_path / "test_entity_cross_check.db"
    return StateManager(db_path=str(db_file))


def test_entity_cross_check_stopwords_and_legal_suffixes_match():
    """Verify stripping of 'The', 'and', 'Corporation', 'LLC' and matching core tokens."""
    hint = "The Sterling Spectrum Communications Corporation LLC"
    pages = [
        {
            "title": "Welcome to Sterling Spectrum - High-Reliability RF Systems",
            "readable_text": "Sterling Spectrum provides mission-critical communications infrastructure for enterprise networks.",
        }
    ]
    status, evidence = check_entity_match(hint, pages=pages)
    assert status == "match"
    assert "Sterling Spectrum" in evidence


def test_entity_cross_check_no_match():
    """When the website belongs to an unrelated entity/filer, return no_match."""
    hint = "Prairie Wind Farm Services LLC"
    pages = [
        {
            "title": "Global Telecom Filing Consultants & Regulatory Law",
            "readable_text": "We handle licensing, filings, and compliance for wireless operators nationwide.",
        }
    ]
    status, evidence = check_entity_match(hint, pages=pages)
    assert status == "no_match"
    assert evidence == ""


def test_entity_cross_check_no_hint():
    """When company hint is empty or contains only stopwords/legal suffixes, return no_hint."""
    assert check_entity_match("")[0] == "no_hint"
    assert check_entity_match("   ")[0] == "no_hint"
    assert check_entity_match("The LLC Inc")[0] == "no_hint"


@pytest.mark.asyncio
async def test_multiple_entities_on_one_domain_and_no_match_warning_in_export(temp_state, tmp_path):
    """Verify single domain with multiple EntityNames exports both hints and applies no_match warning."""
    await temp_state.init_db()

    csv_text = (
        "EntityName,FirstName,LastName,EmailAddress,EmailValid\n"
        "Alpha County Radio,Dan,Brown,licensing@consultinglawfake.test,1\n"
        "Beta Regional Dispatch,Sara,White,filing@consultinglawfake.test,1\n"
    )
    res = await create_batch_from_file(csv_text.encode("utf-8"), "multi_entity.csv", temp_state, max_domains=5)
    batch_id = res["batch_id"]

    # Only 1 unique domain record was created
    domains = await temp_state.get_import_domains(batch_id)
    assert len(domains) == 1
    assert domains[0]["domain"] == "consultinglawfake.test"

    run_id = "run_consulting_law"
    domain = "consultinglawfake.test"

    # Simulate completed research run where entity check is 'no_match'
    await temp_state.save_research_run(
        run_id=run_id,
        input_email="licensing@consultinglawfake.test",
        normalized_email="licensing@consultinglawfake.test",
        domain=domain,
        status="completed",
    )
    await temp_state.save_company_website(
        website_id="site_consulting",
        run_id=run_id,
        domain=domain,
        final_url="https://consultinglawfake.test/",
        verification_status="verified",
        confidence=1.0,
    )
    await temp_state.save_research_finding(
        finding_id="f_ov_c",
        run_id=run_id,
        extraction_run_id="ext_c",
        domain=domain,
        finding_type="overview",
        value_text="Regulatory compliance and filing law specialists.",
        verification_status="verified",
        confidence=0.9,
    )
    await temp_state.save_research_person(
        person_id="p_attorney",
        run_id=run_id,
        extraction_run_id="ext_p_c",
        domain=domain,
        full_name="Morgan Vance",
        title="Managing Partner",
        role_category="founder",
        verification_status="verified",
        evidence_text="Morgan Vance is managing partner at Consulting Law.",
        source_url="https://consultinglawfake.test/team",
    )

    # Set status as completed with entity_check = no_match
    await temp_state.update_import_domain_status(
        batch_id,
        domain,
        status="completed",
        run_id=run_id,
        entity_check="no_match",
        entity_check_evidence="",
    )

    export_dir = tmp_path / "exports"
    export_res = await export_batch_results(batch_id, temp_state, export_dir=export_dir)

    # Check contacts_results.csv
    with open(export_res["contacts_results"], "r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))
        assert len(reader) == 2
        # Row 1 has Alpha County Radio
        assert reader[0]["company_name_hint"] == "Alpha County Radio"
        assert "no_match: domain may belong to a filer" in reader[0]["entity_check"]
        assert "[WARNING: domain may belong to a filer, consultant or parent organisation]" in reader[0]["decision_makers"]

        # Row 2 has Beta Regional Dispatch
        assert reader[1]["company_name_hint"] == "Beta Regional Dispatch"
        assert "no_match: domain may belong to a filer" in reader[1]["entity_check"]

    # Check decision_makers.csv has the warning in evidence
    with open(export_res["decision_makers"], "r", encoding="utf-8") as f:
        dm_rows = list(csv.DictReader(f))
        assert len(dm_rows) == 1
        assert "[domain may belong to a filer, consultant or parent organisation]" in dm_rows[0]["evidence"]
