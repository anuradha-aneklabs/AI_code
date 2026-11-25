"""Capture Playwright browser UI proof screenshots for Ma'am's workflow on port 5566."""

import asyncio
import os
from pathlib import Path
from playwright.async_api import async_playwright

OUT_DIR = Path("data/exports/maam_proof")
PORT = 5566
BATCH_ID = "3cb3b28557d6"


async def capture_proof():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    from harvey.dashboard import token_store
    token = token_store.get()
    url = f"http://127.0.0.1:{PORT}/#token={token}"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1440, "height": 960})
        page = await context.new_page()

        print(f"Navigating to dashboard on port {PORT}...")
        await page.goto(url, wait_until="networkidle")
        await asyncio.sleep(2.0)

        # 1. Switch to Research tab
        print("Switching to Research tab...")
        research_btn = page.locator("button[data-tab='research'], a[href*='research'], button:has-text('Research')")
        if await research_btn.count() > 0:
            await research_btn.first.click()
            await asyncio.sleep(2.0)

        # 2. Select Batch 3cb3b28557d6
        print("Selecting batch in dropdown...")
        batch_select = page.locator("#research-batch-select")
        if await batch_select.count() > 0:
            options = await batch_select.locator("option").all_inner_texts()
            for opt in options:
                if BATCH_ID[:6] in opt or "Maam" in opt:
                    await batch_select.select_option(label=opt)
                    break
            await asyncio.sleep(2.0)

        # Screenshot 1: Batch Overview & Metrics Banner
        print("Capturing 01_maam_batch_overview.png...")
        await page.screenshot(path=str(OUT_DIR / "01_maam_batch_overview.png"), full_page=False)
        print("Saved 01_maam_batch_overview.png")

        # 3. Open Details Modal for anikalabs.com
        print("Opening details modal for anikalabs.com...")
        anika_row = page.locator("tr:has-text('anikalabs.com')").first
        if await anika_row.count() > 0:
            btn = anika_row.locator("button:has-text('View Details'), button:has-text('Details'), a:has-text('Details')")
            if await btn.count() > 0:
                await btn.first.click()
            else:
                await anika_row.click()
            await asyncio.sleep(2.0)

        # Screenshot 2: Anika Labs Details Modal (Overview, Services, CXO Leaders)
        print("Capturing 02_maam_anikalabs_modal.png...")
        modal = page.locator("#research-detail-modal")
        if await modal.count() > 0 and await modal.is_visible():
            await page.screenshot(path=str(OUT_DIR / "02_maam_anikalabs_modal.png"), full_page=False)
            print("Saved 02_maam_anikalabs_modal.png")
            # Close modal cleanly
            await page.keyboard.press("Escape")
            await page.evaluate("() => { if (typeof closeResearchDetailModal === 'function') closeResearchDetailModal(); const m = document.getElementById('research-detail-modal'); if (m) m.style.display = 'none'; }")
            await asyncio.sleep(1.0)

        # Screenshot 3: Exports & Unmask Control Bar
        print("Capturing 03_maam_exports_and_unmask.png...")
        unmask_btn = page.locator("#btn-research-unmask-toggle")
        if await unmask_btn.count() > 0:
            await unmask_btn.first.scroll_into_view_if_needed()
            await asyncio.sleep(0.5)
        await page.screenshot(path=str(OUT_DIR / "03_maam_exports_and_unmask.png"), full_page=False)
        print("Saved 03_maam_exports_and_unmask.png")

        # Screenshot 4: Garbage & Dead Domain Filtering (Scroll to bottom of table)
        print("Capturing 04_maam_garbage_filtering.png...")
        dead_row = page.locator("tr:has-text('nonexistent-domain')").first
        if await dead_row.count() > 0:
            await dead_row.scroll_into_view_if_needed()
            await asyncio.sleep(1.0)
        await page.screenshot(path=str(OUT_DIR / "04_maam_garbage_filtering.png"), full_page=False)
        print("Saved 04_maam_garbage_filtering.png")

        await browser.close()
        print("All UI proof screenshots captured successfully!")


if __name__ == "__main__":
    asyncio.run(capture_proof())
