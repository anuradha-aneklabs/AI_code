# Harvey AI Sales Development Platform — Production Deployment & Migration Runbook

**Target Project:** `C:\Users\aneka\Desktop\harvey`  
**Target Database:** `data/harvey.db`  
**Current Baseline Hash:** `DDFEAE896B4D34260B6061D61BCCC91873E9BF06B05F5728B846DFE90FEC4D47`  
**Current Schema Version:** `user_version` 31  
**Target Schema Version:** `user_version` 32  
**Audience:** Product Owner, Sir, and DevOps Infrastructure Team  
**Status:** **REFERENCE RUNBOOK ONLY — DO NOT EXECUTE ON PRODUCTION WITHOUT EXPLICIT WRITTEN SIR APPROVAL**

---

## 0. Sir Approval Mandatory Checklist

Before running ANY step that touches the production environment or real `data/harvey.db`:

- [ ] **Sir Written Authorization**: Jitesh Sir's explicit written approval received for:
  1. Taking production backup and running v31 -> v32 schema migration on real `data/harvey.db`.
  2. Setting `HARVEY_ALLOW_REAL_DB_WRITE=1` strictly for the duration of the migration step.
  3. Starting the production daemon on port 5555.
  4. Keeping `HARVEY_LIVE_SENDING_ENABLED` unset (no live email sending is authorized).
- [ ] **Master Kill Switch Intact**: Master send kill switch in `harvey/integrations/mail_provider.py` is permanently ACTIVE.
- [ ] **HARVEY_LIVE_SENDING_ENABLED Unset**: `HARVEY_LIVE_SENDING_ENABLED` must stay completely UNSET in `os.environ`, `.env`, and shell.
- [ ] **Read-Only Gmail Scope Verified**: Gmail OAuth token only possesses `https://www.googleapis.com/auth/gmail.readonly` scope. Write scope is absent; the Gmail API will reject any transmission attempt at the provider level.
- [ ] **Safety Hold Maintained**: `sending_paused` remains active (`"Pilot Evaluation - Read-Only Safety Hold"`) and `channels.email.require_approval = True`.
- [ ] **Baseline SHA-256 Verified**: `Get-FileHash data/harvey.db` matches `DDFEAE896B4D34260B6061D61BCCC91873E9BF06B05F5728B846DFE90FEC4D47`.
- [ ] **Pre-Deployment Backup Taken**: Point-in-time SQLite backup taken to `data/backups/`.
- [ ] **Dryrun Rehearsal Confirmed**: 100% passed on `data/validation/harvey_deploy_dryrun.db`.
- [ ] **Authorized Real-DB Flag**: Understand that `HARVEY_ALLOW_REAL_DB_WRITE=1` is strictly required to modify `data/harvey.db`; without it, `harvey/paths.py` throws `RuntimeError`.

---

## 1. System Architecture & How Harvey is Actually Run

1. **Host & Port**:
   - **Production Port**: Strictly `127.0.0.1:5555`. Port 5555 is the production loopback daemon.
   - **Demo / QA Port Isolation**: Port `5566` was used STRICTLY for test/demo automation and browser sign-off runs on disposable copies (`data/validation/harvey_video_clean.db`). Port `5566` is NOT a deployment target and must NEVER be used in production.
2. **Reverse Proxy & Edge Routing**: Reverse proxy (Nginx / Cloudflare) terminates TLS and proxies external requests to local port 5555.
3. **Execution Mode**:
   - **Local CLI / Daemon**:
     ```powershell
     cd C:\Users\aneka\Desktop\harvey
     .\.venv\Scripts\python.exe -m harvey dashboard --port 5555
     ```
   - **Docker Production**:
     ```bash
     docker compose up -d
     ```
     Mounted at `/app` with `network_mode: host` running `["dashboard"]`.
4. **Environment Variables**:
   - Production writes are protected by `HARVEY_ALLOW_REAL_DB_WRITE=1`.
   - Test/copy runs override the database path using `HARVEY_DB_PATH`.
   - `HARVEY_LIVE_SENDING_ENABLED` is left UNSET in all production configs.

---

## 2. Step 1: Immutable Backup & SHA-256 Verification
**[STATUS: EXECUTED ON THE COPY; NOT VERIFIED ON PRODUCTION DB]**

Execute this step before touching the production database.

### Command
```powershell
# In PowerShell at C:\Users\aneka\Desktop\harvey
cd C:\Users\aneka\Desktop\harvey

# 1. Take online point-in-time SQLite snapshot via Harvey CLI
.\.venv\Scripts\python.exe -m harvey db backup --dest data/backups/harvey_pre_v32_production_backup.db

# 2. Record SHA-256 of the production database
Get-FileHash data/harvey.db -Algorithm SHA256 | Select-Object Path, Hash
```

### Verification Criteria
- Baseline hash before migration MUST match:
  `DDFEAE896B4D34260B6061D61BCCC91873E9BF06B05F5728B846DFE90FEC4D47`
- Backup file `data/backups/harvey_pre_v32_production_backup.db` exists with non-zero size.

---

## 3. Step 2: Additive Migration to Schema v32
**[STATUS: EXECUTED ON COPY data/validation/harvey_deploy_dryrun.db; NOT EXECUTED ON PRODUCTION DB]**

The migration is **100% additive**: introduces columns for product classification and tables for batch research. It does NOT drop tables or alter existing records.

### SQL Statements
```sql
ALTER TABLE companies ADD COLUMN product_class TEXT DEFAULT '';
ALTER TABLE companies ADD COLUMN product_class_evidence TEXT DEFAULT '';
ALTER TABLE discovery_results ADD COLUMN product_class TEXT DEFAULT '';
ALTER TABLE discovery_results ADD COLUMN product_class_evidence TEXT DEFAULT '';
PRAGMA user_version = 32;
```

### How Migration is Applied
**Authorized Variable:** `HARVEY_ALLOW_REAL_DB_WRITE=1` is strictly required to authorize writes to `data/harvey.db`.
```powershell
# Automated via StateManager startup (authorized)
.\.venv\Scripts\python.exe -c "import asyncio, os; os.environ['HARVEY_ALLOW_REAL_DB_WRITE']='1'; from harvey.state import StateManager; asyncio.run(StateManager('data/harvey.db').init_db())"
```

---

## 4. Step 3: Post-Migration Invariants Verification
**[STATUS: EXECUTED ON COPY data/validation/harvey_deploy_dryrun.db; VERIFIED ZERO ROW LOSS]**

Verify that all safety gates and business data are intact.

### Verification Command (Read-Only)
```powershell
.\.venv\Scripts\python.exe -c "
import sqlite3
con = sqlite3.connect('file:data/harvey.db?mode=ro', uri=True)
cur = con.cursor()

# Check user_version
cur.execute('PRAGMA user_version')
print('Schema user_version:', cur.fetchone()[0])

# Check true row counts
cur.execute('SELECT count(*) FROM companies')
print('Companies:', cur.fetchone()[0])
cur.execute('SELECT count(*) FROM prospects')
print('Prospects:', cur.fetchone()[0])
cur.execute('SELECT count(*) FROM outbox')
print('Outbox:', cur.fetchone()[0])

cur.execute('SELECT status, count(*) FROM outbox GROUP BY status')
print('Outbox statuses:', cur.fetchall())

con.close()
"
```

### True Production Database Counts
- `user_version` = `32` (was `31`)
- `companies` count = `85` (exact match)
- `prospects` count = `178` (exact match)
- `outbox` count = `233` (exact match)
- `observations` count = `366` (exact match)
- `actions` count = `671` (exact match)
- `runs` count = `26` (exact match)
- `outbox` status breakdown:
  - `pending_review`: 124
  - `cancelled`: 82
  - `approved`: 25
  - `rejected`: 1
  - `sent`: 1 (fixture)
- `sending_paused` = `"Pilot Evaluation - Read-Only Safety Hold"`
- `channels.email.require_approval` = `True`

---

## 5. Step 4: Restart Dashboard Server on Port 5555
**[STATUS: NOT VERIFIED ON PRODUCTION (PORT 5555 RESERVED FOR PROD; PORT 5566 USED ONLY ON DEMO COPY)]**

Once migration is confirmed, start the production dashboard daemon.

### Command
```powershell
cd C:\Users\aneka\Desktop\harvey
.\.venv\Scripts\python.exe -m harvey dashboard --port 5555
```

### Health Check
```powershell
curl -I http://127.0.0.1:5555/api/health
```
Expected HTTP Response: `HTTP/1.1 200 OK` with `{"status": "ok", "app": "harvey", "version": "2.0.0"}`.

---

## 6. Step 5: Confirm Free Signals via Legit Path
**[STATUS: EXECUTED ON COPY; NOT EXECUTED ON PRODUCTION]**

Ensure discovery is unblocked for zero-cost prospecting.

```powershell
.\.venv\Scripts\python.exe -m harvey signals --confirm free
```
Expected Output: All 26 free signals marked as `confirmed`.

---

## 7. Step 6: First Live Discovery Smoke Test ($0 Estimate First)
**[STATUS: VERIFIED ON COPY; NOT EXECUTED ON PRODUCTION]**

Always estimate cost prior to running live collection.

> **CRITICAL NOTE ON PROVIDERS:**
> - **OpenStreetMap / Nominatim**: Strictly used for physical local brick-and-mortar storefronts (e.g. dentists, retail). **NEVER** used for SaaS or software discovery.
> - **TinyFish**: Provider for web-scale and SaaS discovery.

### TinyFish $0 Estimate Command
```powershell
.\.venv\Scripts\python.exe -m harvey discover --estimate --industry "SaaS" --city "Indore, Madhya Pradesh, India" --provider tinyfish
```
Expected Output: `Estimated cost: $0.0000 (cap: $1.00)`.

---

## 8. Rollback Procedure
**[STATUS: EXECUTED ON THE COPY; NOT VERIFIED ON PRODUCTION DB (DRY-RUN TESTED WITH 100% HASH MATCH)]**

If any verification assertion fails, perform an immediate, clean rollback:

```powershell
# 1. Terminate running dashboard process on port 5555
Get-Process | Where-Object { $_.ProcessName -match "python" } | Stop-Process -Force

# 2. Restore production database from pre-migration backup
Copy-Item data/backups/harvey_pre_v32_production_backup.db data/harvey.db -Force

# 3. Verify restored hash matches original baseline
Get-FileHash data/harvey.db -Algorithm SHA256 | Select-Object Path, Hash
# Expected: DDFEAE896B4D34260B6061D61BCCC91873E9BF06B05F5728B846DFE90FEC4D47

# 4. Restart server on previous version
.\.venv\Scripts\python.exe -m harvey dashboard --port 5555
```
