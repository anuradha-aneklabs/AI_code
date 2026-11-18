"""
Storage and File Inventory Scanner for Harvey
"""
import os
import sys
from pathlib import Path

ROOT = Path("C:/Users/aneka/Desktop/harvey")

def format_size(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.2f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"

def get_dir_size(path):
    total = 0
    file_count = 0
    for root, dirs, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            try:
                total += os.path.getsize(fp)
                file_count += 1
            except Exception:
                pass
    return total, file_count

def scan_project():
    print("=" * 70)
    print("HARVEY PROJECT STORAGE AND FILE INVENTORY SCAN")
    print(f"Target Root: {ROOT}")
    print("=" * 70)

    # 1. Top level directory breakdown
    print("\n--- TOP-LEVEL DIRECTORY SIZES ---")
    top_dirs = [d for d in ROOT.iterdir() if d.is_dir()]
    dir_stats = []
    for d in sorted(top_dirs):
        size, count = get_dir_size(d)
        dir_stats.append((d.name, size, count))
        print(f"  {d.name:<25}: {format_size(size):<12} ({count} files)")

    # 2. Pycache directories
    pycache_dirs = []
    total_pycache_size = 0
    total_pycache_files = 0
    for root, dirs, files in os.walk(ROOT):
        if "__pycache__" in dirs:
            p = Path(root) / "__pycache__"
            # Exclude .venv
            if ".venv" not in p.parts:
                sz, cnt = get_dir_size(p)
                pycache_dirs.append((p, sz, cnt))
                total_pycache_size += sz
                total_pycache_files += cnt

    print(f"\n--- PYCACHE FOLDERS (Non-venv) ---")
    print(f"  Total __pycache__ folders: {len(pycache_dirs)}")
    print(f"  Total __pycache__ size: {format_size(total_pycache_size)} ({total_pycache_files} files)")

    # 3. .pytest_cache
    pytest_cache = ROOT / ".pytest_cache"
    if pytest_cache.exists():
        pt_sz, pt_cnt = get_dir_size(pytest_cache)
        print(f"\n--- .pytest_cache ---")
        print(f"  Size: {format_size(pt_sz)} ({pt_cnt} files)")
    else:
        pt_sz = 0

    # 4. Build artifacts (dist/, *.egg-info/)
    build_artifacts = []
    dist = ROOT / "dist"
    if dist.exists():
        d_sz, d_cnt = get_dir_size(dist)
        build_artifacts.append(("dist/", d_sz, d_cnt))
    for egg in ROOT.glob("*.egg-info"):
        e_sz, e_cnt = get_dir_size(egg)
        build_artifacts.append((egg.name, e_sz, e_cnt))

    print(f"\n--- BUILD ARTIFACTS ---")
    for name, sz, cnt in build_artifacts:
        print(f"  {name:<25}: {format_size(sz)} ({cnt} files)")

    # 5. Scratch, screenshots, client_demo_output
    special_dirs = ["scratch", "screenshots", "client_demo_output"]
    print(f"\n--- SPECIAL TEMPORARY / OUTPUT DIRECTORIES ---")
    for sd in special_dirs:
        p = ROOT / sd
        if p.exists():
            s_sz, s_cnt = get_dir_size(p)
            print(f"  {sd:<25}: {format_size(s_sz)} ({s_cnt} files)")
            # List files inside
            for item in p.iterdir():
                if item.is_file():
                    print(f"    - {item.name} ({format_size(item.stat().st_size)})")
                elif item.is_dir():
                    sub_sz, sub_cnt = get_dir_size(item)
                    print(f"    - [DIR] {item.name}/ ({format_size(sub_sz)}, {sub_cnt} files)")

    # 6. Check root harvey.db
    root_db = ROOT / "harvey.db"
    if root_db.exists():
        print(f"\n--- ROOT harvey.db ---")
        print(f"  Exists: True, Size: {root_db.stat().st_size} bytes")

    # 7. Check reports directory
    reports_dir = ROOT / "reports"
    if reports_dir.exists():
        r_sz, r_cnt = get_dir_size(reports_dir)
        print(f"\n--- REPORTS DIRECTORY ---")
        print(f"  Size: {format_size(r_sz)} ({r_cnt} files)")
        for item in sorted(reports_dir.iterdir()):
            if item.is_file():
                print(f"    - {item.name} ({format_size(item.stat().st_size)})")

    # 8. Check data directory
    data_dir = ROOT / "data"
    if data_dir.exists():
        dt_sz, dt_cnt = get_dir_size(data_dir)
        print(f"\n--- DATA DIRECTORY (STRICTLY PRESERVE) ---")
        print(f"  Size: {format_size(dt_sz)} ({dt_cnt} files)")
        for item in sorted(data_dir.iterdir()):
            if item.is_file():
                print(f"    - {item.name} ({format_size(item.stat().st_size)})")
            elif item.is_dir():
                sub_sz, sub_cnt = get_dir_size(item)
                print(f"    - [DIR] {item.name}/ ({format_size(sub_sz)}, {sub_cnt} files)")

if __name__ == "__main__":
    scan_project()
