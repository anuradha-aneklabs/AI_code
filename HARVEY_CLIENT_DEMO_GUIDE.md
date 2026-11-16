# Harvey AI Sales Agent — Client Demonstration Guide & Playbook

**Platform:** Harvey AI Sales Agent (Enterprise Edition v2.0.0)  
**Target Audience:** Prospective Clients, Enterprise Decision Makers, VP of Sales, CROs, CTOs  
**Demo Duration:** 15–25 Minutes  
**Project Path:** `C:\Users\aneka\Desktop\harvey`  

---

## 1. Executive Demonstration Narrative & Value Proposition

When presenting Harvey to prospective clients, emphasize four core differentiators:

1. **Autonomous Intelligence with Mandatory Human Oversight:**
   Harvey autonomously identifies high-intent accounts, researches company infrastructure, and drafts hyper-personalized outreach. However, Harvey **never sends an email without human review and approval**. This eliminates compliance risks, brand damage, and hallucinations.
2. **Universal Multi-Industry Agility:**
   Harvey is not hardcoded to a single niche. It natively supports 19 pre-configured industry profiles (B2B SaaS, Legal, Manufacturing, Real Estate, Accounting, Logistics, Healthcare, etc.) and allows clients to define bespoke ICPs and qualification rules in seconds.
3. **Free & Private Local Core (Zero Per-Seat SaaS Subscriptions):**
   Unlike legacy sales platforms charging $100–$300/user/month, Harvey operates out-of-the-box using local open-source models (Ollama), local geo-discovery (OpenStreetMap/Nominatim), and local vector search (ChromaDB) — with zero mandatory API costs.
4. **Enterprise Email Parity:**
   Supports direct OAuth integration with both Google Workspace (Gmail) and Microsoft 365 (Exchange Online).

---

## 2. Pre-Demo Preparation (5 Minutes Before the Call)

Execute these checks to ensure the local environment is warm:

### A. Start Required Services
Open PowerShell in `C:\Users\aneka\Desktop\harvey`:
```powershell
# 1. Ensure Ollama inference daemon is running
ollama serve

# 2. Ensure Harvey Dashboard daemon is running on port 5555
.venv\Scripts\python.exe -m harvey.cli dashboard --port 5555
```

### B. Verify Dashboard Access
1. Open Google Chrome or Microsoft Edge.
2. Retrieve the active authentication token from `data/dashboard-token`:
   ```powershell
   Get-Content data/dashboard-token
   ```
3. Navigate to: `http://127.0.0.1:5555/#token=<YOUR_DASHBOARD_TOKEN>`
4. Confirm the lock overlay dismisses and the **Today / Pulse** tab loads cleanly.

---

## 3. Step-by-Step Live Demonstration Script

### Act 1: The Modern SDR Bottleneck & Solution Overview (2 mins)
- **What to say:**
  > *"Modern outbound sales teams waste 65% of their time manually browsing directories, hunting for verified contacts, and copy-pasting generic email templates. Harvey automates the entire top-of-funnel pipeline — lead discovery, BANT qualification, account research, and outreach drafting — while keeping your sales manager in 100% control before any email is dispatched."*
- **What to show:**
  - Show the **Today / Pulse** tab. Point out the live metrics: active prospects, leads discovered, drafts pending review, and system status.
  - Click the **Theme Toggle** button (`#theme-btn` in top right) to demonstrate Dark/Light mode support.

---

### Act 2: Multi-Industry Customization & ICP Profile Selection (3 mins)
- **What to say:**
  > *"Every company sells to a different Ideal Customer Profile. Whether you sell enterprise cloud software, legal counsel, or industrial manufacturing equipment, Harvey adapts instantly."*
- **What to show:**
  - Click on the **Discover** tab.
  - Point to the **Active Business Profile** dropdown (`#active-business-profile`).
  - Demonstrate switching from `B2B SaaS & High-Growth Tech` to `Legal Services & Law Firms` or `Manufacturing & Industrial Equipment`.
  - Notice how the target job titles, company size filters, and qualification criteria instantly update.
  - Show the **+ New Profile** button to show that clients can define custom verticals with their own domain rules.

---

### Act 3: Live Lead Discovery via Free Geo-Directories (4 mins)
- **What to say:**
  > *"Instead of paying thousands of dollars for stale static databases, Harvey connects live to OpenStreetMap and public directories to discover real operational businesses."*
- **What to show:**
  - On the **Discover** tab, select location: `Austin, TX` and category: `dentist` or `software`.
  - Click **Discover Leads**.
  - Show the live request fetching real businesses with verified addresses, geo-coordinates, and external IDs.
  - Highlight the candidate cards: each card displays company name, address, website, and an **Ingest into CRM** button.

---

### Act 4: Explainable BANT Qualification Scoring (3 mins)
- **What to say:**
  > *"AI shouldn't be a black box. Harvey scores every account across the four classic BANT pillars: Budget, Authority, Need, and Timeline — scoring up to 100 points with transparent explanations."*
- **What to show:**
  - Click on the **CRM Pipeline** tab.
  - Select any qualified lead card (e.g., `Apex Cloud Technologies` or `Smiles by Dentistry`).
  - Expand the **BANT Qualification Breakdown**:
    - **Budget (25/25):** Company headcount aligns with target tooling budget.
    - **Authority (25/25):** Direct VP or C-level decision maker identified.
    - **Need (25/25):** Pain point match detected from tech stack and growth signals.
    - **Timeline (0/25):** Unverified direct channel requires research.
  - Explain that leads are categorized into High Fit, Medium Fit, and Low Fit tiers.

---

### Act 5: AI Outreach Generation & The Review Gate (4 mins)
- **What to say:**
  > *"Here is Harvey's primary safety innovation. When Harvey drafts an email, it doesn't send it. It places it in the Outbox Review Queue. Your reps or managers review, edit, or approve with one click."*
- **What to show:**
  - Click on the **Outbox** tab.
  - Point out the prominent banner:
    `Sending Mode: Pilot Evaluation - Read-Only Safety Hold (require_approval=True)`.
  - Show a staged email draft:
    - Point out the personalized subject line and concise, value-focused body.
    - Show the **Approve** and **Reject** buttons.
  - Click **Approve** on a staged draft. Show how the status changes to `approved`.
  - Explain that because the system is in evaluation safety mode, approved drafts remain held safely until authorized for sending.

---

### Act 6: Enterprise Mail Integration & Proven Delivery (3 mins)
- **What to say:**
  > *"Harvey doesn't use risky third-party bulk relays that get your domain blacklisted. It sends authenticated emails directly from your team's Google Workspace or Microsoft 365 mailbox."*
- **What to show:**
  - Navigate to the **Settings** tab.
  - Show the **Email Integration** card:
    - Point to **Gmail**: Connected as `sunilpawar82150@gmail.com`.
    - Show the live proof: exactly 1 controlled test email sent to `sunilrajput62003@gmail.com`, verified directly inside Google's official Sent folder.
    - Point to **Microsoft 365**: Ready for Azure Entra ID sign-in with 73 automated tests passing.
  - Point to the **Safety Allowlist** configuration (`TEST_RECIPIENT_EMAIL`), proving that unauthorized emails can never leave the platform.

---

### Act 7: Knowledge Store & Compliance RAG Search (3 mins)
- **What to say:**
  > *"How does Harvey know what your product does? You upload your sales decks, whitepapers, and brand guidelines into the Knowledge Store. Harvey indexes them into a local vector database and retrieves exact facts when generating copy."*
- **What to show:**
  - Click on the **Knowledge** tab.
  - Show the indexed documents table (`#rag-docs-tbody`).
  - In the **Interactive RAG Query** box, type:
    `What are Harvey's compliance rules for cold outreach?`
  - Click **Query**.
  - Show the instant retrieval of relevant chunks explaining mandatory human review, opt-out handling, and CAN-SPAM / GDPR safeguards.

---

## 4. Addressing Common Client Objections & Questions

| Client Question | Recommended Response |
| :--- | :--- |
| **"Will this get our corporate domain marked as spam?"** | *"No. Harvey is designed for targeted account-based prospecting (<50 emails/day), not bulk mass spam. It sends directly from your Google Workspace or M365 mailbox using SPF/DKIM/DMARC, spaces sends randomly, and requires human approval for every message."* |
| **"Can we use our own AI models instead of local ones?"** | *"Yes. Harvey has a pluggable provider router. While Ollama is free and private, you can switch to Google Gemini, Groq, or OpenRouter in `harvey.yaml` by providing your API key."* |
| **"Where is our prospect data stored?"** | *"100% on your own infrastructure. Harvey runs as a local Python service or Docker container with a private SQLite database. Your prospect lists, CRM notes, and email history never leave your private environment."* |
| **"How hard is it to deploy?"** | *"It deploys in under 5 minutes using Docker Compose, or as a background Windows service daemon. We provide a single pre-built source package with zero external dependencies required."* |

---

## 5. Demo Wrap-Up & Next Steps

Close the demonstration with a concrete pilot proposal:
1. **Pilot Scope:** 14-day evaluation on a dedicated secondary outbound domain.
2. **Setup Time:** 15 minutes to configure `.env` with the client's Gmail or Microsoft 365 mailbox.
3. **Success Milestone:** Generate the first 50 qualified leads and stage approved outreach drafts for sales management review.

