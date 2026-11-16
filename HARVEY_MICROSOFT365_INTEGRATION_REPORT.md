# HARVEY AI SALES AGENT — MICROSOFT 365 INTEGRATION AUDIT & SETUP REPORT

**Project Path:** `C:\Users\aneka\Desktop\harvey`  
**Integration Module:** `harvey/integrations/microsoft.py`  
**Current Status:** **CREDENTIAL REQUIRED**  
**Audit Date:** November 16, 2026  
**Auditor:** Antigravity AI  

---

## 1. Executive Summary

This report documents the architectural, security, and live runtime status of the Microsoft 365 / Microsoft Graph integration in Harvey.

The Microsoft 365 provider connects to Exchange Online using the Microsoft Graph REST API (`https://graph.microsoft.com/v1.0`), eliminating the need for deprecated IMAP/Basic authentication. It utilizes OAuth 2.0 with PKCE (Proof Key for Code Exchange) as a public client or authorization code grant, supporting automated token refresh, mailbox reading, thread correlation, and outbound message dispatch.

In the active local environment:
- `M365_CLIENT_ID` and `M365_TENANT_ID` are not yet configured in `.env`.
- `data/m365_token.json` does not exist.
- Real API probes confirm that Microsoft identity servers (`https://login.microsoftonline.com`) and Microsoft Graph (`https://graph.microsoft.com`) are live, accessible, and responsive.
- Running `harvey microsoft test` accurately and honestly reports `✗ M365_CLIENT_ID missing from .env`.

---

## 2. Technical Architecture & Endpoints

| Component | Specification / Configuration |
| :--- | :--- |
| **Source Implementation** | [`harvey/integrations/microsoft.py`](file:///C:/Users/aneka/Desktop/harvey/harvey/integrations/microsoft.py) |
| **Microsoft Graph Base** | `https://graph.microsoft.com/v1.0` |
| **OpenID Discovery** | `https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration` |
| **OAuth Token URL** | `https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token` |
| **OAuth Authorize URL**| `https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize` |
| **Redirect URI / Loopback** | `http://localhost:8766` (Handled by built-in Python loopback listener) |
| **Active Read Scope** | `offline_access https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Calendars.Read https://graph.microsoft.com/User.Read` |
| **Sending Scope** | `https://graph.microsoft.com/Mail.Send` or `Mail.ReadWrite` |
| **Token Storage** | `data/m365_token.json` (Mode 600 filesystem permissions) |
| **Paging & Pacing** | Max 40 inbox pages per cycle, 7-day lookback interval |

---

## 3. Live Verification & Probe Evidence

### Test Execution 1: CLI Diagnostic Probe
Command executed:
```powershell
.venv\Scripts\python.exe -m harvey.cli microsoft test
```
Result:
```text
✗ M365_CLIENT_ID missing from .env
```

### Test Execution 2: Real HTTP Endpoint Probes
- **Microsoft OpenID Configuration (`https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration`):**
  - Probe: `GET https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration`
  - Response: **HTTP 200 OK** (Microsoft Identity v2.0 endpoints verified online).
- **Microsoft Graph API Base (`https://graph.microsoft.com/v1.0`):**
  - Probe: `GET https://graph.microsoft.com/v1.0`
  - Response: **HTTP 200 / Live** (Graph API online and accessible from this network).

---

## 4. Operator Activation & Authorization Instructions

To connect an enterprise Microsoft 365 or Outlook mailbox for client demonstration:

### Step 1: Microsoft Entra ID App Registration
1. Sign in to the [Microsoft Entra Admin Center](https://entra.microsoft.com/) (formerly Azure Active Directory).
2. Navigate to **Identity → Applications → App registrations**.
3. Click **+ New registration**:
   - **Name:** `Harvey AI Sales Agent`
   - **Supported account types:** `Accounts in this organizational directory only` (Single tenant) or `Accounts in any organizational directory` (Multitenant).
   - **Redirect URI:**
     - Platform: **Public client/native (mobile & desktop)**
     - URI: `http://localhost:8766`
4. Click **Register**.
5. From the **Overview** blade, copy:
   - **Application (client) ID**
   - **Directory (tenant) ID**
6. Navigate to **API permissions**:
   - Click **+ Add a permission → Microsoft Graph → Delegated permissions**.
   - Add:
     - `User.Read`
     - `Mail.Read`
     - `Calendars.Read`
     - `offline_access`
     - *(Optional, only if email sending is authorized)*: `Mail.Send`
   - Click **Add permissions**. If prompted, click **Grant admin consent for <Organization>**.

### Step 2: Configure Environment Variables
In `C:\Users\aneka\Desktop\harvey\.env`:
```env
M365_CLIENT_ID="<your-application-client-id>"
M365_TENANT_ID="<your-directory-tenant-id>"
M365_USER_EMAIL="sales@yourdomain.com"
```

### Step 3: Complete Interactive OAuth Authorization
Run the interactive CLI authorization:
```powershell
.venv\Scripts\python.exe -m harvey.cli microsoft auth
```
1. Harvey launches a local HTTP listener on `http://localhost:8766` and opens your browser.
2. Sign in with your Microsoft 365 work account.
3. Review and accept the requested delegated permissions.
4. The authorization code is received at `http://localhost:8766` and exchanged for a refresh token.
5. The token is stored in `data/m365_token.json`.

### Step 4: Verify Live Mailbox Connectivity
```powershell
.venv\Scripts\python.exe -m harvey.cli microsoft test
```
Expected output:
```text
✓ Signed in as sales@yourdomain.com
✓ Microsoft Graph API connection verified
```

