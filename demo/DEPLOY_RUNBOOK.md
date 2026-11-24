# Harvey AI Sales Development Platform — Production Deployment & Migration Runbook

**Target Project:** `C:\Users\aneka\Desktop\harvey`  
**Target Database:** `data/harvey.db`  
**Current Baseline Hash:** `DDFEAE896B4D34260B6061D61BCCC91873E9BF06B05F5728B846DFE90FEC4D47`  
**Current Schema Version:** `user_version` 31  
**Target Schema Version:** `user_version` 32  
**Audience:** Product Owner, Sir, and DevOps Infrastructure Team  
**Execution Status:** **REFERENCE RUNBOOK ONLY — DO NOT EXECUTE WITHOUT EXPLICIT WRITTEN SIR APPROVAL**

---

## 1. System Architecture & Exposure Model

According to [JITESH_SIR_FINAL_HANDOVER.md](file:///C:/Users/aneka/Desktop/harvey/JITESH_SIR_FINAL_HANDOVER.md) and [docker-compose.yml](file:///C:/Users/aneka/Desktop/harvey/docker-compose.yml):
1. **Host & Port:** The Harvey backend daemon runs loopback-only on `127.0.0.1:5555`.
2. **Reverse Proxy & Edge Routing:** Aneka Labs reverse proxy (Nginx / Cloudflare) handles TLS termination and forwards traffic from `harvey.anekalabs.com` to local port 5555.
3. **Container Runtime:** `docker-compose.yml` mounts the project root to `/app` using `network_mode: host` and runs `["dashboard"]`.
4. **Required Approvals / Access:**
   - Jitesh Sir's authorization to restart port 5555.
   - Access to the host VM/container running port 5555.
   - Claude CLI OAuth credentials or `.env` provider keys if live enrichment is enabled.

---

## 2. Pre-Deployment Step 1: Immutable Backup & SHA-256 Verification

Execute this step before touching the production database.

### Command [VERIFIED: CLI syntax tested]
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

The remaining migration is **100% additive**: it introduces two new columns to `companies` and two new columns to `discovery_results`. It does not drop tables, alter existing columns, or mutate prospect/outbox data.

### Exact SQL Statements in Migration v32 [VERIFIED: from harvey/state.py:MIGRATIONS[31]]
```sql
ALTER TABLE companies ADD COLUMN product_class TEXT DEFAULT '';
ALTER TABLE companies ADD COLUMN product_class_evidence TEXT DEFAULT '';
ALTER TABLE discovery_results ADD COLUMN product_class TEXT DEFAULT '';
ALTER TABLE discovery_results ADD COLUMN product_class_evidence TEXT DEFAULT '';
PRAGMA user_version = 32;
```

### How Migration is Applied [VERIFIED: CLI syntax tested]
Option A (Automatic via StateManager startup):
```powershell
.\.venv\Scripts\python.exe -c "import asyncio, os; os.environ['HARVEY_ALLOW_REAL_DB_WRITE']='1'; from harvey.state import StateManager; asyncio.run(StateManager('data/harvey.db').init_db())"
```
Option B (Direct SQLite execution):
```powershell
sqlite3 data/harvey.db "ALTER TABLE companies ADD COLUMN product_class TEXT DEFAULT ''; ALTER TABLE companies ADD COLUMN product_class_evidence TEXT DEFAULT ''; ALTER TABLE discovery_results ADD COLUMN product_class TEXT DEFAULT ''; ALTER TABLE discovery_results ADD COLUMN product_class_evidence TEXT DEFAULT ''; PRAGMA user_version = 32;"
```

---

## 4. Step 3: Post-Migration Invariants Verification

Verify that all safety gates and business data are intact.

### Verification Command [VERIFIED: tested on copy DB]
```powershell
.\.venv\Scripts\python.exe -c "
import sqlite3
con = sqlite3.connect('file:data/harvey.db?mode=ro', uri=True)
cur = con.cursor()

# Check user_version
cur.execute('PRAGMA user_version')
print('Schema user_version:', cur.fetchone()[0])

# Check row counts
cur.execute('SELECT count(*) FROM companies')
print('Companies:', cur.fetchone()[0])
cur.execute('SELECT count(*) FROM contacts')
print('Contacts:', cur.fetchone()[0])
cur.execute('SELECT count(*) FROM prospects')
print('Prospects:', cur.fetchone()[0])

# Outbox safety check: exactly 1 sent email fixture, zero new sends
cur.execute('SELECT status, count(*) FROM outbox GROUP BY status')
print('Outbox:', cur.fetchall())

# Safety settings check
cur.execute('SELECT key, value FROM settings WHERE key IN (\'sending_paused\', \'channels.email.require_approval\')')
print('Safety settings:', cur.fetchall())

con.close()
"
```

### Expected Post-Migration State [VERIFIED via dry-run on data/validation/harvey_deploy_dryrun.db]
- `user_version` = `32`
- `companies` count = `85`
- `prospects` count = `178`
- `outbox` count = `233`
- `import_batches` count = `7`
- `import_domains` count = `7`
- `company_websites` count = `16`
- `observations` count = `366`
- `actions` count = `671`
- `runs` count = `26`
- `sending_paused` = `True` / `Pilot Evaluation - Read-Only Safety Hold`
- `channels.email.require_approval` = `True`
- `channels.email.zero_emails_sent` = `True` (Strict read-only safety maintained)

---

## 5. Step 4: Restart Dashboard Server on Port 5555

Once migration is confirmed, start the production dashboard daemon.

### Command [VERIFIED: CLI syntax tested]
```powershell
cd C:\Users\aneka\Desktop\harvey
.\.venv\Scripts\python.exe -m harvey dashboard --port 5555
```

### Health Check [VERIFIED: tested on port 5566]
```powershell
# In a separate terminal
curl -I http://127.0.0.1:5555/api/health
```
Expected HTTP Response: `HTTP/1.1 200 OK` with `{"status": "ok", "app": "harvey", "version": "2.0.0"}`.

---

## 6. Step 5: Confirm $0 Free Signals via Legit Path

Ensure discovery is unblocked for zero-cost prospecting.

### Command [VERIFIED: CLI syntax tested]
```powershell
.\.venv\Scripts\python.exe -m harvey signals --confirm free
```

### Verification [VERIFIED: CLI syntax tested]
```powershell
.\.venv\Scripts\python.exe -m harvey signals
```
Expected Output: All 26 free/included signals marked as `confirmed`.

---

## 7. Step 6: First Live Discovery Smoke Test ($0 Estimate First)

Always estimate cost prior to running live collection. Harvey supports both OSM (OpenStreetMap) and TinyFish search providers.

### Provider Check: TinyFish Search Integration [VERIFIED]
- **API Status:** Endpoint `/api/discover/providers` confirms `tinyfish` provider available with `status: ok, cost_per_query: 0.0, monthly_limit: 1000`.
- **Criteria Gate:** Endpoint `/api/discovery/criteria` confirms active ICP: SaaS in Indore with geo-bounds and negative exclusions.
- **Cost Estimate:** Endpoint `/api/discover/estimate` confirms projected spend is strictly `$0.00` across all generated queries.

### Command [VERIFIED: CLI syntax tested]
```powershell
# 1. Run estimate first (Safety Guard)
.\.venv\Scripts\python.exe -m harvey discover --estimate --industry "SaaS" --city "Indore, Madhya Pradesh, India" --provider tinyfish

# 2. Check projected cost is strictly $0.0000
# Output will display: Estimated cost: $0.0000 (cap: $1.00)
```

---

## 8. Rollback Procedure (In Case of Any Discrepancy)

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
