"""Company narrative extraction (deterministic, evidence-first).

Milestone 2 implementation:
Input: an existing research run_id (its stored source_pages).
Output: findings for company overview, vision, mission, and product_service,
each with source URL, verbatim evidence, confidence, and verification status.

Safety:
- No LLMs, no external network calls, no LinkedIn, no people extraction.
- Verbatim evidence check: evidence must be an exact substring of the source page.
- Status capping: if company_websites verification_status != "verified", findings capped at "unverified".
- Fallback to "unavailable" with explicit reason if nothing found.
- Additive persistence: each extraction run gets a new extraction_run_id, nothing deleted.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import difflib
import json
import logging
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag, NavigableString

from harvey.state import StateManager, _new_id, _utcnow

logger = logging.getLogger("harvey.company_narrative")


@dataclass
class ServiceFilteringRules:
    """Configured rules for filtering and prioritizing high-quality service names."""
    cta_verbs: list[str] = field(default_factory=lambda: [
        "schedule", "book", "get", "contact", "request", "start",
        "talk", "learn", "join", "download", "sign up", "sign-up",
        "hire", "call", "try", "explore", "view", "see", "read",
        "find", "check", "click", "subscribe", "tell", "lets", "let's"
    ])
    drop_patterns: list[str] = field(default_factory=lambda: [
        r"^\d+[%+]?\s*",
        r"^\d+[\.:]\s*",
        r"^[\"'“‘].*[\"'”’]$",
        r"^testimonial",
        r"\b(quote|pricing|case study|faq|faqs|review|rating|award|clutch|upwork)\b",
        r"^(you|your|we|our|us|they|i|one|every|all)\b",
    ])
    non_service_section_phrases: list[str] = field(default_factory=lambda: [
        "process", "how it works", "how we work", "tech stack", "faq", "faqs",
        "why us", "why choose", "stories", "testimonials", "reviews", "pricing",
        "leadership", "team", "careers", "what we’ve built", "what we've built"
    ])
    max_service_name_chars: int = 80
    max_service_words: int = 10
    similarity_threshold: float = 0.7


@dataclass
class NarrativeRules:
    """Configured rules for company narrative extraction."""
    heading_phrases: dict[str, list[str]] = field(default_factory=lambda: {
        "overview": ["about us", "who we are", "our story", "company overview", "about"],
        "vision": ["vision", "our vision"],
        "mission": ["mission", "our mission"],
        "product_service": [
            "what we do", "our services", "services", "products",
            "solutions", "offerings", "platform",
            "specialized services", "core services",
        ],
    })
    confidence: dict[str, float] = field(default_factory=lambda: {
        "jsonld": 1.0,
        "heading_section": 0.9,
        "list_under_heading": 0.9,
        "card_title": 0.95,
        "meta_description": 0.4,
    })
    max_section_chars: int = 1200
    max_list_items: int = 20
    service_filtering: ServiceFilteringRules = field(default_factory=ServiceFilteringRules)


def load_narrative_rules(config_path: str | Path | None = None) -> NarrativeRules:
    """Load extraction rules from configuration file or fall back to defaults."""
    paths_to_check: list[Path] = []
    if config_path:
        paths_to_check.append(Path(config_path))

    project_root = Path(__file__).resolve().parent.parent
    paths_to_check.extend([
        project_root / "config" / "company_narrative_rules.json",
        Path("config/company_narrative_rules.json"),
    ])

    for p in paths_to_check:
        if p.exists() and p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                filt_data = data.get("service_filtering", {})
                service_filt = ServiceFilteringRules(
                    cta_verbs=filt_data.get("cta_verbs", ServiceFilteringRules().cta_verbs),
                    drop_patterns=filt_data.get("drop_patterns", ServiceFilteringRules().drop_patterns),
                    non_service_section_phrases=filt_data.get(
                        "non_service_section_phrases", ServiceFilteringRules().non_service_section_phrases
                    ),
                    max_service_name_chars=int(filt_data.get("max_service_name_chars", 80)),
                    max_service_words=int(filt_data.get("max_service_words", 10)),
                    similarity_threshold=float(filt_data.get("similarity_threshold", 0.7)),
                )
                return NarrativeRules(
                    heading_phrases=data.get("heading_phrases", {}),
                    confidence=data.get("confidence", {}),
                    max_section_chars=int(data.get("max_section_chars", 1200)),
                    max_list_items=int(data.get("max_list_items", 20)),
                    service_filtering=service_filt,
                )
            except Exception as e:
                logger.warning("Failed to load narrative rules from %s: %s", p, e)

    return NarrativeRules()


def is_valid_service_name(name: str, rules: NarrativeRules | None = None) -> tuple[bool, str]:
    """Validate whether an extracted string qualifies as a legitimate service name.
    
    Drops:
    1. Items starting with action verbs used for calls to action.
    2. Items that are mostly numbers, metrics, or percentages.
    3. Testimonials or quote-like text.
    4. Strings exceeding maximum configured length or word count.
    5. Pure generic single words or FAQ questions.
    """
    active_rules = rules or NarrativeRules()
    filt = active_rules.service_filtering
    norm = _normalize_ws(name)
    if not norm:
        return False, "empty"

    words = norm.split()
    if len(norm) > filt.max_service_name_chars or len(words) > filt.max_service_words:
        return False, f"too_long ({len(norm)} chars, {len(words)} words)"

    if norm.endswith("?") or re.match(r"^q\d+", norm, re.I):
        return False, "question_or_faq"

    first_word = words[0].lower().rstrip(":,.-")
    two_words = f"{words[0]} {words[1]}".lower() if len(words) > 1 else ""
    cta_set = set(v.lower() for v in filt.cta_verbs)
    if first_word in cta_set or two_words in cta_set:
        return False, f"cta_verb ({first_word})"

    for pat in filt.drop_patterns:
        if re.search(pat, norm, re.I):
            return False, f"matches_drop_pattern ({pat})"

    if len(words) == 1 and norm.lower() in {
        "scope", "design", "build", "harden", "run", "services",
        "solutions", "products", "overview", "more", "details"
    }:
        return False, "generic_single_word"

    return True, "ok"


def filter_and_deduplicate_services(
    services: list[Finding],
    rules: NarrativeRules | None = None,
) -> list[Finding]:
    """Filter candidate service findings and deduplicate near-duplicates by normalized similarity."""
    active_rules = rules or NarrativeRules()
    filt = active_rules.service_filtering
    valid_candidates: list[Finding] = []

    for s in services:
        valid, reason = is_valid_service_name(s.value_text, active_rules)
        if valid:
            valid_candidates.append(s)
        else:
            logger.debug("Dropped candidate service '%s': %s", s.value_text[:50], reason)

    unique: list[Finding] = []
    for cand in valid_candidates:
        c_norm = re.sub(r"[^a-z0-9]", "", cand.value_text.lower())
        if not c_norm:
            continue
        is_dup = False
        for u in unique:
            u_norm = re.sub(r"[^a-z0-9]", "", u.value_text.lower())
            if c_norm == u_norm:
                is_dup = True
                break
            ratio = difflib.SequenceMatcher(None, c_norm, u_norm).ratio()
            if ratio >= filt.similarity_threshold:
                is_dup = True
                break
        if not is_dup:
            unique.append(cand)

    return unique


@dataclass
class Finding:
    """A single extracted company narrative finding."""
    finding_type: str  # "overview" | "vision" | "mission" | "product_service"
    value_text: str
    evidence_text: str
    source_url: str
    source_page_id: str
    domain: str
    confidence: float
    verification_status: str  # "verified" | "unverified" | "unavailable"
    extraction_method: str    # "jsonld" | "heading_section" | "list_under_heading" | "meta_description" | "none"
    reason: str = ""


def _normalize_ws(text: str) -> str:
    """Collapse consecutive whitespace and strip."""
    return re.sub(r"\s+", " ", text or "").strip()


def _is_verbatim_evidence(evidence: str, page_html: str, page_text: str) -> bool:
    """Verify that evidence_text is verbatim present in page_html or page_text."""
    norm_ev = _normalize_ws(evidence)
    if not norm_ev:
        return False
    norm_html = _normalize_ws(page_html)
    norm_text = _normalize_ws(page_text)
    return (norm_ev in norm_html) or (norm_ev in norm_text)


def extract_from_jsonld(
    raw_html: str,
    source_url: str,
    page_id: str,
    domain: str,
) -> list[Finding]:
    """Extract organization overview/description from JSON-LD scripts."""
    findings: list[Finding] = []
    if not raw_html:
        return findings

    soup = BeautifulSoup(raw_html, "html.parser")
    org_types = {"organization", "corporation", "localbusiness", "company"}

    for script in soup.find_all("script", type=re.compile(r"ld\+json", re.I)):
        text = script.string or script.get_text()
        if not text:
            continue
        try:
            data = json.loads(text)
        except Exception:
            continue

        items = data if isinstance(data, list) else [data]
        # Handle @graph wrapper
        expanded_items: list[dict] = []
        for it in items:
            if isinstance(it, dict):
                if "@graph" in it and isinstance(it["@graph"], list):
                    expanded_items.extend([g for g in it["@graph"] if isinstance(g, dict)])
                else:
                    expanded_items.append(it)

        for obj in expanded_items:
            type_val = obj.get("@type", "")
            matched_type = False
            if isinstance(type_val, str) and type_val.lower() in org_types:
                matched_type = True
            elif isinstance(type_val, list) and any(str(t).lower() in org_types for t in type_val):
                matched_type = True

            if matched_type:
                desc = obj.get("description") or obj.get("slogan")
                if isinstance(desc, str) and desc.strip():
                    clean_desc = desc.strip()
                    findings.append(Finding(
                        finding_type="overview",
                        value_text=clean_desc,
                        evidence_text=clean_desc,
                        source_url=source_url,
                        source_page_id=page_id,
                        domain=domain,
                        confidence=1.0,
                        verification_status="verified",
                        extraction_method="jsonld",
                    ))
    return findings


def extract_from_meta(
    raw_html: str,
    source_url: str,
    page_id: str,
    domain: str,
) -> list[Finding]:
    """Extract overview finding from meta description or og:description."""
    findings: list[Finding] = []
    if not raw_html:
        return findings

    soup = BeautifulSoup(raw_html, "html.parser")
    desc = ""
    # Standard meta description
    meta_tag = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    if meta_tag and meta_tag.get("content"):
        desc = meta_tag["content"].strip()
    if not desc:
        # OpenGraph fallback
        og_tag = soup.find("meta", attrs={"property": re.compile(r"^og:description$", re.I)})
        if og_tag and og_tag.get("content"):
            desc = og_tag["content"].strip()

    if desc:
        findings.append(Finding(
            finding_type="overview",
            value_text=desc,
            evidence_text=desc,
            source_url=source_url,
            source_page_id=page_id,
            domain=domain,
            confidence=0.4,
            verification_status="unverified",
            extraction_method="meta_description",
        ))
    return findings


def _clean_soup_for_headings(soup: BeautifulSoup) -> None:
    """Remove boilerplate noise, scripts, navigation, headers, footers, forms, and cookie banners."""
    # Decompose structural boilerplate tags
    for tag in soup(["script", "style", "noscript", "svg", "nav", "header", "footer", "aside", "form"]):
        tag.decompose()

    # Decompose cookie/consent/banner elements by class, id, or role
    cookie_pattern = re.compile(r"(cookie|consent|banner|notice|gdpr|modal)", re.I)
    for el in list(soup.find_all(True)):
        if el.attrs is None or el.name in ("body", "html", "main"):
            continue
        cls = el.attrs.get("class", [])
        classes = " ".join(cls) if isinstance(cls, list) else str(cls or "")
        el_id = str(el.attrs.get("id", "") or "")
        el_role = str(el.attrs.get("role", "") or "")
        if (
            cookie_pattern.search(classes)
            or cookie_pattern.search(el_id)
            or el_role in ("dialog", "alertdialog")
        ):
            # Check if this element contains a primary content tag like <main> or <article>
            if not el.find(["main", "article"]):
                el.decompose()


def extract_from_headings(
    raw_html: str,
    source_url: str,
    page_id: str,
    domain: str,
    rules: NarrativeRules,
) -> list[Finding]:
    """Extract findings for overview, vision, mission, and product_service from headings."""
    findings: list[Finding] = []
    if not raw_html:
        return findings

    soup = BeautifulSoup(raw_html, "html.parser")
    _clean_soup_for_headings(soup)

    non_service_phrases = rules.service_filtering.non_service_section_phrases
    seen_values: set[str] = set()

    # 1. On services pages, scan card headings and individual service blocks
    parsed_path = urlparse(source_url).path.lower()
    is_services_page = any(seg in parsed_path for seg in ("services", "solutions", "what-we-do", "offerings"))

    if is_services_page:
        for h in soup.find_all(re.compile(r"^h[2-4]$", re.I)):
            h_text = _normalize_ws(h.get_text())
            if not h_text or any(p in h_text.lower() for p in non_service_phrases):
                continue
            if h_text.lower() in ("services", "our services", "what we do", "custom software development services", "specialized services"):
                continue
            valid, _ = is_valid_service_name(h_text, rules)
            if valid and h_text not in seen_values:
                seen_values.add(h_text)
                findings.append(Finding(
                    finding_type="product_service",
                    value_text=h_text,
                    evidence_text=h_text,
                    source_url=source_url,
                    source_page_id=page_id,
                    domain=domain,
                    confidence=rules.confidence.get("card_title", 0.95),
                    verification_status="verified",
                    extraction_method="card_title",
                ))

    # 2. General section & heading extraction
    heading_tags = soup.find_all(re.compile(r"^h[1-6]$", re.I))

    # Pre-compile heading matchers
    type_priority = ["vision", "mission", "product_service", "overview"]
    matchers: list[tuple[str, list[re.Pattern]]] = []
    for ftype in type_priority:
        phrases = rules.heading_phrases.get(ftype, [])
        patterns = [
            re.compile(rf"^\s*{re.escape(p)}\s*$", re.I) for p in phrases
        ] + [
            re.compile(rf"\b{re.escape(p)}\b", re.I) for p in phrases
        ]
        matchers.append((ftype, patterns))

    for h in heading_tags:
        h_text = _normalize_ws(h.get_text())
        if not h_text:
            continue

        # Skip sections that are explicitly non-service (process, team, faq, etc.)
        if any(p in h_text.lower() for p in non_service_phrases):
            continue

        matched_type: str | None = None
        for ftype, patterns in matchers:
            if any(pat.search(h_text) for pat in patterns):
                matched_type = ftype
                break

        if not matched_type:
            continue

        # Collect content following this heading until the next heading
        sibling_elements: list[Tag] = []
        curr = h.next_sibling
        while curr:
            if isinstance(curr, Tag):
                if re.match(r"^h[1-6]$", curr.name, re.I):
                    break
                sibling_elements.append(curr)
            curr = curr.next_sibling

        # If heading is wrapped in a container with little else, inspect parent's following siblings
        if not sibling_elements and h.parent and h.parent.name not in ("body", "html", "main"):
            curr_parent_sib = h.parent.next_sibling
            while curr_parent_sib:
                if isinstance(curr_parent_sib, Tag):
                    if re.match(r"^h[1-6]$", curr_parent_sib.name, re.I):
                        break
                    sibling_elements.append(curr_parent_sib)
                curr_parent_sib = curr_parent_sib.next_sibling

        if matched_type == "product_service":
            # Extract card titles inside child containers
            for sib in sibling_elements:
                for card_h in sib.find_all(["h3", "h4", "h5", "strong"]):
                    card_title = _normalize_ws(card_h.get_text())
                    valid, _ = is_valid_service_name(card_title, rules)
                    if valid and card_title not in seen_values:
                        seen_values.add(card_title)
                        findings.append(Finding(
                            finding_type="product_service",
                            value_text=card_title,
                            evidence_text=card_title,
                            source_url=source_url,
                            source_page_id=page_id,
                            domain=domain,
                            confidence=rules.confidence.get("card_title", 0.95),
                            verification_status="verified",
                            extraction_method="card_title",
                        ))

            # Look for lists (ul, ol)
            list_tag: Tag | None = None
            for sib in sibling_elements:
                if sib.name in ("ul", "ol"):
                    list_tag = sib
                    break
                found_list = sib.find(["ul", "ol"])
                if found_list:
                    list_tag = found_list
                    break

            if list_tag:
                items = list_tag.find_all("li")[:rules.max_list_items]
                for li in items:
                    li_text = _normalize_ws(li.get_text(separator=" ", strip=True))
                    if not li_text or li_text in seen_values:
                        continue
                    # Prefer short noun-phrase from inner title or separator
                    inner_title = li.find(["h3", "h4", "h5", "h6", "strong", "b"])
                    if inner_title:
                        short_name = _normalize_ws(inner_title.get_text())
                    else:
                        short_name = li_text
                        for sep in (" — ", " - ", ": ", " | "):
                            if sep in li_text:
                                left = li_text.split(sep, 1)[0].strip()
                                if len(left.split()) <= rules.service_filtering.max_service_words:
                                    short_name = left
                                    break

                    valid, _ = is_valid_service_name(short_name, rules)
                    if valid:
                        seen_values.add(li_text)
                        findings.append(Finding(
                            finding_type="product_service",
                            value_text=short_name,
                            evidence_text=li_text,
                            source_url=source_url,
                            source_page_id=page_id,
                            domain=domain,
                            confidence=rules.confidence.get("list_under_heading", 0.9),
                            verification_status="verified",
                            extraction_method="list_under_heading",
                        ))
                continue

        # For overview, vision, mission, or product_service without a list:
        # Gather text up to max_section_chars
        content_parts: list[str] = []
        for sib in sibling_elements:
            t = _normalize_ws(sib.get_text(separator=" ", strip=True))
            if t:
                content_parts.append(t)
            if sum(len(p) for p in content_parts) >= rules.max_section_chars:
                break

        section_text = " ".join(content_parts).strip()
        if section_text:
            if len(section_text) > rules.max_section_chars:
                # Find sentence boundary within limit
                truncated = section_text[:rules.max_section_chars]
                last_dot = max(truncated.rfind(". "), truncated.rfind(".\n"))
                if last_dot > int(rules.max_section_chars * 0.6):
                    section_text = truncated[:last_dot + 1].strip()
                else:
                    section_text = truncated.strip()

            if section_text and section_text not in seen_values:
                # If matched_type is product_service, ensure section_text passes service validation
                if matched_type == "product_service":
                    valid, _ = is_valid_service_name(section_text, rules)
                    if not valid:
                        continue
                seen_values.add(section_text)
                findings.append(Finding(
                    finding_type=matched_type,
                    value_text=section_text,
                    evidence_text=section_text,
                    source_url=source_url,
                    source_page_id=page_id,
                    domain=domain,
                    confidence=rules.confidence.get("heading_section", 0.9),
                    verification_status="verified",
                    extraction_method="heading_section",
                ))

    return findings


def extract_from_raw_text(
    raw_text: str,
    source_url: str,
    page_id: str,
    domain: str,
    rules: NarrativeRules,
) -> list[Finding]:
    """Fallback extractor when raw_html is unavailable, scanning lines of readable text."""
    findings: list[Finding] = []
    if not raw_text:
        return findings

    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
    type_priority = ["vision", "mission", "product_service", "overview"]
    
    seen_values: set[str] = set()

    for i, line in enumerate(lines):
        line_clean = line.lower()
        matched_type: str | None = None
        for ftype in type_priority:
            phrases = rules.heading_phrases.get(ftype, [])
            for p in phrases:
                if re.match(rf"^\s*{re.escape(p)}\s*[:\-]?", line_clean, re.I):
                    matched_type = ftype
                    break
            if matched_type:
                break

        if not matched_type:
            continue

        # Look at the following lines for section content
        following_content: list[str] = []
        for j in range(i + 1, min(i + 6, len(lines))):
            next_line = lines[j]
            # If next line looks like another heading, stop
            if any(re.match(rf"^\s*{re.escape(p)}\b", next_line, re.I)
                   for f in type_priority for p in rules.heading_phrases.get(f, [])):
                break
            following_content.append(next_line)
            if sum(len(c) for c in following_content) >= rules.max_section_chars:
                break

        combined = " ".join(following_content).strip()
        if combined and combined not in seen_values:
            val = combined[:rules.max_section_chars]
            seen_values.add(val)
            findings.append(Finding(
                finding_type=matched_type,
                value_text=val,
                evidence_text=val,
                source_url=source_url,
                source_page_id=page_id,
                domain=domain,
                confidence=0.7,
                verification_status="verified",
                extraction_method="heading_section",
            ))

    return findings


async def extract_company_narrative(
    run_id: str,
    state: StateManager | None = None,
    config_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Execute Milestone 2 company narrative extraction for an existing research run.
    
    - Generates a new extraction_run_id for additive persistence.
    - Extracts findings across all stored source pages.
    - Validates verbatim evidence invariant for every finding.
    - Caps verification_status if company_websites verification_status != 'verified'.
    - Produces 'unavailable' rows with explicit reasons for any missing finding types.
    - Updates 'content_extraction' research stage.
    """
    st = state or StateManager()
    await st.init_db()

    run = await st.get_research_run(run_id)
    if not run:
        raise ValueError(f"Research run '{run_id}' not found")

    target_domain = run.get("domain", "")
    pages = await st.get_source_pages(run_id)
    website = await st.get_company_website(run_id) or {}
    site_status = website.get("verification_status", "unavailable")

    rules = load_narrative_rules(config_path)
    extraction_run_id = _new_id()
    stage_id = _new_id()
    stage_start = _utcnow().isoformat()

    await st.save_research_stage(
        stage_id=stage_id,
        run_id=run_id,
        stage_name="content_extraction",
        status="running",
        started_at=stage_start,
        details_json=json.dumps({"extraction_run_id": extraction_run_id}),
    )

    extracted_findings: list[Finding] = []
    page_errors: list[dict[str, Any]] = []

    # Process each page with strict isolation
    for p in pages:
        p_id = p.get("id", "")
        p_url = p.get("url", "")
        raw_html = p.get("raw_html", "") or ""
        raw_text = p.get("raw_text", "") or ""

        try:
            candidates: list[Finding] = []
            if raw_html:
                # 1. JSON-LD extraction
                candidates.extend(extract_from_jsonld(raw_html, p_url, p_id, target_domain))
                # 2. Headings extraction
                candidates.extend(extract_from_headings(raw_html, p_url, p_id, target_domain, rules))
                # 3. Meta description extraction
                candidates.extend(extract_from_meta(raw_html, p_url, p_id, target_domain))
            elif raw_text:
                candidates.extend(extract_from_raw_text(raw_text, p_url, p_id, target_domain, rules))

            # Verbatim evidence invariant check
            for cand in candidates:
                if _is_verbatim_evidence(cand.evidence_text, raw_html, raw_text):
                    extracted_findings.append(cand)
                else:
                    logger.debug(
                        "Discarded non-verbatim candidate for %s on %s: %s",
                        cand.finding_type, p_url, cand.evidence_text[:80],
                    )

        except Exception as e:
            logger.exception("Error extracting from page %s (%s)", p_id, p_url)
            page_errors.append({"page_id": p_id, "url": p_url, "error": str(e)})

    # Cap verification status if website is unverified or unavailable (AC6)
    for f in extracted_findings:
        if site_status != "verified":
            f.verification_status = "unverified"

    # Deduplicate and prioritize findings
    # For overview: prefer jsonld (1.0), then heading_section (0.9), then meta_description (0.4)
    final_findings: list[Finding] = []

    overviews = [f for f in extracted_findings if f.finding_type == "overview"]
    if overviews:
        overviews.sort(key=lambda x: x.confidence, reverse=True)
        # Select highest-confidence overview
        final_findings.append(overviews[0])

    # For vision and mission:
    for ftype in ("vision", "mission"):
        matches = [f for f in extracted_findings if f.finding_type == ftype]
        if matches:
            matches.sort(key=lambda x: x.confidence, reverse=True)
            final_findings.append(matches[0])

    # For product_service:
    raw_services = [f for f in extracted_findings if f.finding_type == "product_service"]
    filtered_services = filter_and_deduplicate_services(raw_services, rules)
    for s in filtered_services[:rules.max_list_items]:
        final_findings.append(s)

    # Unavailable handling: Ensure all 4 finding types are accounted for (AC3, AC10)
    all_types = ["overview", "vision", "mission", "product_service"]
    for t in all_types:
        if not any(f.finding_type == t for f in final_findings):
            if t == "product_service":
                reason_msg = "no clear service list found"
            else:
                reason_msg = "No matching heading, jsonld, or meta description found in fetched pages"
            if not pages:
                reason_msg = "No source pages fetched for this domain"
            elif all(p.get("extraction_status") != "fetched" for p in pages):
                reason_msg = "All source pages failed to fetch"

            final_findings.append(Finding(
                finding_type=t,
                value_text="unavailable",
                evidence_text="",
                source_url="",
                source_page_id="",
                domain=target_domain,
                confidence=0.0,
                verification_status="unavailable",
                extraction_method="none",
                reason=reason_msg,
            ))

    # Persist findings to database
    for f in final_findings:
        f_id = _new_id()
        await st.save_research_finding(
            finding_id=f_id,
            run_id=run_id,
            extraction_run_id=extraction_run_id,
            source_page_id=f.source_page_id,
            domain=f.domain,
            finding_type=f.finding_type,
            value_text=f.value_text,
            evidence_text=f.evidence_text,
            source_url=f.source_url,
            confidence=f.confidence,
            verification_status=f.verification_status,
            extraction_method=f.extraction_method,
            reason=f.reason,
        )

    stage_end = _utcnow().isoformat()
    stage_status = "completed"
    if page_errors:
        stage_status = "partial"

    findings_count_by_type = {}
    for t in all_types:
        findings_count_by_type[t] = sum(1 for f in final_findings if f.finding_type == t and f.verification_status != "unavailable")

    await st.save_research_stage(
        stage_id=stage_id,
        run_id=run_id,
        stage_name="content_extraction",
        status=stage_status,
        started_at=stage_start,
        finished_at=stage_end,
        error_message="" if not page_errors else f"{len(page_errors)} page extraction error(s)",
        details_json=json.dumps({
            "extraction_run_id": extraction_run_id,
            "counts": findings_count_by_type,
            "total_persisted": len(final_findings),
            "errors": page_errors,
        }),
    )

    return await st.get_research_findings(run_id, extraction_run_id=extraction_run_id)
