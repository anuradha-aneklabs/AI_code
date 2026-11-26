"""Harvey Production Readiness End-to-End Verification Suite.

Executes all 10 real validation scenarios on an isolated database:
1. Single company research (cargill.com)
2. Multiple domains via Excel import (Maam_Pilot_Records.xlsx)
3. Different industries classification (Product vs Services vs Other)
4. Different geographies & multi-part TLD handling
5. Invalid, malformed & duplicate records handling
6. Blocked/bot-challenged website handling (bearcom.com Cloudflare 403)
7. Interruption, crash recovery & resume pipeline
8. API polling safety & orphaned batch handling
9. UI and DB consistency verification
10. CSV and HTML export verification
"""

import asyncio
import base64
import csv
import io
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

# Force isolated test DB before importing harvey modules
TEST_DB_PATH = Path("data/validation/harvey_prod_readiness.db").resolve()
os.environ["HARVEY_DB_PATH"] = str(TEST_DB_PATH)
os.environ["HARVEY_ALLOW_REAL_DB_WRITE"] = "1"

from harvey.state import StateManager
from harvey.single_domain_research import (
    run_single_email_research,
    verify_website,
    ResearchConfig,
    DomainRateLimiter,
)
from harvey.company_narrative import extract_company_narrative
from harvey.decision_makers import find_decision_makers
from harvey.batch_research import (
    parse_import_file,
    create_batch_from_file,
    run_batch,
    export_batch_results,
    mask_email,
    mask_phone,
)
from harvey.entity_cross_check import check_entity_match
from harvey.software_classifier import classify_software_business
from starlette.testclient import TestClient
import harvey.dashboard as dash
import httpx


async def run_scenario_1_single_company(state: StateManager) -> dict:
    print("\n" + "=" * 70)
    print("SCENARIO 1: Single Company Research (cargill.com)")
    print("=" * 70)
    email = "sunil@cargill.com"
    t0 = time.time()
    
    # 1. Single email research
    res = await run_single_email_research(email=email, state=state)
    run_id = res.get("id") or res.get("run_id")
    print(f"Research run created: ID={run_id}, status={res.get('status')}")
    
    # 2. Extract company narrative
    narrative = await extract_company_narrative(run_id, state=state)
    overview = ""
    vision = ""
    mission = ""
    services = []
    if isinstance(narrative, list):
        for f in narrative:
            ftype = f.get("finding_type")
            fval = f.get("value_text", "")
            if ftype == "overview" and not overview:
                overview = fval
            elif ftype == "vision" and not vision:
                vision = fval
            elif ftype == "mission" and not mission:
                mission = fval
            elif ftype in ("services", "product_service"):
                services.append(fval)
    elif isinstance(narrative, dict):
        overview = narrative.get("overview", {}).get("value_text", "")
        vision = narrative.get("vision", {}).get("value_text", "")
        mission = narrative.get("mission", {}).get("value_text", "")
        services = [s.get("value_text") for s in narrative.get("services", [])]
    
    # 3. Decision makers
    people = await find_decision_makers(run_id, state=state)
    
    # Verify DB records
    website = await state.get_company_website(run_id)
    pages = await state.get_source_pages(run_id)
    elapsed = round(time.time() - t0, 2)
    
    print(f"Website Verification: status={website.get('verification_status')}, url={website.get('final_url')}")
    print(f"Pages Crawled: {len(pages)}")
    print(f"Overview Extracted: {bool(overview)} ({len(overview)} chars)")
    print(f"Vision Extracted: {bool(vision)}")
    print(f"Mission Extracted: {bool(mission)}")
    print(f"Services Found: {len(services)}")
    print(f"Decision Makers: {len(people)}")
    print(f"Elapsed Time: {elapsed}s")
    
    success = website.get("verification_status") in ("verified", "unverified") and len(pages) > 0
    return {
        "scenario": 1,
        "name": "Single Company Research (cargill.com)",
        "status": "PASS" if success else "FAIL",
        "elapsed_seconds": elapsed,
        "details": {
            "domain": "cargill.com",
            "run_id": run_id,
            "website_status": website.get("verification_status"),
            "final_url": website.get("final_url"),
            "pages_crawled": len(pages),
            "decision_makers_count": len(people),
        },
    }


async def run_scenario_2_excel_multi_domain(state: StateManager) -> dict:
    print("\n" + "=" * 70)
    print("SCENARIO 2: Multiple Domains via Excel Import (Maam_Pilot_Records.xlsx)")
    print("=" * 70)
    excel_path = Path("data/input/Maam_Pilot_Records.xlsx")
    if not excel_path.exists():
        print(f"XLSX file not found at {excel_path}")
        return {"scenario": 2, "name": "Excel Multi-Domain", "status": "SKIPPED", "error": "File not found"}
        
    t0 = time.time()
    content_bytes = excel_path.read_bytes()
    
    batch_res = await create_batch_from_file(
        filename="Maam_Pilot_Records.xlsx",
        content_bytes=content_bytes,
        state=state,
        max_domains=2,  # Cap at 2 domains for economical run
    )
    batch_id = batch_res["batch_id"]
    print(f"Batch created: {batch_id}")
    print(f"Total Rows: {batch_res['total_rows']}, Valid Rows: {batch_res['valid_rows']}, Unique Domains: {batch_res['unique_domains']}")
    print(f"Column Mapping: {batch_res['column_mapping']}")
    
    # Run batch sequentially
    run_res = await run_batch(batch_id, state=state, max_domains=2)
    elapsed = round(time.time() - t0, 2)
    print(f"Batch execution finished in {elapsed}s: status={run_res['status']}")
    print(f"Completed Domains: {run_res['completed_domains']}, Failed: {run_res['failed_domains']}")
    
    success = run_res["status"] in ("completed", "partial") and run_res["completed_domains"] > 0
    return {
        "scenario": 2,
        "name": "Excel Multi-Domain Import",
        "status": "PASS" if success else "FAIL",
        "elapsed_seconds": elapsed,
        "details": {
            "batch_id": batch_id,
            "total_rows": batch_res["total_rows"],
            "unique_domains": batch_res["unique_domains"],
            "completed_domains": run_res["completed_domains"],
            "batch_status": run_res["status"],
        },
    }


async def run_scenario_3_industry_classification() -> dict:
    print("\n" + "=" * 70)
    print("SCENARIO 3: Industry & Software Product Classification")
    print("=" * 70)
    t0 = time.time()
    
    # Test 1: Real SaaS product page
    saas_html = "<html><head><title>CloudMetrics - Analytics Platform</title></head><body><h1>The Modern Growth Platform for Teams</h1><p>Simple, transparent pricing. Start your free trial today or book a demo.</p><a href='/signup'>Sign Up Free</a><a href='/login'>Dashboard Login</a></body></html>"
    c1, ev1 = classify_software_business(saas_html, title="CloudMetrics - Analytics Platform")
    print(f"Test 1 (SaaS platform): {c1} (evidence={ev1[:60]}...)")
    
    # Test 2: Custom development agency
    agency_html = "<html><head><title>Apex Software Agency</title></head><body><h1>Bespoke Web Development & Consulting</h1><p>We build software for startups. Hire our dedicated engineering team.</p><a href='/contact'>Request a Quote</a></body></html>"
    c2, ev2 = classify_software_business(agency_html, title="Apex Software Agency")
    print(f"Test 2 (Dev Agency): {c2} (evidence={ev2[:60]}...)")
    
    # Test 3: Physical logistics / agriculture
    agri_html = "<html><head><title>GrainCorp Agriculture</title></head><body><h1>Grain Supply & Elevators</h1><p>Bulk grain distribution and livestock feed.</p></body></html>"
    c3, ev3 = classify_software_business(agri_html, title="GrainCorp Agriculture")
    print(f"Test 3 (Physical Commodities): {c3} (evidence={ev3[:60]}...)")
    
    elapsed = round(time.time() - t0, 2)
    success = (
        c1 == "software_product"
        and c2 == "software_services"
        and c3 == "other"
    )
    return {
        "scenario": 3,
        "name": "Industry & Software Product Classification",
        "status": "PASS" if success else "FAIL",
        "elapsed_seconds": elapsed,
        "details": {
            "saas_classification": c1,
            "agency_classification": c2,
            "commodity_classification": c3,
        },
    }


async def run_scenario_4_geographies_and_tlds() -> dict:
    print("\n" + "=" * 70)
    print("SCENARIO 4: Geography & Multi-Part TLD Handling (.in, .co.uk, .com)")
    print("=" * 70)
    t0 = time.time()
    from harvey.single_domain_research import is_same_registrable_domain, extract_domain, EmailValidationResult
    
    # 1. Indian TLDs
    d1 = is_same_registrable_domain("delhidentalcare.co.in", "www.delhidentalcare.co.in")
    d2 = is_same_registrable_domain("delhidentalcare.co.in", "otherdental.co.in")
    
    # 2. UK TLDs
    d3 = is_same_registrable_domain("firm.co.uk", "sub.firm.co.uk")
    d4 = is_same_registrable_domain("firm.co.uk", "firm.com")
    
    # 3. IDN / Internationalized domain
    val = EmailValidationResult(
        valid_syntax=True,
        is_free_mail=False,
        normalized_email="contact@müller.de",
        domain="müller.de",
    )
    d_ext = extract_domain(val)
    print(f"IDN Domain extraction: {d_ext.domain} (status={d_ext.status})")
    
    elapsed = round(time.time() - t0, 2)
    success = d1 is True and d2 is False and d3 is True and d4 is False and "xn--" in d_ext.domain
    return {
        "scenario": 4,
        "name": "Geography & Multi-Part TLD Handling",
        "status": "PASS" if success else "FAIL",
        "elapsed_seconds": elapsed,
        "details": {
            "co_in_same": d1,
            "co_in_diff": not d2,
            "co_uk_same": d3,
            "cross_tld_diff": not d4,
            "idn_punycode": d_ext.domain,
        },
    }


async def run_scenario_5_invalid_and_duplicates(state: StateManager) -> dict:
    print("\n" + "=" * 70)
    print("SCENARIO 5: Invalid & Duplicate Records Handling")
    print("=" * 70)
    t0 = time.time()
    
    csv_data = (
        "Name,Email,Company,Phone\n"
        "Valid Lead 1,alice@alpha-corp-test.com,Alpha Corp,555-1111\n"
        "Duplicate Lead,bob@alpha-corp-test.com,Alpha Corp,555-2222\n"
        "Consumer Lead,charlie@gmail.com,Personal,555-3333\n"
        "Invalid Syntax,not-an-email,Bad Corp,555-4444\n"
        "Missing Domain,dave@,Incomplete Corp,555-5555\n"
    )
    
    batch_res = await create_batch_from_file(
        filename="test_hygiene.csv",
        content_bytes=csv_data.encode("utf-8"),
        state=state,
    )
    batch_id = batch_res["batch_id"]
    rows = await state.get_import_rows(batch_id)
    domains = await state.get_import_domains(batch_id)
    
    row_statuses = [r["row_status"] for r in rows]
    print(f"Total Rows: {len(rows)}, Parsed Statuses: {row_statuses}")
    print(f"Enqueued Commercial Domains: {[d['domain'] for d in domains]}")
    
    elapsed = round(time.time() - t0, 2)
    success = (
        row_statuses[0] == "valid"
        and row_statuses[1] == "duplicate_domain"
        and row_statuses[2] in ("free_mail", "consumer_email", "skipped: consumer email")
        and row_statuses[3] in ("invalid", "invalid_email")
        and row_statuses[4] in ("invalid", "invalid_email")
        and len(domains) == 1
        and domains[0]["domain"] == "alpha-corp-test.com"
    )
    return {
        "scenario": 5,
        "name": "Invalid & Duplicate Records Handling",
        "status": "PASS" if success else "FAIL",
        "elapsed_seconds": elapsed,
        "details": {
            "row_statuses": row_statuses,
            "enqueued_domains": [d["domain"] for d in domains],
        },
    }


async def run_scenario_6_blocked_website_bot_challenge() -> dict:
    print("\n" + "=" * 70)
    print("SCENARIO 6: Blocked/Unavailable Website (bearcom.com Bot Challenge)")
    print("=" * 70)
    t0 = time.time()
    config = ResearchConfig(timeout_seconds=5.0)
    rate_limiter = DomainRateLimiter()
    
    async with httpx.AsyncClient(verify=False) as client:
        res = await verify_website("bearcom.com", client=client, config=config, rate_limiter=rate_limiter)
    
    elapsed = round(time.time() - t0, 2)
    print(f"Verification status: {res.verification_status}")
    print(f"HTTP Status: {res.http_status}")
    print(f"Evidence text: {res.evidence_text}")
    print(f"Elapsed: {elapsed}s (Fast short-circuit on Cloudflare challenge)")
    
    # Must be unavailable with honest bot challenge or 403 reason, completed rapidly without 4 redundant retries
    success = res.verification_status == "unavailable" and ("bot_challenge" in res.evidence_text or "403" in res.evidence_text or "HTTP" in res.evidence_text)
    return {
        "scenario": 6,
        "name": "Blocked Website / Bot Challenge Fast Abort",
        "status": "PASS" if success else "FAIL",
        "elapsed_seconds": elapsed,
        "details": {
            "verification_status": res.verification_status,
            "http_status": res.http_status,
            "evidence_text": res.evidence_text,
            "short_circuit_seconds": elapsed,
        },
    }


async def run_scenario_7_interruption_and_recovery(state: StateManager) -> dict:
    print("\n" + "=" * 70)
    print("SCENARIO 7: Interruption, Crash Recovery & Batch Resumability")
    print("=" * 70)
    t0 = time.time()
    
    csv_data = (
        "Name,Email,Company\n"
        "Lead 1,test1@cargill.com,Cargill\n"
        "Lead 2,test2@example.org,Example Org\n"
    )
    batch_res = await create_batch_from_file(
        filename="crash_test.csv",
        content_bytes=csv_data.encode("utf-8"),
        state=state,
    )
    batch_id = batch_res["batch_id"]
    
    # Simulate a sudden server kill during execution: batch is left as 'running'
    await state.update_import_batch_status(batch_id, status="running")
    await state.update_import_domain_status(batch_id, "cargill.com", status="running")
    
    b_before = await state.get_import_batch(batch_id)
    print(f"Simulated crashed batch state in SQLite: status={b_before['status']}")
    
    # Step 1: Server startup / Lifespan reconciliation
    reconciled_count = await state.reconcile_interrupted_batches()
    print(f"Reconciled orphaned batches count: {reconciled_count}")
    
    b_after = await state.get_import_batch(batch_id)
    d_after = await state.get_import_domains(batch_id)
    print(f"Post-reconciliation batch status: {b_after['status']}")
    print(f"Post-reconciliation domain 1 status: {d_after[0]['status']}")
    
    assert b_after["status"] == "interrupted"
    assert d_after[0]["status"] == "interrupted"
    
    # Step 2: Resume batch execution
    print("Triggering batch resume (resume=True)...")
    resume_res = await run_batch(batch_id, state=state, resume=True, max_domains=1)
    b_final = await state.get_import_batch(batch_id)
    print(f"Batch status after resume: {b_final['status']}")
    
    elapsed = round(time.time() - t0, 2)
    success = b_after["status"] == "interrupted" and b_final["status"] in ("completed", "partial")
    return {
        "scenario": 7,
        "name": "Interruption & Crash Recovery",
        "status": "PASS" if success else "FAIL",
        "elapsed_seconds": elapsed,
        "details": {
            "initial_crash_status": b_before["status"],
            "reconciled_status": b_after["status"],
            "final_resumed_status": b_final["status"],
        },
    }


def run_scenario_8_api_polling_safety(state: StateManager) -> dict:
    print("\n" + "=" * 70)
    print("SCENARIO 8: API Polling Safety & Dashboard Integration")
    print("=" * 70)
    t0 = time.time()
    
    dash._state = lambda: state
    dash.DB_PATH = Path(state.db_path)
    dash.token_store.get = lambda: "test-auth-token"
    
    client = TestClient(
        dash.app,
        base_url="http://127.0.0.1:5566",
        headers={
            "Authorization": "Bearer test-auth-token",
            "Host": "127.0.0.1:5566",
            "Sec-Fetch-Site": "same-origin",
        },
    )
    
    # 1. Health endpoint
    h_resp = client.get("/api/health")
    assert h_resp.status_code == 200
    
    # 2. List batches
    b_resp = client.get("/api/research/batches")
    assert b_resp.status_code == 200
    batches = b_resp.json().get("batches", [])
    print(f"Retrieved {len(batches)} batches via API")
    
    if batches:
        test_batch_id = batches[0]["id"]
        # 3. Details endpoint
        d_resp = client.get(f"/api/research/batch/{test_batch_id}/details")
        assert d_resp.status_code == 200
        d_json = d_resp.json()
        print(f"Batch details retrieved: is_running={d_json.get('is_running')}")
        print(f"Metrics: {d_json.get('metrics')}")
        assert d_json.get("is_running") is False
    
    elapsed = round(time.time() - t0, 2)
    return {
        "scenario": 8,
        "name": "API Polling Safety & Dashboard Integration",
        "status": "PASS",
        "elapsed_seconds": elapsed,
        "details": {"api_health": h_resp.status_code, "batches_listed": len(batches)},
    }


async def run_scenario_9_ui_db_consistency(state: StateManager) -> dict:
    print("\n" + "=" * 70)
    print("SCENARIO 9: UI and Database Mathematical Consistency")
    print("=" * 70)
    t0 = time.time()
    
    batches = await state.list_import_batches(limit=5)
    if not batches:
        return {"scenario": 9, "name": "UI/DB Consistency", "status": "SKIPPED"}
        
    test_batch = batches[0]
    batch_id = test_batch["id"]
    
    # Direct DB queries
    async with state._connect() as db:
        cur = await db.execute("SELECT COUNT(*) FROM import_rows WHERE batch_id = ?", (batch_id,))
        raw_row_count = (await cur.fetchone())[0]
        cur = await db.execute("SELECT COUNT(*) FROM import_domains WHERE batch_id = ?", (batch_id,))
        raw_domain_count = (await cur.fetchone())[0]
        cur = await db.execute(
            """SELECT COUNT(*) FROM company_websites cw 
               JOIN import_domains id ON cw.run_id = id.run_id 
               WHERE id.batch_id = ? AND cw.verification_status = 'verified'""",
            (batch_id,),
        )
        raw_verified_websites = (await cur.fetchone())[0]
    
    # API queries
    dash._state = lambda: state
    dash.DB_PATH = Path(state.db_path)
    dash.token_store.get = lambda: "test-auth-token"
    client = TestClient(
        dash.app,
        base_url="http://127.0.0.1:5566",
        headers={"Authorization": "Bearer test-auth-token", "Host": "127.0.0.1:5566", "Sec-Fetch-Site": "same-origin"},
    )
    resp = client.get(f"/api/research/batch/{batch_id}/details")
    metrics = resp.json().get("metrics", {})
    
    print(f"Raw SQLite Rows: {raw_row_count} | API total_contacts: {metrics.get('total_contacts')}")
    print(f"Raw SQLite Domains: {raw_domain_count} | API unique_domains: {metrics.get('unique_domains')}")
    print(f"Raw SQLite Verified Sites: {raw_verified_websites} | API websites_verified: {metrics.get('websites_verified')}")
    
    consistent = (
        raw_row_count == metrics.get("total_contacts")
        and raw_domain_count == metrics.get("unique_domains")
        and raw_verified_websites == metrics.get("websites_verified")
    )
    elapsed = round(time.time() - t0, 2)
    return {
        "scenario": 9,
        "name": "UI and Database Consistency",
        "status": "PASS" if consistent else "FAIL",
        "elapsed_seconds": elapsed,
        "details": {
            "row_count_match": raw_row_count == metrics.get("total_contacts"),
            "domain_count_match": raw_domain_count == metrics.get("unique_domains"),
            "verified_sites_match": raw_verified_websites == metrics.get("websites_verified"),
        },
    }


async def run_scenario_10_csv_html_export(state: StateManager) -> dict:
    print("\n" + "=" * 70)
    print("SCENARIO 10: CSV and HTML Export Verification")
    print("=" * 70)
    t0 = time.time()
    
    batches = await state.list_import_batches(limit=5)
    if not batches:
        return {"scenario": 10, "name": "Export Verification", "status": "SKIPPED"}
        
    batch_id = batches[0]["id"]
    
    # 1. Export CSV
    export_paths = await export_batch_results(batch_id, state=state)
    contacts_csv = Path(export_paths["contacts_results"])
    decision_makers_csv = Path(export_paths["decision_makers"])
    csv_text = contacts_csv.read_text(encoding="utf-8")
    csv_reader = list(csv.reader(io.StringIO(csv_text)))
    print(f"Exported Contacts CSV: {len(csv_reader)} lines (File: {contacts_csv.name})")
    print(f"Exported Decision Makers CSV: (File: {decision_makers_csv.name})")
    
    # 2. Export HTML via API
    dash._state = lambda: state
    dash.DB_PATH = Path(state.db_path)
    dash.token_store.get = lambda: "test-auth-token"
    client = TestClient(
        dash.app,
        base_url="http://127.0.0.1:5566",
        headers={"Authorization": "Bearer test-auth-token", "Host": "127.0.0.1:5566", "Sec-Fetch-Site": "same-origin"},
    )
    html_resp = client.get(f"/api/research/batch/{batch_id}/export?format=html")
    print(f"Exported HTML via API: status={html_resp.status_code}, bytes={len(html_resp.content)}")
    html_text = html_resp.text
    
    # Verify proper structure
    has_html_tags = html_resp.status_code == 200 and ("<!DOCTYPE html>" in html_text or "<html" in html_text or "<table" in html_text)
    has_csv_content = len(csv_reader) >= 2
    
    elapsed = round(time.time() - t0, 2)
    success = has_html_tags and has_csv_content
    return {
        "scenario": 10,
        "name": "CSV and HTML Export Verification",
        "status": "PASS" if success else "FAIL",
        "elapsed_seconds": elapsed,
        "details": {
            "csv_rows": len(csv_reader),
            "contacts_csv": contacts_csv.name,
            "decision_makers_csv": decision_makers_csv.name,
            "html_bytes": len(html_resp.content),
            "html_valid": has_html_tags,
        },
    }


async def main():
    print("=" * 70)
    print("STARTING HARVEY PRODUCTION READINESS E2E VERIFICATION")
    print(f"Isolated Validation DB: {TEST_DB_PATH}")
    print("=" * 70)
    
    state = StateManager(db_path=str(TEST_DB_PATH))
    await state.init_db()
    
    results = []
    
    results.append(await run_scenario_1_single_company(state))
    results.append(await run_scenario_2_excel_multi_domain(state))
    results.append(await run_scenario_3_industry_classification())
    results.append(await run_scenario_4_geographies_and_tlds())
    results.append(await run_scenario_5_invalid_and_duplicates(state))
    results.append(await run_scenario_6_blocked_website_bot_challenge())
    results.append(await run_scenario_7_interruption_and_recovery(state))
    results.append(run_scenario_8_api_polling_safety(state))
    results.append(await run_scenario_9_ui_db_consistency(state))
    results.append(await run_scenario_10_csv_html_export(state))
    
    print("\n" + "=" * 70)
    print("PRODUCTION READINESS VERIFICATION SUMMARY")
    print("=" * 70)
    
    all_passed = True
    for r in results:
        status = r["status"]
        if status != "PASS":
            all_passed = False
        print(f"Scenario {r['scenario']}: {r['name']:<45} [{status}] ({r.get('elapsed_seconds', 0)}s)")
    
    out_json = Path("demo/PRODUCTION_READINESS_E2E_RESULTS.json")
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nDetailed evidence written to {out_json}")
    
    if all_passed:
        print("\nALL 10 SCENARIOS PASSED SUCCESSFULLY!")
    else:
        print("\nSOME SCENARIOS FAILED OR BLOCKED.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
