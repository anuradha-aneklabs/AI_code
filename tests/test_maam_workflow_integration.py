"""Integration test for Ma'am's 10-step Lead Intelligence Workflow.

Verifies:
1. Excel stdlib parser and builder.
2. Email domain extraction and validation.
3. Garbage, freemail, invalid email filtering.
4. Leadership role classification.
5. Entity cross check matching.
"""

from pathlib import Path
import tempfile
from harvey.batch_research import (
    _read_xlsx_stdlib,
    load_free_email_providers,
    validate_and_normalize_email,
)
from harvey.decision_makers import categorize_title, load_roles_config
from harvey.entity_cross_check import check_entity_match
from scripts.build_maam_xlsx import build_xlsx


def test_excel_stdlib_roundtrip():
    headers = ["Full Name", "Work Email", "Phone", "Company Name"]
    rows = [
        ["Sunil Rajput", "sunil@anikalabs.com", "+91-9876543210", "Anika Labs"],
        ["Test User", "testuser@gmail.com", "9876543210", "Personal Account"],
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_xlsx = Path(tmpdir) / "test.xlsx"
        build_xlsx(headers, rows, tmp_xlsx)
        assert tmp_xlsx.exists()
        raw_bytes = tmp_xlsx.read_bytes()
        assert len(raw_bytes) > 0

        all_rows = _read_xlsx_stdlib(raw_bytes)
        assert len(all_rows) >= 3
        read_headers = all_rows[0]
        read_rows = all_rows[1:]
        assert read_headers == headers
        assert len(read_rows) == 2
        assert read_rows[0][1] == "sunil@anikalabs.com"
        assert read_rows[1][1] == "testuser@gmail.com"


def test_email_domain_extraction_and_filtering():
    free_providers = load_free_email_providers()

    # 1. Pilot domain extraction
    res_pilot = validate_and_normalize_email("sunil@anikalabs.com", free_providers)
    assert res_pilot.valid_syntax is True
    assert res_pilot.domain == "anikalabs.com"
    assert res_pilot.is_free_mail is False

    # 2. Case normalization and strip
    res_upper = validate_and_normalize_email("  SUNIL@ANIKALABS.COM  ", free_providers)
    assert res_upper.valid_syntax is True
    assert res_upper.normalized_email == "sunil@anikalabs.com"
    assert res_upper.domain == "anikalabs.com"

    # 3. Freemail detection
    res_free = validate_and_normalize_email("test@gmail.com", free_providers)
    assert res_free.valid_syntax is True
    assert res_free.is_free_mail is True

    # 4. Invalid email format
    res_invalid = validate_and_normalize_email("not_a_valid_email_at_all", free_providers)
    assert res_invalid.valid_syntax is False
    assert res_invalid.domain == ""


def test_leadership_role_classification():
    cfg = load_roles_config()
    # Founder, Owner, Director, CEO, CXO/Executive
    assert categorize_title("Chief Executive Officer", cfg) == "ceo"
    assert categorize_title("Chief Technology Officer", cfg) == "cto"
    assert categorize_title("Chief Financial Officer", cfg) == "cfo"
    assert categorize_title("Chief Operating Officer", cfg) == "coo"
    assert categorize_title("Managing Director", cfg) == "director"
    assert categorize_title("Founder & Owner", cfg) == "founder"
    assert categorize_title("Chief Growth Officer", cfg) == "executive"
    assert categorize_title("Chief Creative Officer", cfg) == "executive"
    assert categorize_title("Chief Innovation Officer", cfg) == "executive"


def test_entity_cross_check_matching():
    # Exact and token overlap matching
    status, evidence = check_entity_match(
        company_name_hint="Anika Labs",
        website={"title": "Anika Labs | Your On-Demand Design & Development Team"},
        domain="anikalabs.com"
    )
    assert status == "match"
    assert "Anika Labs" in evidence
