#!/usr/bin/env python3
"""CLI runner for Milestone 1 single-domain research foundation.

Usage:
    python scripts/research_one.py <email>

Executes email validation, domain extraction, website verification,
page discovery, and page fetching, progressively persisting all results.
"""

import asyncio
import json
import sys
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from harvey.single_domain_research import run_single_email_research
from harvey.state import StateManager


async def main():
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("Usage: python scripts/research_one.py <email>")
        sys.exit(1)

    email = sys.argv[1].strip()
    print("=" * 70)
    print("HARVEY SINGLE-DOMAIN RESEARCH RUNNER")
    print("=" * 70)
    print(f"Target Email: {email}")

    state = StateManager()
    await state.init_db()

    result = await run_single_email_research(email, state=state)
    if not result:
        print("[ERROR] Research run returned no result.")
        sys.exit(1)

    run_id = result.get("id")
    status = result.get("status")
    domain = result.get("domain") or "N/A"

    print(f"\nRun ID:  {run_id}")
    print(f"Domain:  {domain}")
    print(f"Status:  {status.upper() if status else 'UNKNOWN'}")
    if result.get("error_summary"):
        print(f"Summary: {result.get('error_summary')}")

    print("\n--- Pipeline Stages ---")
    stages = result.get("stages_list", [])
    for stg in stages:
        s_name = stg.get("stage_name")
        s_status = stg.get("status")
        s_err = stg.get("error_message") or ""
        mark = "✓" if s_status == "completed" else ("⊘" if s_status == "skipped" else "✗")
        err_disp = f" ({s_err})" if s_err else ""
        print(f"  [{mark}] {s_name:<22}: {s_status}{err_disp}")

    print("\n--- Website Verification ---")
    website = result.get("website")
    if website:
        v_status = website.get("verification_status")
        c_url = website.get("candidate_url")
        f_url = website.get("final_url") or "None"
        code = website.get("http_status")
        conf = website.get("confidence")
        ev = website.get("evidence_text")
        print(f"  Verification Status: {v_status}")
        print(f"  Confidence:          {conf}")
        print(f"  Candidate URL:       {c_url}")
        print(f"  Final URL:           {f_url} (HTTP {code})")
        print(f"  Evidence:            {ev}")
    else:
        print("  No website record persisted for this run.")

    print("\n--- Discovered & Fetched Pages ---")
    pages = result.get("pages", [])
    if pages:
        print(f"  Total Pages: {len(pages)}")
        for idx, pg in enumerate(pages, 1):
            p_url = pg.get("url")
            p_type = pg.get("page_type")
            p_stat = pg.get("extraction_status")
            p_code = pg.get("http_status")
            p_hash = (pg.get("content_hash") or "")[:12]
            p_err = pg.get("error_message") or ""
            err_str = f" - {p_err}" if p_err else ""
            print(f"  {idx}. [{p_stat}] {p_type:<10} | HTTP {p_code} | Hash: {p_hash} | {p_url}{err_str}")
    else:
        print("  No pages discovered or fetched for this run.")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
