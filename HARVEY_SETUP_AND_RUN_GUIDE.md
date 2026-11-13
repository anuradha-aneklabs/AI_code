# Harvey AI — Setup & Run Guide
**Version:** 1.0  
**Project Path:** `C:\Users\aneka\Desktop\harvey`  
**Platform:** Windows 11 (PowerShell) / Python 3.11.9  

---

## 1. Quick Start (Running Harvey Locally)

Harvey runs locally with two background processes:
1. **Ollama daemon** — provides local AI inference (`qwen2.5:1.5b`) and embeddings (`nomic-embed-text`)
2. **Harvey Dashboard** — provides the FastAPI backend and web interface on port 5555

### Starting Ollama (Daemon)
In PowerShell:
```powershell
& "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" serve
```
Verify Ollama models are present:
```powershell
& "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" list
```
Required models:
- `qwen2.5:1.5b` (LLM inference)
- `nomic-embed-text` (RAG vector embeddings)

If missing, pull them:
```powershell
& "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" pull qwen2.5:1.5b
& "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" pull nomic-embed-text
```

### Starting the Harvey Dashboard
In PowerShell (inside `C:\Users\aneka\Desktop\harvey`):
```powershell
Set-Location C:\Users\aneka\Desktop\harvey
.venv\Scripts\harvey.exe dashboard
```
The dashboard prints:
```
Harvey dashboard listening on http://127.0.0.1:5555/#token=<token>
```

### Accessing the Web UI
Open your browser to:
```
http://127.0.0.1:5555/#token=<token>
```
> [!TIP]
> The authentication token is stored on disk in `data/dashboard-token`. Passing `#token=<token>` automatically authenticates the session and bypasses the lock screen.

---

## 2. Setup Checklist (7 Required Items)

The dashboard **Today** and **Settings** tabs track 7 required setup items:

| # | Setup Item | Current Status | How to Complete |
|---|------------|----------------|-----------------|
| 1 | Python virtual environment | **DONE** | Python 3.11.9 in `.venv` |
| 2 | Environment file (`.env`) | **DONE** | `.env` file exists with `OLLAMA_BASE_URL` |
| 3 | Email provider configured | **PENDING CREDENTIALS** | Gmail OAuth (see Section 3 below) |
| 4 | Email verification key | **PENDING CREDENTIALS** | Add Reoon, ZeroBounce, or Hunter key in `.env` |
| 5 | Harvey configured (`harvey.yaml`) | **DONE** | Configured with Harvey AI persona & product |
| 6 | Product knowledge trained | **DONE** | `skills/product_knowledge.md` generated |
| 7 | Mailbox warmup | **PENDING CREDENTIALS** | TrulyInbox API key or select manual warmup |

**Current Progress:** **4 of 7 completed (57%)**. The remaining 3 items require external accounts and API keys.

---

## 3. Gmail Read-Only Integration & Inbound Email Prioritization

> [!IMPORTANT]
> **Read-Only Security Default:** Harvey's Gmail integration strictly defaults to read-only permissions (`https://www.googleapis.com/auth/gmail.readonly`). An internal send guard rejects any live send attempt if the OAuth token has read-only scope.
>
> In addition, the system-level safety hold remains active (`Pilot Evaluation - Read-Only Safety Hold`), ensuring zero real emails are sent without operator intervention (`require_approval = true`).

### Current Configuration Status
You can check Gmail configuration at any time via `GET /api/gmail/status` or on the dashboard:
- **Status:** `disconnected` (Honest status: OAuth has not been completed).
- **Scope:** `https://www.googleapis.com/auth/gmail.readonly` (Read-only inbox polling only).
- **Missing Configuration:**
  - `GMAIL_CLIENT_ID` in `.env` (unset)
  - `GMAIL_CLIENT_SECRET` in `.env` (unset)
  - `data/gmail_token.json` (does not exist yet)

### Connecting Gmail with Read-Only Permissions

#### Step 1: Create Google Cloud OAuth Client
1. Visit [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (e.g. `Harvey AI Lead Intake`)
3. Navigate to **APIs & Services** -> **Library** and enable the **Gmail API**
4. Configure the **OAuth Consent Screen**:
   - User Type: **External**
   - Publishing Status: **Testing**
   - Scopes: Add `https://www.googleapis.com/auth/gmail.readonly`
   - Test Users: Add your monitored Gmail address
5. Go to **Credentials** -> **Create Credentials** -> **OAuth Client ID**
   - Application Type: **Desktop app**
   - Name: `Harvey AI Desktop Client`
6. Copy the **Client ID** and **Client Secret**.

#### Step 2: Add Credentials to `.env`
Add the following to `C:\Users\aneka\Desktop\harvey\.env`:
```ini
GMAIL_CLIENT_ID=your_client_id_here.apps.googleusercontent.com
GMAIL_CLIENT_SECRET=your_client_secret_here
```

#### Step 3: Authorize
Run in PowerShell:
```powershell
Set-Location C:\Users\aneka\Desktop\harvey
.venv\Scripts\harvey.exe gmail auth
```
A browser window opens requesting read-only access to view email messages and settings. Upon authorization, Harvey saves the token to `data/gmail_token.json`.

---

## 4. Inbound Email Prioritization Workflow & Local Mock Testing

Harvey features an autonomous Email Prioritization engine (`harvey/agents/prioritizer.py`) that classifies inbound replies using multi-factor heuristics (Sender, Subject, Content, Urgency, Business Relevance) and manages human review:

| Priority | Criteria & Intent | System Action | Outbox Draft Action |
|----------|-------------------|---------------|---------------------|
| **HIGH** | Buying interest, demo/pilot inquiries, pricing questions, urgent calls/meetings | Moves conversation to `needs_human`, cancels remaining cold sequence steps | **Automatically stages grounded reply draft** in Outbox with `status="pending_review"` |
| **MEDIUM** | General clarifications, vendor comparisons, colleague referrals | Updates thread, sets status to `open` | Stored in conversation thread; no auto-draft created |
| **LOW** | Out of office autoreplies, negative rejections, opt-out/unsubscribe requests, bounces | If opt-out: immediately updates prospect status to `opted_out`, cancels all pending drafts, logs safety event | **No draft created** (`NO DRAFT NEEDED`). Hard suppression enforced |

### Multi-Factor Evaluation Architecture
- **Sender Factor:** Analyzes sender address (detects `mailer-daemon@`, `no-reply@` as automated system senders) and checks recipient role (elevates decision makers e.g. Clinical Director, Owner, Partner).
- **Subject Factor:** Checks for urgency flags (`urgent`, `asap`, `today`), bounce markers (`delivery status notification`, `failure`), and explicit intent.
- **Content Factor:** Identifies commercial/buying triggers, questions, colleague referrals, or opt-outs.
- **Urgency Factor:** Tags time-sensitive inquiries as `URGENT`.
- **Business Relevance:** Distinguishes high-value commercial inquiries from administrative chatter or automated responses.

### Testing Locally with Mock / Sample Data (No OAuth Required)
You can evaluate the entire prioritization engine, UI badging, and outbox draft staging locally using pre-packaged mock samples:

#### 1. Inspect Available Mock Scenarios
```powershell
.venv\Scripts\python -c "import httpx; t=open('data/dashboard-token').read().strip(); r=httpx.get('http://127.0.0.1:5555/api/inbox/mock-sample', headers={'Authorization': 'Bearer '+t}); print(r.json())"
```
Available scenarios:
- `buying_intent` (High Priority — Buying Intent & Demo Request)
- `urgent_quote` (High Priority — Urgent Pricing Quote)
- `product_question` (Medium Priority — Software Compatibility Question)
- `referral` (Medium Priority — Internal Colleague Referral)
- `out_of_office` (Low Priority — Automated Out of Office Notice)
- `unsubscribe` (Low Priority — Unsubscribe / Opt-Out Request)
- `bounce` (Low Priority — Delivery Status Notification Failure)

#### 2. Ingest Sample Inbound Email(s)
To ingest all sample scenarios:
```powershell
.venv\Scripts\python -c "import httpx; t=open('data/dashboard-token').read().strip(); r=httpx.post('http://127.0.0.1:5555/api/inbox/mock-sample', headers={'Authorization': 'Bearer '+t}, json={'sample_id': 'all'}); print('Ingested:', r.json()['count'])"
```
Or ingest an individual sample scenario (e.g. `buying_intent`):
```powershell
.venv\Scripts\python -c "import httpx; t=open('data/dashboard-token').read().strip(); r=httpx.post('http://127.0.0.1:5555/api/inbox/mock-sample', headers={'Authorization': 'Bearer '+t}, json={'sample_id': 'buying_intent'}); print(r.json())"
```

### Operator Review in Dashboard UI
1. **Conversations Tab (Operator Decision Desk):**
   - **In-Place Reply Editor:** High-priority conversations with staged drafts render a dedicated draft card directly in the conversation view. Operators can read, edit, and save the staged response in-place without context-switching to Outbox.
   - **Canonical Outbox Sync:** Edits made in the Conversations tab update the canonical Outbox draft record via `/api/outbox/{id}/edit`, maintaining zero duplicate records and enforcing brevity and compliance gate limits.
   - **Clear Status Badges:** Explicit badge indicates draft status:
     - `Pending Human Review` (amber) — Draft is held and requires explicit human approval before queueing.
     - `Approved · Queued in Outbox` (green) — Draft is signed off and awaiting sending unpause.
   - **Draft Rejection with Feedback:** Operators can reject inappropriate drafts with an optional feedback note explaining the issue (e.g., tone, pricing nuance). Feedback is permanently stored in the SQLite `feedback` and `actions` tables for iterative prompt improvements.
   - **Conversation History Context Injection:** When generating follow-up replies, Harvey automatically injects up to 5 recent thread messages into the local LLM prompt, keeping follow-ups tightly grounded in prior exchanges.
   - **Visual Badges & Tags:** `HIGH` (red), `MEDIUM` (amber), `LOW` (gray/muted), `SAMPLE` (purple), `URGENT` (red), `DRAFT READY` (green).
   - **Segmented Filter Bar:** **Needs you**, **High Priority**, **Medium**, **Open**, **Low / Auto**, **Awaiting reply**, **Closed**, and **All**.
   - **Contextual Empty States:** Displays helpful explanations when no conversations match a selected filter.
   - **Smart Priority Sorting:** Threads needing human attention or with ready drafts appear at the top, followed by High, Medium, and Low.
2. **Outbox Tab (Canonical Decisions Desk):**
   - All staged replies appear alongside cold outreach drafts under **Pending Review**.
   - Strict safety: No draft is ever dispatched automatically; operators can review, edit, or reject before manual approval.
   - In-place edits made in Conversations instantly reflect here and vice versa.

---

## 5. Configuring Email Verification (Free Tier Available)

Without an email verifier key, discovered emails remain in `guess` status and Harvey refuses to send to them:

1. **Reoon Verifier (Recommended — Free 600 checks/month):**
   - Sign up at [reoon.com](https://www.reoon.com/)
   - Copy your API key
   - Add to `.env`:
     ```ini
     REOON_API_KEY=your_reoon_api_key
     ```
2. **Alternative Providers:**
   - ZeroBounce: `ZEROBOUNCE_API_KEY=...`
   - Hunter.io: `HUNTER_API_KEY=...`

---

## 6. Daily Operator Workflows

### Reviewing the Decisions Desk (Outbox)
1. Open dashboard to `http://127.0.0.1:5555/#token=...`
2. Navigate to **Outbox** tab
3. Review pending drafts:
   - Check recipient name, clinic/company, and email address
   - Verify claims match your verified public records or RAG knowledge base
   - Click **Edit** to adjust wording (subject must be concise, body under 220 words)
   - Click **Approve** when satisfied
4. Approved emails queue in the outbox until the sending pause is lifted.

### AI Lead Discovery & Qualification Workflow
1. Open dashboard and navigate to **Discover** tab.
2. Select target ICP from the **Target Business Profile** dropdown (e.g. *Dental Clinics & Practices (India)* or *B2B SaaS & High-Growth Tech*).
3. Review target criteria pills (industry, geography, company size, target job titles).
4. Click **Discover Sample Leads**:
   - Evaluates realistic candidate organizations locally using `SampleLeadDiscoveryProvider`.
   - Populates structured research profiles with verified source evidence.
   - Flags missing or unverified attributes transparently without fabricating facts.
   - Computes deterministic qualification scores (0-100) and fit tiers (`High Fit`, `Moderate Fit`, `Low Fit`).
5. Review the qualification breakdown box on each candidate card (green positive matches, amber missing fields).
6. Click **Add to Pipeline** on approved qualified leads:
   - Evaluates domain and email deduplication against existing records.
   - Promotes unique organization to **Companies** register and decision maker to **Contacts** (`status: new`).
   - Updates lead button to `✓ In CRM Pipeline`.

### Automated Multi-Step Campaign Generation Workflow
1. Open dashboard to `http://127.0.0.1:5555/#token=...` and navigate to the **Campaigns** tab.
2. Click **+ Generate Campaign** in the header to open the Automated Campaign Generator panel.
3. Select your target **Business Profile** (e.g. *Dental Clinics & Practices (India)* or *B2B SaaS & High-Growth Tech*).
   - *Profile Isolation:* The lead picker automatically updates to show only qualified CRM leads associated with that specific business profile (`status IN ('new', 'queued')`).
4. Select one or more target leads using the checkboxes (or click **Select All Qualified Leads**).
5. Configure sequence parameters:
   - **Campaign Name:** Descriptive label (e.g., `Indore Dental Autumn Outreach`).
   - **Sequence Steps:** Choose 2-Step (Introductory + Follow-up) or 3-Step (Introductory + Value Reinforcement + Breakup/Check-in).
   - **Delay Between Steps:** Days to wait before subsequent touches (default: 3 days).
   - **Campaign Goal & Tone:** Tailor value proposition and communication style (e.g. consultative, professional).
6. Click **Generate Sequence Preview**:
   - The generator compiles verified company research, flags any missing attributes, and queries ChromaDB for relevant product knowledge.
   - Strictly enforces fact honesty: never fabricates metrics, awards, or customer counts. Conservative phrasing is used when specifics are unverified.
7. Review and Edit in the Preview Container:
   - Each lead card displays verified grounding facts and RAG source provenance tags.
   - **In-place editing:** You can edit the subject and body of Step 1, Step 2, or Step 3 directly before staging.
   - Clear badges indicate:
     - `Step 1 · Staged in Outbox` (will become a pending review draft)
     - `Step 2 · Planned in Sequence` (saved in sequence JSON, not sent or queued)
     - `Step 3 · Planned in Sequence` (saved in sequence JSON, not sent or queued)
8. Click **Stage Campaign to Outbox**:
   - Saves the full multi-step campaign and sequence configuration in the database.
   - **Step 1 drafts** are staged into the canonical Outbox with status `pending_review`.
   - **Steps 2 and 3** remain safely planned in the sequence data structure and are **NOT** placed into the outbox.
   - Retry deduplication prevents re-creating drafts if clicked again.
9. Navigate to the **Outbox** tab to review, polish, or approve the staged Step 1 drafts when ready.

### Campaign Review & Approval Workflow
1. In the **Campaigns** tab, locate any generated campaign card.
2. Click **Review Queue** (`Review Queue (N pending / M total)`):
   - Opens the dedicated Campaign Review & Approval panel directly on the campaign card.
   - Summarizes total drafts, pending reviews, approved drafts, and rejected drafts.
   - Shows badge confirming later steps: `Later Steps: 2 planned in sequence only (0 in Outbox)`.
3. Review Step 1 Drafts:
   - Inspect recipient name, clinic/company, email, subject line, and body copy.
   - Status badge indicates `Pending Review` (amber), `✓ Approved · Staged for Send (Paused)` (green), or `✗ Rejected` (red).
4. In-Place Editing Before Approval:
   - Click **Edit** on any draft awaiting review.
   - Adjust subject or body text; pre-send gate rules enforce subject brevity (<= 80 chars) and formatting.
   - Click **Save Changes**; updates the Outbox record immediately and records a `campaign_draft_edited` audit log.
5. Explicit Approval Action:
   - Click **✓ Approve Draft**.
   - Updates draft status to `approved` and records operator sign-off (`approved_by = "you"`).
   - *Strict Safety:* Approval stages the email for the next manual sending step, but does **NOT** dispatch it. The global sending hold remains active (`sending_paused = "Pilot Evaluation - Read-Only Safety Hold"`) and `sent_count = 0`.
   - *Idempotency:* The button transitions to disabled `✓ Approved` to prevent accidental double-approvals.
6. Explicit Rejection Action:
   - Click **Reject** to expand the rejection input box.
   - Enter a mandatory rejection reason (e.g. *Needs softer opening*, *Incorrect ICP*). An empty reason is rejected.
   - Click **Confirm Rejection**; sets status to `rejected`, stores the note in the SQLite `feedback` table for prompt iteration, and logs `campaign_draft_rejected`.
   - Operators can reconsider and approve a previously rejected draft at any time.
7. Audit Trail Inspection:
   - Scroll to the bottom of the review panel to view the chronological **Review & Audit Trail**.
   - Displays timestamp, operator identity, action type (`approved`, `rejected`, `edited`), and status transitions.

### Testing RAG Grounding
1. Navigate to **Knowledge** tab
2. Upload any product guides (`.md`, `.txt`, or `.pdf`)
3. Use the search box to test semantic retrieval
4. Verify chunk similarity scores (typically >0.50 for high relevance)

### Pipeline Controls (Controls Tab)
- **Start / Stop Pipeline:** Controls autonomous discovery, scraping, and draft staging cycles
- **Approval Mode:** Keep set to `Approval` (default) for human-in-the-loop review
- **Sending Pause:** To resume after safety hold, use `Review and resume` from Outbox (requires explicit user action)

---

## 7. Troubleshooting & Self-Healing

### Stale "Harvey's Model is Paused" Warning
- **Root Cause:** Occurs if Ollama was offline during a prior heartbeat.
- **Fix:** Start Ollama (`& "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" serve`). The dashboard automatically re-checks Ollama connectivity on each request and self-heals the status to `ok` without any manual DB edits.

### Dashboard Lock Screen Appears
- If the session token expires or is cleared, copy the token from `data/dashboard-token` and enter it into the password field on screen, or load the dashboard with `/#token=<token>`.

### Port 5555 Already in Use
If another dashboard instance is running:
```powershell
Get-NetTCPConnection -LocalPort 5555 | Select-Object OwningProcess
Stop-Process -Id <OwningProcessId> -Force
```
Then restart the dashboard.

