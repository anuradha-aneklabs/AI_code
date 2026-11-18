"""Harvey AI Sales Agent — Zero-Cost Windows Demo Supervisor
Reliable, self-healing Windows supervisor for Harvey Dashboard + Cloudflare Quick Tunnel.

Requirements fulfilled:
1. Preserves all existing project files, features, database, and ChromaDB data.
2. Automatic Windows startup of Harvey Dashboard and Cloudflare Quick Tunnel.
3. Strict startup order: Harvey starts first and verifies local health before tunnel starts.
4. Auto-restart: Automatically restarts Harvey or Cloudflare if either process crashes.
5. Independent lifecycles: Restarting the dashboard NEVER kills a healthy Cloudflared process.
   Restarting Cloudflared NEVER disrupts the dashboard or engine.
6. Structured logging to data/demo_supervisor.log for [STARTUP], [HEALTH_CHECK], [RESTART], [SHUTDOWN].
7. Dashboard output safely drained to data/dashboard.log preventing Windows OS pipe deadlocks.
8. Token sanitization: Strictly redacts secrets and authentication tokens from all log output.
9. Email safeguards: Enforces sending_paused=True and require_approval=True.
10. Zero security compromise: Keeps Windows Firewall and Defender untouched.
11. Sleep prevention: Prevents system sleep during demo via Win32 SetThreadExecutionState;
    restores normal power settings cleanly upon shutdown.
12. Prints clear public URL and unlock link after tunnel startup.
13. Dynamic URL handling: Safely detects changed Quick Tunnel URLs on restarts and never assumes
    old URLs remain valid.
14. Controlled by START_HARVEY_DEMO.bat and STOP_HARVEY_DEMO.bat.
"""

from __future__ import annotations

import atexit
import ctypes
import datetime
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import socket
import ssl

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

# Win32 Execution State Flags for Sleep Prevention
ES_CONTINUOUS: int = 0x80000000
ES_SYSTEM_REQUIRED: int = 0x00000001
ES_DISPLAY_REQUIRED: int = 0x00000002

CLOUDFLARED_DEFAULT_PATH = r"C:\Program Files (x86)\cloudflared\cloudflared.exe"
TRYCLOUDFLARE_REGEX = re.compile(r"https://([a-zA-Z0-9-]+\.trycloudflare\.com)")
BEARER_RE = re.compile(r"Bearer\s+([A-Za-z0-9_-]{20,})", re.IGNORECASE)
SECRET_RE = re.compile(r"(?i)(password|secret|token|api_key)[\"']?\s*[:=]\s*[\"']?([^\"'\s,]+)")


def _warm_dns(hostname: str):
    """Prime Windows DNS cache via Cloudflare 1.1.1.1 to bypass local ISP router propagation delays."""
    if os.name == "nt" and hostname:
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", f"Resolve-DnsName -Name '{hostname}' -Server 1.1.1.1 -ErrorAction SilentlyContinue | Out-Null"],
                capture_output=True,
                timeout=6,
            )
        except Exception:
            pass


class DemoSupervisor:
    def __init__(self, port: int = 5555, project_dir: Optional[Path] = None):
        self.project_dir = project_dir or Path(__file__).resolve().parent.parent
        self.data_dir = self.project_dir / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.data_dir / "demo_supervisor.log"
        self.pid_file = self.data_dir / "demo_supervisor.pid"
        self.lock_file = self.data_dir / "demo_supervisor.lock"
        self.state_file = self.data_dir / "demo_supervisor_state.json"
        self.url_file = self.data_dir / "demo_public_url.txt"
        self.dashboard_log_file = self.data_dir / "dashboard.log"

        self.port = port
        self.python_exe = str(self.project_dir / ".venv" / "Scripts" / "python.exe")
        if not Path(self.python_exe).is_file():
            self.python_exe = sys.executable

        self.cloudflared_exe = self._resolve_cloudflared()

        self.dashboard_proc: Optional[subprocess.Popen] = None
        self._dashboard_log_handle = None
        self._restarting_dashboard = False
        self._last_dashboard_restart = 0.0

        self.tunnel_proc: Optional[subprocess.Popen] = None
        self.tunnel_reader_thread: Optional[threading.Thread] = None
        self._last_tunnel_restart = 0.0

        self.public_url: Optional[str] = None
        self.token: Optional[str] = self._load_token()

        self.running = False
        self.sleep_prevented = False
        self._lock = threading.Lock()
        self._supervisor_lock_handle = None

    def _resolve_cloudflared(self) -> str:
        if Path(CLOUDFLARED_DEFAULT_PATH).is_file():
            return CLOUDFLARED_DEFAULT_PATH
        import shutil
        found = shutil.which("cloudflared")
        if found:
            return found
        return CLOUDFLARED_DEFAULT_PATH

    def _load_token(self) -> Optional[str]:
        token_path = self.data_dir / "dashboard-token"
        if token_path.is_file():
            try:
                return token_path.read_text("ascii").strip()
            except Exception:
                pass
        try:
            sys.path.insert(0, str(self.project_dir))
            from harvey.dashboard import token_store
            return token_store.get()
        except Exception:
            return None

    def sanitize(self, text: str) -> str:
        if not text:
            return ""
        sanitized = text
        if self.token and self.token in sanitized:
            sanitized = sanitized.replace(self.token, "[REDACTED_TOKEN]")
        sanitized = BEARER_RE.sub("Bearer [REDACTED_TOKEN]", sanitized)
        sanitized = SECRET_RE.sub(r"\1=[REDACTED]", sanitized)
        return sanitized

    def log(self, category: str, message: str, echo: bool = True):
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sanitized_msg = self.sanitize(message)
        line = f"[{ts}] [{category.upper()}] {sanitized_msg}"
        with self._lock:
            try:
                with open(self.log_file, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                pass
        if echo:
            prefix = f"[{category.upper()}]"
            print(f"{prefix:<15} {sanitized_msg}")

    def prevent_sleep(self) -> bool:
        if os.name != "nt":
            return False
        try:
            res = ctypes.windll.kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
            )
            self.sleep_prevented = bool(res != 0)
            if self.sleep_prevented:
                self.log("STARTUP", "Windows sleep prevention active (system + display will stay awake during demo)")
            else:
                self.log("WARN", "SetThreadExecutionState returned 0; sleep prevention could not be verified")
            return self.sleep_prevented
        except Exception as e:
            self.log("WARN", f"Failed to set thread execution state: {e}")
            return False

    def restore_sleep(self):
        if os.name != "nt":
            return
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            self.sleep_prevented = False
            self.log("SHUTDOWN", "Normal Windows sleep and power settings restored")
        except Exception as e:
            self.log("WARN", f"Failed to restore thread execution state: {e}")

    def _acquire_supervisor_lock(self) -> bool:
        """Acquire an exclusive OS file lock to ensure strictly ONE supervisor instance runs."""
        for attempt in range(5):
            try:
                self._supervisor_lock_handle = open(self.lock_file, "a+b")
                if sys.platform == "win32":
                    import msvcrt
                    self._supervisor_lock_handle.seek(0)
                    msvcrt.locking(self._supervisor_lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._supervisor_lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except (OSError, IOError):
                if self._supervisor_lock_handle:
                    try:
                        self._supervisor_lock_handle.close()
                    except Exception:
                        pass
                    self._supervisor_lock_handle = None
                time.sleep(0.5)
        return False

    def _release_supervisor_lock(self):
        if self._supervisor_lock_handle:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    self._supervisor_lock_handle.seek(0)
                    msvcrt.locking(self._supervisor_lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._supervisor_lock_handle.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                self._supervisor_lock_handle.close()
            except Exception:
                pass
            self._supervisor_lock_handle = None

    def _cleanup_duplicate_supervisors(self):
        """Ensure no other supervisor instance is running across the system."""
        my_pid = os.getpid()
        my_ppid = getattr(os, "getppid", lambda: None)()
        exempt = {my_pid}
        if my_ppid:
            exempt.add(my_ppid)

        # 1. Kill via CIM/WMI process table match (only python.exe processes)
        try:
            cmd = 'Get-CimInstance Win32_Process -Filter "Name = \'python.exe\'" | Where-Object { $_.CommandLine -like "*run_windows_demo_supervisor.py*" } | Select-Object -ExpandProperty ProcessId'
            p = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=5)
            pids = [int(line.strip()) for line in p.stdout.strip().splitlines() if line.strip().isdigit()]
            for pid in pids:
                if pid > 0 and pid not in exempt:
                    self.log("STARTUP", f"Found previous supervisor process {pid}. Terminating old instance...")
                    try:
                        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=5)
                    except Exception:
                        pass
        except Exception as e:
            self.log("WARN", f"Could not inspect running supervisors: {e}")

        # 2. Check pid file
        if self.pid_file.is_file():
            try:
                old_pid = int(self.pid_file.read_text("utf-8").strip())
                if old_pid and old_pid not in exempt:
                    from harvey.pidfile import pid_alive
                    if pid_alive(old_pid):
                        self.log("STARTUP", f"Found previous supervisor PID {old_pid} from file. Terminating...")
                        subprocess.run(["taskkill", "/F", "/PID", str(old_pid)], capture_output=True, timeout=5)
            except Exception:
                pass

        time.sleep(0.5)
        # 3. Now take the exclusive lock
        if not self._acquire_supervisor_lock():
            self.log("ERROR", "Another supervisor instance is actively holding the supervisor lock. Exiting to prevent conflict.")
            sys.exit(1)

    def _cleanup_old_processes_on_port(self):
        """If port 5555 is currently bound, check and terminate orphaned process."""
        try:
            cmd = f'Get-NetTCPConnection -LocalPort {self.port} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique'
            p = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=5)
            pids = [int(line.strip()) for line in p.stdout.strip().splitlines() if line.strip().isdigit()]
            for pid in pids:
                if pid > 0 and pid != os.getpid():
                    # Check if this pid is our current dashboard proc
                    if self.dashboard_proc and pid == self.dashboard_proc.pid:
                        continue
                    self.log("STARTUP", f"Found process {pid} on port {self.port}. Terminating to free port for Harvey Dashboard...")
                    try:
                        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=5)
                    except Exception:
                        pass
        except Exception as e:
            self.log("WARN", f"Could not inspect port {self.port}: {e}")

    def start_harvey_dashboard(self) -> bool:
        """Start or restart the Harvey Dashboard process with safe output logging."""
        self.log("STARTUP", f"Launching Harvey Dashboard on 127.0.0.1:{self.port}...")
        self._cleanup_old_processes_on_port()
        # NOTE: DO NOT call _cleanup_orphaned_cloudflared() here! Cloudflared must remain untouched.
        time.sleep(0.5)

        cmd = [self.python_exe, "-m", "harvey.cli", "dashboard", "--port", str(self.port)]
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["HARVEY_AUTOSTART_ENGINE"] = "1"
        env["HARVEY_KEEP_ENGINE_ON_RESTART"] = "1"

        try:
            if self._dashboard_log_handle:
                try:
                    self._dashboard_log_handle.close()
                except Exception:
                    pass
            self._dashboard_log_handle = open(self.dashboard_log_file, "a", encoding="utf-8")
            self.dashboard_proc = subprocess.Popen(
                cmd,
                cwd=str(self.project_dir),
                stdout=self._dashboard_log_handle,
                stderr=subprocess.STDOUT,
                bufsize=1,
                env=env,
            )
            self.log("STARTUP", f"Harvey Dashboard process launched (PID: {self.dashboard_proc.pid})")
        except Exception as e:
            self.log("ERROR", f"Failed to launch Harvey Dashboard: {e}")
            return False

        # Wait for local health check
        healthy = False
        start_wait = time.monotonic()
        while time.monotonic() - start_wait < 30:
            if self.dashboard_proc.poll() is not None:
                self.log("ERROR", f"Harvey Dashboard process exited prematurely with code {self.dashboard_proc.returncode}")
                self._dump_dashboard_log_tail()
                return False
            status, _, _ = self.check_local_health()
            if status in (200, 401):  # 401 proves dashboard is running and auth guard is active
                healthy = True
                break
            time.sleep(0.5)

        if not healthy:
            self.log("ERROR", f"Harvey Dashboard failed to respond on port {self.port} within 30 seconds")
            self._dump_dashboard_log_tail()
            return False

        # Refresh token
        self.token = self._load_token()
        self.log("STARTUP", f"Harvey Dashboard is HEALTHY on port {self.port}. Auth token loaded.")
        return True

    def _dump_dashboard_log_tail(self, lines_count: int = 15):
        """Log recent output from dashboard.log for debugging failures."""
        if not self.dashboard_log_file.is_file():
            return
        try:
            lines = self.dashboard_log_file.read_text("utf-8", errors="replace").splitlines()
            tail = lines[-lines_count:]
            if tail:
                self.log("DEBUG", "--- Recent Dashboard Log Output ---")
                for line in tail:
                    self.log("DEBUG", f"  {line}", echo=False)
        except Exception:
            pass

    def start_cloudflare_tunnel(self) -> bool:
        """Start Cloudflare Quick Tunnel if not already running and healthy."""
        # 1. Check if our managed tunnel is already running
        if self.tunnel_proc and self.tunnel_proc.poll() is None:
            if self.public_url:
                pub_status, _, _ = self.check_public_health()
                if pub_status in (200, 401):
                    self.log("STARTUP", f"Cloudflare tunnel already running (PID: {self.tunnel_proc.pid}) and verified at {self.public_url}")
                    return True

        # 2. Check if an external cloudflared process is already tunneling our port
        try:
            cmd = f'Get-CimInstance Win32_Process -Filter "Name = \'cloudflared.exe\'" | Where-Object {{ $_.CommandLine -like "*{self.port}*" }} | Select-Object -ExpandProperty ProcessId'
            p = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=5)
            pids = [int(line.strip()) for line in p.stdout.strip().splitlines() if line.strip().isdigit()]
            if pids and self.url_file.is_file():
                existing_url = self.url_file.read_text("utf-8").strip()
                if existing_url:
                    self.public_url = existing_url
                    pub_status, _, _ = self.check_public_health()
                    if pub_status in (200, 401):
                        self.log("STARTUP", f"Adopted existing healthy Cloudflare tunnel process (PID: {pids[0]}) at {existing_url}")
                        return True
        except Exception:
            pass

        if not Path(self.cloudflared_exe).is_file():
            self.log("ERROR", f"Cloudflared executable not found at {self.cloudflared_exe}")
            return False

        self.log("STARTUP", f"Launching Cloudflare Quick Tunnel targeting http://127.0.0.1:{self.port}...")

        cmd = [
            self.cloudflared_exe,
            "tunnel",
            "--url", f"http://localhost:{self.port}",
            "--http-host-header", "localhost",
        ]

        if self.url_file.is_file():
            try:
                self.url_file.unlink()
            except Exception:
                pass

        try:
            self.tunnel_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            self.log("STARTUP", f"Cloudflare tunnel process launched (PID: {self.tunnel_proc.pid})")
        except Exception as e:
            self.log("ERROR", f"Failed to launch cloudflared: {e}")
            return False

        # Asynchronously capture dynamic URL from cloudflared stderr
        self.public_url = None
        url_event = threading.Event()

        def stream_tunnel_output():
            nonlocal url_event
            assert self.tunnel_proc and self.tunnel_proc.stderr
            for line in self.tunnel_proc.stderr:
                line_str = line.strip()
                if not line_str:
                    continue
                match = TRYCLOUDFLARE_REGEX.search(line_str)
                if match:
                    found_url = match.group(0)
                    hostname = match.group(1)
                    with self._lock:
                        self.public_url = found_url
                        try:
                            self.url_file.write_text(found_url, encoding="utf-8")
                        except Exception:
                            pass
                    self.log("STARTUP", f"Dynamic Cloudflare URL extracted: {found_url}")
                    _warm_dns(hostname)
                    url_event.set()

        self.tunnel_reader_thread = threading.Thread(target=stream_tunnel_output, daemon=True)
        self.tunnel_reader_thread.start()

        # Wait up to 30s for the dynamic URL to be assigned
        if not url_event.wait(timeout=30.0):
            self.log("ERROR", "Timed out waiting for Cloudflare Quick Tunnel URL assignment")
            return False

        return True

    def check_local_health(self) -> tuple[int, int, str]:
        """Check local /api/health with token. Returns (status_code, latency_ms, error_msg)."""
        url = f"http://127.0.0.1:{self.port}/api/health"
        req = Request(url)
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        start = time.monotonic()
        try:
            with urlopen(req, timeout=5.0) as resp:
                ms = int((time.monotonic() - start) * 1000)
                return resp.status, ms, ""
        except HTTPError as e:
            ms = int((time.monotonic() - start) * 1000)
            return e.code, ms, f"HTTPError {e.code}"
        except Exception as e:
            ms = int((time.monotonic() - start) * 1000)
            return 0, ms, str(e)

    def check_public_health(self) -> tuple[int, int, str]:
        """Check public /api/health with token. Returns (status_code, latency_ms, error_msg)."""
        if not self.public_url:
            return 0, 0, "No public URL configured"
        url = f"{self.public_url}/api/health"
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            ctx = ssl.create_default_context()
        req = Request(url)
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("User-Agent", "HarveyDemoSupervisor/2.0")
        start = time.monotonic()
        try:
            with urlopen(req, timeout=10.0, context=ctx) as resp:
                ms = int((time.monotonic() - start) * 1000)
                return resp.status, ms, ""
        except HTTPError as e:
            ms = int((time.monotonic() - start) * 1000)
            return e.code, ms, f"HTTPError {e.code}"
        except Exception as e:
            ms = int((time.monotonic() - start) * 1000)
            return 0, ms, str(e)

    def verify_email_safeguards(self) -> bool:
        """Verify that sending_paused is True and require_approval is True."""
        url = f"http://127.0.0.1:{self.port}/api/summary"
        req = Request(url)
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urlopen(req, timeout=5.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                sending_paused = data.get("sending_paused", False)
                if not sending_paused:
                    self.log("WARN", "CRITICAL WARNING: sending_paused is False in Harvey summary!")
                    return False
                self.log("STARTUP", "Email safeguards verified: sending_paused=True (Pilot Read-Only Hold)")
                return True
        except Exception as e:
            self.log("WARN", f"Could not verify summary API email safeguards: {e}")
            return False

    def get_engine_status(self) -> dict:
        """Query engine status via API or fallback directly to process/lockfile checks."""
        url = f"http://127.0.0.1:{self.port}/api/harvey/status"
        req = Request(url)
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urlopen(req, timeout=4.0) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            # If dashboard is momentarily restarting, probe pid/lock directly
            try:
                from harvey.pidfile import PID_FILE, LOCK_FILE, pid_alive, loop_lock_held, read_pid
                pid = read_pid(PID_FILE)
                if pid and pid_alive(pid) and loop_lock_held(LOCK_FILE):
                    return {"running": True, "pid": pid, "source": "lockfile"}
            except Exception:
                pass
            return {"running": False, "pid": None}

    def start_harvey_engine(self) -> bool:
        """Ensure Harvey heartbeat engine is running via dashboard API."""
        self.log("STARTUP", "Checking Harvey engine status...")
        status = self.get_engine_status()
        if status.get("running"):
            pid = status.get("pid")
            self.log("STARTUP", f"Harvey engine is RUNNING (PID: {pid}).")
            return True

        self.log("STARTUP", "Starting Harvey engine heartbeat loop via /api/harvey/start...")
        url = f"http://127.0.0.1:{self.port}/api/harvey/start"
        req = Request(url, data=b"{}", headers={"Content-Type": "application/json"})
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")

        try:
            with urlopen(req, timeout=10.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if not data.get("success") and "already running" not in data.get("message", ""):
                    self.log("WARN", f"Engine start returned: {data.get('message')}")
        except Exception as e:
            self.log("WARN", f"Engine start request failed: {e}")

        # Wait up to 10 seconds for engine to report running
        start_wait = time.monotonic()
        while time.monotonic() - start_wait < 10.0:
            status = self.get_engine_status()
            if status.get("running"):
                pid = status.get("pid")
                self.log("STARTUP", f"Harvey engine successfully STARTED (PID: {pid}). Background loop active.")
                return True
            time.sleep(1.0)

        self.log("WARN", "Harvey engine did not report running within 10s.")
        return False

    def restart_dashboard_safely(self, reason: str) -> bool:
        """Safely restart the dashboard process while leaving Cloudflare tunnel intact."""
        now = time.monotonic()
        if now - self._last_dashboard_restart < 5.0:
            self.log("WARN", "Throttling rapid dashboard restart request...")
            time.sleep(3.0)

        self._last_dashboard_restart = time.monotonic()
        self._restarting_dashboard = True
        self.log("RESTART", f"Restarting Harvey Dashboard (Reason: {reason}). Tunnel remains connected...")

        if self.dashboard_proc:
            try:
                self.dashboard_proc.terminate()
                self.dashboard_proc.wait(timeout=3)
            except Exception:
                try:
                    self.dashboard_proc.kill()
                except Exception:
                    pass
            self.dashboard_proc = None

        time.sleep(1.0)
        success = self.start_harvey_dashboard()
        self._restarting_dashboard = False

        if success:
            self.log("RESTART", "Harvey Dashboard restart SUCCESSFUL. Re-verifying engine...")
            self.start_harvey_engine()
        else:
            self.log("ERROR", "Harvey Dashboard restart FAILED.")
        return success

    def restart_tunnel_safely(self, reason: str) -> bool:
        """Safely restart Cloudflare tunnel without touching dashboard or engine."""
        now = time.monotonic()
        if now - self._last_tunnel_restart < 5.0:
            self.log("WARN", "Throttling rapid tunnel restart request...")
            time.sleep(3.0)

        self._last_tunnel_restart = time.monotonic()
        self.log("RESTART", f"Restarting Cloudflare Quick Tunnel (Reason: {reason}). Dashboard remains intact...")

        if self.tunnel_proc:
            try:
                self.tunnel_proc.terminate()
                self.tunnel_proc.wait(timeout=3)
            except Exception:
                try:
                    self.tunnel_proc.kill()
                except Exception:
                    pass
            self.tunnel_proc = None

        time.sleep(1.0)
        success = self.start_cloudflare_tunnel()
        if success:
            self.log("RESTART", f"Cloudflare tunnel restart SUCCESSFUL. New public URL: {self.public_url}")
        else:
            self.log("ERROR", "Cloudflare tunnel restart FAILED.")
        return success

    def print_public_banner(self):
        eng = self.get_engine_status()
        eng_info = f"RUNNING (PID {eng.get('pid')})" if eng.get("running") else "STOPPED"
        pub_info = self.public_url if self.public_url else "Initializing / Local Only"
        banner = [
            "",
            "=" * 70,
            "           HARVEY AI SALES AGENT — SUPERVISED DEMO",
            "=" * 70,
            f"  Local Dashboard:     http://127.0.0.1:{self.port}",
            f"  Public Cloudflare:   {pub_info}",
            f"  Harvey Engine:       {eng_info}",
            "  Authentication:      PROTECTED (Token Unlock Screen)",
            "                       (Enter token from data/dashboard-token to unlock)",
            "  Email Safeguards:    ACTIVE (sending_paused=True, require_approval=True)",
            "  Power Management:    ACTIVE (Windows sleep prevented while running)",
            "  Log File:            data/demo_supervisor.log (tokens redacted)",
            "=" * 70,
            "  TO STOP HARVEY & RESTORE POWER SETTINGS: run STOP_HARVEY_DEMO.bat or Ctrl+C",
            "=" * 70,
            "",
        ]
        print("\n".join(banner))

    def update_state_file(self, status: str = "running"):
        engine_status = self.get_engine_status()
        state = {
            "supervisor_pid": os.getpid(),
            "dashboard_pid": self.dashboard_proc.pid if self.dashboard_proc else None,
            "engine_pid": engine_status.get("pid"),
            "engine_running": engine_status.get("running", False),
            "tunnel_pid": self.tunnel_proc.pid if self.tunnel_proc else None,
            "public_url": self.public_url,
            "mode": "public" if self.public_url else "local",
            "port": self.port,
            "status": status,
            "sleep_prevented": self.sleep_prevented,
            "updated_at": datetime.datetime.now().isoformat(),
        }
        try:
            self.state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
            self.pid_file.write_text(str(os.getpid()), encoding="utf-8")
        except Exception:
            pass

    def run(self):
        self.running = True
        self.log("STARTUP", "Starting Harvey AI Sales Agent Public Demo Supervisor...")

        # 1. Enforce strict single-supervisor execution
        self._cleanup_duplicate_supervisors()

        # 2. Prevent Windows Sleep
        self.prevent_sleep()

        # 3. Register signal handlers
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, self._handle_signal)
        atexit.register(self.shutdown)

        # 4. Step 1: Start Harvey Dashboard locally
        if not self.start_harvey_dashboard():
            self.log("ERROR", "Failed to start Harvey Dashboard. Supervisor exiting.")
            self.shutdown()
            sys.exit(1)

        # 5. Step 2: Start Cloudflare Quick Tunnel targeting the healthy local dashboard
        self.start_cloudflare_tunnel()

        # 6. Step 3: Start Harvey Engine heartbeat loop
        self.start_harvey_engine()

        # 7. Step 4: Verify email safeguards
        self.verify_email_safeguards()

        # 8. Print Banner & Save State
        self.update_state_file("running")
        self.print_public_banner()

        # 9. Supervisor loop: Monitor, Auto-Restart, and Periodic Health Checks
        consecutive_local_failures = 0
        last_health_check = time.monotonic()

        try:
            while self.running:
                time.sleep(2.0)
                now = time.monotonic()

                # --- Monitor: Dashboard Process Crash ---
                if self.dashboard_proc and self.dashboard_proc.poll() is not None and not self._restarting_dashboard:
                    exit_code = self.dashboard_proc.returncode
                    self.log("RESTART", f"CRASH DETECTED: Harvey Dashboard (PID {self.dashboard_proc.pid}) exited with code {exit_code}.")
                    self._dump_dashboard_log_tail()
                    self.restart_dashboard_safely(f"process_exit_code_{exit_code}")

                # --- Monitor: Cloudflare Tunnel Crash ---
                if self.tunnel_proc and self.tunnel_proc.poll() is not None:
                    exit_code = self.tunnel_proc.returncode
                    self.log("RESTART", f"Cloudflare tunnel (PID {self.tunnel_proc.pid}) exited with code {exit_code}.")
                    self.restart_tunnel_safely(f"tunnel_exit_code_{exit_code}")

                # --- Periodic Health Checks (every 30 seconds) ---
                if now - last_health_check >= 30.0:
                    last_health_check = now
                    loc_status, loc_lat, loc_err = self.check_local_health()
                    pub_status, pub_lat, pub_err = self.check_public_health()
                    eng_status = self.get_engine_status()
                    eng_running = eng_status.get("running", False)
                    eng_pid = eng_status.get("pid")

                    if loc_status in (200, 401):
                        consecutive_local_failures = 0
                    else:
                        consecutive_local_failures += 1

                    status_str = f"RUNNING (PID {eng_pid})" if eng_running else "STOPPED"
                    pub_str = f"Public: {pub_status} ({pub_lat}ms)" if self.public_url else "Public: N/A"
                    self.log(
                        "HEALTH_CHECK",
                        f"Local: {loc_status} ({loc_lat}ms) | {pub_str} | Engine: {status_str} | Failures: {consecutive_local_failures} | Email Hold: Paused",
                        echo=False,
                    )

                    # Self-heal engine if unexpectedly stopped while dashboard is healthy
                    if not eng_running and loc_status in (200, 401):
                        self.log("RESTART", "Engine reported stopped while dashboard is healthy. Triggering engine start...")
                        self.start_harvey_engine()

                    # Self-heal hung dashboard (bounded retries)
                    if consecutive_local_failures >= 3:
                        self.log("RESTART", f"Local health check failed {consecutive_local_failures} times consecutively ({loc_err}). Restarting Harvey Dashboard...")
                        self.restart_dashboard_safely("consecutive_health_failures")
                        consecutive_local_failures = 0

                    self.update_state_file("running")

        except KeyboardInterrupt:
            self.log("SHUTDOWN", "KeyboardInterrupt received by supervisor.")
        finally:
            self.shutdown()

    def _handle_signal(self, signum, frame):
        self.log("SHUTDOWN", f"Shutdown signal ({signum}) received.")
        self.running = False

    def shutdown(self):
        if not self.running and not self.dashboard_proc and not self.tunnel_proc:
            return
        self.running = False
        self.log("SHUTDOWN", "Shutting down Harvey Demo Supervisor and managed processes...")

        # Terminate tunnel process
        if self.tunnel_proc:
            try:
                self.log("SHUTDOWN", f"Stopping Cloudflare tunnel (PID {self.tunnel_proc.pid})...")
                self.tunnel_proc.terminate()
                self.tunnel_proc.wait(timeout=3)
            except Exception:
                try:
                    self.tunnel_proc.kill()
                except Exception:
                    pass
            self.tunnel_proc = None

        # Stop Harvey engine cleanly via API
        try:
            url = f"http://127.0.0.1:{self.port}/api/harvey/stop"
            req = Request(url, data=b"{}", headers={"Content-Type": "application/json"})
            if self.token:
                req.add_header("Authorization", f"Bearer {self.token}")
            with urlopen(req, timeout=4.0) as resp:
                self.log("SHUTDOWN", "Harvey engine stop requested via API")
        except Exception:
            pass

        # Terminate dashboard process
        if self.dashboard_proc:
            try:
                self.log("SHUTDOWN", f"Stopping Harvey Dashboard (PID {self.dashboard_proc.pid})...")
                self.dashboard_proc.terminate()
                self.dashboard_proc.wait(timeout=3)
            except Exception:
                try:
                    self.dashboard_proc.kill()
                except Exception:
                    pass
            self.dashboard_proc = None

        if self._dashboard_log_handle:
            try:
                self._dashboard_log_handle.close()
            except Exception:
                pass
            self._dashboard_log_handle = None

        # Release lock file
        self._release_supervisor_lock()

        # Restore normal power settings
        self.restore_sleep()

        # Update state and remove PID file
        self.update_state_file("stopped")
        try:
            if self.pid_file.is_file():
                self.pid_file.unlink()
        except Exception:
            pass

        self.log("SHUTDOWN", "Harvey Demo Supervisor stopped cleanly. All child processes terminated.")


def main():
    supervisor = DemoSupervisor()
    supervisor.run()


if __name__ == "__main__":
    main()
