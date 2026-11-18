"""Full-coverage Playwright UI test and screenshot capture across all 15 Harvey Command Deck tabs.
"""

import os
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

SCREENSHOTS_DIR = Path("reports/screenshots")
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

URL = "http://127.0.0.1:5555/#token=REPLACE_WITH_LOCAL_DASHBOARD_TOKEN"

TABS = [
    ("today", "01_today_overview.png", "Today / Overview"),
    ("prospects", "02_prospects_work.png", "Work / Prospects"),
    ("outbox", "03_outbox.png", "Outbox / Review & Approval"),
    ("conversations", "04_conversations.png", "Conversations"),
    ("crm-pipeline", "05_crm_pipeline.png", "CRM / Pipeline"),
    ("signals", "06_signals.png", "Signals Catalog"),
    ("discover", "07_discover.png", "Lead Discovery & ICP"),
    ("companies", "08_companies.png", "Companies & Profiles"),
    ("campaigns", "09_campaigns.png", "Campaigns & Drafting"),
    ("knowledge", "10_knowledge_rag.png", "Knowledge Base / RAG"),
    ("activity", "11_activity.png", "System Activity Feed"),
    ("usage", "12_usage.png", "Usage & Resource Analytics"),
    ("controls", "13_controls.png", "Controls & Safety Holds"),
    ("settings", "14_settings.png", "Settings & Integrations"),
    ("help", "15_help.png", "Help & Documentation"),
]

def run_ui_audit():
    console_errors = []
    page_errors = []
    network_errors = []
    results = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        page.on("console", lambda msg: console_errors.append(f"[{msg.type}] {msg.text}") if msg.type in ("error", "warning") and "favicon" not in msg.text else None)
        page.on("pageerror", lambda err: page_errors.append(str(err)))
        page.on("requestfailed", lambda req: network_errors.append(f"{req.method} {req.url} - {req.failure}"))

        print(f"Navigating to {URL}...")
        page.goto(URL, wait_until="networkidle", timeout=15000)
        time.sleep(2)

        # Confirm token unlock
        title = page.title()
        print(f"Loaded page: {title}")

        for tab_id, filename, label in TABS:
            print(f"\n--- Testing Tab: {label} (id: {tab_id}) ---")
            start = time.monotonic()
            try:
                # Click the tab button or invoke showTab
                page.evaluate(f"showTab('{tab_id}')")
                time.sleep(1.5)

                # Special interactions per tab
                if tab_id == "discover":
                    # Check provider menu
                    page.evaluate("if (typeof loadDiscoverProviders === 'function') loadDiscoverProviders();")
                    time.sleep(1)
                elif tab_id == "crm-pipeline":
                    # Refresh pipeline
                    page.evaluate("if (typeof renderCrmPipeline === 'function') renderCrmPipeline();")
                    time.sleep(1)
                elif tab_id == "companies":
                    page.evaluate("if (typeof loadCompanies === 'function') loadCompanies();")
                    time.sleep(1)
                elif tab_id == "settings":
                    page.evaluate("if (typeof loadIntegrationsMatrix === 'function') loadIntegrationsMatrix();")
                    time.sleep(1)

                screenshot_path = SCREENSHOTS_DIR / filename
                page.screenshot(path=str(screenshot_path), full_page=False)
                dur = time.monotonic() - start
                print(f"  Captured screenshot: {screenshot_path} ({dur:.2f}s)")
                results[tab_id] = {
                    "label": label,
                    "status": "PASS",
                    "screenshot": str(screenshot_path),
                    "duration_s": round(dur, 2),
                }
            except Exception as e:
                print(f"  Error on tab {tab_id}: {e}")
                results[tab_id] = {
                    "label": label,
                    "status": "FAIL",
                    "error": str(e),
                }

        # Test specific interactive elements:
        print("\n--- Testing Specific UI Interactions ---")

        # 1. Discover Provider selection & Estimate
        try:
            page.evaluate("showTab('discover')")
            time.sleep(1)
            # Click Estimate Cost button
            est_btn = page.query_selector("button:has-text('Estimate cost')")
            if est_btn:
                print("  Found 'Estimate cost' button, clicking...")
                est_btn.click()
                time.sleep(2)
                est_content = page.inner_text("#discover-estimate")
                print(f"  Estimate result: {est_content[:100]}...")
        except Exception as e:
            print(f"  Estimate interaction failed: {e}")

        # 2. Open a Company Drawer
        try:
            page.evaluate("showTab('companies')")
            time.sleep(1)
            co_row = page.query_selector(".company-row, tr.clickable, .card h3")
            if co_row:
                print("  Clicking first company row...")
                co_row.click()
                time.sleep(1.5)
                page.screenshot(path=str(SCREENSHOTS_DIR / "08b_company_profile_drawer.png"))
                print("  Saved 08b_company_profile_drawer.png")
        except Exception as e:
            print(f"  Company drawer interaction failed: {e}")

        # 3. Knowledge / RAG Search
        try:
            page.evaluate("showTab('knowledge')")
            time.sleep(1)
            search_input = page.query_selector("input#rag-search, input[placeholder*='Ask'], input[placeholder*='query']")
            if search_input:
                print("  Testing RAG knowledge query...")
                search_input.fill("What are our target industries?")
                page.keyboard.press("Enter")
                time.sleep(2)
                page.screenshot(path=str(SCREENSHOTS_DIR / "10b_rag_query_result.png"))
                print("  Saved 10b_rag_query_result.png")
        except Exception as e:
            print(f"  RAG search interaction failed: {e}")

        browser.close()

    print("\n" + "=" * 60)
    print("UI AUDIT SUMMARY:")
    print(f"  Tabs Tested: {len(results)}")
    print(f"  Passed: {sum(1 for r in results.values() if r.get('status') == 'PASS')}")
    print(f"  Failed: {sum(1 for r in results.values() if r.get('status') == 'FAIL')}")
    print(f"  Console Warnings/Errors: {len(console_errors)}")
    for ce in console_errors[:5]:
        print(f"    {ce}")
    print(f"  Page Uncaught Errors: {len(page_errors)}")
    for pe in page_errors:
        print(f"    {pe}")
    print(f"  Network Failed Requests: {len(network_errors)}")
    for ne in network_errors[:5]:
        print(f"    {ne}")
    print("=" * 60)


if __name__ == "__main__":
    run_ui_audit()
