"""Unit and integration tests for service quality filtering, CTA removal, and deduplication."""

import csv
import pytest
from harvey.company_narrative import (
    Finding,
    NarrativeRules,
    filter_and_deduplicate_services,
    is_valid_service_name,
    load_narrative_rules,
)
from harvey.batch_research import (
    create_batch_from_file,
    export_batch_results,
)
from harvey.state import StateManager


@pytest.fixture
def temp_state(tmp_path):
    db_file = tmp_path / "test_service_filtering.db"
    return StateManager(db_path=str(db_file))


@pytest.fixture
def narrative_rules():
    return load_narrative_rules()


def test_cta_action_verbs_dropped(narrative_rules):
    """Items starting with action verbs used for calls to action must be dropped."""
    cta_samples = [
        "Schedule a Free Consultation",
        "Book a Free 30-Min Call",
        "Get in touch with our team",
        "Contact us for pricing",
        "Request a demo today",
        "Start a project",
        "Talk to our engineering leads",
        "Learn more about our platform",
        "Join our developer community",
        "Download our whitepaper",
        "Sign up for a free trial",
        "Hire Dedicated Developers",
        "Explore Flutter Studio",
        "View Case Study",
    ]
    for sample in cta_samples:
        valid, reason = is_valid_service_name(sample, narrative_rules)
        assert not valid, f"Expected '{sample}' to be dropped, but was accepted (reason: {reason})"
        assert "cta_verb" in reason


def test_metric_and_percentage_dropped(narrative_rules):
    """Items that are mostly numbers, metrics, percentages, or ordered questions must be dropped."""
    metric_samples = [
        "99% On-Time",
        "100% Code Ownership & NDA",
        "500+ Products Delivered",
        "14+ Years in Business",
        "1. How much does web app development cost?",
        "Q01What services does the team offer?",
    ]
    for sample in metric_samples:
        valid, reason = is_valid_service_name(sample, narrative_rules)
        assert not valid, f"Expected '{sample}' to be dropped as metric/question, but was accepted"


def test_testimonials_and_quotes_dropped(narrative_rules):
    """Items that are quote-like, testimonial text, or pricing/reviews must be dropped."""
    quote_samples = [
        '"The engineering team delivered ahead of schedule."',
        '“Exceptional mobile development partner”',
        "Testimonial from enterprise client",
        "Pricing and retainer options",
        "Customer review on Clutch",
    ]
    for sample in quote_samples:
        valid, reason = is_valid_service_name(sample, narrative_rules)
        assert not valid, f"Expected '{sample}' to be dropped as quote/review"


def test_length_filter_dropped(narrative_rules):
    """Sentences or paragraphs longer than configured length/words must be dropped."""
    long_samples = [
        "From first-time founders to Fortune 500s - we've shipped software across the US, UK, UAE, and Australia. Every number below is a product that went live.",
        "From early Figma screens to a finished product in your users' hands—we help startups and growing businesses design, build, and scale reliable software.",
        "We build the software behind ambitious businesses: web, mobile, SaaS, AI agents and automation, designed and shipped by our engineering team.",
    ]
    for sample in long_samples:
        valid, reason = is_valid_service_name(sample, narrative_rules)
        assert not valid, f"Expected long text to be dropped by length filter: {sample[:40]}"
        assert "too_long" in reason


def test_near_duplicates_dropped_and_first_kept(narrative_rules):
    """Items that heavily overlap another item (> 0.7 similarity) are dropped, keeping the first."""
    findings = [
        Finding(
            finding_type="product_service",
            value_text="Cloud Infrastructure Automation",
            evidence_text="Cloud Infrastructure Automation",
            source_url="https://test.co/services",
            source_page_id="p1",
            domain="test.co",
            confidence=0.9,
            verification_status="verified",
            extraction_method="list_under_heading",
        ),
        Finding(
            finding_type="product_service",
            value_text="Cloud Infrastructure Automation & DevOps",
            evidence_text="Cloud Infrastructure Automation & DevOps",
            source_url="https://test.co/services",
            source_page_id="p1",
            domain="test.co",
            confidence=0.9,
            verification_status="verified",
            extraction_method="list_under_heading",
        ),
        Finding(
            finding_type="product_service",
            value_text="Mobile App Development",
            evidence_text="Mobile App Development",
            source_url="https://test.co/services",
            source_page_id="p1",
            domain="test.co",
            confidence=0.9,
            verification_status="verified",
            extraction_method="list_under_heading",
        ),
    ]

    deduped = filter_and_deduplicate_services(findings, narrative_rules)
    values = [f.value_text for f in deduped]
    assert "Cloud Infrastructure Automation" in values
    assert "Cloud Infrastructure Automation & DevOps" not in values, "Near-duplicate was not dropped"
    assert "Mobile App Development" in values
    assert len(deduped) == 2


def test_legitimate_short_service_names_kept(narrative_rules):
    """Legitimate short noun-phrase service names must be kept (guard against over-filtering)."""
    legit_services = [
        "Mobile App Development",
        "Cloud Migration",
        "Custom Web Development",
        "AI Solutions",
        "SaaS Product Development",
        "API & Backend Engineering",
        "UI/UX Design",
        "Database Architecture",
        "DevOps & CI/CD",
        "Quality Assurance & Testing",
    ]
    for s in legit_services:
        valid, reason = is_valid_service_name(s, narrative_rules)
        assert valid, f"Legitimate service '{s}' was incorrectly dropped (reason: {reason})"


@pytest.mark.asyncio
async def test_export_fallback_when_no_clear_services_pass_filter(temp_state, tmp_path):
    """When all candidate services fail filter, export shows 'unavailable: no clear service list found'."""
    await temp_state.init_db()
    csv_content = "Name,Email\nPartner,partner@noisycorp.test\n".encode("utf-8")
    batch_res = await create_batch_from_file(csv_content, "noisy.csv", temp_state, max_domains=5)
    batch_id = batch_res["batch_id"]

    domain = "noisycorp.test"
    run_id = "run_noisy_1"

    await temp_state.save_research_run(
        run_id=run_id,
        input_email="partner@noisycorp.test",
        normalized_email="partner@noisycorp.test",
        domain=domain,
        status="completed",
    )
    await temp_state.save_company_website(
        website_id="site_noisy",
        run_id=run_id,
        domain=domain,
        final_url="https://noisycorp.test/",
        verification_status="verified",
        confidence=1.0,
    )
    # Only noise / CTAs / stats in findings
    noisy_items = [
        "Schedule a Free Consultation",
        "Book a Free 30-Min Call",
        "99% On-Time",
        "From first-time founders to Fortune 500s - we've shipped software across the US, UK, UAE, and Australia. Every number below is a product that went live.",
    ]
    for i, item in enumerate(noisy_items):
        await temp_state.save_research_finding(
            finding_id=f"f_noisy_{i}",
            run_id=run_id,
            extraction_run_id="ext_noisy_1",
            domain=domain,
            finding_type="product_service",
            value_text=item,
            verification_status="verified",
            confidence=0.9,
        )

    await temp_state.update_import_domain_status(
        batch_id,
        domain,
        status="completed",
        run_id=run_id,
    )

    export_dir = tmp_path / "exports"
    export_res = await export_batch_results(batch_id, temp_state, export_dir=export_dir)

    with open(export_res["contacts_results"], "r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))
        assert len(reader) == 1
        assert reader[0]["services"] == "unavailable: no clear service list found"
