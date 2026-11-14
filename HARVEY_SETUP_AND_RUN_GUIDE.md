# Harvey AI Sales Agent — Setup & Operator Run Guide

**Project Path:** `C:\Users\aneka\Desktop\harvey`  
**Current Git Commit:** `bf7d437`  
**Platform:** Windows 11 Enterprise (Python 3.11.9, `.venv\Scripts\python.exe`)  
**Dashboard URL:** `http://127.0.0.1:5555/`  

---

## 1. System Overview

Harvey is an autonomous AI sales agent and lead intelligence system engineered for spatial discovery, web enrichment, ICP/BANT qualification, local RAG vector retrieval, local neural drafting, and human-governed outbox management.

### Key Architecture Components
1. **Local Neural AI (Ollama):** High-speed local completion via `qwen2.5:1.5b` and dense semantic vector embeddings via `nomic-embed-text:latest` running on `http://127.0.0.1:11434`.
2. **Vector Store (ChromaDB):** Embedded vector database at `data/chroma` storing indexed knowledge base documents.
3. **Database Engine:** SQLite 3 with Write-Ahead Logging (WAL) and foreign key enforcement at `data/harvey.db` (Schema v23).
4. **Operations Dashboard:** FastHTML/Starlette ASGI application running on port 5555, protected by token authentication (`data/dashboard-token`).
5. **Safety Guard:** Strict human-in-the-loop review with `require_approval = true` and `sending_paused = "Pilot Evaluation - Read-Only Safety Hold"`.

---

## 2. Prerequisites & Environment Setup

### 2.1 Python Environment
Ensure Python 3.11 is active in the virtual environment:
```powershell
# In C:\Users\aneka\Desktop\harvey
.venv\Scripts\python.exe --version
# Output: Python 3.11.9
```

### 2.2 Local Ollama AI Engine
1. Download and install [Ollama for Windows](https://ollama.com/download/windows).
2. Start the Ollama server:
   ```powershell
   ollama serve
   ```
3. Pull the required models:
   ```powershell
   ollama pull qwen2.5:1.5b
   ollama pull nomic-embed-text
   ```
4. Verify models are available:
   ```powershell
   curl http://127.0.0.1:11434/api/tags
   ```

---

## 3. Starting the Application

### 3.1 Cold-Booting the Harvey Dashboard
Execute the CLI dashboard command:
```powershell
.venv\Scripts\python.exe -m harvey.cli dashboard --port 5555
```
The server will bind to `http://127.0.0.1:5555/`.

### 3.2 Accessing the Dashboard UI
1. Retrieve the authentication token:
   ```powershell
   Get-Content data\dashboard-token
   ```
2. Open a modern web browser and navigate to:
   ```
   http://127.0.0.1:5555/#token=<YOUR_TOKEN>
   ```
3. If prompted by the lock screen, paste the token to unlock the dashboard.

---

## 4. External Provider Configuration

To connect commercial and external cloud providers, create or edit `C:\Users\aneka\Desktop\harvey\.env`:

```env
# ── Core & Local Services ──
OLLAMA_BASE_URL=http://localhost:11434

# ── Google Workspace Gmail (Read-Only) ──
GMAIL_CLIENT_ID=your_client_id.apps.googleusercontent.com
GMAIL_CLIENT_SECRET=GOCSPX-your_secret

# ── Microsoft 365 / Entra ID (Read-Only) ──
M365_CLIENT_ID=your_azure_client_id
M365_TENANT_ID=your_azure_tenant_id
M365_CLIENT_SECRET=your_azure_client_secret

# ── Search & Discovery ──
DATAFORSEO_LOGIN=your_dataforseo_email
DATAFORSEO_PASSWORD=your_dataforseo_password
SERPER_API_KEY=your_serper_api_key
TINYFISH_API_KEY=your_tinyfish_api_key
SCAVIO_API_KEY=your_scavio_api_key

# ── Email Verification ──
ZEROBOUNCE_API_KEY=your_zerobounce_api_key
HUNTER_API_KEY=your_hunter_api_key
REOON_API_KEY=your_reoon_api_key

# ── Calendar & Availability ──
CALCOM_API_KEY=your_calcom_api_key
CALENDLY_TOKEN=your_calendly_personal_access_token
```

### 4.1 Gmail Read-Only Authorization
Once `GMAIL_CLIENT_ID` and `GMAIL_CLIENT_SECRET` are present in `.env`:
```powershell
.venv\Scripts\python.exe -m harvey.cli gmail-auth
```
- A browser will open requesting consent for the `https://www.googleapis.com/auth/gmail.readonly` scope.
- Upon authorization, tokens are securely written to `data/gmail_token.json`.

### 4.2 Microsoft 365 Authorization
Ensure the Azure App Registration has delegated permissions: `User.Read`, `Mail.Read`, `Calendars.Read`, `offline_access`.
```powershell
.venv\Scripts\python.exe -m harvey.cli m365-auth
```
- Tokens are securely saved to `data/m365_token.json`.

---

## 5. Testing & Verification Commands

### 5.1 Provider Connectivity Audit
Safely test all 15 provider connections without sending mail or consuming unnecessary quotas:
```powershell
.venv\Scripts\python.exe scratch/test_all_connections.py
```

### 5.2 Full Functional Journey (Real-Data E2E)
Execute the complete 9-stage sales journey:
```powershell
.venv\Scripts\python.exe scratch/test_functional_journeys.py
```

### 5.3 Browser UI Interactive Validation (Playwright)
Run the headless browser validation across modals, editors, and tabs:
```powershell
.venv\Scripts\python.exe scratch/validate_mvp_ui_e2e.py
```

### 5.4 Full Pytest Regression Suite
Run all unit and integration tests:
```powershell
.venv\Scripts\pytest.exe -q
```

### 5.5 Safety Invariant Verification
Confirm `sent_count = 0`, `require_approval = true`, and pilot drafts are intact:
```powershell
.venv\Scripts\python.exe scratch/verify_safety_invariants.py
```

---

## 6. Database Maintenance & Backups

### 6.1 Creating a Database Backup
```powershell
.venv\Scripts\python.exe -c "from harvey.state import StateManager; sm = StateManager('data/harvey.db'); print('Backup:', sm.backup_db())"
```
Backups are archived into `data/backups/harvey_backup_YYYYMMDD_HHMMSS.db`.

### 6.2 Verifying Database Integrity
```powershell
.venv\Scripts\python.exe -c "import sqlite3; con = sqlite3.connect('data/harvey.db'); print('Integrity:', con.execute('PRAGMA integrity_check').fetchall()); print('Foreign Keys:', con.execute('PRAGMA foreign_key_check').fetchall())"
```

---

## 7. Safety & Compliance Rules

1. **Never Send Live Cold Emails:** Outbound dispatch is disabled by `sending_paused` and `require_approval`.
2. **Never Request Write Scopes:** Only authorize `gmail.readonly` and Microsoft `Mail.Read`.
3. **Never Commit Secrets:** `.env` and `data/*token.json` are git-ignored and must never be committed.
4. **Preserve Pilot Records:** Do not alter the 4 pilot drafts (`36dfa015198c`, `3be7a27f3620`, `a02fed19ec8d`, `fad4e4984339`).

---

## 8. Production Deployment Options

### 8.1 Docker Container Deployment
Use the production compose specification:
```bash
docker compose -f docker-compose.prod.yml up -d
```
Access the loopback port via an authenticated reverse proxy (e.g. Caddy/Nginx) or SSH tunnel:
```bash
ssh -L 5555:127.0.0.1:5555 user@your-server
```

### 8.2 Linux VPS Systemd Service
Copy the systemd configuration file to your systemd directory:
```bash
sudo cp harvey.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now harvey
```

### 8.3 Windows Server Background Supervisor
Run the PowerShell supervisor daemon:
```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_production_daemon.ps1
```
