"""Comprehensive Database Comparison Script for Task 1.

Compares:
1. data/backups/harvey_backup_20261122_200000.db (Hash 2D3D9703...)
2. data/backup_harvey_20261123_190942.db (Hash 012B390C...)
3. data/harvey.db (Current real DB)

Inspects row counts for every table, settings, outbox, outreach_drafts, schema version.
"""

import sqlite3
import hashlib
from pathlib import Path


def get_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().upper()


def inspect_db(db_path: str):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = conn.cursor()

    # Schema version
    cur.execute("PRAGMA user_version")
    user_version = cur.fetchone()[0]

    # Tables and row counts
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r[0] for r in cur.fetchall()]

    counts = {}
    for t in tables:
        try:
            cur.execute(f"SELECT count(*) FROM [{t}]")
            counts[t] = cur.fetchone()[0]
        except Exception as e:
            counts[t] = f"ERROR: {e}"

    # Settings table content
    settings = {}
    if "settings" in tables:
        try:
            cur.execute("SELECT key, value FROM settings ORDER BY key")
            settings = dict(cur.fetchall())
        except Exception as e:
            settings = {"ERROR": str(e)}

    # Outbox status counts
    outbox_status = {}
    if "outbox" in tables:
        try:
            cur.execute("SELECT status, count(*) FROM outbox GROUP BY status ORDER BY status")
            outbox_status = dict(cur.fetchall())
        except Exception as e:
            outbox_status = {"ERROR": str(e)}

    # Outreach drafts status counts
    drafts_status = {}
    if "outreach_drafts" in tables:
        try:
            cur.execute("SELECT status, count(*) FROM outreach_drafts GROUP BY status ORDER BY status")
            drafts_status = dict(cur.fetchall())
        except Exception as e:
            drafts_status = {"ERROR": str(e)}

    conn.close()
    return {
        "path": db_path,
        "sha256": get_sha256(db_path),
        "size": Path(db_path).stat().st_size,
        "user_version": user_version,
        "tables_count": len(tables),
        "counts": counts,
        "settings": settings,
        "outbox_status": outbox_status,
        "drafts_status": drafts_status,
    }


def main():
    db_2d3d = "data/backups/harvey_backup_20261122_200000.db"
    db_012b = "data/backup_harvey_20261123_190942.db"
    db_real = "data/harvey.db"

    info_2d3d = inspect_db(db_2d3d)
    info_012b = inspect_db(db_012b)
    info_real = inspect_db(db_real)

    print("=" * 80)
    print("TASK 1: DATABASE METRICS AND COMPARISON")
    print("=" * 80)
    for info in [info_2d3d, info_012b, info_real]:
        print(f"\nPath: {info['path']}")
        print(f"  SHA256:       {info['sha256']}")
        print(f"  Size:         {info['size']} bytes")
        print(f"  User Version: {info['user_version']}")
        print(f"  Tables:       {info['tables_count']}")
        print(f"  Outbox:       {info['outbox_status']}")
        print(f"  Drafts:       {info['drafts_status']}")
        print(f"  Settings:     {info['settings']}")

    # Compare 2D3D (Nov 22 backup) vs 012B (Nov 23 backup) vs Real
    print("\n" + "=" * 80)
    print("TABLE ROW COUNT COMPARISON:")
    print(f"{'Table':<25} | {'2D3D97 (Nov 22)':<15} | {'012B39 (Nov 23)':<15} | {'Real DB':<15} | {'Diff (Real - 2D3D)':<20}")
    print("-" * 95)
    all_tables = sorted(set(info_2d3d['counts'].keys()) | set(info_012b['counts'].keys()) | set(info_real['counts'].keys()))
    for t in all_tables:
        c1 = info_2d3d['counts'].get(t, "N/A")
        c2 = info_012b['counts'].get(t, "N/A")
        c3 = info_real['counts'].get(t, "N/A")
        diff_str = ""
        if isinstance(c1, int) and isinstance(c3, int):
            d = c3 - c1
            diff_str = f"{d:+d}" if d != 0 else "0 (identical)"
        print(f"{t:<25} | {str(c1):<15} | {str(c2):<15} | {str(c3):<15} | {diff_str:<20}")

    # Compare 012B vs Real DB binary differences
    print("\n" + "=" * 80)
    print("BINARY COMPARISON: data/backup_harvey_20261123_190942.db vs data/harvey.db")
    print("=" * 80)
    with open(db_012b, "rb") as f1, open(db_real, "rb") as f2:
        b1 = f1.read()
        b2 = f2.read()

    print(f"Length b1: {len(b1)}, Length b2: {len(b2)}")
    diff_bytes = []
    for i in range(min(len(b1), len(b2))):
        if b1[i] != b2[i]:
            diff_bytes.append((i, b1[i], b2[i]))

    print(f"Total differing bytes: {len(diff_bytes)} / {len(b1)} ({len(diff_bytes)/len(b1)*100:.4f}%)")
    if diff_bytes:
        print("First 20 differing byte offsets (offset, backup_val, real_val):")
        for offset, v1, v2 in diff_bytes[:20]:
            print(f"  Offset 0x{offset:06x} ({offset:8d}): 0x{v1:02x} vs 0x{v2:02x}")


if __name__ == "__main__":
    main()
