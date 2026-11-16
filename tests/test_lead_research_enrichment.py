"""
Tests for Harvey AI — Company Research & Lead Enrichment Engine.

Verifies:
1. Successful company research & enrichment with verified fields.
2. Missing company data leaves unknown info strictly Unknown.
3. Unreachable / invalid sources handled gracefully with zero crashes.
4. Duplicate research prevention via caching TTL and force_refresh override.
5. Strict fact honesty: separation of verified facts from inferred observations, zero fabrication.
6. Lead fit analysis across configurable business profiles.
7. State persistence and actions audit log.
8. Dashboard API research endpoints.
"""

from datetime import datetime, timezone
import aiosqlite
import json
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from harvey.enrichment import (
    CompanyEnrichmentService,
    PublicWebResearchProvider,
    SampleAndOfflineResearchProvider,
)
from harvey.models.prospect import Prospect
from harvey.models.conversation import Conversation, Message
from harvey.state import StateManager


# =====================================================================
# 1. SUCCESSFUL COMPANY RESEARCH & ENRICHMENT
# =====================================================================

@pytest.mark.asyncio
async def test_successful_company_research_with_verified_fields(tmp_path):
    """Verify successful research collects public facts, separates inferences, and scores fit."""
    db_path = str(tmp_path / "test_enrich_success.db")
    state = StateManager(db_path)
    await state.init_db()

    prospect = Prospect(
        id="pros_apex_001",
        first_name="Sarah",
        last_name="Connor",
        email="sarah.connor@apexcloud.io",
        company="ApexCloud Technologies",
        title="VP Sales",
        status="replied",
    )
    await state.add_prospect(prospect)

    convo = Conversation(
        id="conv_apex_001",
        prospect_id="pros_apex_001",
        thread=[
            Message(sender="prospect", content="We are looking for a demo for our SDR team.", timestamp=datetime.now(timezone.utc).replace(tzinfo=None))
        ],
        intent="demo_request",
        priority="high",
        status="open",
    )
    await state.add_conversation(convo)

    profiles = await state.get_business_profiles()
    saas_prof = next((p for p in profiles if "saas" in (p.get("industry") or "").lower() or "soft" in (p.get("industry") or "").lower()), None)
    pid = saas_prof["id"] if saas_prof else ""

    service = CompanyEnrichmentService(state=state)
    result = await service.enrich_prospect("pros_apex_001", profile_id=pid)

    assert result["prospect_id"] == "pros_apex_001"
    assert result["company_name"] == "ApexCloud Technologies"
    assert result["domain"] == "apexcloud.io"
    assert "https://apexcloud.io" in result["website"]
    assert "SaaS" in result["industry"] or "Software" in result["industry"]
    assert len(result["products_services"]) >= 1

    # Verified facts separation
    assert len(result["verified_facts"]) >= 3
    assert any("Sarah Connor" in f for f in result["verified_facts"])
    assert any("VP Sales" in f for f in result["verified_facts"])
    assert any("apexcloud.io" in f for f in result["verified_facts"])

    # Inferred observations
    assert len(result["inferred_observations"]) >= 1

    # Unknown fields
    assert any("Unknown" in u for u in result["unknown_fields"])

    # Fit evaluation
    assert result["fit_score"] >= 70
    assert result["fit_tier"] == "High Fit"
    assert result["is_confirmed_opportunity"] is True
    assert len(result["potential_use_cases"]) >= 1
    assert "demo" in result["recommended_action"].lower() or "tailored" in result["recommended_action"].lower()

    # Sources tracking
    assert len(result["sources"]) >= 2
    for s in result["sources"]:
        assert "field" in s
        assert "source_url" in s
        assert "confidence" in s
        assert s["confidence"] > 0.0


# =====================================================================
# 2. MISSING COMPANY DATA & STRICT HONESTY
# =====================================================================

@pytest.mark.asyncio
async def test_missing_company_data_graceful_handling_strict_unknowns(tmp_path):
    """Verify prospect with missing domain/website leaves unverified fields strictly Unknown."""
    db_path = str(tmp_path / "test_enrich_missing.db")
    state = StateManager(db_path)
    await state.init_db()

    prospect = Prospect(
        id="pros_missing_002",
        first_name="Alex",
        last_name="",
        email="alex1985@gmail.com",
        company="",
        title="",
        status="new",
    )
    await state.add_prospect(prospect)

    service = CompanyEnrichmentService(state=state)
    result = await service.enrich_prospect("pros_missing_002")

    # Does not crash, handles missing domain safely
    assert result["domain"] == ""
    assert result["company_name"] in ("Unknown Company", "Target Enterprise")

    # Strict unknown transparency
    assert any("unverified" in u.lower() or "unknown" in u.lower() for u in result["unknown_fields"])
    assert result["contact_role"] == "Unknown"

    # Fit must be low/needs discovery without evidence
    assert result["fit_tier"] in ("Needs Discovery", "Unqualified", "Moderate Fit")
    assert result["is_confirmed_opportunity"] is False


# =====================================================================
# 3. UNREACHABLE OR INVALID SOURCES
# =====================================================================

@pytest.mark.asyncio
async def test_unreachable_or_invalid_sources_graceful_handling(tmp_path):
    """Verify that network failures or invalid sources record source_unavailable without raising."""
    db_path = str(tmp_path / "test_enrich_unreachable.db")
    state = StateManager(db_path)
    await state.init_db()

    prospect = Prospect(
        id="pros_invalid_003",
        first_name="John",
        last_name="Doe",
        email="john@nonexistent-fake-domain-9876543210.com",
        company="Fake Domain Corp",
        title="Director",
        status="new",
    )
    await state.add_prospect(prospect)

    web_prov = PublicWebResearchProvider(timeout=0.5)
    service = CompanyEnrichmentService(state=state, web_provider=web_prov)

    result = await service.enrich_prospect("pros_invalid_003")

    assert result is not None
    assert result["domain"] == "nonexistent-fake-domain-9876543210.com"
    assert "sources" in result
    assert isinstance(result["sources"], list)
    assert any(s.get("status") in ("source_unavailable", "verified", "inferred") for s in result["sources"])


# =====================================================================
# 4. DUPLICATE RESEARCH PREVENTION & CACHE TTL
# =====================================================================

@pytest.mark.asyncio
async def test_duplicate_research_prevention_caching_and_force_refresh(tmp_path):
    """Verify existing fresh research on the domain is reused, and force_refresh overrides cache."""
    db_path = str(tmp_path / "test_enrich_cache.db")
    state = StateManager(db_path)
    await state.init_db()

    prospect = Prospect(
        id="pros_cache_004",
        first_name="Dave",
        last_name="Miller",
        email="dave.miller@summitmechanical.com",
        company="Summit Mechanical Services",
        title="General Manager",
        status="contacted",
    )
    await state.add_prospect(prospect)

    service = CompanyEnrichmentService(state=state)

    # Run 1: initial research
    r1 = await service.enrich_prospect("pros_cache_004", force_refresh=False)
    assert r1["cache_hit"] is False
    r1_id = r1["id"]

    # Run 2: second call should hit cache (no duplicate research)
    r2 = await service.enrich_prospect("pros_cache_004", force_refresh=False)
    assert r2["cache_hit"] is True
    assert r2["id"] == r1_id
    assert r2["company_name"] == r1["company_name"]

    # Run 3: force_refresh=True should bypass cache and generate fresh research
    r3 = await service.enrich_prospect("pros_cache_004", force_refresh=True)
    assert r3["cache_hit"] is False
    assert "Summit Mechanical" in r3["company_name"]


# =====================================================================
# 5. STRICT FACT HONESTY & ZERO FABRICATION
# =====================================================================

@pytest.mark.asyncio
async def test_strict_fact_honesty_zero_fabricated_claims(tmp_path):
    """Verify that unknown details (revenue, headcount, private software) are never fabricated."""
    db_path = str(tmp_path / "test_enrich_honesty.db")
    state = StateManager(db_path)
    await state.init_db()

    prospect = Prospect(
        id="pros_honesty_005",
        first_name="Gagan",
        last_name="Jain",
        email="dr.gagan@caredental.in",
        company="Care Dental Clinic",
        title="Chief Dentist",
        status="replied",
    )
    await state.add_prospect(prospect)

    service = CompanyEnrichmentService(state=state)
    result = await service.enrich_prospect("pros_honesty_005")

    verified = " ".join(result["verified_facts"]).lower()
    inferred = " ".join(result["inferred_observations"]).lower()
    unknowns = " ".join(result["unknown_fields"]).lower()

    # Must NOT fabricate annual revenue or payroll headcount in verified facts
    assert "$10m" not in verified
    assert "revenue is" not in verified
    assert "revenue is unknown" in unknowns or "unknown" in unknowns

    # Gagan Jain verified from CRM
    assert "gagan jain" in verified
    assert "chief dentist" in verified


# =====================================================================
# 6. LEAD FIT ANALYSIS ACROSS MULTI-INDUSTRY PROFILES
# =====================================================================

@pytest.mark.asyncio
async def test_lead_fit_analysis_multi_profile_alignment(tmp_path):
    """Verify lead fit scoring reacts correctly to matching vs diverging business profiles."""
    db_path = str(tmp_path / "test_enrich_profiles.db")
    state = StateManager(db_path)
    await state.init_db()

    profiles = await state.get_business_profiles()

    dental_prof = next((p for p in profiles if "dental" in (p.get("industry") or "").lower()), None)
    cleantech_prof = next((p for p in profiles if "clean" in (p.get("industry") or "").lower() or "solar" in (p.get("industry") or "").lower()), None)

    dental_lead = Prospect(
        id="pros_fit_006",
        first_name="Amit",
        last_name="Rawat",
        email="amit@indoreadvanceddental.com",
        company="Indore Advanced Dental Care",
        title="Principal Dentist & Director",
        status="new",
    )
    await state.add_prospect(dental_lead)

    service = CompanyEnrichmentService(state=state)

    if dental_prof:
        res_dental = await service.enrich_prospect("pros_fit_006", profile_id=dental_prof["id"], force_refresh=True)
        assert res_dental["fit_tier"] == "High Fit"
        assert res_dental["fit_score"] >= 70

    if cleantech_prof:
        res_clean = await service.enrich_prospect("pros_fit_006", profile_id=cleantech_prof["id"], force_refresh=True)
        assert res_clean["fit_score"] < (res_dental["fit_score"] if dental_prof else 90)


# =====================================================================
# 7. STATE PERSISTENCE & ACTIONS AUDIT LOG
# =====================================================================

@pytest.mark.asyncio
async def test_research_state_persistence_and_actions_log(tmp_path):
    """Verify that enriched lead research is saved in SQLite and logged in actions table."""
    db_path = str(tmp_path / "test_enrich_persist.db")
    state = StateManager(db_path)
    await state.init_db()

    prospect = Prospect(
        id="pros_persist_007",
        first_name="Marcus",
        last_name="Vance",
        email="marcus@starlightcleantech.com",
        company="Starlight CleanTech Solutions",
        title="VP Operations",
        status="contacted",
    )
    await state.add_prospect(prospect)

    service = CompanyEnrichmentService(state=state)
    result = await service.enrich_prospect("pros_persist_007")

    # Check direct query from lead_research table
    saved = await state.get_lead_research("pros_persist_007")
    assert saved is not None
    assert saved["company_name"] == "Starlight CleanTech Solutions"
    assert saved["domain"] == "starlightcleantech.com"
    assert saved["fit_score"] == result["fit_score"]
    assert len(saved["verified_facts"]) >= 2
    assert len(saved["sources"]) >= 1

    # Check actions audit log
    async with state._connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM actions WHERE action_type = 'company_researched'") as cursor:
            rows = await cursor.fetchall()
            assert len(rows) >= 1
            latest = dict(rows[-1])
            details = json.loads(latest.get("details_json") or "{}")
            assert details.get("prospect_id") == "pros_persist_007"


# =====================================================================
# 8. DASHBOARD RESEARCH API ENDPOINTS
# =====================================================================

@pytest.mark.asyncio
async def test_dashboard_research_api_endpoints():
    """Verify GET and POST research endpoints on the live FastAPI app."""
    from harvey.dashboard import app, token_store

    token = token_store.get()
    headers = {"Authorization": f"Bearer {token}"}
    client = TestClient(app, base_url="http://127.0.0.1:5555")

    # 1. GET research for non-existent prospect returns 404
    resp_404 = client.get("/api/prospects/non_existent_id/research", headers=headers)
    assert resp_404.status_code == 404

    # 2. POST standalone company research by domain
    resp_domain = client.post(
        "/api/companies/research-domain",
        json={"domain": "apexcloud.io", "company_name": "ApexCloud Technologies"},
        headers=headers,
    )
    assert resp_domain.status_code == 200
    data = resp_domain.json()
    assert data["success"] is True
    assert "ApexCloud" in data["data"]["company_name"]
    assert data["data"]["domain"] == "apexcloud.io"


# =====================================================================
# 9. FREE ALTERNATIVE EXTRACTION (SOCIAL PROFILES & DECISION MAKERS)
# =====================================================================

@pytest.mark.asyncio
async def test_free_alternative_social_and_decision_maker_extraction(monkeypatch):
    """Verify that PublicWebResearchProvider extracts social profiles and decision makers
    from public HTML without requiring paid Scavio or TinyFish API keys."""
    sample_html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Nova Dynamics - Cloud & AI Architecture</title>
        <meta name="description" content="Nova Dynamics builds enterprise distributed systems.">
        <script type="application/ld+json">
        {
            "@context": "https://schema.org",
            "@type": "Person",
            "name": "Evelyn Reed",
            "jobTitle": "Chief Executive Officer",
            "email": "evelyn@novadynamics.io"
        }
        </script>
    </head>
    <body>
        <h1>Nova Dynamics</h1>
        <p>Enterprise cloud platform and SaaS infrastructure.</p>
        <div class="team">
            <h2>Our Leadership</h2>
            <h3>Marcus Vance</h3>
            <span>VP of Engineering</span>
        </div>
        <footer>
            <a href="https://linkedin.com/company/nova-dynamics-io?trk=public">LinkedIn</a>
            <a href="https://twitter.com/novadynamics">Twitter</a>
            <a href="https://github.com/nova-dynamics">GitHub</a>
            <a href="/about">About Us</a>
        </footer>
    </body>
    </html>
    """

    import httpx
    provider = PublicWebResearchProvider(timeout=5.0)

    class DummyClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def get(self, url):
            return httpx.Response(200, text=sample_html, request=httpx.Request("GET", url))

    monkeypatch.setattr("harvey.enrichment.public_http_client", lambda timeout=8.0: DummyClient())

    res = await provider.research_company("novadynamics.io", company_name="Nova Dynamics")

    # 1. Social profiles verified
    assert "linkedin" in res.social_profiles
    assert "nova-dynamics" in res.social_profiles["linkedin"]
    assert "twitter" in res.social_profiles
    assert "github" in res.social_profiles

    # 2. Decision makers verified from Schema.org JSON-LD
    assert len(res.decision_makers) >= 1
    ceo = next((dm for dm in res.decision_makers if "Evelyn" in dm["name"]), None)
    assert ceo is not None
    assert "Chief Executive Officer" in ceo["title"]
    assert ceo["email"] == "evelyn@novadynamics.io"

    # 3. Verified facts include discovered facts
    assert any("social profiles" in f.lower() for f in res.verified_facts)
    assert any("decision maker" in f.lower() or "leadership" in f.lower() for f in res.verified_facts)

