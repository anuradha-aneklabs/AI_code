# Harvey AI Sales Agent — Complete End-to-End Test Report (Phase 8)

**Project Path:** `C:\Users\aneka\Desktop\harvey`  
**Execution Date:** November 13, 2026  
**Test Harness:** Playwright Chromium (Automated Browser) + Pytest Suite  
**Test Result:** **13/13 Phase 8 Workflows Passed (100% Pass Rate)**  

---

## 1. Executive Summary

This report documents the rigorous execution and empirical results of the **Phase 8 End-to-End Workflow Testing** suite across Harvey AI Sales Agent. Testing was conducted on the running production application using authenticated browser automation, simulated network conditions, and direct SQLite database assertions.

All 13 core operator workflows passed without errors. Zero JavaScript console errors were encountered, database mutations persisted reliably across sessions, and all strict safety invariants remained 100% intact throughout.

---

## 2. Phase 8 Workflow Scenarios Execution Matrix

| Scenario # | Workflow Description | Key Components Verified | Browser Evidence / Screenshot | Result |
| :---: | :--- | :--- | :--- | :---: |
| **1** | Discover Company $\rightarrow$ Research $\rightarrow$ Qualify $\rightarrow$ CRM Pipeline | Candidate toolbar counters, BANT badge tooltips, CRM import action, duplicate warnings | `01_discover_candidates.png`, `01_crm_pipeline_view.png` | **PASS** |
| **2** | Company $\rightarrow$ Decision Maker $\rightarrow$ Contact Enrichment | Researched companies catalog (44 companies), decision makers directory (79 contacts), role flagging | `02_companies_list.png`, `02_contacts_list.png` | **PASS** |
| **3** | CRM Lead $\rightarrow$ Personalized Outreach Draft Generation | Grounded cold email drafting, research context injection, zero hallucination | `03_outbox_drafts.png` (107 drafts) | **PASS** |
| **4** | Draft $\rightarrow$ Edit $\rightarrow$ Review $\rightarrow$ Approval Gate | Campaign review modal, inline editor, rejection justification requirement, outbox staging | `04_campaign_review_gate.png` | **PASS** |
| **5** | Campaign $\rightarrow$ Sequence $\rightarrow$ Follow-up Planning | Multi-step sequence chains (Step 1 Intro $\rightarrow$ Step 2 Follow-up), delay interval configuration | `05_campaigns_sequence.png` (111 campaigns) | **PASS** |
| **6** | Gmail Read-Only $\rightarrow$ Inbox Sync $\rightarrow$ Classification | `gmail.readonly` scope enforcement, status card display, mime decoder | `06_gmail_settings_readonly.png` | **PASS** |
| **7** | Conversation $\rightarrow$ Reply Intelligence $\rightarrow$ Suggested Reply | Thread history, sentiment detection, in-place suggested reply editor | `07_conversations_view.png` | **PASS** |
| **8** | Knowledge Upload $\rightarrow$ Indexing $\rightarrow$ RAG Query $\rightarrow$ Citations | ChromaDB local vector store, 7 indexed docs, 19 total chunks, similarity search with citations | `08_knowledge_rag_citations.png` | **PASS** |
| **9** | Calendar $\rightarrow$ Availability $\rightarrow$ Scheduling $\rightarrow$ ICS Export | Working hours & weekend filtering, 20 available meeting slots computed, RFC 5545 `.ics` export | `09_calendar_scheduling.png` | **PASS** |
| **10** | Compliance Check $\rightarrow$ Audit Log | Unsubscribe handling, opt-out suppression, audit trail logging (113 activity events) | `10_activity_audit_log.png` | **PASS** |
| **11** | Dashboard $\rightarrow$ Analytics $\rightarrow$ Reports | Today command deck, safety hold banner, usage token analytics, CSV download | `11_usage_analytics.png`, `11_today_command_deck.png` | **PASS** |
| **12** | Integration Configuration $\rightarrow$ Health Check | Gmail, M365, Ollama, ZeroBounce, Scraping provider status and health probes | `12_settings_integrations.png` | **PASS** |
| **13** | Application Restart $\rightarrow$ Database Persistence & Safety | Server restart resilience, SQLite data retention, safety invariant enforcement | Verified via SQLite & Config API | **PASS** |

---

## 3. Detailed Scenario Test Evidence

### Scenario 1: Lead Discovery $\rightarrow$ ICP Qualification $\rightarrow$ CRM Pipeline
- **Input:** Profile `Dental Clinics & Practices (India)` (`prof_dental_in`).
- **Observations:**
  - Candidates toolbar accurately partitioned: **Pending: 2**, **In CRM: 6**, **All: 8**.
  - `Metro Ortho Care Pune`: Displays `80 · High Fit`, `BANT 75/100` (geography mismatch received +0 pts instead of previous erroneous +20 pts).
  - `Apex Global Hospital Network`: Displays `0 · Low Fit`, `BANT 0/100` Disqualified with transparent reasons (Sector divergence, 4,500+ headcount, role contact).
  - CRM Pipeline tab rendered 34 active records partitioned across 6 stages (`Discovered`, `Contacted`, `Replied`, `Meeting Scheduled`, `Qualified`, `Closed`).

### Scenario 2: Companies & Contacts Catalog
- **Directory Audit:**
  - Companies: 44 companies cataloged with domains, locations, employee sizes, and sector classifications.
  - Contacts: 79 decision-makers cataloged with titles, verified/risky deliverability tags, and research context.

### Scenario 3 & 4: Outreach Drafting & Campaign Review
- **Outbox Queue:** 107 staged drafts verified.
- **Review Gates:** Every draft requires human operator review before sending. Approving moves draft to ready state; rejecting requires explicit written justification recorded in the audit log.

### Scenario 6 & 12: Integrations & Gmail Safety
- **Gmail Integration:** Scopes locked strictly to `gmail.readonly`. No write, send, or delete permissions granted.
- **Microsoft 365:** Tested OAuth public client flow and safe sandbox simulation mode.

### Scenario 8: Local RAG Knowledge Base & Semantic Search
- **Store Status:** Local ChromaDB collection healthy.
- **Indexed Documents:** 7 documents indexed (`dental_product_guide.md`, `accounting_product_guide.md`, `legal_product_guide.md`, `saas_product_guide.md`, `home_services_product_guide.md`, `sample_product.md`, `e2e_qa_procedure.md`).
- **Total Chunks:** 19 vector chunks with `nomic-embed-text` 768-dimensional embeddings.
- **Search Verification:** Query `"dental clinic automation"` retrieved 3 relevant chunks with similarity scores $\ge 0.58$ and exact document filename citations.

### Scenario 9: Calendar Scheduling & RFC 5545 ICS Export
- **Availability Engine:** Computed 20 valid 30-minute meeting slots across upcoming business days, excluding weekends and non-working hours.
- **ICS Generator:** Emitted standard RFC 5545 `VEVENT` with valid `UID`, `DTSTAMP`, `DTSTART`, `DTEND`, `SUMMARY`, and `DESCRIPTION`.

---

## 4. Resilience, Error Handling & Edge Case Testing

| Edge Case Test | Condition Simulated | Expected Behavior | Observed Result | Status |
| :--- | :--- | :--- | :--- | :---: |
| **Invalid Profile Submission** | Empty name / missing industry | Rejection toast, submission blocked | Rejected with `"Profile name and industry are required"` | **PASS** |
| **Missing Discovery API Key** | Empty `TINYFISH_API_KEY` | Graceful fallback to OpenStreetMap / sample | Safe fallback executed without crash | **PASS** |
| **Ollama Outage & Recovery** | Daemon stopped then restarted | Auto-recovery without DB edits | Detected online daemon, cleared paused banner | **PASS** |
| **Duplicate Lead Import** | Importing existing CRM domain | Warning banner, prevent duplicate | Flagged as `"Already exists in CRM"` | **PASS** |
| **Browser Hard Refresh** | Refresh during draft review | Retain session and outbox state | Auth preserved in sessionStorage, data intact | **PASS** |
| **Server Process Restart** | Kill and restart dashboard daemon | SQLite migrations and data intact | All 19 database tables preserved | **PASS** |

---

## 5. Mandatory Safety Controls Verification

```
require_approval = true                        [CONFIRMED]
sending_paused = "Pilot Evaluation - Read-Only Safety Hold" [CONFIRMED]
sent_count = 0                                 [CONFIRMED]
Gmail permission = gmail.readonly              [CONFIRMED]

Original Pilot Drafts:
  - a02fed19ec8d: pending_review (Care Dental Clinic)       [INTACT]
  - 36dfa015198c: cancelled (Seraphic Dental Clinic)         [INTACT]
  - 3be7a27f3620: cancelled (Suryawanshi's Dental Care)     [INTACT]
  - fad4e4984339: pending_review (Smile Dental Clinic)      [INTACT]
```

