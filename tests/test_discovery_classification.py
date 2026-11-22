"""Tests for discovery query expansion, result classification, and location verification."""

import json
import os
import re
from pathlib import Path
import pytest
import httpx

from harvey.collectors.discover import (
    DiscoveryQuery,
    Business,
    build_queries,
    classify_search_result,
    root_domain_url,
    run_discovery,
    load_discovery_classification_config,
    load_discovery_queries_config,
)
from harvey.collectors.profile import verify_location_evidence
from harvey.config import load_config
from harvey.paths import PROJECT_ROOT
from harvey.state import StateManager


# ── Fixtures ──────────────────────────────────────────────────────────

JUNK_FIXTURES = [
    {
        "url": "https://www.britannica.com/place/Indore",
        "title": "Indore | History, Population, Map, & Facts | Britannica",
        "expected_class": "article_or_reference",
    },
    {
        "url": "https://indore.nic.in/en/",
        "title": "District Indore, Government of Madhya Pradesh | India",
        "expected_class": "government_or_education",
    },
    {
        "url": "https://www.sikhphilosophy.net/threads/guru-nanaks-travels-to-madhya-pradesh-during-first-travel.54317/",
        "title": "Guru Nanak's travels to Madhya Pradesh | Sikh Philosophy Network",
        "expected_class": "forum_or_community",
    },
    {
        "url": "https://huggingface.co/Cherishh/wav2vec2-slu-1/resolve/refs%2Fpr%2F1/unigrams.txt?download=true",
        "title": "Cherishh/wav2vec2-slu-1 · Hugging Face",
        "expected_class": "file_or_model_hub",
    },
]

GENERIC_COMPANY_FIXTURES = [
    {
        "url": "https://acmecloudapps.com/",
        "title": "Acme Cloud - Enterprise Software & Cloud Platforms",
        "expected_class": "company_homepage",
        "expected_domain": "acmecloudapps.com",
        "expected_root": "https://acmecloudapps.com/",
    },
    {
        "url": "https://devtechcorp.io/about",
        "title": "About Us | DevTech Systems Software Engineering",
        "expected_class": "company_homepage",
        "expected_domain": "devtechcorp.io",
        "expected_root": "https://devtechcorp.io/",
    },
    {
        "url": "https://smartdataworks.org/contact-us",
        "title": "Contact SmartDataWorks Technologies",
        "expected_class": "company_homepage",
        "expected_domain": "smartdataworks.org",
        "expected_root": "https://smartdataworks.org/",
    },
]


# ── Tests ─────────────────────────────────────────────────────────────

def test_classify_junk_fixtures():
    """Assert all four live junk examples are classified as non-company."""
    for item in JUNK_FIXTURES:
        cls, reason = classify_search_result(item["url"], item["title"])
        assert cls == item["expected_class"], (
            f"Expected {item['expected_class']} for {item['url']}, got {cls} (reason: {reason})"
        )
        assert cls != "company_homepage"
        assert len(reason) > 0


def test_classify_company_homepage_fixtures():
    """Assert generic company homepages and top-level pages are classified as company_homepage."""
    for item in GENERIC_COMPANY_FIXTURES:
        cls, reason = classify_search_result(item["url"], item["title"])
        assert cls == item["expected_class"], (
            f"Expected {item['expected_class']} for {item['url']}, got {cls} (reason: {reason})"
        )


def test_root_domain_normalization():
    """Assert deep links are normalized to registrable-domain root URLs."""
    for item in GENERIC_COMPANY_FIXTURES:
        root = root_domain_url(item["url"], item["expected_domain"])
        assert root == item["expected_root"]


def test_config_changes_classification_behavior():
    """Assert that updating discovery_classification config alters classification dynamically."""
    custom_cfg = {
        "reference_hosts": ["custom-reference-encyclopedia.org"],
        "file_extensions": [".custombin"],
    }
    # Before custom config: default is company_homepage
    cls_default, _ = classify_search_result("https://custom-reference-encyclopedia.org/page")
    assert cls_default == "company_homepage"

    # With custom config: classified as article_or_reference
    cls_custom, _ = classify_search_result(
        "https://custom-reference-encyclopedia.org/page",
        config=custom_cfg,
    )
    assert cls_custom == "article_or_reference"

    # Custom file extension
    cls_bin, _ = classify_search_result(
        "https://example.com/download/model.custombin",
        config=custom_cfg,
    )
    assert cls_bin == "file_or_model_hub"


def test_query_expansion_config_driven():
    """Assert build_queries expands templates and synonyms from config, capped at max_queries."""
    config = load_config()
    queries = build_queries(config, cities=["Indore, Madhya Pradesh, India"], industries=["SaaS"], expand_queries=True)
    # Capped at max_queries (4)
    assert 1 <= len(queries) <= 4
    for q in queries:
        kw = q.keyword()
        assert len(kw) > 0
        # Must not duplicate location in the string
        assert "Indore, Madhya Pradesh, India Indore, Madhya Pradesh, India" not in kw


@pytest.mark.asyncio
async def test_discovery_saves_only_company_homepages(tmp_path, monkeypatch):
    """Assert junk results are NOT saved in companies table, while company_homepage results are."""
    db_path = tmp_path / "disc_test.db"
    monkeypatch.setenv("HARVEY_DB_PATH", str(db_path))

    state = StateManager(str(db_path))
    await state.init_db()

    # Seed catalog and confirm free signals so discovery can run
    from harvey.signals import seed_signal_catalog
    await seed_signal_catalog(state)
    await state.set_signal_status("FOUND_IN_SERP", "confirmed")

    config = load_config()

    # Combine 4 junk fixtures + 3 company fixtures
    all_mock_businesses = []
    for f in JUNK_FIXTURES:
        all_mock_businesses.append(
            Business(
                name=f["title"][:30],
                raw_title=f["title"],
                domain=f["url"].split("/")[2].replace("www.", ""),
                website=f["url"],
                source_url=f["url"],
                location="Indore, Madhya Pradesh, India",
                provider="tinyfish",
            )
        )
    for f in GENERIC_COMPANY_FIXTURES:
        all_mock_businesses.append(
            Business(
                name=f["title"][:30],
                raw_title=f["title"],
                domain=f["expected_domain"],
                website=f["url"],
                source_url=f["url"],
                location="Indore, Madhya Pradesh, India",
                provider="tinyfish",
            )
        )

    # Mock provider fetch
    import harvey.collectors.discover as disc_mod

    class MockProvider:
        key = "tinyfish"
        label = "TinyFish"
        kind = "serp"
        needs_coordinates = False
        rank_codes = ()
        stop_reason = ""
        last_error = ""
        last_cost = 0.0
        complete = True
        remaining_budget = 1.0

        def estimate(self, queries):
            return 0.0

        def missing_keys(self, env):
            return ()

        async def fetch(self, client, env, query):
            return all_mock_businesses

    monkeypatch.setitem(disc_mod.PROVIDERS, "tinyfish", MockProvider())

    query = DiscoveryQuery(
        term="SaaS",
        location="Indore, Madhya Pradesh, India",
        full_query="SaaS company in Indore, Madhya Pradesh, India",
    )

    report = await run_discovery(state, config, provider_key="tinyfish", queries=[query])

    # Assert 3 new companies saved (the 3 company_homepages), 4 filtered out
    assert report.new_companies == 3

    # Check companies table: only the 3 company homepages must exist
    import aiosqlite
    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT domain, website, source_url FROM companies") as cur:
            companies = await cur.fetchall()

        saved_domains = {c["domain"] for c in companies}
        assert "britannica.com" not in saved_domains
        assert "indore.nic.in" not in saved_domains
        assert "sikhphilosophy.net" not in saved_domains
        assert "huggingface.co" not in saved_domains

        assert "acmecloudapps.com" in saved_domains
        assert "devtechcorp.io" in saved_domains
        assert "smartdataworks.org" in saved_domains

        # For devtechcorp.io, assert website is root while source_url is deep
        for c in companies:
            if c["domain"] == "devtechcorp.io":
                assert c["website"] == "https://devtechcorp.io/"
                assert c["source_url"] == "https://devtechcorp.io/about"

        # Check discovery_results table: ALL 7 results must be recorded (delete nothing)
        async with db.execute("SELECT result_class, count(*) FROM discovery_results GROUP BY result_class") as cur:
            class_counts = dict(await cur.fetchall())

        assert class_counts.get("article_or_reference", 0) >= 1
        assert class_counts.get("government_or_education", 0) >= 1
        assert class_counts.get("forum_or_community", 0) >= 1
        assert class_counts.get("file_or_model_hub", 0) >= 1
        assert class_counts.get("company_homepage", 0) == 3


def test_location_verification_evidence():
    """Assert location verification checks city token in plausible context."""
    # Plausible address context
    html_with_address = """
    <html>
      <footer>
        <div class="office-location">
          <h3>Registered Office</h3>
          <p>Building 4B, Crystal IT Park, Ring Road, Indore, Madhya Pradesh 452001, India</p>
          <p>Contact: +91 731 2970000 | info@example.com</p>
        </div>
      </footer>
    </html>
    """
    status, snippet, url = verify_location_evidence(
        html_with_address, "Indore, Madhya Pradesh, India", "https://example.com"
    )
    assert status == "location_verified"
    assert "Indore" in snippet
    assert "Crystal IT Park" in snippet or "Office" in snippet
    assert url == "https://example.com"

    # No city mention
    html_no_city = "<html><p>We are a global SaaS company building products in Bangalore.</p></html>"
    status, snippet, url = verify_location_evidence(
        html_no_city, "Indore, Madhya Pradesh, India", "https://example.com"
    )
    assert status == "location_unverified"
    assert snippet == ""

    # Casual mention without location context
    html_casual = "<html><p>Our founder once visited the historic palace in Indore on vacation.</p></html>"
    status, snippet, url = verify_location_evidence(
        html_casual, "Indore, Madhya Pradesh, India", "https://example.com"
    )
    assert status == "location_unverified"


def test_ac9_no_hardcoded_company_names_in_source():
    """Assert that no forbidden hardcoded company names exist in harvey/ source code."""
    harvey_dir = PROJECT_ROOT / "harvey"
    forbidden_pattern = re.compile(r"\b(aneka|anika|anekalabs|anikalabs)\b", re.IGNORECASE)

    violations = []
    for py_file in harvey_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8", errors="ignore")
        for line_num, line in enumerate(content.splitlines(), start=1):
            if forbidden_pattern.search(line):
                violations.append(f"{py_file.name}:{line_num} -> {line.strip()}")

    assert len(violations) == 0, f"Found hardcoded company names:\n" + "\n".join(violations)
