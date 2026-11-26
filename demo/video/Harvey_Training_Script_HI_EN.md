# Harvey Training Video Script (Hindi-English Mixed / Hinglish)

**Video Title:** Harvey Autonomous Sales Intelligence & Prospecting Engine — Comprehensive Training Guide  
**Target Audience:** New Team Members, QA Engineers, and Sales Intelligence Operators  
**Language:** Natural Hindi-English Mixed (Roman-Script Hinglish)  
**Resolution:** 1280x720 (720p HD)  
**Narration Voice:** hi-IN-MadhurNeural (Warm, professional, educational tone)  
**Demo Server Port:** 5566 (Strict isolated safety test environment)  

---

## Step 0: Welcome & Clean Workspace Architecture
- **UI Screen:** Today Command Deck / Dashboard Overview
- **Visual Action:** Open browser to `http://127.0.0.1:5566/#token=...`. Display clean workspace badge and system readiness.
- **Estimated Duration:** 14 seconds
- **Hinglish Narration:**
  "Namaste aur welcome! Aaj ke is training session mein hum Harvey autonomous prospecting aur research pipeline ko step-by-step explore karenge. Harvey ek intelligent B2B lead discovery aur automated company research platform hai. Hamara dashboard bilkul clean database ke sath port 5566 par safely run ho raha hai, jahan safety invariants aur read-only controls permanently active hain."

---

## Step 1: Safety Controls & Zero Outbox Invariant
- **UI Screen:** Today Command Deck (`#today-view`) / Safety Indicator
- **Visual Action:** Highlight the top-right Safety Status pill: "Read-Only Safety Hold". Hover over Outbox showing 0 sent emails.
- **Estimated Duration:** 15 seconds
- **Hinglish Narration:**
  "Sabse pehle system ki primary safety policy ko samajhna bohot zaroori hai. Harvey mein email sending strictly paused hai. 'channels.email.require_approval' hamesha true rehta hai aur outbox zero emails par locked hai. Iska matlab hai ki research aur discovery ke dauran Harvey kabhi bhi bina explicit manual review ke koi email dispatch ya queue nahi karega."

---

## Step 2: Signals Gate Check & Discovery Guardrail
- **UI Screen:** Signals Tab (`#tab-signals`) & Discovery Search Attempt
- **Visual Action:** Click on Signals tab. Show 29 proposed signals in catalog. Attempt discovery search before confirmation to demonstrate the safety gate error banner: "No signals are confirmed — confirm free signals first".
- **Estimated Duration:** 16 seconds
- **Hinglish Narration:**
  "Harvey ka discovery engine blind scraping allow nahi karta. Jab tak signals confirm nahi hote, discovery system locked rehta hai. Dekhiye, bina signals confirm kiye search karne par Harvey clear warning show karta hai: 'No signals are confirmed'. Ye architectural gate ensure karta hai ki aap hamesha deliberate aur compliant data criteria par hi kaam karein."

---

## Step 3: Confirming Free Zero-Cost Signal Catalog
- **UI Screen:** Signals Tab (`#tab-signals`)
- **Visual Action:** Click "Confirm Free Signals" button. Notice the status change for 26 free signals from 'proposed' to 'confirmed'. The confirmation toast appears.
- **Estimated Duration:** 14 seconds
- **Hinglish Narration:**
  "Ab hum Signals panel mein jaakar sirf free zero-cost signals ko confirm karte hain. Ek single click se sabhi 26 free signals instantly confirm ho jaate hain. Zero credit cost ke sath hamara discovery engine ab targeted searches run karne ke liye unlock ho chuka hai."

---

## Step 4: Discover Search 1 — Logistics Software in Pune
- **UI Screen:** Discover Tab (`#tab-discover`)
- **Visual Action:** Enter Industry: "logistics software", Location: "Pune, Maharashtra, India". Click "Estimate Cost" first ($0.0000 confirmed), then click "Discover Leads". 60 discovery results fetched (top 16 visible company homepages populate the table).
- **Estimated Duration:** 18 seconds
- **Hinglish Narration:**
  "Ab hum pehla real-world discovery search run karenge. Industry field mein enter karte hain 'logistics software' aur location mein 'Pune, Maharashtra, India'. Search run karne se pehle Estimate button par click karte hain — cost bilkul zero dollars hai. Ab search trigger karte hi Pune region ke 60 discovery results fetch hote hain jisme se top 16 visible company homepages grid mein live populate ho jaate hain."

---

## Step 5: Free Homepage Profiler & Deep Classification
- **UI Screen:** Discover Tab (`#tab-discover`) / Lead Details
- **Visual Action:** Click "Run Free Profiler". The system autonomously crawls the homepage of discovered companies, tags product vs service offerings, and validates location evidence.
- **Estimated Duration:** 16 seconds
- **Hinglish Narration:**
  "Leads milne ke baad hum Harvey ka Free Profiler trigger karte hain. Ye collector bina kisi paid API ke discovered companies ke official homepages ko inspect karta hai, unke products aur services extract karta hai, aur unhe structured company profiles mein convert kar deta hai."

---

## Step 6: Discover Search 2 — Accounting Software in Austin
- **UI Screen:** Discover Tab (`#tab-discover`)
- **Visual Action:** Enter Industry: "accounting software", Location: "Austin, Texas, USA". Check estimate ($0.00), run discovery. 30 results fetched (19 qualified company homepages). Total company homepages reach 55.
- **Estimated Duration:** 17 seconds
- **Hinglish Narration:**
  "Next, hum international market search perform karte hain: 'accounting software' in 'Austin, Texas, USA'. Estimate check verify karta hai zero cost. Search click karte hi Austin ke 30 discovery results fetch hote hain jisme 19 qualified company homepages hain. Hamare clean database mein ab total 55 qualified company homepages register ho chuki hain."

---

## Step 7: Companies Directory & Evidence Verification
- **UI Screen:** Companies Tab (`#tab-companies`)
- **Visual Action:** Switch to Companies tab. Filter by product classification. Inspect location evidence badges and source URLs.
- **Estimated Duration:** 15 seconds
- **Hinglish Narration:**
  "Companies directory mein har company ka structured card dekha ja sakta hai. Yahan classification tags clearly distinguish karte hain ki company pure product vendor hai ya IT services provider. Saath hi homepage source URL logged rehta hai, aur address geocoding na hone par location status unverified transparently indicate hota hai."

---

## Step 8: Research Pipeline & Pilot CSV List Upload
- **UI Screen:** Research Tab (`#tab-research`)
- **Visual Action:** Switch to Research tab. Click "Upload Contact List (CSV / Excel)" to expand panel. Select `pilot_real2.csv`. Click "Upload & Preview". Show detected columns and parsed rows.
- **Estimated Duration:** 16 seconds
- **Hinglish Narration:**
  "Ab hum Harvey ke sabse powerful module — Research Pipeline — par aate hain. Expand panel par click karke hum hamari pilot CSV file select karte hain. Upload aur preview karte hi Harvey automatic column mapping perform karta hai: Entity Name, First Name, Phone, aur Email columns automatically detect aur validate ho jaate hain."

---

## Step 9: Production Excel Batch Upload with Domain Cap
- **UI Screen:** Research Tab (`#tab-research`)
- **Visual Action:** Select `FCC_SampleRecords20261106.xlsx`. Set Max Domains Cap to 5. Click "Upload & Preview". The summary displays 250 rows parsed, 11 unique domains, capped to 5.
- **Estimated Duration:** 17 seconds
- **Hinglish Narration:**
  "Ab production testing ke liye hum 250 records wali real FCC sample Excel workbook upload karte hain. Demo economy maintain karne ke liye hum Max Domains Cap ko 5 set karte hain. Harvey file ko binary mode mein parse karke 11 unique domains identify karta hai aur exact safety cap apply karta hai."

---

## Step 10: Sequential Autonomous Domain Research Execution
- **UI Screen:** Research Tab (`#tab-research`) / Progress Banner
- **Visual Action:** Click "Start Research Run". The blue progress bar activates with badge "Running". Observe sequential status updates as domains transition from pending to running to completed.
- **Estimated Duration:** 18 seconds
- **Hinglish Narration:**
  "'Start Research Run' par click karte hi autonomous research worker background mein start ho jaata hai. Harvey har domain par sequential stages execute karta hai: DNS check, website availability, about us and leadership page discovery, executive identification, aur entity consistency verification. UI live polling ke through real-time progress display karti hai."

---

## Step 11: Deep Inspection — Verified Leaders Modal (Cargill & BNSF)
- **UI Screen:** Research Modal (`#research-detail-modal`)
- **Visual Action:** Click "View Details" on `cargill.com`. Show 5 verified executives, titles, contextual excerpts, entity match badge, and masked PII. Close and open `bnsf.com` showing 6 executives.
- **Estimated Duration:** 20 seconds
- **Hinglish Narration:**
  "Result table mein Cargill par 'View Details' click karte hain. Modal mein dekhiye: 5 genuine leadership contacts identify hue hain with titles and context. Notice karein ki PII safety rules ke mutabiq personal names masked hain aur direct public corporate emails preserved hain. Entity check clearly 'match' confirm karta hai. BNSF Railway par bhi 6 executive officers verify hue hain."

---

## Step 12: Honest Unavailable Handling — BearCom Bot Challenge
- **UI Screen:** Research Modal (`#research-detail-modal`)
- **Visual Action:** Click "View Details" on `bearcom.com`. Show status "Unavailable" with honest diagnostic reason: "bot_challenge: HTTP 403 (Cloudflare/Bot challenge)". No hallucinated leadership.
- **Estimated Duration:** 18 seconds
- **Hinglish Narration:**
  "Harvey ki sabse badi strength hai honesty — zero hallucination. BearCom domain par click karke dekhiye. Jahan websites Cloudflare ya aggressive bot challenge return karti hain, Harvey fake data generate karne ke bajaye transparently report karta hai: 'HTTP 403 Bot challenge - Unavailable'. Operator ko accurate status milta hai bina kisi false lead ke."

---

## Step 13: Exporting Intelligence & Operational Summary
- **UI Screen:** Research Tab (`#tab-research`) / Export Actions
- **Visual Action:** Highlight "Export CSV" and "Export HTML" buttons. Open and display the exported CSV contact spreadsheet and the executive research report HTML document.
- **Estimated Duration:** 18 seconds
- **Hinglish Narration:**
  "Final step mein hum research data export karte hain. Ek click par complete contacts CSV spreadsheet download ho jaati hai, jise CRM mein directly import kiya ja sakta hai. Saath hi comprehensive HTML audit report generate hoti hai jisme complete source URLs, findings aur entity evidence documented hote hain. Is tarah Harvey aapka complete prospecting workflow autonomously complete karta hai!"
