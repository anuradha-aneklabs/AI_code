import sqlite3

conn = sqlite3.connect("data/validation/harvey_e2e.db")
cur = conn.cursor()

print("=== 1. DISCOVERY PROFILE IN DB (business_profiles) ===")
cur.execute("SELECT id, name, industry, is_default, created_at FROM business_profiles WHERE id='6d0649a9313e'")
print(cur.fetchall())

print("\n=== 2 & 3. CONFIRMED SIGNALS COUNT IN DB ===")
cur.execute("SELECT status, count(*) FROM signal_codes GROUP BY status")
print(cur.fetchall())

print("\n=== 4. LIVE DISCOVERY RESULTS IN DB (RUN 81ecc6914562) ===")
cur.execute("SELECT run_id, count(*), sum(confidence) FROM observations WHERE run_id='81ecc6914562'")
print("Observations for run:", cur.fetchall())

cur.execute("""
SELECT c.name, c.domain, c.product_class, c.product_class_evidence
FROM companies c
JOIN observations o ON o.company_id = c.id
WHERE o.run_id = '81ecc6914562'
""")
rows = cur.fetchall()
print(f"Companies saved for run ({len(rows)}):")
for r in rows:
    print(" ", r)

print("\n=== 5. PROFILED OBSERVATIONS IN DB ===")
cur.execute("SELECT count(*) FROM observations WHERE collector = 'profile'")
print("Total profile collector observations:", cur.fetchone()[0])

print("\n=== 6. BATCH RESEARCH IN DB (BATCH 5e903c09b42f) ===")
cur.execute("PRAGMA table_info(import_batches)")
print("import_batches columns:", [r[1] for r in cur.fetchall()])
cur.execute("SELECT * FROM import_batches WHERE id='5e903c09b42f'")
print("Batch row:", cur.fetchall())

cur.execute("PRAGMA table_info(import_rows)")
print("import_rows columns:", [r[1] for r in cur.fetchall()])
cur.execute("SELECT domain, company_name_hint, row_status FROM import_rows WHERE batch_id='5e903c09b42f'")
print("Import rows in batch:", cur.fetchall())

cur.execute("PRAGMA table_info(import_domains)")
print("import_domains columns:", [r[1] for r in cur.fetchall()])
cur.execute("SELECT domain, status, run_id FROM import_domains WHERE batch_id='5e903c09b42f'")
print("Import domains in batch:", cur.fetchall())

cur.execute("SELECT count(*) FROM research_findings WHERE run_id IN (SELECT run_id FROM import_domains WHERE batch_id='5e903c09b42f')")
print("Research findings count for batch:", cur.fetchone()[0])
