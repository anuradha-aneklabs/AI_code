"""
Independent Verification of Dashboard Endpoints and Health Status
"""
import json
import os
import pathlib
import sys
import httpx

BASE_URL = "http://127.0.0.1:5555"

def verify_all_endpoints():
    token_file = pathlib.Path("data/dashboard-token")
    if not token_file.exists():
        print("ERROR: data/dashboard-token not found!")
        return False

    token = token_file.read_text().strip()
    headers = {"Authorization": f"Bearer {token}"}

    print("=" * 70)
    print("HARVEY DASHBOARD ENDPOINT & HEALTH INDEPENDENT AUDIT")
    print(f"Target URL: {BASE_URL}")
    print(f"Auth Token Present: Yes ({len(token)} chars, masked)")
    print("=" * 70)

    # 1. Unauthenticated test (verify that security gate works)
    try:
        unauth_resp = httpx.get(f"{BASE_URL}/api/health", timeout=5.0)
        print(f"\n[UNAUTHENTICATED GATE] GET /api/health -> HTTP {unauth_resp.status_code}")
        print(f"  Response Body: {unauth_resp.json()}")
        assert unauth_resp.status_code in (401, 403), f"Expected 401/403, got {unauth_resp.status_code}"
        print("  [CONFIRMED] Security invariant holds: Unauthenticated requests are rejected.")
    except Exception as e:
        print(f"  [FAIL] Unauthenticated check error: {e}")
        return False

    # 2. Static root test
    try:
        root_resp = httpx.get(f"{BASE_URL}/", timeout=5.0)
        print(f"\n[STATIC ASSET] GET / -> HTTP {root_resp.status_code} ({len(root_resp.content)} bytes)")
        assert root_resp.status_code == 200
        assert "<!doctype html>" in root_resp.text.lower() or "<html" in root_resp.text.lower()
        print("  [CONFIRMED] Root dashboard HTML delivered successfully.")
    except Exception as e:
        print(f"  [FAIL] Root HTML check error: {e}")
        return False

    # 3. Authenticated API Endpoints
    endpoints = [
        ("GET", "/api/health"),
        ("GET", "/api/summary"),
        ("GET", "/api/setup-status"),
        ("GET", "/api/settings"),
        ("GET", "/api/db/verify"),
        ("GET", "/api/companies"),
        ("GET", "/api/harvey/status"),
        ("GET", "/api/stats"),
        ("GET", "/api/usage"),
        ("GET", "/api/prospects"),
        ("GET", "/api/outbox"),
        ("GET", "/api/sending-mode"),
    ]

    print("\n[AUTHENTICATED API ENDPOINTS TEST]")
    results = []
    all_ok = True
    for method, ep in endpoints:
        try:
            r = httpx.request(method, f"{BASE_URL}{ep}", headers=headers, timeout=10.0)
            status = r.status_code
            try:
                data = r.json()
                if isinstance(data, dict):
                    keys = list(data.keys())[:6]
                    summary = f"dict with keys {keys}"
                elif isinstance(data, list):
                    summary = f"list with {len(data)} items"
                else:
                    summary = str(type(data))
            except Exception:
                summary = f"non-json ({len(r.content)} bytes)"

            print(f"  {method} {ep:<25} -> HTTP {status} | {summary}")
            if status != 200:
                all_ok = False
            results.append((ep, status, summary))
        except Exception as e:
            print(f"  {method} {ep:<25} -> ERROR: {e}")
            all_ok = False
            results.append((ep, "ERROR", str(e)))

    print("\n" + "=" * 70)
    print(f"DASHBOARD VERIFICATION SUMMARY: {'ALL 200 OK' if all_ok else 'SOME FAILED'}")
    print("=" * 70)
    return all_ok

if __name__ == "__main__":
    success = verify_all_endpoints()
    sys.exit(0 if success else 1)
