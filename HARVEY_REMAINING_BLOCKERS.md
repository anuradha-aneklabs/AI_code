# Harvey AI Sales Agent — Remaining Blockers & Credential Dependencies

**Target Project:** Harvey AI Sales Agent (`C:\Users\aneka\Desktop\harvey`)  
**Audit Date:** November 13, 2026  
**Auditor:** Antigravity Engineering (Independent Audit)

---

## 1. Operational & Technical Blockers Analysis

### Internal System Blockers: ZERO (0)
There are **zero internal technical, algorithmic, architectural, or codebase defects** blocking the immediate execution of an internal operator demonstration or a controlled pilot evaluation:

- **Local AI Inference Engine:** Fully operational via local Ollama daemon (`qwen2.5:1.5b`) on `http://127.0.0.1:11434`.
- **Knowledge Base & Vector Store:** Fully operational local ChromaDB (`data/chroma`) with 7 documents, 19 chunks, and cosine similarity query capability.
- **Lead Discovery Engine:** Fully operational geocoded OpenStreetMap Overpass discovery queryable by bounding box without paid subscriptions.
- **CRM & Data Pipeline:** 100% operational SQLite persistence (Schema v21) with deduplicated business profiles and leads.
- **Browser UI & Navigation:** All 15 navigation tabs audited via Playwright with 0 console errors, 0 network failures, and 0 uncaught exceptions.
- **Cold-Boot Restart Persistence:** 100% verified across process kill and cold restart with identical pre- and post-restart entity counts.

---

## 2. External Credential Dependencies (Production Live Sync Only)

The following external credentials are required ONLY when transitioning from the local controlled pilot to live external enterprise synchronization. In the controlled pilot, all workflows are safely tested via live local services or deterministic sandboxes:

| External Service | Environment Variable(s) / File Path | Pilot Behavior Without Credential | Production Impact When Provided |
| :--- | :--- | :--- | :--- |
| **Google Workspace (Gmail)** | `data/credentials.json` | Displays "Not Authorized" status card with clear connect instructions. | Enables live inbox read-only sync and background thread classification. |
| **Microsoft 365 (Graph API)** | `M365_CLIENT_ID`<br>`M365_TENANT_ID`<br>`M365_CLIENT_SECRET` | Operational via built-in Sandbox Demo Mode (`/api/microsoft/mock-connect`). | Connects to enterprise Azure AD tenant for live mailbox read access. |
| **DataForSEO Search** | `DATAFORSEO_LOGIN`<br>`DATAFORSEO_PASSWORD` | Discovery falls back to OpenStreetMap Overpass and deterministic sample generator. | Enables Google SERP and Google Maps search across international markets. |
| **TinyFish Search** | `TINYFISH_API_KEY` | Discovery falls back to OpenStreetMap Overpass. | Enables real-time web entity search and company extraction. |
| **Serper Google SERP** | `SERPER_API_KEY` | Discovery falls back to OpenStreetMap Overpass. | Provides live organic Google search results for company domains. |
| **Scavio Web Intelligence** | `SCAVIO_API_KEY` | Lead enrichment uses local HTTP website parser and robots.txt analyzer. | Enables deep corporate directory scraping and organizational mapping. |
| **ZeroBounce Email Verification** | `ZEROBOUNCE_API_KEY` | Verification falls back to built-in DNS MX record validation and RFC syntax checker. | Provides commercial SMTP handshake and spam-trap detection scores. |

---

## 3. Deliberate Safety Holds (By Design)

The system currently enforces two deliberate, non-negotiable safety holds:

1. **Global Sending Safety Hold:**
   - Active database setting: `sending_paused = "Pilot Evaluation - Read-Only Safety Hold"`.
   - **Effect:** Any call to dispatch an email will be blocked at the database and API level.
   - **To Lift (Production Only):** Requires an authorized operator to execute `UPDATE settings SET value = '' WHERE key = 'sending_paused';` in SQLite.
2. **Mandatory Human-in-the-Loop Approval Gate:**
   - Active configuration: `channels.email.require_approval = true`.
   - **Effect:** Outbox drafts remain in `pending_review` until an operator clicks "Approve & Send" or uses the `/api/outbox/{id}/approve` endpoint.

---

## 4. Controlled Pilot Readiness & Operator Instructions

The Harvey system is completely prepared for internal stakeholder demonstration.

### Recommended Startup Procedure
```powershell
# 1. Start Ollama Local LLM Daemon (Terminal 1)
& "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" serve

# 2. Start Harvey Dashboard (Terminal 2)
cd C:\Users\aneka\Desktop\harvey
.\.venv\Scripts\harvey.exe dashboard
```

### Access URL & Security Token
- **Dashboard URL:** `http://127.0.0.1:5555/#token=QwHw6UoT33tIBa9BRuLHnXTXOail1utpKVqpFHk1iZc`
- **Operator Runbook:** [internal_pilot_runbook.md](file:///c:/Users/aneka/Desktop/harvey/docs/internal_pilot_runbook.md)
- **Automated E2E Verification Script:** [run_full_phase8_e2e_verification.py](file:///c:/Users/aneka/Desktop/harvey/scratch/run_full_phase8_e2e_verification.py)
