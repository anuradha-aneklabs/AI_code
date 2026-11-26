"""Unit and integration tests for export PII masking (fake data only)."""

import csv
from pathlib import Path
import pytest

from harvey.batch_research import (
    mask_name,
    mask_email,
    mask_phone,
    create_batch_from_file,
    export_batch_results,
)
from harvey.state import StateManager
from scripts.make_demo_report import generate_html_report


def test_mask_helpers():
    """Verify masking helper functions with fake data."""
    # Name masking (initials)
    assert mask_name("John Doe") == "J. D."
    assert mask_name("Alice Middle Bob") == "A. M. B."
    assert mask_name("Cher") == "C."
    assert mask_name("") == "—"
    assert mask_name(None) == "—"
    assert mask_name("  !@#  ") == "—"

    # Email masking (first 2 letters + ***@domain)
    assert mask_email("john.doe@example.com") == "jo***@example.com"
    assert mask_email("a@example.com") == "a***@example.com"
    assert mask_email("ab@example.com") == "ab***@example.com"
    assert mask_email("") == ""

    # Phone masking (last 2 digits)
    assert mask_phone("1234567890") == "***-***-**90"
    assert mask_phone("+1 (555) 019-2834") == "***-***-**34"
    assert mask_phone("12") == "***12"
    assert mask_phone("") == ""


@pytest.mark.asyncio
async def test_export_batch_results_masking(tmp_path):
    """Verify export_batch_results masks contact PII by default and unmasks when requested."""
    db_file = tmp_path / "test_masking.db"
    state = StateManager(db_path=str(db_file))
    await state.init_db()

    csv_data = (
        "Name,Email,Company,Phone\n"
        "Alice Smith,alice.smith@fakecompany.com,Fake Company Inc,+1 555 123 4567\n"
        "Bob Jones,bob.jones@fakecompany.com,Fake Company Inc,555-987-6543\n"
    )
    batch_res = await create_batch_from_file(
        filename="fake_contacts.csv",
        content_bytes=csv_data.encode("utf-8"),
        state=state,
        max_domains=1,
    )
    batch_id = batch_res["batch_id"]

    domains = await state.get_import_domains(batch_id)
    assert len(domains) == 1
    run_id = "test_run_mask_001"
    now_iso = "2026-11-25T12:00:00"

    # Link domain to fake research run
    await state.update_import_domain_status(
        batch_id=batch_id,
        domain="fakecompany.com",
        status="completed",
        run_id=run_id,
        entity_check="match",
        entity_check_evidence="Found Fake Company on homepage",
    )

    await state.save_research_run(
        run_id=run_id,
        input_email="alice.smith@fakecompany.com",
        normalized_email="alice.smith@fakecompany.com",
        domain="fakecompany.com",
        status="completed",
        started_at=now_iso,
        finished_at=now_iso,
    )
    await state.save_company_website(
        website_id="ws_001",
        run_id=run_id,
        domain="fakecompany.com",
        final_url="https://fakecompany.com",
        verification_status="verified",
    )
    await state.save_research_person(
        person_id="p_001",
        run_id=run_id,
        extraction_run_id=run_id,
        domain="fakecompany.com",
        full_name="Eleanor Vance",
        title="Chief Executive Officer",
        role_category="executive",
        verification_status="verified",
        source_url="https://fakecompany.com/team",
        evidence_text="Eleanor Vance is CEO of Fake Company",
    )

    # Test A: Default Export (unmask=False, mask_names=True)
    out_dir_masked = tmp_path / "exports_masked"
    paths_masked = await export_batch_results(
        batch_id=batch_id,
        state=state,
        export_dir=out_dir_masked,
        unmask=False,
        mask_names=True,
    )
    
    with open(paths_masked["contacts_results"], "r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))
        assert len(reader) == 2
        # Contact PII must be masked
        assert reader[0]["name"] == "A. S."
        assert reader[0]["email"] == "al***@fakecompany.com"
        assert reader[0]["phone"] == "***-***-**67"
        assert reader[1]["name"] == "B. J."
        assert reader[1]["email"] == "bo***@fakecompany.com"
        assert reader[1]["phone"] == "***-***-**43"
        # Public website leader remains unmasked
        assert "Eleanor Vance - Chief Executive Officer [verified]" in reader[0]["decision_makers"]

    # Test B: Explicit Unmasked Export (unmask=True)
    out_dir_unmasked = tmp_path / "exports_unmasked"
    paths_unmasked = await export_batch_results(
        batch_id=batch_id,
        state=state,
        export_dir=out_dir_unmasked,
        unmask=True,
    )
    
    with open(paths_unmasked["contacts_results"], "r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))
        assert len(reader) == 2
        # Contact PII must be raw
        assert reader[0]["name"] == "Alice Smith"
        assert reader[0]["email"] == "alice.smith@fakecompany.com"
        assert reader[0]["phone"] == "+1 555 123 4567"

    # Test C: HTML Report Masking
    html_masked_path = generate_html_report(
        db_path=str(db_file),
        batch_id=batch_id,
        output_path=tmp_path / "report_masked.html",
        unmask=False,
    )
    with open(html_masked_path, "r", encoding="utf-8") as f:
        html_content = f.read()
        assert "A. S." in html_content
        assert "al***@fakecompany.com" in html_content
        assert "Alice Smith" not in html_content
        assert "Eleanor Vance" in html_content
