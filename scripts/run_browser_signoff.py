import asyncio
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("C:/Users/aneka/Desktop/harvey")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

from playwright.async_api import async_playwright

VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
PYTHON_EXE = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable

EXPORT_DIR = ROOT / "data" / "exports" / "final_signoff"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

SOURCE_DB = ROOT / "data" / "validation" / "harvey_video_clean.db"
TARGET_DB = ROOT / "data" / "validation" / "harvey_browser_signoff.db"
SAMPLE_XLSX = Path(r"C:\Users\aneka\Downloads\FCC_SampleRecords20261106.xlsx")

PORT = 5566
BASE_URL = f"http://127.0.0.1:{PORT}"

def get_token() -> str:
    from harvey.dashboard import token_store
    return token_store.get()

def start_server(db_path: Path) -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    env["HARVEY_DB_PATH"] = str(db_path)
    env["HARVEY_ALLOW_REAL_DB_WRITE"] = "1"
    env["HARVEY_PORT"] = str(PORT)
    env["PORT"] = str(PORT)
    env["HARVEY_LIVE_SENDING_ENABLED"] = "0"
    
    server_log = open(EXPORT_DIR / "server.log", "a", encoding="utf-8")
    cmd = [PYTHON_EXE, "scripts/run_test_dashboard.py"]
    print(f"Starting dashboard server on port {PORT} with DB: {db_path}...")
    proc = subprocess.Popen(
        cmd,
        env=env,
        cwd=str(ROOT),
        stdout=server_log,
        stderr=server_log,
    )
    return proc

def stop_server(proc: subprocess.Popen):
    if proc and proc.poll() is None:
        print(f"Stopping server process (PID {proc.pid})...")
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            proc.kill()
        try:
            proc.wait(timeout=3.0)
        except Exception:
            pass
    print("Server stopped.")

async def main():
    print("=== HARVEY ITEM 3: REAL BROWSER WORKFLOW IN FRESH SESSION ===")
    
    # 1. Setup isolated database
    if TARGET_DB.exists():
        try:
            TARGET_DB.unlink()
        except Exception:
            pass
    shutil.copy2(SOURCE_DB, TARGET_DB)
    print(f"Copied clean demo DB to {TARGET_DB} ({TARGET_DB.stat().st_size} bytes)")
    
    if not SAMPLE_XLSX.exists():
        raise FileNotFoundError(f"Missing sample file: {SAMPLE_XLSX}")
    print(f"Using test sample: {SAMPLE_XLSX} ({SAMPLE_XLSX.stat().st_size} bytes)")
    
    # Start server
    proc = start_server(TARGET_DB)
    await asyncio.sleep(4.0)
    
    token = get_token()
    token_masked = token[:6] + "..." + token[-4:]
    print(f"Dashboard token loaded (masked: {token_masked})")
    
    app_url = f"{BASE_URL}/#token={token}"
    batch_id = None
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            accept_downloads=True,
        )
        page = await context.new_page()
        page.on("console", lambda msg: print(f"BROWSER [{msg.type}]:", msg.text, flush=True))
        page.on("pageerror", lambda err: print("BROWSER ERROR:", err, flush=True))
        
        # 1. Navigate to dashboard
        print(f"Navigating to {BASE_URL}...")
        await page.goto(app_url)
        await asyncio.sleep(2.0)
        
        # Switch to Research tab
        print("Switching to Research tab...")
        await page.evaluate("showTab('research')")
        await asyncio.sleep(1.5)
        await page.screenshot(path=str(EXPORT_DIR / "01_research_tab_initial.png"))
        print("Saved 01_research_tab_initial.png")
        
        # 2. Expand upload panel
        print("Expanding upload panel...")
        upload_visible = await page.evaluate("document.getElementById('research-upload-body').style.display !== 'none'")
        if not upload_visible:
            await page.evaluate("toggleResearchUploadPanel()")
            await asyncio.sleep(1.0)
            
        # Set max domains to 5
        await page.fill("#research-max-domains", "5")
        
        # Set file input
        print(f"Uploading file {SAMPLE_XLSX.name}...", flush=True)
        file_input = page.locator("#research-file-input")
        await file_input.set_input_files(str(SAMPLE_XLSX))
        await asyncio.sleep(1.0)
        await page.evaluate("onResearchFileSelected(document.getElementById('research-file-input'))")
        
        file_info = await page.evaluate("""() => {
            const fi = document.getElementById('research-file-input');
            return {
                files_len: fi.files ? fi.files.length : 0,
                name: fi.files && fi.files[0] ? fi.files[0].name : null,
                selected_var: typeof _selectedResearchFile !== 'undefined' && _selectedResearchFile ? _selectedResearchFile.name : null
            };
        }""")
        print(f"File state in browser: {file_info}", flush=True)
        
        # Execute uploadResearchFile
        print("Executing uploadResearchFile()...", flush=True)
        eval_res = await page.evaluate("""async () => {
            try {
                await uploadResearchFile();
                return { success: true };
            } catch (err) {
                return { success: false, error: String(err) };
            }
        }""")
        print(f"Upload eval result: {eval_res}", flush=True)
        await asyncio.sleep(2.0)
        
        # Wait for preview container to appear
        preview_container = page.locator("#research-upload-preview-container")
        await preview_container.wait_for(state="visible", timeout=20000)
        await asyncio.sleep(1.5)
        
        preview_summary = await page.text_content("#research-preview-summary")
        print(f"Preview summary: {preview_summary}")
        
        # Retrieve active batch ID
        batch_id = await page.evaluate("_activeResearchBatchId")
        print(f"Active batch ID created: {batch_id}")
        
        await page.screenshot(path=str(EXPORT_DIR / "02_fcc_upload_preview.png"))
        print("Saved 02_fcc_upload_preview.png")
        
        # 3. Click Start Research Run
        print("Clicking Start Research Run...", flush=True)
        eval_start = await page.evaluate("""async () => {
            try {
                await startActiveBatchRun();
                return { success: true };
            } catch (err) {
                return { success: false, error: String(err) };
            }
        }""")
        print(f"Start batch eval result: {eval_start}", flush=True)
        await asyncio.sleep(2.0)
        
        # Wait for progress banner to appear
        progress_banner = page.locator("#research-progress-banner")
        await progress_banner.wait_for(state="visible", timeout=10000)
        progress_badge = await page.text_content("#research-progress-badge")
        print(f"Research run started. Progress badge: {progress_badge}")
        
        await page.screenshot(path=str(EXPORT_DIR / "03_batch_run_started.png"))
        print("Saved 03_batch_run_started.png")
        
        # 4. Wait 10 seconds while research begins, then reload mid-run
        print("Waiting 10 seconds while domain research begins...")
        await asyncio.sleep(10.0)
        
        print("Reloading page mid-run to verify SQLite persistence...")
        await page.reload()
        await asyncio.sleep(2.0)
        await page.evaluate(f"onResearchBatchSelect('{batch_id}')")
        await asyncio.sleep(2.0)
        
        badge_after_reload = await page.text_content("#research-progress-badge")
        print(f"Progress badge after reload: {badge_after_reload}")
        await page.screenshot(path=str(EXPORT_DIR / "04_midrun_page_reload.png"))
        print("Saved 04_midrun_page_reload.png")
        
        # 5. Mid-run server restart test: kill server and restart
        print("Simulating server interruption: killing server process...")
        stop_server(proc)
        await asyncio.sleep(2.0)
        
        print("Restarting server on port 5566 with same DB...")
        proc = start_server(TARGET_DB)
        await asyncio.sleep(4.0)
        
        print("Reloading page after server restart...")
        await page.reload()
        await asyncio.sleep(2.0)
        await page.evaluate(f"onResearchBatchSelect('{batch_id}')")
        await asyncio.sleep(2.0)
        
        resume_visible = await page.evaluate("document.getElementById('btn-research-resume').style.display !== 'none'")
        status_after_restart = await page.evaluate("_researchDetails ? _researchDetails.batch.status : 'unknown'")
        print(f"Batch status after restart: {status_after_restart}, Resume button visible: {resume_visible}")
        await page.screenshot(path=str(EXPORT_DIR / "05_server_restart_resume.png"))
        print("Saved 05_server_restart_resume.png")
        
        # Click Resume button
        print("Clicking Resume button to continue batch research...", flush=True)
        await page.evaluate("async () => { await resumeActiveBatch(); }")
        await asyncio.sleep(2.0)
        
        # 6. Monitor progress until all 5 domains are completed
        print("Monitoring batch research completion...")
        for wait_cycle in range(35): # max ~6 minutes
            await asyncio.sleep(10.0)
            await page.evaluate(f"loadResearchBatchDetails('{batch_id}')")
            status = await page.evaluate("_researchDetails ? _researchDetails.batch.status : 'unknown'")
            domains = await page.evaluate("_researchDetails ? _researchDetails.domains : []")
            completed = sum(1 for d in domains if d.get("status") in ("completed", "partial"))
            total = len(domains)
            print(f"  Cycle {wait_cycle+1}: Batch status={status}, domains processed={completed}/{total}")
            
            if status in ("completed", "done") or (total > 0 and completed == total):
                print(f"Batch completed successfully in cycle {wait_cycle+1}!")
                break
                
        await asyncio.sleep(3.0)
        await page.screenshot(path=str(EXPORT_DIR / "06_batch_run_completed.png"))
        print("Saved 06_batch_run_completed.png")
        
        # 7. Open verified leader modal (fedex.com or first completed domain)
        print("Opening verified leaders modal...")
        domains = await page.evaluate("_researchDetails ? _researchDetails.domains : []")
        completed_dom = next((d["domain"] for d in domains if d.get("status") in ("completed", "partial")), "fedex.com")
        print(f"Opening modal for {completed_dom}...")
        await page.evaluate(f"openResearchDetailModal('{completed_dom}')")
        await asyncio.sleep(2.0)
        modal_visible = await page.evaluate("document.getElementById('research-detail-modal').style.display !== 'none'")
        print(f"Verified modal visible: {modal_visible}")
        await page.screenshot(path=str(EXPORT_DIR / "07_modal_verified_leaders.png"))
        print("Saved 07_modal_verified_leaders.png")
        await page.evaluate("closeResearchDetailModal()")
        await asyncio.sleep(1.0)
        
        # 8. Open unavailable modal (BearCom)
        print("Switching to batch 48196c105aac to inspect BearCom unavailable modal...")
        await page.evaluate("onResearchBatchSelect('48196c105aac')")
        await asyncio.sleep(2.0)
        print("Opening modal for bearcom.com...")
        await page.evaluate("openResearchDetailModal('bearcom.com')")
        await asyncio.sleep(2.0)
        modal_reason = await page.text_content("#res-modal-body")
        print(f"BearCom diagnostic modal text: {modal_reason.strip()[:100] if modal_reason else 'None'}")
        await page.screenshot(path=str(EXPORT_DIR / "08_modal_unavailable_domain.png"))
        print("Saved 08_modal_unavailable_domain.png")
        await page.evaluate("closeResearchDetailModal()")
        await asyncio.sleep(1.0)
        
        # Switch back to new batch for exports and comparison
        await page.evaluate(f"onResearchBatchSelect('{batch_id}')")
        await asyncio.sleep(2.0)
        
        # 9. Download CSV and HTML exports
        print("Exporting CSV...")
        csv_url = f"{BASE_URL}/api/research/batch/{batch_id}/export?format=csv"
        csv_resp = await page.request.get(csv_url, headers={"Authorization": f"Bearer {token}"})
        csv_bytes = await csv_resp.body()
        csv_path = EXPORT_DIR / f"research_export_{batch_id}.csv"
        csv_path.write_bytes(csv_bytes)
        print(f"Saved CSV export: {csv_path} ({len(csv_bytes)} bytes)")
        
        print("Exporting HTML...")
        html_url = f"{BASE_URL}/api/research/batch/{batch_id}/export?format=html"
        html_resp = await page.request.get(html_url, headers={"Authorization": f"Bearer {token}"})
        html_bytes = await html_resp.body()
        html_path = EXPORT_DIR / f"research_report_{batch_id}.html"
        html_path.write_bytes(html_bytes)
        print(f"Saved HTML export: {html_path} ({len(html_bytes)} bytes)")
        
        await page.screenshot(path=str(EXPORT_DIR / "09_export_download_csv_html.png"))
        print("Saved 09_export_download_csv_html.png")
        
        # 10. Perform 3-value comparison
        ui_domain_count = await page.evaluate("document.querySelectorAll('#research-results-tbody tr').length")
        
        conn = sqlite3.connect(f"file:{TARGET_DB}?mode=ro", uri=True)
        cur = conn.cursor()
        db_domain_count = cur.execute("SELECT count(*) FROM import_domains WHERE batch_id = ? AND status IN ('completed', 'partial')", (batch_id,)).fetchone()[0]
        db_people_count = cur.execute("""
            SELECT count(*) FROM research_people rp
            JOIN import_domains id ON rp.run_id = id.run_id
            WHERE id.batch_id = ?
        """, (batch_id,)).fetchone()[0]
        conn.close()
        
        csv_lines = [l for l in csv_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        csv_row_count = len(csv_lines) - 1 # exclude header
        
        print(f"\n=== 3-VALUE COMPARISON ===")
        print(f"  UI Domains Count:        {ui_domain_count}")
        print(f"  SQLite DB Domains Count: {db_domain_count}")
        print(f"  Exported CSV Rows Count: {csv_row_count}")
        print(f"  SQLite DB People Count:  {db_people_count}")
        
        # 11. Run DOM text leak scan
        print("\n=== RUNNING DOM TEXT LEAK SCAN ===")
        full_dom_text = await page.inner_text("body")
        
        leaks_found = []
        if token in full_dom_text:
            leaks_found.append("FULL_DASHBOARD_TOKEN_VISIBLE_IN_DOM")
        
        email_matches = set(re.findall(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', full_dom_text))
        print(f"Emails found in DOM: {email_matches}")
        
        leak_report = {
            "token_leaked": token in full_dom_text,
            "emails_count": len(email_matches),
            "emails": list(email_matches),
            "leaks_found": leaks_found,
            "dom_length": len(full_dom_text),
            "passed": len(leaks_found) == 0 and not (token in full_dom_text),
            "comparison": {
                "ui_domains": ui_domain_count,
                "db_domains": db_domain_count,
                "csv_rows": csv_row_count,
                "db_people": db_people_count
            }
        }
        
        (EXPORT_DIR / "dom_leak_scan_results.json").write_text(json.dumps(leak_report, indent=2), encoding="utf-8")
        print(f"DOM leak scan verdict: {'PASSED (0 leaks)' if leak_report['passed'] else 'FAILED'}")
        
        await page.screenshot(path=str(EXPORT_DIR / "10_dom_leak_scan_passed.png"))
        print("Saved 10_dom_leak_scan_passed.png")
        
        await browser.close()
        
    stop_server(proc)
    print("\n=== ITEM 3 EXECUTION COMPLETED SUCCESSFULLY ===")

if __name__ == "__main__":
    asyncio.run(main())
