"""Generic config-driven classifier for software product companies vs software services/agencies.

Uses on-page text and keywords loaded from config/software_classification.json.
No hardcoded company, industry, or city names.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("harvey.software_classifier")

_DEFAULT_CONFIG_PATH = Path("config/software_classification.json")

_FALLBACK_PRODUCT_KEYWORDS = (
    "pricing plans", "pricing tiers", "pricing", "per month", "per user", "billed annually",
    "billed monthly", "sign up free", "sign up", "sign-up", "free trial", "start free trial",
    "try for free", "dashboard login", "client login", "customer portal", "product tour",
    "product features", "product demo", "book a product demo", "api documentation",
    "developer docs", "api reference", "self-serve", "subscription plans", "software license",
)

_FALLBACK_SERVICES_KEYWORDS = (
    "custom software development", "software development company", "software development services",
    "web development services", "web app development services", "mobile app development services",
    "app development company", "web app development company", "ai development company",
    "ai development services", "development services", "it services", "it consulting",
    "technology consulting", "staff augmentation", "hire developers", "hire dedicated developers",
    "hire dedicated", "hire engineers", "dedicated team", "dedicated developers",
    "dedicated development team", "outsourcing", "offshore development", "offshore team",
    "software agency", "web agency", "digital agency", "development agency", "our clients",
    "we build for clients", "case studies", "our portfolio", "projects delivered", "client work",
    "engagement models", "fixed pricing and flexible engagement models", "schedule a free consultation",
    "book a free 30-min call",
)

_SERVICE_OFFERING_PATTERNS = (
    re.compile(r"\b(saas|platform|platforms|ai|cloud)\s+(development|services|solutions|consulting|agency|company|firm|engineering|for\s+clients|projects|delivery)\b", re.I),
    re.compile(r"\b(custom|build|building|hire|offshore|dedicated|outsourcing)\s+(saas|platform|platforms|ai|software|web\s+app|mobile\s+app)\b", re.I),
    re.compile(r"\bwe\s+build\s+(saas|platforms?|apps?|software)\s+(for|with)\b", re.I),
)

_DIRECTORY_OR_PLATFORM_PATTERNS = (
    re.compile(r"(^|\.)(linkedin|indeed|glassdoor|naukri|ziprecruiter|monster|shine|foundit)\.", re.I),
    re.compile(r"(^|\.)(yelp|yellowpages|justdial|indiamart|tradeindia|sulekha|clutch|goodfirms|upwork|freelancer)\.", re.I),
    re.compile(r"(^|\.)(facebook|twitter|x|instagram|youtube|wikipedia|crunchbase)\.", re.I),
)

_NON_PROFIT_KEYWORDS = (
    "non-profit", "nonprofit", "not-for-profit", "foundation", "association",
    "charity", "ngo", "registered society", "charitable trust", "chapter",
    "community organization", "the indus entrepreneurs", "chamber of commerce",
    "volunteer", "philanthropic",
)

_CORE_AGENCY_INDICATORS = (
    "custom software development", "software development company", "hire developers",
    "hire dedicated", "software agency", "web development agency", "outsourcing",
    "staff augmentation", "dedicated team", "offshore development", "we build for clients",
)


def _load_config(config_path: Path | str | None = None) -> tuple[list[str], list[str]]:
    path = Path(config_path) if config_path else _DEFAULT_CONFIG_PATH
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            prod = [k.lower().strip() for k in data.get("product_keywords", []) if k.strip()]
            serv = [k.lower().strip() for k in data.get("services_keywords", []) if k.strip()]
            return prod or list(_FALLBACK_PRODUCT_KEYWORDS), serv or list(_FALLBACK_SERVICES_KEYWORDS)
        except Exception as e:
            logger.warning("Failed to load software classification config from %s: %s", path, e)
    return list(_FALLBACK_PRODUCT_KEYWORDS), list(_FALLBACK_SERVICES_KEYWORDS)


def _strip_html(html: str) -> str:
    """Strip script, style, and HTML tags to extract clean readable prose."""
    if not html:
        return ""
    cleaned = re.sub(r"(?is)<script\b.*?</script>", " ", html)
    cleaned = re.sub(r"(?is)<style\b.*?</style>", " ", cleaned)
    cleaned = re.sub(r"(?is)<!--.*?-->", " ", cleaned)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def classify_software_business(
    text_or_html: str,
    title: str = "",
    domain: str = "",
    config_path: Path | str | None = None,
    result_class: str = "company_homepage",
) -> tuple[str, str]:
    """Classify a company as software_product vs software_services vs unclear vs other vs not_applicable.

    Returns:
        (product_class, evidence_string)
        product_class is one of: 'software_product', 'software_services', 'unclear', 'other', 'not_applicable'
    """
    # 1. Non-company homepage results (directories, job boards, articles, etc.) are not applicable
    if result_class and result_class != "company_homepage":
        return "not_applicable", f"Not applicable for non-company homepage result class: {result_class}"

    # 2. Check for directory, job board, or social platforms by domain
    clean_domain = (domain or "").strip().lower()
    if clean_domain:
        for pat in _DIRECTORY_OR_PLATFORM_PATTERNS:
            if pat.search(clean_domain):
                return "not_applicable", f"Not applicable for directory, job board, or platform: {clean_domain}"

    if not text_or_html and not title:
        return "other", "No text or title available for classification"

    clean_text = _strip_html(text_or_html)
    combined = f"{title} {clean_text}".lower()

    # 3. Check for non-profit / association / community organization
    matched_non_profit = []
    for np_kw in _NON_PROFIT_KEYWORDS:
        pattern = r"\b" + re.escape(np_kw) + r"\b"
        if re.search(pattern, combined):
            matched_non_profit.append(np_kw)
    if clean_domain.endswith(".org") and not matched_non_profit:
        if any(term in combined for term in ("chapter", "network", "community", "mission", "charity")):
            matched_non_profit.append(".org community/mission")

    if matched_non_profit:
        return "other", f"Non-profit or association profile ({', '.join(matched_non_profit[:3])})"

    product_keywords, services_keywords = _load_config(config_path)

    # 4. Check if "saas", "platform", or "ai" appear as a client service offering
    matched_services: list[str] = []
    for pat in _SERVICE_OFFERING_PATTERNS:
        for match in pat.finditer(combined):
            matched_str = match.group(0).strip()
            if matched_str and matched_str not in matched_services:
                matched_services.append(matched_str)

    # 5. Extract regular services keywords
    for kw in services_keywords:
        pattern = r"\b" + re.escape(kw) + r"\b"
        if re.search(pattern, combined) and kw not in matched_services:
            matched_services.append(kw)

    # 6. Extract product keywords
    matched_product: list[str] = []
    for kw in product_keywords:
        pattern = r"\b" + re.escape(kw) + r"\b"
        if re.search(pattern, combined) and kw not in matched_product:
            matched_product.append(kw)

    p_count = len(matched_product)
    s_count = len(matched_services)

    # 7. Decision logic:
    # "software_product" requires PRODUCT evidence and is outweighed by SERVICES evidence.
    has_strong_agency_cue = any(
        cue in combined for cue in _CORE_AGENCY_INDICATORS
    ) or any("development" in s or "hire" in s or "agency" in s for s in matched_services)

    if p_count == 0 and s_count == 0:
        return "other", "No decisive product or services keywords detected"

    if s_count > 0 and p_count == 0:
        return "software_services", f"Services/Agency indicators ({s_count}): {', '.join(matched_services[:4])}"

    if p_count > 0 and s_count == 0:
        return "software_product", f"Product indicators ({p_count}): {', '.join(matched_product[:4])}"

    # Both product and services indicators are present
    if s_count > p_count or (s_count >= p_count and has_strong_agency_cue):
        return "software_services", f"Services/Agency indicators outweigh product ({s_count} vs {p_count}): {', '.join(matched_services[:3])}"

    if has_strong_agency_cue and p_count <= 2:
        return "software_services", f"Services/Agency profile: agency cues outweigh minor product terms: {', '.join(matched_services[:3])}"

    if p_count > s_count and not has_strong_agency_cue and p_count >= 3:
        return "software_product", f"Dominant product indicators ({p_count} vs {s_count}): {', '.join(matched_product[:3])}"

    # When evidence is mixed, classify as 'unclear' instead of defaulting to product
    return "unclear", f"Mixed profile: product ({p_count}: {', '.join(matched_product[:2])}) vs services ({s_count}: {', '.join(matched_services[:2])})"
