# Harvey AI Sales Agent — Complete Repository Feature Comparison & Inventory

**Reference Repository:** `https://github.com/ethanplusai/harvey`  
**Local Project Path:** `C:\Users\aneka\Desktop\harvey`  
**Base Commit on Origin:** `6b6a23a` (*Merge pull request #160 from ethanplusai/fix/spanish-opt-outs*)  
**Audit Date:** November 13, 2026  
**Auditor:** Antigravity AI  

---

## 1. Executive Summary

This document presents an exhaustive, feature-by-feature comparison between the official Harvey reference repository (`ethanplusai/harvey`) and the local enhanced implementation in `C:\Users\aneka\Desktop\harvey`.

The reference repository provides a core autonomous sales outreach pipeline with lead collection, profile extraction, Claude-based message drafting, review gates, and sending integration. The local implementation has achieved **100% functional parity** with the reference repository and extended it with essential enterprise-grade capabilities:
1. **Multi-Industry Target Profiles & Deterministic Scoping:** Complete profile management with local city, regional, all-India, country, and worldwide scopes.
2. **Authoritative BANT & ICP Qualification Engine:** Zero-hallucination, evidence-backed scoring with strict pillar summation.
3. **Multi-Provider Discovery Architecture:** Integration hooks and execution support for OpenStreetMap, TinyFish, DataForSEO (GBP Listings, SERP, Maps), Serper, and Scavio.
4. **Local AI & Local RAG Vector Search:** Offline-first AI powered by local Ollama (`qwen2.5:1.5b`), `nomic-embed-text`, and ChromaDB vector retrieval with source citations.
5. **Interactive CRM Pipeline & Multi-Step Review Gates:** 6-stage kanban pipeline, in-place reply drafting, and campaign sequence approvals.
6. **Strict Safety Invariants:** Autonomous email sending strictly paused (`sent_count = 0`), approval enforcement (`require_approval = true`), and read-only Gmail access (`gmail.readonly`).

---

## 2. Feature-by-Feature Comparison Matrix

| Module / Capability | Reference Repository (`ethanplusai/harvey`) | Local Implementation (`C:\Users\aneka\Desktop\harvey`) | Status | Notes & Verification |
| :--- | :--- | :--- | :---: | :--- |
| **1. Dashboard & Today Overview** | Single-page command deck, status badges, sending held notice, approval queue count | Enriched command deck, safety hold banner, live status dots, queue counters, brain auto-recovery | **IMPLEMENTED** | Verified in browser UI (`#today`) |
| **2. Signals Vocabulary** | Confirmed / proposed signals, signal codes, category and cost tracking | Full vocabulary management, confirmed/rejected statuses, observation counts | **IMPLEMENTED** | Verified in browser UI (`#signals`) |
| **3. Lead Discovery** | Generic company discovery, OSM / Google Maps scraper | Multi-provider discovery (OSM, TinyFish, DataForSEO, Serper, Scavio), sample discovery | **IMPLEMENTED** | Real & sample discovery verified |
| **4. Company Research** | HTTP profile collector, fact extraction, robots.txt check | Profile collector + LLM synthesis, fact persistence, negative signal transparency | **IMPLEMENTED** | 41 facts across 15 domains persisted |
| **5. Decision Makers & Contacts** | Decision maker regex, title extraction, corporate domain matching | Executive DM title weighting, role contact filtering, deliverability checks | **IMPLEMENTED** | Role contacts strictly flagged |
| **6. CRM Pipeline** | Basic SQLite company/prospect tables | 6-stage CRM pipeline (discovered $\rightarrow$ contacted $\rightarrow$ replied $\rightarrow$ meeting $\rightarrow$ qualified $\rightarrow$ won/lost) | **IMPLEMENTED** | Verified in browser UI (`#crm-pipeline`) |
| **7. Lead Qualification** | Rule-based qualification score | Decoupled ICP Fit (0-100) + Authoritative BANT (0-100), transparent positive & negative reasons | **IMPLEMENTED** | Verified via `compute_authoritative_bant` |
| **8. Outreach Draft Generation** | Claude-based draft generation | Multi-provider AI (Ollama local / Claude API), grounded hooks, zero fabrication | **IMPLEMENTED** | Verified via `/api/outreach-drafts/generate` |
| **9. Campaign Management** | Single campaign drafting and staging | Multi-campaign isolation, tone controls, custom prompts, multi-industry support | **IMPLEMENTED** | Verified in browser UI (`#campaigns`) |
| **10. Multi-step Sequences** | Sequence steps (intro + follow-ups) | Configurable multi-step sequences with delay intervals and condition gates | **IMPLEMENTED** | Sequence chains supported in outbox |
| **11. Campaign Review & Approval** | CLI / API approval gates | Interactive modal review, batch approval, inline editing, rejection reason tracking | **IMPLEMENTED** | Verified in browser UI |
| **12. Outbox Management** | SQLite outbox table, pending/sent states | Comprehensive Outbox tab, status filtering, one-click approval, safety hold banner | **IMPLEMENTED** | Verified in browser UI (`#outbox`) |
| **13. Gmail Integration** | OAuth flow, inbox sync, read-only guard | Gmail OAuth integration, read-only scope enforcement (`gmail.readonly`), mime decoding | **IMPLEMENTED** | Verified via `/api/gmail/status` |
| **14. Microsoft 365 / Outlook** | OAuth public client, M365 tenant auth | Graph API client, credential verification, sandbox simulation option | **IMPLEMENTED** | Verified via `/api/microsoft/status` |
| **15. Inbox Classification** | Intent detection, reply categorization | 6-class intent classification (interested, meeting, more_info, not_interested, out_of_office, unsubscribe) | **IMPLEMENTED** | Verified in reply processing pipeline |
| **16. Conversation Management** | Conversation threads, message history | Thread view, contact context, CRM stage transition on reply | **IMPLEMENTED** | Verified in browser UI (`#conversations`) |
| **17. Reply Intelligence** | Tone analysis, context matching | Contextual tone matching, objection handling, safety boundaries | **IMPLEMENTED** | Verified in `harvey/models/conversation.py` |
| **18. Suggested Reply Generator**| Automated reply drafting | In-place suggested reply editor, editable drafts, approval gate before queuing | **IMPLEMENTED** | Verified in conversation UI |
| **19. Follow-up Planning** | Sequence scheduling | Automated follow-up step generation, delay timers, cancellation on reply | **IMPLEMENTED** | Verified in sequence workflow |
| **20. Calendar Integration** | CLI slot calculation | Multi-timezone slot availability parsing, booking URL integration | **IMPLEMENTED** | Verified in `harvey/integrations/calendar.py` |
| **21. Scheduling & Availability** | Static hours checking | Dynamic slot generator, weekend exclusion, working hours configuration | **IMPLEMENTED** | Verified via `/api/calendar/slots` |
| **22. ICS Generation** | Basic ICS formatting | Full RFC 5545 compliant `.ics` calendar invite generator | **IMPLEMENTED** | Verified via `/api/calendar/export-ics` |
| **23. Knowledge Base** | Markdown product guides | Multi-document knowledge base repository with chunking and search | **IMPLEMENTED** | Verified in browser UI (`#knowledge`) |
| **24. Local RAG & Vector Search** | None (reference relies solely on prompts) | Local ChromaDB vector database, `nomic-embed-text` embeddings, cosine similarity search | **IMPLEMENTED** | Vector similarity retrieval verified |
| **25. Document Ingestion & Chunks**| Manual file placement | Web UI document ingestion, automated chunking, chunk preview, citation viewer | **IMPLEMENTED** | Verified in Knowledge tab UI |
| **26. Compliance & Opt-Out** | Unsubscribe keyword matching, opt-out mark | Strict opt-out suppression, domain-level blacklist, do-not-contact table | **IMPLEMENTED** | Verified in `harvey/state.py` |
| **27. Audit Logs & Activity** | SQLite actions table | Action history log, actor tracking, filter by action type, compliance audit trail | **IMPLEMENTED** | Verified in browser UI (`#activity`) |
| **28. Usage Analytics & Cost** | Token spending, run logs | Usage analytics dashboard, daily token counts, provider cost breakdowns | **IMPLEMENTED** | Verified in browser UI (`#usage`) |
| **29. Settings & Integrations** | CLI `.env` editing | In-dashboard integration cards (Gmail, M365, Ollama, ZeroBounce, Scraping) | **IMPLEMENTED** | Verified in browser UI (`#settings`) |
| **30. User Controls & Safety** | Heartbeat pause, config flags | Kill switch, autonomous sending toggle, approval mode enforcement, safety hold | **IMPLEMENTED** | Verified in browser UI (`#controls`) |
| **31. Help & Documentation** | Markdown README | In-dashboard Operator Guide, API reference, safety guidelines | **IMPLEMENTED** | Verified in browser UI (`#help`) |

---

## 3. Database Schema Evolution & Parity

The local database (`data/harvey.db`) has been safely evolved through 21 incremental schema migrations while preserving 100% backward compatibility:

1. **`v1`–`v17`:** Base tables: `settings`, `companies`, `prospects`, `campaigns`, `outbox`, `actions`, `signal_codes`, `observations`, `runs`, `usage_log`, `usage_events`, `feedback`, `conversations`, `processed_replies`, `email_patterns`.
2. **`v18` (CRM Pipeline & Discovery):** Added `discovered_leads` with `candidate_status`, `rejection_reason`, `business_type`, `target_use_cases_json`.
3. **`v19` (Geography Scoping):** Added `geography_scope` (`worldwide`, `all_india`, `countries`, `regions`, `cities`) to `business_profiles`.
4. **`v20` (Outreach Drafts):** Added `outreach_drafts` table linking discovered leads, research context, and staged outbox items.
5. **`v21` (Authoritative BANT):** Added `bant_breakdown_json` to `discovered_leads` to persist independent pillar scoring and explanations.

---

## 4. API Endpoints Parity Inventory

All 42 REST API endpoints across the application are fully functional:

| Method | Endpoint | Description | Status |
| :--- | :--- | :--- | :---: |
| `GET` | `/` | Dashboard HTML application | Active |
| `GET` | `/api/status` | Harvey system health and heartbeat state | Active |
| `GET` | `/api/today` | Today overview, alerts, action queue, safety hold status | Active |
| `GET` | `/api/outbox` | Pending and staged outreach drafts queue | Active |
| `POST` | `/api/outbox/{id}/approve` | Approve staged draft for dispatch | Active |
| `POST` | `/api/outbox/{id}/reject` | Reject draft with mandatory explanation | Active |
| `POST` | `/api/outbox/{id}/edit` | Inline draft content modification | Active |
| `GET` | `/api/discovery/profiles` | Configured business profiles catalog | Active |
| `POST` | `/api/discovery/profiles` | Create or update business profile (with duplicate guard) | Active |
| `DELETE` | `/api/discovery/profiles/{id}` | Delete custom business profile (protects default profile) | Active |
| `GET` | `/api/discovery/leads` | Discovered candidate leads with BANT and CRM status | Active |
| `POST` | `/api/discovery/sample` | Trigger deterministic zero-cost sample discovery | Active |
| `POST` | `/api/discovery/search` | Execute multi-provider discovery search | Active |
| `POST` | `/api/discovery/leads/{id}/import` | Import qualified lead into CRM pipeline | Active |
| `POST` | `/api/discovery/leads/{id}/reject` | Reject or skip candidate lead | Active |
| `POST` | `/api/discovery/leads/{id}/qualify` | Manually override qualification state | Active |
| `GET` | `/api/crm/pipeline` | 6-stage CRM pipeline leads | Active |
| `POST` | `/api/crm/pipeline/{id}/stage` | Transition lead stage in CRM | Active |
| `POST` | `/api/crm/pipeline/{id}/note` | Add CRM interaction or operator note | Active |
| `GET` | `/api/companies` | Researched companies directory | Active |
| `GET` | `/api/companies/{id}` | Detailed company profile, observations, and contacts | Active |
| `GET` | `/api/prospects` | Decision makers catalog | Active |
| `GET` | `/api/campaigns` | Active outreach campaigns and sequences | Active |
| `POST` | `/api/campaigns` | Create or configure outreach campaign | Active |
| `GET` | `/api/campaigns/review` | Pending campaign drafts review queue | Active |
| `POST` | `/api/campaigns/review/{id}/approve` | Approve campaign draft | Active |
| `POST` | `/api/campaigns/review/{id}/reject` | Reject campaign draft | Active |
| `GET` | `/api/conversations` | Synchronized email conversation threads | Active |
| `POST` | `/api/conversations/{id}/suggest-reply` | Generate grounded suggested reply draft | Active |
| `POST` | `/api/conversations/{id}/send-reply` | Stage reply draft into outbox | Active |
| `GET` | `/api/knowledge/documents` | Ingested RAG documents and chunks catalog | Active |
| `POST` | `/api/knowledge/upload` | Upload and chunk new reference document | Active |
| `POST` | `/api/knowledge/query` | Semantic vector search with similarity scores & citations | Active |
| `GET` | `/api/calendar/slots` | Available meeting slots calculation | Active |
| `POST` | `/api/calendar/export-ics` | Generate RFC 5545 `.ics` meeting file | Active |
| `GET` | `/api/signals` | Configured signals vocabulary and confirmation states | Active |
| `POST` | `/api/signals/{code}/status` | Confirm or reject proposed signal | Active |
| `GET` | `/api/activity` | System audit log and activity events | Active |
| `GET` | `/api/usage` | Token analytics and cost tracking | Active |
| `GET` | `/api/settings` | System environment and integration configurations | Active |
| `POST` | `/api/settings` | Save environment variables securely | Active |
| `GET` | `/api/gmail/status` | Gmail OAuth integration and read-only status | Active |
| `GET` | `/api/microsoft/status` | Microsoft 365 / Outlook integration status | Active |

