import asyncio
import os
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from harvey.collectors.discover import (
    Business,
    DiscoveryQuery,
    OpenStreetMap,
    build_queries,
    geocode_city,
    get_nominatim_user_agent,
    parse_cities,
    run_discovery,
    wait_for_nominatim_rate_limit,
    NOMINATIM_THROTTLE_FILE,
    NOMINATIM_LOCK_FILE,
)
from harvey.state import StateManager
from harvey.config import load_config
from harvey.signals import seed_signal_catalog, SIGNAL_CATALOG


def test_parse_cities_variants():
    """Verify robust geographic parsing across all input formats."""
    # Comma-separated list of cities
    assert parse_cities("Austin, Dallas, Houston") == ["Austin", "Dallas", "Houston"]
    # Semicolon-separated list
    assert parse_cities("Austin; Dallas; Houston") == ["Austin", "Dallas", "Houston"]
    # Semicolon-separated with state
    assert parse_cities("Austin, TX; Dallas, TX; Houston, TX") == [
        "Austin, TX",
        "Dallas, TX",
        "Houston, TX",
    ]
    # Comma-separated with 2-letter state abbreviations
    assert parse_cities("Austin, TX, Dallas, TX, Houston, TX") == [
        "Austin, TX",
        "Dallas, TX",
        "Houston, TX",
    ]
    # Single city with state
    assert parse_cities("Denver, CO") == ["Denver, CO"]
    assert parse_cities("Austin, TX") == ["Austin, TX"]
    # Single international location with country
    assert parse_cities("Indore, India") == ["Indore, India"]
    assert parse_cities("Indore, Madhya Pradesh, India") == ["Indore, Madhya Pradesh, India"]
    # Empty and None
    assert parse_cities("") == []
    assert parse_cities(None) == []
    # List containing multi-city string
    assert parse_cities(["Austin, Dallas, Houston"]) == ["Austin", "Dallas", "Houston"]


def test_build_queries_splits_multi_city_input():
    """build_queries must generate independent queries for each parsed city."""
    base_config = load_config()
    base_config.icp.industries = ["dentist"]
    base_config.icp.geography = ["Austin, Dallas, Houston"]

    # Passed via parameter
    queries = build_queries(base_config, cities=["Austin, Dallas, Houston"])
    locations = [q.location for q in queries]
    assert locations == ["Austin", "Dallas", "Houston"]
    assert len(queries) == 3

    # Inferred from icp.geography
    queries_from_icp = build_queries(base_config)
    locations_icp = [q.location for q in queries_from_icp]
    assert locations_icp == ["Austin", "Dallas", "Houston"]
    assert len(queries_from_icp) == 3


def test_osm_enable_nominatim_fallback_disabled_by_default():
    """Nominatim business/POI discovery fallback is disabled per acceptable use policy."""
    osm = OpenStreetMap()
    assert osm.enable_nominatim_fallback is False


@pytest.mark.asyncio
async def test_osm_fetch_missing_coordinate_returns_empty_without_nominatim():
    """When coordinates are missing, OpenStreetMap returns [] and does NOT query Nominatim."""
    osm = OpenStreetMap()
    mock_client = AsyncMock()

    q = DiscoveryQuery(term="dentist", location="Austin", coordinate="", limit=5)
    results = await osm.fetch(mock_client, {}, q)

    assert results == []
    mock_client.get.assert_not_called()
    mock_client.post.assert_not_called()


@pytest.mark.asyncio
async def test_osm_fetch_unmappable_term_raises_without_nominatim():
    """When a search term has no category tag in TAG_MAP, OpenStreetMap fails closed."""
    osm = OpenStreetMap()
    mock_client = AsyncMock()

    q = DiscoveryQuery(term="completely_unsupported_term_xyz", location="Austin", coordinate="30.26,-97.74,40", limit=5)
    with pytest.raises(RuntimeError, match="OpenStreetMap has no category for"):
        await osm.fetch(mock_client, {}, q)

    mock_client.get.assert_not_called()


@pytest.mark.asyncio
async def test_osm_fetch_overpass_error_fails_closed_without_nominatim():
    """When Overpass mirrors return 502/timeout, OpenStreetMap raises a clear error and does NOT call Nominatim."""
    osm = OpenStreetMap()
    mock_client = AsyncMock()
    mock_post_resp = MagicMock(status_code=502)
    mock_client.post.return_value = mock_post_resp

    with patch("asyncio.sleep", return_value=None):
        q = DiscoveryQuery(
            term="dentist",
            location="Dallas",
            coordinate="32.7767,-96.7970,40",
            limit=5,
        )
        with pytest.raises(RuntimeError, match="Every public Overpass mirror is unavailable or throttling"):
            await osm.fetch(mock_client, {}, q)

    assert mock_client.post.call_count == len(osm.ENDPOINTS)
    mock_client.get.assert_not_called()


@pytest.mark.asyncio
async def test_overpass_success_parses_elements_properly():
    """When Overpass succeeds, elements are parsed into Business records with Overpass provenance."""
    osm = OpenStreetMap()
    mock_client = AsyncMock()
    mock_post_resp = MagicMock(status_code=200)
    mock_post_resp.json.return_value = {
        "elements": [{
            "type": "node",
            "id": 111,
            "tags": {
                "name": "Apex Dental",
                "amenity": "dentist",
                "website": "https://apexdental.com",
                "phone": "+1-303-555-1234",
                "addr:housenumber": "100",
                "addr:street": "Main St",
                "addr:city": "Denver",
                "addr:state": "CO",
            }
        }]
    }
    mock_client.post.return_value = mock_post_resp

    q = DiscoveryQuery(term="dentist", location="Denver", coordinate="39.7392,-104.9903,25", limit=5)
    results = await osm.fetch(mock_client, {}, q)

    assert len(results) == 1
    biz = results[0]
    assert biz.name == "Apex Dental"
    assert biz.domain == "apexdental.com"
    assert biz.website == "https://apexdental.com"
    assert biz.phone == "+1-303-555-1234"
    assert biz.address == "100 Main St"
    assert biz.external_id == "osm:node/111"
    assert biz.source_url == "https://www.openstreetmap.org/node/111"
    assert "Overpass" in biz.provenance
    mock_client.get.assert_not_called()


def test_get_nominatim_user_agent_configured(monkeypatch):
    """When NOMINATIM_CONTACT_EMAIL is configured, it is included in User-Agent."""
    monkeypatch.setenv("NOMINATIM_CONTACT_EMAIL", "lead-dev@company.com")
    ua = get_nominatim_user_agent()
    assert "contact: lead-dev@company.com" in ua
    assert "HarveySalesAgent/1.0" in ua


def test_get_nominatim_user_agent_unconfigured_uses_repo_fallback(monkeypatch):
    """When no email is configured, logs warning and falls back to repository URL without inventing email."""
    monkeypatch.delenv("NOMINATIM_CONTACT_EMAIL", raising=False)
    monkeypatch.delenv("HARVEY_OPERATOR_EMAIL", raising=False)
    monkeypatch.delenv("OPERATOR_EMAIL", raising=False)

    ua = get_nominatim_user_agent()
    assert "HarveySalesAgent/1.0" in ua
    assert "github.com/ethanplusai/harvey" in ua
    assert "@" not in ua


@pytest.mark.asyncio
async def test_wait_for_nominatim_rate_limit(tmp_path, monkeypatch):
    """wait_for_nominatim_rate_limit enforces minimum spacing across sequential calls."""
    throttle_file = tmp_path / ".nominatim_throttle"
    lock_file = tmp_path / ".nominatim_lock"
    monkeypatch.setattr("harvey.collectors.discover.NOMINATIM_THROTTLE_FILE", throttle_file)
    monkeypatch.setattr("harvey.collectors.discover.NOMINATIM_LOCK_FILE", lock_file)

    # First call: immediate (no previous requests)
    delay1 = await wait_for_nominatim_rate_limit(min_interval=0.1)
    assert delay1 == 0.0
    assert throttle_file.exists()

    # Immediate second call: must schedule into future and sleep
    delay2 = await wait_for_nominatim_rate_limit(min_interval=0.1)
    assert delay2 > 0.0


@pytest.mark.asyncio
async def test_geocode_city_persistent_caching(tmp_path):
    """geocode_city checks SQLite cache before making any HTTP call."""
    db_path = str(tmp_path / "test_harvey.db")
    state = StateManager(db_path=db_path)
    await state.init_db()

    # Pre-seed cache
    await state.set_setting("geo:indore", "22.7196,75.8577")

    mock_client = AsyncMock()
    point = await geocode_city(state, "Indore", radius_km=25, client=mock_client)

    # Returns cached coordinates without any network request
    assert point == "22.7196,75.8577,25"
    mock_client.get.assert_not_called()

    # Pre-seed negative cache hit
    await state.set_setting("geo:nonexistent_city", "-")
    neg_point = await geocode_city(state, "nonexistent_city", radius_km=25, client=mock_client)
    assert neg_point == ""
    mock_client.get.assert_not_called()


@pytest.mark.asyncio
async def test_geocode_city_fetches_and_persists_on_cache_miss(tmp_path):
    """On cache miss, geocode_city queries Nominatim with proper UA and caches result."""
    db_path = str(tmp_path / "test_harvey.db")
    state = StateManager(db_path=db_path)
    await state.init_db()

    mock_client = AsyncMock()
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = [{"lat": "28.6139", "lon": "77.2090"}]
    mock_client.get.return_value = mock_resp

    with patch("harvey.collectors.discover.wait_for_nominatim_rate_limit", new_callable=AsyncMock):
        point = await geocode_city(state, "New Delhi", radius_km=40, client=mock_client)

    assert point == "28.6139,77.2090,40"
    mock_client.get.assert_called_once()

    # Verify persisted in SQLite
    cached_val = await state.get_setting("geo:new delhi")
    assert cached_val == "28.6139,77.2090"


@pytest.mark.asyncio
async def test_run_discovery_end_to_end_persists_companies_and_observations(tmp_path):
    """Verify that run_discovery successfully persists discovered companies and observations to SQLite."""
    db_path = str(tmp_path / "test_harvey.db")
    state = StateManager(db_path=db_path)
    await state.init_db()
    await seed_signal_catalog(state)
    for sig in SIGNAL_CATALOG:
        await state.set_signal_status(sig["code"], "confirmed")

    config = load_config()
    config.icp.industries = ["dentist"]
    config.icp.geography = ["Austin, TX"]

    fake_osm_businesses = [
        Business(
            name="Austin Family Dental Care",
            domain="austinfamilydentalcare.com",
            website="https://austinfamilydentalcare.com",
            phone="+1-512-555-1234",
            address="100 Congress Ave, Austin, TX",
            location="Austin, Texas",
            category="dentist",
            has_website=True,
            external_id="osm:node/555111",
            source_url="https://www.openstreetmap.org/node/555111",
            provider="osm",
            provenance="OpenStreetMap Overpass (Free Public Geo Directory)",
        )
    ]

    with patch("harvey.collectors.discover.OpenStreetMap.fetch", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = fake_osm_businesses
        queries = [DiscoveryQuery(term="dentist", location="Austin, TX", coordinate="30.2649,-97.7474,40")]

        report = await run_discovery(
            state=state,
            config=config,
            provider_key="osm",
            queries=queries,
            max_spend=1.0,
            dry_run=False,
        )

    assert report.new_companies == 1
    assert report.errors == []
    assert report.found == 1

    # Verify company record in SQLite
    saved_co = await state.get_company_by_domain("austinfamilydentalcare.com")
    assert saved_co is not None
    assert saved_co.name == "Austin Family Dental Care"
    assert saved_co.location == "Austin, Texas"

    # Verify observations recorded
    co_obs = await state.get_observations(saved_co.id)
    assert len(co_obs) > 0
    obs_codes = {o["signal_code"] for o in co_obs}
    assert "FOUND_IN_SERP" in obs_codes
