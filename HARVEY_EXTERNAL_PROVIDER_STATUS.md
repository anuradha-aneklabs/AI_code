# Harvey AI Sales Agent — External Provider Status Matrix & Onboarding Guide

**Project Path:** `C:\Users\aneka\Desktop\harvey`  
**Current Git Commit:** `e1e8e8d69426f29a6ca9d1dab0cb6e713f95bcc6`  
**Audit Completion Date:** 2026-11-14  
**Operating System:** Windows 11 Enterprise (Python 3.11.9, `.venv\Scripts\python.exe`)  
**Target Node:** `http://127.0.0.1:5555/`  

---

## 1. Provider Status Matrix

| Provider | Category | Implementation Status | Verification Status | Endpoint / Host | Auth Method / Scopes | Latency | Last Verified | HTTP Code / Status Detail | Operator Action to Activate |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **OpenStreetMap** | Discovery | Fully Implemented | **LIVE VERIFIED** | `https://overpass-api.de/api/interpreter` | None (Public API) | 879 ms | 2026-11-14 13:30 UTC | HTTP 200 OK | Operational out-of-the-box. No credentials needed. |
| **Nominatim** | Geocoding | Fully Implemented | **LIVE VERIFIED** | `https://nominatim.openstreetmap.org/search` | None (Public API / User-Agent) | 412 ms | 2026-11-14 13:30 UTC | HTTP 200 OK | Operational as automatic failover when Overpass is busy. |
| **Web Scraper** | Enrichment | Fully Implemented | **LIVE VERIFIED** | Target Company Web Domains | HTTP/HTTPS GET (Public) | 350 ms | 2026-11-14 13:30 UTC | HTTP 200 OK | Operational out-of-the-box. Scrapes public web pages. |
| **ChromaDB** | Vector RAG | Fully Implemented | **LIVE VERIFIED** | Local File Store (`data/chroma`) | Local File API | 12 ms | 2026-11-14 13:30 UTC | 19 Chunks Indexed | Operational out-of-the-box. Local DuckDB/SQLite store. |
| **Ollama Completion** | AI Generation | Fully Implemented | **LIVE VERIFIED** | `http://127.0.0.1:11434/api/chat` | Local REST Socket (`qwen2.5:1.5b`) | 3,600 ms | 2026-11-14 13:30 UTC | HTTP 200 OK | Operational. Started via `ollama serve` background task. |
| **Ollama Embeddings** | AI Embeddings | Fully Implemented | **LIVE VERIFIED** | `http://127.0.0.1:11434/api/embeddings` | Local REST Socket (`nomic-embed-text`) | 48 ms | 2026-11-14 13:30 UTC | HTTP 200 OK | Operational. 768-dim embeddings generated locally. |
| **Google Gmail** | Email (Read) | Fully Implemented | **SIMULATED / SANDBOX** | `https://gmail.googleapis.com/` | OAuth 2.0 (`gmail.readonly`) | 9 ms | 2026-11-14 13:30 UTC | Sandbox Active | Supply `credentials.json` to switch to live inbox read. |
| **Microsoft 365** | Email & Cal | Fully Implemented | **CREDENTIAL BLOCKED** | `https://graph.microsoft.com/v1.0` | OAuth 2.0 PKCE (`Mail.Read`, `Calendars.Read`) | 3 ms | 2026-11-14 13:30 UTC | Token Missing | Register Azure Entra App; add `M365_CLIENT_ID` to `.env`. |
| **DataForSEO** | Discovery | Fully Implemented | **CREDENTIAL BLOCKED** | `https://api.dataforseo.com/v3` | HTTP Basic Auth | 0 ms | 2026-11-14 13:30 UTC | Credentials Missing | Add `DATAFORSEO_LOGIN` and `DATAFORSEO_PASSWORD` to `.env`. |
| **Serper** | Search SERP | Fully Implemented | **CREDENTIAL BLOCKED** | `https://google.serper.dev/search` | Header `X-API-KEY` | 0 ms | 2026-11-14 13:30 UTC | Credentials Missing | Add `SERPER_API_KEY` to `.env`. |
| **TinyFish** | Search SERP | Fully Implemented | **CREDENTIAL BLOCKED** | `https://api.search.tinyfish.ai` | Bearer Token | 0 ms | 2026-11-14 13:30 UTC | Credentials Missing | Add `TINYFISH_API_KEY` to `.env`. |
| **Scavio** | Maps / Search | Fully Implemented | **CREDENTIAL BLOCKED** | `https://api.scavio.dev/api/v2/google` | Bearer Token | 0 ms | 2026-11-14 13:30 UTC | Credentials Missing | Add `SCAVIO_API_KEY` to `.env`. |
| **ZeroBounce** | Email Verify | Fully Implemented | **CREDENTIAL BLOCKED** | `https://api.zerobounce.net/v2/validate` | Query Param `api_key` | 0 ms | 2026-11-14 13:30 UTC | Credentials Missing | Add `ZEROBOUNCE_API_KEY` to `.env`. |
| **Hunter.io** | Domain Search | Fully Implemented | **CREDENTIAL BLOCKED** | `https://api.hunter.io/v2` | Query Param `api_key` | 0 ms | 2026-11-14 13:30 UTC | Credentials Missing | Add `HUNTER_API_KEY` to `.env`. |
| **Reoon** | Email Verify | Fully Implemented | **CREDENTIAL BLOCKED** | `https://emailverifier.reoon.com/api/v1/verify` | Query Param `key` | 0 ms | 2026-11-14 13:30 UTC | Credentials Missing | Add `REOON_API_KEY` to `.env`. |
| **Cal.com** | Calendar | Fully Implemented | **CREDENTIAL BLOCKED** | `https://api.cal.com/v1` | Query Param `apiKey` | 0 ms | 2026-11-14 13:30 UTC | Credentials Missing | Add `CALCOM_API_KEY` to `.env`. |
| **Calendly** | Calendar | Fully Implemented | **CREDENTIAL BLOCKED** | `https://api.calendly.com` | Bearer Token | 0 ms | 2026-11-14 13:30 UTC | Credentials Missing | Add `CALENDLY_TOKEN` to `.env`. |

---

## 2. Operator Onboarding & Configuration Guide

To activate any of the credential-blocked integrations, provide the corresponding secrets in the local `.env` file located at `C:\Users\aneka\Desktop\harvey\.env`.

### 2.1 Microsoft 365 Azure Entra ID Configuration
1. Log in to the [Microsoft Entra ID Admin Center](https://entra.microsoft.com/).
2. Navigate to **App registrations** > **New registration**.
   - **Name:** `Harvey Sales Agent Pilot`
   - **Supported account types:** Accounts in this organizational directory only (Single tenant) or Multitenant.
   - **Redirect URI (Mobile and desktop applications):** `http://localhost:5555/api/microsoft/oauth/callback`
3. Under **API permissions**, click **Add a permission** > **Microsoft Graph** > **Delegated permissions**:
   - Add: `User.Read`
   - Add: `Mail.Read`
   - Add: `Calendars.Read`
   - Add: `offline_access`
   > [!IMPORTANT]
   > DO NOT add `Mail.Send` or `Mail.ReadWrite`. Harvey enforces strict read-only compliance and will reject tokens containing write scopes.
4. Under **Certificates & secrets**, create a new client secret and copy its value.
5. In `C:\Users\aneka\Desktop\harvey\.env`, add:
   ```env
   M365_CLIENT_ID=your_azure_client_id_here
   M365_TENANT_ID=your_azure_tenant_id_here
   M365_CLIENT_SECRET=your_azure_client_secret_here
   ```
6. Run the CLI OAuth authorization command:
   ```powershell
   .venv\Scripts\python.exe -m harvey.cli m365-auth
   ```

### 2.2 Google Workspace Gmail Live Setup
1. Log in to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create or select a project, then navigate to **APIs & Services** > **Library**.
3. Enable the **Gmail API**.
4. Go to **APIs & Services** > **Credentials** > **Create Credentials** > **OAuth client ID**.
   - Application type: **Desktop app**.
   - Download the client configuration and save as `credentials.json` in the project root.
5. In `harvey.yaml`, confirm the Gmail scope is set to `https://www.googleapis.com/auth/gmail.readonly`.
6. Run the Gmail OAuth helper:
   ```powershell
   .venv\Scripts\python.exe -m harvey.cli gmail-auth
   ```
   A browser window will open requesting authorization. Upon completion, the token is saved to `data/gmail-token.json`.

### 2.3 Search & Discovery Providers

#### DataForSEO
- Sign up at [https://app.dataforseo.com/api-access](https://app.dataforseo.com/api-access).
- Add to `.env`:
  ```env
  DATAFORSEO_LOGIN=your_email@example.com
  DATAFORSEO_PASSWORD=your_api_password
  ```

#### Serper
- Sign up at [https://serper.dev](https://serper.dev).
- Add to `.env`:
  ```env
  SERPER_API_KEY=your_serper_key_here
  ```

#### TinyFish Web Search
- Obtain an API key from [https://agent.tinyfish.ai/api-keys](https://agent.tinyfish.ai/api-keys).
- Add to `.env`:
  ```env
  TINYFISH_API_KEY=your_tinyfish_key_here
  ```

#### Scavio (Google Search & Maps)
- Sign up at [https://scavio.dev](https://scavio.dev).
- Add to `.env`:
  ```env
  SCAVIO_API_KEY=your_scavio_key_here
  ```

### 2.4 Email Verification Providers

#### ZeroBounce
- Create an account at [https://www.zerobounce.net](https://www.zerobounce.net).
- Add to `.env`:
  ```env
  ZEROBOUNCE_API_KEY=your_zerobounce_api_key_here
  ```

#### Hunter.io
- Create an account at [https://hunter.io](https://hunter.io).
- Add to `.env`:
  ```env
  HUNTER_API_KEY=your_hunter_api_key_here
  ```

#### Reoon Email Verifier
- Create an account at [https://reoon.com/email-verifier/](https://reoon.com/email-verifier/).
- Add to `.env`:
  ```env
  REOON_API_KEY=your_reoon_api_key_here
  ```

### 2.5 Calendar & Scheduling Providers

#### Cal.com
- In your Cal.com settings, generate an API key under **Settings** > **Developer** > **API Keys**.
- Add to `.env`:
  ```env
  CALCOM_API_KEY=your_calcom_api_key_here
  ```

#### Calendly
- In your Calendly account, go to **Integrations** > **API and Webhooks** > **Personal Access Tokens**.
- Generate a token and add to `.env`:
  ```env
  CALENDLY_TOKEN=your_calendly_pat_here
  ```

---

## 3. Testing Provider Connectivity

Once keys are placed in `.env`, verify all connections safely without consuming significant quotas or sending any email:

```powershell
.venv\Scripts\python.exe scratch/test_all_connections.py
```

The script queries the Harvey unified endpoint `/api/integrations/test-connection` for each provider, verifies authentication and status, measures round-trip latency in milliseconds, and updates the database tables `integrations` and `provider_logs`.

---

## 4. Security & Compliance Rules for Operators

1. **Never Commit Secrets:** Never commit `.env`, `credentials.json`, `data/gmail-token.json`, or `data/m365-token.json` to Git. The `.gitignore` file enforces this by default.
2. **Minimal Privileges:** Never grant write permissions to mailboxes. Only authorize `gmail.readonly` and Microsoft Graph `Mail.Read`, `Calendars.Read`, `User.Read`.
3. **Safety Setting:** Verify that `sending_paused` remains set to `"Pilot Evaluation - Read-Only Safety Hold"` until full manual sign-off by the compliance officer.
4. **Approval Required:** Ensure `require_approval: true` remains set in [harvey.yaml](file:///C:/Users/aneka/Desktop/harvey/harvey.yaml).
