"""Automated Playwright browser runner for testing Harvey dashboard modules one-by-one.

Captures console logs, page errors, network status, DOM validation, and screenshots.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

SCREENSHOT_DIR = Path("data/reports/e2e_testing")
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

# Tell pytest this is a CLI runner script, not a pytest test file
__test__ = False


def get_dashboard_url(port: int = 5555) -> str:
    token_file = Path("data/dashboard-token")
    if not token_file.exists():
        raise FileNotFoundError("data/dashboard-token not found")
    tok = token_file.read_text(encoding="utf-8").strip()
    return f"http://127.0.0.1:{port}/#token={tok}"


def test_module(module_name: str, port: int = 5555) -> dict:
    url = get_dashboard_url(port)
    results = {
        "module": module_name,
        "status": "FAIL",
        "tests_executed": [],
        "errors": [],
        "console_logs": [],
        "screenshot": "",
        "details": {},
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()

        # Capture console and page errors
        page.on("console", lambda msg: results["console_logs"].append(f"[{msg.type}] {msg.text}"))
        page.on("pageerror", lambda err: results["errors"].append(str(err)))

        # Navigate to dashboard
        page.goto(url, wait_until="networkidle", timeout=15000)
        time.sleep(1.0)

        # Ensure lock screen is not active
        lock_screen = page.locator("#lock-screen")
        if lock_screen.is_visible():
            results["errors"].append("Dashboard is locked - authorization failed")
            results["status"] = "FAIL"
            browser.close()
            return results

        results["tests_executed"].append("auth_and_unlock")

        # Module-specific testing logic
        tab_id_map = {
            "today": "today",
            "discover": "discover",
            "outbox": "outbox",
            "conversations": "conversations",
            "signals": "signals",
            "crm-pipeline": "crm-pipeline",
            "companies": "companies",
            "contacts": "prospects",
            "campaigns": "campaigns",
            "knowledge": "knowledge",
            "activity": "activity",
            "usage": "usage",
            "settings": "settings",
            "research": "research",
        }

        tab_id = tab_id_map.get(module_name.lower())
        if not tab_id:
            results["errors"].append(f"Unknown module '{module_name}'")
            browser.close()
            return results

        # Switch to tab
        page.evaluate(f"showTab('{tab_id}')")
        time.sleep(1.5)
        results["tests_executed"].append("tab_switch")

        # Verify section is active
        section = page.locator(f"#{tab_id}")
        is_active = section.evaluate("el => el.classList.contains('active')")
        if not is_active:
            results["errors"].append(f"Section #{tab_id} does not have 'active' class")
        else:
            results["tests_executed"].append("section_active_verified")

        # Specific assertions per module
        if module_name.lower() == "today":
            kpis = page.locator("#today-kpis")
            queue = page.locator("#today-queue")
            results["details"]["kpis_visible"] = kpis.is_visible()
            results["details"]["queue_visible"] = queue.is_visible()
            results["tests_executed"].extend(["kpis_checked", "queue_checked"])

        elif module_name.lower() == "discover":
            profile_sel = page.locator("#active-business-profile")
            btn_sample = page.locator("#btn-sample-discover")
            results["details"]["profile_selector_visible"] = profile_sel.is_visible()
            results["details"]["sample_btn_visible"] = btn_sample.is_visible()
            results["tests_executed"].extend(["profile_selector_checked", "sample_btn_checked"])

        elif module_name.lower() == "outbox":
            outbox_section = page.locator("#outbox")
            results["details"]["outbox_visible"] = outbox_section.is_visible()
            results["tests_executed"].append("outbox_container_checked")

        elif module_name.lower() == "conversations":
            conv_section = page.locator("#conversations")
            results["details"]["conversations_visible"] = conv_section.is_visible()
            results["tests_executed"].append("conversations_container_checked")

        elif module_name.lower() == "signals":
            signals_section = page.locator("#signals")
            results["details"]["signals_visible"] = signals_section.is_visible()
            results["tests_executed"].append("signals_container_checked")

        elif module_name.lower() == "crm-pipeline":
            crm_section = page.locator("#crm-pipeline")
            results["details"]["crm_visible"] = crm_section.is_visible()
            results["tests_executed"].append("crm_container_checked")

        elif module_name.lower() == "companies":
            companies_table = page.locator("#companies")
            results["details"]["companies_visible"] = companies_table.is_visible()
            results["tests_executed"].append("companies_container_checked")

        elif module_name.lower() == "contacts":
            contacts_section = page.locator("#prospects")
            results["details"]["contacts_visible"] = contacts_section.is_visible()
            results["tests_executed"].append("contacts_container_checked")

        elif module_name.lower() == "campaigns":
            camp_section = page.locator("#campaigns")
            results["details"]["campaigns_visible"] = camp_section.is_visible()
            results["tests_executed"].append("campaigns_container_checked")

        elif module_name.lower() == "knowledge":
            k_docs = page.locator("#rag-kpi-docs")
            results["details"]["knowledge_kpis_visible"] = k_docs.is_visible()
            results["tests_executed"].append("knowledge_kpis_checked")

        elif module_name.lower() == "activity":
            act_section = page.locator("#activity")
            results["details"]["activity_visible"] = act_section.is_visible()
            results["tests_executed"].append("activity_container_checked")

        elif module_name.lower() == "usage":
            usage_section = page.locator("#usage")
            results["details"]["usage_visible"] = usage_section.is_visible()
            results["tests_executed"].append("usage_container_checked")

        elif module_name.lower() == "settings":
            set_section = page.locator("#settings")
            results["details"]["settings_visible"] = set_section.is_visible()
            results["tests_executed"].append("settings_container_checked")

        elif module_name.lower() == "research":
            res_kpis = page.locator("#research-kpis")
            res_table = page.locator("#research-results-table")
            file_input = page.locator("#research-file-input")
            results["details"]["research_kpis_visible"] = res_kpis.is_visible()
            results["details"]["table_visible"] = res_table.is_visible()
            results["details"]["upload_input_exists"] = file_input.count() > 0
            results["tests_executed"].extend(["research_kpis_checked", "table_checked", "file_input_checked"])

        # Take screenshot
        screenshot_path = SCREENSHOT_DIR / f"{module_name.lower()}_verified.png"
        page.screenshot(path=str(screenshot_path))
        results["screenshot"] = str(screenshot_path)

        # Check for fatal errors
        fatal_errors = [e for e in results["errors"] if "favicon" not in e.lower()]
        if not fatal_errors:
            results["status"] = "PASS"
        else:
            results["status"] = "FAIL"

        browser.close()

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--module", required=True)
    parser.add_argument("--port", type=int, default=5555)
    args = parser.parse_args()

    res = test_module(args.module, args.port)
    print(json.dumps(res, indent=2))
