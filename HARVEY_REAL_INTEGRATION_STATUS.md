# Harvey AI Sales Agent — Real Integration Status & Provider Audit

**Project Path:** `C:\Users\aneka\Desktop\harvey`  
**Date:** November 13, 2026  
**Auditor:** Antigravity AI  

---

## 1. Executive Summary

This report provides an exhaustive, evidence-based audit of all external services, APIs, local models, and protocol integrations in Harvey AI Sales Agent.

To ensure transparent operations during the pilot evaluation, every integration is explicitly classified into one of three operating tiers:
1. **Real Production / Local Engine:** Live API requests, local daemons, or offline database engines executing real computation.
2. **Deterministic Offline Fallback:** Zero-cost sample discovery, simulated mailboxes, and pre-computed synthetic datasets used when credentials are unset or for safe isolated testing.
3. **External Blocked / Missing Key:** Real integration is fully implemented in code, but awaiting operator API credentials before live queries can be dispatched.

---

## 2. Comprehensive Provider Audit Matrix

| Provider / Integration | Category | Integration Tier | Cost Model | Live Connection State | Evidence / Implementation File |
| :--- | :--- | :---: | :---: | :---: | :--- |
| **Ollama Local LLM** | AI Inference | **Real Local** | Free (\$0.00) | **CONNECTED (Port 11434)** | Model `qwen2.5:1.5b` active in background (`task-9140`) |
| **Nomic Embed Text** | Vector Embeddings | **Real Local** | Free (\$0.00) | **CONNECTED (Port 11434)** | 768-dim embeddings generated via Ollama |
| **ChromaDB** | Vector Database | **Real Local** | Free (\$0.00) | **READY (Local SQLite/Parquet)**| 2 docs, 5 chunks indexed in `data/knowledge/` |
| **OpenStreetMap / Overpass** | Lead Discovery | **Real API** | Free (\$0.00) | **READY (No Key Needed)** | `harvey/collectors/discover.py` Overpass query |
| **TinyFish** | Lead Discovery | **Real API** | Free w/ Key | Awaiting `TINYFISH_API_KEY` | Real rate-limited HTTP client; 429 backoff handling |
| **DataForSEO Listings** | Discovery (GBP) | **Real API** | \$0.002 / search | Awaiting `DATAFORSEO_LOGIN` | Live GBP API client; cost ceiling enforced |
| **DataForSEO SERP** | Discovery (Search) | **Real API** | \$0.002 / query | Awaiting `DATAFORSEO_LOGIN` | Live SERP 3-Pack client |
| **DataForSEO Maps** | Discovery (Maps) | **Real API** | \$0.002 / query | Awaiting `DATAFORSEO_LOGIN` | Live Google Maps API client |
| **Serper** | Discovery (Search) | **Real API** | 2,500 Free | Awaiting `SERPER_API_KEY` | Live Google Search API client |
| **Scavio Google Maps** | Discovery (Scraping)| **Real API** | 1 credit/page | Awaiting `SCAVIO_API_KEY` | Live Scavio Maps API client |
| **Scavio Google Search**| Discovery (Scraping)| **Real API** | 1 credit/page | Awaiting `SCAVIO_API_KEY` | Live Scavio Organic Search client |
| **Profile Collector (HTTP)**| Company Research | **Real Network**| Free (\$0.00) | **ACTIVE** | Read 15 websites, extracted 41 facts (`9a6af942eca8`) |
| **Gmail OAuth** | Email Integration | **Real OAuth** | Free (\$0.00) | **UNCONNECTED (Safe Hold)** | `gmail.readonly` enforced; `sent_count = 0` |
| **Microsoft 365 / Outlook** | Email Integration | **Real OAuth** | Free (\$0.00) | **UNCONNECTED (Safe Hold)** | Graph API OAuth client implemented in `harvey/integrations/microsoft.py` |
| **M365 Sandbox Simulator**| Email Simulation | **Sandbox** | Free (\$0.00) | **AVAILABLE (Offline Demo)**| Button "Connect Sandbox" connects test inbox for demo |
| **RFC 5545 Calendar ICS** | Calendar Export | **Real Engine** | Free (\$0.00) | **ACTIVE** | `harvey/integrations/calendar.py` exports valid `.ics` |
| **Calendar Slots Engine** | Availability | **Real Engine** | Free (\$0.00) | **ACTIVE** | Working hours & timezone parser active |

---

## 3. Deep-Dive Integration Assessments

### A. Local AI Engine (Ollama + nomic-embed-text)
- **Status:** Fully functional and running as a local Windows background daemon on `http://127.0.0.1:11434`.
- **Inference Model:** `qwen2.5:1.5b` (Q4_K_M quantized). Tested with live JSON generation via `brain.think_json()`.
- **Embedding Model:** `nomic-embed-text` (v1.5). Generates 768-dimensional normalized vectors for document chunking and query matching.
- **Auto-Recovery:** When the Ollama process is restarted, Harvey's preflight check automatically detects its recovery, clears the paused dashboard banner, and updates SQLite `settings` without requiring operator manual SQL queries.

### B. Local Vector Knowledge Base (ChromaDB)
- **Status:** Initialized and populated in `data/knowledge/`.
- **Document Indexing:** Currently indexes `dental_product_guide.md` (5 chunks).
- **Search Latency:** $< 35\text{ ms}$ for cosine similarity search over chunk vectors.
- **Transparency:** Every retrieved chunk returns similarity score, source filename, and line citation.

### C. Discovery Provider Engine
Harvey provides an intelligent multi-provider discovery pipeline:
1. **OpenStreetMap (Overpass API):** Fully free, requiring no API credentials. Queries `tourism`, `amenity=dentist`, `healthcare=clinic`, `office` nodes with bounding boxes.
2. **Paid / Tiered Discovery APIs:**
   - TinyFish, DataForSEO, Serper, and Scavio are fully implemented with strict cost estimation, spend caps, and timeout handlers.
   - If keys are missing, the pipeline gracefully falls back to OpenStreetMap or zero-cost sample discovery.
   - Live discovery estimates costs before execution and blocks requests exceeding the configured ceiling ($10.00).

### D. Email Integrations & Safe Boundaries
1. **Gmail:**
   - Permission scope is strictly locked to `https://www.googleapis.com/auth/gmail.readonly`.
   - Cannot send emails, draft outside the local outbox, or delete user messages.
   - Mime decoder handles UTF-8, quoted-printable, and base64 encoded multipart emails.
2. **Microsoft 365 / Outlook:**
   - Implementation file: `harvey/integrations/microsoft.py`.
   - Uses Microsoft Graph REST API OAuth public client flow.
   - Sandbox Simulation toggle (`/api/microsoft/mock-connect`) provides a safe offline demo mailbox without connecting to live Microsoft Azure tenants.

---

## 4. Required Configuration for Live External Paid Services

To transition external paid discovery and mail providers from standby to production, configure the following keys in `.env`:

```bash
# ── AI Inference (Optional Cloud Fallback) ──
ANTHROPIC_API_KEY=sk-ant-...

# ── Lead Discovery (Optional Paid Services) ──
TINYFISH_API_KEY=tf_...
DATAFORSEO_LOGIN=user@example.com
DATAFORSEO_PASSWORD=secret_password
SERPER_API_KEY=serper_...
SCAVIO_API_KEY=scavio_...

# ── Email Deliverability Verification ──
ZEROBOUNCE_API_KEY=zb_...

# ── Microsoft 365 / Outlook OAuth ──
M365_CLIENT_ID=00000000-0000-0000-0000-000000000000
M365_TENANT_ID=00000000-0000-0000-0000-000000000000
```

