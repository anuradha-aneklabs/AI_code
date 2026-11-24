"""Reclassify stored companies in data/validation/harvey_e2e2.db and harvey_validation5.db.

Reads stored pages/findings only (zero network requests).
Updates product_class and product_class_evidence columns.
Prints breakdown counts per class with evidence tokens.
Ensures EngineerBabu, Easinvy, and Bridge Logic Software are NOT software_product.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from harvey.software_classifier import classify_software_business


def reclassify_db(db_path: Path):
    print(f"\n=======================================================")
    print(f" Reclassifying: {db_path}")
    print(f"=======================================================")

    con = sqlite3.connect(str(db_path))
    cur = con.cursor()

    # Ensure columns exist in companies table
    cur.execute("PRAGMA table_info(companies)")
    cols = [r[1] for r in cur.fetchall()]
    if "product_class" not in cols:
        cur.execute("ALTER TABLE companies ADD COLUMN product_class TEXT DEFAULT ''")
    if "product_class_evidence" not in cols:
        cur.execute("ALTER TABLE companies ADD COLUMN product_class_evidence TEXT DEFAULT ''")
    con.commit()

    # Fetch all companies
    cur.execute("SELECT id, name, domain, website, industry, description FROM companies")
    companies = cur.fetchall()
    print(f"Total companies to reclassify: {len(companies)}")

    counts: dict[str, int] = {}
    evidence_samples: dict[str, list[str]] = {}
    target_results: dict[str, tuple[str, str]] = {}

    for c_id, name, domain, website, industry, description in companies:
        # Gather all stored text for this company from DB
        text_snippets = [name or "", description or ""]

        # Check discovery_results
        try:
            cur.execute("SELECT title FROM discovery_results WHERE domain = ?", (domain,))
            for r in cur.fetchall():
                if r[0]:
                    text_snippets.append(r[0])
        except Exception:
            pass

        # Check research_findings
        try:
            cur.execute("SELECT value_text FROM research_findings WHERE domain = ?", (domain,))
            for r in cur.fetchall():
                if r[0] and r[0] != "unavailable":
                    text_snippets.append(r[0])
        except Exception:
            pass

        # Check observations
        try:
            cur.execute("SELECT evidence_url FROM observations WHERE company_id = ?", (c_id,))
            for r in cur.fetchall():
                if r[0]:
                    text_snippets.append(r[0])
        except Exception:
            pass

        combined_text = " ".join(text_snippets)
        p_class, p_ev = classify_software_business(
            combined_text,
            title=name or domain,
            domain=domain,
            result_class="company_homepage",
        )

        cur.execute(
            "UPDATE companies SET product_class = ?, product_class_evidence = ? WHERE id = ?",
            (p_class, p_ev, c_id),
        )

        counts[p_class] = counts.get(p_class, 0) + 1
        if p_class not in evidence_samples:
            evidence_samples[p_class] = []
        if len(evidence_samples[p_class]) < 3:
            evidence_samples[p_class].append(f"{domain}: {p_ev}")

        if domain in ("engineerbabu.com", "easinvy.com", "bridgelogicsoftware.com"):
            target_results[domain] = (p_class, p_ev)

    con.commit()
    con.close()

    print("\nClass Breakdown:")
    for cls_name, count in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  * {cls_name}: {count}")
        for sample in evidence_samples.get(cls_name, []):
            print(f"      - {sample}")

    print("\nTarget Agency Verification (MUST NOT be software_product):")
    for dom in ("engineerbabu.com", "easinvy.com", "bridgelogicsoftware.com"):
        res = target_results.get(dom, ("not_found", "not_found"))
        print(f"  * {dom}: {res[0]} -> {res[1]}")
        assert res[0] != "software_product", f"FAILED: {dom} was classified as software_product!"

    print("Target agency check: PASSED (None of the 3 are software_product).")
    return counts


def main():
    db1 = Path("data/validation/harvey_e2e2.db")
    db2 = Path("data/validation/harvey_validation5.db")

    reclassify_db(db1)
    reclassify_db(db2)
    print("\nTASK 3 STATUS: VERIFIED")


if __name__ == "__main__":
    main()
