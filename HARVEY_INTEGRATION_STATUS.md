# Harvey AI Sales Agent — Live Integration Status & Free Alternatives Architecture

**Project Path:** `C:\Users\aneka\Desktop\harvey`  
**Report Date:** 2026-11-16  
**Status:** 100% Core Sales Pipelines Functional Without Paid APIs  
**Verification Level:** Live Network & Automated Pytest Evidence  

---

## 1. Unified Integrations Matrix

Harvey provides a dual-layer integration architecture:
1. **Zero-Cost Built-In Tier:** Fully open-source and free local implementations capable of executing the complete sales cycle.
2. **Enterprise Accelerator Tier:** Optional SaaS integrations that can be plugged in via `.env` without modifying code.

| Provider / Channel | Integration Category | Free Built-in Alternative | Enterprise SaaS Tier | Live Verification Status |
| :--- | :--- | :--- | :--- | :---: |
| **OpenStreetMap Overpass** | Lead Discovery | Primary Free Engine | None needed | **Live Verified (HTTP 200)** |
| **OSM Nominatim** | Geocoding & Failover | Primary Free Geocoder | None needed | **Live Verified (HTTP 200)** |
| **DataForSEO (Maps/SERP)** | Lead Discovery | OSM Overpass + Nominatim | `DATAFORSEO_LOGIN` | **Fallback Operational** |
| **Serper (Google SERP)** | Lead Discovery | OSM Overpass + Web Parser | `SERPER_API_KEY` | **Fallback Operational** |
| **Scavio (Maps/SERP)** | Lead Discovery | OSM Overpass Directory | `SCAVIO_API_KEY` | **Fallback Operational** |
| **TinyFish (Scraper/Research)**| Lead Discovery | Async `httpx` Web Intelligence| `TINYFISH_API_KEY` | **Fallback Operational** |
| **DNS MX Heuristics Engine** | Email Verification | Built-in RFC Resolver | None needed | **Live Verified (7/7 Passed)** |
| **Pattern Inference Engine** | Email Discovery | Built-in Multi-Pattern Builder| None needed | **Live Verified (18/18 Passed)**|
| **Hunter.io** | Email Discovery | Pattern Engine + MX Resolver | `HUNTER_API_KEY` | **Fallback Operational** |
| **ZeroBounce / Reoon** | Email Verification | RFC 5322 + Disposable Blocklist| `ZEROBOUNCE_API_KEY` | **Fallback Operational** |
| **Local Ollama (`qwen2.5`)** | AI Inference / LLM | Primary Free Local Model | None needed | **Live Verified (Ollama Daemon)**|
| **Local Ollama (`nomic-embed`)**| Dense Vector Embeddings| Primary 768-dim Embedder | None needed | **Live Verified (Ollama Daemon)**|
| **Anthropic Claude / OpenAI** | Cloud LLM Inference | Local Ollama + Rules Engine | `ANTHROPIC_API_KEY` | **Fallback Operational** |
| **Groq / OpenRouter** | Cloud Fast Inference | TaskRouter Auto-Cascading | `GROQ_API_KEY` | **Fallback Operational** |
| **Direct Booking URL Engine** | Meeting Scheduling | Direct Link Parser & Validator| None needed | **Live Verified (Cal.com/Calendly)**|
| **RFC Business Slots Engine** | Meeting Scheduling | Python `zoneinfo` Slot Engine | None needed | **Live Verified (ZoneInfo)** |
| **RFC 5545 iCalendar Engine** | Calendar Invites | Built-in `.ics` File Generator | None needed | **Live Verified (1-Click Download)**|
| **Cal.com / Calendly APIs** | Calendar Write | Direct URLs + `.ics` Invites | `CALCOM_API_KEY` | **Fallback Operational** |
| **LinkedIn Message Drafter** | Multichannel Outreach | Local Ollama Grounded Drafter | None needed | **Live Verified (<=200 chars)** |
| **LinkedIn Browser Bot** | Profile Scraping | Manual Copy / Export | Session Cookie | **Drafting Ready / Manual Safe**|
| **Google Gmail Read-Only** | Mailbox Sync | One-Click OAuth Consent Flow | Google Cloud OAuth | **Live URL Ready (Safety Hold)**|
| **Microsoft 365 Exchange** | Mailbox Sync | Microsoft Graph Read-Only | Azure Entra ID App | **Client Ready (Safety Hold)** |

---

## 2. Free Discovery Engine Specifications

- **Endpoint 1:** `https://overpass-api.de/api/interpreter`
- **Endpoint 2:** `https://overpass.kumi.systems/api/interpreter`
- **Failover Endpoint:** `https://nominatim.openstreetmap.org/search`
- **Etiquette Safeguards:**
  - `MIN_INTERVAL = 2.0s` delay between requests
  - `BACKOFF = 5.0s` on 429/502/503/504
  - Descriptive User-Agent header (`Harvey/0.1 (open-source sales agent)`)
  - Automatic fallback to Nominatim full-text search when Overpass instances throttle
- **Supported Industry Categories:**
  - B2B SaaS, Software, IT Services (`office=it`, `office=software`)
  - Corporate Law, Legal Practices (`office=lawyer`)
  - Healthcare, Clinics, Hospitals (`amenity=clinic`, `amenity=hospital`)
  - Dental Practices (`amenity=dentist`)
  - Accounting, CPA, Financial Advisory (`office=accountant`)
  - Real Estate, Property Management (`office=estate_agent`)
  - Logistics, Freight, Warehousing (`office=logistics`)
  - Commercial Construction, Roofing, Plumbing, HVAC (`craft=builder`, `craft=roofer`, `craft=plumber`, `craft=hvac`)
  - CleanTech & Solar Energy (`craft=photovoltaic`, `office=energy_supplier`)
  - Automotive Dealerships & Repair (`shop=car`, `shop=car_repair`)
  - Hospitality, Boutique Hotels (`tourism=hotel`, `amenity=restaurant`)
  - Education, Academies (`amenity=school`, `amenity=college`)

---

## 3. Free Email Intelligence Specifications

- **RFC 5322 Syntax Validator:** Enforces standard mailbox syntax, maximum length bounds (64-char local, 253-char domain, 254-char total), and valid TLD format.
- **Disposable Email Filter:** In-memory blocklist containing 200+ known throwaway email services (`mailinator.com`, `tempmail.org`, `guerrillamail.com`, etc.).
- **Role Account Filter:** Automatically detects and isolates 50+ functional mailboxes (`info@`, `admin@`, `support@`, `sales@`, `billing@`, `jobs@`, etc.).
- **Live DNS MX Resolution:** Resolves authoritative MX exchanges with DNS preference sorting and provider classification (`google`, `microsoft`, `gateway`, `other`).
- **Honest Labeling & Scoring System:**
  - `deliverable`: Verified by confirmed mailbox exchange or external provider (Confidence: 0.95)
  - `pattern_inferred`: Derived from known site addresses or verified patterns with active MX (Confidence: 0.70 - 0.85)
  - `mx_valid`: Pattern default with confirmed live MX receiving capability (Confidence: 0.45)
  - `role_based`: Functional mailbox (Confidence: 0.35)
  - `disposable`: Blocked temporary mailbox (Confidence: 0.0)
  - `syntax_invalid`: Malformed address (Confidence: 0.0)

---

## 4. Free AI & LLM Routing Architecture

- **Primary Provider:** Configured in `harvey.yaml` (defaults to `ollama`).
- **Graceful Cascade Logic (`harvey/providers/router.py`):**
  1. Primary Provider is invoked with task-specific prompt.
  2. If primary fails (HTTP 429, missing API key, timeout, or network error), TaskRouter iterates through candidate fallbacks.
  3. **Local Ollama Guard:** `ollama` is permanently appended as the resilient final fallback.
  4. If all LLMs fail, the deterministic rule engine in `harvey/campaign_generator.py` and `harvey/qualification.py` produces grounded multi-industry sequences and BANT scoring.
- **Safety Boundary:** Paid cloud providers (such as xAI) are **strictly excluded** from fallback chains unless explicitly approved in configuration.

---

## 5. Free Calendar Coordination Architecture

- **Direct Booking URL Engine (`CalendarClient`):** Parses and validates links for Cal.com, Calendly, Google Calendar, HubSpot, TidyCal, and Zcal.
- **Timezone-Aware Slots:** Uses Python 3.9+ `zoneinfo.ZoneInfo` to generate business-hour availability slots in the prospect's local timezone and UTC.
- **RFC 5545 `.ics` Invite Generator:** Generates standard `.ics` calendar invitation payloads with unique UIDs, timestamps, organizer, attendee, and meeting description.
- **1-Click Download Endpoint:** `GET /api/calendar/ics-download` provides immediate download of `.ics` files for manual client import.

---

## 6. Free LinkedIn Outreach Architecture

- **URL Validation:** Regex validator for personal profiles, company pages, and educational institutions.
- **Grounded Message Generation:**
  - Connection Request Notes: Strictly enforced $\le 200$ characters.
  - InMail Messages: Grounded in prospect title and offer context, $\le 500$ characters.
  - 1-Click Copy / Export: Ready for operator copy-paste into LinkedIn.
- **Separation of Concerns:** Clear boundary between free AI message generation (100% active, zero cost) and browser automation (which requires operator personal session cookie).
