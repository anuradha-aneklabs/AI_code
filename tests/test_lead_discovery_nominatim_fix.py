import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from harvey.collectors.discover import (
    Business,
    DiscoveryQuery,
    OpenStreetMap,
    build_queries,
    parse_cities,
    run_discovery,
)
from harvey.state import StateManager
from harvey.config import load_config
from harvey.signals import seed_signal_catalog, SIGNAL_CATALOG


def test_parse_cities_variants():
    """Verify robust geographic parsing across all input formats."""
    # Comma-separated list of cities (the root cause of the bug)
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
    # Mutate icp.geography with multi-city string
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


def test_osm_enable_nominatim_fallback_default():
    """OpenStreetMap must have enable_nominatim_fallback = True by default."""
    osm = OpenStreetMap()
    assert osm.enable_nominatim_fallback is True


@pytest.mark.asyncio
async def test_osm_fetch_missing_coordinate_falls_back_to_nominatim():
    """When coordinates are missing, OpenStreetMap transparently falls back to Nominatim."""
    osm = OpenStreetMap()
    fake_nom_response = [
        {
            "display_name": "Smile 360 Dental, Austin, TX",
            "name": "Smile 360 Dental",
            "osm_type": "node",
            "osm_id": 1234567,
            "address": {"city": "Austin", "state": "Texas"},
            "extratags": {
                "website": "https://smile360dental.com",
                "phone": "+1-512-555-0199",
            },
        }
    ]

    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = fake_nom_response
    mock_client.get.return_value = mock_resp

    q = DiscoveryQuery(term="dentist", location="Austin", coordinate="", limit=5)
    results = await osm.fetch(mock_client, {}, q)

    assert len(results) == 1
    biz = results[0]
    assert biz.name == "Smile 360 Dental"
    assert biz.domain == "smile360dental.com"
    assert biz.website == "https://smile360dental.com"
    assert biz.external_id == "osm:node/1234567"
    assert biz.has_website is True
    assert "Nominatim" in biz.provenance


@pytest.mark.asyncio
async def test_osm_fetch_overpass_error_falls_back_to_nominatim():
    """When Overpass mirrors return 502/timeout, OpenStreetMap falls back to Nominatim."""
    osm = OpenStreetMap()
    fake_nom_response = [
        {
            "display_name": "Luxe Dental, Dallas, TX",
            "name": "Luxe Dental",
            "osm_type": "node",
            "osm_id": 9876543,
            "address": {"city": "Dallas", "state": "Texas"},
            "extratags": {"website": "https://luxedental.com"},
        }
    ]

    mock_client = AsyncMock()
    # Mock overpass POST returning 502 Bad Gateway
    mock_post_resp = MagicMock()
    mock_post_resp.status_code = 502
    mock_client.post.return_value = mock_post_resp

    # Mock nominatim GET returning 200 OK
    mock_get_resp = MagicMock()
    mock_get_resp.status_code = 200
    mock_get_resp.json.return_value = fake_nom_response
    mock_client.get.return_value = mock_get_resp

    with patch("asyncio.sleep", return_value=None):
        q = DiscoveryQuery(
            term="dentist",
            location="Dallas",
            coordinate="32.7767,-96.7970,40",
            limit=5,
        )
        results = await osm.fetch(mock_client, {}, q)

    assert len(results) == 1
    assert results[0].name == "Luxe Dental"
    assert results[0].domain == "luxedental.com"
    assert "Nominatim" in results[0].provenance


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
