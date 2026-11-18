# Harvey AI Sales Agent — Friend & Collaborator Handover Guide

Welcome to **Harvey AI Sales Agent**! This guide contains everything you need to install, configure, develop, deploy, and maintain Harvey independently.

Your primary objective is to deploy Harvey 24/7 on a secure cloud server and provide the live, working public URL.

---

## 1. System Overview & Architecture

Harvey is a privacy-first, universal autonomous B2B sales development platform.

```
┌────────────────────────────────────────────────────────────────────────┐
│                        HARVEY SYSTEM TOPOLOGY                          │
├────────────────────────────────────────────────────────────────────────┤
│                                                                        │
│   Web Browser (Dashboard UI) ───[HTTPS / Bearer Token]───┐             │
│                                                          │             │
│   ┌──────────────────────────────────────────────────────┴─────────┐   │
│   │  Harvey Core Application Container                             │   │
│   │                                                                │   │
│   │  ├── FastAPI Web Server (Port 5555)                            │   │
│   │  │   ├── 15 Dashboard Module Tabs (SPA in /harvey/web)         │   │
│   │  │   └── DashboardGuard & Host Security Middleware             │   │
│   │  │                                                             │   │
│   │  ├── Autonomous Heartbeat Engine (harvey run)                  │   │
│   │  │   ├── Discovery: OpenStreetMap / Nominatim Geospatial       │   │
│   │  │   ├── Profiler: Website & Tech Stack Analysis               │   │
│   │  │   ├── People Finder: Decision Maker Extraction              │   │
│   │  │   ├── Qualifier: ICP Match Scoring & Objection Handling     │   │
│   │  │   ├── Campaign Writer: Multi-Step Grounded Outreach         │   │
│   │  │   └── Outbox Review Gate: Human Approval Safeguard          │   │
│   │  │                                                             │   │
│   │  └── Playwright Headless Chromium (Browser Automation)         │   │
│   └──────────────────────────────────┬─────────────────────────────┘   │
│                                      │                                 │
│          ┌───────────────────────────┴───────────────────────────┐     │
│          │ Persistent Storage Volume (/app/data)                 │     │
│          │  ├── harvey.db (SQLite 3 with WAL Mode)               │     │
│          │  ├── /rag/chroma (ChromaDB Vector Store)              │     │
│          │  └── dashboard-token & gmail_token.json               │     │
│          └───────────────────────────────────────────────────────┘     │
│                                                                        │
│   AI Inference: Free Cloud APIs (Google Gemini 1.5 Flash / Groq LPU)   │
│                 or Local Ollama (qwen2.5:1.5b + nomic-embed-text)      │
│                                                                        │
│   Public Access: Cloudflare Zero-Trust Tunnel (cloudflared)            │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Prerequisites

- **OS:** Ubuntu 22.04 / 24.04 LTS (recommended for cloud) or Windows 10/11 / macOS (for local dev).
- **Python:** Python 3.11 or 3.12.
- **Docker:** Docker Engine 24+ and Docker Compose v2 (for production).
- **Memory Requirement:** Minimum 2 GB RAM (Harvey peaks at ~1.75 GB when Playwright Chromium runs).

---

## 3. Local Quickstart (Development Mode)

### Step 1: Clone and Set Up Virtual Environment
```bash
git clone <your-repo-url>
cd harvey

# Create virtualenv
python -m venv .venv

# Activate virtualenv
# Linux / macOS:
source .venv/bin/activate
# Windows:
.venv\Scripts\activate

# Install dependencies in editable mode
pip install -e .

# Install Playwright browser dependencies
playwright install --with-deps chromium
```

### Step 2: Configure Environment
```bash
cp .env.example .env
```
Edit `.env` and set your preferred AI provider:
- **Zero-Cost Cloud Option (Recommended):** Get a free API key from [Google AI Studio](https://aistudio.google.com/app/apikey) and set:
  ```env
  GEMINI_API_KEY=AIzaSy...
  ```
  In `harvey.yaml`, set:
  ```yaml
  brain:
    provider: "gemini"
    model: "gemini-1.5-flash"
  ```
- **Local Ollama Option:** If you have [Ollama](https://ollama.com) installed locally:
  ```bash
  ollama run qwen2.5:1.5b
  ollama pull nomic-embed-text
  ```

### Step 3: Launch Local Dashboard
```bash
# On Linux / macOS:
python -m harvey dashboard --port 5555

# On Windows:
START_HARVEY_DEMO.bat
```
Open your browser at `http://127.0.0.1:5555`.
Unlock using the bearer token generated in `data/dashboard-token`.

---

## 4. Production Cloud Deployment (Linux VPS / Docker)

The recommended hosting target is a **Linux VPS** (e.g. Oracle Cloud Always Free ARM or Hetzner Cloud CX22 at ~₹348/mo) using the provided [docker-compose.prod.yml](docker-compose.prod.yml).

### Step 1: Install Docker on the Server
```bash
sudo apt update && sudo apt upgrade -y
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
newgrp docker
```

### Step 2: Transfer Existing Data & State
Transfer the existing SQLite database (`harvey.db`), ChromaDB embeddings (`rag/`), and token files into the server's data directory so all existing prospects and campaigns are preserved:
```bash
mkdir -p /opt/harvey/data
# Copy data from local backup or scp directly
scp -r ./data/* user@<server-ip>:/opt/harvey/data/
chmod 600 /opt/harvey/data/dashboard-token
```

### Step 3: Configure Production `.env`
```bash
cd /opt/harvey
cp .env.example .env
nano .env
```
Ensure the following variables are set:
```env
HARVEY_ALLOW_REMOTE_BIND=1
HARVEY_ALLOWED_HOSTS=localhost,127.0.0.1,*.trycloudflare.com,your-domain.com
HARVEY_PORT=5555
BRAIN_PROVIDER=gemini
GEMINI_API_KEY=your_gemini_api_key
NOMINATIM_CONTACT_EMAIL=your-real-email@domain.com
REQUIRE_APPROVAL=true
LIVE_SENDING_ENABLED=false
```

### Step 4: Build and Launch
```bash
docker compose -f docker-compose.prod.yml up -d --build
```
Verify the container is healthy:
```bash
docker compose -f docker-compose.prod.yml ps
docker logs -f harvey-sales-agent
```

---

## 5. Exposing the Public HTTPS URL (Cloudflare Tunnel)

To make Harvey accessible over the internet with zero open firewall ports and free SSL/TLS:

1. Install Cloudflare Tunnel daemon:
   ```bash
   curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
   sudo dpkg -i cloudflared.deb
   ```
2. Run a persistent Quick Tunnel (or Named Tunnel):
   ```bash
   # Quick Tunnel for instant HTTPS URL:
   cloudflared tunnel --url http://localhost:5555
   ```
3. Copy the generated `https://*.trycloudflare.com` URL (or your custom domain) and send it to the project owner!

---

## 6. Mandatory Safety Controls & Verification

Before executing any outreach, confirm that outbound sending remains paused:
1. **Require Approval:** In `harvey.yaml`, `channels.email.require_approval` must be `true`.
2. **Sending Paused:** In `.env`, `LIVE_SENDING_ENABLED` must be `false`.
3. **Gmail Read-Only:** All Gmail synchronization must use the `gmail.readonly` scope.
4. **Outbox Review:** Outbound drafts accumulate in the **Outbox** tab for manual human inspection and approval.

---

## 7. Daily Maintenance & Automated Backups

Set up an automated daily SQLite database backup on the host:
```bash
# Add to crontab -e:
0 3 * * * sqlite3 /opt/harvey/data/harvey.db ".backup '/opt/harvey/data/backups/harvey_$(date +\%Y\%m\%d).db'" && find /opt/harvey/data/backups/ -name "*.db" -mtime +14 -delete
```

---

## 8. Troubleshooting Reference

- **Dashboard says "Unauthorized":** Pass the bearer token from `data/dashboard-token` in the `#token=` URL fragment or HTTP header: `Authorization: Bearer <token>`.
- **HTTP 400 Bad Host:** If accessing via a new domain or proxy, add the hostname to `HARVEY_ALLOWED_HOSTS` in `.env`.
- **Playwright Fails to Launch:** Ensure Linux dependencies are installed via `playwright install --with-deps chromium`.
- **OOM Kill:** Ensure the VPS has at least 2 GB of RAM (or configure a 2 GB Linux swap file: `sudo fallocate -l 2G /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile`).
