"""Harvey AI Sales Agent — Demo Supervisor End-to-End Test & Verification
Tests all 12 requirements:
- Startup order
- Dynamic URL extraction
- Sleep prevention (Win32 API)
- Health-checking
- Token sanitization in logs
- Auto-restart crash recovery (for both Cloudflare and Dashboard)
- Clean stop via stop script
"""

from __future__ import annotations

import datetime
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

PROJECT_DIR = Path(__file__).resolve().parent.parent
PYTHON_EXE = PROJECT_DIR / ".venv" / "Scripts" / "python.exe"
DATA_DIR = PROJECT_DIR / "data"
LOG_FILE = DATA_DIR / "demo_supervisor.log"
STATE_FILE = DATA_DIR / "demo_supervisor_state.json"
PID_FILE = DATA_DIR / "demo_supervisor.pid"
URL_FILE = DATA_DIR / "demo_public_url.txt"
TOKEN_FILE = DATA_DIR / "dashboard-token"


def test_sleep_prevention():
    print("\n--- Test 1: Win32 Sleep Prevention API ---")
    import ctypes
    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001
    ES_DISPLAY_REQUIRED = 0x00000002

    res1 = ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
    print(f"  Prevent sleep result: {hex(res1)}")
    assert res1 != 0, "SetThreadExecutionState failed to prevent sleep"

    res2 = ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
    print(f"  Restore sleep result: {hex(res2)}")
    assert res2 != 0, "SetThreadExecutionState failed to restore sleep"
    print("  [PASS] Sleep prevention Win32 API verified.")


def start_supervisor_process():
    print("\n--- Test 2: Launch Supervisor Subprocess ---")
    # First ensure any lingering processes are terminated
    subprocess.run([str(PYTHON_EXE), "scripts/stop_windows_demo_supervisor.py"], cwd=str(PROJECT_DIR), capture_output=True)
    time.sleep(2)

    proc = subprocess.Popen(
        [str(PYTHON_EXE), "scripts/run_windows_demo_supervisor.py"],
        cwd=str(PROJECT_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    print(f"  Supervisor process started with PID: {proc.pid}")
    return proc


def wait_for_supervisor_live(proc: subprocess.Popen, timeout: float = 45.0):
    print(f"\n--- Test 3: Wait for Live Status & Dynamic URL (Timeout: {timeout}s) ---")
    start = time.monotonic()
    url = None
    state = {}

    while time.monotonic() - start < timeout:
        if proc.poll() is not None:
            out, _ = proc.communicate(timeout=2)
            print(f"Supervisor died unexpectedly:\n{out}")
            raise RuntimeError("Supervisor died unexpectedly")

        if URL_FILE.is_file() and STATE_FILE.is_file():
            try:
                url = URL_FILE.read_text("utf-8").strip()
                state = json.loads(STATE_FILE.read_text("utf-8"))
                if url and state.get("status") == "running" and state.get("tunnel_pid"):
                    print(f"  Dynamic URL captured: {url}")
                    print(f"  State: {state}")
                    break
            except Exception:
                pass
        time.sleep(1.0)

    assert url, "Dynamic Cloudflare URL was not captured within timeout"
    assert "trycloudflare.com" in url, f"Unexpected URL format: {url}"
    print("  [PASS] Dynamic URL extraction and state verification passed.")
    return url, state


def wait_for_dns(hostname: str, timeout: float = 75.0) -> bool:
    print(f"  Waiting for DNS resolution of {hostname} (up to {timeout}s)...")
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        try:
            socket.getaddrinfo(hostname, 443)
            elapsed = time.monotonic() - start
            print(f"  DNS successfully resolved for {hostname} in {elapsed:.1f}s.")
            return True
        except Exception:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", f"Resolve-DnsName -Name '{hostname}' -Server 1.1.1.1 -ErrorAction SilentlyContinue | Out-Null"],
                capture_output=True,
            )
            time.sleep(2.0)
    return False


def verify_endpoints(url: str, token: str):
    print("\n--- Test 4: Verify Local & Public Endpoints ---")
    # 1. Local /api/health
    req_local = Request("http://127.0.0.1:5555/api/health")
    req_local.add_header("Authorization", f"Bearer {token}")
    with urlopen(req_local, timeout=5) as resp:
        data_local = json.loads(resp.read().decode())
        print(f"  Local /api/health: {resp.status} -> {data_local}")
        assert resp.status == 200
        assert data_local.get("status") == "ok"

    # 2. Local /api/summary (Email paused check)
    req_sum = Request("http://127.0.0.1:5555/api/summary")
    req_sum.add_header("Authorization", f"Bearer {token}")
    with urlopen(req_sum, timeout=5) as resp:
        data_sum = json.loads(resp.read().decode())
        print(f"  Local /api/summary: sending_paused = {data_sum.get('sending_paused')}")
        assert data_sum.get("sending_paused") is True, "sending_paused must be True!"

    # 3. Public /api/health via Cloudflare
    host = url.replace("https://", "").replace("http://", "").split("/")[0]
    dns_ok = wait_for_dns(host, timeout=75.0)
    assert dns_ok, f"DNS failed to resolve {host} within 75s"

    req_pub = Request(f"{url}/api/health")
    req_pub.add_header("Authorization", f"Bearer {token}")
    req_pub.add_header("User-Agent", "HarveyTest/1.0")
    ctx = ssl.create_default_context()
    with urlopen(req_pub, timeout=15, context=ctx) as resp:
        data_pub = json.loads(resp.read().decode())
        print(f"  Public /api/health: {resp.status} -> {data_pub}")
        assert resp.status == 200
        assert data_pub.get("status") == "ok"

    print("  [PASS] Both Local and Public endpoints verified 200 OK with email hold.")


def verify_log_security(token: str):
    print("\n--- Test 5: Log Sanitization & Security ---")
    assert LOG_FILE.is_file(), f"Log file {LOG_FILE} not found"
    log_content = LOG_FILE.read_text("utf-8")

    # Assert literal token does not appear anywhere in logs
    if token and len(token) > 10:
        assert token not in log_content, "SECURITY VIOLATION: Literal authentication token leaked into logs!"
        print("  [PASS] Authentication token is completely absent from demo_supervisor.log (Redacted).")

    # Check for required lifecycle markers
    assert "[STARTUP]" in log_content, "Missing [STARTUP] logs"
    print("  [PASS] Structured lifecycle logging verified.")


def test_crash_recovery_cloudflare(proc: subprocess.Popen, token: str):
    print("\n--- Test 6: Auto-Restart on Cloudflare Tunnel Crash ---")
    state = json.loads(STATE_FILE.read_text("utf-8"))
    old_tunnel_pid = state.get("tunnel_pid")
    old_url = URL_FILE.read_text("utf-8").strip()
    print(f"  Terminating Cloudflare tunnel (PID: {old_tunnel_pid})...")

    subprocess.run(["taskkill", "/F", "/PID", str(old_tunnel_pid)], capture_output=True)

    # Supervisor should detect crash within 5 seconds and restart
    print("  Waiting for supervisor to auto-restart Cloudflare and obtain new URL...")
    start = time.monotonic()
    new_url = None
    new_tunnel_pid = None

    while time.monotonic() - start < 35.0:
        try:
            curr_state = json.loads(STATE_FILE.read_text("utf-8"))
            curr_pid = curr_state.get("tunnel_pid")
            if curr_pid and curr_pid != old_tunnel_pid:
                new_tunnel_pid = curr_pid
                new_url = URL_FILE.read_text("utf-8").strip()
                if new_url:
                    break
        except Exception:
            pass
        time.sleep(1.0)

    assert new_tunnel_pid, "Supervisor failed to restart Cloudflare tunnel"
    print(f"  Cloudflare restarted successfully with new PID: {new_tunnel_pid}")
    print(f"  New dynamic URL: {new_url}")

    # Verify public health on new URL
    new_host = new_url.replace("https://", "").replace("http://", "").split("/")[0]
    dns_ok = wait_for_dns(new_host, timeout=75.0)
    assert dns_ok, f"DNS failed to resolve new tunnel host {new_host} within 75s"

    req_pub = Request(f"{new_url}/api/health")
    req_pub.add_header("Authorization", f"Bearer {token}")
    req_pub.add_header("User-Agent", "HarveyTest/1.0")
    ctx = ssl.create_default_context()
    with urlopen(req_pub, timeout=15, context=ctx) as resp:
        assert resp.status == 200
        print(f"  Public health on new URL verified: HTTP {resp.status}")

    print("  [PASS] Cloudflare crash auto-restart test passed.")
    return new_url


def test_crash_recovery_dashboard(token: str):
    print("\n--- Test 7: Auto-Restart on Harvey Dashboard Crash ---")
    state = json.loads(STATE_FILE.read_text("utf-8"))
    old_dash_pid = state.get("dashboard_pid")
    print(f"  Terminating Harvey Dashboard (PID: {old_dash_pid})...")

    subprocess.run(["taskkill", "/F", "/PID", str(old_dash_pid)], capture_output=True)

    print("  Waiting for supervisor to auto-restart Harvey Dashboard...")
    start = time.monotonic()
    new_dash_pid = None

    while time.monotonic() - start < 25.0:
        try:
            curr_state = json.loads(STATE_FILE.read_text("utf-8"))
            curr_pid = curr_state.get("dashboard_pid")
            if curr_pid and curr_pid != old_dash_pid:
                new_dash_pid = curr_pid
                break
        except Exception:
            pass
        time.sleep(1.0)

    assert new_dash_pid, "Supervisor failed to restart Harvey Dashboard"
    print(f"  Harvey Dashboard restarted successfully with new PID: {new_dash_pid}")

    # Verify local health
    req_local = Request("http://127.0.0.1:5555/api/health")
    req_local.add_header("Authorization", f"Bearer {token}")
    with urlopen(req_local, timeout=5) as resp:
        assert resp.status == 200
        print(f"  Local health verified after restart: HTTP {resp.status}")

    print("  [PASS] Harvey Dashboard crash auto-restart test passed.")


def test_clean_stop():
    print("\n--- Test 8: Clean Stop via stop_windows_demo_supervisor.py ---")
    res = subprocess.run([str(PYTHON_EXE), "scripts/stop_windows_demo_supervisor.py"], cwd=str(PROJECT_DIR), capture_output=True, text=True)
    print(res.stdout)
    assert res.returncode == 0, f"Stop script exited with code {res.returncode}"

    # Verify port 5555 is freed
    time.sleep(1)
    p = subprocess.run(
        ["powershell", "-NoProfile", "-Command", "Get-NetTCPConnection -LocalPort 5555 -State Listen -ErrorAction SilentlyContinue"],
        capture_output=True,
        text=True,
    )
    port_in_use = bool(p.stdout.strip())
    assert not port_in_use, f"Port 5555 is still listening after stop: {p.stdout}"
    print("  [PASS] Port 5555 freed cleanly.")

    # Verify PID file is gone
    assert not PID_FILE.is_file(), "PID file was not removed after stop"
    print("  [PASS] PID file cleanly removed.")


def main():
    print("======================================================================")
    print("  HARVEY AI SALES AGENT — DEMO SUPERVISOR AUTOMATED TEST SUITE")
    print("======================================================================")

    test_sleep_prevention()

    proc = start_supervisor_process()
    try:
        url, state = wait_for_supervisor_live(proc)
        token = TOKEN_FILE.read_text("ascii").strip()

        verify_endpoints(url, token)
        verify_log_security(token)
        new_url = test_crash_recovery_cloudflare(proc, token)
        test_crash_recovery_dashboard(token)

        # Allow one health-check interval to log
        time.sleep(5)
    finally:
        test_clean_stop()

    print("\n======================================================================")
    print("  ALL 8 SUPERVISOR TESTS COMPLETED SUCCESSFULLY (100% PASS)")
    print("======================================================================")


if __name__ == "__main__":
    main()
