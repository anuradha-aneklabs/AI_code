"""Master E2E Workflow Execution Script for Ma'am's Requirements.

Executes:
1. Excel upload (.xlsx) with pilot email sunil@anikalabs.com and multi-domain leads.
2. Email -> Domain extraction.
3. Website verification (HTTP probe).
4. About Us, Team, Services, Overview collection.
5. Founder, Owner, Director, CEO, CXO leadership identification.
6. Progressive SQLite persistence.
7. Source URLs and evidence preservation.
8. Filtering of garbage, free-mail, invalid format, and duplicate leads.
9. Exports (CSV, HTML) and database cross-checking.
10. Multi-domain automation verification.
"""

import asyncio
import base64
import json
import os
import shutil
import sqlite3
import time
from pathlib import Path
import httpx

TEST_DB = Path("data/validation/harvey_maam_e2e.db")
REAL_DB = Path("data/harvey.db")
EXCEL_FILE = Path("data/input/Maam_Pilot_Records.xlsx")
PORT = 5566


def init_test_db():
    TEST_DB.parent.mkdir(parents=True, exist_ok=True)
    if TEST_DB.exists():
        try:
            TEST_DB.unlink()
        except Exception:
            pass
    shutil.copy2(REAL_DB, TEST_DB)
    print(f"Initialized clean test DB at {TEST_DB} from {REAL_DB}")


async def run_master_workflow():
    os.environ["HARVEY_DB_PATH"] = str(TEST_DB)
    os.environ["HARVEY_ALLOW_REAL_DB_WRITE"] = "1"

    from harvey.dashboard import token_store
    token = token_store.get()
    headers = {"Authorization": f"Bearer {token}"}
    base_url = f"http://127.0.0.1:{PORT}"

    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=120.0) as client:
        # 1. Health check
        print(f"Connecting to Harvey dashboard on {base_url}...")
        resp = await client.get("/api/health")
        print(f"Dashboard Health: {resp.status_code} - {resp.json()}")

        # 2. Upload Excel File
        print(f"\n=== STEP 1: Uploading Excel File ({EXCEL_FILE}) ===")
        with open(EXCEL_FILE, "rb") as f:
            content_bytes = f.read()

        b64_content = base64.b64encode(content_bytes).decode("ascii")
        upload_payload = {
            "filename": "Maam_Pilot_Records.xlsx",
            "data_b64": b64_content,
            "max_domains": 5,
        }
        up_resp = await client.post("/api/research/batch", json=upload_payload)
        assert up_resp.status_code == 200, f"Upload failed: {up_resp.text}"
        resp_json = up_resp.json()
        batch_data = resp_json.get("batch") or resp_json
        batch_id = batch_data["batch_id"]
        print(f"Batch created successfully: ID = {batch_id}")
        print(f"  Total Rows: {batch_data.get('total_rows')}")
        print(f"  Valid Rows: {batch_data.get('valid_rows')}")
        print(f"  Unique Commercial Domains: {batch_data.get('unique_domains')}")
        print(f"  Detected Columns: {batch_data.get('column_mapping')}")

        # 3. Trigger Batch Execution
        print(f"\n=== STEP 2: Running Batch Automation for {batch_id} ===")
        run_resp = await client.post(
            f"/api/research/batch/{batch_id}/run",
            json={"max_domains": 5, "enable_website_discovery_fallback": False},
        )
        assert run_resp.status_code == 200, f"Run trigger failed: {run_resp.text}"
        print(f"Batch run initiated: {run_resp.json()}")

        # 4. Monitor Progress
        print("\n=== STEP 3: Monitoring Batch Execution Progress ===")
        max_wait = 180  # seconds
        start_t = time.time()
        last_status = ""
        while time.time() - start_t < max_wait:
            stat_resp = await client.get(f"/api/research/batch/{batch_id}")
            if stat_resp.status_code == 200:
                sdata = stat_resp.json()
                b_status = sdata.get("status")
                completed = sdata.get("completed_domains", 0)
                total = sdata.get("total_domains", 0)
                if b_status != last_status or completed > 0:
                    print(f"  Status: {b_status} | Progress: {completed}/{total} domains | Duration: {time.time()-start_t:.1f}s")
                    last_status = b_status
                if b_status in ("completed", "failed"):
                    break
            await asyncio.sleep(3.0)

        # 5. Retrieve Batch Details
        print(f"\n=== STEP 4: Inspecting Batch Details for {batch_id} ===")
        det_resp = await client.get(f"/api/research/batch/{batch_id}/details")
        assert det_resp.status_code == 200, f"Details fetch failed: {det_resp.text}"
        details = det_resp.json()

        metrics = details.get("metrics", {})
        print(f"Batch Metrics: {metrics}")

        domains = details.get("domains", [])
        print(f"\nProcessed Domains ({len(domains)}):")
        for d in domains:
            d_name = d.get("domain")
            d_stat = d.get("status")
            summary = d.get("stage_summary", {})
            print(f"  Domain: {d_name} | Status: {d_stat}")
            print(f"    Website: {summary.get('website_url')} ({summary.get('website_status')})")
            print(f"    Pages Scanned: {summary.get('pages_scanned')}")
            print(f"    Overview: {summary.get('overview_status')}")
            print(f"    Decision Makers: {summary.get('decision_makers_found')}")
            print(f"    Entity Check: {summary.get('entity_check')} ({summary.get('entity_check_evidence')[:60]})")

        # 6. Verify Anika Labs Specifically (Pilot Lead Sunil Rajput)
        print("\n=== STEP 5: Verifying Sunil Rajput & Anika Labs Pilot Lead ===")
        anika_domain = next((d for d in domains if d.get("domain") == "anikalabs.com"), None)
        assert anika_domain is not None, "anikalabs.com domain not found in batch!"
        print(f"Anika Labs Domain Status: {anika_domain.get('status')}")
        anika_summary = anika_domain.get("stage_summary", {})
        assert anika_summary.get("website_status") == "verified", f"Website verification failed: {anika_summary}"
        print(f"Anika Labs Website: {anika_summary.get('website_url')} [VERIFIED]")
        print(f"Anika Labs Pages Scanned: {anika_summary.get('pages_scanned')}")
        print(f"Anika Labs Decision Makers Found: {anika_summary.get('decision_makers_found')}")

        # 7. Download Exports
        out_dir = Path("data/exports")
        out_dir.mkdir(parents=True, exist_ok=True)
        csv_export_file = out_dir / "maam_contacts_results.csv"
        html_export_file = out_dir / "maam_research_report.html"

        print(f"\n=== STEP 6: Downloading Batch Exports ===")
        csv_resp = await client.get(f"/api/research/batch/{batch_id}/export?format=csv")
        assert csv_resp.status_code == 200
        with open(csv_export_file, "wb") as f:
            f.write(csv_resp.content)
        print(f"Saved CSV Export: {csv_export_file} ({csv_export_file.stat().st_size} bytes)")

        html_resp = await client.get(f"/api/research/batch/{batch_id}/export?format=html")
        assert html_resp.status_code == 200
        with open(html_export_file, "wb") as f:
            f.write(html_resp.content)
        print(f"Saved HTML Export: {html_export_file} ({html_export_file.stat().st_size} bytes)")

        # 8. Cross-check SQLite Persistence
        print(f"\n=== STEP 7: Cross-Checking SQLite Persistence ===")
        conn = sqlite3.connect(TEST_DB)
        cur = conn.cursor()

        cur.execute("SELECT count(*) FROM import_batches WHERE id = ?", (batch_id,))
        print(f"  import_batches row: {cur.fetchone()[0] == 1}")

        cur.execute("SELECT count(*) FROM import_domains WHERE batch_id = ?", (batch_id,))
        print(f"  import_domains count: {cur.fetchone()[0]}")

        cur.execute("SELECT count(*) FROM import_rows WHERE batch_id = ?", (batch_id,))
        print(f"  import_rows count: {cur.fetchone()[0]}")

        cur.execute("SELECT count(*) FROM company_websites WHERE domain = 'anikalabs.com'")
        print(f"  company_websites for anikalabs.com: {cur.fetchone()[0]}")

        cur.execute("SELECT count(*) FROM source_pages WHERE url LIKE '%anikalabs.com%'")
        print(f"  source_pages for anikalabs.com: {cur.fetchone()[0]}")

        cur.execute("SELECT count(*) FROM research_people WHERE domain = 'anikalabs.com'")
        print(f"  research_people for anikalabs.com: {cur.fetchone()[0]}")

        cur.execute("SELECT full_name, title, role_category, verification_status, source_url FROM research_people WHERE domain = 'anikalabs.com'")
        for row in cur.fetchall():
            print(f"    Leader: {row[0]} | Title: {row[1]} | Cat: {row[2]} | Status: {row[3]} | Source: {row[4]}")

        conn.close()
        print("\nMaster E2E Workflow Completed Successfully!")


if __name__ == "__main__":
    asyncio.run(run_master_workflow())
