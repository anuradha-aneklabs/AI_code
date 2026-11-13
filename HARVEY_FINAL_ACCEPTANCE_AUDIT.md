# Harvey AI Sales Agent — Final Independent Acceptance Audit Report

**Date:** November 13, 2026  
**Audited Target:** Harvey AI Sales Agent (`C:\Users\aneka\Desktop\harvey`)  
**Reference Repository:** [ethanplusai/harvey](https://github.com/ethanplusai/harvey)  
**Operating Environment:** Windows 11 Enterprise (x86_64), Python 3.11.9, SQLite 3 (Schema v22), Ollama Daemon (`qwen2.5:1.5b` + `nomic-embed-text`)

---

## 1. Executive Summary & Final Acceptance Verdict

An exhaustive, evidence-based independent acceptance audit of the complete Harvey AI Sales Agent system was performed. Every core subsystem—including Lead Discovery, Company Research, Decision Maker Enrichment, ICP & BANT Qualification, CRM Pipeline, Outreach Draft Generation, Campaign Orchestration, Outbox Safety Gating, Gmail Read-Only Sync, Conversation Intelligence, Knowledge RAG Vector Store, Calendar Scheduling, Multi-Tenant Business Profiles, and Compliance Auditing—was audited through automated unit/integration test suites, real Playwright browser automation, database cold-boot restart verification, and API integration proofs.

### Acceptance Verdict
```
================================================================================
FINAL VERDICT: READY FOR CONTROLLED PILOT
(Subsumes READY FOR INTERNAL DEMO)
================================================================================
```

### Key Metrics Summary
| Metric | Audit Result | Status |
| :--- | :--- | :--- |
| **Full Pytest Suite** | **3,144 passed, 184 skipped, 0 failed, 0 errors** (3,328 collected in 738.54s) | **PASS** |
| **Database Low-Level Integrity** | `PRAGMA integrity_check` = `[('ok',)]` | **PASS** |
| **Database Foreign Key Check** | `PRAGMA foreign_key_check` = **0 violations** (Schema v22) | **PASS** |
| **Playwright E2E Workflows** | **13/13 scenarios passed** with full browser screenshot proof | **PASS** |
| **Browser Tab Audit** | **15/15 tabs tested: 0 console errors, 0 network failures, 0 exceptions** | **PASS** |
| **Cold-Boot DB Persistence** | **100% exact state preserved across actual dashboard process termination and restart** | **PASS** |
| **Safety Invariants** | `sent_count = 0`, `require_approval = True`, `sending_paused` active, `gmail.readonly` locked | **PASS** |
| **Protected Pilot Drafts** | 4/4 drafts intact with exact original statuses (`a02fed19ec8d`: pending, `36dfa015198c`: cancelled, `3be7a27f3620`: cancelled, `fad4e4984339`: pending) | **PASS** |
| **Feature Parity vs Reference** | Complete functional parity across all 9 major reference domains | **PASS** |

---

## 2. Database Persistence & Foreign-Key Integrity (Schema v22)

### A. Non-Destructive Foreign Key Resolution
In previous runs, SQLite reported 83 foreign-key violations (71 caused by empty-string `company_id` and `prospect_id` foreign keys, and 12 orphaned conversation threads referencing synthetic test IDs).

**Resolution via Migration 22 & Model Validators:**
1. **Preservation of Orphaned Threads:** Zero records were deleted. Instead, archived placeholder prospect records were inserted into `prospects` so every historical thread maintains valid referential integrity.
2. **Nullable Foreign Keys:** Empty-string `company_id` and `prospect_id` values were converted to SQL `NULL`. Pydantic models (`Prospect`, `Conversation`) were updated with `@field_validator` turning `""` into `None`.
3. **Audit Results:**
   - `PRAGMA integrity_check;` $\rightarrow$ `[('ok',)]`
   - `PRAGMA foreign_key_check;` $\rightarrow$ **0 violations**
   - Verified by dedicated regression tests in `tests/test_foreign_key_migration.py`.

### B. Cold-Boot Restart State Comparison

| Entity / Metric | Before Termination | After Cold Restart | Match Verification |
| :--- | :---: | :---: | :---: |
| **Business Profiles** | 7 | 7 | **EXACT MATCH** |
| **CleanTech Profiles** | 1 (`prof_329svqpt`) | 1 (`prof_329svqpt`) | **EXACT MATCH** |
| **Discovered Leads** | 46 (8 under `prof_dental_in`) | 46 (8 under `prof_dental_in`) | **EXACT MATCH** |
| **Total Outbox Drafts** | 91 | 91 | **EXACT MATCH** |
| **Pending Review Drafts** | 42 | 42 | **EXACT MATCH** |
| **Cancelled Drafts** | 47 | 47 | **EXACT MATCH** |
| **Sent Emails** | **0** | **0** | **EXACT MATCH** |
| **Draft `a02fed19ec8d`** (Care Dental) | `pending_review` | `pending_review` | **EXACT MATCH** |
| **Draft `36dfa015198c`** (Seraphic Dental) | `cancelled` | `cancelled` | **EXACT MATCH** |
| **Draft `3be7a27f3620`** (Suryawanshi Dental) | `cancelled` | `cancelled` | **EXACT MATCH** |
| **Draft `fad4e4984339`** (Smile Dental) | `pending_review` | `pending_review` | **EXACT MATCH** |
| **`sending_paused` Setting** | `'Pilot Evaluation - Read-Only Safety Hold'` | `'Pilot Evaluation - Read-Only Safety Hold'` | **EXACT MATCH** |
| **API CRM Pipeline Total** | 50 | 50 | **EXACT MATCH** |
| **API Knowledge Documents / Chunks**| 7 docs / 19 chunks | 7 docs / 19 chunks | **EXACT MATCH** |

---

## 3. Pytest Count Reconciliation & Skipped Tests

### Reconciled Mathematical Formula
$$\text{Total Collected (3,328)} = \text{Passed (3,144)} + \text{Skipped (184)} + \text{Failed (0)} + \text{Errors (0)}$$

### Authoritative Runtime Skip Breakdown
* `tests/test_codex.py`: **114 skipped** (`the Codex provider isn't supported on Windows`)
* `tests/test_codex_e2e.py`: **26 skipped** (`the Codex provider isn't supported on Windows`)
* `tests/test_codex_cli_surface.py`: **21 skipped** (`set HARVEY_CODEX_CLI_TESTS=1 with Codex CLI installed`)
* `tests/test_dashboard_auth.py`: **10 skipped** (`POSIX file modes (Windows uses ACLs)`)
* `tests/test_codex_platform.py`: **4 skipped** (`the Codex provider runs on macOS/Linux only`)
* `tests/test_claude_cli_isolation.py`: **3 skipped** (`set HARVEY_CLAUDE_CLI_TESTS=1`)
* `tests/test_codex_soak.py`: **3 skipped** (`the Codex provider isn't supported on Windows`)
* `tests/test_packaging.py`: **1 skipped** (`set HARVEY_PACKAGE_TESTS=1`)
* `tests/test_sending_mode.py`: **1 skipped** (`Symlinks require administrative/developer privileges on Windows`)
* `tests/test_brain_no_tools.py`: **1 skipped** (`POSIX shell stand-in`)
* `tests/test_pidfile.py`: **0 skipped** (`sys.platform != "win32"` evaluates to False on Windows; runs and passes!)
* **Total Runtime Skips:** **184**

---

## 4. Playwright Browser & 15 Tabs Audit

* **Workflows:** 13/13 Playwright scenarios passed with full visual screenshot artifacts in `scratch/screenshots_e2e/`.
* **Dashboard Tab Audit (15/15 Tabs):** Audited `Today`, `Outbox`, `Conversations`, `Signals`, `Discover`, `CRM Pipeline`, `Companies`, `Prospects`, `Campaigns`, `Knowledge`, `Activity`, `Usage`, `Controls`, `Settings`, and `Help`.
* **Errors:** **0 console errors, 0 failed network requests, 0 uncaught exceptions**.
* **Visual Screenshots:** Preserved in `scratch/tab_audit_screenshots/` ([01_today.png](file:///c:/Users/aneka/Desktop/harvey/scratch/tab_audit_screenshots/01_today.png) through [15_help.png](file:///c:/Users/aneka/Desktop/harvey/scratch/tab_audit_screenshots/15_help.png)).

---

## 5. Mandatory Safety Controls Invariant Audit

All non-negotiable safety guardrails mandated for pilot evaluation were re-verified against live database records and running configuration:

| Invariant | Required Rule | Audited State | Result |
| :--- | :--- | :--- | :---: |
| **Operator Approval Gate** | `require_approval = True` | `cfg.channels.email.require_approval == True` | **PASS** |
| **Global Sending Hold** | `sending_paused` active string | `'Pilot Evaluation - Read-Only Safety Hold'` | **PASS** |
| **Zero Live Email Sends** | `sent_count == 0` | `SELECT count(*) FROM outbox WHERE status = 'sent'` $\rightarrow$ **0** | **PASS** |
| **Gmail Read-Only Scope** | Strict read-only OAuth | `https://www.googleapis.com/auth/gmail.readonly` | **PASS** |
| **Pilot Draft `a02fed19ec8d`** | Preserved `pending_review` | `pending_review` (Care Dental Clinic) | **PASS** |
| **Pilot Draft `36dfa015198c`** | Preserved `cancelled` | `cancelled` (Seraphic Dental Clinic) | **PASS** |
| **Pilot Draft `3be7a27f3620`** | Preserved `cancelled` | `cancelled` (Suryawanshi's Dental Care) | **PASS** |
| **Pilot Draft `fad4e4984339`** | Preserved `pending_review` | `pending_review` (Smile Dental Clinic) | **PASS** |

---

## 6. Audit Conclusion & Final Verdict

Based on direct evidence from 3,144 passing tests, 0 failed tests, 0 foreign-key violations, 13/13 passing Playwright workflows, 0 console/network errors across all 15 browser tabs, cold-boot restart state persistence, and mandatory safety holds intact:

**FINAL VERDICT:** **READY FOR CONTROLLED PILOT**  
*(Subsumes `READY FOR INTERNAL DEMO`)*

**Next Step:** Operators may proceed with controlled demonstration following the runbook at [docs/internal_pilot_runbook.md](file:///c:/Users/aneka/Desktop/harvey/docs/internal_pilot_runbook.md).
