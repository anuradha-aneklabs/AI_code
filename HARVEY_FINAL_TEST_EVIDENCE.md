# Harvey AI Sales Agent — Final Test Evidence & Benchmark Log

**Document Version:** 1.0.0  
**Audit Date:** November 14, 2026  
**Project Path:** `C:\Users\aneka\Desktop\harvey`  
**Execution Environment:** Windows 11 Enterprise, Python 3.11.9 (`.venv`)  
**Git HEAD Commit:** `d9dd0dfc7ab43723aec775d447ac363386aa9b6f`  

---

## 1. Executive Summary

This document contains raw execution logs, exact test outputs, exit codes, benchmark figures, and before/after restart comparisons collected during the final stabilization phase of the Harvey AI Sales Agent.

---

## 2. Test Execution Records

### 2.1 Complete Pytest Regression Suite
- **Command:** `.venv\Scripts\pytest.exe -q`
- **Working Directory:** `C:\Users\aneka\Desktop\harvey`
- **Execution Timestamp:** 2026-11-14T11:41:00 IST
- **Exit Code:** `0`
- **Execution Log:**
  ```
  3154 passed, 184 skipped in 732.78s (0:12:12)
  ```
- **Test Metric Breakdown:**
  - Total Items Collected: `3,338`
  - Passed: `3,154`
  - Skipped: `184` (accounted for external API credentials / platform limits)
  - Failed: `0`
  - Errors: `0`

### 2.2 Testimonial Parser Performance Benchmark
- **Test File:** `tests/test_people_finder.py`
- **Target Test:** `test_nested_testimonials_are_bounded`
- **Assertion:** `assert time.monotonic() - start < 1.0` (preserved 100% intact)
- **Input:** 2,000 unclosed nested `<div class='testimonial'>` tags followed by character `'x'` and 2,000 closing tags.
- **Before Optimization:** `1.39s` (TIMED OUT)
- **After Optimization:** `0.0310s` (**45x faster**)
- **File Test Run:**
  ```
  .venv\Scripts\pytest.exe tests/test_people_finder.py -v
  ============================= 102 passed in 2.57s =============================
  ```

### 2.3 RAG Vector Store & Multi-Industry Test Runs
- **Command:** `.venv\Scripts\pytest.exe tests/test_rag.py tests/test_universal_multi_industry.py -v`
- **Exit Code:** `0`
- **Execution Log:**
  ```
  tests/test_rag.py::test_chunk_text_basic PASSED                          [  4%]
  tests/test_rag.py::test_chunk_text_empty PASSED                          [  8%]
  tests/test_rag.py::test_ingest_markdown PASSED                           [ 12%]
  tests/test_rag.py::test_ingest_txt PASSED                                [ 16%]
  tests/test_rag.py::test_ingest_multipage_pdf PASSED                      [ 20%]
  tests/test_rag.py::test_unsupported_file_extension PASSED                [ 24%]
  tests/test_rag.py::test_document_chunk_metadata PASSED                   [ 28%]
  tests/test_rag.py::test_rag_retriever_empty_prompt_context PASSED        [ 32%]
  tests/test_rag.py::test_rag_retriever_grounded_prompt_context PASSED     [ 36%]
  tests/test_rag.py::test_vector_store_lifecycle PASSED                    [ 40%]
  tests/test_rag.py::test_dashboard_rag_endpoints PASSED                   [ 44%]
  tests/test_rag.py::test_writer_rag_prompt_injection PASSED               [ 48%]
  tests/test_universal_multi_industry.py::test_default_profiles_seeded PASSED [ 52%]
  tests/test_universal_multi_industry.py::test_create_and_delete_custom_profile PASSED [ 56%]
  tests/test_universal_multi_industry.py::test_delete_default_profile_is_forbidden PASSED [ 60%]
  tests/test_universal_multi_industry.py::test_discover_legal_leads PASSED [ 64%]
  tests/test_universal_multi_industry.py::test_discover_accounting_leads PASSED [ 68%]
  tests/test_universal_multi_industry.py::test_discover_home_services_leads PASSED [ 72%]
  tests/test_universal_multi_industry.py::test_discover_generic_fallback PASSED [ 76%]
  tests/test_universal_multi_industry.py::test_qualification_scoring_across_industries PASSED [ 80%]
  tests/test_universal_multi_industry.py::test_sequence_generation_saas_has_no_clinical_copy PASSED [ 84%]
  tests/test_universal_multi_industry.py::test_sequence_generation_legal_grounded PASSED [ 88%]
  tests/test_universal_multi_industry.py::test_sequence_generation_home_services_hvac_grounded PASSED [ 92%]
  tests/test_universal_multi_industry.py::test_rag_retrieval_across_industries PASSED [ 96%]
  tests/test_universal_multi_industry.py::test_safety_invariants_preserved PASSED [100%]
  ============================= 25 passed in 115.47s =============================
  ```

---

## 3. Browser & Dashboard Audit Evidence

### 3.1 Playwright 15-Tab Full Navigation Audit
- **Command:** `.venv\Scripts\python.exe scratch/audit_browser_tabs.py`
- **Exit Code:** `0`
- **Output:**
  ```
  Navigating to http://127.0.0.1:5555/#token=...
  [01/15] Auditing Tab: 'today'...
         Result: PASS | Console: 0 | Network: 0 | Exceptions: 0
  [02/15] Auditing Tab: 'outbox'...
         Result: PASS | Console: 0 | Network: 0 | Exceptions: 0
  [03/15] Auditing Tab: 'conversations'...
         Result: PASS | Console: 0 | Network: 0 | Exceptions: 0
  [04/15] Auditing Tab: 'signals'...
         Result: PASS | Console: 0 | Network: 0 | Exceptions: 0
  [05/15] Auditing Tab: 'discover'...
         Result: PASS | Console: 0 | Network: 0 | Exceptions: 0
  [06/15] Auditing Tab: 'crm-pipeline'...
         Result: PASS | Console: 0 | Network: 0 | Exceptions: 0
  [07/15] Auditing Tab: 'companies'...
         Result: PASS | Console: 0 | Network: 0 | Exceptions: 0
  [08/15] Auditing Tab: 'prospects'...
         Result: PASS | Console: 0 | Network: 0 | Exceptions: 0
  [09/15] Auditing Tab: 'campaigns'...
         Result: PASS | Console: 0 | Network: 0 | Exceptions: 0
  [10/15] Auditing Tab: 'knowledge'...
         Result: PASS | Console: 0 | Network: 0 | Exceptions: 0
  [11/15] Auditing Tab: 'activity'...
         Result: PASS | Console: 0 | Network: 0 | Exceptions: 0
  [12/15] Auditing Tab: 'usage'...
         Result: PASS | Console: 0 | Network: 0 | Exceptions: 0
  [13/15] Auditing Tab: 'controls'...
         Result: PASS | Console: 0 | Network: 0 | Exceptions: 0
  [14/15] Auditing Tab: 'settings'...
         Result: PASS | Console: 0 | Network: 0 | Exceptions: 0
  [15/15] Auditing Tab: 'help'...
         Result: PASS | Console: 0 | Network: 0 | Exceptions: 0
  ===========================================================================
  Total Console Errors: 0
  Total Failed Network Requests: 0
  Total Uncaught Exceptions: 0
  ===========================================================================
  ```

---

## 4. Database Integrity & Cold-Restart Evidence

### 4.1 SQLite PRAGMA Checks
- **Command:** `.venv\Scripts\python.exe scratch/check_db_integrity.py`
- **Exit Code:** `0`
- **Output:**
  ```
  --- PRAGMA integrity_check ---
  [('ok',)]
  --- PRAGMA foreign_key_check ---
  []
  --- Settings ---
  sending_paused: Pilot Evaluation - Read-Only Safety Hold
  --- Outbox Sent Count ---
  sent_count: 0
  --- Preserved Pilot Drafts ---
    36dfa015198c: cancelled (To: drvishakha@seraphicclinic.com)
    3be7a27f3620: cancelled (To: varun.suryawanshi@gmail.com)
    a02fed19ec8d: pending_review (To: dentistgaganj@gmail.com)
    fad4e4984339: pending_review (To: smiledental364@gmail.com)
  [SUCCESS] All SQLite integrity and invariant checks passed!
  ```

### 4.2 Cold-Boot Restart State Comparison Table
Executed via `scratch/verify_actual_restart_persistence.py`:

| State Parameter | Before Process Kill | After Cold Restart | Match Status |
| :--- | :--- | :--- | :--- |
| **Business Profiles Count** | `7` | `7` | **EXACT MATCH** |
| **Discovered Leads Count** | `100` | `100` | **EXACT MATCH** |
| **Total Outbox Records** | `105` | `105` | **EXACT MATCH** |
| **Outbox Sent Count** | `0` | `0` | **EXACT MATCH** |
| **Outbox Pending Review** | `48` | `48` | **EXACT MATCH** |
| **Outbox Cancelled** | `52` | `52` | **EXACT MATCH** |
| **Pilot Draft `36dfa015198c`** | `cancelled` | `cancelled` | **EXACT MATCH** |
| **Pilot Draft `3be7a27f3620`** | `cancelled` | `cancelled` | **EXACT MATCH** |
| **Pilot Draft `a02fed19ec8d`** | `pending_review` | `pending_review` | **EXACT MATCH** |
| **Pilot Draft `fad4e4984339`** | `pending_review` | `pending_review` | **EXACT MATCH** |
| **`sending_paused` Value** | `Pilot Evaluation - Read-Only Safety Hold` | `Pilot Evaluation - Read-Only Safety Hold` | **EXACT MATCH** |
| **CRM Total Prospects** | `73` | `73` | **EXACT MATCH** |
| **Conversations Count** | `104` | `104` | **EXACT MATCH** |
| **RAG Documents Count** | `7` | `7` | **EXACT MATCH** |
| **RAG Chunks Count** | `19` | `19` | **EXACT MATCH** |

---

## 5. Live Business Workflow Execution Evidence

- **Command:** `.venv\Scripts\python.exe scratch/execute_fresh_workflow.py`
- **Exit Code:** `0`
- **Output:**
  ```
  ================================================================================
  HARVEY AI SALES AGENT — FRESH END-TO-END BUSINESS WORKFLOW
  ================================================================================
  [STEP 1] BUSINESS PROFILE VERIFICATION
    Target Profile: Dental Clinics & Practices (India) (ID: prof_dental_in)
    Industry: Healthcare / Dental | Target Geo: Indore, Madhya Pradesh, India

  [STEP 2] LIVE SPATIAL LEAD DISCOVERY (OPENSTREETMAP)
    OpenStreetMap returned 5 live businesses in 155172ms
    Discovered Entity: 'The Great Smile Dental Clinic' (Lead ID: a35ca2fe8b6a)
    Location: Juni Indore Tahsil, Madhya Pradesh | Source: https://www.openstreetmap.org/node/6376772801

  [STEP 3] WEBSITE RESEARCH & STRUCTURED FACT EXTRACTION
    Researched Target: The Great Smile Dental Clinic
    Unknown Fields Explicitly Tracked: ['Website content unreachable or blocked', 'Product catalog unverified', 'Annual revenue is Unknown', 'Exact employee headcount is Unknown']

  [STEP 4] ICP QUALIFICATION & 4-PILLAR BANT SCORING
    ICP Fit Score: 60/100 (Tier: Moderate Fit)
    BANT 4-Pillars -> Budget: 0/25 | Authority: 0/25 | Need: 25/25 | Timeline: 0/25
    BANT Total Score: 25/100 (Sum: 0 + 0 + 25 + 0 = 25)
    BANT Sales Verdict: Disqualified (ICP Qualified: False)

  [STEP 5] CRM PERSISTENCE & HONEST CONTACT PRESENTATION
    [CRM Company Upserted] ID: co_wf_86a34182 | Name: The Great Smile Dental Clinic | Domain: osm-0b582c.example.com
    [CRM Prospect Created] ID: pr_wf_0e2438fd | Name: Clinic Front Desk (Unverified Contact) | Email: contact@osm-0b582c.example.com (Status: synthetic)

  [STEP 6] LOCAL RAG PRODUCT KNOWLEDGE RETRIEVAL
    RAG Retrieved 2 relevant knowledge chunk(s):
      - Source: dental_product_guide.md | Score: 1.0 | Text: # Harvey AI Dental Practice Automation Guide...
      - Source: dental_product_guide.md | Score: 0.9 | Text: sk staff receives and confirms the scheduled slot...

  [STEP 7] GROUNDED AI OUTREACH DRAFT GENERATION
    Draft Generated Successfully:
      Draft ID: draft_894d90cb392e | Outbox ID: ec3e595281b9
      Subject: question regarding the great smile dental clinic
      Status: ready_for_review
      Recipient: contact@osm-0b582c.example.com (Status: synthetic)
      Body Preview: Hi The Great Smile Dental Clinic Team, I came across The Great Smile Dental Clinic...

  [STEP 8] HUMAN OPERATOR APPROVAL WORKFLOW
    Pre-approval Outbox Status: pending_review
    Post-approval Outbox Status: approved (approved_by: 'you')

  [STEP 9] FINAL SAFETY AUDIT & INVARIANT CHECKS
    outbox sent_count: 0 (Invariant: strictly 0)
    drafts sent_count: 0 (Invariant: strictly 0)
    sending_paused setting: 'Pilot Evaluation - Read-Only Safety Hold'
    Preserved Pilot Drafts (4/4): All 4 original statuses intact.
  ================================================================================
  ALL 9 STEPS OF FRESH BUSINESS WORKFLOW VERIFIED SUCCESSFULLY!
  ================================================================================
  ```

---

## 6. Verification Verdict

All tests, benchmarks, browser validations, database constraints, and end-to-end business workflows have been executed and verified with zero failures, zero regressions, and full compliance with safety holds.

