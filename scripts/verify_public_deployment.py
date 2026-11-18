"""Public deployment verification: exercises Harvey live on the public Cloudflare HTTPS URL.
"""

import os
import sys
import time
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright

from harvey.dashboard import token_store

PUBLIC_BASE = "https://produce-handled-fees-concord.trycloudflare.com"
TOKEN = token_store.get()
PUBLIC_UNLOCK_URL = f"{PUBLIC_BASE}/#token={TOKEN}"
SCREENSHOTS_DIR = Path("reports/screenshots")
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

def verify_public_apis():
    print(f"Testing public authenticated REST APIs on {PUBLIC_BASE}...")
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
    }
    endpoints = [
        ("GET", "/api/health"),
        ("GET", "/api/summary"),
        ("GET", "/api/companies"),
        ("GET", "/api/outbox"),
        ("GET", "/api/crm/pipeline"),
        ("GET", "/api/discover/providers"),
        ("POST", "/api/discover/estimate", {"provider": "osm"}),
        ("GET", "/api/integrations/status"),
    ]
    results = {}
    with httpx.Client(base_url=PUBLIC_BASE, headers=headers, timeout=15.0) as client:
        for item in endpoints:
            method = item[0]
            path = item[1]
            payload = item[2] if len(item) > 2 else None
            start = time.monotonic()
            try:
                if method == "GET":
                    r = client.get(path)
                else:
                    r = client.post(path, json=payload or {})
                ms = int((time.monotonic() - start) * 1000)
                results[f"{method} {path}"] = {
                    "status_code": r.status_code,
                    "latency_ms": ms,
                    "ok": r.status_code in (200, 201),
                }
                print(f"  {method} {path}: {r.status_code} ({ms}ms)")
            except Exception as e:
                results[f"{method} {path}"] = {"error": str(e), "ok": False}
                print(f"  {method} {path}: ERROR ({e})")
    return results


def verify_public_browser():
    print(f"\nTesting public browser login and UI rendering on {PUBLIC_UNLOCK_URL}...")
    ui_results = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        console_logs = []
        page.on("console", lambda m: console_logs.append(f"[{m.type}] {m.text}") if m.type in ("error", "warning") and "favicon" not in m.text else None)

        start = time.monotonic()
        page.goto(PUBLIC_UNLOCK_URL, wait_until="networkidle", timeout=25000)
        time.sleep(2)
        load_time = time.monotonic() - start

        title = page.title()
        print(f"  Page loaded: {title} ({load_time:.2f}s)")
        ui_results["title"] = title
        ui_results["unlocked"] = "Harvey" in title

        # Take screenshot of Today
        sc1 = SCREENSHOTS_DIR / "public_01_today.png"
        page.screenshot(path=str(sc1))
        print(f"  Saved screenshot: {sc1}")

        # Navigate to Discover and estimate cost
        page.evaluate("showTab('discover')")
        time.sleep(1.5)
        est_btn = page.query_selector("button:has-text('Estimate cost')")
        if est_btn:
            est_btn.click()
            time.sleep(1.5)
        sc2 = SCREENSHOTS_DIR / "public_02_discover.png"
        page.screenshot(path=str(sc2))
        print(f"  Saved screenshot: {sc2}")

        # Navigate to CRM Pipeline
        page.evaluate("showTab('crm-pipeline')")
        time.sleep(1.5)
        sc3 = SCREENSHOTS_DIR / "public_03_pipeline.png"
        page.screenshot(path=str(sc3))
        print(f"  Saved screenshot: {sc3}")

        # Navigate to Outbox
        page.evaluate("showTab('outbox')")
        time.sleep(1.5)
        sc4 = SCREENSHOTS_DIR / "public_04_outbox.png"
        page.screenshot(path=str(sc4))
        print(f"  Saved screenshot: {sc4}")

        browser.close()
        ui_results["console_issues"] = console_logs
    return ui_results


if __name__ == "__main__":
    api_res = verify_public_apis()
    ui_res = verify_public_browser()

    print("\n" + "=" * 60)
    print("PUBLIC DEPLOYMENT VERIFICATION RESULTS:")
    print(f"  Public Base URL: {PUBLIC_BASE}")
    print(f"  Client Unlock URL: {PUBLIC_UNLOCK_URL}")
    print(f"  APIs Tested: {len(api_res)}, Passed: {sum(1 for v in api_res.values() if v.get('ok'))}")
    print(f"  Browser Title: {ui_res.get('title')}")
    print(f"  Browser Unlocked: {ui_res.get('unlocked')}")
    print(f"  Console Errors: {len(ui_res.get('console_issues', []))}")
    print("=" * 60)
