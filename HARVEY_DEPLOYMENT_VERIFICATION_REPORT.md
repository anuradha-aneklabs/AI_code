# Harvey AI Sales Agent — Deployment Verification & Operations Report

**Project Path:** `C:\Users\aneka\Desktop\harvey`  
**Current Git Commit:** `d0d415c`  
**Deployment Date:** 2026-11-14  
**Local Production Node:** `http://127.0.0.1:5555/`  
**Operating System:** Windows 11 Enterprise (Python 3.11.9, `.venv\Scripts\python.exe`)  
**Deployment Classification:** **PRODUCTION ASSETS VERIFIED; LOCAL NODE OPERATIONAL; CLOUD PUSH GATED BY OPERATOR REPO PERMISSIONS**  

---

## 1. Executive Summary

This report documents the production readiness, deployment asset verification, and deployment execution audit for the Harvey AI Sales Agent.

In accordance with Phase 8 of the master directive, all in-repository deployment configurations have been prepared, audited, and verified. A production node is currently operational on loopback port 5555, protected by token authentication and strict email safety holds.

### Master Deployment Status Overview
| Deployment Dimension | Configured Target / Mechanism | Operational Status | Verification Evidence |
| :--- | :--- | :--- | :--- |
| **Local Pilot Node** | Windows Background Service Daemon | **LIVE & OPERATIONAL** | Serving `http://127.0.0.1:5555/`; token authenticated; healthchecks passing. |
| **Container Engine** | `Dockerfile` & `docker-compose.prod.yml` | **ASSETS COMPLETE & AUDITED** | Multi-stage non-root container with Playwright Chromium & loopback healthcheck. |
| **Linux Daemon** | `harvey.service` (systemd unit) | **ASSETS COMPLETE & AUDITED** | Automatic process supervision, standard logging, auto-restart on exit. |
| **Remote Git Push** | GitHub `origin` (`ethanplusai/harvey`) | **CREDENTIAL GATED (HTTP 403)** | Operator `Sunilrajput-2004` lacks write permissions to `ethanplusai/harvey`. |
| **Cloud Container Deploy** | Railway / Render / Fly.io / VPS | **CONFIGURATION READY** | Awaiting operator target selection and API token in environment. |

---

## 2. Production Deployment Assets & Configurations

### 2.1 Production Dockerfile ([Dockerfile](file:///C:/Users/aneka/Desktop/harvey/Dockerfile))
- **Base Image:** `python:3.12-slim`
- **Security:** Runs as non-root user `harvey` (`UID 1000`, `GID 1000`).
- **Headless Browser:** Bundles Playwright Chromium and system dependencies for web research and scrapers.
- **Port:** Exposes loopback port 5555.
- **Healthcheck:**
  ```dockerfile
  HEALTHCHECK --interval=1m --timeout=10s --start-period=1m --retries=3 \
    CMD python -c "import socket; socket.create_connection(('127.0.0.1', 5555), timeout=5).close()"
  ```
- **Entrypoint:** `python -m harvey dashboard`

### 2.2 Production Compose Spec ([docker-compose.prod.yml](file:///C:/Users/aneka/Desktop/harvey/docker-compose.prod.yml))
- **Isolation:** Mounts loopback port `127.0.0.1:5555:5555` to prevent unauthenticated public exposure.
- **Persistence:** Named volume `harvey_data` mapped to `/app/data` to ensure SQLite database (`harvey.db`), ChromaDB vectors, and tokens persist across container restarts.
- **Read-Only Mounts:** Mounts `harvey.yaml`, `.env`, `prompts/`, and `skills/` as read-only volumes.

### 2.3 Linux Systemd Service ([harvey.service](file:///C:/Users/aneka/Desktop/harvey/harvey.service))
- **Daemon Supervision:** Configured with `Restart=always` and `RestartSec=5` for self-healing process execution on Linux VPS environments.

### 2.4 Windows Production Runner ([scripts/run_production_daemon.ps1](file:///C:/Users/aneka/Desktop/harvey/scripts/run_production_daemon.ps1))
- **Supervisor Loop:** Automatic process watchdog monitoring `python -m harvey.cli dashboard --port 5555` with automated restart upon unexpected exit.

---

## 3. Remote Deployment & GitHub Push Audit

In Phase 8, a live Git remote push verification was attempted:

```powershell
git push --dry-run origin main
```

### Execution Output:
```
remote: Permission to ethanplusai/harvey.git denied to Sunilrajput-2004.
fatal: unable to access 'https://github.com/ethanplusai/harvey/': The requested URL returned error: 403
```

### Root Cause Analysis:
1. The remote `origin` is set to `https://github.com/ethanplusai/harvey.git`.
2. The active local Git credentials belong to account `Sunilrajput-2004`.
3. Account `Sunilrajput-2004` has not been granted Collaborator/Write permissions on the `ethanplusai/harvey` repository.
4. **Per Master Prompt Rule:** *"Do not claim deployment or GitHub push succeeded unless the actual operation succeeds. If no deployment target or credentials exist, identify exactly what is missing and prepare the deployment configuration so that only the required operator action remains."*

---

## 4. Operational Health of Local Production Node

The local Harvey production node is actively running and serving traffic:

1. **Dashboard URL:** `http://127.0.0.1:5555/`
2. **Access Token Location:** `data/dashboard-token`
3. **Health Status:**
   - Web Server: Listening on TCP 127.0.0.1:5555
   - Database Connection: SQLite Schema v23 (`PRAGMA integrity_check = ok`)
   - ChromaDB Vector Store: 19 chunks indexed (`data/chroma`)
   - Local AI Daemon: Ollama running on `http://127.0.0.1:11434` with `qwen2.5:1.5b` and `nomic-embed-text`
4. **Browser UI Auditing:**
   - 15/15 Dashboard Tabs verified with 0 console errors, 0 failed requests, and 0 uncaught exceptions.
   - Interactive modal outreach editor and approval workflow verified via Playwright.

---

## 5. Non-Negotiable Safety Verification Post-Deployment

All email and data safety invariants were verified against the live production database (`data/harvey.db`):

| Invariant | Value | Status |
| :--- | :--- | :---: |
| `sent_count` | `0` | **VERIFIED (Zero emails sent)** |
| `require_approval` | `true` | **VERIFIED (Human gate enforced)** |
| `sending_paused` | `"Pilot Evaluation - Read-Only Safety Hold"` | **VERIFIED (Active safety hold)** |
| Gmail Scope | `https://www.googleapis.com/auth/gmail.readonly` | **VERIFIED (Read-only)** |
| Microsoft 365 Scope | `User.Read Mail.Read Calendars.Read offline_access` | **VERIFIED (Read-only)** |
| Pilot Draft `36dfa015198c` | `cancelled` | **PRESERVED** |
| Pilot Draft `3be7a27f3620` | `cancelled` | **PRESERVED** |
| Pilot Draft `a02fed19ec8d` | `pending_review` | **PRESERVED** |
| Pilot Draft `fad4e4984339` | `pending_review` | **PRESERVED** |

---

## 6. Actionable Operator Steps for Cloud Deployment

To complete remote cloud deployment to GitHub or a cloud hosting provider, the operator should perform one of the following standard actions:

### Option A: Authorize GitHub Push
1. In the GitHub repository settings for `ethanplusai/harvey`, add user `Sunilrajput-2004` as a collaborator with Write permissions (or configure a Personal Access Token with `repo` scope).
2. Execute the push command:
   ```powershell
   git push origin main
   ```

### Option B: Deploy to a Linux Cloud VPS via Docker
1. Copy the project folder to the target server:
   ```bash
   scp -r C:\Users\aneka\Desktop\harvey user@your-cloud-vps:/opt/harvey
   ```
2. Start the containerized service:
   ```bash
   cd /opt/harvey
   docker compose -f docker-compose.prod.yml up -d
   ```
3. Securely tunnel to the dashboard from your workstation:
   ```bash
   ssh -L 5555:127.0.0.1:5555 user@your-cloud-vps
   ```

### Option C: Deploy to Railway / Render / Fly.io
1. Connect your GitHub repository to Railway or Render.
2. The platform will automatically detect [Dockerfile](file:///C:/Users/aneka/Desktop/harvey/Dockerfile).
3. Set environment variable `OLLAMA_BASE_URL` pointing to your hosted Ollama or OpenAI-compatible endpoint.
4. Mount a persistent disk to `/app/data`.

