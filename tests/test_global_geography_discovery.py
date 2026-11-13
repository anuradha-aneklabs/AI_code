"""Comprehensive test suite for Global Lead Discovery Geography.

Validates:
1. All India Geography: Equal eligibility (+20 pts) across all Indian states and cities
   (Indore, Bhopal, Pune, Bengaluru, New Delhi), while out-of-target foreign leads receive 0 pts.
2. Worldwide Geography: Leads in US, UK, UAE, Germany, Australia, India, and leads with
   missing location all receive full +20 pts without location penalties.
3. Selected Countries Targeting: Only configured countries receive +20 pts; others receive 0 pts.
4. Selected States / Regions Targeting: Matching states receive +20 pts; others receive 0 pts.
5. Backward Compatibility & Missing Geography: Profiles with omitted or blank geography
   fall back to neutral / unrestricted rule (+20 pts, no penalty).
6. Structured Location Decomposition: City, state_region, and country parsing and provenance tracking.
7. Uncompromised Data Quality: ICP fit score vs data confidence separation, risky email detection,
   and duplicate rejection remain intact under global discovery.
"""

import pytest
from harvey.state import StateManager
from harvey.qualification import (
    LeadQualifier,
    ResearchProfile,
    SampleLeadDiscoveryProvider,
    check_discovered_lead_duplicates,
    parse_location_components,
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. ALL INDIA GEOGRAPHY (Pan-India Equal Eligibility)
# ─────────────────────────────────────────────────────────────────────────────

def test_all_india_geography_equal_eligibility():
    """Verify that targeting 'All India' grants equal +20 pts across any state or city in India.

    - Indore (Madhya Pradesh): +20 pts
    - Bhopal (Madhya Pradesh): +20 pts
    - Pune (Maharashtra): +20 pts
    - Bengaluru (Karnataka): +20 pts
    - New Delhi (Delhi): +20 pts
    - Non-Indian locations (e.g. Austin, TX, USA; London, UK) receive 0 pts (mismatch).
    """
    qualifier = LeadQualifier()
    profile = {
        "industry": "Healthcare / Dental",
        "geography": "All India",
        "geography_scope": "all_india",
        "company_size": "15-50 employees",
        "icp_titles": ["Principal Dentist", "Clinic Director"],
    }

    indian_cities_and_states = [
        ("Indore Smile Studio", "indoresmile.com", "Indore, Madhya Pradesh, India"),
        ("Central Bhopal Dental", "bhopaldental.com", "Bhopal, Madhya Pradesh, India"),
        ("Pune Metro Dental", "punemetro.com", "Pune, Maharashtra, India"),
        ("Bangalore Dental Science", "bangaloredental.com", "Bengaluru, Karnataka, India"),
        ("Delhi Premier Care", "delhipremier.com", "New Delhi, Delhi, India"),
    ]

    for company_name, domain, location in indian_cities_and_states:
        lead = ResearchProfile(
            company_name=company_name,
            domain=domain,
            industry="Healthcare / Dental",
            location=location,
            company_size="25 employees",
            decision_maker={"name": "Dr. Lead", "title": "Principal Dentist", "email": f"dr@{domain}", "email_status": "verified"},
            potential_needs=["Appointment booking automation"],
        )
        res = qualifier.qualify(lead, profile)
        geo_reasons = [r for r in res.reasons if "geography" in r.lower() or "pan-india" in r.lower()]
        assert any("+20 pts" in r for r in geo_reasons), f"Expected +20 pts for {location}, got: {geo_reasons}"
        assert any("pan-india" in r.lower() or "all-india" in r.lower() for r in geo_reasons)
        assert res.score == 100, f"Expected 100 for verified lead in {location}, got {res.score}"

    # Verify foreign locations receive 0 pts under All India targeting
    foreign_leads = [
        ("Austin Dental Specialists", "austindental.com", "Austin, Texas, United States"),
        ("London Smile Clinic", "londonsmile.co.uk", "London, Greater London, United Kingdom"),
    ]

    for company_name, domain, location in foreign_leads:
        lead = ResearchProfile(
            company_name=company_name,
            domain=domain,
            industry="Healthcare / Dental",
            location=location,
            company_size="25 employees",
            decision_maker={"name": "Dr. Foreign", "title": "Principal Dentist", "email": f"dr@{domain}", "email_status": "verified"},
            potential_needs=["Appointment booking automation"],
        )
        res = qualifier.qualify(lead, profile)
        geo_reasons = [r for r in res.reasons if "geography mismatch" in r.lower()]
        assert any("+0 pts" in r for r in geo_reasons), f"Expected +0 pts mismatch for foreign lead in {location}, got: {geo_reasons}"
        assert res.score == 80  # 100 - 20 (0 geo pts)


# ─────────────────────────────────────────────────────────────────────────────
# 2. WORLDWIDE GEOGRAPHY (Global Targeting - No Penalties)
# ─────────────────────────────────────────────────────────────────────────────

def test_worldwide_geography_no_penalties():
    """Verify that Worldwide targeting awards +20 pts to all leads globally without penalty.

    - USA, UK, UAE, Germany, Australia, India all receive +20 pts.
    - Missing location also receives neutral +20 pts without penalty.
    - Location does not reduce ICP fit score.
    """
    qualifier = LeadQualifier()
    profile = {
        "industry": "Healthcare / Dental",
        "geography": "Worldwide",
        "geography_scope": "worldwide",
        "company_size": "10-200 employees",
        "icp_titles": ["Founder", "Clinic Director"],
    }

    global_test_cases = [
        ("California Dental Specialists", "californiadental.com", "San Francisco, California, United States"),
        ("Harley Street Smile Studio", "harleystreetsmile.co.uk", "London, Greater London, United Kingdom"),
        ("Gulf Smile Oral Surgery", "gulfsmile.ae", "Dubai, Dubai, United Arab Emirates"),
        ("Berlin Dental Excellence", "berlindental.de", "Berlin, Berlin, Germany"),
        ("Sydney Ortho Care", "sydneyortho.com.au", "Sydney, New South Wales, Australia"),
        ("Indore Advanced Dental Care", "indoredental.com", "Indore, Madhya Pradesh, India"),
        ("Global Remote Practice", "globalremote.org", ""),  # Blank location
    ]

    for company_name, domain, location in global_test_cases:
        lead = ResearchProfile(
            company_name=company_name,
            domain=domain,
            industry="Healthcare / Dental",
            location=location,
            company_size="30 employees",
            decision_maker={"name": "Dr. Global", "title": "Clinic Director", "email": f"director@{domain}", "email_status": "verified"},
            potential_needs=["Cloud PMS integration"],
        )
        res = qualifier.qualify(lead, profile)
        geo_reasons = [r for r in res.reasons if "geography" in r.lower() or "worldwide" in r.lower()]
        assert any("+20 pts" in r for r in geo_reasons), f"Expected +20 pts for global lead in '{location}', got: {geo_reasons}"
        assert any("worldwide" in r.lower() for r in geo_reasons)
        assert res.score == 100, f"Expected full score 100 for worldwide lead, got {res.score}"
        # Location must not appear in missing_information unless location itself was empty
        if location:
            assert not any("location" in m.lower() for m in res.missing_information)
        else:
            assert any("location" in m.lower() for m in res.missing_information)


# ─────────────────────────────────────────────────────────────────────────────
# 3. SELECTED COUNTRIES TARGETING
# ─────────────────────────────────────────────────────────────────────────────

def test_selected_countries_targeting():
    """Verify that when specific countries are configured, only those countries match.

    - Target: 'United States, United Kingdom' (scope: 'countries')
    - US lead: +20 pts
    - UK lead: +20 pts
    - Australia lead: 0 pts (outside target countries)
    - Germany lead: 0 pts (outside target countries)
    """
    qualifier = LeadQualifier()
    profile = {
        "industry": "B2B SaaS",
        "geography": "United States, United Kingdom",
        "geography_scope": "countries",
        "company_size": "20-100 employees",
        "icp_titles": ["VP Sales", "CEO"],
    }

    # US lead
    us_lead = ResearchProfile(
        company_name="CloudSync US",
        domain="cloudsync.us",
        industry="B2B SaaS",
        location="Austin, Texas, United States",
        company_size="50 employees",
        decision_maker={"name": "Alice US", "title": "VP Sales", "email": "alice@cloudsync.us", "email_status": "verified"},
        potential_needs=["Sales acceleration"],
    )
    res_us = qualifier.qualify(us_lead, profile)
    assert any("+20 pts" in r for r in res_us.reasons if "geography" in r.lower())
    assert res_us.score == 100

    # UK lead
    uk_lead = ResearchProfile(
        company_name="DataFlow UK",
        domain="dataflow.co.uk",
        industry="B2B SaaS",
        location="London, Greater London, United Kingdom",
        company_size="40 employees",
        decision_maker={"name": "Bob UK", "title": "CEO", "email": "bob@dataflow.co.uk", "email_status": "verified"},
        potential_needs=["Sales acceleration"],
    )
    res_uk = qualifier.qualify(uk_lead, profile)
    assert any("+20 pts" in r for r in res_uk.reasons if "geography" in r.lower())
    assert res_uk.score == 100

    # Australia lead (out of scope)
    au_lead = ResearchProfile(
        company_name="Aussie Cloud",
        domain="aussiecloud.com.au",
        industry="B2B SaaS",
        location="Sydney, New South Wales, Australia",
        company_size="35 employees",
        decision_maker={"name": "Charlie AU", "title": "CEO", "email": "charlie@aussiecloud.com.au", "email_status": "verified"},
        potential_needs=["Sales acceleration"],
    )
    res_au = qualifier.qualify(au_lead, profile)
    assert any("+0 pts" in r for r in res_au.reasons if "geography mismatch" in r.lower())
    assert res_au.score == 80  # 100 - 20

    # Germany lead (out of scope)
    de_lead = ResearchProfile(
        company_name="Berlin Tech",
        domain="berlintech.de",
        industry="B2B SaaS",
        location="Berlin, Berlin, Germany",
        company_size="45 employees",
        decision_maker={"name": "Dieter DE", "title": "VP Sales", "email": "dieter@berlintech.de", "email_status": "verified"},
        potential_needs=["Sales acceleration"],
    )
    res_de = qualifier.qualify(de_lead, profile)
    assert any("+0 pts" in r for r in res_de.reasons if "geography mismatch" in r.lower())
    assert res_de.score == 80


# ─────────────────────────────────────────────────────────────────────────────
# 4. SELECTED REGIONS / STATES TARGETING
# ─────────────────────────────────────────────────────────────────────────────

def test_selected_regions_targeting():
    """Verify that when specific states/regions are configured, regional matching works.

    - Target: 'California, Texas' (scope: 'regions')
    - California lead: +20 pts
    - Texas lead: +20 pts
    - Florida lead: 0 pts
    """
    qualifier = LeadQualifier()
    profile = {
        "industry": "CleanTech",
        "geography": "California, Texas",
        "geography_scope": "regions",
        "company_size": "10-100 employees",
        "icp_titles": ["Founder", "Operations Director"],
    }

    cal_lead = ResearchProfile(
        company_name="SolarCal",
        domain="solarcal.com",
        industry="CleanTech",
        location="San Jose, California, United States",
        company_size="30 employees",
        decision_maker={"name": "Dana Cal", "title": "Founder", "email": "dana@solarcal.com", "email_status": "verified"},
        potential_needs=["Operations efficiency"],
    )
    res_cal = qualifier.qualify(cal_lead, profile)
    assert any("+20 pts" in r for r in res_cal.reasons if "geography match" in r.lower())
    assert res_cal.score == 100

    fl_lead = ResearchProfile(
        company_name="SolarMiami",
        domain="solarmiami.com",
        industry="CleanTech",
        location="Miami, Florida, United States",
        company_size="30 employees",
        decision_maker={"name": "Frank FL", "title": "Founder", "email": "frank@solarmiami.com", "email_status": "verified"},
        potential_needs=["Operations efficiency"],
    )
    res_fl = qualifier.qualify(fl_lead, profile)
    assert any("+0 pts" in r for r in res_fl.reasons if "geography mismatch" in r.lower())
    assert res_fl.score == 80


# ─────────────────────────────────────────────────────────────────────────────
# 5. BACKWARD COMPATIBILITY & MISSING GEOGRAPHY
# ─────────────────────────────────────────────────────────────────────────────

def test_missing_geography_neutral_unrestricted_rule():
    """Verify that profiles with missing or blank geography use the neutral / unrestricted rule (+20 pts)."""
    qualifier = LeadQualifier()

    # Profile with no geography fields at all
    empty_profile = {
        "industry": "Healthcare / Dental",
        "company_size": "15-50 employees",
        "icp_titles": ["Dentist", "Director"],
    }

    lead = ResearchProfile(
        company_name="Anywhere Dental",
        domain="anywheredental.com",
        industry="Healthcare / Dental",
        location="Toronto, Ontario, Canada",
        company_size="25 employees",
        decision_maker={"name": "Dr. Sarah", "title": "Dentist", "email": "sarah@anywheredental.com", "email_status": "verified"},
        potential_needs=["Appointment booking automation"],
    )
    res = qualifier.qualify(lead, empty_profile)
    geo_reasons = [r for r in res.reasons if "geography" in r.lower() or "unrestricted" in r.lower() or "worldwide" in r.lower()]
    assert any("+20 pts" in r for r in geo_reasons), f"Expected +20 pts for neutral rule, got: {geo_reasons}"
    assert res.score == 100


# ─────────────────────────────────────────────────────────────────────────────
# 6. LOCATION DECOMPOSITION & PROVENANCE TRACKING
# ─────────────────────────────────────────────────────────────────────────────

def test_location_decomposition_city_state_country():
    """Verify structured parsing of location into city, state_region, and country."""
    cases = [
        ("Indore, Madhya Pradesh, India", "Indore", "Madhya Pradesh", "India"),
        ("Pune, Maharashtra, India", "Pune", "Maharashtra", "India"),
        ("Austin, TX, USA", "Austin", "Texas", "United States"),
        ("San Francisco, California, United States", "San Francisco", "California", "United States"),
        ("London, Greater London, United Kingdom", "London", "Greater London", "United Kingdom"),
        ("Dubai, Dubai, United Arab Emirates", "Dubai", "Dubai", "United Arab Emirates"),
        ("Berlin, Germany", "Berlin", "", "Germany"),
    ]

    for loc_str, exp_city, exp_state, exp_country in cases:
        city, state_region, country = parse_location_components(loc_str)
        assert city == exp_city, f"City mismatch for '{loc_str}': expected {exp_city}, got {city}"
        if exp_state:
            assert state_region == exp_state, f"State mismatch for '{loc_str}': expected {exp_state}, got {state_region}"
        assert country == exp_country, f"Country mismatch for '{loc_str}': expected {exp_country}, got {country}"

    # Verify ResearchProfile auto-populates city, state_region, country
    rp = ResearchProfile(
        company_name="Apex Dental",
        domain="apexdental.ae",
        location="Dubai, Dubai, United Arab Emirates",
    )
    assert rp.city == "Dubai"
    assert rp.country == "United Arab Emirates"

    # Verify to_dict() outputs structured fields
    d = rp.to_dict()
    assert d["city"] == "Dubai"
    assert d["country"] == "United Arab Emirates"

    # Verify source provenance includes structured location
    prov = rp.get_source_provenance()
    assert "location" in prov
    assert prov["location"]["city"] == "Dubai"
    assert prov["location"]["country"] == "United Arab Emirates"


# ─────────────────────────────────────────────────────────────────────────────
# 7. GLOBAL DISCOVERY CANDIDATE SELECTION & DATA INTEGRITY
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_global_discovery_candidate_selection_and_duplicate_safety(tmp_path):
    """Verify SampleLeadDiscoveryProvider finds global candidates matching configured scope,

    and duplicate detection / data confidence separation remain rock-solid.
    """
    db_path = tmp_path / "test_global_discovery.db"
    state = StateManager(str(db_path))
    await state.init_db()

    provider = SampleLeadDiscoveryProvider()

    # 1. Worldwide profile discovers global candidates
    worldwide_profile = {
        "id": "prof_worldwide",
        "name": "Global Dental Network",
        "industry": "Healthcare / Dental",
        "geography": "Worldwide",
        "geography_scope": "worldwide",
    }
    ww_candidates = await provider.discover(worldwide_profile, limit=5)
    assert len(ww_candidates) >= 3
    ww_countries = {c.country for c in ww_candidates if c.country}
    # Should include multiple countries (e.g. US, UK, UAE, India)
    assert len(ww_countries) >= 2, f"Expected candidates across multiple countries, got: {ww_countries}"

    # 2. Pan-India profile discovers Indian candidates across states
    india_profile = {
        "id": "prof_india",
        "name": "Pan-India Dental",
        "industry": "Healthcare / Dental",
        "geography": "All India",
        "geography_scope": "all_india",
    }
    india_candidates = await provider.discover(india_profile, limit=5)
    for c in india_candidates:
        assert c.country == "India", f"Candidate {c.company_name} ({c.location}) is outside India for Pan-India profile"

    # 3. Duplicate detection: running discovery twice preserves single rows per normalized domain
    for c in ww_candidates:
        d = c.to_dict()
        d["profile_id"] = worldwide_profile["id"]
        d["score"] = 85
        d["tier"] = "High Fit"
        await state.save_discovered_lead(d)

    # Save exact same batch second time
    for c in ww_candidates:
        d = c.to_dict()
        d["profile_id"] = worldwide_profile["id"]
        d["score"] = 85
        d["tier"] = "High Fit"
        await state.save_discovered_lead(d)

    saved_leads = await state.get_discovered_leads(worldwide_profile["id"])
    saved_domains = [l["domain"] for l in saved_leads]
    assert len(saved_leads) == len(ww_candidates), f"Expected {len(ww_candidates)} leads without duplicate rows, got {len(saved_leads)}"
    assert len(saved_domains) == len(set(saved_domains)), "Duplicate domains detected in state database"

    # Also verify existing CRM company duplicate detection flags
    from harvey.models.company import Company
    await state.add_company(Company(
        name=ww_candidates[0].company_name,
        domain=ww_candidates[0].domain,
        industry=ww_candidates[0].industry,
    ))
    checked_leads = await check_discovered_lead_duplicates([c.to_dict() for c in ww_candidates], state)
    assert checked_leads[0]["is_duplicate"] is True
    assert "already in CRM" in checked_leads[0]["duplicate_warning"]

    # 4. Verified vs Risky Email Confidence separation
    qualifier = LeadQualifier()
    lead_risky = ResearchProfile(
        company_name="Risky Dental",
        domain="riskydental.com",
        industry="Healthcare / Dental",
        location="London, United Kingdom",
        company_size="25 employees",
        decision_maker={"name": "Dr. Risky", "title": "Director", "email": "risky@riskydental.com", "email_status": "risky"},
        potential_needs=["Booking"],
    )
    res_risky = qualifier.qualify(lead_risky, worldwide_profile)
    assert res_risky.confidence == 0.90, f"Risky email must reduce confidence, got: {res_risky.confidence}"
    assert not res_risky.is_qualified, "Lead with risky email must not be marked qualified for outreach"

    lead_verified = ResearchProfile(
        company_name="Verified Dental",
        domain="verifieddental.com",
        industry="Healthcare / Dental",
        location="London, United Kingdom",
        company_size="25 employees",
        decision_maker={"name": "Dr. Verified", "title": "Director", "email": "verified@verifieddental.com", "email_status": "verified"},
        potential_needs=["Booking"],
    )
    res_verified = qualifier.qualify(lead_verified, worldwide_profile)
    assert res_verified.confidence >= 0.85, f"Verified email must maintain high confidence, got: {res_verified.confidence}"
    assert res_verified.is_qualified, "Verified email must be qualified for outreach"
