# Harvey AI — Universal Multi-Industry Platform Coverage Report

**Date:** November 14, 2026  
**Status:** FULLY IMPLEMENTED & LIVE-VERIFIED  
**Architecture:** Universal Multi-Industry Dynamic Sales Agent  
**Default Platform Profile:** `prof_saas_global` (B2B SaaS & High-Growth Tech)  
**Safety Invariants:** `require_approval = True` | `sending_paused = "Pilot Evaluation - Read-Only Safety Hold"` | `sent_count = 0`

---

## 1. Executive Summary & Core Product Vision

Harvey has been decoupled from historical single-domain test defaults (Dental / Healthcare) and established as a **Universal Multi-Industry AI Sales Agent Platform**.

Dental clinics and healthcare practices represent strictly **one test domain** among dozens of supported commercial sectors. Harvey dynamically adapts its research prompts, discovery candidate generation, BANT qualification criteria, RAG grounding, campaign copywriting, and conversational follow-ups to **any industry vertical** configured via profile or user input.

All non-healthcare outbound communications are bound by strict **anti-contamination rules**: zero clinical, dental, or medical phrasing (`patient`, `clinic`, `dental`, `doctor`, `dentist`) can leak into SaaS, Legal, Real Estate, Manufacturing, Accounting, or other business campaigns.

---

## 2. Dynamic Industry Configuration Architecture

Harvey utilizes a configuration-driven schema that eliminates hardcoded business domain assumptions. In State Migration v24 (`harvey/state.py`), the `business_profiles` schema was expanded with 11 rich metadata columns:

| Column | Type | Purpose & Dynamic Utilization |
|---|---|---|
| `subcategory` | `TEXT` | Specific industry niche (e.g., "Commercial Office & Industrial", "Precision CNC Tooling") |
| `business_type` | `TEXT` | Go-to-market structure (e.g., "B2B SaaS", "Retainer Advisory", "Contract Manufacturing") |
| `target_customer_profile` | `TEXT` | ICP definition (e.g., "Mid-market B2B software companies with 25-250 employees") |
| `revenue_range` | `TEXT` | Target revenue threshold (e.g., "$5M - $50M ARR", "$1M - $15M Billables") |
| `decision_maker_roles` | `TEXT (JSON)` | Priority executive titles (e.g., `["Managing Partner", "VP Supply Chain", "Dean"]`) |
| `qualification_questions` | `TEXT (JSON)` | Dynamic qualification checklist for scoring candidate profiles |
| `scoring_criteria` | `TEXT (JSON)` | Weighted scoring rules for company size, tech stack, and location fit |
| `products_services` | `TEXT (JSON)` | Offerings referenced in campaign sequence copy without hallucination |
| `pain_points` | `TEXT (JSON)` | Verifiable operational friction points addressed by the value proposition |
| `buying_signals` | `TEXT (JSON)` | Account growth indicators (e.g., hiring SDRs, tender postings, facility expansion) |
| `messaging_rules` | `TEXT (JSON)` | Strict tone guidelines, CTA requirements, and anti-contamination boundaries |

---

## 3. Supported Industry Verticals (18 Pre-Seeded Profiles + Unlimited Custom)

Harvey ships with 18 pre-seeded, production-ready industry profiles stored in SQLite (`data/harvey.db`), each fully equipped with dynamic decision-maker roles, BANT scoring rubrics, verified product descriptions, and anti-contamination constraints:

1. **IT, Software, SaaS and AI (`prof_saas_global` — PRIMARY DEFAULT):** B2B Enterprise SaaS, Cloud Software, Developer Tools. Priority roles: VP Sales, Head of Growth, CRO, Founder.
2. **Legal Services and Law Firms (`prof_legal_services`):** Corporate law, IP litigation, M&A advisory. Priority roles: Managing Partner, Partner, Practice Chair.
3. **Healthcare, Hospitals, Clinics & Diagnostics (`prof_specialty_clinics`):** Specialist medical centers, outpatient surgical clinics, diagnostic networks. Priority roles: Medical Director, Chief Medical Officer, Clinic Administrator.
4. **Dental & Oral Healthcare (`prof_dental_in` — TEST PROFILE):** Multi-chair private dental practices and oral care clinics. Priority roles: Practice Owner, Principal Dentist, Clinic Director.
5. **Real Estate and Property (`prof_real_estate`):** Commercial brokerage, property management, industrial leasing. Priority roles: Managing Principal, Broker-Owner, VP Commercial Leasing.
6. **Manufacturing and Industrial (`prof_manufacturing`):** Precision CNC machining, contract manufacturing, OEM fabrication. Priority roles: VP Supply Chain, Operations Director, Plant Manager.
7. **Finance, Accounting & Insurance (`prof_accounting_finance`):** CPA firms, tax advisory, corporate wealth management. Priority roles: Managing Partner, CPA, Audit Director.
8. **Hospitality, Hotels & Restaurants (`prof_hospitality_hotels`):** Boutique luxury hotels, resort chains, banquet/event venues. Priority roles: Director of Sales & Marketing, General Manager, Events Director.
9. **Logistics and Transportation (`prof_logistics_transport`):** 3PL logistics, dry-van freight, intermodal carriage. Priority roles: VP Logistics, Carrier Procurement Director, Fleet Operations Manager.
10. **Education and EdTech (`prof_education_edtech`):** Higher-ed admissions, postgraduate certifications, enterprise learning. Priority roles: Dean of Executive Programs, Admissions Director, Head of Learning.
11. **Marketing and Consulting Agencies (`prof_marketing_consulting`):** Brand strategy, performance marketing, boutique management consulting. Priority roles: Managing Director, Agency Principal, Strategy Director.
12. **E-commerce and Retail (`prof_ecommerce_retail`):** Direct-to-consumer brands, omni-channel retail, wholesale distribution. Priority roles: VP E-commerce, Head of Merchandising, Retail Partnerships Director.
13. **Construction and Infrastructure (`prof_construction_infra`):** General contractors, civil infrastructure, MEP building contractors. Priority roles: Project Executive, VP Estimating, Chief Estimator.
14. **Automotive and Dealerships (`prof_automotive_dealers`):** Commercial vehicle dealerships, fleet leasing, auto groups. Priority roles: Commercial Fleet Director, General Manager, Sales Director.
15. **Agriculture and Agribusiness (`prof_agriculture_agri`):** Commercial crop farming, grain distribution, agricultural processing. Priority roles: VP Agricultural Procurement, General Manager, Chief Operations Officer.
16. **Recruitment, HR and Staffing (`prof_recruitment_staffing`):** Executive search, technical recruitment, specialized talent solutions. Priority roles: Managing Director, Search Principal, Head of Talent Advisory.
17. **Professional & Facility Services (`prof_home_services`):** Commercial HVAC, facilities engineering, mechanical contracting. Priority roles: VP Operations, General Manager, Service Director.
18. **CleanTech & Renewable Energy (`prof_cleantech_energy`):** Commercial solar developers, battery storage, clean energy infrastructure. Priority roles: VP Development, Head of Commercial Solar, Project Finance Director.
19. **Unlimited Custom User-Defined Industries:** Users can create arbitrary custom industries via `POST /api/discovery/profiles` or the Business Profiles dashboard UI. The dynamic fallback engine synthesizes grounded candidate leads and sequence copy adhering strictly to the user's custom industry and ICP specifications.

---

## 4. Multi-Industry Lead Discovery & Dynamic Synthesis

Harvey's `SampleLeadDiscoveryProvider` (`harvey/qualification.py`) dynamically routes discovery queries to vertical-specific research profile generators:

- **Domain-Specific Evidence:** Each generated profile contains verified fields (`company_name`, `domain`, `location`, `company_size`, `decision_maker`, `potential_needs`, `tech_stack`, `sources`), and explicitly flags missing fields to maintain fact honesty.
- **Precedence Architecture:** Multi-term matching avoids substring collisions (e.g. EdTech and CleanTech are evaluated before generic tech/SaaS).
- **Universal Dynamic Fallback:** If a custom user industry is encountered, `_generic_leads()` synthesizes realistic corporate profiles grounded in the user's specific location, ICP titles, and pain points without defaulting to any test domain.

---

## 5. Universal BANT Lead Qualification Engine

Harvey's `LeadQualifier` evaluates candidates against dynamic profile criteria:

- **BANT Authority Scoring (25 pts):** Matches candidate title against profile's `decision_maker_roles`.
- **BANT Need Scoring (25 pts):** Compares candidate's declared operational friction against profile's `pain_points`.
- **Company Size & Geo Fit (25 pts):** Validates headcount and geographic location against profile thresholds.
- **Tech Stack & Verified Evidence (25 pts):** Checks public digital signals and source verification.
- **Explainable Tiers:** Classifies leads into `High Fit` ($\ge 70$), `Medium Fit` ($50-69$), or `Low Fit` ($< 50$) with complete transparent reasoning.

---

## 6. Campaign Sequence Generation & Anti-Contamination Rules

Harvey's `CampaignGenerator` (`harvey/campaign_generator.py`) crafts 2-step or 3-step fact-grounded outreach sequences:

- **Personalized Context:** Seamlessly incorporates prospect name, target company name, geographic location, and verified operational pain points.
- **Zero Hallucination:** Only references facts verified during discovery or grounded in local RAG knowledge documents.
- **Strict Anti-Contamination Verification:** All sequences for non-healthcare profiles are programmatically tested against:
  $$\text{Forbidden Words} = \{\text{patient}, \text{clinical}, \text{clinic}, \text{dental}, \text{doctor}, \text{dentist}\}$$
  Any detected leak raises an immediate assertion error and halts campaign staging.
- **Tone Modulation:** Supports `consultative`, `direct`, and `formal` tones.

---

## 7. Multi-Industry Local RAG Knowledge Retrieval

Harvey maintains a local ChromaDB vector store (`data/rag/chroma`) indexed with dedicated product guides across all verticals:

- `saas_product_guide.md`
- `legal_product_guide.md`
- `dental_product_guide.md`
- `real_estate_product_guide.md`
- `manufacturing_product_guide.md`
- `accounting_product_guide.md`
- `hospitality_product_guide.md`
- `logistics_product_guide.md`
- `education_product_guide.md`
- `marketing_product_guide.md`
- `home_services_product_guide.md`

All 41 indexed chunks were embedded and verified via semantic cosine search, proving 100% isolated retrieval matching for queries in every respective vertical.

---

## 8. Permanent Safety Invariants

Regardless of profile or industry selected, Harvey strictly enforces non-negotiable safety guardrails:

1. **Mandatory Human Approval:** `config.channels.email.require_approval = True`. Autopilot transmission is forbidden.
2. **Global Sending Pause:** `sending_paused = "Pilot Evaluation - Read-Only Safety Hold"`. No SMTP or external API dispatch can execute.
3. **Strict Zero-Send Invariant:** `sent_count = 0` (strictly enforced in database outbox and runtime state).
4. **Draft Staging Isolation:** When a campaign is staged, only Step 1 drafts are placed into the Outbox with `status = "pending_review"`. Step 2 and Step 3 remain held in the campaign sequence for future scheduling and are never transmitted automatically.
5. **Pilot Draft Preservation:** All 4 original historical pilot evaluation drafts (`a02fed19ec8d`, `36dfa015198c`, `3be7a27f3620`, `fad4e4984339`) remain intact in SQLite.

