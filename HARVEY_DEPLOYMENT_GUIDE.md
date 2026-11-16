# Harvey AI Sales Agent — Production Deployment & Operations Guide

**Application:** Harvey AI Sales Agent (Enterprise Edition v2.0.0)  
**Distribution Archive:** `dist/harvey-v2.0.0-production-source.zip` (326 verified source files, 8.08 MB)  
**Target Environments:** Windows Server / 11, Linux (Ubuntu/Debian), Docker Containers, Cloud PaaS (Render / Railway)  

---

## 1. System Requirements & Prerequisites

### Minimum Hardware Specifications:
- **CPU:** 4 Cores (x86_64 or ARM64)
- **RAM:** 8 GB RAM minimum (16 GB recommended if running local 7B Ollama models locally)
- **Storage:** 20 GB available SSD space (database and vector store persist locally)
- **Network:** Outbound HTTPS (port 443) to Google / Microsoft / OSM endpoints; Inbound port 5555 for Dashboard

### Software Prerequisites:
- **Python:** Version 3.11+ (tested on Python 3.11.9)
- **Pip & Virtualenv:** standard packaging tools
- **Ollama:** (Optional for local inference; download from [ollama.com](https://ollama.com))

---

## 2. Production Source Package Verification

The production archive is located at:
`dist/harvey-v2.0.0-production-source.zip`

### Package Integrity & Hygiene Guarantee:
- **File Count:** 326 production source files.
- **Excluded Non-Production Data:** `.git`, `.venv`, `.pytest_cache`, `__pycache__`, temporary files, and test databases.
- **Strict Secrets Exclusion:** Contains **zero** credentials, secrets, tokens (`dashboard-token`, `gmail_token.json`), or `.env` files.
- **Template Included:** Includes clean [.env.example](file:///C:/Users/aneka/Desktop/harvey/.env.example) with all configuration variables.

---

## 3. Deployment Option 1: Native Windows Background Daemon

For dedicated Windows workstations or Windows Server:

### Step 1: Extract and Setup Virtual Environment
```powershell
# Extract package
Expand-Archive -Path "dist\harvey-v2.0.0-production-source.zip" -DestinationPath "C:\Harvey"
cd C:\Harvey

# Create Python 3.11 virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install production dependencies
pip install -r requirements.txt
```

### Step 2: Configure Environment
```powershell
Copy-Item .env.example .env
notepad .env
```
Fill in the mailbox configuration (`GMAIL_CLIENT_ID` or `M365_CLIENT_ID`), AI keys (or leave default for local Ollama), and set `TEST_RECIPIENT_EMAIL`.

### Step 3: Run the Background Production Daemon
Execute the provided production daemon script:
```powershell
.\scripts\run_production_daemon.ps1
```
This script validates prerequisites, initializes the SQLite database schema, creates a secure access token, and starts the Harvey Dashboard on port 5555 as a resilient background service.

---

## 4. Deployment Option 2: Docker Compose (Recommended for Production)

Harvey includes a production-ready [docker-compose.prod.yml](file:///C:/Users/aneka/Desktop/harvey/docker-compose.prod.yml) orchestrating the application and local inference containers.

### Step 1: Start Containers
```bash
# Start Harvey and Ollama services in detached mode
docker compose -f docker-compose.prod.yml up -d
```

### Architecture:
- **`harvey-web`:** Runs the dashboard and task orchestrator on port `5555`.
- **`harvey-ollama`:** Runs the private AI engine on port `11434`.
- **Persistent Volume:** Mounts host directory `./data` to `/app/data` to ensure SQLite database, CRM records, and audit logs persist across container updates.

---

## 5. Deployment Option 3: Cloud PaaS (Render / Railway)

Harvey includes a validated [render.yaml](file:///C:/Users/aneka/Desktop/harvey/render.yaml) blueprint for cloud hosting.

### Key Cloud Settings:
In `.env` or PaaS Environment Variables:
```env
HARVEY_ALLOW_REMOTE_BIND=1
HARVEY_ALLOWED_HOSTS="your-subdomain.onrender.com,localhost"
```

### Cloud Disk Requirement:
Ensure a persistent disk is mounted at `/app/data` (minimum 10 GB) so that SQLite database changes persist across redeployments.

---

## 6. Database Maintenance & Backup Procedures

### Automated Point-in-Time Backup:
Harvey maintains point-in-time SQLite backups in `data/backups/`.

To trigger an immediate backup:
```powershell
.venv\Scripts\python.exe -c "
import shutil, datetime
from pathlib import Path
ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
dest = Path('data/backups') / f'harvey_backup_{ts}.db'
shutil.copy2('data/harvey.db', dest)
print(f'Backup created at: {dest}')
"
```

### Integrity Verification:
Verify physical database health and foreign key consistency:
```powershell
.venv\Scripts\python.exe scratch/verify_db_safety.py
```
Expected output:
- `physical_integrity: ok`
- `foreign_keys_violations: []`
- `sending_paused_setting: Pilot Evaluation - Read-Only Safety Hold`
- `config_require_approval: True`

### Disaster Recovery & Restore:
To restore from a backup:
1. Stop the dashboard service.
2. Replace `data/harvey.db` with the chosen backup file:
   ```powershell
   Copy-Item "data\backups\harvey_backup_<TIMESTAMP>.db" "data\harvey.db" -Force
   ```
3. Restart the service.

---

## 7. Security Hardening & Zero-Spam Invariants

When running in production, adhere to the following operational invariants:

1. **Keep Human Review Enabled:** In `harvey.yaml`, keep `channels.email.require_approval: true`.
2. **Paced Outbound Volume:** Configure `channels.email.daily_limit: 30` (or lower for newly warmed domains).
3. **Dedicated Outbound Domains:** Never send cold outreach from your primary corporate email domain. Use secondary domains configured with SPF, DKIM, and DMARC records.
4. **Token Security:** The dashboard bearer token in `data/dashboard-token` must be kept private and rotated periodically.

