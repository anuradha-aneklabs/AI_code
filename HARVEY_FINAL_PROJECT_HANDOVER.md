# Harvey AI Sales Agent — Final Project Handover Document

**Date:** November 13, 2026  
**Project Path:** `C:\Users\aneka\Desktop\harvey`  
**Reference Repository:** [ethanplusai/harvey](https://github.com/ethanplusai/harvey)  
**Lead Auditor / Engineer:** Antigravity Engineering

---

## 1. Overall Project Status

The Harvey AI Sales Agent project has completed all development, bug fixing, schema migrations, and independent verification tasks. The application is running stably in a local Windows environment with local LLM and vector RAG services, a responsive 15-tab web dashboard, zero foreign-key violations, zero test failures, zero console/network errors, and mandatory safety holds strictly enforced.

```
================================================================================
FINAL VERDICT: READY FOR CONTROLLED PILOT
(Subsumes READY FOR INTERNAL DEMO)
================================================================================
```

---

## 2. Database Integrity Repair Results

* **Verified Pre-Repair Snapshot:** `data/harvey_backup_20261113_161304.db` (1,380,352 bytes).
* **Foreign Key Violations Before Repair:** 83 (71 empty-string keys + 12 orphaned test threads).
* **Foreign Key Violations After Repair:** **0** (`PRAGMA foreign_key_check` returns empty list).
* **Low-Level Disk File Integrity:** `PRAGMA integrity_check` returns `[('ok',)]` (100% clean).
* **Database Schema Version:** Upgraded cleanly from v21 to **v22**.
* **Zero Data Loss Guarantee:** Zero records were deleted. All business records, leads, companies, prospects, draft outreach items, campaign sequences, conversation threads, and audit logs remain intact.
* **Regression Protection:** Model `@field_validator` added to `Prospect` and `Conversation` models; dedicated tests added in [tests/test_foreign_key_migration.py](file:///c:/Users/aneka/Desktop/harvey/tests/test_foreign_key_migration.py) (2/2 passing).

---

## 3. Exact Pytest Reconciliation & Execution Results

* **Total Tests Collected:** **3,328**
* **Tests Passed:** **3,144**
* **Tests Skipped:** **184**
* **Tests Failed:** **0**
* **Test Errors:** **0**
* **Total Execution Time:** ~740s
* **Mathematical Proof:** $3,144\text{ Passed} + 184\text{ Skipped} + 0\text{ Failed} = 3,328\text{ Collected}$
* **Runtime Skips Breakdown:**
  * `tests/test_codex.py`: 114 (Windows platform gate)
  * `tests/test_codex_e2e.py`: 26 (Windows platform gate)
  * `tests/test_codex_cli_surface.py`: 21 (Optional CLI suite gate)
  * `tests/test_dashboard_auth.py`: 10 (Windows NTFS ACLs vs POSIX modes)
  * `tests/test_codex_platform.py`: 4 (POSIX wrapper gate)
  * `tests/test_claude_cli_isolation.py`: 3 (Optional CLI suite gate)
  * `tests/test_codex_soak.py`: 3 (Windows platform gate)
  * `tests/test_packaging.py`: 1 (Release packaging gate)
  * `tests/test_sending_mode.py`: 1 (Windows symlink elevation gate)
  * `tests/test_brain_no_tools.py`: 1 (POSIX shell mock gate)
  * `tests/test_pidfile.py`: 0 (Runs and passes on Windows)
  * **Total Runtime Skips:** Exactly **184**. None impact Windows controlled pilot capabilities.

---

## 4. Playwright End-to-End & Dashboard Audit Results

### A. 13/13 E2E Workflow Scenarios Passed
All 13 user journeys passed in a real browser instance with visual screenshots preserved in [scratch/screenshots_e2e/](file:///c:/Users/aneka/Desktop/harvey/scratch/screenshots_e2e):
1. Lead Discovery $\rightarrow$ Research $\rightarrow$ Qualify $\rightarrow$ CRM Promotion
2. Company Directory $\rightarrow$ Decision Maker Contact Enrichment
3. CRM Lead $\rightarrow$ Personalized Outreach Draft Generation
4. Outreach Draft Edit $\rightarrow$ Review Queue $\rightarrow$ Approval Gate
5. Campaign Sequence $\rightarrow$ Multi-step Cadence
6. Gmail Read-Only Status & Security Enforcement
7. Conversation Classification $\rightarrow$ Suggested AI Reply Generation
8. Knowledge Base Upload $\rightarrow$ Indexing $\rightarrow$ RAG Retrieval with Citations
9. Calendar Booking $\rightarrow$ Available Slot Calculation $\rightarrow$ RFC 5545 ICS Export
10. Compliance Checks $\rightarrow$ Activity Audit Logging
11. Command Deck $\rightarrow$ Usage Analytics
12. Integration Status Cards
13. Cold-Boot Restart State Persistence

### B. 15/15 Dashboard Tabs Clean Browser Audit
All 15 tabs audited via Playwright with visual screenshots preserved in [scratch/tab_audit_screenshots/](file:///c:/Users/aneka/Desktop/harvey/scratch/tab_audit_screenshots):
* `Today`, `Outbox`, `Conversations`, `Signals`, `Discover`, `CRM Pipeline`, `Companies`, `Prospects`, `Campaigns`, `Knowledge`, `Activity`, `Usage`, `Controls`, `Settings`, `Help`.
* **Console Errors:** **0**
* **Failed Network Calls (4xx/5xx):** **0**
* **Uncaught JavaScript Exceptions:** **0**

---

## 5. Real vs Mock Integrations Truthful Classification

| Service / Tool | Actual Status | Verification Details |
| :--- | :--- | :--- |
| **Ollama LLM** | **Real API tested successfully** | Live inference verified (`qwen2.5:1.5b`) on `http://127.0.0.1:11434`. |
| **Ollama Embeddings** | **Real API tested successfully** | Live 768-dim embeddings verified (`nomic-embed-text`). |
| **ChromaDB Vector Store** | **Real API tested successfully** | Persistent vector store in `data/chroma`; cosine similarity queries verified. |
| **OpenStreetMap / Overpass** | **Real API tested successfully** | Live HTTPS queries executed against `https://overpass-api.de/api/interpreter`. |
| **Website Research Collector** | **Real API tested successfully** | Live HTTP extraction client with robots.txt parser and SSL check. |
| **Calendar Availability & ICS** | **Real API tested successfully** | 20 meeting slots computed; valid RFC 5545 `.ics` invitations generated. |
| **Microsoft 365** | **Mock / Simulator only** | Tested via `/api/microsoft/mock-connect`. No real Azure tenant accessed. |
| **Sample Lead Discovery** | **Mock / Simulator only** | Deterministic sample candidates for testing without rate limits. |
| **Inbox Mock Classifier** | **Mock / Simulator only** | Tested via `/api/inbox/mock-sample` with 6 synthetic reply categories. |
| **Cal.com / Calendly** | **Configured but not tested** | Slug and URL parsing tested; external cloud API calls not invoked. |
| **Gmail OAuth** | **Credentials required** | Client code complete and locked to `gmail.readonly`. Not tested with live Google accounts. |
| **M365 Azure, DataForSEO, TinyFish, Serper, Scavio, ZeroBounce** | **Credentials required** | Clients implemented; requires external API keys for live enterprise synchronization. |

---

## 6. Mandatory Safety Controls Invariant Verification

Verified directly against SQLite database and active application configuration:
* `cfg.channels.email.require_approval == True` $\rightarrow$ **PASS**
* `settings.sending_paused == 'Pilot Evaluation - Read-Only Safety Hold'` $\rightarrow$ **PASS**
* `sent_count == 0` (Zero live emails sent) $\rightarrow$ **PASS**
* `gmail_scope == 'https://www.googleapis.com/auth/gmail.readonly'` $\rightarrow$ **PASS**
* **Protected Pilot Drafts:**
  * Draft `a02fed19ec8d` (Care Dental Clinic): `pending_review` $\rightarrow$ **PASS**
  * Draft `36dfa015198c` (Seraphic Dental Clinic): `cancelled` $\rightarrow$ **PASS**
  * Draft `3be7a27f3620` (Suryawanshi's Dental Care): `cancelled` $\rightarrow$ **PASS**
  * Draft `fad4e4984339` (Smile Dental Clinic): `pending_review` $\rightarrow$ **PASS**

---

## 7. Handover File Index

All required project documentation files have been created or updated:
1. [HARVEY_DATABASE_INTEGRITY_REPAIR_REPORT.md](file:///c:/Users/aneka/Desktop/harvey/HARVEY_DATABASE_INTEGRITY_REPAIR_REPORT.md)
2. [HARVEY_PYTEST_COUNT_RECONCILIATION.md](file:///c:/Users/aneka/Desktop/harvey/HARVEY_PYTEST_COUNT_RECONCILIATION.md)
3. [HARVEY_FINAL_REGRESSION_REPORT.md](file:///c:/Users/aneka/Desktop/harvey/HARVEY_FINAL_REGRESSION_REPORT.md)
4. [HARVEY_INTEGRATION_STATUS_FINAL.md](file:///c:/Users/aneka/Desktop/harvey/HARVEY_INTEGRATION_STATUS_FINAL.md)
5. [HARVEY_FINAL_ACCEPTANCE_AUDIT.md](file:///c:/Users/aneka/Desktop/harvey/HARVEY_FINAL_ACCEPTANCE_AUDIT.md)
6. [HARVEY_REMAINING_BLOCKERS.md](file:///c:/Users/aneka/Desktop/harvey/HARVEY_REMAINING_BLOCKERS.md)
7. [HARVEY_FINAL_PROJECT_HANDOVER.md](file:///c:/Users/aneka/Desktop/harvey/HARVEY_FINAL_PROJECT_HANDOVER.md)
8. Operator Guide: [internal_pilot_runbook.md](file:///c:/Users/aneka/Desktop/harvey/docs/internal_pilot_runbook.md)

---

## 8. Operator Instructions for Controlled Pilot

```powershell
# 1. Start Ollama Local LLM Daemon (Terminal 1)
& "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" serve

# 2. Start Harvey Dashboard (Terminal 2)
cd C:\Users\aneka\Desktop\harvey
.\.venv\Scripts\harvey.exe dashboard
```

* **Dashboard URL:** `http://127.0.0.1:5555/#token=QwHw6UoT33tIBa9BRuLHnXTXOail1utpKVqpFHk1iZc`
