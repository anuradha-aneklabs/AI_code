#!/usr/bin/env python3
"""CLI entry point to execute company narrative extraction for an existing research run.

Usage:
    python scripts/extract_company_one.py <run_id>
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from harvey.company_narrative import extract_company_narrative
from harvey.state import StateManager


async def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python scripts/extract_company_one.py <run_id>")
        return 1

    run_id = sys.argv[1].strip()
    state = StateManager()
    await state.init_db()

    run = await state.get_research_run(run_id)
    if not run:
        print(f"Error: Research run '{run_id}' not found.", file=sys.stderr)
        return 1

    domain = run.get("domain", "")
    print(f"Starting narrative extraction for run: {run_id} (domain: {domain})")

    try:
        findings = await extract_company_narrative(run_id, state=state)
    except Exception as e:
        print(f"Extraction failed: {e}", file=sys.stderr)
        return 1

    print("\n" + "=" * 60)
    print(f"EXTRACTION RESULTS FOR RUN: {run_id}")
    print(f"Domain: {domain}")
    print(f"Total findings: {len(findings)}")
    print("=" * 60)

    for i, f in enumerate(findings, start=1):
        ftype = f.get("finding_type", "").upper()
        v_status = f.get("verification_status", "")
        method = f.get("extraction_method", "")
        conf = f.get("confidence", 0.0)
        source = f.get("source_url", "")
        val = f.get("value_text", "")
        evidence = f.get("evidence_text", "")
        reason = f.get("reason", "")

        print(f"\n[{i}] {ftype} | status: {v_status} | method: {method} | conf: {conf:.1f}")
        if source:
            print(f"    Source URL: {source}")
        if v_status == "unavailable":
            print(f"    Reason: {reason}")
        else:
            print(f"    Value: {val[:200]}{'...' if len(val) > 200 else ''}")
            if evidence and evidence != val:
                print(f"    Evidence: {evidence[:150]}{'...' if len(evidence) > 150 else ''}")

    print("\n" + "=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
