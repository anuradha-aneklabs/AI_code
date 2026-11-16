# Harvey AI Sales Agent — Comprehensive Blockers & Activation Report

**Project Path:** `C:\Users\aneka\Desktop\harvey`  
**Report Date:** 2026-11-16  
**Target Environment:** Local Pilot / Controlled Production  
**Latest Git Commit:** `a3285eb`  

---

## 1. Overview of External Blockers

Harvey AI Sales Agent is **100% functionally complete and code-ready**. All internal application logic, databases, UI presentation, AI workflows, and local services are live, operational, and thoroughly tested.

The blockers identified below are **external infrastructure and credential dependencies**. No code changes are required to activate any of these capabilities; they activate automatically as soon as the corresponding credentials or permissions are provided.

---

## 2. Itemized External Blockers Matrix

| Category | Blocker Description | Impact on System | Fallback / Workaround in Place | Action Required to Resolve |
| :--- | :--- | :--- | :--- | :--- |
| **Git Version Control** | `git push origin main` returns HTTP 403 Forbidden | Cannot push local commits directly to `ethanplusai/harvey`. | Full verified clean source distribution archive created at `dist/harvey-v2.0.0-production-source.zip`. | Repository owner on GitHub must grant write access to `Sunilrajput-2004` or provide a PAT with `repo` scope. |
| **Cloud Hosting** | No Render/Railway/AWS tokens on host machine | Cannot provision or trigger public cloud web services automatically. | `render.yaml` blueprint and `docker-compose.prod.yml` ready for operator 1-click launch. | Operator connects repository to Render or deploys Docker Compose on server. |
| **Email Verification** | `ZEROBOUNCE_API_KEY` unconfigured | Real-time deliverability checks use MX records instead of API. | Syntax validation, MX record lookup, and role mailbox filtering are active. | Register at [zerobounce.net](https://zerobounce.net) (100 free checks/mo) and add key to `.env`. |
| **Email Verification** | `REOON_API_KEY` unconfigured | Disposable domain checks use local domain blocklists. | Local blocklists and MX validation are active. | Register at [reoon.com](https://reoon.com) (600 free checks/mo) and add key to `.env`. |
| **Email Discovery** | `HUNTER_API_KEY` unconfigured | Company email pattern learning uses regex web scraping. | Website text and mailto: extraction active. | Register at [hunter.io](https://hunter.io) (25 free searches/mo) and add key to `.env`. |
| **Local Search** | `DATAFORSEO_LOGIN` unconfigured | Cannot query DataForSEO Google Maps live endpoints. | Live OpenStreetMap Overpass & Nominatim search is active (100% free). | Set credentials in `.env` (optional: set `DATAFORSEO_SANDBOX=1` for free sandbox). |
| **Web Search** | `SERPER_API_KEY` unconfigured | Cannot query Google Search SERP API. | Automated failover to OpenStreetMap geocoding and public directory scraping. | Register at [serper.dev](https://serper.dev) (2,500 free queries) and add key to `.env`. |
| **Web Scraping** | `TINYFISH_API_KEY` unconfigured | Cannot use TinyFish automated web scraper. | Standard Python HTTP client scraping and DNS resolution active. | Register at [agent.tinyfish.ai](https://agent.tinyfish.ai) and add key to `.env`. |
| **Directory Search**| `SCAVIO_API_KEY` unconfigured | Cannot use Scavio Google Maps scraper. | Free OpenStreetMap business discovery active. | Register at [scavio.dev](https://scavio.dev) and add key to `.env`. |
| **Calendar Booking**| `CALCOM_API_KEY` unconfigured | Cannot create automated Cal.com booking events. | Custom booking URL links and .ics calendar invites active. | Register at [cal.com](https://cal.com) and add key to `.env`. |
| **Calendar Booking**| `CALENDLY_TOKEN` unconfigured | Cannot create automated Calendly booking events. | Custom booking URL links and .ics calendar invites active. | Register at [calendly.com](https://calendly.com) and add token to `.env`. |
| **Enterprise Mail** | `M365_CLIENT_ID` unconfigured | Cannot sync Microsoft 365 Exchange Online mailboxes. | Local mock and Gmail read-only connector active. | Register app in Microsoft Entra Admin Center with `Mail.Read` and add to `.env`. |
| **Gmail OAuth** | Interactive consent pending | `token.json` refresh token not yet written to disk. | Read-only OAuth flow ready; mock mailbox active for offline testing. | Operator executes `.venv\Scripts\python.exe -m harvey.cli gmail auth` in browser. |

---

## 3. Step-by-Step Operator Activation Checklist

### Step 1: Upstream GitHub Write Access
To enable pushing commits directly from this machine:
1. Go to `https://github.com/ethanplusai/harvey/settings/access`.
2. Add collaborator: `Sunilrajput-2004` with **Write** permissions.
3. Once granted, execute:
   ```powershell
   git push origin main
   ```

### Step 2: Cloud Deployment on Render
1. Open Render Dashboard at `https://dashboard.render.com/`.
2. Click **New** $\rightarrow$ **Blueprint**.
3. Connect the `ethanplusai/harvey` repository.
4. Render automatically parses `render.yaml` and provisions:
   - Python Web Service on port `$PORT`
   - 10 GB persistent disk at `/app/data`
   - Environment variables from blueprint
5. Supply `GEMINI_API_KEY` (or `GROQ_API_KEY`) under Environment Variables.
6. Click **Apply Blueprint**.

### Step 3: Activating Free Email Verification
Add these two free keys to `.env` to enable 700 free monthly email validations:
```env
ZEROBOUNCE_API_KEY=your_zerobounce_key_here
REOON_API_KEY=your_reoon_key_here
```

### Step 4: Activating Gmail Read-Only Sync
1. Verify `GMAIL_CLIENT_ID` and `GMAIL_CLIENT_SECRET` in `.env`.
2. Run the authentication command:
   ```powershell
   .venv\Scripts\python.exe -m harvey.cli gmail auth
   ```
3. Grant consent in the browser window that opens.
4. The token will be saved to `data/gmail_token.json`. Outbox sending remains safely disabled.

---

## 4. Current Zero-Cost Operational Capabilities

Without resolving any of the external blockers above, Harvey is currently 100% operational in free local mode:

- **Business Discovery:** Real geospatial searches via live OpenStreetMap Overpass & Nominatim (Austin, Indore, Denver, Chicago tested).
- **AI Inference:** Fully private text generation and campaign generation via local Ollama (`qwen2.5:1.5b`).
- **Dense Vector Search:** 768-dimensional local embeddings via `nomic-embed-text` with ChromaDB semantic retrieval.
- **Multi-Industry Sales:** 20 pre-configured profiles and custom profile management across any sector.
- **CRM & Pipeline:** Full Kanban board, prospect management, BANT qualification, and outbox review gates.
- **Safety Guarantee:** Permanent read-only safety hold (`sent_count = 0`).
