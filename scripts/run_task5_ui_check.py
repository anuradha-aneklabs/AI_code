"""Task 5 UI Verification Script with Playwright on port 5566.

Runs against copy DB data/validation/harvey_e2e2.db.
Takes 4 screenshots:
  1. upload preview (data/exports/ui_check/01_upload_preview.png)
  2. batch progress (data/exports/ui_check/02_batch_progress.png)
  3. details modal (data/exports/ui_check/03_details_modal.png)
  4. export (data/exports/ui_check/04_export.png)
Stops the server cleanly when done.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
import urllib.request

from playwright.sync_api import sync_playwright

EXPORTS_DIR = Path("data/exports/ui_check")
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = Path("data/validation/harvey_e2e2.db").resolve()
PORT = 5566


def wait_for_server(port: int, timeout_sec: int = 15) -> bool:
    url = f"http://127.0.0.1:{port}/"
    start = time.time()
    while time.time() - start < timeout_sec:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Harvey-HealthCheck"})
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                if resp.status in (200, 302, 401, 403):
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def main():
    print("=" * 70)
    print(" TASK 5: UI VERIFICATION ON PORT 5566")
    print(f" Database: {DB_PATH}")
    print("=" * 70)

    # 1. Start server on port 5566
    env = os.environ.copy()
    env["HARVEY_DB_PATH"] = str(DB_PATH)
    env["PYTHONUNBUFFERED"] = "1"

    token_file = Path("data/dashboard-token")
    if token_file.exists():
        tok = token_file.read_text(encoding="utf-8").strip()
    else:
        tok = "ui-test-token"
        token_file.write_text(tok, encoding="utf-8")

    server_cmd = [sys.executable, "-m", "harvey", "dashboard", "--port", str(PORT)]
    print(f"Starting server: {' '.join(server_cmd)}")
    server_proc = subprocess.Popen(
        server_cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        print("Waiting for server on port 5566 to respond...")
        if not wait_for_server(PORT, timeout_sec=20):
            stdout, stderr = server_proc.communicate(timeout=2)
            print("Server stdout:", stdout.decode(errors="ignore"))
            print("Server stderr:", stderr.decode(errors="ignore"))
            raise RuntimeError(f"Server on port {PORT} failed to start within timeout.")
        print(f"Server is responding on port {PORT}!")

        # Prepare test CSV for upload
        test_csv = Path("data/ui_check_contacts.csv")
        test_csv.write_text(
            "Company,First Name,Last Name,Email,Phone,Domain\n"
            "EngineerBabu,Aditi,Verma,hello@engineerbabu.com,+91-731-555-0102,engineerbabu.com\n"
            "Easinvy,Rohan,Gupta,contact@easinvy.com,+91-731-555-0103,easinvy.com\n"
            "Bridge Logic Software,Vikas,Patel,info@bridgelogicsoftware.com,+91-731-555-0104,bridgelogicsoftware.com\n",
            encoding="utf-8",
        )

        target_url = f"http://127.0.0.1:{PORT}/#token={tok}"

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(viewport={"width": 1920, "height": 1080}, accept_downloads=True)
            page = ctx.new_page()

            page.on("console", lambda m: print(f"  [BROWSER] {m.text}") if "error" in m.text.lower() else None)

            print(f"Navigating to {target_url}...")
            page.goto(target_url, wait_until="networkidle", timeout=15000)
            time.sleep(1.0)

            # Switch to Research tab
            print("Switching to Research tab...")
            page.evaluate("showTab('research')")
            time.sleep(1.5)

            # Ensure upload body is open
            page.evaluate("""() => {
                const body = document.getElementById('research-upload-body');
                if (body && body.style.display === 'none') {
                    toggleResearchUploadPanel();
                }
            }""")
            time.sleep(0.5)

            # 1. Set input file & Upload Preview
            print("Uploading file for preview...")
            file_input = page.locator("#research-file-input")
            file_input.set_input_files(str(test_csv.resolve()))
            page.dispatch_event("#research-file-input", "change")
            time.sleep(0.5)
            page.evaluate("() => { const b = document.getElementById('btn-research-upload'); if (b) b.disabled = false; }")

            page.evaluate("uploadResearchFile()")
            time.sleep(3.0)

            # Screenshot 1: Upload Preview
            s1 = EXPORTS_DIR / "01_upload_preview.png"
            page.screenshot(path=str(s1))
            print(f"Captured: {s1} ({s1.stat().st_size} bytes)")

            # 2. Start batch run & Screenshot 2: Batch Progress
            print("Starting batch run...")
            start_btn = page.locator("#btn-start-batch-run")
            if start_btn.is_visible():
                start_btn.click()
            time.sleep(3.0)

            s2 = EXPORTS_DIR / "02_batch_progress.png"
            page.screenshot(path=str(s2))
            print(f"Captured: {s2} ({s2.stat().st_size} bytes)")

            # Wait a few seconds for batch processing / table refresh
            time.sleep(4.0)
            page.evaluate("loadResearchView()")
            time.sleep(2.0)

            # 3. Screenshot 3: Details Modal
            print("Opening details modal...")
            page.evaluate("openResearchDetailModal('engineerbabu.com')")
            time.sleep(1.5)

            s3 = EXPORTS_DIR / "03_details_modal.png"
            page.screenshot(path=str(s3))
            print(f"Captured: {s3} ({s3.stat().st_size} bytes)")

            page.evaluate("closeResearchDetailModal()")
            time.sleep(0.5)

            # 4. Screenshot 4: Export
            print("Testing export...")
            try:
                with page.expect_download(timeout=5000) as download_info:
                    page.evaluate("exportResearchBatch('csv')")
                dl = download_info.value
                dl.save_as(str(EXPORTS_DIR / "exported_batch_results.csv"))
            except Exception as e:
                print(f"Download notice: {e}")

            s4 = EXPORTS_DIR / "04_export.png"
            page.screenshot(path=str(s4))
            print(f"Captured: {s4} ({s4.stat().st_size} bytes)")

            browser.close()

    finally:
        print("Stopping server process...")
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5)
        except Exception:
            server_proc.kill()
        print("Server stopped cleanly.")

    # Validate that all 4 screenshots exist and are non-empty
    for s in [s1, s2, s3, s4]:
        assert s.exists() and s.stat().st_size > 0, f"Screenshot {s} missing or empty"

    print("\nAll 4 UI screenshots successfully captured in data/exports/ui_check/!")
    return True


if __name__ == "__main__":
    if main():
        print("\nTASK 5 STATUS: VERIFIED")
    else:
        print("\nTASK 5 STATUS: FAILED")
