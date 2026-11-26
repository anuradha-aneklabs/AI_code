"""Tests for CLI export masking defaults and --unmask flag using FAKE data."""

import argparse
import csv
import os
from pathlib import Path
import pytest

from harvey.batch_research import create_batch_from_file
from harvey.state import StateManager
from scripts.import_batch import cmd_export
from scripts.make_demo_report import generate_html_report


@pytest.mark.asyncio
async def test_cli_import_batch_export_default_masked(tmp_path):
    # 1. Setup isolated test DB
    db_path = tmp_path / "test_cli_masking.db"
    state = StateManager(db_path=str(db_path))
    await state.init_db()

    # 2. Fake CSV input content with explicit personal contact data
    csv_content = (
        "Name,Email,Phone,Domain\n"
        "Alice Cooper,alice.cooper@fakedrivingcorp.com,555-432-8765,fakedrivingcorp.com\n"
    ).encode("utf-8")

    batch_res = await create_batch_from_file(
        content_bytes=csv_content,
        filename="fake_pilot.csv",
        state=state,
    )
    batch_id = batch_res["batch_id"]

    # 3. Test default export (unmask=False)
    out_dir_default = tmp_path / "exports_default"
    out_dir_default.mkdir(parents=True, exist_ok=True)

    args_default = argparse.Namespace(
        batch_id=batch_id,
        output_dir=out_dir_default,
        unmask=False,
    )
    os.environ["HARVEY_DB_PATH"] = str(db_path)

    await cmd_export(args_default)

    contacts_csv = out_dir_default / f"contacts_results_{batch_id}.csv"
    assert contacts_csv.exists()

    with open(contacts_csv, "r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))
        assert len(reader) == 1
        row = reader[0]
        # Must be masked by default
        assert row["name"] == "A. C."
        assert row["email"] == "al***@fakedrivingcorp.com"
        assert row["phone"] == "***-***-**65"
        assert "Alice Cooper" not in row["name"]
        assert "alice.cooper" not in row["email"]

    # 4. Test unmasked export (unmask=True)
    out_dir_unmasked = tmp_path / "exports_unmasked"
    out_dir_unmasked.mkdir(parents=True, exist_ok=True)

    args_unmasked = argparse.Namespace(
        batch_id=batch_id,
        output_dir=out_dir_unmasked,
        unmask=True,
    )
    await cmd_export(args_unmasked)

    contacts_csv_unmasked = out_dir_unmasked / f"contacts_results_{batch_id}.csv"
    assert contacts_csv_unmasked.exists()

    with open(contacts_csv_unmasked, "r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))
        assert len(reader) == 1
        row = reader[0]
        # Must be raw unmasked
        assert row["name"] == "Alice Cooper"
        assert row["email"] == "alice.cooper@fakedrivingcorp.com"
        assert row["phone"] == "555-432-8765"


@pytest.mark.asyncio
async def test_make_demo_report_masking_default_and_unmasked(tmp_path):
    # Setup test DB using StateManager to have full schema
    db_path = tmp_path / "test_report_masking.db"
    state = StateManager(db_path=str(db_path))
    await state.init_db()

    csv_content = (
        "Name,Email,Phone,Domain,Company\n"
        "Bob Vance,bob.vance@fakecorp.com,555-888-9999,fakecorp.com,Fake Corp\n"
        "Eve Adams,eve.adams@gmail.com,555-111-2222,gmail.com,Free Provider\n"
    ).encode("utf-8")

    batch_res = await create_batch_from_file(
        content_bytes=csv_content,
        filename="fake_report_input.csv",
        state=state,
    )
    batch_id = batch_res["batch_id"]

    # 1. Test default masked report
    out_html_masked = tmp_path / "report_masked.html"
    generate_html_report(
        db_path=str(db_path),
        batch_id=batch_id,
        output_path=out_html_masked,
        unmask=False,
    )
    assert out_html_masked.exists()
    content_masked = out_html_masked.read_text(encoding="utf-8")
    assert "B. V." in content_masked
    assert "bo***@fakecorp.com" in content_masked
    assert "***-***-**99" in content_masked
    assert "E. A." in content_masked
    assert "ev***@gmail.com" in content_masked
    assert "Bob Vance" not in content_masked
    assert "bob.vance@fakecorp.com" not in content_masked
    assert "Eve Adams" not in content_masked

    # 2. Test unmasked report
    out_html_unmasked = tmp_path / "report_unmasked.html"
    generate_html_report(
        db_path=str(db_path),
        batch_id=batch_id,
        output_path=out_html_unmasked,
        unmask=True,
    )
    assert out_html_unmasked.exists()
    content_unmasked = out_html_unmasked.read_text(encoding="utf-8")
    assert "Bob Vance" in content_unmasked
    assert "bob.vance@fakecorp.com" in content_unmasked
    assert "555-888-9999" in content_unmasked
    assert "Eve Adams" in content_unmasked
    assert "eve.adams@gmail.com" in content_unmasked
