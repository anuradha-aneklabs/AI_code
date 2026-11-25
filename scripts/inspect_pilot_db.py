import sqlite3

conn = sqlite3.connect("data/validation/harvey_maam_live.db")
cur = conn.cursor()

print("=== research_findings ===")
cur.execute("SELECT finding_type, value_text, verification_status, confidence, source_url FROM research_findings WHERE run_id = ?", ("a08f076c8053",))
for row in cur.fetchall():
    print(f"  [{row[0]}] status={row[2]} conf={row[3]} url={row[4]}")
    val_preview = (row[1] or "")[:120].replace("\n", " ")
    print(f"    val: {val_preview}")

print("\n=== research_people ===")
cur.execute("SELECT full_name, title, role_category, verification_status, source_url, evidence_text FROM research_people WHERE run_id = ?", ("a08f076c8053",))
for row in cur.fetchall():
    print(f"  Leader: {row[0]} | Title: {row[1]} | Cat: {row[2]} | Status: {row[3]} | URL: {row[4]}")
    ev_preview = (row[5] or "")[:80].replace("\n", " ")
    print(f"    Evidence: {ev_preview}")

conn.close()
