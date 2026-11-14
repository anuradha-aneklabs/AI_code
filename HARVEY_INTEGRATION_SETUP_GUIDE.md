# Harvey AI Sales Agent — Integration Setup & Operator Guide

**Document Version:** 1.0.0  
**Date:** November 13, 2026  
**Project Path:** `C:\Users\aneka\Desktop\harvey`  
**Target Environment:** Local Pilot / Controlled Production  

---

## 1. Overview

This guide provides instructions for configuring credentials and authentication for all 14 integrations supported by Harvey AI Sales Agent. During development and local pilot evaluation, Harvey operates with local LLMs (Ollama) and public geocoding services (OpenStreetMap) without requiring paid external API subscriptions. When ready to enable external data providers, follow the step-by-step instructions below.

> [!IMPORTANT]
> Never commit real API keys, tokens, or secret files (`.env`, `credentials.json`, `token.json`) to git version control. Use `.gitignore` to protect all secret files.

---

## 2. Environment Variables Reference (`.env.example`)

Create or update `.env` in the project root with the required keys:

```bash
# ==========================================
# HARVEY AI SALES AGENT — ENVIRONMENT CONFIG
# ==========================================

# 1. CORE APPLICATION & DASHBOARD
PORT=5555
HOST=127.0.0.1
HARVEY_ENV=production
DATABASE_PATH=data/harvey.db

# 2. LOCAL AI BRAIN (OLLAMA)
OLLAMA_BASE_URL=http://127.0.0.1:11434
DEFAULT_BRAIN_MODEL=qwen2.5:1.5b
DEFAULT_EMBED_MODEL=nomic-embed-text

# 3. CLOUD AI (ANTHROPIC CLAUDE) — OPTIONAL FALLBACK
ANTHROPIC_API_KEY=sk-ant-api03-REDACTED_API_KEY

# 4. GMAIL READ-ONLY OAUTH 2.0
GMAIL_CLIENT_ID=REDACTED_CLIENT_ID.apps.googleusercontent.com
GMAIL_CLIENT_SECRET=GOCSPX-REDACTED_CLIENT_SECRET
GMAIL_REDIRECT_URI=http://127.0.0.1:5555/oauth2callback
GMAIL_SCOPES=https://www.googleapis.com/auth/gmail.readonly

# 5. MICROSOFT 365 / AZURE AD OAUTH 2.0
M365_CLIENT_ID=00000000-0000-0000-0000-000000000000
M365_TENANT_ID=common
M365_CLIENT_SECRET=REDACTED_M365_CLIENT_SECRET
M365_REDIRECT_URI=http://127.0.0.1:5555/api/microsoft/authorize

# 6. LEAD DISCOVERY PROVIDERS
DATAFORSEO_LOGIN=user@example.com
DATAFORSEO_PASSWORD=REDACTED_DATAFORSEO_PASSWORD
SERPER_API_KEY=REDACTED_SERPER_API_KEY

# 7. WEB SCRAPING & INTELLIGENCE
TINYFISH_API_KEY=tf_live_REDACTED_KEY
SCAVIO_API_KEY=scavio_live_REDACTED_KEY

# 8. EMAIL VERIFICATION
ZEROBOUNCE_API_KEY=REDACTED_ZEROBOUNCE_KEY

# 9. CALENDAR SCHEDULING
CALCOM_API_KEY=cal_live_REDACTED_KEY
CALENDLY_TOKEN=REDACTED_CALENDLY_PERSONAL_TOKEN

# 10. SAFETY CONTROLS (MANDATORY IN PILOT)
SENDING_PAUSED="Pilot Evaluation - Read-Only Safety Hold"
REQUIRE_APPROVAL=true
```

---

## 3. Step-by-Step Provider Setup

### 3.1. OpenStreetMap (Zero Setup Required)
- **Status:** Pre-configured and active by default.
- **Cost:** Free, public geocoding and business search via Overpass QL and Nominatim.
- **Attribution:** Retains OpenStreetMap contributors attribution under ODbL.

### 3.2. Local Ollama AI Engine (Recommended for Privacy & Offline Use)
1. Install Ollama from [ollama.com](https://ollama.com).
2. Start the Ollama background daemon:
   ```powershell
   ollama serve
   ```
3. Pull the required models:
   ```powershell
   ollama pull qwen2.5:1.5b
   ollama pull nomic-embed-text
   ```
4. Verify connectivity:
   ```powershell
   curl http://127.0.0.1:11434/api/tags
   ```

### 3.3. Gmail Read-Only OAuth 2.0 Integration
1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project: `Harvey-AI-Agent`.
3. Enable the **Gmail API** under *APIs & Services > Library*.
4. Configure the **OAuth Consent Screen**:
   - User Type: *External* (or *Internal* if using Google Workspace).
   - Add scope: `https://www.googleapis.com/auth/gmail.readonly`.
   - Add your test user email address.
5. Create **OAuth Client ID** credentials:
   - Application Type: *Web application*.
   - Authorized redirect URI: `http://127.0.0.1:5555/oauth2callback`.
6. Download credentials JSON to `data/credentials/gmail_credentials.json`.
7. Click **Connect Gmail** in the Harvey Dashboard (Settings tab) to authorize.

### 3.4. Microsoft 365 / Azure AD OAuth 2.0 Integration
1. Sign in to the [Microsoft Entra Admin Center](https://entra.microsoft.com/).
2. Navigate to *Identity > Applications > App registrations*.
3. Click **New registration**:
   - Name: `Harvey Sales Agent`.
   - Supported account types: *Accounts in any organizational directory (Multitenant) and personal Microsoft accounts*.
   - Redirect URI (Web): `http://127.0.0.1:5555/api/microsoft/authorize`.
4. Under *API Permissions*, click **Add a permission > Microsoft Graph > Delegated permissions**:
   - `User.Read`
   - `Mail.Read`
   - `offline_access`
5. Under *Certificates & Secrets*, generate a new Client Secret and record the value.
6. Set `M365_CLIENT_ID`, `M365_TENANT_ID`, and `M365_CLIENT_SECRET` in `.env`.
7. In the Harvey Dashboard Settings tab, click **Sign In with Microsoft**.

### 3.5. DataForSEO & Serper (Lead Discovery)
1. **DataForSEO:**
   - Register at [dataforseo.com](https://dataforseo.com/).
   - Obtain your API Login and Password from the DataForSEO dashboard.
   - Set `DATAFORSEO_LOGIN` and `DATAFORSEO_PASSWORD` in `.env`.
2. **Serper:**
   - Register at [serper.dev](https://serper.dev/).
   - Copy your API Key and set `SERPER_API_KEY` in `.env`.

### 3.6. ZeroBounce (Email Verification)
1. Sign up at [zerobounce.net](https://www.zerobounce.net/).
2. Generate an API Key under *API > API Keys*.
3. Set `ZEROBOUNCE_API_KEY` in `.env`.
4. Test connection via the Dashboard Settings Integrations card.

### 3.7. Cal.com & Calendly (Meeting Scheduling)
1. **Cal.com:**
   - Navigate to [cal.com/settings/developer/api-keys](https://cal.com/settings/developer/api-keys).
   - Generate a new API Key.
   - Set `CALCOM_API_KEY` in `.env`.
2. **Calendly:**
   - Navigate to [calendly.com/integrations/api_webhooks](https://calendly.com/integrations/api_webhooks).
   - Generate a Personal Access Token with read access.
   - Set `CALENDLY_TOKEN` in `.env`.

---

## 4. Testing & Verifying Connections

Harvey includes a built-in connection verification utility. In the Dashboard (`http://127.0.0.1:5555/`), navigate to the **Settings** tab:

1. Scroll to the **Integrations Management Matrix**.
2. Locate the provider card (e.g., OpenStreetMap, Microsoft 365, ZeroBounce).
3. Click the **Test Connection** button on the card.
4. The system executes a live test, updates the card status badge (`Connected` or `Disconnected`), and logs the request to the `provider_logs` audit table.

