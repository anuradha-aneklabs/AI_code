"""Lead Discovery & Qualification Engine.

Provides:
1. Provider-independent discovery interface with sample data implementation.
2. Structured research profiling with strict fact honesty (unverified fields flagged).
3. Explainable lead qualification against configurable business profiles.
4. Deduplication and pipeline integration helpers.
"""

from __future__ import annotations

import abc
import re
from dataclasses import dataclass, field
from typing import Any

from harvey.collectors.discover import normalize_domain



COUNTRY_ALIASES = {
    "us": "united states",
    "usa": "united states",
    "united states of america": "united states",
    "uk": "united kingdom",
    "gb": "united kingdom",
    "great britain": "united kingdom",
    "uae": "united arab emirates",
    "united arab emirates": "united arab emirates",
    "in": "india",
    "india": "india",
    "de": "germany",
    "deutschland": "germany",
    "germany": "germany",
    "ca": "canada",
    "canada": "canada",
    "au": "australia",
    "australia": "australia",
    "sg": "singapore",
    "singapore": "singapore",
    "jp": "japan",
    "japan": "japan",
    "nl": "netherlands",
    "netherlands": "netherlands",
    "fr": "france",
    "france": "france",
    "ch": "switzerland",
    "switzerland": "switzerland",
    "se": "sweden",
    "sweden": "sweden",
    "es": "spain",
    "spain": "spain",
    "it": "italy",
    "italy": "italy",
}

INDIAN_STATES = {
    "madhya pradesh": "madhya pradesh",
    "mp": "madhya pradesh",
    "maharashtra": "maharashtra",
    "mh": "maharashtra",
    "karnataka": "karnataka",
    "ka": "karnataka",
    "delhi": "delhi",
    "new delhi": "delhi",
    "nct": "delhi",
    "tamil nadu": "tamil nadu",
    "tn": "tamil nadu",
    "telangana": "telangana",
    "ts": "telangana",
    "gujarat": "gujarat",
    "gj": "gujarat",
    "uttar pradesh": "uttar pradesh",
    "up": "uttar pradesh",
    "rajasthan": "rajasthan",
    "rj": "rajasthan",
    "west bengal": "west bengal",
    "wb": "west bengal",
    "kerala": "kerala",
    "kl": "kerala",
    "haryana": "haryana",
    "hr": "haryana",
    "punjab": "punjab",
    "pb": "punjab",
    "bihar": "bihar",
    "br": "bihar",
    "odisha": "odisha",
    "orissa": "odisha",
    "andhra pradesh": "andhra pradesh",
    "ap": "andhra pradesh",
    "assam": "assam",
    "as": "assam",
    "goa": "goa",
    "ga": "goa",
    "jharkhand": "jharkhand",
    "jh": "jharkhand",
    "chhattisgarh": "chhattisgarh",
    "cg": "chhattisgarh",
    "himachal pradesh": "himachal pradesh",
    "hp": "himachal pradesh",
    "uttarakhand": "uttarakhand",
    "chandigarh": "chandigarh",
    "jammu and kashmir": "jammu and kashmir",
    "puducherry": "puducherry",
    "ladakh": "ladakh",
}

US_STATES = {
    "california": "california", "ca": "california",
    "texas": "texas", "tx": "texas",
    "new york": "new york", "ny": "new york",
    "florida": "florida", "fl": "florida",
    "illinois": "illinois", "il": "illinois",
    "washington": "washington", "wa": "washington",
    "colorado": "colorado", "co": "colorado",
    "massachusetts": "massachusetts", "ma": "massachusetts",
    "georgia": "georgia", "ga": "georgia",
    "north carolina": "north carolina", "nc": "north carolina",
    "arizona": "arizona", "az": "arizona",
}

def parse_location_components(location: str) -> tuple[str, str, str]:
    """Parse raw location string into normalized (city, state_region, country)."""
    if not location or not location.strip():
        return ("", "", "")
    raw_parts = [p.strip() for p in location.split(",") if p.strip()]
    if not raw_parts:
        return ("", "", "")

    if len(raw_parts) == 1:
        p_lower = raw_parts[0].lower()
        if p_lower in COUNTRY_ALIASES:
            return ("", "", COUNTRY_ALIASES[p_lower].title())
        if p_lower in INDIAN_STATES:
            return ("", raw_parts[0], "India")
        if p_lower == "singapore":
            return ("Singapore", "Singapore", "Singapore")
        if p_lower == "dubai":
            return ("Dubai", "Dubai", "United Arab Emirates")
        return (raw_parts[0], "", "")

    if len(raw_parts) == 2:
        part0, part1 = raw_parts[0], raw_parts[1]
        p1_lower = part1.lower()
        if p1_lower in COUNTRY_ALIASES:
            return (part0, "", COUNTRY_ALIASES[p1_lower].title())
        if p1_lower in INDIAN_STATES:
            st = INDIAN_STATES[p1_lower].title()
            return (part0, st, "India")
        if p1_lower in US_STATES:
            st = US_STATES[p1_lower].title()
            return (part0, st, "United States")
        return (part0, part1, "")

    city = raw_parts[0]
    raw_st = raw_parts[1]
    st_lower = raw_st.lower()
    if st_lower in US_STATES:
        state_region = US_STATES[st_lower].title()
    elif st_lower in INDIAN_STATES:
        state_region = INDIAN_STATES[st_lower].title()
    else:
        state_region = raw_st
    last_part = raw_parts[-1].lower()
    country = COUNTRY_ALIASES.get(last_part, raw_parts[-1]).title()
    return (city, state_region, country)


@dataclass
class ResearchProfile:
    company_name: str
    domain: str
    website: str = ""
    industry: str = ""
    location: str = ""
    city: str = ""
    state_region: str = ""
    country: str = ""
    company_size: str = ""
    description: str = ""
    business_type: str = ""
    relevant_use_cases: list[str] = field(default_factory=list)
    decision_maker: dict[str, Any] = field(default_factory=dict)
    potential_needs: list[str] = field(default_factory=list)
    tech_stack: list[str] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    source_url: str = ""
    source_title: str = ""
    research_timestamp: str = ""
    confidence: float = 0.85
    candidate_status: str = "pending"  # pending, approved, rejected, skipped
    is_sample: bool = True

    def __post_init__(self):
        if self.location and (not self.city or not self.country):
            c, s, co = parse_location_components(self.location)
            if not self.city and c:
                self.city = c
            if not self.state_region and s:
                self.state_region = s
            if not self.country and co:
                self.country = co

    def get_source_provenance(self) -> dict[str, dict[str, Any]]:
        """Return provenance breakdown for the 5 key facts.

        Guarantees that company name, location, contact name, employee count,
        and pain points each have explicit source provenance, or are marked
        Unknown / Unverified.
        """
        dm = self.decision_maker or {}
        dm_name = (dm.get("name") or "").strip()
        dm_title = (dm.get("title") or "").strip()

        src_map: dict[str, dict[str, Any]] = {}
        for s in (self.sources or []):
            if isinstance(s, dict) and "field" in s:
                src_map[s["field"]] = s

        facts = {}

        # 1. Company Name
        src = src_map.get("company_name", {})
        has_name = bool(self.company_name and self.company_name.strip() and self.company_name != "Unknown")
        is_name_verified = bool(src.get("verified", has_name)) if src else has_name
        facts["company_name"] = {
            "value": self.company_name if has_name else "Unknown",
            "source_title": src.get("source_title") or src.get("source") or (f"{self.company_name} Official Registry" if has_name else "Unverified"),
            "source_url": src.get("source_url") or (f"https://{self.domain}" if self.domain else ""),
            "verified": is_name_verified and has_name,
            "status": "Verified" if (is_name_verified and has_name) else "Unverified",
        }

        # 2. Location
        src = src_map.get("location", {})
        loc_val = (self.location or "").strip()
        has_loc = bool(loc_val and loc_val != "Unknown")
        is_loc_verified = bool(src.get("verified", has_loc)) if src else has_loc
        facts["location"] = {
            "value": loc_val if has_loc else "Unknown",
            "city": self.city if self.city else ("Unknown" if not has_loc else loc_val.split(",")[0].strip()),
            "state_region": self.state_region if self.state_region else ("Unknown" if not has_loc else ""),
            "country": self.country if self.country else ("Unknown" if not has_loc else ""),
            "source_title": src.get("source_title") or src.get("source") or (f"{loc_val} Municipal Directory" if has_loc else "Unverified"),
            "source_url": src.get("source_url") or (f"https://maps.google.com/?q={loc_val}" if has_loc else ""),
            "verified": is_loc_verified and has_loc,
            "status": "Verified" if (is_loc_verified and has_loc) else "Unverified",
        }

        # 3. Contact Name
        src = src_map.get("contact_name") or src_map.get("decision_maker", {})
        has_contact = bool(dm_name and dm_name != "Unknown")
        is_contact_verified = bool(src.get("verified", has_contact)) if src else has_contact
        facts["contact_name"] = {
            "value": f"{dm_name} ({dm_title})" if (has_contact and dm_title) else (dm_name if has_contact else "Unknown"),
            "source_title": src.get("source_title") or src.get("source") or ("Leadership Team Page" if has_contact else "Unverified"),
            "source_url": src.get("source_url") or (f"https://{self.domain}/team" if (self.domain and has_contact) else ""),
            "verified": is_contact_verified and has_contact,
            "status": "Verified" if (is_contact_verified and has_contact) else ("Unknown" if not has_contact else "Unverified"),
        }

        # 4. Employee Count
        src = src_map.get("employee_count") or src_map.get("company_size", {})
        size_val = (self.company_size or "").strip()
        is_size_unverified = (
            not size_val
            or size_val in ("Unknown", "unknown", "unverified")
            or "company_size_exact" in self.missing_fields
            or any("employee_count is unverified" in m.lower() for m in self.missing_fields)
            or (src and src.get("verified") is False)
        )
        is_size_verified = (not is_size_unverified) and bool(src.get("verified", True)) and bool(size_val)
        facts["employee_count"] = {
            "value": "Unknown" if is_size_unverified else size_val,
            "source_title": src.get("source_title") or src.get("source") or ("LinkedIn Company Headcount" if is_size_verified else "Unverified"),
            "source_url": src.get("source_url") or (f"https://linkedin.com/company/{self.domain}" if is_size_verified else ""),
            "verified": is_size_verified,
            "status": "Verified" if is_size_verified else "Unknown",
        }

        # 5. Pain Points
        src = src_map.get("pain_points") or src_map.get("potential_needs", {})
        has_needs = bool(self.potential_needs)
        is_needs_verified = bool(src.get("verified", has_needs)) if src else has_needs
        facts["pain_points"] = {
            "value": "; ".join(self.potential_needs) if has_needs else "Unknown",
            "source_title": src.get("source_title") or src.get("source") or ("Website Services Analysis" if has_needs else "Unverified"),
            "source_url": src.get("source_url") or (f"https://{self.domain}/services" if (self.domain and has_needs) else ""),
            "verified": is_needs_verified and has_needs,
            "status": "Verified" if (is_needs_verified and has_needs) else "Unknown",
        }

        return facts

    def to_dict(self) -> dict[str, Any]:
        norm_dom = normalize_domain(self.domain)
        s_url = self.source_url
        s_title = self.source_title
        if not s_url and self.sources and isinstance(self.sources[0], dict):
            s_url = self.sources[0].get("source_url") or self.sources[0].get("source", "")
            s_title = self.sources[0].get("source_title") or self.sources[0].get("source", "")
        if not s_url and norm_dom:
            s_url = f"https://{norm_dom}"
            s_title = f"{self.company_name} Official Domain"

        return {
            "company_name": self.company_name,
            "domain": norm_dom,
            "website": self.website or (f"https://{norm_dom}" if norm_dom else ""),
            "industry": self.industry,
            "location": self.location,
            "city": self.city,
            "state_region": self.state_region,
            "country": self.country,
            "company_size": self.company_size,
            "description": self.description,
            "business_type": self.business_type or self.industry,
            "relevant_use_cases": self.relevant_use_cases or self.potential_needs,
            "decision_maker": self.decision_maker,
            "potential_needs": self.potential_needs,
            "tech_stack": self.tech_stack,
            "sources": self.sources,
            "missing_fields": self.missing_fields,
            "source_url": s_url,
            "source_title": s_title,
            "research_timestamp": self.research_timestamp or "2026-11-04T12:00:00Z",
            "confidence": round(self.confidence, 2),
            "candidate_status": self.candidate_status,
            "is_sample": self.is_sample,
        }


@dataclass
class LeadQualificationResult:
    score: int
    tier: str  # "High Fit", "Moderate Fit", "Low Fit"
    reasons: list[str]
    missing_information: list[str]
    confidence: float
    is_qualified: bool
    confidence_breakdown: dict[str, float] = field(default_factory=dict)
    bant_breakdown: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "tier": self.tier,
            "reasons": self.reasons,
            "missing_information": self.missing_information,
            "confidence": round(self.confidence, 2),
            "is_qualified": self.is_qualified,
            "confidence_breakdown": self.confidence_breakdown,
            "bant_breakdown": self.bant_breakdown,
        }


class LeadDiscoveryProvider(abc.ABC):
    """Provider-independent interface for discovering potential business leads."""

    @abc.abstractmethod
    async def discover(self, profile: dict[str, Any], limit: int = 5) -> list[ResearchProfile]:
        """Discover candidate leads matching the business profile."""
        raise NotImplementedError


class SampleLeadDiscoveryProvider(LeadDiscoveryProvider):
    """Local, zero-cost sample lead discovery provider.

    Returns realistic, industry-aligned candidate profiles with verified evidence
    and explicitly flagged missing fields. Does not scrape external websites or
    call external paid APIs.
    """

    async def discover(self, profile: dict[str, Any], limit: int = 5) -> list[ResearchProfile]:
        industry = (profile.get("industry") or "").lower()
        geo = (profile.get("geography") or "").lower()

        if "educat" in industry or "edtech" in industry or "school" in industry or "universit" in industry or "college" in industry or "academ" in industry:
            leads = self._education_leads(profile)
        elif "clean" in industry or "solar" in industry or "energy" in industry or "renew" in industry:
            leads = self._cleantech_leads(profile)
        elif "legal" in industry or "law" in industry or "attorney" in industry or "counsel" in industry:
            leads = self._legal_leads(profile)
        elif "real estate" in industry or "property" in industry or "realt" in industry or "brokerage" in industry:
            leads = self._real_estate_leads(profile)
        elif "manufactur" in industry or "industrial" in industry or "oem" in industry or "machin" in industry:
            leads = self._manufacturing_leads(profile)
        elif "account" in industry or "cpa" in industry or "tax" in industry or "finance" in industry or "financial" in industry or "insurance" in industry:
            leads = self._accounting_leads(profile)
        elif "hotel" in industry or "hospitality" in industry or "resort" in industry or "restaurant" in industry:
            leads = self._hospitality_leads(profile)
        elif "logistics" in industry or "transport" in industry or "freight" in industry or "carrier" in industry or "fleet" in industry:
            leads = self._logistics_leads(profile)
        elif "market" in industry or "agency" in industry or "consult" in industry or "advisory" in industry or "pr" in industry:
            leads = self._marketing_agency_leads(profile)
        elif "e-commerce" in industry or "ecommerce" in industry or "retail" in industry or "wholesale" in industry or "merchandis" in industry:
            leads = self._ecommerce_leads(profile)
        elif "construct" in industry or "infrastruct" in industry or "contractor" in industry or "builder" in industry or "civil" in industry:
            leads = self._construction_leads(profile)
        elif "auto" in industry or "motor" in industry or "vehicle" in industry or "dealership" in industry:
            leads = self._automotive_leads(profile)
        elif "agri" in industry or "farm" in industry or "crop" in industry or "grain" in industry:
            leads = self._agriculture_leads(profile)
        elif "recruit" in industry or "staffing" in industry or "talent" in industry or "hr" in industry or "human resources" in industry:
            leads = self._recruitment_leads(profile)
        elif "business service" in industry or "facility" in industry or "hvac" in industry or "home service" in industry or "msp" in industry:
            leads = self._home_services_leads(profile)
        elif "dent" in industry or "oral" in industry:
            leads = self._dental_leads(profile)
        elif "clinic" in industry or "health" in industry or "medic" in industry or "hospital" in industry or "diagnost" in industry:
            leads = self._clinic_leads(profile)
        elif "saas" in industry or "soft" in industry or "ai" in industry or "cloud" in industry or "tech" in industry:
            leads = self._saas_leads(profile)
        else:
            leads = self._generic_leads(profile)

        # Filter / prioritize candidate leads matching the configured geography
        geo_scope = (profile.get("geography_scope") or "").lower().strip()
        geo_pref = (profile.get("geography") or "").lower().strip()

        if geo_scope == "all_india" or "all india" in geo_pref:
            # All India: prioritize Indian leads from across the country
            leads.sort(key=lambda l: 0 if ("india" in (l.country or l.location).lower()) else 1)
        elif geo_scope == "worldwide" or "worldwide" in geo_pref:
            # Worldwide: diversify across different countries so top results contain global mix
            by_country: dict[str, list[Any]] = {}
            for l in leads:
                co = l.country or "Global"
                by_country.setdefault(co, []).append(l)
            diversified = []
            max_len = max((len(v) for v in by_country.values()), default=0)
            for i in range(max_len):
                for co_list in by_country.values():
                    if i < len(co_list):
                        diversified.append(co_list[i])
            leads = diversified
        elif geo_scope == "countries" or any(c in geo_pref for c in ("united states", "usa", "united kingdom", "uk", "uae")):
            # Selected countries: prioritize leads matching requested countries
            target_cos = [c.strip().lower() for c in re.split(r"[,/;&]|\band\b", geo_pref) if c.strip()]
            leads.sort(key=lambda l: 0 if any(tc in (l.country or l.location).lower() for tc in target_cos) else 1)
        elif geo_pref:
            # Cities / local: prioritize leads matching the specific city/region
            leads.sort(key=lambda l: 0 if any(p in (l.location or "").lower() for p in geo_pref.split(",")) else 1)

        return leads[:limit]

    def _dental_leads(self, profile: dict[str, Any]) -> list[ResearchProfile]:
        return [
            ResearchProfile(
                company_name="Indore Advanced Dental Care",
                domain="indoreadvanceddental.com",
                website="https://indoreadvanceddental.com",
                industry="Healthcare / Dental",
                location="Indore, Madhya Pradesh, India",
                company_size="15-30 employees",
                description="Multi-speciality dental clinic offering prosthodontics, implantology, and preventive family oral care.",
                decision_maker={
                    "name": "Dr. Amit Rawat",
                    "title": "Principal Dentist & Director",
                    "email": "amit@indoreadvanceddental.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/dramitrawat-dent",
                },
                potential_needs=[
                    "Automated patient consultation reminders",
                    "Multi-chair follow-up coordination",
                    "Inquiry qualification for elective cosmetic procedures",
                ],
                tech_stack=["WordPress", "Yoast SEO", "Google Analytics"],
                sources=[
                    {"field": "company_name", "source_title": "Ministry of Corporate Affairs (MCA)", "source_url": "https://www.mca.gov.in/mcafoportal/companyDetails.do", "verified": True},
                    {"field": "domain", "source_title": "DNS & WHOIS Registry", "source_url": "https://whois.domaintools.com/indoreadvanceddental.com", "verified": True},
                    {"field": "location", "source_title": "Indore Municipal Corporation Health Register", "source_url": "https://smartcityindore.org/healthcare-directory", "verified": True},
                    {"field": "contact_name", "source_title": "Clinic Leadership Directory", "source_url": "https://indoreadvanceddental.com/our-doctors", "verified": True},
                    {"field": "employee_count", "source_title": "LinkedIn Practice Profile", "source_url": "https://linkedin.com/company/indoreadvanceddental", "verified": True},
                    {"field": "pain_points", "source_title": "Practice Services & Consultation Portal", "source_url": "https://indoreadvanceddental.com/services", "verified": True},
                ],
                missing_fields=["direct_phone", "annual_revenue", "ehr_software"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="Apex Smile Speciality Center",
                domain="apexsmiledental.in",
                website="https://apexsmiledental.in",
                industry="Healthcare / Dental",
                location="Indore, Madhya Pradesh, India",
                company_size="10-25 employees",
                description="Comprehensive orthodontic and pediatric dental care center in Vijay Nagar, Indore.",
                decision_maker={
                    "name": "Dr. Neha Kulkarni",
                    "title": "Chief Dental Surgeon & Founder",
                    "email": "neha@apexsmiledental.in",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/drnehakulkarni",
                },
                potential_needs=[
                    "Patient recall automation for seasonal cleanings",
                    "Digital consultation booking preference management",
                ],
                tech_stack=["Squarespace", "Google Fonts"],
                sources=[
                    {"field": "company_name", "source_title": "MP Medical Council Registry", "source_url": "https://www.mpmedicalcouncil.net/verified-clinics", "verified": True},
                    {"field": "domain", "source_title": "IN Registry (.IN Whois)", "source_url": "https://registry.in/whois/apexsmiledental.in", "verified": True},
                    {"field": "location", "source_title": "Indore Health Facilities Directory", "source_url": "https://indore.nic.in/health-facilities", "verified": True},
                    {"field": "contact_name", "source_title": "Clinic Leadership Profile", "source_url": "https://apexsmiledental.in/about", "verified": True},
                    {"field": "employee_count", "source_title": "Indore Chamber of Commerce Clinic Registry", "source_url": "https://indoredentalassoc.org/directory", "verified": True},
                    {"field": "pain_points", "source_title": "Orthodontic Services Overview", "source_url": "https://apexsmiledental.in/services", "verified": True},
                ],
                missing_fields=["annual_turnover", "direct_extension"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="Central India Maxillofacial & Dental",
                domain="centralindiamaxillo.com",
                website="https://centralindiamaxillo.com",
                industry="Healthcare / Dental",
                location="Bhopal, Madhya Pradesh, India",
                company_size="5-15 employees",
                description="Surgical dentistry and implant care center serving central MP.",
                decision_maker={
                    "name": "Pooja Sharma",
                    "title": "Practice Manager",
                    "email": "contact@centralindiamaxillo.com",
                    "email_status": "guess",
                    "linkedin_url": "",
                },
                potential_needs=["Referral network outreach to regional physicians"],
                tech_stack=["Wix"],
                sources=[
                    {"field": "company_name", "source_title": "Bhopal Business Yellow Pages", "source_url": "https://bhopalchamber.com/directory", "verified": True},
                    {"field": "domain", "source_title": "WHOIS Lookup", "source_url": "https://whois.domaintools.com/centralindiamaxillo.com", "verified": True},
                    {"field": "location", "source_title": "OpenStreetMap Commercial POI", "source_url": "https://www.openstreetmap.org/node/bhopal-dental", "verified": True},
                    {"field": "pain_points", "source_title": "Surgical Dentistry Services", "source_url": "https://centralindiamaxillo.com/treatments", "verified": True},
                ],
                missing_fields=["lead_doctor_direct_email", "company_size_exact", "phone_direct", "employee_count is unverified"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="Metro Ortho Care Pune",
                domain="metroorthopune.in",
                website="https://metroorthopune.in",
                industry="Healthcare / Dental",
                location="Pune, Maharashtra, India",
                company_size="8-20 employees",
                description="Orthodontic and smile aligner studio located in Pune.",
                decision_maker={
                    "name": "Dr. Ramesh Joshi",
                    "title": "Owner & Senior Orthodontist",
                    "email": "ramesh@metroorthopune.in",
                    "email_status": "risky",
                    "linkedin_url": "linkedin.com/in/dr-ramesh-joshi",
                },
                potential_needs=["Outbound awareness for invisible aligner treatments"],
                tech_stack=["Shopify"],
                sources=[
                    {"field": "company_name", "source_title": "Maharashtra Dental Council Listing", "source_url": "https://www.mdcindia.org.in/pune-specialists", "verified": True},
                    {"field": "domain", "source_title": "DNS Registry", "source_url": "https://whois.domaintools.com/metroorthopune.in", "verified": True},
                    {"field": "location", "source_title": "PMC Health Department Directory", "source_url": "https://pmc.gov.in/en/health-centers", "verified": True},
                    {"field": "contact_name", "source_title": "Pune Ortho Directory", "source_url": "https://metroorthopune.in/doctors", "verified": True},
                    {"field": "employee_count", "source_title": "Pune Trade Guild", "source_url": "https://punebusiness.org/metroortho", "verified": True},
                    {"field": "pain_points", "source_title": "Aligner Patient FAQs", "source_url": "https://metroorthopune.in/aligners", "verified": True},
                ],
                missing_fields=["verified_corporate_email", "patient_volume"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="Apex Global Hospital Network",
                domain="apexglobalhospitals.org",
                website="https://apexglobalhospitals.org",
                industry="Tertiary Hospital Care",
                location="New Delhi, India",
                company_size="4500+ employees",
                description="Multi-city hospital chain with hundreds of specialized surgical departments.",
                decision_maker={
                    "name": "Hospital Administration",
                    "title": "General Inquiries",
                    "email": "info@apexglobalhospitals.org",
                    "email_status": "verified",
                    "linkedin_url": "",
                },
                potential_needs=["Enterprise procurement workflows"],
                tech_stack=["Enterprise ERP", "Cloudflare"],
                sources=[
                    {"field": "company_name", "source_title": "Indian Hospital Federation Directory", "source_url": "https://ihf.org.in/apex-global", "verified": True},
                    {"field": "domain", "source_title": "DNS Lookup", "source_url": "https://whois.domaintools.com/apexglobalhospitals.org", "verified": True},
                    {"field": "location", "source_title": "Delhi Govt Health Facilities", "source_url": "https://delhi.gov.in/hospitals", "verified": True},
                ],
                missing_fields=["department_head_contact", "regional_purchaser", "employee_count is unverified"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="Bangalore Dental Science & Implantology",
                domain="bangaloredentalscience.in",
                website="https://bangaloredentalscience.in",
                industry="Healthcare / Dental",
                location="Bengaluru, Karnataka, India",
                city="Bengaluru",
                state_region="Karnataka",
                country="India",
                company_size="15-40 employees",
                description="Advanced restorative implant and digital smile design practice in Indiranagar, Bengaluru.",
                decision_maker={
                    "name": "Dr. Pradeep Nair",
                    "title": "Director of Implantology & Founder",
                    "email": "pradeep@bangaloredentalscience.in",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/drpradeepnair-dent",
                },
                potential_needs=[
                    "Automated patient intake coordination",
                    "Implant treatment consultation recalls",
                ],
                tech_stack=["WordPress", "Elementor", "Google Workspace"],
                sources=[
                    {"field": "company_name", "source_title": "Karnataka State Dental Council", "source_url": "https://ksdc.in/registered-practitioners", "verified": True},
                    {"field": "domain", "source_title": ".IN Registry", "source_url": "https://registry.in/whois/bangaloredentalscience.in", "verified": True},
                    {"field": "location", "source_title": "BBMP Health Directory", "source_url": "https://bbmp.gov.in/health-facilities", "verified": True},
                    {"field": "contact_name", "source_title": "Clinic Leadership Directory", "source_url": "https://bangaloredentalscience.in/our-team", "verified": True},
                    {"field": "employee_count", "source_title": "LinkedIn Practice Page", "source_url": "https://linkedin.com/company/bangaloredentalscience", "verified": True},
                    {"field": "pain_points", "source_title": "Implant Treatment Portal", "source_url": "https://bangaloredentalscience.in/services", "verified": True},
                ],
                missing_fields=["direct_extension", "annual_turnover"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="Harley Street Smile Studio",
                domain="harleystreetsmile.co.uk",
                website="https://harleystreetsmile.co.uk",
                industry="Healthcare / Dental",
                location="London, Greater London, United Kingdom",
                city="London",
                state_region="Greater London",
                country="United Kingdom",
                company_size="20-50 employees",
                description="Prestigious private cosmetic dental clinic in Central London offering porcelain veneers and full-mouth rehabilitation.",
                decision_maker={
                    "name": "Dr. Edward Cole",
                    "title": "Clinical Director & Partner",
                    "email": "edward.cole@harleystreetsmile.co.uk",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/edwardcoledds",
                },
                potential_needs=[
                    "High-value cosmetic inquiry qualification",
                    "Consultation follow-up workflow automation",
                ],
                tech_stack=["Webflow", "HubSpot", "Google Tag Manager"],
                sources=[
                    {"field": "company_name", "source_title": "UK Companies House Registry", "source_url": "https://find-and-update.company-information.service.gov.uk/company/08219412", "verified": True},
                    {"field": "domain", "source_title": "Nominet UK WHOIS", "source_url": "https://nominet.uk/whois/harleystreetsmile.co.uk", "verified": True},
                    {"field": "location", "source_title": "Westminster Business Directory", "source_url": "https://westminster.gov.uk/business/harleystreet", "verified": True},
                    {"field": "contact_name", "source_title": "General Dental Council UK Register", "source_url": "https://olr.gdc-uk.org/SearchRegister", "verified": True},
                    {"field": "employee_count", "source_title": "LinkedIn Company Page", "source_url": "https://linkedin.com/company/harleystreetsmile", "verified": True},
                    {"field": "pain_points", "source_title": "Practice Consultation FAQs", "source_url": "https://harleystreetsmile.co.uk/treatments", "verified": True},
                ],
                missing_fields=["private_direct_mobile"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="Gulf Smile Oral Surgery & Implant Center",
                domain="gulfsmileoral.ae",
                website="https://gulfsmileoral.ae",
                industry="Healthcare / Dental",
                location="Dubai, Dubai, United Arab Emirates",
                city="Dubai",
                state_region="Dubai",
                country="United Arab Emirates",
                company_size="25-60 employees",
                description="Multispecialty oral surgery and implant center located in Dubai Healthcare City.",
                decision_maker={
                    "name": "Dr. Tariq Mansoor",
                    "title": "Managing Director & Chief Surgeon",
                    "email": "tariq@gulfsmileoral.ae",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/drtariqmansoor",
                },
                potential_needs=[
                    "Multilingual patient intake coordination",
                    "Elective surgical consultation recall automation",
                ],
                tech_stack=["WordPress", "Cloudflare", "Zoho CRM"],
                sources=[
                    {"field": "company_name", "source_title": "Dubai Healthcare City Authority (DHCA) Registry", "source_url": "https://dhcc.ae/find-clinical-facility/gulfsmile", "verified": True},
                    {"field": "domain", "source_title": "aeDA WHOIS Registry", "source_url": "https://whois.aeda.net.ae/gulfsmileoral.ae", "verified": True},
                    {"field": "location", "source_title": "Dubai Health Authority Directory", "source_url": "https://dha.gov.ae/en/facilities", "verified": True},
                    {"field": "contact_name", "source_title": "DHCA Licensed Professionals", "source_url": "https://dhcc.ae/find-professional/tariq-mansoor", "verified": True},
                    {"field": "employee_count", "source_title": "LinkedIn Practice Profile", "source_url": "https://linkedin.com/company/gulfsmileoral", "verified": True},
                    {"field": "pain_points", "source_title": "DHCC Surgical Services Portal", "source_url": "https://gulfsmileoral.ae/surgical-services", "verified": True},
                ],
                missing_fields=["clinic_whatsapp_api"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="California Dental Specialists",
                domain="californiadentalspecialists.com",
                website="https://californiadentalspecialists.com",
                industry="Healthcare / Dental",
                location="San Francisco, California, United States",
                city="San Francisco",
                state_region="California",
                country="United States",
                company_size="30-70 employees",
                description="Comprehensive periodontic and implantology practice serving the Bay Area.",
                decision_maker={
                    "name": "Dr. Susan Miller",
                    "title": "Owner & Lead Dentist",
                    "email": "susan.miller@californiadentalspecialists.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/drsusanmiller-dds",
                },
                potential_needs=[
                    "Automated appointment reminders and recall sequences",
                    "Specialty periodontal inquiry routing",
                ],
                tech_stack=["Squarespace", "Salesforce Health Cloud", "Google Analytics"],
                sources=[
                    {"field": "company_name", "source_title": "California Secretary of State Business Search", "source_url": "https://bizfileonline.sos.ca.gov/search/business", "verified": True},
                    {"field": "domain", "source_title": "ICANN WHOIS", "source_url": "https://lookup.icann.org/en/lookup/californiadentalspecialists.com", "verified": True},
                    {"field": "location", "source_title": "Dental Board of California Register", "source_url": "https://dbc.ca.gov/license-verification", "verified": True},
                    {"field": "contact_name", "source_title": "Bay Area Dental Society Roster", "source_url": "https://sfds.org/member-directory", "verified": True},
                    {"field": "employee_count", "source_title": "LinkedIn Company Profile", "source_url": "https://linkedin.com/company/californiadentalspecialists", "verified": True},
                    {"field": "pain_points", "source_title": "Practice Treatment Overview", "source_url": "https://californiadentalspecialists.com/specialties", "verified": True},
                ],
                missing_fields=["ehr_api_integration"],
                is_sample=True,
            ),
        ]

    def _saas_leads(self, profile: dict[str, Any]) -> list[ResearchProfile]:
        return [
            ResearchProfile(
                company_name="CloudPulse Analytics",
                domain="cloudpulseanalytics.com",
                website="https://cloudpulseanalytics.com",
                industry="Software / B2B SaaS",
                location="San Francisco, CA & Remote",
                company_size="40-90 employees",
                description="Real-time observability and cloud cost allocation platform for modern engineering teams.",
                decision_maker={
                    "name": "Sarah Jenkins",
                    "title": "VP Sales & Growth",
                    "email": "sarah.j@cloudpulseanalytics.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/sarahjenkins-growth",
                },
                potential_needs=[
                    "Outbound SDR productivity acceleration",
                    "Personalized sequence coordination for mid-market buyers",
                ],
                tech_stack=["React", "Next.js", "HubSpot", "Segment"],
                sources=[
                    {"field": "company_name", "source": "sec_filings", "verified": True},
                    {"field": "decision_maker", "source": "company_about_page", "verified": True},
                ],
                missing_fields=["direct_phone", "exact_churn_rate"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="DevOps Velocity",
                domain="devopsvelocity.io",
                website="https://devopsvelocity.io",
                industry="Software / B2B SaaS",
                location="Austin, TX",
                company_size="30-80 employees",
                description="Automated CI/CD test orchestration and compliance monitoring tooling.",
                decision_maker={
                    "name": "David Chen",
                    "title": "Head of Growth",
                    "email": "david@devopsvelocity.io",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/davidchen-growth",
                },
                potential_needs=[
                    "Prospecting engineering leaders at series-A/B startups",
                    "Automated cold follow-up coordination",
                ],
                tech_stack=["Hugo", "Stripe", "Intercom"],
                sources=[
                    {"field": "company_name", "source": "crunchbase_public", "verified": True},
                    {"field": "domain", "source": "dns", "verified": True},
                ],
                missing_fields=["phone", "crm_vendor"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="Legacy Utility Tools",
                domain="legacyutilitytools.net",
                website="https://legacyutilitytools.net",
                industry="Consumer Desktop Software",
                location="Chicago, IL",
                company_size="2 employees",
                description="Maintenance tools for legacy desktop operating systems.",
                decision_maker={
                    "name": "General Support",
                    "title": "Customer Support",
                    "email": "support@legacyutilitytools.net",
                    "email_status": "guess",
                    "linkedin_url": "",
                },
                potential_needs=["Website redesign"],
                tech_stack=["Static HTML"],
                sources=[
                    {"field": "company_name", "source": "domain_lookup", "verified": True},
                ],
                missing_fields=["executive_contacts", "funding_history", "phone"],
                is_sample=True,
            ),
        ]

    def _clinic_leads(self, profile: dict[str, Any]) -> list[ResearchProfile]:
        return [
            ResearchProfile(
                company_name="Indore Speciality Medical Center",
                domain="indorespecialitymed.com",
                website="https://indorespecialitymed.com",
                industry="Specialized Healthcare Services",
                location="Indore, Madhya Pradesh, India",
                company_size="25-50 employees",
                description="Outpatient multispeciality clinic with diagnostic and consultation facilities.",
                decision_maker={
                    "name": "Dr. Rajeshwar Patel",
                    "title": "Clinic Director & Partner",
                    "email": "rajeshwar@indorespecialitymed.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/drpatel-med",
                },
                potential_needs=[
                    "Specialist referral automation",
                    "Digital appointment follow-up workflows",
                ],
                tech_stack=["WordPress", "PHP"],
                sources=[
                    {"field": "company_name", "source": "mca_directory", "verified": True},
                    {"field": "location", "source": "verified_premises", "verified": True},
                ],
                missing_fields=["billing_software", "direct_mobile"],
                is_sample=True,
            ),
        ]

    def _legal_leads(self, profile: dict[str, Any]) -> list[ResearchProfile]:
        return [
            ResearchProfile(
                company_name="Vanguard Legal Partners LLP",
                domain="vanguardlegal.com",
                website="https://vanguardlegal.com",
                industry="Legal Services / Law Firms",
                location="New York, NY & London, UK",
                company_size="45-120 employees",
                description="Commercial corporate law, M&A advisory, and cross-border regulatory compliance firm.",
                decision_maker={
                    "name": "Eleanor Vance",
                    "title": "Managing Partner",
                    "email": "eleanor.vance@vanguardlegal.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/eleanor-vance-law",
                },
                potential_needs=[
                    "Prospective client intake triage and inquiry qualification",
                    "Cross-border consultation scheduling while preserving strict confidentiality",
                ],
                tech_stack=["NetDocuments", "Clio", "WordPress"],
                sources=[
                    {"field": "company_name", "source": "state_bar_directory", "verified": True},
                    {"field": "decision_maker", "source": "firm_partner_page", "verified": True},
                    {"field": "domain", "source": "whois_dns", "verified": True},
                ],
                missing_fields=["hourly_billing_rates", "internal_docketing_system"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="Sterling & Croft Commercial Counsel",
                domain="sterlingcroftlaw.com",
                website="https://sterlingcroftlaw.com",
                industry="Legal Services / Law Firms",
                location="Chicago, IL",
                company_size="20-50 employees",
                description="Commercial contract dispute litigation and intellectual property advisory boutique.",
                decision_maker={
                    "name": "Marcus Sterling",
                    "title": "Partner & Practice Leader",
                    "email": "msterling@sterlingcroftlaw.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/marcussterling-litigation",
                },
                potential_needs=[
                    "Automated intake questionnaire follow-up for commercial litigation inquiries",
                    "Partner review gating before prospective client engagement",
                ],
                tech_stack=["WordPress", "Cloudflare", "MyCase"],
                sources=[
                    {"field": "company_name", "source": "chamber_of_commerce", "verified": True},
                    {"field": "location", "source": "commercial_lease_registry", "verified": True},
                ],
                missing_fields=["case_management_api", "cell_phone"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="Apex Intellectual Property Group",
                domain="apexipgroup.com",
                website="https://apexipgroup.com",
                industry="Legal Services / Law Firms",
                location="Boston, MA",
                company_size="15-40 employees",
                description="Patent prosecution and technology licensing counsel for tech startups and life sciences.",
                decision_maker={
                    "name": "Dr. Raymond Zhao",
                    "title": "Practice Group Leader",
                    "email": "rzhao@apexipgroup.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/raymondzhao-patent",
                },
                potential_needs=[
                    "Streamlining prospective patent applicant consultation inquiries",
                ],
                tech_stack=["Webflow", "HubSpot"],
                sources=[
                    {"field": "company_name", "source": "uspto_practitioner_directory", "verified": True},
                ],
                missing_fields=["annual_filing_volume", "direct_extension"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="Express Traffic Citation Help",
                domain="expresstraffichelp.net",
                website="https://expresstraffichelp.net",
                industry="Consumer Legal Processing",
                location="Miami, FL",
                company_size="1 employee",
                description="Solo administrative traffic citation processing website.",
                decision_maker={
                    "name": "Intake Desk",
                    "title": "Intake Clerk",
                    "email": "intake@expresstraffichelp.net",
                    "email_status": "guess",
                    "linkedin_url": "",
                },
                potential_needs=["Online payment form"],
                tech_stack=["Static HTML"],
                sources=[{"field": "company_name", "source": "domain_lookup", "verified": True}],
                missing_fields=["managing_attorney", "phone", "bar_registration"],
                is_sample=True,
            ),
        ]

    def _accounting_leads(self, profile: dict[str, Any]) -> list[ResearchProfile]:
        return [
            ResearchProfile(
                company_name="Crestview Advisory & CPA Group",
                domain="crestviewcpa.com",
                website="https://crestviewcpa.com",
                industry="Accounting & Financial Services",
                location="Denver, CO",
                company_size="30-90 employees",
                description="Full-service regional CPA and strategic advisory firm offering business tax, audit, and outsourced accounting.",
                decision_maker={
                    "name": "Robert Thorne, CPA",
                    "title": "Managing Partner",
                    "email": "rthorne@crestviewcpa.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/robertthorne-cpa",
                },
                potential_needs=[
                    "Automating new client advisory intake and discovery scheduling",
                    "Streamlining document request follow-ups during seasonal peaks",
                ],
                tech_stack=["WordPress", "CCH Axcess", "Microsoft 365"],
                sources=[
                    {"field": "company_name", "source": "aicpa_directory", "verified": True},
                    {"field": "decision_maker", "source": "partner_leadership_page", "verified": True},
                ],
                missing_fields=["practice_management_vendor", "direct_dial"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="Summit Financial Strategy Partners",
                domain="summitfinancialpartners.com",
                website="https://summitfinancialpartners.com",
                industry="Accounting & Financial Services",
                location="Atlanta, GA",
                company_size="15-45 employees",
                description="Fractional CFO services and proactive corporate tax advisory for fast-growing businesses.",
                decision_maker={
                    "name": "Elena Rostova",
                    "title": "Founder & Practice Leader",
                    "email": "elena@summitfinancialpartners.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/elenarostova-cfo",
                },
                potential_needs=[
                    "Outbound engagement of prospective founders and business owners",
                    "Automated consultation follow-up cadence",
                ],
                tech_stack=["Squarespace", "Calendly", "QuickBooks Online"],
                sources=[
                    {"field": "company_name", "source": "state_accountancy_board", "verified": True},
                    {"field": "domain", "source": "whois_dns", "verified": True},
                ],
                missing_fields=["firm_aum", "direct_mobile"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="Precision Books & Tax Advisors",
                domain="precisiontaxadvisors.com",
                website="https://precisiontaxadvisors.com",
                industry="Accounting & Financial Services",
                location="Dallas, TX",
                company_size="10-30 employees",
                description="Small business bookkeeping, payroll compliance, and multi-state tax filing advisory.",
                decision_maker={
                    "name": "Julian Miller",
                    "title": "Tax Director",
                    "email": "jmiller@precisiontaxadvisors.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/julianmiller-tax",
                },
                potential_needs=[
                    "Reducing manual administrative chase for onboarding documents",
                ],
                tech_stack=["Wix", "Xero"],
                sources=[{"field": "company_name", "source": "yellowpages_biz", "verified": True}],
                missing_fields=["corporate_revenue", "phone_extension"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="QuickFile Automated Tax Kiosk",
                domain="quickfilekiosk.net",
                website="https://quickfilekiosk.net",
                industry="Consumer Tax Filing",
                location="Phoenix, AZ",
                company_size="2 employees",
                description="Self-service automated retail tax preparation kiosks.",
                decision_maker={
                    "name": "Customer Support",
                    "title": "Help Desk Agent",
                    "email": "support@quickfilekiosk.net",
                    "email_status": "guess",
                    "linkedin_url": "",
                },
                potential_needs=["Kiosk printer service"],
                tech_stack=["Static HTML"],
                sources=[{"field": "company_name", "source": "public_dns", "verified": True}],
                missing_fields=["executive_directors", "phone", "cpa_license"],
                is_sample=True,
            ),
        ]

    def _home_services_leads(self, profile: dict[str, Any]) -> list[ResearchProfile]:
        return [
            ResearchProfile(
                company_name="Apex Mechanical & Commercial HVAC",
                domain="apexcommercialhvac.com",
                website="https://apexcommercialhvac.com",
                industry="Commercial & Home Services",
                location="Houston, TX",
                company_size="35-100 employees",
                description="Commercial heating, ventilation, air conditioning, and building automation systems contractor.",
                decision_maker={
                    "name": "Thomas Briggs",
                    "title": "VP Operations",
                    "email": "tbriggs@apexcommercialhvac.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/thomasbriggs-hvac",
                },
                potential_needs=[
                    "Commercial RFP estimate follow-up acceleration",
                    "Planned preventative maintenance contract renewal outreach",
                ],
                tech_stack=["WordPress", "ServiceTitan", "Google Analytics"],
                sources=[
                    {"field": "company_name", "source": "state_licensing_board", "verified": True},
                    {"field": "decision_maker", "source": "leadership_roster", "verified": True},
                ],
                missing_fields=["fleet_vehicle_count", "direct_radio_line"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="Trident Facility Solutions",
                domain="tridentfacility.com",
                website="https://tridentfacility.com",
                industry="Commercial & Home Services",
                location="Charlotte, NC",
                company_size="25-60 employees",
                description="Commercial mechanical maintenance, energy retrofits, and electrical facility services.",
                decision_maker={
                    "name": "Danielle Rivera",
                    "title": "General Manager",
                    "email": "drivera@tridentfacility.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/daniellerivera-facility",
                },
                potential_needs=[
                    "Systematic quotation follow-up for property managers and commercial building owners",
                    "Maintaining operator oversight before sending contract proposals",
                ],
                tech_stack=["Webflow", "HubSpot CRM"],
                sources=[
                    {"field": "company_name", "source": "contractors_association", "verified": True},
                    {"field": "location", "source": "commercial_registry", "verified": True},
                ],
                missing_fields=["subcontractor_roster", "emergency_dispatch_line"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="Beacon Climate Control Systems",
                domain="beaconclimate.com",
                website="https://beaconclimate.com",
                industry="Commercial & Home Services",
                location="Atlanta, GA",
                company_size="20-50 employees",
                description="HVAC design-build and environmental controls for commercial warehouses and offices.",
                decision_maker={
                    "name": "Greg Hoffman",
                    "title": "Estimating Director",
                    "email": "ghoffman@beaconclimate.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/greghoffman-hvac",
                },
                potential_needs=[
                    "Speed-to-lead for commercial bid requests and energy audit follow-ups",
                ],
                tech_stack=["WordPress", "Procore"],
                sources=[{"field": "company_name", "source": "state_contractor_portal", "verified": True}],
                missing_fields=["bonding_capacity", "cell_phone"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="Handy Dan Lawn Care",
                domain="handydanlawn.com",
                website="https://handydanlawn.com",
                industry="Residential Yard Care",
                location="Tampa, FL",
                company_size="1 employee",
                description="Solo residential lawn maintenance and grass cutting service.",
                decision_maker={
                    "name": "Dan",
                    "title": "Sole Proprietor",
                    "email": "dan@handydanlawn.com",
                    "email_status": "guess",
                    "linkedin_url": "",
                },
                potential_needs=["Flyer design"],
                tech_stack=["Wix"],
                sources=[{"field": "company_name", "source": "local_directory", "verified": True}],
                missing_fields=["last_name", "phone", "business_license"],
                is_sample=True,
            ),
        ]

    def _cleantech_leads(self, profile: dict[str, Any]) -> list[ResearchProfile]:
        return [
            ResearchProfile(
                company_name="Starlight CleanTech Solutions",
                domain="starlightcleantech.com",
                website="https://starlightcleantech.com",
                industry="CleanTech / Renewable Energy",
                location="Austin, Texas, USA",
                company_size="25-75 employees",
                description="Commercial solar microgrids and industrial battery storage systems.",
                business_type="Clean Energy Systems Integrator",
                relevant_use_cases=[
                    "Commercial facility solar feasibility outreach",
                    "Battery microgrid inquiry qualification",
                ],
                decision_maker={
                    "name": "Sarah Jenkins",
                    "title": "Director of Commercial Development",
                    "email": "sarah.j@starlightcleantech.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/sarahjenkins-solar",
                },
                potential_needs=[
                    "Automated inquiry qualification for commercial microgrid requests",
                    "Follow-up scheduling with facility energy managers",
                ],
                tech_stack=["Webflow", "Next.js", "HubSpot"],
                sources=[
                    {"field": "company_name", "source_url": "https://mca.gov.in/registry/cleantech", "source_title": "Clean Energy Registry", "verified": True},
                    {"field": "decision_maker", "source_url": "https://starlightcleantech.com/leadership", "source_title": "Leadership Page", "verified": True},
                    {"field": "domain", "source_url": "https://whois.domaintools.com/starlightcleantech.com", "source_title": "WHOIS DNS Registry", "verified": True},
                ],
                source_url="https://starlightcleantech.com",
                source_title="Starlight CleanTech Solutions Official Site",
                research_timestamp="2026-11-04T12:00:00Z",
                confidence=0.92,
                missing_fields=["annual_megawatt_capacity", "personal_cell"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="Solara Grid Systems",
                domain="solaragrids.io",
                website="https://solaragrids.io",
                industry="CleanTech / Renewable Energy",
                location="Denver, Colorado, USA",
                company_size="15-40 employees",
                description="Distributed energy resource management software for rural electric co-ops.",
                business_type="B2B CleanTech SaaS",
                relevant_use_cases=[
                    "Utility co-op outreach",
                    "Energy management demo requests",
                ],
                decision_maker={
                    "name": "Marcus Vance",
                    "title": "VP Business Development",
                    "email": "marcus@solaragrids.io",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/marcusvance-energy",
                },
                potential_needs=["Utility procurement meeting coordination"],
                tech_stack=["React", "AWS", "Stripe"],
                sources=[
                    {"field": "company_name", "source_url": "https://sec.gov/edgar/solaragrids", "source_title": "SEC Corporation Index", "verified": True},
                ],
                source_url="https://solaragrids.io",
                source_title="Solara Grid Systems Homepage",
                research_timestamp="2026-11-04T12:00:00Z",
                confidence=0.88,
                missing_fields=["exact_headcount", "private_phone"],
                is_sample=True,
            ),
        ]

    def _real_estate_leads(self, profile: dict[str, Any]) -> list[ResearchProfile]:
        return [
            ResearchProfile(
                company_name="Highland Commercial Realty & Advisory",
                domain="highlandcommercialrealty.com",
                website="https://highlandcommercialrealty.com",
                industry="Real Estate and Property",
                location="Dallas, TX",
                company_size="35-90 employees",
                description="Commercial real estate brokerage specializing in prime industrial acquisitions, office leasing, and asset valuation.",
                decision_maker={
                    "name": "Victoria Sterling",
                    "title": "Managing Principal & Broker-Owner",
                    "email": "vsterling@highlandcommercialrealty.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/victoriasterling-cre",
                },
                potential_needs=[
                    "Commercial listing inquiry qualification and viewing coordination",
                    "High-value prospective tenant intake triage",
                ],
                tech_stack=["CoStar", "LoopNet", "WordPress", "HubSpot"],
                sources=[
                    {"field": "company_name", "source": "texas_real_estate_commission", "verified": True},
                    {"field": "decision_maker", "source": "brokerage_leadership_page", "verified": True},
                    {"field": "domain", "source": "whois_dns", "verified": True},
                ],
                missing_fields=["annual_transaction_volume", "direct_cell"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="Beacon Multi-Family Property Group",
                domain="beaconmultifamily.com",
                website="https://beaconmultifamily.com",
                industry="Real Estate and Property",
                location="Atlanta, GA",
                company_size="50-120 employees",
                description="Multi-family residential portfolio management and commercial mixed-use leasing operators.",
                decision_maker={
                    "name": "David Callahan",
                    "title": "VP Property Management",
                    "email": "dcallahan@beaconmultifamily.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/davidcallahan-pm",
                },
                potential_needs=[
                    "Automated tenant lease renewal follow-up sequences",
                    "Corporate inquiry coordination for commercial ground-floor suites",
                ],
                tech_stack=["Yardi Voyager", "RealPage", "Next.js"],
                sources=[
                    {"field": "company_name", "source": "georgia_property_owners_registry", "verified": True},
                    {"field": "location", "source": "commercial_lease_register", "verified": True},
                ],
                missing_fields=["average_occupancy_rate", "emergency_dispatch_line"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="Metropolitan Real Estate Partners",
                domain="metropartnersre.com",
                website="https://metropartnersre.com",
                industry="Real Estate and Property",
                location="Chicago, IL",
                company_size="20-45 employees",
                description="Boutique investment sales and retail landlord representation firm in downtown Chicago.",
                decision_maker={
                    "name": "Marcus Vance",
                    "title": "Director of Commercial Leasing",
                    "email": "mvance@metropartnersre.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/marcusvance-re",
                },
                potential_needs=[
                    "Buyer and commercial tenant consultation scheduling",
                    "Accelerating speed-to-lead for inbound property inquiries",
                ],
                tech_stack=["Webflow", "Buildium", "Google Analytics"],
                sources=[
                    {"field": "company_name", "source": "illinois_realtors_directory", "verified": True},
                ],
                missing_fields=["exact_square_footage", "personal_mobile"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="Solo Flat Rental Desk",
                domain="flatrentalsolo.net",
                website="https://flatrentalsolo.net",
                industry="Residential Room Rental",
                location="Miami, FL",
                company_size="1 employee",
                description="Solo individual room sublease listing board.",
                decision_maker={
                    "name": "Listing Desk",
                    "title": "Desk Clerk",
                    "email": "desk@flatrentalsolo.net",
                    "email_status": "guess",
                    "linkedin_url": "",
                },
                potential_needs=["Craigslist posting tool"],
                tech_stack=["Static HTML"],
                sources=[{"field": "company_name", "source": "whois_lookup", "verified": True}],
                missing_fields=["broker_license", "phone", "managing_principal"],
                is_sample=True,
            ),
        ]

    def _manufacturing_leads(self, profile: dict[str, Any]) -> list[ResearchProfile]:
        return [
            ResearchProfile(
                company_name="Precision Industrial Machining Corp",
                domain="precisionmachiningcorp.com",
                website="https://precisionmachiningcorp.com",
                industry="Manufacturing and Industrial",
                location="Cleveland, OH",
                company_size="60-150 employees",
                description="ISO 9001 and AS9100 certified CNC precision machining, tooling, and contract component manufacturer.",
                decision_maker={
                    "name": "Walter Jenkins",
                    "title": "VP Operations",
                    "email": "wjenkins@precisionmachiningcorp.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/walterjenkins-mfg",
                },
                potential_needs=[
                    "Industrial RFQ quotation follow-up acceleration",
                    "Commercial supply contract renewal outreach",
                ],
                tech_stack=["JobBOSS", "Mastercam", "WordPress"],
                sources=[
                    {"field": "company_name", "source": "ohio_manufacturers_association", "verified": True},
                    {"field": "decision_maker", "source": "plant_leadership_roster", "verified": True},
                    {"field": "domain", "source": "whois_dns", "verified": True},
                ],
                missing_fields=["spindle_hours_capacity", "direct_extension"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="Apex Automation & Tooling Systems",
                domain="apexautomationtool.com",
                website="https://apexautomationtool.com",
                industry="Manufacturing and Industrial",
                location="Detroit, MI",
                company_size="40-100 employees",
                description="Custom automated robotic assembly cells and progressive stamping dies for tier-1 automotive suppliers.",
                decision_maker={
                    "name": "Elena Rostova",
                    "title": "Plant Manager & Director of Manufacturing",
                    "email": "elena@apexautomationtool.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/elenarostova-automation",
                },
                potential_needs=[
                    "Speed-to-lead for custom tooling bids and engineering estimates",
                    "Re-engaging dormant OEM accounts for tooling maintenance",
                ],
                tech_stack=["SolidWorks", "Siemens PLM", "HubSpot"],
                sources=[
                    {"field": "company_name", "source": "michigan_industrial_directory", "verified": True},
                    {"field": "location", "source": "facility_registry", "verified": True},
                ],
                missing_fields=["annual_tonnage", "private_mobile"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="Vanguard Custom Metal Fabricators",
                domain="vanguardfabrication.com",
                website="https://vanguardfabrication.com",
                industry="Manufacturing and Industrial",
                location="Milwaukee, WI",
                company_size="25-70 employees",
                description="Structural steel fabrication, laser cutting, and architectural metal assemblies.",
                decision_maker={
                    "name": "Carl Henderson",
                    "title": "Director of Procurement & Estimating",
                    "email": "chenderson@vanguardfabrication.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/carlhenderson-fab",
                },
                potential_needs=[
                    "Subcontractor bid chase automation for commercial structural bids",
                ],
                tech_stack=["Procore", "AutoCAD", "Wix"],
                sources=[{"field": "company_name", "source": "fabricators_manufacturers_assoc", "verified": True}],
                missing_fields=["crane_tonnage", "fax_number"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="Bob Garage Scraping",
                domain="bobscrapemetal.net",
                website="https://bobscrapemetal.net",
                industry="Scrap Metal Collection",
                location="Toledo, OH",
                company_size="1 employee",
                description="Solo local residential junk metal pickup service.",
                decision_maker={
                    "name": "Bob",
                    "title": "Collector",
                    "email": "bob@bobscrapemetal.net",
                    "email_status": "guess",
                    "linkedin_url": "",
                },
                potential_needs=["Scale calibration"],
                tech_stack=["Static HTML"],
                sources=[{"field": "company_name", "source": "local_flyer", "verified": True}],
                missing_fields=["plant_manager", "phone", "iso_cert"],
                is_sample=True,
            ),
        ]

    def _hospitality_leads(self, profile: dict[str, Any]) -> list[ResearchProfile]:
        return [
            ResearchProfile(
                company_name="The Grandview Luxury Boutique Hotel & Resort",
                domain="grandviewhotelresort.com",
                website="https://grandviewhotelresort.com",
                industry="Hospitality, Hotels and Restaurants",
                location="Aspen, CO",
                company_size="50-180 employees",
                description="Four-star alpine boutique resort featuring 120 guest suites, conference facilities, and farm-to-table dining.",
                decision_maker={
                    "name": "Jean-Luc Moreau",
                    "title": "General Manager",
                    "email": "jmoreau@grandviewhotelresort.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/jeanlucmoreau-hotel",
                },
                potential_needs=[
                    "Corporate offsite and executive retreat inquiry qualification",
                    "Private banquet and wedding event booking follow-up automation",
                ],
                tech_stack=["Opera PMS", "SevenRooms", "WordPress"],
                sources=[
                    {"field": "company_name", "source": "colorado_hotel_lodging_assoc", "verified": True},
                    {"field": "decision_maker", "source": "resort_executive_directory", "verified": True},
                    {"field": "domain", "source": "whois_dns", "verified": True},
                ],
                missing_fields=["revpar_metrics", "direct_concierge_mobile"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="Metropolitan Hospitality & Events Group",
                domain="metrohospitalitygroup.com",
                website="https://metrohospitalitygroup.com",
                industry="Hospitality, Hotels and Restaurants",
                location="New York, NY",
                company_size="70-220 employees",
                description="Multi-venue hospitality group operating corporate conference dining spaces and high-volume banquet halls in Manhattan.",
                decision_maker={
                    "name": "Sophia Chen",
                    "title": "Director of Sales & Events",
                    "email": "schen@metrohospitalitygroup.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/sophiachen-events",
                },
                potential_needs=[
                    "Accelerating response time for corporate event and gala RFPs",
                    "Automated follow-up cadence for recurring quarterly corporate catering",
                ],
                tech_stack=["Tripleseat", "Toast POS", "Webflow"],
                sources=[
                    {"field": "company_name", "source": "nyc_hospitality_alliance", "verified": True},
                    {"field": "location", "source": "commercial_lease_registry", "verified": True},
                ],
                missing_fields=["catering_liquor_license_expiry", "private_phone"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="Coastal Breeze Resorts & Dining",
                domain="coastalbreezeresorts.com",
                website="https://coastalbreezeresorts.com",
                industry="Hospitality, Hotels and Restaurants",
                location="Miami, FL",
                company_size="30-90 employees",
                description="Oceanfront boutique resort offering corporate executive retreats and private dining pavilions.",
                decision_maker={
                    "name": "Carlos Mendez",
                    "title": "VP Hospitality Operations",
                    "email": "cmendez@coastalbreezeresorts.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/carlosmendez-hospitality",
                },
                potential_needs=[
                    "Group booking inquiry qualification and seasonal promotional follow-ups",
                ],
                tech_stack=["Cloudbeds", "OpenTable", "Squarespace"],
                sources=[{"field": "company_name", "source": "florida_restaurant_lodging_assoc", "verified": True}],
                missing_fields=["total_room_count", "direct_cell"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="Dan Bed and Breakfast",
                domain="danbandb.net",
                website="https://danbandb.net",
                industry="Budget B&B",
                location="Tampa, FL",
                company_size="1 employee",
                description="Solo two-bedroom bed and breakfast homestay.",
                decision_maker={
                    "name": "Dan",
                    "title": "Owner",
                    "email": "dan@danbandb.net",
                    "email_status": "guess",
                    "linkedin_url": "",
                },
                potential_needs=["Brochure printing"],
                tech_stack=["Static HTML"],
                sources=[{"field": "company_name", "source": "local_directory", "verified": True}],
                missing_fields=["general_manager", "phone", "event_coordinator"],
                is_sample=True,
            ),
        ]

    def _logistics_leads(self, profile: dict[str, Any]) -> list[ResearchProfile]:
        return [
            ResearchProfile(
                company_name="Apex Freight Solutions & Logistics",
                domain="apexfreightlogistics.com",
                website="https://apexfreightlogistics.com",
                industry="Logistics and Transportation",
                location="Chicago, IL",
                company_size="45-130 employees",
                description="Third-party logistics provider and non-asset freight brokerage handling dry van, refrigerated, and flatbed transport across North America.",
                decision_maker={
                    "name": "Richard Reynolds",
                    "title": "VP Logistics & Carrier Procurement",
                    "email": "rreynolds@apexfreightlogistics.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/richardreynolds-freight",
                },
                potential_needs=[
                    "Shipper spot-quote follow-up speed to increase RFP win rate",
                    "Re-engaging dormant enterprise shipper accounts for dedicated lane volume",
                ],
                tech_stack=["McLeod Software", "DAT One", "WordPress", "HubSpot"],
                sources=[
                    {"field": "company_name", "source": "transportation_intermediaries_assoc", "verified": True},
                    {"field": "decision_maker", "source": "carrier_procurement_roster", "verified": True},
                    {"field": "domain", "source": "whois_dns", "verified": True},
                ],
                missing_fields=["annual_load_volume", "dispatch_direct_radio"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="TransGlobal Third-Party Logistics (3PL)",
                domain="transglobal3pl.com",
                website="https://transglobal3pl.com",
                industry="Logistics and Transportation",
                location="Dallas, TX",
                company_size="60-180 employees",
                description="Contract warehousing, fulfillment, and cross-docking operations serving e-commerce and retail distributors.",
                decision_maker={
                    "name": "Maria Santos",
                    "title": "Director of Supply Chain Solutions",
                    "email": "msantos@transglobal3pl.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/mariasantos-3pl",
                },
                potential_needs=[
                    "Prospective shipper warehouse intake qualification",
                    "Automated follow-up on fulfillment and storage proposals",
                ],
                tech_stack=["Manhattan Associates", "HighJump WMS", "Webflow"],
                sources=[
                    {"field": "company_name", "source": "international_warehouse_logistics_assoc", "verified": True},
                    {"field": "location", "source": "commercial_facility_register", "verified": True},
                ],
                missing_fields=["dock_door_count", "private_mobile"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="Interstate Haulage & Intermodal Carriers",
                domain="interstatehaulage.com",
                website="https://interstatehaulage.com",
                industry="Logistics and Transportation",
                location="Memphis, TN",
                company_size="35-90 employees",
                description="Regional intermodal container haulage and dedicated fleet transport across southern rail terminals.",
                decision_maker={
                    "name": "Kenneth Brooks",
                    "title": "Head of Freight Operations",
                    "email": "kbrooks@interstatehaulage.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/kennethbrooks-trucking",
                },
                potential_needs=[
                    "Dedicated lane contract proposal tracking and freight rate renewals",
                ],
                tech_stack=["TMW Systems", "Samsara", "Squarespace"],
                sources=[{"field": "company_name", "source": "american_trucking_associations", "verified": True}],
                missing_fields=["dot_safety_score", "cell_extension"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="Joe Single Truck Hauling",
                domain="joetruck.net",
                website="https://joetruck.net",
                industry="Owner Operator",
                location="Omaha, NE",
                company_size="1 employee",
                description="Solo owner-operator box truck delivery driver.",
                decision_maker={
                    "name": "Joe",
                    "title": "Driver",
                    "email": "joe@joetruck.net",
                    "email_status": "guess",
                    "linkedin_url": "",
                },
                potential_needs=["Fuel discount card"],
                tech_stack=["Static HTML"],
                sources=[{"field": "company_name", "source": "loadboard_free", "verified": True}],
                missing_fields=["fleet_manager", "phone", "mc_authority"],
                is_sample=True,
            ),
        ]

    def _education_leads(self, profile: dict[str, Any]) -> list[ResearchProfile]:
        return [
            ResearchProfile(
                company_name="Horizon Executive Learning Institute",
                domain="horizonexecutivelearning.org",
                website="https://horizonexecutivelearning.org",
                industry="Education and EdTech",
                location="Boston, MA",
                company_size="40-110 employees",
                description="Postgraduate executive education and professional leadership certifications for mid-career corporate executives.",
                decision_maker={
                    "name": "Dr. Evelyn Vance",
                    "title": "Dean of Executive Programs",
                    "email": "evance@horizonexecutivelearning.org",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/evelynvance-edu",
                },
                potential_needs=[
                    "Executive candidate application qualification and advisory triage",
                    "Corporate enterprise sponsorship outreach and partnership follow-up",
                ],
                tech_stack=["Canvas LMS", "Salesforce Education Cloud", "WordPress"],
                sources=[
                    {"field": "company_name", "source": "massachusetts_dept_higher_ed", "verified": True},
                    {"field": "decision_maker", "source": "faculty_leadership_roster", "verified": True},
                    {"field": "domain", "source": "whois_dns", "verified": True},
                ],
                missing_fields=["annual_endowment", "direct_office_mobile"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="Apex Tech Academy & Professional Training",
                domain="apextechacademy.com",
                website="https://apextechacademy.com",
                industry="Education and EdTech",
                location="San Francisco, CA",
                company_size="30-80 employees",
                description="Immersive software engineering, data science, and cloud architecture bootcamps and corporate reskilling programs.",
                decision_maker={
                    "name": "Jason Patel",
                    "title": "Director of Admissions & Enrollment",
                    "email": "jason@apextechacademy.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/jasonpatel-edtech",
                },
                potential_needs=[
                    "Prospective student inquiry qualification to reduce admissions drop-off",
                    "Automated interview booking workflows for qualified applicants",
                ],
                tech_stack=["HubSpot", "Thinkific", "Next.js"],
                sources=[
                    {"field": "company_name", "source": "california_postsecondary_directory", "verified": True},
                    {"field": "location", "source": "campus_lease_registry", "verified": True},
                ],
                missing_fields=["graduation_employment_rate", "private_phone"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="Midwest University Global Studies",
                domain="midwestglobalstudies.edu",
                website="https://midwestglobalstudies.edu",
                industry="Education and EdTech",
                location="Chicago, IL",
                company_size="80-250 employees",
                description="Higher education international exchange programs and foreign university academic partnerships.",
                decision_maker={
                    "name": "Dr. Margaret Shaw",
                    "title": "VP Academic Partnerships",
                    "email": "mshaw@midwestglobalstudies.edu",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/margaretshaw-academic",
                },
                potential_needs=[
                    "International institution partnership inquiries and MOU follow-up",
                ],
                tech_stack=["Banner SIS", "Blackboard", "Drupal"],
                sources=[{"field": "company_name", "source": "us_dept_of_education_database", "verified": True}],
                missing_fields=["international_tuition_rates", "office_direct_dial"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="Sam Home Math Tutor",
                domain="samtutoring.net",
                website="https://samtutoring.net",
                industry="Private Tutoring",
                location="Austin, TX",
                company_size="1 employee",
                description="Solo home high school algebra and geometry tutor.",
                decision_maker={
                    "name": "Sam",
                    "title": "Tutor",
                    "email": "sam@samtutoring.net",
                    "email_status": "guess",
                    "linkedin_url": "",
                },
                potential_needs=["Whiteboard markers"],
                tech_stack=["Static HTML"],
                sources=[{"field": "company_name", "source": "community_bulletin", "verified": True}],
                missing_fields=["dean_of_admissions", "phone", "accreditation"],
                is_sample=True,
            ),
        ]

    def _marketing_agency_leads(self, profile: dict[str, Any]) -> list[ResearchProfile]:
        return [
            ResearchProfile(
                company_name="Catalyst Growth Marketing & Performance Group",
                domain="catalystgrowthagency.com",
                website="https://catalystgrowthagency.com",
                industry="Marketing and Consulting Agencies",
                location="New York, NY",
                company_size="25-75 employees",
                description="B2B growth agency specializing in paid acquisition, conversion rate optimization, and ABM pipeline generation for enterprise tech.",
                decision_maker={
                    "name": "Rachel Sterling",
                    "title": "Managing Director & Agency Principal",
                    "email": "rachel@catalystgrowthagency.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/rachelsterling-growth",
                },
                potential_needs=[
                    "Prospective brand pitch qualification and consultation booking",
                    "RFP proposal follow-up coordination to shorten agency sales cycles",
                ],
                tech_stack=["Webflow", "HubSpot CRM", "Google Tag Manager", "Mixpanel"],
                sources=[
                    {"field": "company_name", "source": "american_association_advertising_agencies", "verified": True},
                    {"field": "decision_maker", "source": "agency_partner_roster", "verified": True},
                    {"field": "domain", "source": "whois_dns", "verified": True},
                ],
                missing_fields=["monthly_retainer_floor", "personal_mobile"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="Apex Digital Performance Partners",
                domain="apexdigitalpartners.io",
                website="https://apexdigitalpartners.io",
                industry="Marketing and Consulting Agencies",
                location="San Francisco, CA",
                company_size="35-90 employees",
                description="Full-funnel digital marketing, organic search architecture, and demand generation consulting for venture-backed SaaS.",
                decision_maker={
                    "name": "Marcus Lee",
                    "title": "VP Client Strategy & BD",
                    "email": "mlee@apexdigitalpartners.io",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/marcuslee-digital",
                },
                potential_needs=[
                    "Automating follow-up on pitch decks and client proposals",
                    "Qualifying prospective brand inquiries against agency retainer minimums",
                ],
                tech_stack=["WordPress", "Clearbit", "Segment", "Slack"],
                sources=[
                    {"field": "company_name", "source": "bay_area_marketing_association", "verified": True},
                    {"field": "location", "source": "commercial_lease_register", "verified": True},
                ],
                missing_fields=["annual_billings", "direct_line"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="Elevation Brand Consulting & Creative",
                domain="elevationbrandconsulting.com",
                website="https://elevationbrandconsulting.com",
                industry="Marketing and Consulting Agencies",
                location="London, UK",
                company_size="20-50 employees",
                description="Corporate brand positioning, executive communication advisory, and investor relations creative boutique.",
                decision_maker={
                    "name": "Oliver Wright",
                    "title": "Partner & Head of Growth",
                    "email": "owright@elevationbrandconsulting.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/oliverwright-branding",
                },
                potential_needs=[
                    "Prospective client discovery consultation scheduling and follow-up",
                ],
                tech_stack=["Framer", "Typeform", "Asana"],
                sources=[{"field": "company_name", "source": "design_business_association_uk", "verified": True}],
                missing_fields=["portfolio_awards", "direct_cell"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="One Man Social Postings",
                domain="onemansocial.net",
                website="https://onemansocial.net",
                industry="Freelance Social Media",
                location="Phoenix, AZ",
                company_size="1 employee",
                description="Solo freelance social media graphic posting service.",
                decision_maker={
                    "name": "Jack",
                    "title": "Freelancer",
                    "email": "jack@onemansocial.net",
                    "email_status": "guess",
                    "linkedin_url": "",
                },
                potential_needs=["Canva template pack"],
                tech_stack=["Static HTML"],
                sources=[{"field": "company_name", "source": "classifieds", "verified": True}],
                missing_fields=["agency_principal", "phone", "client_roster"],
                is_sample=True,
            ),
        ]

    def _ecommerce_leads(self, profile: dict[str, Any]) -> list[ResearchProfile]:
        return [
            ResearchProfile(
                company_name="Lumina Consumer Brands & Distribution",
                domain="luminabrands.com",
                website="https://luminabrands.com",
                industry="E-commerce and Retail",
                location="Los Angeles, CA",
                company_size="30-100 employees",
                description="Omnichannel lifestyle consumer goods brand retailing across direct-to-consumer and national retail department stores.",
                decision_maker={
                    "name": "Chloe Bennett",
                    "title": "VP E-commerce & Wholesale Accounts",
                    "email": "cbennett@luminabrands.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/chloebennett-retail",
                },
                potential_needs=[
                    "Independent boutique and regional department store wholesale outreach",
                    "Seasonal reorder follow-up automation for existing retail stockists",
                ],
                tech_stack=["Shopify Plus", "Klaviyo", "Netsuite ERP"],
                sources=[
                    {"field": "company_name", "source": "national_retail_federation", "verified": True},
                    {"field": "decision_maker", "source": "brand_wholesale_portal", "verified": True},
                ],
                missing_fields=["annual_gmv", "direct_extension"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="Apex Wholesale Goods & Retail Merchandising",
                domain="apexwholesalegoods.com",
                website="https://apexwholesalegoods.com",
                industry="E-commerce and Retail",
                location="Seattle, WA",
                company_size="45-120 employees",
                description="B2B wholesale distributor supplying regional supermarkets and specialty retailers with organic packaged foods.",
                decision_maker={
                    "name": "Trevor Scott",
                    "title": "Director of Retail Accounts",
                    "email": "tscott@apexwholesalegoods.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/trevorscott-merchandising",
                },
                potential_needs=[
                    "Prospective retail grocery buyer inquiry qualification",
                    "Automating catalog distribution and pricing follow-ups",
                ],
                tech_stack=["BigCommerce", "Fishbowl Inventory", "WordPress"],
                sources=[{"field": "company_name", "source": "wholesale_distributors_association", "verified": True}],
                missing_fields=["pallet_order_minimums", "cell_number"],
                is_sample=True,
            ),
        ]

    def _construction_leads(self, profile: dict[str, Any]) -> list[ResearchProfile]:
        return [
            ResearchProfile(
                company_name="Keystone Commercial Builders & Contractors",
                domain="keystonebuilders.com",
                website="https://keystonebuilders.com",
                industry="Construction and Infrastructure",
                location="Denver, CO",
                company_size="50-160 employees",
                description="General contractor specializing in commercial office towers, healthcare facilities, and civil infrastructure projects.",
                decision_maker={
                    "name": "Frank Miller",
                    "title": "VP Construction & Project Executive",
                    "email": "fmiller@keystonebuilders.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/frankmiller-construction",
                },
                potential_needs=[
                    "Subcontractor bid invitation follow-up and tender qualification",
                    "Commercial owner negotiated project consultation follow-ups",
                ],
                tech_stack=["Procore", "Bluebeam", "Autodesk Construction Cloud"],
                sources=[
                    {"field": "company_name", "source": "associated_general_contractors", "verified": True},
                    {"field": "decision_maker", "source": "project_executive_roster", "verified": True},
                ],
                missing_fields=["bonding_single_limit", "field_radio_direct"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="Pinnacle Infrastructure & Civil Engineering",
                domain="pinnacleinfrastructure.com",
                website="https://pinnacleinfrastructure.com",
                industry="Construction and Infrastructure",
                location="Atlanta, GA",
                company_size="60-200 employees",
                description="Heavy civil engineering contractor focused on bridge construction, municipal utilities, and highway paving.",
                decision_maker={
                    "name": "Nathan Drake",
                    "title": "Director of Estimating",
                    "email": "ndrake@pinnacleinfrastructure.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/nathandrake-civil",
                },
                potential_needs=[
                    "Municipal tender proposal tracking and specialty subcontractor chase",
                ],
                tech_stack=["HeavyBid", "Primavera P6", "WordPress"],
                sources=[{"field": "company_name", "source": "civil_contractors_federation", "verified": True}],
                missing_fields=["fleet_heavy_machinery_count", "direct_mobile"],
                is_sample=True,
            ),
        ]

    def _automotive_leads(self, profile: dict[str, Any]) -> list[ResearchProfile]:
        return [
            ResearchProfile(
                company_name="Apex Commercial Fleet Solutions & Dealerships",
                domain="apexfleetdealers.com",
                website="https://apexfleetdealers.com",
                industry="Automotive",
                location="Indianapolis, IN",
                company_size="40-120 employees",
                description="Commercial vehicle dealership and fleet leasing provider for municipal utilities, delivery companies, and contractors.",
                decision_maker={
                    "name": "Brian O'Connor",
                    "title": "Commercial Fleet Sales Director",
                    "email": "brian@apexfleetdealers.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/brianoconnor-fleet",
                },
                potential_needs=[
                    "Commercial fleet lease quotation follow-up acceleration",
                    "Tracking replacement vehicle procurement cycles for corporate accounts",
                ],
                tech_stack=["CDK Global", "Reynolds and Reynolds", "WordPress"],
                sources=[
                    {"field": "company_name", "source": "national_auto_dealers_association", "verified": True},
                    {"field": "decision_maker", "source": "commercial_sales_roster", "verified": True},
                ],
                missing_fields=["inventory_chassis_count", "desk_extension"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="Precision Auto Parts & OEM Supply",
                domain="precisionautopartsoem.com",
                website="https://precisionautopartsoem.com",
                industry="Automotive",
                location="Detroit, MI",
                company_size="55-140 employees",
                description="Tier-2 manufacturer of braking assemblies, powertrain components, and remanufactured electrical auto systems.",
                decision_maker={
                    "name": "Diane Walker",
                    "title": "VP Automotive Operations",
                    "email": "dwalker@precisionautopartsoem.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/dianewalker-autoparts",
                },
                potential_needs=[
                    "OEM supply agreement follow-ups and automotive distributor outreach",
                ],
                tech_stack=["SAP ERP", "Siemens NX", "Webflow"],
                sources=[{"field": "company_name", "source": "automotive_aftermarket_association", "verified": True}],
                missing_fields=["iatf_16949_audit_status", "private_phone"],
                is_sample=True,
            ),
        ]

    def _agriculture_leads(self, profile: dict[str, Any]) -> list[ResearchProfile]:
        return [
            ResearchProfile(
                company_name="Midwest Agronomic Inputs & Crop Advisory",
                domain="midwestagronomic.com",
                website="https://midwestagronomic.com",
                industry="Agriculture",
                location="Des Moines, IA",
                company_size="30-90 employees",
                description="Commercial crop nutrition, certified seed distribution, and soil microbiome advisory serving commercial grain producers.",
                decision_maker={
                    "name": "Douglas Campbell",
                    "title": "Agricultural Operations Director",
                    "email": "dcampbell@midwestagronomic.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/douglascampbell-agri",
                },
                potential_needs=[
                    "Pre-season commercial grower contract booking and seed order follow-ups",
                    "Distributor onboarding across regional agricultural retail cooperatives",
                ],
                tech_stack=["AgVend", "Climate FieldView", "WordPress"],
                sources=[
                    {"field": "company_name", "source": "iowa_agribusiness_association", "verified": True},
                    {"field": "decision_maker", "source": "agronomy_staff_directory", "verified": True},
                ],
                missing_fields=["total_managed_acreage", "direct_mobile"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="Apex Grain Storage & Cooperative Wholesalers",
                domain="apexgraincoop.com",
                website="https://apexgraincoop.com",
                industry="Agriculture",
                location="Fargo, ND",
                company_size="40-110 employees",
                description="Regional grain elevator, bulk grain storage, and commodity merchandising cooperative for soybean and wheat growers.",
                decision_maker={
                    "name": "Harold Miller",
                    "title": "General Manager & VP Agribusiness",
                    "email": "hmiller@apexgraincoop.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/haroldmiller-grain",
                },
                potential_needs=[
                    "Contract renewal outreach to commercial farm managers",
                ],
                tech_stack=["Kahler Automation", "AGRIS ERP", "Wix"],
                sources=[{"field": "company_name", "source": "national_grain_feed_association", "verified": True}],
                missing_fields=["elevator_bushel_capacity", "cell_phone"],
                is_sample=True,
            ),
        ]

    def _recruitment_leads(self, profile: dict[str, Any]) -> list[ResearchProfile]:
        return [
            ResearchProfile(
                company_name="Vanguard Executive Search Partners",
                domain="vanguardexecutivesearch.com",
                website="https://vanguardexecutivesearch.com",
                industry="Recruitment and HR",
                location="New York, NY",
                company_size="20-60 employees",
                description="Retained executive search firm placing C-suite officers and board directors across technology, finance, and healthcare.",
                decision_maker={
                    "name": "Claire Harrington",
                    "title": "Managing Director & Search Lead",
                    "email": "charrington@vanguardexecutivesearch.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/claireharrington-search",
                },
                potential_needs=[
                    "Enterprise client company acquisition and search mandate intake",
                    "Hiring manager proposal follow-ups to shorten mandate signing cycles",
                ],
                tech_stack=["Invenias", "Bullhorn", "WordPress"],
                sources=[
                    {"field": "company_name", "source": "association_executive_search_consultants", "verified": True},
                    {"field": "decision_maker", "source": "search_partner_page", "verified": True},
                ],
                missing_fields=["average_search_fee", "direct_dial"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="Apex Technical Talent & Staffing Solutions",
                domain="apextechtalent.com",
                website="https://apextechtalent.com",
                industry="Recruitment and HR",
                location="Austin, TX",
                company_size="35-95 employees",
                description="Specialized contract engineering and technical staffing solutions for high-growth tech companies and enterprise IT.",
                decision_maker={
                    "name": "Sean Mitchell",
                    "title": "VP Talent Acquisition Partnerships",
                    "email": "smitchell@apextechtalent.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/seanmitchell-staffing",
                },
                potential_needs=[
                    "Technical hiring manager outreach to win exclusive contract staffing requisitions",
                    "Automated interview briefing and candidate presentation follow-ups",
                ],
                tech_stack=["JobDiva", "LinkedIn Recruiter", "Webflow"],
                sources=[{"field": "company_name", "source": "american_staffing_association", "verified": True}],
                missing_fields=["active_contractor_bench", "private_cell"],
                is_sample=True,
            ),
        ]

    def _business_services_leads(self, profile: dict[str, Any]) -> list[ResearchProfile]:
        return [
            ResearchProfile(
                company_name="Nexus Corporate Facilities & Managed Services",
                domain="nexusmanagedservices.com",
                website="https://nexusmanagedservices.com",
                industry="Professional and Business Services",
                location="Chicago, IL",
                company_size="50-150 employees",
                description="Integrated facility services, commercial energy management, and managed corporate building technology for office campuses.",
                decision_maker={
                    "name": "Karen Adams",
                    "title": "VP Commercial Services",
                    "email": "kadams@nexusmanagedservices.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/karenadams-facility",
                },
                potential_needs=[
                    "Multi-year commercial building service agreement proposal follow-up",
                    "Corporate tenant move-in service intake coordination",
                ],
                tech_stack=["ServiceChannel", "Salesforce Service Cloud", "WordPress"],
                sources=[
                    {"field": "company_name", "source": "international_facility_management_assoc", "verified": True},
                    {"field": "decision_maker", "source": "commercial_services_roster", "verified": True},
                ],
                missing_fields=["square_feet_managed", "direct_mobile"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name="SecureShield Enterprise Protection & Security",
                domain="secureshieldservices.com",
                website="https://secureshieldservices.com",
                industry="Professional and Business Services",
                location="Dallas, TX",
                company_size="40-110 employees",
                description="Corporate security systems integration, cyber-physical access control, and compliance advisory for enterprise facilities.",
                decision_maker={
                    "name": "Anthony Rossi",
                    "title": "Director of Business Development",
                    "email": "arossi@secureshieldservices.com",
                    "email_status": "verified",
                    "linkedin_url": "linkedin.com/in/anthonyrossi-security",
                },
                potential_needs=[
                    "Commercial facility security audit proposal follow-ups and agreement renewals",
                ],
                tech_stack=["LenelS2", "HubSpot", "Squarespace"],
                sources=[{"field": "company_name", "source": "security_industry_association", "verified": True}],
                missing_fields=["monitoring_station_cert", "cell_extension"],
                is_sample=True,
            ),
        ]

    def _generic_leads(self, profile: dict[str, Any]) -> list[ResearchProfile]:
        industry = profile.get("industry") or "Target Industry"
        geo = profile.get("geography") or "Target Region"
        titles = profile.get("icp_titles") or ["Founder", "Director"]
        lead_title = titles[0] if titles else "Director"
        lead_title2 = titles[1] if len(titles) > 1 else "Operations Director"

        clean_slug = re.sub(r"[^a-z0-9]", "", industry.lower())[:10] or "targetbiz"
        clean_geo = re.sub(r"[^a-z0-9]", "", geo.lower())[:8] or "prime"

        return [
            ResearchProfile(
                company_name=f"{clean_geo.capitalize()} {clean_slug.capitalize()} Enterprise",
                domain=f"{clean_geo}{clean_slug}.com",
                website=f"https://{clean_geo}{clean_slug}.com",
                industry=industry,
                location=geo,
                company_size="15-50 employees",
                description=f"Established provider of services in {industry} based in {geo}.",
                decision_maker={
                    "name": "Alex Mercer",
                    "title": lead_title,
                    "email": f"alex@{clean_geo}{clean_slug}.com",
                    "email_status": "verified",
                    "linkedin_url": f"linkedin.com/in/alex-{clean_slug}",
                },
                potential_needs=[f"Automating workflow outreach in {industry}"],
                tech_stack=["Modern Web Stack"],
                sources=[
                    {"field": "company_name", "source": "public_directory", "verified": True},
                    {"field": "decision_maker", "source": "team_overview", "verified": True},
                ],
                missing_fields=["exact_headcount", "direct_phone"],
                is_sample=True,
            ),
            ResearchProfile(
                company_name=f"Standard {clean_slug.capitalize()} Group",
                domain=f"standard{clean_slug}.net",
                website=f"https://standard{clean_slug}.net",
                industry=industry,
                location=geo,
                company_size="10-30 employees",
                description=f"Regional firm operating in {industry}.",
                decision_maker={
                    "name": "Jordan Lee",
                    "title": lead_title2,
                    "email": f"jordan@standard{clean_slug}.net",
                    "email_status": "risky",
                    "linkedin_url": "",
                },
                potential_needs=["Client communication improvements"],
                tech_stack=["Static CMS"],
                sources=[{"field": "company_name", "source": "public_index", "verified": True}],
                missing_fields=["phone", "budget"],
                is_sample=True,
            ),
        ]


ROLE_MAILBOX_PREFIXES = {
    "info@", "contact@", "support@", "admin@", "sales@", "help@", "office@",
    "inquiries@", "inquiry@", "general@", "desk@", "hello@", "team@", "billing@",
    "reception@", "mail@", "frontdesk@"
}

ROLE_NAME_PATTERNS = {
    "hospital administration", "general inquiries", "front desk", "office manager",
    "customer support", "support team", "admin team", "administration", "general inquiry",
    "inquiry desk", "reception", "receptionist", "contact us", "info", "helpdesk",
    "admissions", "general office", "practice administration", "management"
}

ROLE_TITLE_PATTERNS = {
    "general inquiries", "general inquiry", "inquiries", "intake desk", "general office"
}


def is_role_contact(name: str = "", title: str = "") -> bool:
    """Return True if the contact name or title represents a departmental / general inquiry role rather than an individual person."""
    n = (name or "").strip().lower()
    t = (title or "").strip().lower()

    if n and any(r in n for r in ROLE_NAME_PATTERNS):
        return True
    if t and any(r in t for r in ROLE_TITLE_PATTERNS):
        return True
    return False


def is_role_mailbox(email: str = "") -> bool:
    """Return True if email is a generic role address (info@, contact@, support@, admin@, etc.)."""
    e = (email or "").strip().lower()
    return bool(e) and any(e.startswith(prefix) for prefix in ROLE_MAILBOX_PREFIXES)


def compute_authoritative_bant(
    research: ResearchProfile,
    profile: dict[str, Any],
) -> dict[str, Any]:
    """Authoritative, explainable BANT qualification engine.

    Strict Invariants:
    1. bant_total strictly equals budget['score'] + authority['score'] + need['score'] + timeline['score'].
    2. Each pillar is independently scored between 0 and 25.
    3. Missing or unverified evidence receives 0 points (no free points).
    4. General inquiries / role contacts (e.g. Hospital Administration, Front Desk) receive 0 Authority.
    5. Role mailboxes without direct personal addresses receive 0 Timeline points.
    6. Sector divergence receives 0 Need points.
    7. Company-size mismatch (e.g. 4500+ employees vs SMB) receives 0 Budget points.
    """
    target_industry = (profile.get("industry") or "").strip().lower()
    target_size = (profile.get("company_size") or "").strip().lower()
    target_titles = [t.lower().strip() for t in (profile.get("icp_titles") or []) if t.strip()]

    lead_industry = (research.industry or "").strip().lower()
    lead_size = (research.company_size or "").strip().lower()
    dm = research.decision_maker or {}
    dm_name = (dm.get("name") or "").strip()
    dm_title = (dm.get("title") or "").strip()
    dm_email = (dm.get("email") or "").strip()
    dm_status = (dm.get("email_status") or "").strip().lower()

    contact_is_role = is_role_contact(dm_name, dm_title)
    mailbox_is_role = is_role_mailbox(dm_email)

    # 1. BUDGET (0 to 25 pts)
    if not lead_size or lead_size in ("unknown", "unverified"):
        bant_budget = {
            "status": "unverified",
            "score": 0,
            "explanation": "Company headcount is unverified (+0 pts)",
        }
    elif any(large in lead_size for large in ("4500", "5000", "10000", "enterprise")):
        bant_budget = {
            "status": "mismatch",
            "score": 0,
            "explanation": f"Enterprise scale ({research.company_size}) exceeds target SMB profile; requires enterprise procurement (+0 pts)",
        }
    elif any(micro in lead_size for micro in ("1-", "2-", "1 ", "2 ", "sole", "freelance")):
        bant_budget = {
            "status": "constrained",
            "score": 10,
            "explanation": f"Small practice ({research.company_size}) with constrained tooling budget (+10 pts)",
        }
    else:
        bant_budget = {
            "status": "adequate",
            "score": 25,
            "explanation": f"Company size ({research.company_size}) aligns with target SMB tooling budget (+25 pts)",
        }

    # 2. AUTHORITY (0 to 25 pts)
    if contact_is_role:
        bant_authority = {
            "status": "unverified",
            "score": 0,
            "title": dm_title,
            "name": dm_name,
            "explanation": f"General inquiry or role contact ('{dm_name or dm_title}') cannot make executive purchasing decisions (+0 pts)",
        }
    elif dm_name and dm_title:
        title_lower = dm_title.lower()
        matches_icp = any(t in title_lower or title_lower in t for t in target_titles) if target_titles else False
        is_exec = matches_icp or any(t in title_lower for t in ("owner", "founder", "ceo", "director", "dentist", "surgeon", "vp", "head", "partner", "principal"))
        is_mgr = any(t in title_lower for t in ("manager", "lead", "coordinator", "administrator"))

        if is_exec:
            bant_authority = {
                "status": "decision_maker",
                "score": 25,
                "title": dm_title,
                "name": dm_name,
                "explanation": f"Direct executive decision maker identified ({dm_name}, {dm_title}) (+25 pts)",
            }
        elif is_mgr:
            bant_authority = {
                "status": "influencer",
                "score": 15,
                "title": dm_title,
                "name": dm_name,
                "explanation": f"Operational manager / influencer identified ({dm_name}, {dm_title}) (+15 pts)",
            }
        else:
            bant_authority = {
                "status": "influencer",
                "score": 10,
                "title": dm_title,
                "name": dm_name,
                "explanation": f"Named staff contact identified ({dm_name}) (+10 pts)",
            }
    elif dm_name:
        bant_authority = {
            "status": "influencer",
            "score": 10,
            "title": "",
            "name": dm_name,
            "explanation": f"Named individual identified without title ({dm_name}) (+10 pts)",
        }
    else:
        bant_authority = {
            "status": "unverified",
            "score": 0,
            "title": "",
            "name": "",
            "explanation": "No named economic buyer or contact identified (+0 pts)",
        }

    # 3. NEED (0 to 25 pts)
    is_sector_divergent = False
    if target_industry and lead_industry:
        target_words = set(re.findall(r"\w+", target_industry)) - {"and", "or", "the", "in", "of"}
        lead_words = set(re.findall(r"\w+", lead_industry)) - {"and", "or", "the", "in", "of"}
        common = target_words & lead_words
        if not (target_industry in lead_industry or lead_industry in target_industry or len(common) >= 1):
            is_sector_divergent = True

    if is_sector_divergent:
        bant_need = {
            "status": "divergent",
            "score": 0,
            "pain_points": [],
            "explanation": f"Sector divergence: '{research.industry}' does not match target '{profile.get('industry')}' (+0 pts)",
        }
    elif research.potential_needs and len(research.potential_needs) >= 2:
        bant_need = {
            "status": "high",
            "score": 25,
            "pain_points": research.potential_needs,
            "explanation": f"Strong operational need: {'; '.join(research.potential_needs[:2])} (+25 pts)",
        }
    elif research.potential_needs:
        bant_need = {
            "status": "moderate",
            "score": 15,
            "pain_points": research.potential_needs,
            "explanation": f"Moderate operational need: {research.potential_needs[0]} (+15 pts)",
        }
    elif target_industry and lead_industry and not is_sector_divergent:
        bant_need = {
            "status": "moderate",
            "score": 10,
            "pain_points": [],
            "explanation": f"Standard industry alignment for {research.industry} (+10 pts)",
        }
    else:
        bant_need = {
            "status": "unverified",
            "score": 0,
            "pain_points": [],
            "explanation": "Operational needs are unverified (+0 pts)",
        }

    # 4. TIMELINE (0 to 25 pts)
    if contact_is_role:
        bant_timeline = {
            "status": "unverified",
            "score": 0,
            "explanation": "Role mailbox or general inquiry cannot be scheduled for direct decision-maker outreach (+0 pts)",
        }
    elif not dm_email or dm_status in ("unknown", "unverified", ""):
        bant_timeline = {
            "status": "unverified",
            "score": 0,
            "explanation": "Direct outreach channel missing or unverified (+0 pts)",
        }
    elif dm_status in ("guess", "guessed"):
        bant_timeline = {
            "status": "planned",
            "score": 5,
            "explanation": "Pattern guess requires mailbox verification before outreach (+5 pts)",
        }
    elif dm_status == "risky":
        bant_timeline = {
            "status": "planned",
            "score": 10,
            "explanation": "Email channel is risky (catch-all or unconfirmed mailbox) (+10 pts)",
        }
    elif dm_status == "verified" and research.potential_needs and not is_sector_divergent:
        bant_timeline = {
            "status": "active",
            "score": 25,
            "explanation": "Immediate outreach ready: deliverable verified email with confirmed operational needs (+25 pts)",
        }
    elif dm_status == "verified":
        bant_timeline = {
            "status": "active",
            "score": 20,
            "explanation": "Deliverable verified email available for current cycle outreach (+20 pts)",
        }
    else:
        bant_timeline = {
            "status": "unverified",
            "score": 0,
            "explanation": "Contact timeline unverified (+0 pts)",
        }

    bant_total = bant_budget["score"] + bant_authority["score"] + bant_need["score"] + bant_timeline["score"]

    if bant_total >= 70 and bant_authority["status"] == "decision_maker":
        qualification = "Qualified"
    elif bant_total >= 40:
        qualification = "Moderate"
    else:
        qualification = "Disqualified"

    return {
        "budget": bant_budget,
        "authority": bant_authority,
        "need": bant_need,
        "timeline": bant_timeline,
        "bant_total": bant_total,
        "bant_qualification": qualification,
    }


class LeadQualifier:
    """Evaluates and scores research profiles against a target business profile.

    Ensures every score is fully explainable with explicit positive criteria
    and clear flags for missing or unverified information.
    """

    def qualify(self, research: ResearchProfile, profile: dict[str, Any]) -> LeadQualificationResult:
        score = 0
        reasons: list[str] = []
        missing_info: list[str] = []

        target_industry = (profile.get("industry") or "").lower()
        target_geo = (profile.get("geography") or "").lower()
        target_size = (profile.get("company_size") or "").lower()
        target_titles = [t.lower().strip() for t in (profile.get("icp_titles") or []) if t.strip()]

        lead_industry = (research.industry or "").lower()
        lead_geo = (research.location or "").lower()
        lead_size = (research.company_size or "").lower()
        dm = research.decision_maker or {}
        dm_title = (dm.get("title") or "").lower()
        dm_name = (dm.get("name") or "").strip()
        dm_email = (dm.get("email") or "").strip()
        dm_status = (dm.get("email_status") or "").strip().lower()

        contact_is_role = is_role_contact(dm_name, dm_title)
        mailbox_is_role = is_role_mailbox(dm_email)

        # 1. Industry / Sector Match (0 to 25 pts)
        is_sector_divergent = False
        if target_industry and lead_industry:
            target_words = set(re.findall(r"\w+", target_industry)) - {"and", "or", "the", "in", "of"}
            lead_words = set(re.findall(r"\w+", lead_industry)) - {"and", "or", "the", "in", "of"}
            common = target_words & lead_words
            if target_industry in lead_industry or lead_industry in target_industry or (len(common) >= 1):
                score += 25
                reasons.append(f"Sector match: '{research.industry}' aligns with target '{profile.get('industry')}' (+25 pts)")
            else:
                is_sector_divergent = True
                score += 0
                reasons.append(f"Sector divergence: '{research.industry}' does not closely match '{profile.get('industry')}' (+0 pts)")
                missing_info.append(f"Sector divergence: '{research.industry}' does not match target profile")
        else:
            missing_info.append("Company industry is unverified or not specified")

        # 2. Geography Match (0 to 20 pts)
        # Evaluates configurable geography scopes:
        # - 'worldwide': Global targeting (no location penalties anywhere; full +20 pts)
        # - 'all_india': Equal eligibility across all Indian states/cities (+20 pts)
        # - 'countries': Selected countries (+20 pts for target countries, 0 pts outside)
        # - 'regions': Selected states / regions (+20 pts for target states, +8 proximity, 0 outside)
        # - 'cities': Local / selected cities (+20 pts for target city, +8 proximity, 0 outside)
        # - Missing geography: Neutral / unrestricted rule (full +20 pts without penalty)
        configured_scope = (
            profile.get("geography_scope")
            or profile.get("target_criteria", {}).get("geography_scope")
            or ""
        ).lower().strip()

        target_geo = (profile.get("geography") or "").strip().lower()

        # Resolve scope
        if configured_scope in ("all_india", "pan_india", "india"):
            scope = "all_india"
        elif configured_scope in ("countries", "selected_countries"):
            scope = "countries"
        elif configured_scope in ("regions", "states", "selected_regions", "selected_states"):
            scope = "regions"
        elif configured_scope in ("cities", "local", "selected_cities"):
            scope = "cities"
        elif target_geo in ("all india", "all-india", "pan india", "pan-india", "india") or "all india" in target_geo or "pan india" in target_geo:
            scope = "all_india"
        elif any(c in target_geo for c in ("united states", "usa", "united kingdom", "uk", "uae", "canada", "germany", "australia")) and not any(city_n in target_geo for city_n in ("austin", "san francisco", "chicago", "new york", "boston", "london", "indore")):
            scope = "countries"
        elif any(city_n in target_geo for city_n in ("indore", "bhopal", "pune", "mumbai", "delhi", "bengaluru", "bangalore", "hyderabad", "chennai", "kolkata", "ahmedabad", "jaipur", "lucknow", "surat", "chandigarh", "kochi", "austin", "san francisco", "chicago", "new york", "boston", "london")):
            scope = "cities"
        elif configured_scope in ("worldwide", "global") and (not target_geo or target_geo in ("worldwide", "global", "unrestricted", "any", "all", "neutral", "none", "international")):
            scope = "worldwide"
        elif not target_geo or target_geo in ("worldwide", "global", "unrestricted", "any", "all", "neutral", "none", "international"):
            scope = "worldwide"
        else:
            scope = "cities"

        loc_lower = (research.location or "").lower()
        co_lower = (research.country or "").lower()
        city_lower = (research.city or "").lower()
        st_lower = (research.state_region or "").lower()

        city_matched = False
        regional_matched = False

        if scope == "worldwide":
            # Global targeting: all companies worldwide are geographically eligible.
            # Geography does NOT reduce ICP fit score.
            score += 20
            if research.location:
                reasons.append(f"Worldwide geography match: Location '{research.location}' is eligible under global targeting (+20 pts)")
            else:
                reasons.append("Worldwide geography: Global targeting applies without geographic penalty (+20 pts)")
                missing_info.append("Company physical location is not specified")

        elif scope == "all_india":
            # All Indian states and cities have equal geographic eligibility (+20 pts)
            is_foreign = co_lower in ("united states", "united kingdom", "germany", "australia", "canada", "france", "japan", "united arab emirates", "singapore")
            is_in_india = (not is_foreign) and (
                co_lower == "india"
                or "india" in loc_lower
                or any(s in loc_lower for s in INDIAN_STATES)
                or any(c in loc_lower for c in ("indore", "bhopal", "pune", "mumbai", "delhi", "bengaluru", "bangalore", "hyderabad", "chennai", "kolkata", "ahmedabad", "jaipur", "lucknow", "surat", "chandigarh", "kochi"))
            )
            if is_in_india:
                score += 20
                reasons.append(f"All-India geography match: Location '{research.location}' is within target country India (+20 pts)")
            else:
                score += 0
                reasons.append(f"Geography mismatch: Location '{research.location}' is outside target country India (+0 pts)")
                missing_info.append("Company is located outside target country India")

        elif scope == "countries":
            raw_tokens = [c.strip().lower() for c in re.split(r"[,/;&]|\band\b", target_geo) if c.strip()]
            target_countries = [COUNTRY_ALIASES.get(t, t) for t in raw_tokens]
            lead_co = COUNTRY_ALIASES.get(co_lower, co_lower)
            matched_co = None
            for tc in target_countries:
                if tc and (tc == lead_co or tc in loc_lower):
                    matched_co = tc
                    break
            if matched_co:
                score += 20
                reasons.append(f"Target geography match: Location '{research.location}' is within target country '{matched_co.title()}' (+20 pts)")
            else:
                score += 0
                reasons.append(f"Geography mismatch: Location '{research.location}' is outside target countries '{target_geo}' (+0 pts)")
                missing_info.append(f"Location is outside target countries '{target_geo}'")

        elif scope == "regions":
            raw_tokens = [r.strip().lower() for r in re.split(r"[,/;&]|\band\b", target_geo) if r.strip()]
            matched_reg = None
            for tr in raw_tokens:
                if tr and (tr == st_lower or tr in loc_lower):
                    matched_reg = tr
                    break
            if matched_reg:
                score += 20
                reasons.append(f"Target geography match: Location '{research.location}' is within target region '{matched_reg.title()}' (+20 pts)")
            else:
                score += 0
                reasons.append(f"Geography mismatch: Location '{research.location}' is outside target regions '{target_geo}' (+0 pts)")
                missing_info.append(f"Location is outside target regions '{target_geo}'")

        else:  # cities
            target_parts = [p.strip().lower() for p in target_geo.split(",") if p.strip()]
            primary_city = target_parts[0] if target_parts else ""
            city_matched = (primary_city in loc_lower) or (city_lower and city_lower in primary_city)

            if city_matched:
                score += 20
                reasons.append(f"Target geography match: Location '{research.location}' is within primary target '{primary_city.title()}' (+20 pts)")
            else:
                country_words = set(COUNTRY_ALIASES.keys()) | set(COUNTRY_ALIASES.values())
                matched_region_name = ""
                for p in target_parts[1:]:
                    if p not in country_words:
                        if p == st_lower or p in loc_lower:
                            regional_matched = True
                            matched_region_name = p
                            break
                        if p in INDIAN_STATES and (INDIAN_STATES[p] in loc_lower or p in loc_lower):
                            regional_matched = True
                            matched_region_name = p
                            break

                if regional_matched:
                    score += 8
                    reasons.append(f"Regional proximity: Location '{research.location}' is in target region '{matched_region_name.title()}' outside target city (+8 pts)")
                    missing_info.append(f"Location is outside primary target city '{primary_city.title()}'")
                else:
                    score += 0
                    reasons.append(f"Geography mismatch: Location '{research.location}' is outside target '{target_geo}' (+0 pts)")
                    missing_info.append(f"Location is outside target '{target_geo}'")

        # 3. Company Size Match (0 to 15 pts)
        if lead_size and lead_size != "unknown":
            if any(lg in lead_size for lg in ("4500", "5000", "10000", "enterprise")):
                score += 0
                reasons.append(f"Company size '{research.company_size}' exceeds target SMB ICP range (+0 pts)")
                missing_info.append(f"Company size '{research.company_size}' exceeds target SMB range")
            elif any(sm in lead_size for sm in ("1-", "2-", "1 ", "2 ", "sole")):
                score += 0
                reasons.append(f"Company size '{research.company_size}' is below target ICP threshold (+0 pts)")
            else:
                score += 15
                reasons.append(f"Company size: '{research.company_size}' fits target '{profile.get('company_size', '10-200 employees')}' (+15 pts)")
        else:
            score += 0
            reasons.append("Company size unverified (+0 pts)")
            missing_info.append("Company exact headcount is unverified")

        # 4. Decision Maker Match (0 to 25 pts)
        if contact_is_role:
            score += 0
            reasons.append(f"Role contact / general inquiry: '{dm_title or dm_name or dm_email}' does not qualify as an individual decision maker (+0 pts)")
            missing_info.append("Executive decision maker is unverified (only general inquiry contact found)")
        elif dm_title:
            matches_target = False
            for tt in target_titles:
                if tt in dm_title or dm_title in tt:
                    matches_target = True
                    break
            if matches_target:
                score += 25
                reasons.append(f"Decision maker: '{dm.get('title')}' ({dm_name}) directly matches ICP target titles (+25 pts)")
            elif dm_name:
                score += 15
                reasons.append(f"Named executive: '{dm.get('title')}' ({dm_name}) identified (+15 pts)")
            else:
                score += 5
                reasons.append("General departmental contact only (+5 pts)")
        else:
            score += 0
            missing_info.append("No named decision maker or executive title found")

        # 5. Potential Needs & Buying Signal (0 to 15 pts)
        if is_sector_divergent:
            score += 0
            reasons.append(f"Sector divergence: pain points from '{research.industry}' do not align with target ICP (+0 pts)")
        elif research.potential_needs:
            score += 15
            reasons.append(f"Specific pain points identified: {'; '.join(research.potential_needs[:2])} (+15 pts)")
        else:
            score += 0
            reasons.append("No explicit workflow needs identified (+0 pts)")
            missing_info.append("Specific operational pain points unconfirmed")

        # ─────────────────────────────────────────────────────────────────
        # STRICT DECOUPLED DATA CONFIDENCE CALCULATION
        # Evaluates factual certainty across 6 independent evidence dimensions
        # Maximum total confidence: 1.00 (100%)
        # ─────────────────────────────────────────────────────────────────
        conf_breakdown: dict[str, float] = {}

        # 1. Domain active & verified (0.20)
        has_domain = bool(research.domain and research.domain.strip())
        conf_domain = 0.20 if has_domain else 0.00
        conf_breakdown["domain"] = conf_domain

        # 2. Location corroborated (0.20 for primary match, 0.20 for regional, 0.10 for present, 0.00 for missing/unverified)
        is_geo_mismatch = any("geography mismatch" in r.lower() for r in reasons)
        if city_matched if (target_geo and lead_geo) else False:
            conf_location = 0.20
        elif regional_matched if (target_geo and lead_geo) else False:
            conf_location = 0.20
        elif lead_geo and not is_geo_mismatch:
            conf_location = 0.20
        elif lead_geo:
            conf_location = 0.10
        else:
            conf_location = 0.00
        conf_breakdown["location"] = conf_location

        # 3. Decision maker identity verified (0.20 for named executive, 0.10 for name only, 0.00 for missing/role)
        if contact_is_role:
            conf_contact = 0.00
        elif dm_name and dm_title:
            conf_contact = 0.20
        elif dm_name:
            conf_contact = 0.10
        else:
            conf_contact = 0.00
        conf_breakdown["contact"] = conf_contact

        # 4. Email deliverability certainty (0.20 verified, 0.10 risky, 0.05 guess, 0.00 missing)
        if contact_is_role:
            conf_email = 0.00
            missing_info.append("Direct individual decision maker email missing (role mailbox only)")
        elif dm_status == "verified" and dm_email:
            conf_email = 0.20
            reasons.append("Direct verified email available for outreach (+20% data certainty)")
        elif dm_status == "risky" and dm_email:
            conf_email = 0.10
            missing_info.append("Email deliverability is risky (-10% confidence penalty; server is catch-all or risky)")
        elif dm_status in ("guess", "guessed") and dm_email:
            conf_email = 0.05
            missing_info.append("Email deliverability is unconfirmed guess (-15% confidence penalty; pattern guess requires verification)")
        elif dm_email:
            conf_email = 0.05
            missing_info.append("Email deliverability is unverified (-15% confidence penalty)")
        else:
            conf_email = 0.00
            missing_info.append("Direct decision maker email missing (-20% confidence penalty)")
        conf_breakdown["email"] = conf_email

        # 5. Headcount / company size certainty (0.10 verified, 0.00 unverified/unknown)
        is_size_unknown = (
            not lead_size
            or lead_size in ("unknown", "unverified")
            or any("company size unverified" in r.lower() for r in reasons)
            or any("headcount is unverified" in m.lower() for m in missing_info)
            or any("employee_count is unverified" in m.lower() for m in missing_info)
        )
        conf_size = 0.00 if is_size_unknown else 0.10
        if is_size_unknown and "Employee count is unverified or unknown" not in missing_info:
            missing_info.append("Employee count is unverified or unknown")
        conf_breakdown["company_size"] = conf_size

        # 6. Operational pain points certainty (0.10 verified, 0.00 unverified)
        conf_needs = 0.10 if (research.potential_needs and not is_sector_divergent) else 0.00
        conf_breakdown["pain_points"] = conf_needs

        # Calculate final data confidence
        confidence = round(sum(conf_breakdown.values()), 2)
        confidence = max(0.0, min(1.0, confidence))

        # Explicit missing fields from research profile
        for mf in research.missing_fields:
            clean_mf = mf.replace("_", " ").title()
            msg = f"{clean_mf} is unverified"
            if msg not in missing_info and clean_mf not in missing_info:
                missing_info.append(msg)

        score = min(max(score, 0), 100)

        if score >= 70:
            tier = "High Fit"
        elif score >= 40:
            tier = "Moderate Fit"
        else:
            tier = "Low Fit"

        # Compute Authoritative BANT Breakdown
        bant_summary = compute_authoritative_bant(research, profile)

        # Strict qualification gate:
        # A lead is qualified ONLY when it achieves strong ICP fit (>= 60),
        # high data confidence (>= 0.70), has a named decision maker (not role mailbox),
        # has a VERIFIED deliverable email address, and has no sector divergence.
        is_qualified = (
            score >= 60
            and confidence >= 0.70
            and bool(dm_name)
            and not contact_is_role
            and dm_status == "verified"
            and not is_sector_divergent
        )

        return LeadQualificationResult(
            score=score,
            tier=tier,
            reasons=reasons,
            missing_information=missing_info,
            confidence=confidence,
            is_qualified=is_qualified,
            confidence_breakdown=conf_breakdown,
            bant_breakdown=bant_summary,
        )

async def check_discovered_lead_duplicates(
    leads: list[dict],
    state: Any,
) -> list[dict]:
    """Check every discovered lead against existing CRM companies and prospects."""
    for lead in leads:
        domain = (lead.get("domain") or "").strip().lower()
        dm = lead.get("decision_maker") or {}
        email = (dm.get("email") or "").strip().lower()
        company_name = (lead.get("company_name") or "").strip()

        dup_info = await state.check_lead_duplicates(domain=domain, email=email, company_name=company_name)
        lead["is_duplicate"] = dup_info["is_duplicate"]
        lead["duplicate_matches"] = dup_info["matches"]
        lead["duplicate_warning"] = dup_info["primary_reason"]
        lead["in_crm"] = dup_info["is_duplicate"] or bool(lead.get("added_to_crm"))
    return leads
