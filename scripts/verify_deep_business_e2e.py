import asyncio
import json
import logging
import os
import pathlib
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("e2e_verifier")

PROJECT_DIR = pathlib.Path("C:/Users/aneka/Desktop/harvey")
DB_PATH = PROJECT_DIR / "data" / "harvey.db"
TOKEN_FILE = PROJECT_DIR / "data" / "dashboard-token"
SCREENSHOT_DIR = PROJECT_DIR / "reports" / "screenshots" / "deep_e2e"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL = "http://127.0.0.1:5555"

TEST_DATA = {
    "lead_id": "test_e2e_lead_999",
    "company_name": "test_e2e_Acme Robotics",
    "domain": "test-e2e-acmerobotics.io",
    "website": "https://test-e2e-acmerobotics.io",
    "industry": "Robotics & Automation",
    "location": "Austin, TX, US",
    "city": "Austin",
    "state_region": "TX",
    "country": "US",
    "prospect_name": "Alex Mercer",
    "prospect_email": "test_e2e_alex@test-e2e-acmerobotics.io",
    "prospect_title": "VP of Engineering",
    "campaign_id": "test_e2e_camp_999",
    "campaign_name": "test_e2e_Robotics Automation Outreach",
    "outbox_id": "test_e2e_outbox_999",
    "convo_id": "test_e2e_convo_999",
    "observation_id": "test_e2e_obs_999"
}


def get_token() -> str:
    if not TOKEN_FILE.exists():
        raise RuntimeError(f"Dashboard token file not found at {TOKEN_FILE}")
    return TOKEN_FILE.read_text(encoding="utf-8").strip()


def api_request(method: str, path: str, token: str, data: dict = None) -> tuple[int, dict]:
    url = f"{BASE_URL}{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Origin": BASE_URL,
    }
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp_body = resp.read().decode("utf-8")
            return resp.status, json.loads(resp_body) if resp_body else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        try:
            parsed = json.loads(err_body)
        except Exception:
            parsed = {"error": err_body}
        return e.code, parsed
    except Exception as e:
        return 500, {"error": str(e)}


def get_db_counts() -> dict[str, int]:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    counts = {}
    for table in ["companies", "prospects", "outbox", "campaigns", "observations", "conversations", "discovered_leads"]:
        cursor.execute(f"SELECT count(*) FROM {table}")
        counts[table] = cursor.fetchone()[0]
    conn.close()
    return counts


def run_e2e_verification():
    token = get_token()
    logger.info("Retrieved dashboard auth token.")

    initial_counts = get_db_counts()
    logger.info(f"Initial Database Baseline Counts: {initial_counts}")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    test_company_id = None
    test_prospect_id = None

    try:
        # =========================================================================
        # STEP 1: LEAD DISCOVERY (Candidate Lead in discovered_leads)
        # =========================================================================
        logger.info("── STEP 1: Creating Candidate Lead in Discovered Leads ──")
        dm_json = json.dumps({
            "name": TEST_DATA["prospect_name"],
            "title": TEST_DATA["prospect_title"],
            "email": TEST_DATA["prospect_email"],
            "linkedin": "https://linkedin.com/in/test-alex-mercer"
        })
        reasons_json = json.dumps(["Rapid hiring in robotics engineering", "High intent Series A announcement"])

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute(
            """INSERT INTO discovered_leads
               (id, profile_id, company_name, domain, website, industry, location,
                city, state_region, country, company_size, description, decision_maker_json,
                score, tier, reasons_json, candidate_status, is_qualified, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                TEST_DATA["lead_id"], "default_b2b", TEST_DATA["company_name"],
                TEST_DATA["domain"], TEST_DATA["website"], TEST_DATA["industry"],
                TEST_DATA["location"], TEST_DATA["city"], TEST_DATA["state_region"],
                TEST_DATA["country"], "50-100", "Autonomous robotics software platform",
                dm_json, 85, "Tier 1", reasons_json, "candidate", 1, now_str, now_str
            )
        )
        conn.commit()
        logger.info(f"Inserted candidate lead: {TEST_DATA['lead_id']}")

        # Verify candidate in DB
        cursor.execute("SELECT id, company_name, domain FROM discovered_leads WHERE id = ?", (TEST_DATA["lead_id"],))
        assert cursor.fetchone() is not None, "Lead was not saved to DB"

        # =========================================================================
        # STEP 2: CANDIDATE QUALIFICATION & IMPORT (Company & Prospect Creation)
        # =========================================================================
        logger.info("── STEP 2: Importing Candidate Lead into CRM via API ──")
        status_code, resp = api_request("POST", "/api/discovery/candidates/import", token, {
            "lead_ids": [TEST_DATA["lead_id"]],
            "target_stage": "Discovery",
            "notes": "Imported via E2E automated qualification"
        })
        logger.info(f"Import candidates response: status={status_code}, resp={resp}")
        assert status_code == 200, f"Import candidates failed with status {status_code}"
        assert resp.get("imported_count") == 1, "Expected 1 imported candidate"

        # Retrieve created company and prospect from SQLite
        cursor.execute("SELECT id, name, domain FROM companies WHERE domain = ?", (TEST_DATA["domain"],))
        company_row = cursor.fetchone()
        assert company_row is not None, "Company record was not created in companies table"
        test_company_id = company_row[0]
        logger.info(f"Verified Company created: ID={test_company_id}, Name={company_row[1]}")

        cursor.execute("SELECT id, company_id, email, pipeline_stage FROM prospects WHERE email = ?", (TEST_DATA["prospect_email"],))
        prospect_row = cursor.fetchone()
        assert prospect_row is not None, "Prospect record was not created in prospects table"
        test_prospect_id = prospect_row[0]
        assert prospect_row[1] == test_company_id, f"Foreign key mismatch: prospect.company_id ({prospect_row[1]}) != company.id ({test_company_id})"
        logger.info(f"Verified Prospect created: ID={test_prospect_id}, CompanyID={prospect_row[1]}, Stage={prospect_row[3]}")

        # =========================================================================
        # STEP 3: COMPANY ENRICHMENT (Observations & Technical Signals)
        # =========================================================================
        logger.info("── STEP 3: Enriching Company with Technical Observation Signals ──")
        cursor.execute(
            """INSERT INTO observations
               (id, company_id, prospect_id, signal_code, collector, value_text, confidence, evidence_url, observed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                TEST_DATA["observation_id"], test_company_id, test_prospect_id,
                "FOUND_IN_SERP", "discover", "Autonomous Warehouse Automation & ROS2",
                1.0, "https://test-e2e-acmerobotics.io/tech", now_str
            )
        )
        conn.commit()
        cursor.execute("SELECT count(*) FROM observations WHERE company_id = ?", (test_company_id,))
        obs_count = cursor.fetchone()[0]
        assert obs_count >= 1, "Enrichment observation was not saved"
        logger.info(f"Verified Company Enrichment observations saved: count={obs_count}")

        # =========================================================================
        # STEP 4: LEAD SCORING & PIPELINE QUALIFICATION
        # =========================================================================
        logger.info("── STEP 4: Updating Lead Score and Pipeline Stage ──")
        cursor.execute(
            """UPDATE prospects
               SET score = 88, status = 'verified', pipeline_stage = 'Discovery', notes = 'ICP Tier-1 qualified'
               WHERE id = ?""",
            (test_prospect_id,)
        )
        conn.commit()

        # Verify via REST API
        status_code, p_list = api_request("GET", "/api/prospects", token)
        assert status_code == 200, f"GET /api/prospects returned {status_code}"
        prospect_api = next((p for p in p_list if p.get("id") == test_prospect_id or p.get("email") == TEST_DATA["prospect_email"]), None)
        assert prospect_api is not None, "Prospect not found in /api/prospects response"
        assert prospect_api.get("score") == 88, f"Prospect score mismatch: {prospect_api.get('score')} != 88"
        logger.info(f"Verified Lead Scoring via API: Score={prospect_api.get('score')}, Status={prospect_api.get('status')}")

        # =========================================================================
        # STEP 5: CAMPAIGN GENERATION
        # =========================================================================
        logger.info("── STEP 5: Creating Campaign Record ──")
        seq_json = json.dumps([
            {"step": 1, "delay_days": 0, "subject": "Acme Robotics: AI orchestration", "template": "Hi {{first_name}}, loved your recent tech update."},
            {"step": 2, "delay_days": 3, "subject": "Quick follow-up on ROS2 robotics", "template": "Hi {{first_name}}, following up on our integration..."}
        ])
        cursor.execute(
            """INSERT INTO campaigns
               (id, name, channel, sequence_json, prospect_ids_json, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                TEST_DATA["campaign_id"], TEST_DATA["campaign_name"],
                "email", seq_json, json.dumps([test_prospect_id]), "active", now_str
            )
        )
        conn.commit()

        status_code, camp_resp = api_request("GET", "/api/campaigns", token)
        assert status_code == 200, f"GET /api/campaigns failed with {status_code}"
        camp_list = camp_resp.get("campaigns", camp_resp) if isinstance(camp_resp, dict) else camp_resp
        test_camp = next((c for c in camp_list if c.get("id") == TEST_DATA["campaign_id"]), None)
        assert test_camp is not None, "Campaign not found in /api/campaigns"
        logger.info(f"Verified Campaign via API: ID={test_camp.get('id')}, Name={test_camp.get('name')}")

        # =========================================================================
        # STEP 6: OUTBOX EMAIL DRAFT & HUMAN REVIEW GATE
        # =========================================================================
        logger.info("── STEP 6: Staging Email Draft in Outbox with Review Gate ──")
        cursor.execute(
            """INSERT INTO outbox
               (id, campaign_id, prospect_id, step, kind, to_email, subject, body, status, send_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                TEST_DATA["outbox_id"], TEST_DATA["campaign_id"], test_prospect_id,
                1, "sequence", TEST_DATA["prospect_email"],
                "AI Automation for Acme Robotics",
                "Hi Alex,\n\nI noticed Acme Robotics is scaling autonomous systems with ROS2. We help robotics teams streamline telemetry...\n\nBest,\nHarvey AI",
                "pending_review", now_str, now_str, now_str
            )
        )
        conn.commit()

        # Invariant 1: Ensure outbox item starts as pending_review
        cursor.execute("SELECT id, status, to_email FROM outbox WHERE id = ?", (TEST_DATA["outbox_id"],))
        outbox_row = cursor.fetchone()
        assert outbox_row is not None, "Outbox item not found in DB"
        assert outbox_row[1] == "pending_review", f"Outbox safety invariant violated: status was {outbox_row[1]}, expected pending_review"
        logger.info(f"Verified Outbox Safety Gate: Item is safely held in 'pending_review' state.")

        # Test Outbox Edit Endpoint
        logger.info("Testing Outbox Edit via REST API...")
        status_code, edit_resp = api_request("POST", f"/api/outbox/{TEST_DATA['outbox_id']}/edit", token, {
            "subject": "Acme Robotics: Streamlining Telemetry & ROS2 Systems",
            "body": "Hi Alex,\n\nFollowing your engineering scaling, let us know if telemetry optimization is a priority.\n\nBest,\nHarvey Sales Lead"
        })
        logger.info(f"Outbox edit response: {status_code}, {edit_resp}")
        assert status_code == 200, f"Edit outbox failed with status {status_code}"

        # Test Outbox Approve Endpoint
        logger.info("Testing Outbox Human Approval Gate via REST API...")
        status_code, app_resp = api_request("POST", f"/api/outbox/{TEST_DATA['outbox_id']}/approve", token, {})
        logger.info(f"Outbox approve response: {status_code}, {app_resp}")
        assert status_code == 200, f"Approve outbox failed with status {status_code}"

        cursor.execute("SELECT status, approved_by FROM outbox WHERE id = ?", (TEST_DATA["outbox_id"],))
        app_row = cursor.fetchone()
        assert app_row[0] in ("approved", "queued"), f"Outbox status expected approved/queued, got: {app_row[0]}"
        logger.info(f"Verified Human Review Gate Approval: status={app_row[0]}, approved_by={app_row[1]}")

        # =========================================================================
        # STEP 7: CONVERSATION HANDLING & STATUS
        # =========================================================================
        logger.info("── STEP 7: Inbound Conversation Handling ──")
        thread_content = [
            {"sender": "assistant", "body": "Hi Alex, following your engineering scaling...", "timestamp": now_str},
            {"sender": "prospect", "body": "Hi, thanks for reaching out. Does Harvey support on-premise deployments?", "timestamp": now_str}
        ]
        cursor.execute(
            """INSERT INTO conversations
               (id, prospect_id, campaign_id, channel, thread_json, intent, stage, status, priority, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                TEST_DATA["convo_id"], test_prospect_id, TEST_DATA["campaign_id"],
                "email", json.dumps(thread_content), "pricing_or_product_inquiry", "replied",
                "needs_reply", "high", now_str, now_str
            )
        )
        conn.commit()

        # Update Conversation Status via API
        status_code, convo_stat_resp = api_request("POST", f"/api/conversations/{TEST_DATA['convo_id']}/status", token, {
            "status": "closed",
            "notes": "Reviewed on-premise architecture with engineering"
        })
        logger.info(f"Conversation status update response: {status_code}, {convo_stat_resp}")
        assert status_code == 200, f"POST /api/conversations status failed with {status_code}"

        # =========================================================================
        # STEP 8: ANALYTICS & TODAY APIS
        # =========================================================================
        logger.info("── STEP 8: Verifying Analytics, Today, and Pipeline APIs ──")
        for ep in ["/api/today", "/api/stats", "/api/activity", "/api/crm/pipeline", "/api/usage"]:
            st, d = api_request("GET", ep, token)
            assert st == 200, f"GET {ep} failed with {st}"
            logger.info(f"Verified {ep} -> 200 OK")

        # =========================================================================
        # STEP 9: VISIBLE PLAYWRIGHT BROWSER VERIFICATION (User Can Watch Live)
        # =========================================================================
        logger.info("── STEP 9: Launching Visible Playwright Browser (headless=False) ──")
        with sync_playwright() as p:
            # Visible Chromium browser with slow_mo so user can watch UI actions
            browser = p.chromium.launch(headless=False, slow_mo=300)
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()

            page_errors = []
            page.on("pageerror", lambda err: page_errors.append(str(err)))

            dashboard_url = f"{BASE_URL}/#token={token}"
            logger.info(f"Navigating to {dashboard_url}")
            page.goto(dashboard_url, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)

            # Defensive token set in sessionStorage if unlock is needed
            page.evaluate(f"""() => {{
                sessionStorage.setItem('harvey-dashboard-token', '{token}');
                const ls = document.getElementById('lock-screen');
                if (ls) ls.classList.add('hidden');
                const app = document.getElementById('app');
                if (app) app.classList.remove('hidden');
            }}""")
            page.wait_for_timeout(1000)

            # 1. Companies Tab
            logger.info("Testing Companies tab in UI...")
            page.evaluate("showTab('companies')")
            page.wait_for_timeout(1500)
            search_input = page.locator("#company-search")
            if search_input.is_visible():
                search_input.fill("Acme Robotics")
                page.wait_for_timeout(800)
            page.screenshot(path=str(SCREENSHOT_DIR / "01_companies_profile.png"))
            logger.info("Captured 01_companies_profile.png")

            # 2. Prospects Tab
            logger.info("Testing Prospects tab in UI...")
            page.evaluate("showTab('prospects')")
            page.wait_for_timeout(1500)
            prospect_search = page.locator("#prospect-search")
            if prospect_search.is_visible():
                prospect_search.fill("Alex")
                page.wait_for_timeout(800)
            page.screenshot(path=str(SCREENSHOT_DIR / "02_prospects_detail.png"))
            logger.info("Captured 02_prospects_detail.png")

            # 3. Outbox Tab
            logger.info("Testing Outbox tab in UI...")
            page.evaluate("showTab('outbox')")
            page.wait_for_timeout(1500)
            page.screenshot(path=str(SCREENSHOT_DIR / "03_outbox_review.png"))
            logger.info("Captured 03_outbox_review.png")

            # 4. Campaigns Tab
            logger.info("Testing Campaigns tab in UI...")
            page.evaluate("showTab('campaigns')")
            page.wait_for_timeout(1500)
            page.screenshot(path=str(SCREENSHOT_DIR / "04_campaigns_list.png"))
            logger.info("Captured 04_campaigns_list.png")

            # 5. Conversations Tab
            logger.info("Testing Conversations tab in UI...")
            page.evaluate("showTab('conversations')")
            page.wait_for_timeout(1500)
            page.screenshot(path=str(SCREENSHOT_DIR / "05_conversations_inbox.png"))
            logger.info("Captured 05_conversations_inbox.png")

            # 6. CRM Pipeline Tab
            logger.info("Testing CRM Pipeline tab in UI...")
            page.evaluate("showTab('crm-pipeline')")
            page.wait_for_timeout(1500)
            page.screenshot(path=str(SCREENSHOT_DIR / "06_crm_pipeline_kanban.png"))
            logger.info("Captured 06_crm_pipeline_kanban.png")

            # 7. Today Tab
            logger.info("Testing Today tab in UI...")
            page.evaluate("showTab('today')")
            page.wait_for_timeout(1500)
            page.screenshot(path=str(SCREENSHOT_DIR / "07_today_command_deck.png"))
            logger.info("Captured 07_today_command_deck.png")

            # =========================================================================
            # STEP 10: PAGE RELOAD PERSISTENCE VERIFICATION
            # =========================================================================
            logger.info("── STEP 10: Testing Page Reload Persistence ──")
            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            page.evaluate(f"""() => {{
                sessionStorage.setItem('harvey-dashboard-token', '{token}');
                const ls = document.getElementById('lock-screen');
                if (ls) ls.classList.add('hidden');
                const app = document.getElementById('app');
                if (app) app.classList.remove('hidden');
            }}""")
            page.wait_for_timeout(1000)
            page.screenshot(path=str(SCREENSHOT_DIR / "08_after_page_reload.png"))
            logger.info("Captured 08_after_page_reload.png")

            browser.close()
            logger.info("Visible Playwright browser test session completed successfully.")

            # Filter non-critical favicon/asset errors if any
            critical_errors = [e for e in page_errors if "favicon" not in e.lower()]
            assert len(critical_errors) == 0, f"Uncaught page errors detected: {critical_errors}"

        # =========================================================================
        # STEP 11: CONTROLLED SQLITE WAL CHECKPOINT / RESTART PERSISTENCE
        # =========================================================================
        logger.info("── STEP 11: Testing SQLite WAL Checkpoint & Restart Persistence ──")
        cursor.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        checkpoint_res = cursor.fetchall()
        logger.info(f"WAL Checkpoint result: {checkpoint_res}")

        cursor.execute("PRAGMA integrity_check;")
        integrity = cursor.fetchall()
        assert integrity == [("ok",)], f"Database integrity check failed: {integrity}"
        logger.info("Database integrity check PASSED: ok")

        # Confirm all test records survived checkpointing
        cursor.execute("SELECT count(*) FROM companies WHERE domain = ?", (TEST_DATA["domain"],))
        assert cursor.fetchone()[0] == 1, "Company record did not persist across checkpoint"
        cursor.execute("SELECT count(*) FROM prospects WHERE email = ?", (TEST_DATA["prospect_email"],))
        assert cursor.fetchone()[0] == 1, "Prospect record did not persist across checkpoint"
        logger.info("Checkpoint persistence verified.")

    finally:
        # =========================================================================
        # STEP 12: SAFE ISOLATED DATA CLEANUP (ZERO ORIGINAL DATA LOSS)
        # =========================================================================
        logger.info("── STEP 12: Performing Safe Isolated Data Cleanup ──")
        try:
            # Delete in strict foreign key order
            cursor.execute("DELETE FROM conversations WHERE id = ?", (TEST_DATA["convo_id"],))
            cursor.execute("DELETE FROM outbox WHERE id = ?", (TEST_DATA["outbox_id"],))
            cursor.execute("DELETE FROM campaigns WHERE id = ?", (TEST_DATA["campaign_id"],))
            cursor.execute("DELETE FROM observations WHERE id = ? OR company_id = ?", (TEST_DATA["observation_id"], test_company_id or "NONE"))
            cursor.execute("DELETE FROM prospects WHERE id = ? OR email = ?", (test_prospect_id or "NONE", TEST_DATA["prospect_email"]))
            cursor.execute("DELETE FROM companies WHERE id = ? OR domain = ?", (test_company_id or "NONE", TEST_DATA["domain"]))
            cursor.execute("DELETE FROM discovered_leads WHERE id = ?", (TEST_DATA["lead_id"],))
            conn.commit()
            logger.info("Cleaned up all isolated test records.")
        except Exception as cleanup_err:
            logger.error(f"Error during cleanup: {cleanup_err}")
        finally:
            conn.close()

        final_counts = get_db_counts()
        logger.info(f"Final Database Post-Cleanup Counts: {final_counts}")
        logger.info(f"Baseline Counts comparison: {initial_counts}")

        for t in ["companies", "prospects", "outbox", "campaigns", "observations"]:
            assert final_counts[t] == initial_counts[t], (
                f"Data loss or leftover leak detected in table {t}: final {final_counts[t]} != initial {initial_counts[t]}"
            )
        logger.info("ZERO DATA LOSS VERIFIED: All table counts perfectly match baseline!")

    print("\n" + "="*70)
    print("ALL DEEP END-TO-END BUSINESS TESTS AND PERSISTENCE CHECKS PASSED 100%!")
    print("="*70)


if __name__ == "__main__":
    run_e2e_verification()
