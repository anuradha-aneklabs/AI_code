"""Perform migration dry-run v31->v32 on copy DB data/validation/harvey_deploy_dryrun.db."""

import asyncio
import shutil
import sqlite3
from pathlib import Path
from harvey.state import StateManager

src = Path("data/harvey.db")
dst = Path("data/validation/harvey_deploy_dryrun.db")
dst.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(src, dst)

conn_before = sqlite3.connect(dst)
v_before = conn_before.execute("PRAGMA user_version").fetchone()[0]
tables_before = conn_before.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
conn_before.close()

sm = StateManager(db_path=str(dst))
asyncio.run(sm.init_db())

conn_after = sqlite3.connect(dst)
v_after = conn_after.execute("PRAGMA user_version").fetchone()[0]
tables_after = conn_after.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
table_counts = {}
for t in ["companies", "prospects", "outbox", "import_batches", "import_domains", "company_websites", "runs", "observations", "actions"]:
    try:
        cnt = conn_after.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        table_counts[t] = cnt
    except Exception as e:
        table_counts[t] = str(e)
conn_after.close()

print(f"MIGRATION DRY-RUN RESULT:")
print(f"user_version before: v{v_before}")
print(f"user_version after:  v{v_after}")
print(f"Tables before: {len(tables_before)}")
print(f"Tables after:  {len(tables_after)}")
print("Key table row counts:")
for k, v in table_counts.items():
    print(f"  {k}: {v}")
