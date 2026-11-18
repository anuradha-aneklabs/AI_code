"""Harvey AI Sales Agent — Live Stability & Tunnel Verification Script
Tests complete lifecycle:
1. Starts supervisor
2. Verifies local HTTP 200
3. Verifies engine is RUNNING
4. Verifies Cloudflare Quick Tunnel & public HTTPS 200
5. Restarts ONLY the dashboard process
6. Verifies Cloudflare tunnel remains connected with same PID & URL
7. Verifies public access recovers after dashboard restart
8. Tests repeated health checks
9. Verifies email sending remains paused
"""

from __future__ import annotations

import json
import os
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

PROJECT_DIR = Path(__file__).resolve().parent.parent
PYTHON_EXE = PROJECT_DIR / ".venv" / "Scripts" / "python.exe"
DATA_DIR = PROJECT_DIR / "data"
LOG_FILE = DATA_DIR / "demo_supervisor.log"
STATE_FILE = DATA_DIR / "demo_supervisor_state.json"
PID_FILE = DATA_DIR / "demo_supervisor.pid"
URL_FILE = DATA_DIR / "demo_public_url.txt"
TOKEN_FILE = DATA_DIR / "dashboard-token"

# High-reliability DNS resolution via 1.1.1.1 fallback
_orig_getaddrinfo = socket.getaddrinfo

def resolve_hostname_ips(hostname: str) -> list[str]:
    try:
        cmd = f"Resolve-DnsName -Name '{hostname}' -Server 1.1.1.1 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty IPAddress"
        p = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=5)
        return [line.strip() for line in p.stdout.strip().splitlines() if line.strip()]
    except Exception:
        return []

def _fallback_getaddrinfo(host, port, *args, **kwargs):
    try:
        return _orig_getaddrinfo(host, port, *args, **kwargs)
    except socket.gaierror:
        if "trycloudflare.com" in str(host) or "tailscale" in str(host):
            ips = resolve_hostname_ips(str(host))
            for ip in ips:
                try:
                    return _orig_getaddrinfo(ip, port, *args, **kwargs)
                except Exception:
                    continue
        raise

socket.getaddrinfo = _fallback_getaddrinfo


def get_token() -> str:
    return TOKEN_FILE.read_text("ascii").strip()


def check_url(url: str, token: str, timeout: float = 10.0) -> tuple[int, int, dict]:
    req = Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("User-Agent", "HarveyLiveStabilityVerifier/1.0")
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl._create_unverified_context()
    start = time.monotonic()
    try:
        with urlopen(req, timeout=timeout, context=ctx) as resp:
            ms = int((time.monotonic() - start) * 1000)
            data = json.loads(resp.read().decode("utf-8"))
            return resp.status, ms, data
    except HTTPError as e:
        ms = int((time.monotonic() - start) * 1000)
        try:
            data = json.loads(e.read().decode("utf-8"))
        except Exception:
            data = {"error": str(e)}
        return e.code, ms, data
    except Exception as e:
        ms = int((time.monotonic() - start) * 1000)
        return 0, ms, {"error": str(e)}


def main():
    print("=" * 70)
    print("HARVEY LIVE STABILITY & CLOUDFLARE QUICK TUNNEL VERIFICATION")
    print("=" * 70)

    token = get_token()

    # Step 1: Clean stop any old processes first
    print("\n[STEP 1] Cleaning up any lingering processes...")
    subprocess.run([str(PYTHON_EXE), "scripts/stop_windows_demo_supervisor.py"], cwd=str(PROJECT_DIR), capture_output=True)
    if URL_FILE.is_file():
        try:
            URL_FILE.unlink()
        except Exception:
            pass
    time.sleep(2)

    # Step 2: Start the Harvey supervisor
    print("\n[STEP 2] Launching Harvey Supervisor...")
    sup_proc = subprocess.Popen(
        [str(PYTHON_EXE), "scripts/run_windows_demo_supervisor.py"],
        cwd=str(PROJECT_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    print(f"  Supervisor started (PID: {sup_proc.pid})")

    # Step 3: Wait for local dashboard HTTP 200
    print("\n[STEP 3] Waiting for local dashboard health on http://127.0.0.1:5555/api/health...")
    local_ok = False
    start_wait = time.monotonic()
    while time.monotonic() - start_wait < 35:
        status, ms, data = check_url("http://127.0.0.1:5555/api/health", token, timeout=3.0)
        if status == 200 and data.get("status") == "ok":
            print(f"  [PASS] Local dashboard HTTP 200 ({ms}ms) | Response: {data}")
            local_ok = True
            break
        time.sleep(1.0)
    assert local_ok, "Local dashboard failed to become healthy within 35s"

    # Step 4: Verify engine is RUNNING
    print("\n[STEP 4] Verifying Harvey Engine is RUNNING...")
    eng_ok = False
    eng_pid = None
    start_wait = time.monotonic()
    while time.monotonic() - start_wait < 20:
        status, ms, data = check_url("http://127.0.0.1:5555/api/harvey/status", token, timeout=3.0)
        if status == 200 and data.get("running"):
            eng_pid = data.get("pid")
            print(f"  [PASS] Engine status RUNNING (PID: {eng_pid}) | Response: {data}")
            eng_ok = True
            break
        time.sleep(1.0)
    assert eng_ok, "Harvey engine failed to report RUNNING"

    # Step 5: Wait for Cloudflare Quick Tunnel public URL
    print("\n[STEP 5] Waiting for Cloudflare Quick Tunnel to assign public URL...")
    public_url = None
    start_wait = time.monotonic()
    while time.monotonic() - start_wait < 40:
        if URL_FILE.is_file():
            content = URL_FILE.read_text("utf-8").strip()
            if content.startswith("https://") and "trycloudflare.com" in content:
                public_url = content
                print(f"  [PASS] Cloudflare Quick Tunnel URL: {public_url}")
                break
        time.sleep(1.0)
    assert public_url, "Cloudflare Quick Tunnel failed to produce a public URL"

    # Step 6: Verify public URL responds successfully
    print(f"\n[STEP 6] Testing public URL: {public_url}/api/health...")
    pub_ok = False
    start_wait = time.monotonic()
    while time.monotonic() - start_wait < 50:
        status, ms, data = check_url(f"{public_url}/api/health", token, timeout=8.0)
        if status == 200 and data.get("status") == "ok":
            print(f"  [PASS] Public Cloudflare URL responded HTTP 200 ({ms}ms) | Response: {data}")
            pub_ok = True
            break
        print(f"  Waiting for Cloudflare edge routing... (status={status}, ms={ms}, data={data})")
        time.sleep(2.0)
    assert pub_ok, f"Public URL {public_url} failed to return HTTP 200"

    # Step 7: Inspect state file to get dashboard and tunnel PIDs
    assert STATE_FILE.is_file(), "State file missing"
    state_before = json.loads(STATE_FILE.read_text("utf-8"))
    dash_pid_before = state_before.get("dashboard_pid")
    tunnel_pid_before = state_before.get("tunnel_pid")
    print(f"\nState before dashboard restart: Dashboard PID={dash_pid_before}, Tunnel PID={tunnel_pid_before}")
    assert dash_pid_before, "Dashboard PID not found in state"
    assert tunnel_pid_before, "Tunnel PID not found in state"

    # Step 8: Restart ONLY the dashboard process
    print(f"\n[STEP 8] Simulating dashboard crash/restart: killing ONLY dashboard PID {dash_pid_before}...")
    subprocess.run(["taskkill", "/F", "/PID", str(dash_pid_before)], capture_output=True)

    # Immediately verify Cloudflare tunnel is STILL ALIVE
    from harvey.pidfile import pid_alive
    tunnel_still_alive = pid_alive(tunnel_pid_before)
    print(f"  Verifying Cloudflare Tunnel (PID {tunnel_pid_before}) after dashboard termination: {'STILL RUNNING' if tunnel_still_alive else 'TERMINATED'}")
    assert tunnel_still_alive, "Cloudflare tunnel was erroneously terminated when dashboard was killed!"

    # Step 9: Verify dashboard auto-recovers and public URL still works
    print("\n[STEP 9] Verifying supervisor auto-recovers dashboard and public URL resumes...")
    recovered_local = False
    start_wait = time.monotonic()
    while time.monotonic() - start_wait < 35:
        status, ms, data = check_url("http://127.0.0.1:5555/api/health", token, timeout=3.0)
        if status == 200 and data.get("status") == "ok":
            print(f"  [PASS] Local dashboard recovered HTTP 200 ({ms}ms)")
            recovered_local = True
            break
        time.sleep(1.0)
    assert recovered_local, "Local dashboard failed to recover after restart"

    # Verify public access recovers on the SAME tunnel URL
    print(f"\n[STEP 10] Testing public access on existing tunnel URL {public_url}/api/health...")
    recovered_public = False
    start_wait = time.monotonic()
    while time.monotonic() - start_wait < 40:
        status, ms, data = check_url(f"{public_url}/api/health", token, timeout=8.0)
        if status == 200 and data.get("status") == "ok":
            print(f"  [PASS] Public access RECOVERED on same Cloudflare URL ({ms}ms) | Response: {data}")
            recovered_public = True
            break
        time.sleep(2.0)
    assert recovered_public, "Public access failed to recover through Cloudflare tunnel"

    # Step 11: Verify repeated health checks
    print("\n[STEP 11] Running 5 repeated health checks...")
    for i in range(1, 6):
        loc_s, loc_ms, _ = check_url("http://127.0.0.1:5555/api/health", token, timeout=3.0)
        pub_s, pub_ms, _ = check_url(f"{public_url}/api/health", token, timeout=6.0)
        print(f"  Check #{i}: Local HTTP {loc_s} ({loc_ms}ms) | Public HTTP {pub_s} ({pub_ms}ms)")
        assert loc_s == 200, f"Local check {i} failed"
        assert pub_s == 200, f"Public check {i} failed"
        time.sleep(1.0)

    # Step 12: Verify email sending remains paused
    print("\n[STEP 12] Verifying email safeguards...")
    summary_s, _, summary_data = check_url("http://127.0.0.1:5555/api/summary", token, timeout=4.0)
    assert summary_s == 200, "Summary endpoint failed"
    sending_paused = summary_data.get("sending_paused")
    print(f"  sending_paused in summary: {sending_paused}")
    assert sending_paused is True, "sending_paused must remain True!"

    outbox_s, _, outbox_data = check_url("http://127.0.0.1:5555/api/outbox", token, timeout=4.0)
    assert outbox_s == 200, "Outbox endpoint failed"
    print(f"  Outbox paused banner: {outbox_data.get('paused')}")
    assert "Pilot Evaluation" in str(outbox_data.get("paused")), "Outbox safety hold must be active"

    print("\n" + "=" * 70)
    print("ALL 12 STABILITY & RECOVERY TESTS PASSED (100%)")
    print("=" * 70)


if __name__ == "__main__":
    main()
