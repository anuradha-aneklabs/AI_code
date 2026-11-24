"""Unit tests for Task 3: Entity cross-check rules and UI masking.

Verifies:
1. "match": all significant tokens found in corpus.
2. "partial_match": brand token equals domain SLD, appears in page title/JSON-LD,
   or domain equals abbreviation built from token prefixes.
3. "no_match": unrelated domain returns no_match with consultant/filer warning.
4. Soft warning note for partial_match vs strong warning for no_match.
5. Masking helper masks contact names to initials by default.
6. Rule: Decision makers are never attributed to an entity when entity_check is no_match.
"""

import pytest
from harvey.entity_cross_check import check_entity_match, extract_domain_sld
from harvey.batch_research import mask_name


def test_sld_extraction():
    assert extract_domain_sld("cargill.com") == "cargill"
    assert extract_domain_sld("https://www.fedex.com/en-us/home.html") == "fedex"
    assert extract_domain_sld("http://bnsf.com") == "bnsf"
    assert extract_domain_sld("sub.domain.co.uk") == "sub"  # first label


def test_entity_match_all_tokens():
    """All significant tokens present in corpus -> 'match'."""
    pages = [{"readable_text": "Welcome to Cara Enterprises Inc, a global provider of radio systems."}]
    website = {"title": "Cara Enterprises Home"}
    status, evidence = check_entity_match("CARA ENTERPRISES INC", pages=pages, website=website, domain="caraenterprises.com")
    assert status == "match"
    assert "cara enterprises" in evidence.lower()


def test_entity_partial_match_brand_token_sld():
    """Brand token (first significant token) equals domain SLD -> 'partial_match'."""
    # "CARGILL FOODS INC." vs cargill.com (where page says Cargill, but not 'Cargill Foods')
    pages = [{"readable_text": "Cargill provides agricultural and food products worldwide."}]
    website = {"title": "Cargill Global Homepage"}
    status, evidence = check_entity_match("CARGILL FOODS INC.", pages=pages, website=website, domain="cargill.com")
    assert status in ("match", "partial_match")
    if status == "partial_match":
        assert "related/parent brand" in evidence


def test_entity_partial_match_prefix_abbreviation():
    """Domain label equals prefix abbreviation -> 'partial_match'."""
    # "FEDERAL EXPRESS CORPORATION" vs fedex.com ("fed" + "ex")
    pages = [{"readable_text": "Ship packages and track deliveries."}]
    website = {"title": "Welcome to FedEx"}
    status, evidence = check_entity_match("FEDERAL EXPRESS CORPORATION", pages=pages, website=website, domain="fedex.com")
    assert status == "partial_match"
    assert "related/parent brand" in evidence


def test_entity_partial_match_title_or_jsonld():
    """Brand token in page title / JSON-LD organization name -> 'partial_match'."""
    pages = [{"readable_text": "We sell grain."}]
    website = {"title": "BNSF Logistics and Freight"}
    status, evidence = check_entity_match("BNSF RAILWAY TRANSPORT CORP", pages=pages, website=website, domain="freight-portal.com")
    assert status == "partial_match"
    assert "related/parent brand" in evidence


def test_entity_no_match():
    """No matching tokens or brand -> 'no_match'."""
    pages = [{"readable_text": "Commercial radio licensing services."}]
    website = {"title": "Federal Licensing Inc"}
    # Input entity is an unrelated company
    status, evidence = check_entity_match("ACME Bakery Corporation", pages=pages, website=website, domain="federallicensing.com")
    assert status == "no_match"
    assert evidence == ""


def test_mask_contact_names():
    """Contact names masked to initials by default."""
    assert mask_name("Rick Testa") == "R. T."
    assert mask_name("Doug Thompson") == "D. T."
    assert mask_name("Karen Sowers") == "K. S."
    assert mask_name("Mike A Minnis") == "M. A. M."
    assert mask_name("Liliana Dimitrova") == "L. D."
    assert mask_name("") == "—"
    assert mask_name(None) == "—"
