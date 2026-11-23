"""Precision tests for batch CSV export formatting, status mapping, and CSV quoting."""

import csv
import io
import pytest
from harvey.batch_research import (
    create_batch_from_file,
    export_batch_results,
)
from harvey.state import StateManager


@pytest.fixture
def temp_state(tmp_path):
    db_file = tmp_path / "test_batch_export_precision.db"
    return StateManager(db_path=str(db_file))


@pytest.mark.asyncio
async def test_batch_export_findings_with_statuses(temp_state, tmp_path):
    """When narrative findings exist, export shows them with statuses (capped at 5 services)."""
    await temp_state.init_db()
    csv_content = "Name,Email\nExecutive One,exec1@platform.test\n".encode("utf-8")
    batch_res = await create_batch_from_file(csv_content, "findings_test.csv", temp_state, max_domains=5)
    batch_id = batch_res["batch_id"]

    run_id = "run_precision_1"
    ext_id = "ext_precision_1"
    domain = "platform.test"

    await temp_state.save_research_run(
        run_id=run_id,
        input_email="exec1@platform.test",
        normalized_email="exec1@platform.test",
        domain=domain,
        status="completed",
    )
    await temp_state.save_company_website(
        website_id="site_prec_1",
        run_id=run_id,
        domain=domain,
        final_url="https://platform.test/",
        verification_status="verified",
        confidence=1.0,
    )
    # Overview
    await temp_state.save_research_finding(
        finding_id="f_ov_1",
        run_id=run_id,
        extraction_run_id=ext_id,
        domain=domain,
        finding_type="overview",
        value_text="Global cloud orchestration and enterprise tooling.",
        verification_status="verified",
        confidence=0.95,
    )
    # 7 services (all verified) -> only top 5 should be exported
    for i in range(1, 8):
        await temp_state.save_research_finding(
            finding_id=f"f_svc_{i}",
            run_id=run_id,
            extraction_run_id=ext_id,
            domain=domain,
            finding_type="product_service",
            value_text=f"Service Feature {i}",
            verification_status="verified",
            confidence=0.9,
        )
    # Vision
    await temp_state.save_research_finding(
        finding_id="f_vis_1",
        run_id=run_id,
        extraction_run_id=ext_id,
        domain=domain,
        finding_type="vision",
        value_text="To empower engineering teams worldwide.",
        verification_status="verified",
        confidence=0.9,
    )
    # Mission
    await temp_state.save_research_finding(
        finding_id="f_mis_1",
        run_id=run_id,
        extraction_run_id=ext_id,
        domain=domain,
        finding_type="mission",
        value_text="Delivering resilient automation for cloud deployments.",
        verification_status="verified",
        confidence=0.9,
    )
    # Officer
    await temp_state.save_research_person(
        person_id="p_exec_1",
        run_id=run_id,
        extraction_run_id="ext_p_1",
        domain=domain,
        full_name="Alex Morgan",
        title="Chief Operating Officer",
        role_category="coo",
        verification_status="verified",
        excluded_reason="",
    )

    await temp_state.update_import_domain_status(
        batch_id,
        domain,
        status="completed",
        run_id=run_id,
    )

    export_dir = tmp_path / "exports"
    export_res = await export_batch_results(batch_id, temp_state, export_dir=export_dir)

    with open(export_res["contacts_results"], "r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))
        assert len(reader) == 1
        row = reader[0]

        # Overview and its status
        assert row["overview"] == "Global cloud orchestration and enterprise tooling."
        assert row["overview_status"] == "verified"

        # Vision with status
        assert row["vision"] == "To empower engineering teams worldwide. [verified]"

        # Mission with status
        assert row["mission"] == "Delivering resilient automation for cloud deployments. [verified]"

        # Services: capped at 5 and includes status
        services_field = row["services"]
        assert "Service Feature 1 [verified]" in services_field
        assert "Service Feature 5 [verified]" in services_field
        assert "Service Feature 6" not in services_field
        assert "Service Feature 7" not in services_field
        assert len(services_field.split("; ")) == 5

        # Decision makers
        assert "Alex Morgan - Chief Operating Officer [verified]" in row["decision_makers"]


@pytest.mark.asyncio
async def test_batch_export_unavailable_findings_with_reasons(temp_state, tmp_path):
    """When narrative findings are not found, export displays unavailable with explicit reasons."""
    await temp_state.init_db()
    csv_content = "Name,Email\nLead User,user@emptyco.test\n".encode("utf-8")
    batch_res = await create_batch_from_file(csv_content, "empty_test.csv", temp_state, max_domains=5)
    batch_id = batch_res["batch_id"]

    run_id = "run_empty_1"
    ext_id = "ext_empty_1"
    domain = "emptyco.test"

    await temp_state.save_research_run(
        run_id=run_id,
        input_email="user@emptyco.test",
        normalized_email="user@emptyco.test",
        domain=domain,
        status="completed",
    )
    await temp_state.save_company_website(
        website_id="site_empty_1",
        run_id=run_id,
        domain=domain,
        final_url="https://emptyco.test/",
        verification_status="verified",
        confidence=1.0,
    )
    # Unavailable findings
    await temp_state.save_research_finding(
        finding_id="f_ov_unavail",
        run_id=run_id,
        extraction_run_id=ext_id,
        domain=domain,
        finding_type="overview",
        value_text="unavailable",
        verification_status="unavailable",
        reason="No about section detected on scanned pages",
    )
    await temp_state.save_research_finding(
        finding_id="f_svc_unavail",
        run_id=run_id,
        extraction_run_id=ext_id,
        domain=domain,
        finding_type="product_service",
        value_text="unavailable",
        verification_status="unavailable",
        reason="No services list located on scanned pages",
    )
    await temp_state.save_research_finding(
        finding_id="f_vis_unavail",
        run_id=run_id,
        extraction_run_id=ext_id,
        domain=domain,
        finding_type="vision",
        value_text="unavailable",
        verification_status="unavailable",
        reason="No vision statement found on scanned pages",
    )
    await temp_state.save_research_finding(
        finding_id="f_mis_unavail",
        run_id=run_id,
        extraction_run_id=ext_id,
        domain=domain,
        finding_type="mission",
        value_text="unavailable",
        verification_status="unavailable",
        reason="No mission statement found on scanned pages",
    )
    # Unavailable person
    await temp_state.save_research_person(
        person_id="p_unavail",
        run_id=run_id,
        extraction_run_id="ext_p_unavail",
        domain=domain,
        full_name="unavailable",
        title="unavailable",
        role_category="unavailable",
        verification_status="unavailable",
        reason="No named officers detected on team page",
    )

    await temp_state.update_import_domain_status(
        batch_id,
        domain,
        status="completed",
        run_id=run_id,
    )

    export_dir = tmp_path / "exports"
    export_res = await export_batch_results(batch_id, temp_state, export_dir=export_dir)

    with open(export_res["contacts_results"], "r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))
        assert len(reader) == 1
        row = reader[0]

        assert row["overview"] == "unavailable: No about section detected on scanned pages"
        assert row["overview_status"] == "unavailable"
        assert row["services"] == "unavailable: No services list located on scanned pages"
        assert row["vision"] == "unavailable: No vision statement found on scanned pages"
        assert row["mission"] == "unavailable: No mission statement found on scanned pages"
        assert row["decision_makers"] == "unavailable: No named officers detected on team page"


@pytest.mark.asyncio
async def test_batch_export_csv_quoting_and_roundtrip(temp_state, tmp_path):
    """CSV export properly quotes fields with commas, quotes, and semicolons and roundtrips with constant columns."""
    await temp_state.init_db()
    # Name with comma and quotes
    csv_content = 'Name,Email\n"Taylor, Jordan ""JT""",lead@quoting.test\n'.encode("utf-8")
    batch_res = await create_batch_from_file(csv_content, "quoting_test.csv", temp_state, max_domains=5)
    batch_id = batch_res["batch_id"]

    run_id = "run_quoting_1"
    ext_id = "ext_quoting_1"
    domain = "quoting.test"

    await temp_state.save_research_run(
        run_id=run_id,
        input_email="lead@quoting.test",
        normalized_email="lead@quoting.test",
        domain=domain,
        status="completed",
    )
    await temp_state.save_company_website(
        website_id="site_quote_1",
        run_id=run_id,
        domain=domain,
        final_url="https://quoting.test/",
        verification_status="verified",
        confidence=1.0,
    )
    # Findings with quotes, commas, and semicolons
    await temp_state.save_research_finding(
        finding_id="f_ov_quote",
        run_id=run_id,
        extraction_run_id=ext_id,
        domain=domain,
        finding_type="overview",
        value_text='Specialists in "Enterprise AI", data pipelines, and security; certified.',
        verification_status="verified",
        confidence=0.9,
    )
    await temp_state.save_research_finding(
        finding_id="f_svc_quote",
        run_id=run_id,
        extraction_run_id=ext_id,
        domain=domain,
        finding_type="product_service",
        value_text='Custom "SaaS" platforms, microservices, and APIs; 24/7 SLA',
        verification_status="verified",
        confidence=0.9,
    )
    await temp_state.save_research_person(
        person_id="p_quote",
        run_id=run_id,
        extraction_run_id="ext_p_quote",
        domain=domain,
        full_name='Dr. Evelyn "Evie" Wright, PhD',
        title="Vice President, Engineering & Product",
        role_category="vp",
        verification_status="verified",
        evidence_text='Quoted statement: "We build systems, scale fast, and innovate; always."',
        source_url="https://quoting.test/leadership",
    )

    await temp_state.update_import_domain_status(
        batch_id,
        domain,
        status="completed",
        run_id=run_id,
    )

    export_dir = tmp_path / "exports"
    export_res = await export_batch_results(batch_id, temp_state, export_dir=export_dir)

    # 1. Verify contacts_results.csv raw column count with csv.reader
    with open(export_res["contacts_results"], "r", encoding="utf-8") as f:
        reader = list(csv.reader(f))
        assert len(reader) == 2  # 1 header + 1 data row
        header_len = len(reader[0])
        assert header_len == 17
        data_len = len(reader[1])
        assert data_len == header_len, f"Column count mismatch: {data_len} != {header_len}"

    # 2. Verify decision_makers.csv raw column count with csv.reader
    with open(export_res["decision_makers"], "r", encoding="utf-8") as f:
        reader = list(csv.reader(f))
        assert len(reader) == 2  # 1 header + 1 data row
        header_len = len(reader[0])
        assert header_len == 7
        data_len = len(reader[1])
        assert data_len == header_len, f"Column count mismatch: {data_len} != {header_len}"

    # 3. Verify clean DictReader roundtrip
    with open(export_res["contacts_results"], "r", encoding="utf-8") as f:
        dict_rows = list(csv.DictReader(f))
        assert dict_rows[0]["overview"] == 'Specialists in "Enterprise AI", data pipelines, and security; certified.'
        assert 'Custom "SaaS" platforms, microservices, and APIs; 24/7 SLA [verified]' in dict_rows[0]["services"]
