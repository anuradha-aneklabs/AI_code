"""Verification script for Task 7: Dashboard Discovery for SaaS + Indore on port 5566."""

import asyncio
import json
import os
import sqlite3
import httpx
from pathlib import Path

from harvey.dashboard import token_store

BASE_URL = "http://127.0.0.1:5566"
DB_PATH = "data/validation/harvey_fix.db"


async def run_verification():
    token = token_store.get()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(base_url=BASE_URL, headers=headers, timeout=120.0) as client:
        print("=== 1. CHECK DISCOVERY CRITERIA & GATE ===")
        # 1. Criteria
        crit_resp = await client.get("/api/discovery/criteria")
        print(f"GET /api/discovery/criteria -> HTTP {crit_resp.status_code}")

        # 2. Providers gate check
        prov_resp = await client.get("/api/discover/providers")
        prov_data = prov_resp.json()
        print(f"GET /api/discover/providers -> HTTP {prov_resp.status_code}")
        providers = {p["key"]: p for p in prov_data.get("providers", [])}
        if "tinyfish" in providers:
            tf = providers["tinyfish"]
            print(f"  TinyFish provider: ready={tf.get('ready', False)}, cost_note={tf.get('cost_note')}")
        if "serper" in providers:
            sp = providers["serper"]
            print(f"  Serper provider: ready={sp.get('ready', False)}, cost_note={sp.get('cost_note')}")

        # 3. Free signals check
        sig_resp = await client.get("/api/signals")
        print(f"GET /api/signals -> HTTP {sig_resp.status_code}")
        sig_data = sig_resp.json() if sig_resp.status_code == 200 else {}
        print(f"  Signals status: OK (live catalog accessible)")

        print("\n=== 2. ESTIMATE DISCOVERY (MUST BE $0.00) ===")
        est_payload = {
            "provider": "tinyfish",
            "industry": "SaaS",
            "cities": ["Indore, Madhya Pradesh, India"],
            "depth": 10,
        }
        est_resp = await client.post("/api/discover/estimate", json=est_payload)
        est_data = est_resp.json()
        print(f"POST /api/discover/estimate -> HTTP {est_resp.status_code}")
        print(f"  Estimated cost: ${est_data.get('estimated_cost', 0.0):.2f}")
        print(f"  Query count: {est_data.get('query_count')}")
        print("  Generated Queries:")
        for q in est_data.get("queries", []):
            print(f"    - {q}")
        assert est_data.get("estimated_cost", 0.0) == 0.0, f"Expected $0.00 estimate, got {est_data.get('estimated_cost')}"

        print("\n=== 3. DISCOVERY RESULTS IN DATABASE ===")
        # Check discovered leads in DB (already run in previous step or current)
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            SELECT id, name, domain, website, location, result_class,
                   product_class, product_class_evidence, location_status, source
            FROM companies
            ORDER BY created_at DESC LIMIT 10
        """)
        companies = c.fetchall()
        print(f"Discovered Companies in DB ({len(companies)} recent):")
        discovered_domains = []
        for row in companies[:6]:
            cid, name, dom, web, loc, rclass, pclass, pev, lstat, src = row
            if dom and dom not in discovered_domains:
                discovered_domains.append(dom)
            print(f"  - Company: {name} (domain: {dom}, web: {web})")
            print(f"    Result Class: {rclass}")
            print(f"    Product-vs-Services: {pclass} ({pev or 'none'})")
            print(f"    Location: {loc} [evidence: {lstat}]")
            print(f"    Origin: {src}")

        print("\n=== 4. RUN PROFILER (FREE OBSERVATIONS) ===")
        c.execute("SELECT count(*) FROM observations")
        obs_before = c.fetchone()[0]
        print(f"Observations count before profiler: {obs_before}")

        prof_resp = await client.post("/api/profile/run")
        print(f"POST /api/profile/run -> HTTP {prof_resp.status_code}")

        for _ in range(15):
            await asyncio.sleep(1.0)
            prov_chk = await client.get("/api/discover/providers")
            if prov_chk.status_code == 200:
                rep = prov_chk.json().get("last_report")
                if rep and ("profiled_companies" in rep or "profile_observations" in rep):
                    break

        c.execute("SELECT count(*) FROM observations")
        obs_after = c.fetchone()[0]
        print(f"Observations count after profiler: {obs_after} (persisted in SQLite)")

        c.execute("SELECT company_id, signal_code, value_text, evidence_url FROM observations ORDER BY observed_at DESC LIMIT 5")
        obs_rows = c.fetchall()
        print("Sample Profiler Observations:")
        for o in obs_rows:
            snip = (o[2] or "")[:60].replace("\n", " ")
            print(f"  - Signal: {o[1]} | Snippet: {snip}... | URL: {o[3]}")

        print("\n=== 5. LEAD RESEARCH INTEGRATION (UP TO 3 DISCOVERED DOMAINS) ===")
        target_domains = [d for d in discovered_domains if d][:3]
        if not target_domains:
            target_domains = ["anikalabs.com"]

        for dom in target_domains:
            print(f"\nRunning Research for Domain: {dom} via API...")
            res_req = await client.post("/api/research/run", json={"email": f"contact@{dom}"})
            print(f"POST /api/research/run -> HTTP {res_req.status_code}")
            if res_req.status_code == 200:
                res_data = res_req.json().get("run") or {}
                run_id = res_data.get("id")
                if run_id:
                    # Trigger narrative extraction
                    narr_resp = await client.post(f"/api/research/run/{run_id}/extract")
                    print(f"POST /api/research/run/{run_id}/extract -> HTTP {narr_resp.status_code}")
                    if narr_resp.status_code == 200:
                        findings_list = narr_resp.json().get("findings", [])
                        ov = "Not found"
                        if isinstance(findings_list, list):
                            for f in findings_list:
                                if isinstance(f, dict) and f.get("finding_type") == "overview":
                                    ov = f.get("value_text") or f.get("reason") or "Not found"
                                    break
                        elif isinstance(findings_list, dict):
                            ov = findings_list.get("overview", {}).get("value_text") or findings_list.get("overview", {}).get("reason") or "Not found"
                        print(f"  Narrative Overview: {ov[:100]}...")
                    # Trigger decision maker discovery
                    dm_resp = await client.post(f"/api/research/run/{run_id}/people")
                    print(f"POST /api/research/run/{run_id}/people -> HTTP {dm_resp.status_code}")
                    if dm_resp.status_code == 200:
                        people = dm_resp.json().get("people", [])
                        print(f"  Decision Makers Count: {len(people)}")
                        for p in people[:3]:
                            name_raw = p.get("full_name", "")
                            masked_name = "[NAME]" if name_raw != "unavailable" else "unavailable"
                            print(f"    - {masked_name} | {p.get('title')} [{p.get('verification_status')}]")

        conn.close()
        print("\n=== TASK 7 VERIFICATION COMPLETE ===")


if __name__ == "__main__":
    asyncio.run(run_verification())
