"""Stop script for Harvey Windows Demo Supervisor.
Safely stops the supervisor, dashboard, and Cloudflare tunnel, and restores normal power settings.
"""

from __future__ import annotations

import ctypes
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

ES_CONTINUOUS: int = 0x80000000


def log_shutdown_event(log_file: Path, message: str):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [SHUTDOWN] {message}"
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def kill_pid(pid: int, name: str = "Process"):
    if not pid or pid <= 0 or pid == os.getpid():
        return
    try:
        print(f"Terminating {name} (PID: {pid})...")
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=5)
    except Exception as e:
        print(f"Could not kill {name} ({pid}): {e}")


def cleanup_port(port: int = 5555):
    try:
        cmd = f'Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique'
        p = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=5)
        pids = [int(line.strip()) for line in p.stdout.strip().splitlines() if line.strip().isdigit()]
        for pid in pids:
            if pid > 0 and pid != os.getpid():
                print(f"Releasing port {port} from PID {pid}...")
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=5)
    except Exception:
        pass


def cleanup_supervisors():
    try:
        my_pid = os.getpid()
        my_ppid = getattr(os, "getppid", lambda: None)()
        exempt_pids = {my_pid}
        if my_ppid:
            exempt_pids.add(my_ppid)
        cmd = 'Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*run_windows_demo_supervisor.py*" } | Select-Object -ExpandProperty ProcessId'
        p = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=5)
        pids = [int(line.strip()) for line in p.stdout.strip().splitlines() if line.strip().isdigit()]
        for pid in pids:
            if pid > 0 and pid not in exempt_pids:
                print(f"Terminating supervisor process {pid}...")
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=5)
    except Exception:
        pass


def cleanup_engine(project_dir: Path):
    harvey_pid_file = project_dir / "data" / "harvey.pid"
    if harvey_pid_file.is_file():
        try:
            pid = int(harvey_pid_file.read_text("utf-8").strip())
            if pid > 0:
                kill_pid(pid, "Harvey Engine")
        except Exception:
            pass
        try:
            harvey_pid_file.unlink()
        except Exception:
            pass
    try:
        cmd = 'Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*-m harvey run*" } | Select-Object -ExpandProperty ProcessId'
        p = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=5)
        pids = [int(line.strip()) for line in p.stdout.strip().splitlines() if line.strip().isdigit()]
        for pid in pids:
            if pid > 0 and pid != os.getpid():
                print(f"Terminating orphaned engine process {pid}...")
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=5)
    except Exception:
        pass


def cleanup_cloudflared(port: int = 5555):
    try:
        cmd = f'Get-CimInstance Win32_Process -Filter "Name = \'cloudflared.exe\'" | Where-Object {{ $_.CommandLine -like "*{port}*" }} | Select-Object -ExpandProperty ProcessId'
        p = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=5)
        pids = [int(line.strip()) for line in p.stdout.strip().splitlines() if line.strip().isdigit()]
        for pid in pids:
            if pid > 0 and pid != os.getpid():
                print(f"Terminating cloudflared process {pid}...")
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=5)
    except Exception:
        pass


def restore_windows_sleep():
    if os.name == "nt":
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            print("Normal Windows sleep and power settings restored (ES_CONTINUOUS).")
        except Exception as e:
            print(f"Warning: Could not set execution state: {e}")


def main():
    project_dir = Path(__file__).resolve().parent.parent
    data_dir = project_dir / "data"
    log_file = data_dir / "demo_supervisor.log"
    pid_file = data_dir / "demo_supervisor.pid"
    state_file = data_dir / "demo_supervisor_state.json"

    print("=" * 60)
    print("Stopping Harvey AI Sales Agent Public Demo...")
    print("=" * 60)

    # 1. Read state file if available
    state = {}
    if state_file.is_file():
        try:
            state = json.loads(state_file.read_text("utf-8"))
        except Exception:
            pass

    # Read PID file
    sup_pid = None
    if pid_file.is_file():
        try:
            sup_pid = int(pid_file.read_text("utf-8").strip())
        except Exception:
            pass

    if not sup_pid:
        sup_pid = state.get("supervisor_pid")

    dash_pid = state.get("dashboard_pid")
    tun_pid = state.get("tunnel_pid")
    port = state.get("port", 5555)

    # 2. Kill supervisor process first so it doesn't auto-restart children
    if sup_pid:
        kill_pid(sup_pid, "Supervisor")
    cleanup_supervisors()

    if tun_pid:
        kill_pid(tun_pid, "Cloudflare Tunnel")
    if dash_pid:
        kill_pid(dash_pid, "Harvey Dashboard")

    # 3. Clean up engine process
    engine_pid = state.get("engine_pid")
    if engine_pid:
        kill_pid(engine_pid, "Harvey Engine")
    cleanup_engine(project_dir)

    # 4. Clean up any remaining processes on port or orphaned cloudflared
    cleanup_port(port)
    cleanup_cloudflared(port)

    # 4. Restore normal power settings
    restore_windows_sleep()

    # 5. Clean up PID file & mark state stopped
    try:
        if pid_file.is_file():
            pid_file.unlink()
    except Exception:
        pass

    state["status"] = "stopped"
    state["stopped_at"] = datetime.datetime.now().isoformat()
    try:
        state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception:
        pass

    log_shutdown_event(log_file, "STOP_HARVEY_DEMO script executed: All demo processes terminated and power restored.")

    print("=" * 60)
    print("HARVEY PUBLIC DEMO HAS BEEN STOPPED CLEANLY.")
    print(f"- Dashboard port {port} released")
    print("- Cloudflare Quick Tunnel terminated")
    print("- Normal Windows sleep and power settings restored")
    print("=" * 60)


if __name__ == "__main__":
    main()
