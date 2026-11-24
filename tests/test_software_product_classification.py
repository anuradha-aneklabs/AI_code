"""Test generic software product vs services classification on synthetic HTML.

Uses fake synthetic data only.
"""

from __future__ import annotations

import pytest
from harvey.software_classifier import classify_software_business


def test_classify_software_product():
    html = """
    <html>
      <head><title>CloudMetrics - Analytics Platform</title></head>
      <body>
        <h1>The Modern Growth Platform for Teams</h1>
        <p>Simple, transparent pricing. Start your free trial today or book a demo.</p>
        <a href="/signup">Sign Up Free</a>
        <a href="/login">Dashboard Login</a>
      </body>
    </html>
    """
    p_class, ev = classify_software_business(html, title="CloudMetrics - Analytics Platform")
    assert p_class == "software_product"
    assert "platform" in ev.lower() or "free trial" in ev.lower() or "pricing" in ev.lower()


def test_classify_software_services_agency():
    html = """
    <html>
      <head><title>Apex Software Solutions - Custom Development</title></head>
      <body>
        <h1>We Build Custom Digital Experiences</h1>
        <p>Offshore development and IT consulting services. Hire developers and dedicated teams for your projects.</p>
        <p>Our clients love our custom software development process. View our portfolio.</p>
      </body>
    </html>
    """
    p_class, ev = classify_software_business(html, title="Apex Software Solutions")
    assert p_class == "software_services"
    assert "hire developers" in ev.lower() or "custom software development" in ev.lower() or "portfolio" in ev.lower()


def test_classify_other_unrelated():
    html = """
    <html>
      <head><title>Fresh Bakery & Cafe</title></head>
      <body>
        <h1>Artisan Breads & Pastries</h1>
        <p>Baked daily with organic ingredients. Visit our shop or order catering.</p>
      </body>
    </html>
    """
    p_class, ev = classify_software_business(html, title="Fresh Bakery & Cafe")
    assert p_class == "other"


def test_classify_non_profit_not_software_product():
    html = """
    <html>
      <head><title>TiE Indore - The Indus Entrepreneurs Chapter</title></head>
      <body>
        <h1>Fostering Entrepreneurship Through Mentoring and Networking</h1>
        <p>Join our non-profit community platform. Register for upcoming events and become a member.</p>
        <a href="/join">Sign Up For Membership</a>
      </body>
    </html>
    """
    p_class, ev = classify_software_business(html, title="TiE Indore Chapter", domain="indore.tie.org")
    assert p_class != "software_product"
    assert p_class == "other"
    assert "non-profit" in ev.lower() or "association" in ev.lower() or "community" in ev.lower()


def test_classify_directory_and_job_boards_not_applicable():
    # Test directory domains
    for d in ["in.linkedin.com", "indeed.com", "justdial.com", "naukri.com", "clutch.co"]:
        p_class, ev = classify_software_business("<html><body>Software developers</body></html>", title="Top Companies", domain=d)
        assert p_class == "not_applicable"
        assert "not applicable" in ev.lower()


def test_agency_offering_saas_ai_development_classified_as_services():
    # Terms such as "platform", "saas" or "AI" appearing as a service offered to clients must NOT count as product evidence
    html = """
    <html>
      <head><title>NexusDev - Custom Software & AI Development Company</title></head>
      <body>
        <h1>We Build High-Scale Web Apps and SaaS Platforms for Startups</h1>
        <p>NexusDev is a full-service software development company. We offer custom software development,
        mobile app development services, and generative AI solutions for our clients.</p>
        <p>Hire dedicated developers and engineers for your projects. Schedule a free consultation.</p>
        <a href="/consultation">Book a Free 30-Min Call</a>
      </body>
    </html>
    """
    p_class, ev = classify_software_business(html, title="NexusDev - Custom Software & AI Development Company")
    assert p_class == "software_services"
    assert "software_product" != p_class
    assert "services" in ev.lower() or "agency" in ev.lower()


def test_real_looking_saas_homepage_classified_as_product():
    html = """
    <html>
      <head><title>PulseMetrics Cloud - Sales Analytics</title></head>
      <body>
        <h1>Real-Time Pipeline Intelligence for Sales Teams</h1>
        <p>Simple pricing plans starting at $49/mo per user. Billed annually.</p>
        <p>Start your 14-day free trial today. No credit card required.</p>
        <a href="/signup">Sign Up Free</a>
        <a href="/login">Dashboard Login</a>
        <a href="/docs">Developer Docs & API Reference</a>
      </body>
    </html>
    """
    p_class, ev = classify_software_business(html, title="PulseMetrics Cloud")
    assert p_class == "software_product"
    assert "product" in ev.lower()
    assert "pricing" in ev.lower() or "free trial" in ev.lower() or "sign up" in ev.lower()


def test_mixed_evidence_classified_as_unclear():
    # When evidence is mixed, classify as 'unclear' instead of defaulting to product
    html = """
    <html>
      <head><title>HybridTech Solutions</title></head>
      <body>
        <h1>Software Products & IT Consulting</h1>
        <p>Subscribe to our workflow tool: $29/mo per user with simple pricing plans.</p>
        <p>We also offer custom software development and staff augmentation services for enterprise clients.</p>
      </body>
    </html>
    """
    p_class, ev = classify_software_business(html, title="HybridTech Solutions")
    assert p_class in ("unclear", "software_services")
    assert p_class != "software_product"
