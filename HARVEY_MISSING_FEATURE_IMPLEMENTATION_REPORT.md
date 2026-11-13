# Harvey AI Sales Agent — Missing Feature Implementation Report

**Project Path:** `C:\Users\aneka\Desktop\harvey`  
**Reference Repository:** `https://github.com/ethanplusai/harvey`  
**Date:** November 13, 2026  
**Status:** All Planned & Reference Features Implemented End-to-End  

---

## 1. Overview & Objectives

This report documents the end-to-end implementation of all missing, incomplete, and audited features required to achieve complete feature parity with the reference repository and satisfy all operator requirements:
1. Target Business Profiles management and duplicate prevention.
2. Resolution of the duplicate "CleanTech Solar & Storage EPC" profile issue.
3. Multi-tier geography matching and deterministic scoping.
4. Industry mismatch penalties and hard exclusions.
5. Authoritative, evidence-backed BANT scoring engine (`compute_authoritative_bant`).
6. CRM duplicate detection and tab counter synchronization.
7. Decision-maker role contact and generic mailbox filtering.
8. Interactive CRM pipeline and review workflows.
9. Grounded outreach drafting with local LLM integration.
10. Strict safety invariant enforcement.

---

## 2. Implemented Features & Architectural Improvements

### A. Target Business Profiles Management & Duplication Guard
- **The Issue:** Submitting or seeding business profiles with the same name repeatedly generated new random IDs, resulting in 5 duplicate "CleanTech Solar & Storage EPC" profiles in the database and UI selector.
- **Backend Fix (`harvey/state.py`):**
  - Updated `create_business_profile()`: when no explicit `id` is passed, queries for existing profiles by `LOWER(TRIM(name)) = LOWER(TRIM(?))`. If found, reuses that ID and updates the record in-place instead of creating a duplicate.
  - Updated `get_business_profiles()`: filters rows with a deduplication set on normalized names.
  - Database cleanup: consolidated the 5 duplicate CleanTech rows into single canonical ID `prof_329svqpt`, re-linked attached leads, and purged redundant records.
- **Frontend UI (`harvey/web/app.js` & `harvey/web/index.html`):**
  - Implemented `toggleProfileEditor()`, `openCreateProfile()`, `openEditProfile()`, `saveProfileFromEditor()`, and `deleteCurrentProfile()`.
  - Added `#profile-criteria-summary` pills rendering industry, target geography, team size, target titles, and default profile badge.
  - Added delete protection: default business profiles cannot be deleted.

### B. Multi-Tier Geography Scoping
- **The Issue:** Target profile for Indore dental clinics erroneously awarded +20 points to Pune and New Delhi due to silent fallback to `worldwide` scoping.
- **Backend Fix (`harvey/qualification.py`):**
  - Refactored `LeadQualifier.qualify()` to dynamically resolve scope based on target geography contents:
    - `cities`: Exact target city matches receive **+20 pts**, regional proximity (same state) receives **+8 pts**, unrelated cities receive **0 pts**.
    - `all_india`: Any Indian location receives **+20 pts**; foreign locations receive **0 pts**.
    - `countries`: Locations in selected target countries receive **+20 pts**.
    - `worldwide`: Global locations receive **+20 pts** only when explicitly configured.
  - Result: Under Indore city targeting, Indore clinics receive +20 pts, Central India Maxillofacial (Bhopal) receives +8 pts, and Pune / New Delhi receive 0 pts.

### C. Industry Mismatch Penalties & Hard Exclusions
- **The Issue:** Tertiary hospitals (*Apex Global Hospital Network*, 4,500+ employees) scored high fit for SMB dental clinic tooling.
- **Backend Fix (`harvey/qualification.py`):**
  - Added strict sector divergence detection: *Tertiary Hospital Care* vs. *Healthcare / Dental* receives **0 Sector Match points** and **0 Need points**.
  - Enterprise headcount (4,500+) evaluating an SMB profile receives **0 Budget points**.
  - Decision-maker role title (*"Hospital Administration"*) and generic mailbox (*"info@"*) receive **0 Authority points** and **0 Timeline points**.
  - Total ICP score drops from 50 to **0 · Low Fit**, and BANT drops from 80/100 to **0/100 Disqualified**.

### D. Authoritative BANT Score Integrity
- **Implementation:** Created `compute_authoritative_bant()` in `harvey/qualification.py`.
- **Invariants Enforced:**
  - $\text{BANT Total} = \text{Budget} + \text{Authority} + \text{Need} + \text{Timeline}$ strictly.
  - Each pillar is independently scored between 0 and 25 points.
  - Missing data receives **0 points** (no free baseline credit).
  - Persisted to SQLite via migration `v21` (`bant_breakdown_json`).

### E. CRM Duplicate Detection & Counter Synchronization
- **The Issue:** Discovered lead cards displayed *"⚠️ Already exists in CRM"*, but the "In CRM" tab counter showed 0.
- **Backend Fix (`harvey/dashboard.py`):**
  - In `/api/discovery/leads`, total, pending, in-CRM, and rejected counters are computed over the **unfiltered** lead set before status slicing.
  - Leads with `in_crm = True` or `added_to_crm = 1` are excluded from the Pending Review tab and partitioned into the In CRM tab.
  - Counters and rendered cards are 100% synchronized.

### F. Multi-Step Campaign Review & Approval Modal
- **Implementation:** Created `harvey/campaign_review.py` and connected `/api/campaigns/review` endpoints.
- **UI Components:**
  - Campaign Review modal displaying sequence step, recipient domain, subject line, generated body, and grounded research facts.
  - Reject modal requiring mandatory operator justification.
  - In-place edit capability allowing operator adjustments before dispatch.

### G. Local AI & Semantic RAG Integration
- **Implementation:** Added local Ollama preflight synchronization in `harvey/brain.py` and `harvey/dashboard.py`.
- **Auto-Recovery:** When Ollama is restarted, Harvey automatically clears the paused banner without manual database edits.
- **Vector Search:** Local ChromaDB collection query with `nomic-embed-text` embeddings and source citations.

---

## 3. Database Migrations Applied

| Migration | Version | Target Table | Description |
| :--- | :---: | :--- | :--- |
| `v18` | 18 | `discovered_leads` | Schema for candidate leads, review status, business type, and target use cases |
| `v19` | 19 | `business_profiles` | Added `geography_scope` column (`worldwide`, `all_india`, `countries`, `regions`, `cities`) |
| `v20` | 20 | `outreach_drafts` | Added dedicated outreach drafts tracking table with review gates |
| `v21` | 21 | `discovered_leads` | Added `bant_breakdown_json` to persist authoritative BANT pillar scores |

---

## 4. Key Files Modified & Created

### Core Python Modules:
- `harvey/qualification.py`: Added `ROLE_MAILBOX_PREFIXES`, `ROLE_NAME_PATTERNS`, `ROLE_TITLE_PATTERNS`, `is_role_contact()`, `is_role_mailbox()`, `compute_authoritative_bant()`, and dynamic scope resolution.
- `harvey/state.py`: Implemented migration `v21`, profile deduplication in `create_business_profile()` and `get_business_profiles()`, and `bant_breakdown_json` persistence.
- `harvey/dashboard.py`: Fixed `/api/discovery/leads` dynamic qualification refresh and pre-filter counter aggregation; implemented profile CRUD endpoints.
- `harvey/collectors/profile.py`: Enhanced negative observation tracking with explicit `"Not detected"` text.
- `harvey/campaign_review.py`: Full review and approval workflow for campaign drafts.
- `harvey/outreach_drafts.py`: AI-assisted outreach draft generation grounded in verified company research.

### Frontend Web UI:
- `harvey/web/app.js`: Added profile editor handlers (`toggleProfileEditor`, `openCreateProfile`, `openEditProfile`, `saveProfileFromEditor`, `deleteCurrentProfile`), profile criteria summary rendering, and enriched BANT badges with tooltips.
- `harvey/web/index.html`: Wired up profile action buttons, profile editor modal panel, and criteria summary badges.
- `harvey/web/app.css`: Added styles for duplicate warning banners, BANT score chips, and criteria badges.

### Automated Test Suites:
- `tests/test_discover_audit_fixes.py`: 17 comprehensive tests for geography, BANT integrity, role contacts, and CRM deduplication.
- `tests/test_bant_qualification.py`: Tests for independent pillar calculations and qualification overrides.
- `tests/test_lead_discovery_crm_pipeline.py`: CRM pipeline transitions and lead review tests.
- `tests/test_campaign_review_approval.py`: Review modal, approval gates, and reject reason tests.
- `tests/test_outreach_drafts.py`: AI outreach drafting and safety tests.
- `tests/test_full_system_completion.py`: Full system integration and invariant tests.

