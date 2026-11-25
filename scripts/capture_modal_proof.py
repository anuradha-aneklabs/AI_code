import asyncio
import os
import sys
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from playwright.async_api import async_playwright

OUT_DIR = ROOT / "data" / "exports" / "maam_proof"
PORT = 5566
BATCH_ID = "3cb3b28557d6"


async def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # Start server
    proc = subprocess.Popen(
        [".venv/Scripts/python.exe", "scripts/run_test_dashboard.py"],
        env={**os.environ, "HARVEY_DB_PATH": "data/validation/harvey_maam_e2e.db", "HARVEY_ALLOW_REAL_DB_WRITE": "1"}
    )
    await asyncio.sleep(3.0)

    try:
        from harvey.dashboard import token_store
        token = token_store.get()
        url = f"http://127.0.0.1:{PORT}/#token={token}"

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(viewport={"width": 1440, "height": 960})
            page = await context.new_page()

            await page.goto(url, wait_until="networkidle")
            await asyncio.sleep(2.0)

            # Switch to Research tab
            research_btn = page.locator("button[data-tab='research'], a[href*='research'], button:has-text('Research')")
            if await research_btn.count() > 0:
                await research_btn.first.click()
                await asyncio.sleep(2.0)

            # Select batch
            batch_select = page.locator("#research-batch-select")
            if await batch_select.count() > 0:
                options = await batch_select.locator("option").all_inner_texts()
                for opt in options:
                    if BATCH_ID[:6] in opt or "Maam" in opt:
                        await batch_select.select_option(label=opt)
                        break
                await asyncio.sleep(2.0)

            # Open details modal for anikalabs.com via direct JS or View button
            print("Opening modal for anikalabs.com...")
            await page.evaluate("openResearchDetailModal('anikalabs.com')")
            await asyncio.sleep(2.0)

            modal = page.locator("#research-detail-modal")
            if await modal.count() > 0:
                out_path = OUT_DIR / "02_maam_anikalabs_modal.png"
                await modal.screenshot(path=str(out_path))
                print(f"Successfully saved {out_path} ({out_path.stat().st_size} bytes)")

            await browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    asyncio.run(main())
