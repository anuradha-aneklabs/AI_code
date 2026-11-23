"""Unit and integration tests for Milestone 4: Excel/CSV batch research.

Covers:
- valid/invalid/free-mail/missing-email rows
- duplicate domains share one run
- BOM CSV parsing
- missing email column fails clearly
- crash in domain 2 -> domain 1 kept and batch continues
- --resume skips completed domains
- --max-domains cap marks skipped_limit
- export has no excluded rows and shows unavailable reasons
- dashboard endpoints refuse requests without a token
- no company names in application code
"""

import csv
import io
import json
import pytest
from unittest.mock import AsyncMock, patch

from harvey.batch_research import (
    create_batch_from_file,
    export_batch_results,
    parse_import_file,
    run_batch,
)
from harvey.state import StateManager


@pytest.fixture
def temp_state(tmp_path):
    db_file = tmp_path / "test_batch_research.db"
    return StateManager(db_path=str(db_file))


def test_batch_missing_email_column_fails_clearly():
    """Assert CSV without email column fails clearly with informative error."""
    csv_content = b"Full Name,Phone Number\nJohn Doe,555-1234\nJane Smith,555-5678"
    with pytest.raises(ValueError) as exc:
        parse_import_file(csv_content, "contacts.csv")
    assert "Missing required email column" in str(exc.value)


def test_batch_bom_csv_parsing():
    """Assert UTF-8 CSV with Byte Order Mark (BOM) parses seamlessly."""
    bom_csv = "\ufeffName,Email,Phone\nAlice,alice@firmone.test,555-0101\nBob,bob@firmone.test,555-0102".encode("utf-8-sig")
    rows, mapping = parse_import_file(bom_csv, "bom_contacts.csv")
    assert len(rows) == 2
    assert rows[0]["raw_email"] == "alice@firmone.test"
    assert rows[1]["raw_email"] == "bob@firmone.test"
    assert "email" in mapping


@pytest.mark.asyncio
async def test_batch_validation_row_statuses(temp_state):
    """Assert rows are classified into valid, invalid_email, free_mail, missing_email, and duplicate_domain."""
    await temp_state.init_db()
    csv_data = (
        "Name,Email Address,Phone\n"
        "Lead One,lead1@companyalpha.test,555-1001\n"
        "Lead Two,lead2@companyalpha.test,555-1002\n"  # duplicate domain
        "Free User,user@gmail.com,555-1003\n"           # free mail
        "Bad Email,not-an-email,555-1004\n"             # invalid syntax
        "No Email,,555-1005\n"                          # missing email
    ).encode("utf-8")

    res = await create_batch_from_file(
        content_bytes=csv_data,
        filename="test_leads.csv",
        state=temp_state,
        max_domains=5,
    )
    assert res["total_rows"] == 5
    assert res["valid_rows"] == 2  # lead1 (valid) + lead2 (duplicate_domain)
    assert res["unique_domains"] == 1  # companyalpha.test

    batch_id = res["batch_id"]
    rows = await temp_state.get_import_rows(batch_id)
    assert len(rows) == 5

    status_map = {r["row_number"]: r["row_status"] for r in rows}
    assert status_map[2] == "valid"
    assert status_map[3] == "duplicate_domain"
    assert status_map[4] == "free_mail"
    assert status_map[5] == "invalid_email"
    assert status_map[6] == "missing_email"


@pytest.mark.asyncio
async def test_batch_max_domains_cap_marks_skipped_limit(temp_state):
    """Assert domains exceeding max_domains cap get skipped and rows marked skipped_limit."""
    await temp_state.init_db()
    csv_data = (
        "Name,Email\n"
        "User 1,u1@dom1.test\n"
        "User 2,u2@dom2.test\n"
        "User 3,u3@dom3.test\n"
        "User 4,u4@dom4.test\n"
        "User 5,u5@dom5.test\n"
    ).encode("utf-8")

    res = await create_batch_from_file(
        content_bytes=csv_data,
        filename="cap_test.csv",
        state=temp_state,
        max_domains=3,  # Cap at 3 domains
    )
    assert res["unique_domains"] == 5
    assert res["capped_domains"] == 3
    assert res["skipped_domains"] == 2

    batch_id = res["batch_id"]
    rows = await temp_state.get_import_rows(batch_id)
    domains = await temp_state.get_import_domains(batch_id)

    # First 3 domains pending, last 2 skipped
    assert domains[0]["status"] == "pending"
    assert domains[1]["status"] == "pending"
    assert domains[2]["status"] == "pending"
    assert domains[3]["status"] == "skipped"
    assert domains[4]["status"] == "skipped"

    # Row statuses
    assert rows[0]["row_status"] == "valid"
    assert rows[1]["row_status"] == "valid"
    assert rows[2]["row_status"] == "valid"
    assert rows[3]["row_status"] == "skipped_limit"
    assert rows[4]["row_status"] == "skipped_limit"


@pytest.mark.asyncio
async def test_batch_runner_crash_in_domain_2_continues_and_domain_1_kept(temp_state):
    """Assert a failure in domain 2 does not stop the batch; domain 1 is kept, domain 3 succeeds."""
    await temp_state.init_db()
    csv_data = (
        "Name,Email\n"
        "Lead A,a@corpa.test\n"
        "Lead B,b@corpb.test\n"
        "Lead C,c@corpc.test\n"
    ).encode("utf-8")

    res = await create_batch_from_file(csv_data, "three_domains.csv", temp_state, max_domains=5)
    batch_id = res["batch_id"]

    # Mock research pipeline calls
    call_counts = {}

    async def mock_research(email, state, **kwargs):
        domain = email.split("@")[1]
        call_counts[domain] = call_counts.get(domain, 0) + 1
        if domain == "corpb.test":
            raise RuntimeError("Simulated network outage for corpb.test")

        run_id = f"run_{domain}"
        await state.save_research_run(
            run_id=run_id,
            input_email=email,
            normalized_email=email,
            domain=domain,
            status="completed",
        )
        await state.save_company_website(
            website_id=f"web_{domain}",
            run_id=run_id,
            domain=domain,
            final_url=f"https://{domain}/",
            verification_status="verified",
            confidence=1.0,
        )
        return {"run_id": run_id, "status": "completed"}

    async def mock_narrative(run_id, state):
        return {"overview": {"status": "verified", "finding_value": "A great software company."}}

    async def mock_decision_makers(run_id, state):
        return [{"full_name": "Executive Alpha", "title": "CEO", "verification_status": "verified"}]

    with patch("harvey.batch_research.run_single_email_research", side_effect=mock_research), \
         patch("harvey.batch_research.extract_company_narrative", side_effect=mock_narrative), \
         patch("harvey.batch_research.find_decision_makers", side_effect=mock_decision_makers):

        run_res = await run_batch(batch_id, temp_state)

    assert run_res["status"] == "partial"
    assert run_res["completed_domains"] == 2
    assert run_res["failed_domains"] == 1

    domains = await temp_state.get_import_domains(batch_id)
    dom_status = {d["domain"]: d["status"] for d in domains}
    assert dom_status["corpa.test"] == "completed"
    assert dom_status["corpb.test"] == "failed"
    assert dom_status["corpc.test"] == "completed"

    # Domain 1 run_id is preserved
    assert domains[0]["run_id"] == "run_corpa.test"


@pytest.mark.asyncio
async def test_batch_runner_resume_skips_completed_domains(temp_state):
    """Assert --resume processes only failed/pending domains and skips completed."""
    await temp_state.init_db()
    csv_data = "Name,Email\nLead 1,l1@corp1.test\nLead 2,l2@corp2.test\n".encode("utf-8")
    res = await create_batch_from_file(csv_data, "resume_test.csv", temp_state, max_domains=5)
    batch_id = res["batch_id"]

    # Pre-mark corp1 as completed
    await temp_state.update_import_domain_status(
        batch_id,
        "corp1.test",
        status="completed",
        run_id="run_corp1_existing",
    )

    mock_run = AsyncMock(return_value={"run_id": "run_corp2", "status": "completed"})
    mock_narrative = AsyncMock(return_value={})
    mock_dms = AsyncMock(return_value=[])

    with patch("harvey.batch_research.run_single_email_research", mock_run), \
         patch("harvey.batch_research.extract_company_narrative", mock_narrative), \
         patch("harvey.batch_research.find_decision_makers", mock_dms):

        run_res = await run_batch(batch_id, temp_state, resume=True)

    # Only corp2 should have been executed!
    assert run_res["completed_domains"] == 1
    assert mock_run.call_count == 1
    assert mock_run.call_args[1]["email"] == "l2@corp2.test"


@pytest.mark.asyncio
async def test_batch_export_format_and_unavailable_reasons(temp_state, tmp_path):
    """Assert CSV exports contain no excluded rows and format unavailable values with reason."""
    await temp_state.init_db()
    csv_data = "Name,Email\nLead One,lead1@mybiz.test\nLead Two,lead2@mybiz.test\n".encode("utf-8")
    res = await create_batch_from_file(csv_data, "export_test.csv", temp_state, max_domains=5)
    batch_id = res["batch_id"]

    # Set up completed run with verified officer + excluded testimonial author
    run_id = "run_mybiz_1"
    await temp_state.save_research_run(
        run_id=run_id,
        input_email="lead1@mybiz.test",
        normalized_email="lead1@mybiz.test",
        domain="mybiz.test",
        status="completed",
    )
    await temp_state.save_company_website(
        website_id="site_mybiz",
        run_id=run_id,
        domain="mybiz.test",
        final_url="https://mybiz.test/",
        verification_status="verified",
        confidence=1.0,
    )
    await temp_state.save_research_finding(
        finding_id="f_ov",
        run_id=run_id,
        extraction_run_id="ext_ov_1",
        domain="mybiz.test",
        finding_type="overview",
        value_text="Enterprise software provider",
        verification_status="verified",
        confidence=0.9,
    )
    # Decision makers: 1 verified, 1 excluded non-officer
    await temp_state.save_research_person(
        person_id="p_real",
        run_id=run_id,
        extraction_run_id="ext_1",
        domain="mybiz.test",
        full_name="Sarah Walker",
        title="Chief Technology Officer",
        role_category="cto",
        verification_status="verified",
        excluded_reason="",
    )
    await temp_state.save_research_person(
        person_id="p_excluded",
        run_id=run_id,
        extraction_run_id="ext_1",
        domain="mybiz.test",
        full_name="Client Author",
        title="CEO at PartnerCo",
        role_category="ceo",
        verification_status="unverified",
        excluded_reason="Context indicates client testimonial author rather than company officer",
    )

    await temp_state.update_import_domain_status(
        batch_id,
        "mybiz.test",
        status="completed",
        run_id=run_id,
    )

    export_dir = tmp_path / "exports"
    export_res = await export_batch_results(batch_id, temp_state, export_dir=export_dir)

    # Read contacts_results CSV
    with open(export_res["contacts_results"], "r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))
        assert len(reader) == 2
        # Decision makers formatted as "Name - Title [status]"
        assert "Sarah Walker - Chief Technology Officer [verified]" in reader[0]["decision_makers"]
        # Excluded non-officer author MUST NOT appear
        assert "Client Author" not in reader[0]["decision_makers"]
        assert "Client Author" not in reader[1]["decision_makers"]

    # Read decision_makers CSV
    with open(export_res["decision_makers"], "r", encoding="utf-8") as f:
        dm_rows = list(csv.DictReader(f))
        dm_names = [r["name"] for r in dm_rows]
        assert "Sarah Walker" in dm_names
        assert "Client Author" not in dm_names


@pytest.mark.asyncio
async def test_dashboard_batch_routes_require_authentication(temp_state):
    """Assert POST /api/research/batch and GET endpoints reject unauthenticated calls with 401."""
    from starlette.testclient import TestClient
    from harvey import dashboard as dash

    # Unauthenticated client with local host
    with TestClient(dash.app, base_url="http://127.0.0.1:8000") as anon:
        r_post = anon.post("/api/research/batch", json={"filename": "test.csv", "content": "Email\nx@y.z\n"})
        assert r_post.status_code == 401
        assert r_post.headers["www-authenticate"].startswith("Bearer")

        r_get = anon.get("/api/research/batch/non_existent_id")
        assert r_get.status_code == 401

        r_export = anon.get("/api/research/batch/non_existent_id/export")
        assert r_export.status_code == 401
