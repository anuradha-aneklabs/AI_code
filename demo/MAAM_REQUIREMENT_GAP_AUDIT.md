# Ma'am Requirement Gap Audit

**Date:** 2026-11-23  
**Target Repository:** `C:\Users\aneka\Desktop\harvey`  
**Evaluation Scope:** Ma'am's Lead Discovery & Batch Research System Requirements  

---

## Comprehensive Requirement Gap Audit Table

| Requirement | Status | Evidence (File:Line or Command Output) | Identified Gap / Operational Note |
|:---|:---:|:---|:---|
| **1. SaaS + Indore Applied** | **PARTIAL** | `harvey.yaml:31-42` defaults to `dentist` in `icp.industries`; `harvey/gate.py` refuses template defaults. Overridden dynamically via `--industry SaaS --location "Indore, Madhya Pradesh, India"` or `/api/discovery/criteria`. | Base `harvey.yaml` ships with default template ("dentist"); dynamic runtime override required so discovery does not trip refusal gate. |
| **2. Relevant Queries** | **PASS** | `harvey/collectors/discover.py:2177` (`build_queries(config)`); generated queries for SaaS in Indore include "SaaS companies in Indore", "software product companies Indore". | Handled cleanly; query expansion generates focused search queries within budget limit (<= 4 queries). |
| **3. Real Provider** | **PASS** | `harvey/collectors/discover.py:1075` (`class TinyFish(DiscoveryProvider)`); `ENDPOINT = "https://api.search.tinyfish.ai"`. Paced at 30 req/min, free tier ($0 spend). | Approved provider TinyFish search is functional; key in `.env`; strictly capped at max 12 requests. Paid providers (Serper, Scavio, DataForSEO) disabled. |
| **4. Company & Website Verification** | **PASS** | `harvey/batch_research.py:180-220`; HTTP GET/HEAD verification with redirect resolution and status code logging. | Successfully validates live websites, records final canonical URL and HTTP response code. |
| **5. Overview / Services** | **PASS** | `harvey/company_narrative.py:150-250` & `harvey/batch_research.py:260-310`; cleans bullet points and creates discrete service tags/chips. | Extracts concise corporate overview, mission, and distinct core services. |
| **6. Decision Makers with Evidence** | **PASS** | `harvey/decision_makers.py:120-210`; extracts officer roles, titles, confidence score, verbatim quote context (~300 chars), and origin URL. | Decision makers surfaced with verified citations and honest "unavailable" status when absent. |
| **7. Signal Lifecycle & Confirmation** | **PASS** | `harvey/collectors/discover.py:2203-2210`; `run_discovery` checks `state.confirmed_signal_codes()`. Signals proposed in `harvey/signals.py:20-400`. | Signals start as "proposed"; discovery is gated until user confirms free signals via `harvey signals --confirm free` or dashboard API. |
| **8. Progressive Persistence** | **PASS** | `harvey/batch_research.py:320-380`; updates `import_rows`, `import_domains`, `company_websites`, `research_findings`, and `research_people` per domain row. | Each domain result is committed to SQLite immediately upon completion; interrupted jobs can resume without data loss. |
| **9. Duplicate Handling** | **PASS** | `harvey/batch_research.py:330-345` & `harvey/collectors/discover.py:2230`; domain normalization via `_norm` and SLD matching; duplicate domains share research run with masked email. | Prevents redundant web requests and duplicate company rows while preserving individual contact associations. |
| **10. Evidence URLs** | **PASS** | `harvey/batch_research.py:410-440`; stores `source_url` for company findings, leadership profiles, and verified domains. | All extracted facts, officer titles, and website evidence maintain explicit URL provenance. |
| **11. Progress & Error Display** | **PASS** | `harvey/dashboard.py:688` (`/api/research/batch/{id}`) & `harvey/web/app.js:1450-1520`; live progress bar with percentage and real-time status banner. | UI actively polls progress, displays error chips on failures, and provides a resume button for incomplete batches. |
| **12. Sample Data Excluded** | **PASS** | `harvey/gate.py:50-80`; hardcoded fallback generators disabled in real provider mode; template criteria refusal gate active. | Real provider responses are ingested and persisted directly; synthetic mock data strictly isolated to unit test suites. |

---

## Summary of Findings

- **Zero Blocking Architectural Gaps:** The underlying pipeline implements all 12 core requirements.
- **Key Operational Flow:** Discovery must be executed with runtime criteria ("SaaS", "Indore") and free signals must be confirmed prior to running live discovery to satisfy the discovery safety gate.
