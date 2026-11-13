# Harvey AI Sales Agent — Final Regression Verification Report

**Date:** November 13, 2026  
**Auditor:** Antigravity Engineering  
**Target Environment:** Windows 11 Enterprise, Python 3.11.9, SQLite Schema v22, Ollama Daemon (`qwen2.5:1.5b` + `nomic-embed-text`)

---

## 1. Executive Summary

A comprehensive regression verification of the Harvey AI Sales Agent was performed following the application of Database Migration 22 and nullable foreign key fixes. All test suites, API endpoints, RAG vector retrieval pipelines, local AI models, database integrity pragmas, Playwright browser workflows, and cold-boot restarts were verified with zero failures and zero regressions.

### Regression Scorecard
| Verification Category | Tests / Workflows Verified | Result | Status |
| :--- | :---: | :---: | :---: |
| **Python Automated Test Suite** | 3,328 tests collected | **3,144 passed, 184 skipped, 0 failed** | **PASS** |
| **Database Low-Level Checks** | `integrity_check` & `foreign_key_check` | **ok / 0 violations** | **PASS** |
| **Database Migration 22** | Nullable FKs & thread preservation | **Schema v22 verified** | **PASS** |
| **Playwright Real-Browser E2E** | 13 end-to-end user workflows | **13/13 passed (screenshots preserved)** | **PASS** |
| **Dashboard Tab Browser Audit** | 15 dashboard tabs audited | **15/15 passed (0 errors, 0 exceptions)** | **PASS** |
| **Local AI Inference & RAG** | Ollama LLM + nomic-embed + ChromaDB | **Verified with live HTTP queries** | **PASS** |
| **Cold-Boot State Persistence** | Process termination & fresh restart | **100% exact state match** | **PASS** |
| **Mandatory Safety Holds** | Approval gates, paused holds, scopes | **100% preserved (`sent_count = 0`)** | **PASS** |

---

## 2. Module-by-Module Regression Results

### A. Database Migration & Referential Integrity
- **Test File:** `tests/test_foreign_key_migration.py` (2 tests, 100% pass in 1.03s).
- **Checks:**
  - `PRAGMA integrity_check` $\rightarrow$ `[('ok',)]`
  - `PRAGMA foreign_key_check` $\rightarrow$ `[]` (0 violations)
  - `prospects.company_id` stores `NULL` when empty; `conversations.prospect_id` stores `NULL` when empty.
  - Zero application records lost or deleted during migration.

### B. Lead Discovery, Company Research & ICP/BANT Qualification
- **Test Files:** `tests/test_lead_research_enrichment.py`, `tests/test_lead_discovery_crm_pipeline.py`, `tests/test_bant_qualification.py` (22 tests, 100% pass).
- **Checks:**
  - OpenStreetMap Overpass discovery queries real coordinates in Indore without paid API keys.
  - Metro Ortho Care Pune correctly receives 0 geography points under Indore city-level scope.
  - Apex Global Hospital Network scored 0 ICP fit and 0/100 BANT Disqualified.
  - Deduplicated profiles: exactly 7 profiles in DB, CleanTech profile canonicalized to `prof_329svqpt`.

### C. Outreach Draft Generation & AI Personalization
- **Test File:** `tests/test_outreach_drafts.py` (11 tests, 100% pass).
- **Checks:**
  - Draft generation utilizes local Ollama `qwen2.5:1.5b` model with contextual prompts.
  - Missing prospect contacts addressed neutrally without fabricating identity.
  - Deliverability warnings flagged for unverified or risky emails.
  - Draft saving transitions candidate to `Outreach Draft Ready` in CRM pipeline.

### D. Campaign Review Queue & Human-in-the-Loop Approval Gates
- **Test File:** `tests/test_campaign_review_approval.py` (6 tests, 100% pass).
- **Checks:**
  - Draft review queue lists pending drafts isolated by campaign.
  - In-place draft editing preserves operator changes and enforces approval gate.
  - Rejection requires an operator reason and logs to immutable activity audit table.
  - Four original pilot drafts remain strictly preserved in exact original statuses (`a02fed19ec8d`: pending, `36dfa015198c`: cancelled, `3be7a27f3620`: cancelled, `fad4e4984339`: pending).

### E. Reply Intelligence, suggested Replies & Conversations
- **Test File:** `tests/test_reply_intelligence_convo.py` (3 tests, 100% pass).
- **Checks:**
  - Automated classification of incoming replies into 6 categories (Interested, Question, OOO, Unsubscribe, etc.).
  - AI reply drafting enabled for interested prospects.
  - Safety invariant strictly prevents automated reply generation for unsubscribed or bounced leads.

### F. Knowledge Base, ChromaDB Vector Store & RAG Retrieval
- **Test Files:** `tests/test_rag.py`, `tests/test_rag_followup_workflow.py` (100% pass).
- **Checks:**
  - 7 indexed documents and 19 text chunks persistent in `data/chroma`.
  - Ollama `nomic-embed-text` generates 768-dim float vectors.
  - Semantic vector search returns top-k chunks with cosine similarity > 0.80 and exact file citations.

### G. Calendar Link Validation & RFC 5545 ICS Export
- **Test File:** `tests/test_calendar_integration.py` (100% pass).
- **Checks:**
  - Calendar booking link syntax verified.
  - Computed 20 available meeting slots for upcoming business week.
  - RFC 5545 ICS calendar export produces valid calendar invitations.

---

## 3. Playwright Real-Browser Verification Summary

All 13 E2E scenarios passed with preserved visual evidence in `scratch/screenshots_e2e/`:
* **E2E 01:** Discovery $\rightarrow$ Research $\rightarrow$ Qualify $\rightarrow$ CRM Promotion ([01_discover_candidates.png](file:///c:/Users/aneka/Desktop/harvey/scratch/screenshots_e2e/01_discover_candidates.png), [01_crm_pipeline_view.png](file:///c:/Users/aneka/Desktop/harvey/scratch/screenshots_e2e/01_crm_pipeline_view.png))
* **E2E 02:** Company Directory $\rightarrow$ Contacts Enrichment ([02_companies_list.png](file:///c:/Users/aneka/Desktop/harvey/scratch/screenshots_e2e/02_companies_list.png), [02_contacts_list.png](file:///c:/Users/aneka/Desktop/harvey/scratch/screenshots_e2e/02_contacts_list.png))
* **E2E 03:** CRM Lead $\rightarrow$ Personalized Outreach Draft ([03_outbox_drafts.png](file:///c:/Users/aneka/Desktop/harvey/scratch/screenshots_e2e/03_outbox_drafts.png))
* **E2E 04:** Draft Edit $\rightarrow$ Review $\rightarrow$ Approval Gate ([04_campaign_review_gate.png](file:///c:/Users/aneka/Desktop/harvey/scratch/screenshots_e2e/04_campaign_review_gate.png))
* **E2E 05:** Campaign Sequences & Cadence ([05_campaigns_sequence.png](file:///c:/Users/aneka/Desktop/harvey/scratch/screenshots_e2e/05_campaigns_sequence.png))
* **E2E 06:** Gmail Read-Only Security ([06_gmail_settings_readonly.png](file:///c:/Users/aneka/Desktop/harvey/scratch/screenshots_e2e/06_gmail_settings_readonly.png))
* **E2E 07:** Conversations & Reply Classification ([07_conversations_view.png](file:///c:/Users/aneka/Desktop/harvey/scratch/screenshots_e2e/07_conversations_view.png))
* **E2E 08:** Knowledge Upload $\rightarrow$ RAG Query $\rightarrow$ Citations ([08_knowledge_rag_citations.png](file:///c:/Users/aneka/Desktop/harvey/scratch/screenshots_e2e/08_knowledge_rag_citations.png))
* **E2E 09:** Calendar Availability & ICS Scheduling ([09_calendar_scheduling.png](file:///c:/Users/aneka/Desktop/harvey/scratch/screenshots_e2e/09_calendar_scheduling.png))
* **E2E 10:** Compliance Checks & Activity Audit Logs ([10_activity_audit_log.png](file:///c:/Users/aneka/Desktop/harvey/scratch/screenshots_e2e/10_activity_audit_log.png))
* **E2E 11:** Command Deck & Usage Analytics ([11_today_command_deck.png](file:///c:/Users/aneka/Desktop/harvey/scratch/screenshots_e2e/11_today_command_deck.png))
* **E2E 12:** Integration Status Cards ([12_settings_integrations.png](file:///c:/Users/aneka/Desktop/harvey/scratch/screenshots_e2e/12_settings_integrations.png))
* **E2E 13:** Cold-Boot Restart Durability (Verified across daemon process kill)

---

## 4. 15/15 Dashboard Tabs Clean Audit

The browser tab audit re-executed across all 15 sections of the running dashboard (`http://127.0.0.1:5555/`):
* **Today, Outbox, Conversations, Signals, Discover, CRM Pipeline, Companies, Prospects, Campaigns, Knowledge, Activity, Usage, Controls, Settings, Help**
* **Results:** **0 console errors, 0 failed network requests, 0 unhandled exceptions**.
* **Visual Screenshots:** Preserved in `scratch/tab_audit_screenshots/` ([01_today.png](file:///c:/Users/aneka/Desktop/harvey/scratch/tab_audit_screenshots/01_today.png) through [15_help.png](file:///c:/Users/aneka/Desktop/harvey/scratch/tab_audit_screenshots/15_help.png)).

---

## 5. Mandatory Safety Controls Invariants

All safety invariants were verified directly in the SQLite database and active configuration:
* `cfg.channels.email.require_approval == True` $\rightarrow$ **PASS**
* `settings.sending_paused == 'Pilot Evaluation - Read-Only Safety Hold'` $\rightarrow$ **PASS**
* `SELECT count(*) FROM outbox WHERE status = 'sent'` $\rightarrow$ **0 (PASS)**
* `SCOPE == 'https://www.googleapis.com/auth/gmail.readonly'` $\rightarrow$ **PASS**
* Protected pilot drafts (`a02fed19ec8d`, `36dfa015198c`, `3be7a27f3620`, `fad4e4984339`) $\rightarrow$ **100% UNCHANGED (PASS)**
