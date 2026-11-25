"""Live test of Ma'am's workflow for pilot email sunil@anikalabs.com.
Saves all stage data to isolated DB data/validation/harvey_maam_live.db.
"""

import asyncio
import os
import shutil
import sqlite3
from pathlib import Path

# Set test DB path
TEST_DB = Path("data/validation/harvey_maam_live.db")
REAL_DB = Path("data/harvey.db")
TEST_DB.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(REAL_DB, TEST_DB)

os.environ["HARVEY_DB_PATH"] = str(TEST_DB)
os.environ["HARVEY_ALLOW_REAL_DB_WRITE"] = "1"

from harvey.state import StateManager
from harvey.single_domain_research import run_single_email_research
from harvey.company_narrative import extract_company_narrative
from harvey.decision_makers import find_decision_makers


async def run_pilot():
    state = StateManager(db_path=str(TEST_DB))
    await state.init_db()

    email = "sunil@anikalabs.com"
    print(f"=== STAGE 1 & 2: Single Email Research for {email} ===")
    res = await run_single_email_research(email=email, state=state)
    run_id = res.get("id") or res.get("run_id")
    print(f"Run ID: {run_id}")
    print(f"Result Status: {res.get('status')}")

    # Inspect website
    site = await state.get_company_website(run_id)
    print("\n--- Website Verification ---")
    if site:
        for k in ["domain", "candidate_url", "final_url", "http_status", "verification_status", "confidence", "evidence_text"]:
            print(f"  {k}: {site.get(k)}")
    else:
        print("  No company_website row found")

    # Inspect source pages
    pages = await state.get_source_pages(run_id)
    print(f"\n--- Source Pages Scanned ({len(pages)}) ---")
    for p in pages:
        print(f"  URL: {p.get('url')} | Type: {p.get('page_type')} | Status: {p.get('http_status')} | Title: {p.get('title')}")

    # Extract narrative
    print("\n=== STAGE 3: Narrative Extraction (Overview, Services, Vision, Mission) ===")
    narrative = await extract_company_narrative(run_id, state=state)
    if isinstance(narrative, dict):
        for section, data in narrative.items():
            if isinstance(data, dict):
                print(f"  Section [{section}]: status={data.get('status')} | confidence={data.get('confidence')}")
                val = data.get("value") or data.get("text") or data.get("summary") or ""
                if val:
                    val_preview = str(val)[:150].replace("\n", " ")
                    print(f"    Content preview: {val_preview}")

    # Find decision makers
    print("\n=== STAGE 4: Decision Makers Discovery (Founder, CEO, Director, CXO) ===")
    people = await find_decision_makers(run_id, state=state)
    print(f"  Total people items: {len(people)}")
    for person in people:
        name = person.get("name") or person.get("full_name") or "Unknown"
        role = person.get("role") or person.get("title") or "Unknown"
        status = person.get("verification_status") or person.get("status")
        evidence = person.get("evidence_text") or person.get("evidence") or ""
        print(f"  Person: {name} | Role: {role} | Status: {status} | Evidence: {evidence[:100]}")

    # Verify SQLite DB persistence
    print("\n=== STAGE 5: SQLite Database Verification ===")
    conn = sqlite3.connect(TEST_DB)
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM runs WHERE id = ?", (run_id,))
    print(f"  runs row exists: {cur.fetchone()[0] == 1}")
    cur.execute("SELECT count(*) FROM company_websites WHERE run_id = ?", (run_id,))
    print(f"  company_websites row exists: {cur.fetchone()[0] == 1}")
    cur.execute("SELECT count(*) FROM source_pages WHERE run_id = ?", (run_id,))
    print(f"  source_pages count: {cur.fetchone()[0]}")
    cur.execute("SELECT count(*) FROM observations WHERE run_id = ?", (run_id,))
    print(f"  observations count: {cur.fetchone()[0]}")
    cur.execute("SELECT signal_code, value_text, confidence, evidence_url FROM observations WHERE run_id = ?", (run_id,))
    obs_rows = cur.fetchall()
    for code, val, conf, ev_url in obs_rows:
        val_preview = (val or "")[:60].replace("\n", " ")
        print(f"    Obs [{code}]: {val_preview} (conf={conf}) [URL: {ev_url}]")
    conn.close()


if __name__ == "__main__":
    asyncio.run(run_pilot())
