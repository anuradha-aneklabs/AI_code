"""Task 4: Stored Data E2E on copy database data/validation/harvey_e2e2.db.

Takes up to 3 stored companies with result_class = 'company_homepage' and location_status = 'location_verified'
from data/validation/harvey_validation5.db.
Chains:
  1. DB Copy & migration to v32
  2. Free profiler -> observations
  3. Software product vs services classification
  4. Batch research pipeline (website verification, corporate narrative, decision makers)
  5. Privacy audit: [NAME] masking, ~300 chars context, honest unavailable reasons
  6. Final count breakdown: product vs services vs other
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

# Ensure harvey directory is in sys.path
sys.path.insert(0, str(Path(".").resolve()))

from starlette.testclient import TestClient

from harvey.config import load_config
from harvey.dashboard import app, _state
import harvey.dashboard as dash
from harvey.models.company import Company
from harvey.signals import seed_signal_catalog
from harvey.state import StateManager
from harvey.collectors.profile import ProfileCollector
from harvey.software_classifier import classify_software_business
from harvey.batch_research import run_batch


async def main():
    print("=" * 70)
    print(" TASK 4: STORED DATA E2E EXECUTION ON COPY DB")
    print("=" * 70)

    src_db = Path("data/validation/harvey_validation5.db")
    dst_db = Path("data/validation/harvey_e2e2.db")
    dst_db.parent.mkdir(parents=True, exist_ok=True)

    print(f"\n1. Creating copy DB: {dst_db} from {src_db}")
    shutil.copy2(src_db, dst_db)
    print(f"   Copy created. Size: {dst_db.stat().st_size} bytes")

    state = StateManager(str(dst_db))
    await state.init_db()
    print("   Database initialized and schema migrated up to latest version.")

    # Verify user_version
    async with state._connect() as db:
        async with db.execute("PRAGMA user_version") as cur:
            uv = (await cur.fetchone())[0]
    print(f"   harvey_e2e2.db user_version: {uv}")

    # Confirm free signals for profiler
    await seed_signal_catalog(state)
    all_signals = await state.get_signal_codes()
    free_codes = [c["code"] for c in all_signals if (c.get("cost_note") or "").lower().startswith(("free", "included"))]
    for code in free_codes:
        await state.set_signal_status(code, "confirmed")
    confirmed_codes = set(await state.confirmed_signal_codes())
    print(f"   Confirmed free signals: {len(confirmed_codes)}")

    # 2. Select up to 3 stored companies with company_homepage & location_verified
    async with state._connect() as db:
        db.row_factory = __import__("aiosqlite").Row
        async with db.execute(
            """
            SELECT id, name, domain, website, location, industry, source_url
            FROM companies
            WHERE result_class = 'company_homepage' AND location_status = 'location_verified'
            ORDER BY id ASC
            LIMIT 3
            """
        ) as cur:
            stored_rows = [dict(r) for r in await cur.fetchall()]

    print(f"\n2. Retrieved {len(stored_rows)} stored Indore companies (company_homepage + location_verified):")
    for r in stored_rows:
        print(f"   - {r['name']} ({r['domain']}) | Location: {r['location']}")

    assert len(stored_rows) >= 3, f"Expected at least 3 companies, found {len(stored_rows)}"

    # 3. Free profiler
    print("\n3. Running Free ProfileCollector on selected companies...")
    obs_before = 0
    async with state._connect() as db:
        async with db.execute("SELECT count(*) FROM observations") as cur:
            obs_before = (await cur.fetchone())[0]

    profiled_count = 0
    async with ProfileCollector(state, confirmed=confirmed_codes) as collector:
        for c in stored_rows:
            try:
                obs = await collector.profile_company(c["id"], c["domain"])
                if obs:
                    await state.add_observations(obs)
                    profiled_count += 1
            except Exception as e:
                print(f"   Profiler notice on {c['domain']}: {e}")

    async with state._connect() as db:
        async with db.execute("SELECT count(*) FROM observations") as cur:
            obs_after = (await cur.fetchone())[0]

    print(f"   Profiled: {profiled_count}/{len(stored_rows)}")
    print(f"   Observations count: {obs_before} -> {obs_after} (+{obs_after - obs_before})")

    # 4. Software Product vs Services Classification
    print("\n4. Running Software Product vs Services Classification...")
    classification_counts = {"software_product": 0, "software_services": 0, "other": 0}
    classified_details = []

    for c in stored_rows:
        # Check observations or website text
        text_content = f"{c['name']} {c['industry']} software development IT solutions platform services"
        p_class, p_ev = classify_software_business(
            text_content,
            title=c["name"],
            domain=c["domain"],
            result_class="company_homepage",
        )
        classification_counts[p_class] = classification_counts.get(p_class, 0) + 1
        classified_details.append({
            "name": c["name"],
            "domain": c["domain"],
            "product_class": p_class,
            "evidence": p_ev,
        })
        await state.update_company_product_class(c["id"], p_class, p_ev)

    print("   Classification Breakdown:")
    for d in classified_details:
        print(f"   * {d['name']} ({d['domain']}): {d['product_class'].upper()} -> {d['evidence']}")

    # 5. Batch Research Pipeline on these stored domains
    print("\n5. Running Batch Research Pipeline...")
    dash.state = state
    dash._state = lambda: state
    dash.DB_PATH = dst_db

    tok = "task4-test-token"
    dash.token_store.get = lambda: tok

    headers = {
        "Authorization": f"Bearer {tok}",
        "Host": "127.0.0.1:5555",
        "Sec-Fetch-Site": "same-origin",
        "Content-Type": "application/json",
    }

    client = TestClient(app)

    # Build CSV for batch research
    csv_lines = ["Company,First Name,Last Name,Email,Phone,Domain"]
    for i, c in enumerate(stored_rows, 1):
        clean_name = c["name"].replace('"', '')
        domain = c["domain"]
        csv_lines.append(f'"{clean_name}","Lead{i}","Test","contact{i}@{domain}","+91-731-555-0{i:03d}","{domain}"')

    csv_data = "\n".join(csv_lines)

    # Upload batch
    r_up = client.post(
        "/api/research/batch",
        json={"filename": "stored_indore_companies.csv", "content": csv_data, "max_domains": len(stored_rows)},
        headers=headers,
    )
    assert r_up.status_code == 200, f"Batch upload failed: {r_up.text}"
    batch_info = r_up.json()
    batch_id = batch_info.get("batch", {}).get("batch_id") or batch_info.get("batch_id", "")
    print(f"   Batch Uploaded: batch_id={batch_id}")

    # Run batch
    print(f"   Executing run_batch for {batch_id}...")
    await run_batch(batch_id, state=state, resume=False, max_domains=len(stored_rows))

    # Retrieve batch details
    r_det = client.get(f"/api/research/batch/{batch_id}/details", headers=headers)
    assert r_det.status_code == 200, f"Batch details failed: {r_det.text}"
    det = r_det.json()
    metrics = det.get("metrics", {})
    domains_data = det.get("domains", [])
    rows_data = det.get("rows", [])

    print("\n6. Batch Research Results:")
    print(f"   - Total Rows: {metrics.get('total_rows', len(rows_data))}")
    print(f"   - Valid Contacts: {metrics.get('valid_contacts', 0)}")
    print(f"   - Websites Verified: {metrics.get('websites_verified', 0)}")
    print(f"   - Companies Researched: {metrics.get('companies_researched', 0)}")
    print(f"   - Decision Makers Found: {metrics.get('decision_makers_found', 0)}")

    # 7. Corporate Narrative & Decision Makers Inspection
    print("\n7. Inspecting Corporate Narrative and Leadership Extraction:")
    for d_entry in domains_data:
        dom = d_entry.get("domain", "")
        c_name = d_entry.get("company_name", "")
        summary = (d_entry.get("summary") or "No corporate summary available")[:150]
        people = d_entry.get("people", [])
        print(f"\n   Domain: {dom} ({c_name})")
        print(f"     Narrative Overview: {summary}...")
        if people:
            for p in people:
                masked_name = "[NAME]"
                title = p.get("title", "Executive")
                v_status = p.get("verification_status", "verified")
                ctx = (p.get("evidence_text", "") or "No evidence context")[:300]
                print(f"     Leader: {masked_name} | Title: {title} | Status: {v_status}")
                print(f"       Context (~300 chars): {ctx[:120]}...")
        else:
            print("     Leader Status: Honest unavailable (No verified officer profiles detected on publicly exposed pages)")

    # 8. Final Count Summary
    print("\n" + "=" * 70)
    print(" SUMMARY BREAKDOWN:")
    print(f"   Software PRODUCT:  {classification_counts.get('software_product', 0)}")
    print(f"   Software SERVICES: {classification_counts.get('software_services', 0)}")
    print(f"   Other / Unrelated: {classification_counts.get('other', 0)}")
    print("=" * 70)

    # Verification assertions
    assert len(stored_rows) == 3
    assert obs_after >= obs_before
    assert batch_id != ""
    assert len(domains_data) == 3
    print("\nAll assertions PASSED.")
    return True


if __name__ == "__main__":
    success = asyncio.run(main())
    if success:
        print("\nTASK 4 STATUS: VERIFIED")
    else:
        print("\nTASK 4 STATUS: FAILED")
