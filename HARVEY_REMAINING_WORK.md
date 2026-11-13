# Harvey AI — Remaining Work & Production Roadmap

This document outlines the exact, prioritized roadmap required to transition Harvey AI from its current **verified local staged/pilot state** to **autonomous live outbound email operation**.

---

## Current Verified Baseline

Harvey is currently **100% operational across any industry in local staged mode**:
- **Universal Multi-Industry Support:** Configurable business profiles (B2B SaaS, Legal, Accounting, HVAC/Facility Services, Healthcare, and Universal CleanTech/Robotics) with zero hardcoded clinical/dental bias.
- **Multi-Industry Knowledge Base (RAG):** Domain knowledge guides indexed in local ChromaDB with high semantic retrieval accuracy across all verticals.
- **AI Lead Discovery & ICP Scoring:** Industry-aligned sample generators and OpenStreetMap extraction with domain deduplication and explainable scoring.
- **CRM Pipeline & Deduplication:** Company and contact promotion with profile tagging.
- **Automated Campaign Sequence Generation:** Multi-step sequences grounded in profile research and verified RAG docs.
- **Campaign Review & Approval Queue:** In-place draft editing, rejection notes, approval staging, and audit trail logging.
- **Operator Decision Desk & Inbox Prioritization:** High/Medium/Low priority email triage and in-place reply drafting.
- **Canonical Outbox:** Full draft preservation and hold enforcement.
- **Safety Invariants strictly active:** `sent_count = 0`, `sending_paused = "Pilot Evaluation - Read-Only Safety Hold"`, all 4 original pilot drafts intact.

---

## Priority 1: Critical Blockers to Enable Live Outbound Sending

These items are strictly required before Harvey can transmit any live external emails.

### 1.1 Google Cloud OAuth 2.0 Credentials & Token Generation
- **Current Status:** Missing credentials. Gmail integration is disconnected by design.
- **What is Needed:**
  1. Go to [Google Cloud Console](https://console.cloud.google.com/).
  2. Create or select a Google Cloud Project (e.g., `Harvey-AI-Sales-Agent`).
  3. Navigate to **APIs & Services > Library** and enable the **Gmail API**.
  4. Navigate to **APIs & Services > OAuth consent screen**:
     - Choose User Type: **External** (or Internal for Google Workspace orgs).
     - Fill in App name (`Harvey AI`), User support email, and Developer contact email.
     - Scopes: Add `https://www.googleapis.com/auth/gmail.readonly` (for read-only inbox reading) and `https://www.googleapis.com/auth/gmail.send` (or `https://www.googleapis.com/auth/gmail.compose` for drafting/sending).
     - Test Users: Add the sender Gmail address (e.g. your outreach mailbox).
  5. Navigate to **APIs & Services > Credentials > Create Credentials > OAuth client ID**:
     - Application type: **Desktop app**.
     - Name: `Harvey Local Desktop`.
     - Download or copy the **Client ID** and **Client Secret**.
  6. Add to `C:\Users\aneka\Desktop\harvey\.env`:
     ```env
     GMAIL_CLIENT_ID=your_client_id_here.apps.googleusercontent.com
     GMAIL_CLIENT_SECRET=your_client_secret_here
     ```
  7. Run the local OAuth authorization flow:
     ```powershell
     .venv\Scripts\harvey.exe gmail auth
     ```
  8. Complete Google consent in the web browser. Harvey will safely store the refreshable token in `data/gmail_token.json`.

---

### 1.2 Outbound Domain Deliverability & Cold Outreach Safety
- **Current Status:** Not configured for custom domain deliverability.
- **What is Needed:**
  1. **DNS Authentication:**
     - Configure **SPF** (`v=spf1 include:_spf.google.com ~all` or provider specific).
     - Configure **DKIM** (2048-bit domain key via Google Workspace / DNS).
     - Configure **DMARC** (`v=DMARC1; p=none; rua=mailto:dmarc-reports@yourdomain.com`).
  2. **Mailbox Warmup Schedule:**
     - Do not blast outreach immediately. Ramp volume gradually:
       - Week 1: 5–10 approved emails / day
       - Week 2: 15–25 approved emails / day
       - Week 3+: 30–50 approved emails / day
  3. **Bounce & Spam Monitoring:**
     - Maintain hard bounce rate < 2% and spam complaint rate < 0.1%.

---

### 1.3 Safe Sending Gate Activation Protocol
- **Current Status:** Active safety hold prevents any draft from transmitting.
- **Protocol to Enable Live Sending:**
  1. Keep `require_approval = true` active in `harvey/config.py` and settings. Every email must still be clicked and approved by a human operator.
  2. Clear the safety pause hold via dashboard or CLI:
     ```powershell
     .venv\Scripts\harvey.exe settings set sending_paused ""
     ```
  3. Verify that sending engine checks recipient domain against blocklist before sending.
  4. Transmit exactly one test draft to an internal test email address to verify end-to-end receipt, MIME formatting, and compliance footer rendering before touching prospective leads.

---

## Priority 2: Operational Quality & Efficiency Enhancements

These improvements expand automation convenience without altering the core architecture.

### 2.1 External Paid Lead Discovery Integrations
- **Current Status:** Uses free OpenStreetMap (OSM) Overpass API and deterministic sample generators.
- **Enhancement:**
  - Add API connectors for paid B2B data providers (e.g., Hunter.io, Apollo.io, or AnymailFinder) in `harvey/discovery/`.
  - Add real-time DNS MX record verification and SMTP handshake verification before staging leads in CRM.

### 2.2 Bulk Review Actions in Campaign Review Queue
- **Current Status:** In-place edit, approve, and reject operate per-draft.
- **Enhancement:**
  - Add "Approve All Step 1 Drafts" or checkbox multi-select with batch confirmation.
  - Add bulk reject with a common feedback note.

### 2.3 Dynamic Web Scraping for Lead Research
- **Current Status:** Research dossiers use cached data and business profile context.
- **Enhancement:**
  - Integrate a lightweight headless web reader to scrape prospect homepages or "About Us" pages live during discovery.

---

## Priority 3: Scale & Enterprise Features

Recommended for multi-team or multi-channel deployment.

### 3.1 Bi-Directional CRM Sync (HubSpot / Salesforce)
- Add webhook listeners and REST API connectors to sync company status and conversation stages back to corporate CRM systems automatically.

### 3.2 Calendar Booking Integration
- Support auto-injection of booking links (Cal.com / Calendly) and automatic intent recognition ("meeting_booked") when a prospect confirms a call time.

### 3.3 Multi-Tenant Access Control & User Roles
- Introduce role-based access control (RBAC): `Operator` (draft editing and review), `Admin` (settings, safety unpause, credentials), and `Observer` (read-only analytics).

---

## Summary of Next Steps

| Step | Action | Dependency | Est. Effort |
|:---:|:---|:---|:---:|
| **1** | Obtain Google Cloud Client ID & Secret | User / Google Cloud Console | 15 mins |
| **2** | Add credentials to `.env` & run `harvey gmail auth` | Step 1 | 5 mins |
| **3** | Send 1 verified test email to internal test inbox | Step 2 + DNS check | 10 mins |
| **4** | Unpause sending for pilot cohort (5 leads) with operator approval | Step 3 | 5 mins |

