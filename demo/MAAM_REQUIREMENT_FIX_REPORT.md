# Ma'am Requirement Fix & Implementation Report

**Project:** Harvey Sales Development Platform  
**Target Directory:** `C:\Users\aneka\Desktop\harvey`  
**Branch:** `feature/research-pipeline`  
**Database Validated:** `data/validation/harvey_e2e.db` (Copy of real `data/harvey.db`)  
**Safety Status:** Real DB `data/harvey.db` untouched (SHA256 preserved: `012B390C227D030D3F52A01FE261E96672A681459BBF9C29BCA4F7964C9A7762`). Gmail Read-Only (`sent_count = 0`).

---

## 1. Summary of Changes & Fixes

| Requirement | Symptom Encountered | Root Cause | Files Changed | Status |
| :--- | :--- | :--- | :--- | :--- |
| **Req 3: Criteria & Location Gating** | User ran search with empty/default criteria or dental template defaults | Dashboard permitted unconfigured searches that searched default dentists or failed | `harvey/dashboard.py` | **FIXED** |
| **Req 4: Signal Confirmation Gate** | "Discovery stopped: no confirmed signals" appeared without easy UI action | 0/30 signals confirmed by default; dashboard required individual toggle per signal | `harvey/dashboard.py` | **FIXED** |
| **Req 5: Software Product vs Agency** | No distinction between SaaS products and IT service agencies/directories | Lack of product vs agency classification heuristic and DB schema columns | `config/software_classification.json`, `harvey/software_classifier.py`, `harvey/state.py`, `harvey/models/company.py`, `harvey/collectors/discover.py`, `harvey/collectors/profile.py` | **FIXED** |
| **Req 10: Robust Database Path Access** | `query_db` threw `AttributeError: 'str' object has no attribute 'exists'` when `DB_PATH` was set as string | Direct call to `.exists()` assumed `Path` instance | `harvey/dashboard.py` | **FIXED** |
| **Req 11: Public HTTP DNS Transport Resiliency** | `ProfileCollector` failed with `Website resolves to a non-public destination` on public domains (e.g. `github.com`) | Dual-stack / NAT64 DNS answers containing translation prefixes failed the whole resolution | `harvey/public_http.py` | **FIXED** |
| **Req 12: Integration Test-Connection Timeout** | `test_honest_provider_connection_reporting` failed on OpenStreetMap check | 8.0s timeout was exceeded by public Nominatim latency under peak internet load | `harvey/dashboard.py` | **FIXED** |
| **Req 13: Test Harness Fixture Collision** | `pytest` reported ERROR on setup of `test_module` in `tests/test_browser_module.py` | Standalone Playwright CLI runner was collected as a test function | `tests/test_browser_module.py` | **FIXED** |

---

## 2. Detailed Fix Breakdown

### Fix 1: Software Product vs Software Services Classification
- **Original Symptom:** Ma'am required discovery to specifically identify and prioritize software products/SaaS platforms while separating IT services, agencies, consultancies, directories, and non-software companies.
- **Root Cause:** Neither the database schema (`companies`, `discovery_results`) nor the discovery pipeline had a classification engine for software business models.
- **Files Changed:**
  - `config/software_classification.json`
  - `harvey/software_classifier.py`
  - `harvey/state.py` (Schema migration v32)
  - `harvey/models/company.py`
  - `harvey/collectors/discover.py`
  - `harvey/collectors/profile.py`
  - `tests/test_software_product_classification.py`
- **Fix Description:** Implemented rule-based scoring classifying targets as `software_product`, `software_services_agency`, or `other`, with evidentiary terms stored in `product_class_evidence`.
- **Exact Test Run:** `.\.venv\Scripts\pytest.exe -v tests/test_software_product_classification.py` -> 3 passed in 2.11s.
- **Status:** **FIXED**

---

### Fix 2: Template Criteria Refusal Gate
- **Original Symptom:** When users initiated discovery without configuring specific criteria or while default template values ("dentist") remained, discovery either failed or searched irrelevant legacy industries.
- **Root Cause:** `/api/discovery/search` lacked an input validation guard checking for empty criteria or template defaults before dispatching queries.
- **Files Changed:** `harvey/dashboard.py` (Lines 3201–3214), `tests/test_discovery_dashboard_improvements.py`
- **Fix Description:** The endpoint inspects incoming payload criteria; if criteria are empty or match legacy templates, it refuses execution with HTTP 200 and status `template_criteria_refused`, instructing the operator to provide genuine criteria (e.g., "SaaS" and "Indore, Madhya Pradesh, India").
- **Exact Test Run:** `.\.venv\Scripts\pytest.exe -v tests/test_discovery_dashboard_improvements.py::test_template_criteria_refusal` -> 1 passed in 2.14s.
- **Status:** **FIXED**

---

### Fix 3: One-Click Legit Signal Confirmation API
- **Original Symptom:** Discovery halted with `"stopped: no confirmed signals"`. Confirming 20+ free signals previously required individually toggling each signal or running CLI commands.
- **Root Cause:** `/api/signals/status` expected an explicit array of individual signal codes; it did not support batching by tier (`codes: ["free"]`).
- **Files Changed:** `harvey/dashboard.py` (Lines 4330–4345), `tests/test_discovery_dashboard_improvements.py`
- **Fix Description:** Passing `codes: ["free"]` to `POST /api/signals/status` queries the catalog for all signals with `cost_tier == "free"` (26 signals) and marks them `confirmed` in SQLite atomically.
- **Exact Test Run:** `.\.venv\Scripts\pytest.exe -v tests/test_discovery_dashboard_improvements.py::test_confirm_free_signals_api` -> 1 passed in 2.12s.
- **Status:** **FIXED**

---

### Fix 4: Resilient `DB_PATH` Type Handling in `query_db`
- **Original Symptom:** Tests monkeypatching `harvey.dashboard.DB_PATH = "path/to/db"` crashed with `AttributeError: 'str' object has no attribute 'exists'`.
- **Root Cause:** `if not DB_PATH.exists():` assumed `DB_PATH` was always a `pathlib.Path` instance.
- **Files Changed:** `harvey/dashboard.py` (Line 114)
- **Fix Description:** Wrapped in `if not Path(DB_PATH).exists():` to seamlessly handle both string paths and `Path` objects.
- **Exact Test Run:** `.\.venv\Scripts\pytest.exe -v tests/test_reply_intelligence_convo.py` -> 3 passed in 4.12s.
- **Status:** **FIXED**

---

### Fix 5: Public HTTP DNS Multi-Address Handling
- **Original Symptom:** `ProfileCollector` threw `Website resolves to a non-public destination` when fetching public web addresses like `github.com` on dual-stack or NAT64 ISP connections.
- **Root Cause:** When `socket.getaddrinfo` returned both translated IPv6 addresses (`64:ff9b::...`) and valid public IPv4 addresses (`20.207.73.82`), the generator expression in `PublicHTTPTransport` threw `ValueError` on the first non-public prefix, aborting the connection even though a valid public address was present.
- **Files Changed:** `harvey/public_http.py` (Lines 80–88)
- **Fix Description:** Filtered address results in a loop, gracefully discarding non-public translation addresses and selecting verified public IPv4 endpoints.
- **Exact Test Run:** `.\.venv\Scripts\python.exe scripts/verify_dashboard_isolated.py` -> `ProfileCollector` collected 10 observations on `github.com`.
- **Status:** **FIXED**

---

### Fix 6: OpenStreetMap Connection Timeout Resiliency
- **Original Symptom:** `test_honest_provider_connection_reporting` failed on OpenStreetMap status check with `Connection failed: `.
- **Root Cause:** OpenStreetMap Nominatim status check endpoint took 12.1s to respond over the public internet, exceeding the strict 8.0s timeout.
- **Files Changed:** `harvey/dashboard.py` (Line 5472)
- **Fix Description:** Increased the integration check client timeout to 15.0s.
- **Exact Test Run:** `.\.venv\Scripts\pytest.exe -v tests/test_dashboard_health_and_summary.py` -> 3 passed in 13.89s.
- **Status:** **FIXED**

---

### Fix 7: Playwright CLI Runner Pytest Collection Error
- **Original Symptom:** Full pytest suite run reported `ERROR at setup of test_module: fixture 'module_name' not found`.
- **Root Cause:** The untracked CLI runner script `tests/test_browser_module.py` started with `test_` and contained function `test_module`, causing pytest to attempt collecting it as a test function.
- **Files Changed:** `tests/test_browser_module.py`
- **Fix Description:** Added `__test__ = False` to the top of the file so pytest ignores the CLI runner during test collection.
- **Exact Test Run:** `.\.venv\Scripts\pytest.exe -v tests/test_browser_module.py` -> 0 errors, skipped during test collection.
- **Status:** **FIXED**
