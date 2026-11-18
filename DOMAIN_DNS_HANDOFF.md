# Aneka Labs — Harvey AI Sales Agent Domain & DNS Handoff

**Project:** Harvey AI Sales Agent
**Prepared For:** Jitesh Sir & Aneka Labs Infrastructure Team
**Primary Domain:** `anekalabs.com`
**Date:** November 18, 2026
**Document Status:** Ready for DNS Record Population upon Hosting Provisioning

---

## 1. Executive Summary

This document defines the complete domain and DNS routing specification for deploying the Harvey AI Sales Agent on the Aneka Labs primary corporate domain (`anekalabs.com`).

Per architectural design:
- All services run within a unified, high-performance containerized backend.
- RAG (Retrieval-Augmented Generation) and vector search operate in-process inside the backend container; **no separate RAG subdomain is required**.
- All three subdomains terminate at the reverse proxy (Nginx / Cloudflare), which enforces TLS/HTTPS and routes requests to port `5555`.

> [!IMPORTANT]
> **Do NOT invent arbitrary IP addresses or publish unverified A records.**
> The exact target IP address (for an A record) or target CNAME hostname (for Cloudflare / AWS ALBs) must be provided by Jitesh Sir or the hosting infrastructure provider once the target server or cloud instance is provisioned.

---

## 2. Subdomain Architecture & Purpose

| Subdomain | Target Component | Purpose & Access Pattern | Public / Private |
| :--- | :--- | :--- | :--- |
| **`harvey.anekalabs.com`** | Harvey Single-Page Application (SPA) Web Deck | Primary operator interface. Serves the 15-tab dashboard (Overview, Campaigns, Contacts, Outbox, Conversations, Signals, Discover, Knowledge, Controls, Settings, etc.). | Operator / Authenticated (Bearer Token Gate) |
| **`api.harvey.anekalabs.com`** | FastAPI / Uvicorn REST & Streaming API | Direct REST API gateway for automation pipelines, webhook integrations, and programmatic prospect updates. | Authenticated REST Clients & Integrations |
| **`docs.harvey.anekalabs.com`** | FastAPI OpenAPI Interactive Documentation | Live Swagger UI / OpenAPI 3.0 specification (`/docs`) for inspecting endpoints, schemas, and request payloads. | Engineering & Integration Team |

---

## 3. DNS Configuration Plan (Placeholders Awaiting Provider IP)

Once the production Linux server (e.g., Oracle Cloud Always Free, Hetzner VPS, AWS EC2, or Railway) is assigned an IP or hostname, create the following DNS records in Aneka Labs' DNS management console (e.g. Cloudflare, GoDaddy, Route53):

### Option A: Standard Direct VPS Deployment (A Records)

*Use this when Harvey is assigned a dedicated static IPv4 address:*

| Record Type | Host / Name | Value / Target | TTL | Proxy Status | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **A** | `harvey` | `<AWAITING_HOSTING_PROVIDER_STATIC_IP>` | Auto / 300s | DNS Only (or Proxied) | Pending Server IP |
| **A** | `api.harvey` | `<AWAITING_HOSTING_PROVIDER_STATIC_IP>` | Auto / 300s | DNS Only (or Proxied) | Pending Server IP |
| **A** | `docs.harvey` | `<AWAITING_HOSTING_PROVIDER_STATIC_IP>` | Auto / 300s | DNS Only (or Proxied) | Pending Server IP |

---

### Option B: Cloudflare Tunnel / PaaS Deployment (CNAME Records)

*Use this if deploying behind a Cloudflare Tunnel, Railway, or AWS CNAME target:*

| Record Type | Host / Name | Value / Target | TTL | Proxy Status | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **CNAME** | `harvey` | `<AWAITING_TUNNEL_OR_PAAS_TARGET_HOSTNAME>` | Auto | Proxied (Orange Cloud) | Pending Target Host |
| **CNAME** | `api.harvey` | `<AWAITING_TUNNEL_OR_PAAS_TARGET_HOSTNAME>` | Auto | Proxied (Orange Cloud) | Pending Target Host |
| **CNAME** | `docs.harvey` | `<AWAITING_TUNNEL_OR_PAAS_TARGET_HOSTNAME>` | Auto | Proxied (Orange Cloud) | Pending Target Host |

---

## 4. Required Hosting Information Checklist

Before finalizing DNS records, the hosting administrator should supply the following parameters:

1. [ ] **Selected Hosting Platform:** (e.g., Oracle Cloud Always Free ARM / Hetzner CX22 / AWS EC2 / Render)
2. [ ] **Public Static IPv4 Address:** `___ . ___ . ___ . ___`
3. [ ] **Public Static IPv6 Address (Optional):** `____:____:____:____`
4. [ ] **SSL / TLS Termination Strategy:**
   - [ ] Automated Let's Encrypt via Certbot on Nginx
   - [ ] Cloudflare Universal SSL (Edge Termination + Flexible/Full origin)
   - [ ] Cloudflare Origin Certificate installed on reverse proxy
5. [ ] **Reverse Proxy Host Header Verification:**
   - Confirm Nginx or Cloudflare passes:
     - `Host: $host`
     - `X-Forwarded-For: $proxy_add_x_forwarded_for`
     - `X-Forwarded-Proto: $scheme`

---

## 5. Application Host Validation Safeguard

Harvey includes a cryptographic and host-header protection layer (`DashboardGuard`) in `harvey/dashboard_auth.py` that automatically blocks DNS rebinding and malicious spoofing attacks.

In `/opt/harvey/.env`, set:
```ini
HARVEY_ALLOW_REMOTE_BIND=1
HARVEY_ALLOWED_HOSTS=harvey.anekalabs.com,api.harvey.anekalabs.com,docs.harvey.anekalabs.com,localhost,127.0.0.1
```

If a request arrives with an unrecognized `Host` header (or spoofed `X-Forwarded-Host`), Harvey will reject the connection with `HTTP 400 Bad Request: error: bad_host`. Ensuring these three subdomains are in `HARVEY_ALLOWED_HOSTS` guarantees smooth, error-free operation.

---

## 6. Verification Steps Once DNS is Active

Run the following checks from an external workstation once DNS propagates:

```bash
# 1. Verify DNS Resolution
dig +short harvey.anekalabs.com
dig +short api.harvey.anekalabs.com
dig +short docs.harvey.anekalabs.com

# 2. Test SSL Handshake
curl -Iv https://harvey.anekalabs.com

# 3. Test API Gateway Health Endpoint
curl -fsSL https://api.harvey.anekalabs.com/api/harvey/status \
  -H "Authorization: Bearer <DASHBOARD_TOKEN>"

# 4. Open UI Dashboard in Browser
# Navigate to: https://harvey.anekalabs.com/?token=<DASHBOARD_TOKEN>
```
