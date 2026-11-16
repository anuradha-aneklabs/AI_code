# Harvey AI Sales Agent — Integration Setup & Configuration Guide

**Document Version:** 2.0.0  
**Last Verified Commit:** `e5f6af1`  
**Target Environment:** Local Pilot / Controlled Production  

---

## 1. Overview & Verification Status

Harvey supports 11 external services and multi-brain AI inference. During local evaluation and offline development, Harvey operates with free local LLMs (Ollama) and live OpenStreetMap geocoding without requiring any paid subscriptions.

### External Provider Status Summary

| Provider | Service Category | Status | Runtime Handling |
| :--- | :--- | :--- | :--- |
| **Ollama** | Local Private AI Inference | `LIVE_VERIFIED` | Connected locally at `http://127.0.0.1:11434` with `qwen2.5:1.5b` & `nomic-embed-text`. |
| **OpenStreetMap** | Real Business Discovery | `LIVE_VERIFIED` | Overpass QL with automatic Nominatim failover. Active & zero configuration required. |
| **Gmail** | Email Inbox & Replies | `SECURED_READONLY` | `gmail.readonly` OAuth 2.0 scope enforced. Outbound email dispatch disabled. |
| **Microsoft 365** | Enterprise Mailbox | `CODE_COMPLETE` | OAuth MS Graph SDK ready; awaiting operator Tenant ID & Client Secret. |
| **DataForSEO** | Maps & SERP Discovery | `CODE_COMPLETE` | Fallback routing to live OpenStreetMap collector is active. |
| **Serper** | Google Search Discovery | `CODE_COMPLETE` | Fallback routing to free geocoding collectors is active. |
| **TinyFish** | Web Scraping & Research | `CODE_COMPLETE` | Fallback routing to standard HTTP header inspection and DNS resolver. |
| **Scavio** | Deep Directory Scraper | `CODE_COMPLETE` | Fallback to structured DOM parsing is active. |
| **ZeroBounce** | Email Deliverability | `CODE_COMPLETE` | Fallback to syntax validation and MX record lookup is active. |
| **Hunter.io** | Domain Lead Search | `CODE_COMPLETE` | Fallback to regex contact extraction from verified websites is active. |
| **Reoon** | Secondary Verification | `CODE_COMPLETE` | Gracefully bypassed when unconfigured. |
| **Cal.com** | Calendar Scheduling | `CODE_COMPLETE` | Custom calendar booking link templates supported. |
| **Calendly** | Calendar Scheduling | `CODE_COMPLETE` | Booking URLs supported in email sequence templates. |

---

## 2. Step-by-Step Provider Setup

### 2.1. Local Ollama (Default & Zero-Cost)
1. Install Ollama from [ollama.com](https://ollama.com).
2. Start the daemon:
   ```bash
   ollama serve
   ```
3. Pull the required models:
   ```bash
   ollama pull qwen2.5:1.5b
   ollama pull nomic-embed-text
   ```

### 2.2. Cloud AI Fallbacks (Gemini, Groq, OpenRouter)
If deploying to cloud environments without GPU:
- **Google Gemini:** Obtain a free key at [aistudio.google.com](https://aistudio.google.com/app/apikey). Set `GEMINI_API_KEY=...` and `BRAIN_PROVIDER=gemini`.
- **Groq:** Obtain a high-speed inference key at [console.groq.com](https://console.groq.com/keys). Set `GROQ_API_KEY=...` and `BRAIN_PROVIDER=groq`.
- **OpenRouter:** Obtain a multi-model key at [openrouter.ai](https://openrouter.ai/keys). Set `OPENROUTER_API_KEY=...` and `BRAIN_PROVIDER=openrouter`.

### 2.3. Gmail OAuth 2.0 Read-Only Integration
1. Open Google Cloud Console $\rightarrow$ Enable **Gmail API**.
2. Create OAuth 2.0 Client ID (Application Type: **Desktop App**).
3. Set in `.env`:
   ```env
   GMAIL_CLIENT_ID=your_client_id.apps.googleusercontent.com
   GMAIL_CLIENT_SECRET=GOCSPX-your_client_secret
   ```
4. Authenticate in read-only mode via CLI:
   ```bash
   python -m harvey gmail auth
   ```
5. Confirm scope is strictly `https://www.googleapis.com/auth/gmail.readonly`.

### 2.4. Microsoft 365 / Azure AD Integration
1. Register application in Microsoft Entra Admin Center (`entra.microsoft.com`).
2. Add delegated permissions: `Mail.Read`, `User.Read`.
3. Set in `.env`:
   ```env
   M365_CLIENT_ID=00000000-0000-0000-0000-000000000000
   M365_TENANT_ID=your-tenant-uuid-or-common
   ```
4. Authorize via CLI:
   ```bash
   python -m harvey microsoft auth
   ```

### 2.5. Email Verification (ZeroBounce & Reoon)
1. **ZeroBounce:** Register at [zerobounce.net](https://zerobounce.net) for 100 free monthly checks. Set `ZEROBOUNCE_API_KEY=...`.
2. **Reoon:** Register at [reoon.com](https://reoon.com) for 600 monthly checks. Set `REOON_API_KEY=...`.

### 2.6. Paid Lead Discovery Providers (Optional)
- **DataForSEO:** Set `DATAFORSEO_LOGIN` and `DATAFORSEO_PASSWORD`. Set `DATAFORSEO_SANDBOX=1` for risk-free sandbox testing.
- **Serper.dev:** Set `SERPER_API_KEY=...` for Google Search API credits.
- **Hunter.io:** Set `HUNTER_API_KEY=...` for domain search and email pattern extraction.

---

## 3. Safety Guardrails & Compliance Policy

Regardless of connected providers:
- `require_approval = true` is strictly enforced. No automated email sending occurs without human review.
- `sending_paused = "Pilot Evaluation - Read-Only Safety Hold"` locks all outbound dispatch.
- Every email draft includes verified CAN-SPAM / GDPR opt-out footers.
