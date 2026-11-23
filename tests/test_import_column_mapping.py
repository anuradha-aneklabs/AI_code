"""Tests for import column mapping, header normalization, name composition, and company hint.

Uses FAKE data only.
"""

from __future__ import annotations

import csv
import pytest

from harvey.batch_research import create_batch_from_file, export_batch_results, parse_import_file
from harvey.state import StateManager


@pytest.fixture
def temp_state(tmp_path):
    db_file = tmp_path / "test_import_mapping.db"
    return StateManager(db_path=str(db_file))


def test_column_mapping_fcc_headers_and_negative_email_guard():
    """Verify that EmailAddress is selected over EmailValid, and compound names are constructed."""
    headers = [
        "EntityName", "FirstName", "MiddleInitial", "LastName", "Suffix",
        "Phone", "EmailAddress", "EmailValid", "Address1", "City",
        "StateCode", "PostalCode", "POBox", "AttentionLine",
    ]
    row1 = [
        "Apex Spectrum LLC", "Jane", "M", "Doe", "Jr.",
        "555-0199", "jane.doe@spectrumfake.test", "1", "123 Test Rd", "Austin",
        "TX", "78701", "", "",
    ]
    content = (",".join(headers) + "\n" + ",".join(row1) + "\n").encode("utf-8")

    parsed, col_map = parse_import_file(content, "fcc_sample.csv")

    assert col_map["email"] == "EmailAddress"
    assert "company_name" in col_map
    assert col_map["company_name"] == "EntityName"
    assert col_map["phone"] == "Phone"

    assert len(parsed) == 1
    r = parsed[0]
    assert r["raw_email"] == "jane.doe@spectrumfake.test"
    assert r["raw_name"] == "Jane M Doe Jr."
    assert r["company_name_hint"] == "Apex Spectrum LLC"
    assert r["raw_phone"] == "555-0199"


def test_company_only_rows_are_valid():
    """A row without person names is valid (company-only), not an error."""
    csv_text = (
        "EntityName,FirstName,MiddleInitial,LastName,Suffix,Phone,EmailAddress,EmailValid\n"
        "Titan Wireless Co,,,,,555-0144,contact@titanfake.test,1\n"
    )
    content = csv_text.encode("utf-8")

    parsed, col_map = parse_import_file(content, "company_only.csv")
    assert len(parsed) == 1
    assert parsed[0]["raw_name"] == ""
    assert parsed[0]["company_name_hint"] == "Titan Wireless Co"
    assert parsed[0]["raw_email"] == "contact@titanfake.test"


@pytest.mark.asyncio
async def test_bom_handling_and_company_only_validation_in_db(temp_state):
    """Test utf-8-sig (BOM) handling and verify company-only row persists as valid in DB."""
    await temp_state.init_db()

    csv_text = (
        "EntityName,FirstName,LastName,Phone,EmailAddress,EmailValid\n"
        "Beacon Telecom,,,555-9988,operations@beaconfake.test,1\n"
    )
    bom_content = csv_text.encode("utf-8-sig")

    res = await create_batch_from_file(bom_content, "bom_test.csv", temp_state, max_domains=5)
    batch_id = res["batch_id"]

    rows = await temp_state.get_import_rows(batch_id)
    assert len(rows) == 1
    r = rows[0]
    assert r["row_status"] == "valid"
    assert r["company_name_hint"] == "Beacon Telecom"
    assert r["raw_email"] == "operations@beaconfake.test"
    assert r["raw_name"] == ""


@pytest.mark.asyncio
async def test_export_carries_company_name_hint_and_entity_check(temp_state, tmp_path):
    """Verify that batch export carries company_name_hint and entity_check columns when hints exist."""
    await temp_state.init_db()

    csv_text = (
        "EntityName,FirstName,LastName,Phone,EmailAddress,EmailValid\n"
        "Summit Radios,Alice,Smith,555-2345,alice@summitfake.test,1\n"
    )
    res = await create_batch_from_file(csv_text.encode("utf-8"), "summit.csv", temp_state, max_domains=5)
    batch_id = res["batch_id"]

    export_dir = tmp_path / "exports"
    export_res = await export_batch_results(batch_id, temp_state, export_dir=export_dir)

    with open(export_res["contacts_results"], "r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))
        assert len(reader) == 1
        r = reader[0]
        assert "company_name_hint" in r
        assert r["company_name_hint"] == "Summit Radios"
        assert "entity_check" in r
        assert r["name"] == "Alice Smith"
