# Harvey AI Sales Agent — Live Integration Status & Credential Matrix

**Document Version:** 1.0.0  
**Audit Date:** November 14, 2026  
**Project Path:** `C:\Users\aneka\Desktop\harvey`  
**Execution Environment:** Windows 11 Enterprise, Python 3.11.9 (`.venv`)  
**Dashboard API:** `http://127.0.0.1:5555/api/integrations/status`  

---

## 1. Executive Summary

This matrix provides a complete, honest, and evidence-based classification of all 14 external integrations in the Harvey AI Sales Agent. 

In accordance with strict verification rules:
- **Never claim an integration is live if credentials are missing.**
- **Never simulate a successful connection when real credentials fail.**
- **Clearly distinguish LIVE VERIFIED, IMPLEMENTED (CREDENTIAL BLOCKED), and SIMULATED.**

---

## 2. Master Integration Status Matrix

| Provider / Service | Technical Category | Status Label | Missing Configuration | Live Test Verification Evidence |
| :--- | :--- | :--- | :--- | :--- |
| **OpenStreetMap & Nominatim** | **LIVE VERIFIED** | Connected & Verified | *None (Public Free API)* | **HTTP 200 OK** (Discovered 5-10 real clinics in Indore; latency: 901ms) |
| **ChromaDB Local Vector DB** | **LIVE VERIFIED** | Connected & Verified | *None (Local Storage)* | **Operational** (19 persistent chunks indexed in `data/rag/chroma`) |
| **Local Public Web Scraper** | **LIVE VERIFIED** | Connected & Verified | *None (Local HTTP)* | **Operational** (HTTP metadata, OpenGraph, title, and contact parsing) |
| **Ollama Local AI** | **LOCAL INSTALLED / SERVICE OFFLINE** | Local AI Offline (Fallback Active) | `ollama serve` process on host | **Fallback Active** (`OutreachDraftGenerator` deterministic grounded fallback engine) |
| **Google Workspace (Gmail)** | **SIMULATED / SANDBOX ACTIVE** | Mock / Sandbox | Real user OAuth consent | **Sandbox Active** (`pilot-test@example.com`; `gmail.readonly` scope enforced) |
| **Microsoft 365 Graph** | **IMPLEMENTED (CREDENTIAL BLOCKED)**| Credentials Missing | `M365_CLIENT_ID`, `M365_TENANT_ID`, `data/m365_token.json` | Client code complete in `harvey/integrations/microsoft.py` |
| **Cal.com API** | **IMPLEMENTED (CREDENTIAL BLOCKED)**| Credentials Missing | `CALCOM_API_KEY` | Client code complete in `harvey/integrations/calendar.py` |
| **Calendly API** | **IMPLEMENTED (CREDENTIAL BLOCKED)**| Credentials Missing | `CALENDLY_TOKEN` | Client code complete in `harvey/integrations/calendar.py` |
| **DataForSEO** | **IMPLEMENTED (CREDENTIAL BLOCKED)**| Credentials Missing | `DATAFORSEO_LOGIN`, `DATAFORSEO_PASSWORD` | Client code complete in `harvey/dashboard.py` |
| **Serper Google Search** | **IMPLEMENTED (CREDENTIAL BLOCKED)**| Credentials Missing | `SERPER_API_KEY` | Client code complete in `harvey/collectors/` |
| **TinyFish Web Automation** | **IMPLEMENTED (CREDENTIAL BLOCKED)**| Credentials Missing | `TINYFISH_API_KEY` | Client code complete in `harvey/collectors/` |
| **Scavio Business Data** | **IMPLEMENTED (CREDENTIAL BLOCKED)**| Credentials Missing | `SCAVIO_API_KEY` | Client code complete in `harvey/collectors/` |
| **ZeroBounce Email Verify** | **IMPLEMENTED (CREDENTIAL BLOCKED)**| Credentials Missing | `ZEROBOUNCE_API_KEY` | Client code complete in `harvey/deliverability.py` |
| **Hunter.io Domain Search** | **IMPLEMENTED (CREDENTIAL BLOCKED)**| Credentials Missing | `HUNTER_API_KEY` | Client code complete in `harvey/enrichment.py` |
| **Reoon Email Verifier** | **IMPLEMENTED (CREDENTIAL BLOCKED)**| Credentials Missing | `REOON_API_KEY` | Client code complete in `harvey/deliverability.py` |
| **Anthropic Claude AI** | **IMPLEMENTED (CREDENTIAL BLOCKED)**| Credentials Missing | `ANTHROPIC_API_KEY` | Client code complete in `harvey/brain.py` |

---

## 3. Deep-Dive Integration Assessments

### 3.1 Spatial Lead Discovery: OpenStreetMap / Nominatim
- **Category:** **LIVE VERIFIED**
- **Endpoint:** `https://overpass-api.de/api/interpreter`, fallback: `https://nominatim.openstreetmap.org/search`
- **Authentication:** None required (Public Open-Access API).
- **Live Test Evidence:**
  ```json
  {
    "success": true,
    "provider": "openstreetmap",
    "status": "connected",
    "message": "OpenStreetMap / Nominatim operational (HTTP 200 OK)",
    "latency_ms": 901,
    "error": null
  }
  ```
- **Discovered Sample Entities:**
  - `The Great Smile Dental Clinic` (Juni Indore Tahsil, Madhya Pradesh) — Node 6376772801
  - `Family Dental Clinic` (Indore, Madhya Pradesh) — Node 6187425277
  - `Advance Dental Care Clinic` (Indore, Madhya Pradesh) — Node 6187452265

### 3.2 Local Vector Database: ChromaDB
- **Category:** **LIVE VERIFIED**
- **Storage Path:** `data/rag/chroma`
- **Collection:** `harvey_knowledge`
- **Indexed Chunks:** 19 persistent chunks across 7 product guides (SaaS, Legal, Dental, Accounting, HVAC).
- **Live Test Evidence:**
  ```json
  {
    "success": true,
    "provider": "chromadb",
    "status": "connected",
    "message": "ChromaDB operational (19 chunks indexed)",
    "latency_ms": 1298,
    "error": null
  }
  ```

### 3.3 Email Integrations: Gmail & Microsoft 365
#### Gmail OAuth (Read-Only)
- **Category:** **SIMULATED / SANDBOX ACTIVE**
- **Configured Environment:** `GMAIL_CLIENT_ID` and `GMAIL_CLIENT_SECRET` present in `.env`.
- **Token State:** `data/gmail_token.json` is currently configured as a Sandbox Mock Token (`is_mock: True`, `email: pilot-test@example.com`).
- **Enforced Scope:** Strictly `https://www.googleapis.com/auth/gmail.readonly`.
- **Production Pre-requisite:** Launch interactive browser authentication via `python -m harvey.integrations.gmail` to generate a live Google OAuth refresh token.

#### Microsoft 365 Graph
- **Category:** **IMPLEMENTED (CREDENTIAL BLOCKED)**
- **Code Module:** `harvey/integrations/microsoft.py`
- **Enforced Scopes:** `offline_access https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Calendars.Read https://graph.microsoft.com/User.Read`.
- **Required Configuration:** Add `M365_CLIENT_ID`, `M365_TENANT_ID`, `M365_CLIENT_SECRET` to `.env` and execute `python -m harvey.integrations.microsoft` for interactive tenant admin consent.

### 3.4 Calendar Providers: Cal.com & Calendly
#### Cal.com
- **Category:** **IMPLEMENTED (CREDENTIAL BLOCKED)**
- **Code Module:** `harvey/integrations/calendar.py` (`get_calcom_event_types`, `get_calcom_slots`)
- **Required Configuration:** `CALCOM_API_KEY` in `.env`.
- **API Capabilities:** Retrieves event types and real available time slots without booking modification permissions.

#### Calendly
- **Category:** **IMPLEMENTED (CREDENTIAL BLOCKED)**
- **Code Module:** `harvey/integrations/calendar.py` (`get_calendly_event_types`, `get_calendly_slots`)
- **Required Configuration:** `CALENDLY_TOKEN` (or `CALENDLY_API_KEY`) in `.env`.
- **API Capabilities:** Read-only event type inspection via `/users/me` and `/event_type_available_times`.

### 3.5 AI Inference Engines: Ollama & Anthropic
#### Ollama Local AI
- **Category:** **LOCAL INSTALLED / SERVICE OFFLINE**
- **Host Binary:** `C:\Users\aneka\AppData\Local\Programs\Ollama\ollama.exe` (installed).
- **Service Status:** Port 11434 unreachable (service daemon not running in background).
- **Fallback Verification:** Harvey's built-in `OutreachDraftGenerator` automatically activates deterministic grounded fallback when Ollama is offline. Draft generation completes in under 3 seconds without errors.

#### Anthropic Claude
- **Category:** **IMPLEMENTED (CREDENTIAL BLOCKED)**
- **Code Module:** `harvey/brain.py`
- **Required Configuration:** `ANTHROPIC_API_KEY` in `.env`.

---

## 4. Unresolved Credential Transition Guide

To transition any blocked integration to **LIVE VERIFIED** during production deployment, populate the following variables in `.env`:

```env
# 1. Google Workspace Live OAuth
GMAIL_CLIENT_ID=your_oauth_client_id.apps.googleusercontent.com
GMAIL_CLIENT_SECRET=your_oauth_client_secret

# 2. Microsoft 365 Azure Entra ID
M365_CLIENT_ID=your_azure_app_client_id
M365_TENANT_ID=your_azure_tenant_id
M365_CLIENT_SECRET=your_azure_app_secret

# 3. Calendar Providers
CALCOM_API_KEY=cal_live_xxxxxxxxxxxxxxxx
CALENDLY_TOKEN=calendly_pat_xxxxxxxxxxxx

# 4. Search & Discovery
DATAFORSEO_LOGIN=your_login
DATAFORSEO_PASSWORD=your_password
SERPER_API_KEY=your_serper_key
TINYFISH_API_KEY=your_tinyfish_key
SCAVIO_API_KEY=your_scavio_key

# 5. Email Verification
ZEROBOUNCE_API_KEY=your_zerobounce_key
HUNTER_API_KEY=your_hunter_key
REOON_API_KEY=your_reoon_key

# 6. AI Inference
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxx
```

---

## 5. Security & Safety Commitment

Across all live, simulated, and configured integrations:
1. **Zero Outbound Transmission:** Outbox sent count is strictly `0`. No live emails or marketing campaigns are dispatched.
2. **Strict Read-Only Scopes:** Mail and calendar APIs request read-only permissions exclusively.
3. **No External Calendar Edits:** Meeting links and available slots are read-only; no calendar invites or cancellations are generated.
4. **Credential Protection:** Tokens and credentials stored strictly in `.env` and `data/` directories, both ignored by Git version control.

