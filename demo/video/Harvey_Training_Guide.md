# Harvey Autonomous Prospecting Engine: Complete Training & Operational Guide

**Author:** Harvey Engineering & QA Lead  
**Document Version:** 1.0 (Production Verified)  
**Target Audience:** New Colleagues, QA Auditors, Sales Development Representatives, Account Executives  
**Language:** Roman-Script Hinglish (Hindi-English Mixed)  
**Database Reference:** `data/validation/harvey_video_clean.db` / `data/harvey.db` (Read-Only Protected)  
**Server Environment:** Port 5566 (Safety Demonstration Mode)  

---

## 1. Introduction & Overview (Harvey Kya Hai?)

Harvey ek next-generation autonomous B2B sales intelligence aur targeted prospecting platform hai. Harvey ko build kiya gaya hai taaki sales aur marketing teams ko expensive third-party databases par depend na rehna pade aur har company ke liye genuine, fresh, aur verified data directly web se autonomously extract ho sake.

### Core Capabilities:
1. **Targeted Lead Discovery:** Industry aur target geography ke basis par verified company domains find karna.
2. **Autonomous Signal Catalog:** 29 intent aur qualification signals jinme se 26 completely free zero-cost signals hain.
3. **Free Homepage Profiler:** Discovered companies ke homepages ko inspect karke product vs service classify karna aur location evidence collect karna.
4. **Deep Research Pipeline:** Contact lists (CSV ya Excel workbooks) ko ingest karke DNS verification, website availability, about/leadership discovery, CXO/Director identification, aur entity verification perform karna.
5. **Zero-Hallucination & Honesty Policy:** Agar koi website down hai ya bot challenge return karti hai (e.g. HTTP 403), toh fake data generate karne ke bajaye Harvey honest diagnostic failure log karta hai.
6. **Strict Safety Guardrails:** Outbox permanently locked, require_approval hamesha active, aur zero email sending.

---

## 2. Safety Invariants & Architecture (Suraksha Rules)

Harvey run karne se pehle ye teen golden safety rules har operator ko yaad hone chahiye:

> [!IMPORTANT]
> 1. **Gmail Read-Only Mode:** Harvey kabhi bhi autonomously koi email send, schedule, ya draft nahi karega. `channels.email.require_approval = true` aur `sending_paused = "Pilot Evaluation - Read-Only Safety Hold"` hamesha active rehta hai.
> 2. **Production DB Isolation:** Production database `data/harvey.db` strictly read-only hai (SHA-256 protected). Sabhi demos aur tests `data/validation/harvey_video_clean.db` ya isolated test DBs par run hote hain.
> 3. **PII Masking Invariant:** UI aur exported reports mein personal contact names initial-masked (`J. M.`) hote hain jab tak deliberate unmask toggle na dabaya jaye.

---

## 3. Step-by-Step Operational Workflow (Kaise Chalayein?)

### Step 1: Dashboard Setup & Health Verification
1. Dashboard ko isolated port 5566 par start karein:
   ```bash
   $env:HARVEY_DB_PATH="data/validation/harvey_video_clean.db"
   $env:HARVEY_ALLOW_REAL_DB_WRITE="1"
   .venv\Scripts\python.exe scripts/run_test_dashboard.py
   ```
2. Browser mein open karein: `http://127.0.0.1:5566/#token=<DASHBOARD_TOKEN>`.
3. Check karein ki top header par "Safety Hold" indicator green/amber hai aur outbox counter 0 par hai.

### Step 2: Signals Catalog & Confirmation Gate
- **Kyun zaroori hai:** Harvey blind scraping allow nahi karta. Jab tak operator signals confirm nahi karta, discovery tab search allow nahi karta.
- **Kaise karein:**
  - Signals tab par click karein.
  - "Confirm Free Signals" button click karein.
  - Status "proposed" se "confirmed" ban jaata hai (26 free signals instantly confirm ho jaate hain). Cost: **$0.00**.

### Step 3: Running Cost-Effective Discovery Searches
1. Discover tab par jayein.
2. Target details fill karein:
   - **Industry:** `logistics software`
   - **Location:** `Pune, Maharashtra, India`
3. Hamesha pehle **"Estimate Cost"** button dabayein. Verify karein ki estimated cost `$0.0000` hai.
4. **"Discover Leads"** click karein. Harvey 60 discovery results fetch karega (top 16 visible company homepages table mein live display hoti hain; total 36 company homepages, 17 deduplicated homepages; location status unverified rehta hai jab tak street address geocode na ho).
5. Search ke turant baad **"Run Free Profiler"** click karein. Harvey automatically homepages fetch karke product vs service classify karta hai aur textual metadata store karta hai.
6. Similarly, international search ke liye `accounting software` + `Austin, Texas, USA` run karein (30 results fetch honge, jisme 19 company homepages hain). Total registered company homepages 55 ho jayengi.

### Step 4: Contact List & Excel Batch Research Pipeline
1. Research tab par jayein.
2. "Upload Contact List (CSV / Excel)" panel ko expand karein.
3. Test workflow:
   - Pehle `pilot_real2.csv` upload karke column detection preview dekhein.
   - Uske baad production Excel file `FCC_SampleRecords20261106.xlsx` select karein.
   - **Max Domains Cap** field mein `5` enter karein taaki resource optimization bana rahe.
   - **"Upload & Preview"** click karein. Summary dekhein: 250 rows parsed, 11 unique domains identified, capped to 5.
4. **"Start Research Run"** click karein. Background worker launch ho jayega.

### Step 5: Monitoring Real-Time Research Stages
Research pipeline har domain par automatically ye 6 stages sequentially execute karti hai:
1. `dns_check`: Domain ka apex aur www DNS resolve karna.
2. `website_verification`: Homepage fetch karna, redirect resolve karna, HTTP status verify karna.
3. `overview_collection`: About us, Mission, Vision text extract karna.
4. `people_discovery`: Leadership, Executive, Team pages parse karke decision makers (Founders, CEOs, Directors, CXOs) identify karna.
5. `entity_check`: Uploaded entity name (e.g. "CARGILL FOODS INC") ko domain brand (e.g. "cargill") ke sath fuzzy match karna.
6. `persistence`: Har stage ka raw evidence SQLite database mein securely store karna.

---

## 4. Deep Inspection & Modal Analysis (Results Samajhna)

### Case A: Verified Leadership (e.g., Cargill & BNSF)
- Table row mein **"View Details"** click karne par complete intelligence dossier open hota hai.
- **What you see:**
  - **Overview:** Company ka official summary, offerings, aur core industry tags.
  - **Leadership Table:** Har leader ka Full Name (masked for PII safety, e.g. `J. M.`), Designation / Title (e.g. `President & Chief Executive Officer`), Contextual Excerpt (unka company page par mention), aur Evidence Source URL.
  - **Entity Match Badge:** `Match (brand/parent relationship)`.

### Case B: Honest Unavailable Handling (e.g., BearCom)
- Agar koi company website Cloudflare bot challenge, WAF block, ya HTTP 403 return karti hai, Harvey hallucinated fake names generate nahi karta.
- **Modal Diagnosis:**
  - Status: `Unavailable`
  - Reason: `bot_challenge: HTTP 403 (Cloudflare/Bot challenge)`
  - Decision Makers Found: `0`
- Is honesty ki wajah se aapki sales team kabhi fake ya outdated contacts par time waste nahi karti.

---

## 5. Intelligence Export & CRM Integration (Data Export)

Research batch complete hone ke baad do export formats available hote hain:
1. **Contacts CSV Export (`/api/research/batch/<id>/export?format=csv`):**
   - Clean spreadsheet containing: Domain, Company Name, Contact Name, Title, Email, Phone, Verification Status, Evidence URL.
   - Standard CRM format (Salesforce, HubSpot, Zoho ready).
2. **Comprehensive HTML Executive Report (`/api/research/batch/<id>/export?format=html`):**
   - Self-contained, beautifully styled HTML document with KPI cards, domain breakdown tables, entity match audits, and direct links to all scraped source evidence pages.

---

## 6. Troubleshooting & FAQ for Operators

| Problem / Query | Cause | Solution |
|---|---|---|
| Discovery button shows "No signals confirmed" error | Signals gate is active | Signals tab par jakar "Confirm Free Signals" click karein. |
| Research upload panel button disabled | No file chosen | File input par file select karein; change event button ko enable kar deta hai. |
| Why are names showing as `J. D.` instead of full names? | Privacy & PII protection is active | "Toggle Masking" button click karein agar authorised operational unmasking required ho. |
| Why did BearCom show 0 leaders? | Cloudflare HTTP 403 challenge | Website ne bot scraping block ki; Harvey ne honest unavailable log kiya. Zero hallucination. |
| Is email sending possible from this dashboard? | Safety lock is permanently ON | Dashboard se direct email send intentionally disabled hai. Export CSV karke CRM ke verified outbox se send karein. |

---

## 7. Operational Summary & Verification Checklist

Before presenting your work to leadership or clients, ensure:
- [x] Server running on isolated port 5566 with clean test database.
- [x] Zero emails in outbox (`SELECT COUNT(*) FROM outbox` = 0).
- [x] Free signals catalog confirmed (26 free signals).
- [x] Discovery searches verified with $0.00 estimate.
- [x] Real FCC Excel sample parsed with domain cap enforced.
- [x] Cargill & BNSF leadership verified with source URLs.
- [x] BearCom honest 403 unavailable verified.
- [x] CSV and HTML exports generated and verified.
- [x] Zero PII or API secrets leaked in logs or screenshots.
