"""Tests for decision maker name validation, generic non-name exclusion, and context-based officer justification."""

import pytest
from harvey.decision_makers import (
    DecisionMakerCandidate,
    DecisionMakerRolesConfig,
    _is_valid_person_name,
    check_officer_context,
    load_roles_config,
)


NON_PERSON_FIXTURES = [
    "Authorized Signatory",
    "Sales Representative",
    "Support Team",
    "Customer Support Team",
    "Admin User",
    "Legal Department",
    "Contact Person",
    "Not Everyone Can Build Yours.",
    "Anyone Can Build Software",
    "Engineering Team",
    "Marketing Department",
    "Customer Success Manager",
    "Board Member",
    "Executive Staff",
]

GENUINE_NAME_FIXTURES = [
    "Mayank Pratap Singh",
    "Aanchal Chaurasia",
    "Peter Williams",
    "John Smith",
    "Jane Doe",
    "Richard Hall",
    "Dr. Ritesh Malik",
    "Nikhil Sharma",
]


def test_generic_and_legal_non_names_rejected():
    """Assert role, legal, and generic phrases are rejected as person names."""
    cfg = load_roles_config()
    for text in NON_PERSON_FIXTURES:
        assert not _is_valid_person_name(text, cfg), f"Expected '{text}' to be rejected as a person name"


def test_genuine_person_names_accepted():
    """Assert genuine person names are accepted."""
    cfg = load_roles_config()
    for text in GENUINE_NAME_FIXTURES:
        assert _is_valid_person_name(text, cfg), f"Expected '{text}' to be accepted as a person name"


def test_client_testimonial_context_flagged_unverified():
    """Assert candidates in client testimonial sections are flagged unverified without dropping."""
    cfg = load_roles_config()
    cand = DecisionMakerCandidate(
        full_name="Peter Williams",
        title="CEO & Co-founder",
        role_category="founder",
        source_url="https://example.com/",
        source_page_id="page_1",
        evidence_text="Peter Williams CEO & Co-founder",
        confidence=0.9,
        verification_status="verified",
        extraction_method="card",
    )
    page_text = 'Client Stories "Apoliums is the best software company we have worked with. Peter Williams CEO & Co-founder"'
    status, reason = check_officer_context(cand, "", page_text, "apoliums.com", cfg)
    assert status == "unverified"
    assert "testimonial" in reason.lower() or "client" in reason.lower()


def test_external_social_proof_advisor_context_flagged_unverified():
    """Assert candidates in 'trusted by' or advisor/investor sections are flagged unverified without dropping."""
    cfg = load_roles_config()
    cand = DecisionMakerCandidate(
        full_name="Vijay Shekhar Sharma",
        title="Founder & CEO",
        role_category="founder",
        source_url="https://example.com/",
        source_page_id="page_1",
        evidence_text="Vijay Shekhar Sharma Founder & CEO",
        confidence=0.9,
        verification_status="verified",
        extraction_method="card",
    )
    page_text = "They get a partner who has been trusted by the people who know what great looks like. Vijay Shekhar Sharma Founder & CEO"
    status, reason = check_officer_context(cand, "", page_text, "engineerbabu.com", cfg)
    assert status == "unverified"
    assert "advisor" in reason.lower() or "investor" in reason.lower() or "endorsement" in reason.lower()


def test_other_org_in_title_flagged_unverified():
    """Assert candidates whose title explicitly names another company are flagged unverified."""
    cfg = load_roles_config()
    cand = DecisionMakerCandidate(
        full_name="Nimesh Mathur",
        title="CHRO at Curefit",
        role_category="cxo",
        source_url="https://example.com/",
        source_page_id="page_1",
        evidence_text="Nimesh Mathur CHRO at Curefit",
        confidence=0.9,
        verification_status="verified",
        extraction_method="card",
    )
    page_text = "Our network includes Nimesh Mathur CHRO at Curefit."
    status, reason = check_officer_context(cand, "", page_text, "engineerbabu.com", cfg)
    assert status == "unverified"
    assert "another organization" in reason.lower() or "other organization" in reason.lower()


def test_jsonld_founder_stays_verified():
    """Assert genuine company founder defined in schema.org JSON-LD stays verified."""
    cfg = load_roles_config()
    cand = DecisionMakerCandidate(
        full_name="Mayank Pratap Singh",
        title="Co-Founder & CEO",
        role_category="founder",
        source_url="https://engineerbabu.com/",
        source_page_id="page_1",
        evidence_text='Mayank Pratap Singh", "jobTitle": "Co-Founder & CEO',
        confidence=1.0,
        verification_status="verified",
        extraction_method="jsonld_person",
    )
    status, reason = check_officer_context(cand, "", "", "engineerbabu.com", cfg)
    assert status == "verified"
