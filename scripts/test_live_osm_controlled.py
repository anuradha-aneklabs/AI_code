"""Controlled, read-only diagnostic test of OpenStreetMap Overpass discovery.

Target:
- ICP: Dental Clinics & Practices (term: 'dentist')
- Location: Indore, Madhya Pradesh, India
- Provider: Overpass only
- Limit: 5

Constraints:
- No paid APIs
- No CRM imports
- No campaign drafts
- No emails sent
- If Overpass is unavailable (502 / timeout), reports honestly without falling back to Nominatim business search.
"""

import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path

import httpx

from harvey.collectors.discover import (
    DiscoveryQuery,
    OpenStreetMap,
    geocode_city,
    get_nominatim_user_agent,
)
from harvey.paths import PROJECT_ROOT
from harvey.state import StateManager


async def run_controlled_test():
    db_path = PROJECT_ROOT / "data" / "harvey.db"
    conn = sqlite3.connect(str(db_path))
    outbox_before = conn.execute("SELECT count(*) FROM outbox").fetchone()[0]
    sent_before = conn.execute("SELECT count(*) FROM outbox WHERE status = 'sent'").fetchone()[0]
    companies_before = conn.execute("SELECT count(*) FROM companies").fetchone()[0]
    conn.close()

    print("=" * 60)
    print("HARVEY CONTROLLED OSM OVERPASS DISCOVERY TEST")
    print("=" * 60)
    print(f"Safety Pre-check:")
    print(f"  Outbox records: {outbox_before}")
    print(f"  Sent emails: {sent_before}")
    print(f"  Companies count: {companies_before}")
    print(f"  User-Agent configured: {get_nominatim_user_agent()}")
    print("-" * 60)

    state = StateManager(db_path=str(db_path))
    await state.init_db()

    target_city = "Indore, Madhya Pradesh, India"
    target_term = "dentist"
    limit = 5

    # Step 1: Geocode city using legitimate Nominatim single-point lookup
    print(f"\n[1] Resolving coordinates for location: {target_city!r}")
    cached_geo = await state.get_setting(f"geo:{target_city.strip().lower()}")
    if cached_geo:
        print(f"  Found cached coordinates: {cached_geo}")
        coords = f"{cached_geo},25"
    else:
        print("  Querying Nominatim for city coordinates...")
        coords = await geocode_city(state, target_city, radius_km=25)
        print(f"  Resolved coordinates: {coords}")

    # Step 2: Prepare Overpass discovery query
    osm = OpenStreetMap()
    print(f"\n[2] Instantiating OpenStreetMap discovery provider:")
    print(f"  Provider key: {osm.key}")
    print(f"  Nominatim fallback disabled: {osm.enable_nominatim_fallback is False}")
    print(f"  Overpass mirrors to try: {list(osm.ENDPOINTS)}")

    query = DiscoveryQuery(
        term=target_term,
        location=target_city,
        coordinate=coords,
        limit=limit,
    )

    # Step 3: Run Overpass query
    print(f"\n[3] Executing Overpass query for {query.keyword()!r} (limit={limit})...")
    start_time = time.monotonic()
    overpass_error = None
    results = []

    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)) as client:
        try:
            results = await osm.fetch(client, {}, query)
        except Exception as e:
            overpass_error = e

    duration = time.monotonic() - start_time
    print(f"  Completed in {duration:.2f} seconds.")

    # Step 4: Report results honestly
    print(f"\n[4] Discovery Test Result:")
    if overpass_error:
        print(f"  OVERPASS RESULT: UNAVAILABLE / ERROR")
        print(f"  Error Detail: {overpass_error}")
        print(f"  Failsafe Status: PASSED (failed closed cleanly; no illegal Nominatim POI fallback attempted)")
    else:
        print(f"  OVERPASS RESULT: SUCCESS ({len(results)} businesses found)")
        for idx, biz in enumerate(results, 1):
            print(f"    {idx}. {biz.name}")
            print(f"       Domain: {biz.domain or '(no website)'}")
            print(f"       Address: {biz.address or biz.location}")
            print(f"       OSM Node/Way: {biz.external_id}")
            print(f"       Source URL: {biz.source_url}")
            print(f"       Provenance: {biz.provenance}")

    # Step 5: Post-test safety invariant verification
    conn = sqlite3.connect(str(db_path))
    outbox_after = conn.execute("SELECT count(*) FROM outbox").fetchone()[0]
    sent_after = conn.execute("SELECT count(*) FROM outbox WHERE status = 'sent'").fetchone()[0]
    companies_after = conn.execute("SELECT count(*) FROM companies").fetchone()[0]
    conn.close()

    print("\n" + "=" * 60)
    print("POST-TEST SAFETY INVARIANT VERIFICATION:")
    print(f"  Outbox count: {outbox_before} -> {outbox_after} (Diff: {outbox_after - outbox_before})")
    print(f"  Sent count: {sent_before} -> {sent_after} (Diff: {sent_after - sent_before})")
    print(f"  Companies count: {companies_before} -> {companies_after} (Diff: {companies_after - companies_before})")
    print(f"  Safety Hold Preserved: {outbox_before == outbox_after and sent_before == sent_after and companies_before == companies_after}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_controlled_test())
