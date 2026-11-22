#!/usr/bin/env python3
"""CLI entry point to execute decision-maker discovery for an existing research run.

Usage:
    python scripts/find_decision_makers_one.py <run_id>
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from harvey.decision_makers import find_decision_makers
from harvey.state import StateManager


async def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python scripts/find_decision_makers_one.py <run_id>")
        return 1

    run_id = sys.argv[1].strip()
    state = StateManager()
    await state.init_db()

    run = await state.get_research_run(run_id)
    if not run:
        print(f"Error: Research run '{run_id}' not found.", file=sys.stderr)
        return 1

    domain = run.get("domain", "")
    print(f"Starting decision maker discovery for run: {run_id} (domain: {domain})")

    try:
        people = await find_decision_makers(run_id, state=state)
    except Exception as e:
        print(f"Discovery failed: {e}", file=sys.stderr)
        return 1

    print("\n" + "=" * 60)
    print(f"DECISION MAKERS FOR RUN: {run_id}")
    print(f"Domain: {domain}")
    print(f"Total rows: {len(people)}")
    print("=" * 60)

    for i, p in enumerate(people, start=1):
        v_status = p.get("verification_status", "")
        method = p.get("extraction_method", "")
        cat = p.get("role_category", "")
        name = p.get("full_name", "")
        title = p.get("title", "")
        conf = p.get("confidence", 0.0)
        source = p.get("source_url", "")
        evidence = p.get("evidence_text", "")
        reason = p.get("reason", "")
        also_seen = json.loads(p.get("also_seen_urls_json") or "[]")

        print(f"\n[{i}] {name} | {title} (category: {cat}) | status: {v_status} | method: {method} | conf: {conf:.1f}")
        if source:
            print(f"    Source URL: {source}")
        if also_seen:
            print(f"    Also seen at: {', '.join(also_seen)}")
        if v_status == "unavailable":
            print(f"    Reason: {reason}")
        elif evidence:
            print(f"    Evidence: {evidence[:200]}{'...' if len(evidence) > 200 else ''}")

    print("\n" + "=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
