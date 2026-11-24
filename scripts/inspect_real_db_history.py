import sqlite3
import os
import datetime
import hashlib
from pathlib import Path

def hash_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().upper()

def inspect_db(path):
    print(f"=== {path} ===")
    stat = os.stat(path)
    print("SHA256:", hash_file(path))
    print("Size:", stat.st_size)
    print("Mtime (UTC):", datetime.datetime.fromtimestamp(stat.st_mtime, tz=datetime.timezone.utc).isoformat())
    print("Ctime (UTC):", datetime.datetime.fromtimestamp(stat.st_ctime, tz=datetime.timezone.utc).isoformat())
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    cur = con.cursor()
    cur.execute("PRAGMA user_version")
    print("user_version:", cur.fetchone()[0])
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r[0] for r in cur.fetchall()]
    print(f"Tables ({len(tables)}):", tables)
    con.close()
    return tables

t1 = inspect_db('data/backups/harvey_backup_20261122_200000.db')
t2 = inspect_db('data/backup_harvey_20261123_190942.db')
t3 = inspect_db('data/harvey.db')

print("\n--- Diff between Backup Nov 22 and Backup Nov 23 ---")
print("New tables in Nov 23 backup:", sorted(list(set(t2) - set(t1))))

print("\n--- Inspect row in provider_logs: plog_9aa08ffbe3ed ---")
con = sqlite3.connect("file:data/harvey.db?mode=ro", uri=True)
cur = con.cursor()
cur.execute("SELECT * FROM provider_logs WHERE id='plog_9aa08ffbe3ed'")
row = cur.fetchone()
cols = [d[0] for d in cur.description]
print("Row plog_9aa08ffbe3ed:")
for c, v in zip(cols, row):
    print(f"  {c}: {v}")
con.close()
