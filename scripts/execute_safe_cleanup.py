"""
Safe Cleanup Script for Harvey (Dry-Run and Execution)
"""
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path("C:/Users/aneka/Desktop/harvey").resolve()

# Strict whitelisted cleanup targets (disposable build / cache artifacts only)
TARGET_PATTERNS = {
    "root_orphan_db": ROOT / "harvey.db",
    "pytest_cache": ROOT / ".pytest_cache",
    "dist": ROOT / "dist",
    "egg_info": ROOT / "harvey.egg-info",
    "req_bak": ROOT / "requirements.txt.bak",
}

def collect_pycache_dirs():
    pycaches = []
    for root, dirs, files in os.walk(ROOT):
        # Never touch .venv or data
        if ".venv" in Path(root).parts or "data" in Path(root).parts:
            continue
        if "__pycache__" in dirs:
            pycaches.append(Path(root) / "__pycache__")
    return pycaches

def plan_cleanup(dry_run=True):
    print("=" * 70)
    print(f"HARVEY SAFE CLEANUP AUDIT {'[DRY RUN]' if dry_run else '[EXECUTING]'}")
    print(f"Target Root: {ROOT}")
    print("=" * 70)

    deleted_items = []
    total_bytes = 0

    # 1. Check Root orphan harvey.db
    p = TARGET_PATTERNS["root_orphan_db"]
    if p.exists() and p.is_file():
        sz = p.stat().st_size
        if sz == 0:
            deleted_items.append({
                "path": str(p),
                "type": "file",
                "size_bytes": sz,
                "category": "Orphan 0-byte file (real DB is in data/harvey.db)",
            })
            total_bytes += sz
            if not dry_run:
                p.unlink()

    # 2. Check .pytest_cache
    p = TARGET_PATTERNS["pytest_cache"]
    if p.exists() and p.is_dir():
        for r, ds, fs in os.walk(p):
            for f in fs:
                fp = Path(r) / f
                try:
                    sz = fp.stat().st_size
                    deleted_items.append({
                        "path": str(fp),
                        "type": "file",
                        "size_bytes": sz,
                        "category": "Pytest execution cache (.pytest_cache)",
                    })
                    total_bytes += sz
                except Exception:
                    pass
        if not dry_run:
            shutil.rmtree(p, ignore_errors=True)

    # 3. Check dist/
    p = TARGET_PATTERNS["dist"]
    if p.exists() and p.is_dir():
        for r, ds, fs in os.walk(p):
            for f in fs:
                fp = Path(r) / f
                try:
                    sz = fp.stat().st_size
                    deleted_items.append({
                        "path": str(fp),
                        "type": "file",
                        "size_bytes": sz,
                        "category": "Temporary build distribution artifact (dist/)",
                    })
                    total_bytes += sz
                except Exception:
                    pass
        if not dry_run:
            shutil.rmtree(p, ignore_errors=True)

    # 4. Check harvey.egg-info
    p = TARGET_PATTERNS["egg_info"]
    if p.exists() and p.is_dir():
        for r, ds, fs in os.walk(p):
            for f in fs:
                fp = Path(r) / f
                try:
                    sz = fp.stat().st_size
                    deleted_items.append({
                        "path": str(fp),
                        "type": "file",
                        "size_bytes": sz,
                        "category": "Temporary setuptools metadata (harvey.egg-info)",
                    })
                    total_bytes += sz
                except Exception:
                    pass
        if not dry_run:
            shutil.rmtree(p, ignore_errors=True)

    # 4b. Check requirements.txt.bak
    p = TARGET_PATTERNS["req_bak"]
    if p.exists() and p.is_file():
        sz = p.stat().st_size
        deleted_items.append({
            "path": str(p),
            "type": "file",
            "size_bytes": sz,
            "category": "Obsolete backup file (requirements.txt.bak)",
        })
        total_bytes += sz
        if not dry_run:
            p.unlink()

    # 5. Check __pycache__ folders (outside .venv and data)
    pycaches = collect_pycache_dirs()
    for pc in pycaches:
        for r, ds, fs in os.walk(pc):
            for f in fs:
                fp = Path(r) / f
                try:
                    sz = fp.stat().st_size
                    deleted_items.append({
                        "path": str(fp),
                        "type": "file",
                        "size_bytes": sz,
                        "category": "Compiled Python bytecode (__pycache__)",
                    })
                    total_bytes += sz
                except Exception:
                    pass
        if not dry_run:
            shutil.rmtree(pc, ignore_errors=True)

    print(f"\nSummary of confirmed disposable items:")
    print(f"  Total Files: {len(deleted_items)}")
    print(f"  Total Space to Recover: {total_bytes / (1024 * 1024):.2f} MB ({total_bytes} bytes)")

    by_cat = {}
    for item in deleted_items:
        c = item["category"]
        if c not in by_cat:
            by_cat[c] = {"count": 0, "bytes": 0}
        by_cat[c]["count"] += 1
        by_cat[c]["bytes"] += item["size_bytes"]

    print("\nBreakdown by Category:")
    for cat, stat in sorted(by_cat.items()):
        print(f"  - {cat:<60}: {stat['count']} files ({stat['bytes'] / (1024*1024):.2f} MB)")

    if not dry_run:
        # Save log
        log_path = ROOT / "reports" / "deleted_files_log.json"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump({
                "timestamp": "2026-11-17T15:00:00Z",
                "total_files_deleted": len(deleted_items),
                "total_bytes_recovered": total_bytes,
                "total_mb_recovered": round(total_bytes / (1024 * 1024), 2),
                "breakdown_by_category": by_cat,
                "deleted_items": deleted_items
            }, f, indent=2)
        print(f"\nDetailed deletion audit log saved to: {log_path}")

    return deleted_items, total_bytes

if __name__ == "__main__":
    is_dry_run = "--execute" not in sys.argv
    plan_cleanup(dry_run=is_dry_run)
