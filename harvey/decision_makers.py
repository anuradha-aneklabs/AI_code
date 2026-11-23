"""Decision-maker discovery (deterministic, evidence-first).

Milestone 3 implementation:
Input: an existing research run_id (its stored source_pages and company_websites).
Output: every named founder/owner/director/CEO/CTO/CFO/COO/other CXO found on the
company's own pages, each with name, title, role category, source URL, verbatim
evidence, confidence and verification status.

Safety:
- No LLMs, no external network calls, no LinkedIn, no email guessing.
- Verbatim evidence check: evidence must be an exact substring of the source page.
- Status capping: if company_websites verification_status != "verified", findings capped at "unverified".
- Fallback to "unavailable" with explicit reason if nothing found.
- Additive persistence: each extraction run gets a new extraction_run_id, nothing deleted.
- Zero writes to prospects, companies, outbox, or observations.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag, NavigableString

from harvey.collectors.people import (
    COMMON_WORDS,
    _FORMER,
    _SUBORDINATE,
    split_person_name,
    title_names_other_org,
)
from harvey.collectors.profile import NON_NAME_WORDS
from harvey.state import StateManager, _new_id, _utcnow

logger = logging.getLogger("harvey.decision_makers")


@dataclass
class DecisionMakerRolesConfig:
    """Configured roles, title mappings, and confidence scores."""
    role_mappings: dict[str, str] = field(default_factory=dict)
    generic_cxo_regex: str = r"(?i)\bchief\s+[a-z]+\s+officer\b"
    confidence_scores: dict[str, float] = field(default_factory=lambda: {
        "jsonld_person": 1.0,
        "card": 0.9,
        "heading_list": 0.9,
        "sentence": 0.5,
    })
    non_person_marketing_words: list[str] = field(default_factory=list)
    non_person_generic_words: list[str] = field(default_factory=list)
    external_context_patterns: dict[str, str] = field(default_factory=dict)


def load_roles_config(config_path: str | Path | None = None) -> DecisionMakerRolesConfig:
    """Load role mappings and regexes from config file or fall back to defaults."""
    paths_to_check: list[Path] = []
    if config_path:
        paths_to_check.append(Path(config_path))

    project_root = Path(__file__).resolve().parent.parent
    paths_to_check.extend([
        project_root / "config" / "decision_maker_roles.json",
        Path("config/decision_maker_roles.json"),
    ])

    for p in paths_to_check:
        if p.exists() and p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                return DecisionMakerRolesConfig(
                    role_mappings=data.get("role_mappings", {}),
                    generic_cxo_regex=data.get("generic_cxo_regex", r"(?i)\bchief\s+[a-z]+\s+officer\b"),
                    confidence_scores=data.get("confidence_scores", {}),
                    non_person_marketing_words=data.get("non_person_marketing_words", []),
                    non_person_generic_words=data.get("non_person_generic_words", []),
                    external_context_patterns=data.get("external_context_patterns", {}),
                )
            except Exception as e:
                logger.warning("Failed to load decision maker roles from %s: %s", p, e)

    # Built-in fallback
    return DecisionMakerRolesConfig(
        role_mappings={
            "founder": "founder",
            "co-founder": "founder",
            "cofounder": "founder",
            "founding partner": "founder",
            "owner": "owner",
            "co-owner": "owner",
            "proprietor": "owner",
            "director": "director",
            "managing director": "director",
            "executive director": "director",
            "ceo": "ceo",
            "chief executive officer": "ceo",
            "chief executive": "ceo",
            "cto": "cto",
            "chief technology officer": "cto",
            "chief technical officer": "cto",
            "cfo": "cfo",
            "chief financial officer": "cfo",
            "coo": "coo",
            "chief operating officer": "coo",
            "chief operations officer": "coo",
            "cmo": "cxo",
            "chief marketing officer": "cxo",
            "cio": "cxo",
            "chief information officer": "cxo",
            "ciso": "cxo",
            "chief information security officer": "cxo",
            "cpo": "cxo",
            "chief product officer": "cxo",
            "cro": "cxo",
            "chief revenue officer": "cxo",
            "president": "other_leadership",
            "partner": "other_leadership",
            "principal": "other_leadership",
            "chairman": "other_leadership",
            "chairperson": "other_leadership",
        },
        non_person_marketing_words=[
            "landing", "page", "pages", "web", "website", "websites", "mobile",
            "design", "software", "app", "apps", "service", "services", "contact",
            "blog", "news", "pricing", "case", "study", "studies", "feature",
            "features", "benefit", "benefits", "conversion", "optimization",
            "marketing", "agency", "digital", "creative", "strategy", "engineering",
            "intelligence", "lead", "gen", "generation", "product", "products",
            "solution", "solutions", "resource", "resources", "career", "careers",
            "privacy", "terms", "policy", "cookie", "cookies", "login", "signup",
        ]
    )


@dataclass
class DecisionMakerCandidate:
    """An extracted candidate decision-maker."""
    full_name: str
    title: str
    role_category: str
    source_url: str
    source_page_id: str
    evidence_text: str
    confidence: float
    verification_status: str  # "verified" | "unverified" | "unavailable"
    extraction_method: str    # "jsonld_person" | "card" | "sentence" | "heading_list" | "none"
    also_seen_urls: list[str] = field(default_factory=list)
    reason: str = ""
    excluded_reason: str = ""


def _normalize_ws(text: str) -> str:
    """Normalize consecutive whitespace and strip."""
    return re.sub(r"\s+", " ", text or "").strip()


_INVALID_NAME_CHARS = frozenset({"|", "/", "\\", "@", "#", "$", "%", "^", "*", "=", "+", "<", ">", "{", "}", "[", "]", "~", ";", ":"})


def _get_marketing_words(config: DecisionMakerRolesConfig | None = None) -> frozenset[str]:
    """Retrieve marketing and navigation words from configuration."""
    cfg = config or load_roles_config()
    if cfg and cfg.non_person_marketing_words:
        return frozenset(w.lower().strip() for w in cfg.non_person_marketing_words if w.strip())
    return frozenset({
        "landing", "page", "pages", "web", "website", "websites", "mobile",
        "design", "software", "app", "apps", "service", "services", "contact",
        "blog", "news", "pricing", "case", "study", "studies", "feature",
        "features", "benefit", "benefits", "conversion", "optimization",
        "marketing", "agency", "digital", "creative", "strategy", "engineering",
        "intelligence", "lead", "gen", "generation", "product", "products",
        "solution", "solutions", "resource", "resources", "career", "careers",
        "privacy", "terms", "policy", "cookie", "cookies", "login", "signup",
        "home", "support", "about", "team", "leadership"
    })


def _get_generic_words(config: DecisionMakerRolesConfig | None = None) -> frozenset[str]:
    """Retrieve generic and role/legal non-person words from configuration."""
    cfg = config or load_roles_config()
    if cfg and cfg.non_person_generic_words:
        return frozenset(w.lower().strip() for w in cfg.non_person_generic_words if w.strip())
    return frozenset({
        "signatory", "authorized", "representative", "admin", "administrator",
        "team", "support", "sales", "contact", "management", "department",
        "staff", "employee", "customer", "client", "user", "board", "member",
        "officer", "director", "founder", "partner", "president", "legal",
        "signature", "official", "attorney", "counsel", "anyone", "everyone",
        "nobody", "someone", "candidate", "applicant"
    })


def _is_valid_person_name(text: str, config: DecisionMakerRolesConfig | None = None) -> bool:
    """Validate that text looks like a real person name using generic rules:
    - Must not contain invalid characters ('|', '/', '\\', '@', etc.)
    - Must not end with sentence-ending punctuation ('.', '!', '?')
    - Must pass split_person_name(text)
    - Must not contain role, legal, generic, or marketing words loaded from config
    """
    if not text or not isinstance(text, str):
        return False
    clean = text.strip()
    if not clean:
        return False
    # Check invalid punctuation/delimiter chars (pipes, slashes, emails, symbols)
    if any(ch in clean for ch in _INVALID_NAME_CHARS):
        return False
    # Person names should not end with sentence-ending punctuation or quotes
    if clean.endswith((".", "!", "?", ";", ":", '"', "'", "”", "’")):
        return False
    # Must pass standard person name splitting (first + last name)
    split_res = split_person_name(clean)
    if not split_res:
        return False
    first, last = split_res

    # Check for marketing and generic words from config
    cfg = config or load_roles_config()
    marketing_words = _get_marketing_words(cfg)
    generic_words = _get_generic_words(cfg)

    # Check all individual tokens
    tokens = [w.lower().strip(".,'’\"()[]:") for w in re.split(r"[\s\-_]+", clean) if w.strip()]
    if any(w in marketing_words or w in generic_words for w in tokens):
        return False

    # Also verify the extracted first and last names themselves
    first_clean = first.lower().strip()
    last_clean = last.lower().strip()
    if first_clean in marketing_words or first_clean in generic_words:
        return False
    if last_clean in marketing_words or last_clean in generic_words:
        return False

    return True


def check_officer_context(
    cand: DecisionMakerCandidate,
    page_html: str,
    page_text: str,
    target_domain: str,
    config: DecisionMakerRolesConfig,
) -> tuple[str, str]:
    """Check surrounding context to determine if candidate is a genuine officer of target company.

    Flags rows whose context suggests the person is NOT an officer (advisor,
    investor, client, testimonial author, or leader at a different organization).
    Sets cand.excluded_reason when excluded and returns (status, reason).
    Does not drop rows.
    """
    cand.excluded_reason = ""

    # Unavailable candidates stay as-is
    if cand.verification_status == "unavailable":
        return "unavailable", cand.reason

    # JSON-LD persons explicitly defined under organization founders are verified by definition
    if cand.extraction_method == "jsonld_person" and cand.verification_status == "verified":
        return "verified", cand.reason

    # Check if the title explicitly names a different organization
    if title_names_other_org(cand.title, target_domain):
        msg = f"Title indicates role at another organization: {cand.title}"
        cand.excluded_reason = msg
        return "unverified", msg
    at_org_match = re.search(r"(?i)\b(?:at|@)\s+([A-Z][\w&.'’-]+)", cand.title or "")
    if at_org_match:
        org_name = at_org_match.group(1).lower()
        dom_stem = target_domain.split(".")[0].lower() if target_domain else ""
        if dom_stem and dom_stem not in org_name and org_name not in dom_stem:
            msg = f"Title indicates role at another organization: {cand.title}"
            cand.excluded_reason = msg
            return "unverified", msg

    # Next check DOM/HTML structure if page_html is available
    if page_html and cand.full_name:
        soup = BeautifulSoup(page_html, "html.parser")
        matching = soup.find_all(lambda el: el.name not in ["script", "style", "html", "body"] and cand.full_name in el.get_text())
        if matching:
            name_el = min(matching, key=lambda el: len(el.get_text()))
            curr = name_el
            is_team_container = False
            is_testimonial_container = False
            is_advisor_container = False

            while curr and curr != soup:
                tag_name = curr.name.lower() if curr.name else ""
                classes_and_id = " ".join(curr.get("class", [])) + " " + str(curr.get("id", ""))
                c_id_lower = classes_and_id.lower()

                if tag_name == "blockquote":
                    is_testimonial_container = True

                if any(w in c_id_lower for w in ["testimonial", "review", "feedback", "client-say", "client_story", "quote"]):
                    is_testimonial_container = True

                if any(w in c_id_lower for w in ["trusted-by", "trusted_by", "backed-by", "backed_by", "investor", "advisor", "advisory", "endorse", "partner"]):
                    is_advisor_container = True

                if any(w in c_id_lower for w in ["team", "leadership", "founder", "board-of-directors", "management"]):
                    is_team_container = True

                curr = curr.parent

            # Testimonial and external endorsement contexts take precedence
            if is_testimonial_container:
                msg = "Context indicates client testimonial author rather than company officer"
                cand.excluded_reason = msg
                return "unverified", msg

            if is_advisor_container:
                msg = "Context indicates advisor, investor, or external endorsement rather than company officer"
                cand.excluded_reason = msg
                return "unverified", msg

            if is_team_container:
                return cand.verification_status, cand.reason

            # Check closest preceding heading in the document
            name_pos = page_html.find(cand.full_name)
            if name_pos != -1:
                closest_h = None
                closest_h_pos = -1
                for h_tag in ["h1", "h2", "h3", "h4"]:
                    for match in re.finditer(rf"<{h_tag}[^>]*>(.*?)</{h_tag}>", page_html[:name_pos], re.IGNORECASE | re.DOTALL):
                        if match.start() > closest_h_pos:
                            closest_h_pos = match.start()
                            closest_h = re.sub(r"<[^>]+>", " ", match.group(1)).strip().lower()

                if closest_h:
                    if any(w in closest_h for w in ["team", "leadership", "founder", "management", "executive", "directors"]):
                        return cand.verification_status, cand.reason
                    if any(w in closest_h for w in ["testimonial", "review", "what our clients say", "what people say", "client stories", "feedback"]):
                        msg = "Context indicates client testimonial author rather than company officer"
                        cand.excluded_reason = msg
                        return "unverified", msg
                    if any(w in closest_h for w in ["trusted by", "backed by", "investor", "advisor", "advisory", "partners"]):
                        msg = "Context indicates advisor, investor, or external endorsement rather than company officer"
                        cand.excluded_reason = msg
                        return "unverified", msg

    # Fallback to text window search (500 chars before, 300 chars after)
    text_to_search = page_text or ""
    pos = -1
    if cand.evidence_text:
        pos = text_to_search.find(cand.evidence_text[:60])
    if pos == -1 and cand.full_name:
        pos = text_to_search.find(cand.full_name)

    if pos != -1:
        c_start = max(0, pos - 500)
        c_end = min(len(text_to_search), pos + len(cand.evidence_text) + 300)
        context_window = text_to_search[c_start:c_end]
        preceding_text = text_to_search[max(0, pos - 200):pos].lower()
    else:
        context_window = cand.evidence_text
        preceding_text = ""

    if re.search(r"\b(our\s+team|leadership\s+team|management\s+team|executive\s+team|board\s+of\s+directors)\b", preceding_text):
        return cand.verification_status, cand.reason

    patterns = config.external_context_patterns or {
        "testimonial": r"(?i)\b(testimonials?|reviews?|client\s+stories|what\s+(?:our\s+)?clients?\s+say|what\s+people\s+say|customer\s+feedback)\b|[“”]",
        "advisor_or_investor": r"(?i)\b(trusted\s+by|backed\s+by|investors?|advisors?|advisory\s+board|mentors?|portfolio\s+companies|our\s+partners)\b",
    }

    test_pat = patterns.get("testimonial", r"(?i)\b(testimonials?|reviews?|client\s+stories|what\s+(?:our\s+)?clients?\s+say|what\s+people\s+say|customer\s+feedback)\b|[“”]")
    adv_pat = patterns.get("advisor_or_investor", r"(?i)\b(trusted\s+by|backed\s+by|investors?|advisors?|advisory\s+board|mentors?|portfolio\s+companies|our\s+partners)\b")

    if re.search(test_pat, context_window):
        msg = "Context indicates client testimonial author rather than company officer"
        cand.excluded_reason = msg
        return "unverified", msg

    if re.search(adv_pat, context_window):
        msg = "Context indicates advisor, investor, or external endorsement rather than company officer"
        cand.excluded_reason = msg
        return "unverified", msg

    return cand.verification_status, cand.reason


def _is_verbatim_evidence(evidence: str, page_html: str, page_text: str) -> bool:
    """Verify that evidence_text is verbatim present in page_html or page_text."""
    norm_ev = _normalize_ws(evidence)
    if not norm_ev:
        return False
    norm_html = _normalize_ws(page_html)
    norm_text = _normalize_ws(page_text)
    return (norm_ev in norm_html) or (norm_ev in norm_text)


def categorize_title(title: str, config: DecisionMakerRolesConfig) -> str | None:
    """Map a job title to its role_category, excluding subordinates and former roles."""
    if not title:
        return None
    clean = _normalize_ws(title)
    if _FORMER.search(clean) or _SUBORDINATE.search(clean):
        return None

    low = clean.lower()
    # Check exact / word boundary matches against configured role mappings
    # Priority order: founder, ceo, cto, cfo, coo, cxo, director, owner, other_leadership
    priority_order = [
        "founder", "ceo", "cto", "cfo", "coo", "cxo",
        "director", "owner", "other_leadership"
    ]
    matched_categories: list[tuple[int, str]] = []

    for phrase, category in config.role_mappings.items():
        if re.search(rf"\b{re.escape(phrase)}\b", low, re.I):
            prio = priority_order.index(category) if category in priority_order else 99
            matched_categories.append((prio, category))

    if matched_categories:
        matched_categories.sort(key=lambda x: x[0])
        return matched_categories[0][1]

    if re.search(config.generic_cxo_regex, low):
        return "cxo"

    return None


def _clean_soup_for_people(soup: BeautifulSoup) -> None:
    """Remove boilerplate noise, scripts, styles, and forms."""
    for tag in soup(["script", "style", "noscript", "svg", "form"]):
        tag.decompose()


def extract_people_from_jsonld(
    raw_html: str,
    source_url: str,
    page_id: str,
    domain: str,
    config: DecisionMakerRolesConfig,
) -> list[DecisionMakerCandidate]:
    """Extract named persons with leadership titles from schema.org JSON-LD."""
    candidates: list[DecisionMakerCandidate] = []
    if not raw_html:
        return candidates

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
        expanded: list[dict] = []
        for it in items:
            if isinstance(it, dict):
                if "@graph" in it and isinstance(it["@graph"], list):
                    expanded.extend([g for g in it["@graph"] if isinstance(g, dict)])
                else:
                    expanded.append(it)

        for obj in expanded:
            # Check Person objects directly
            obj_type = obj.get("@type", "")
            is_person = (
                (isinstance(obj_type, str) and obj_type.lower() == "person")
                or (isinstance(obj_type, list) and any(str(t).lower() == "person" for t in obj_type))
            )
            if is_person:
                name = obj.get("name")
                title = obj.get("jobTitle") or obj.get("title")
                if isinstance(name, str) and isinstance(title, str):
                    name_clean = _normalize_ws(name)
                    title_clean = _normalize_ws(title)
                    if _is_valid_person_name(name_clean) and not title_names_other_org(title_clean, domain):
                        cat = categorize_title(title_clean, config)
                        if cat:
                            # Build verbatim evidence snippet from the script tag containing name and title
                            if name in text and title in text:
                                pn = text.find(name)
                                pt = text.find(title)
                                s_start = min(pn, pt)
                                s_end = max(pn + len(name), pt + len(title))
                                ev_snippet = text[s_start:s_end]
                                if len(ev_snippet) <= 300:
                                    ev = ev_snippet
                                    status = "verified"
                                    reason = ""
                                else:
                                    ev = ev_snippet[:300]
                                    status = "unverified"
                                    reason = "Name and title separated by more than 300 characters in JSON-LD"
                            else:
                                ev = f"{name_clean} {title_clean}"
                                status = "verified"
                                reason = ""

                            candidates.append(DecisionMakerCandidate(
                                full_name=name_clean,
                                title=title_clean,
                                role_category=cat,
                                source_url=source_url,
                                source_page_id=page_id,
                                evidence_text=ev,
                                confidence=config.confidence_scores.get("jsonld_person", 1.0),
                                verification_status=status,
                                extraction_method="jsonld_person",
                                reason=reason,
                            ))

            # Check Organization founder / employee / member
            matched_org = False
            if isinstance(obj_type, str) and obj_type.lower() in org_types:
                matched_org = True
            elif isinstance(obj_type, list) and any(str(t).lower() in org_types for t in obj_type):
                matched_org = True

            if matched_org:
                # Founders
                founders = obj.get("founder") or obj.get("founders")
                if founders:
                    f_list = founders if isinstance(founders, list) else [founders]
                    for f in f_list:
                        if isinstance(f, dict):
                            f_name = f.get("name")
                            f_title = f.get("jobTitle")
                            if isinstance(f_name, str) and _is_valid_person_name(f_name):
                                if f_title and isinstance(f_title, str):
                                    f_title_clean = _normalize_ws(f_title)
                                    if f_name in text and f_title in text:
                                        pn = text.find(f_name)
                                        pt = text.find(f_title)
                                        s_start = min(pn, pt)
                                        s_end = max(pn + len(f_name), pt + len(f_title))
                                        ev_snippet = text[s_start:s_end]
                                        if len(ev_snippet) <= 300:
                                            ev = ev_snippet
                                            v_status = "verified"
                                            v_reason = ""
                                        else:
                                            ev = ev_snippet[:300]
                                            v_status = "unverified"
                                            v_reason = "Name and title separated by >300 chars in JSON-LD"
                                    else:
                                        ev = f"{_normalize_ws(f_name)} {f_title_clean}"
                                        v_status = "verified"
                                        v_reason = ""
                                else:
                                    f_title_clean = "Founder"
                                    ev = f"{_normalize_ws(f_name)}"
                                    v_status = "unverified"
                                    v_reason = "Title not explicitly present in JSON-LD evidence"
                                candidates.append(DecisionMakerCandidate(
                                    full_name=_normalize_ws(f_name),
                                    title=f_title_clean,
                                    role_category="founder",
                                    source_url=source_url,
                                    source_page_id=page_id,
                                    evidence_text=ev,
                                    confidence=config.confidence_scores.get("jsonld_person", 1.0),
                                    verification_status=v_status,
                                    extraction_method="jsonld_person",
                                    reason=v_reason,
                                ))
                        elif isinstance(f, str) and _is_valid_person_name(f):
                            candidates.append(DecisionMakerCandidate(
                                full_name=_normalize_ws(f),
                                title="Founder",
                                role_category="founder",
                                source_url=source_url,
                                source_page_id=page_id,
                                evidence_text=_normalize_ws(f),
                                confidence=config.confidence_scores.get("jsonld_person", 1.0),
                                verification_status="unverified",
                                extraction_method="jsonld_person",
                                reason="Title not explicitly present in JSON-LD string evidence",
                            ))

    return candidates


def extract_people_from_cards_and_headings(
    raw_html: str,
    source_url: str,
    page_id: str,
    domain: str,
    config: DecisionMakerRolesConfig,
) -> list[DecisionMakerCandidate]:
    """Extract persons with leadership titles from HTML elements, cards, and heading lists."""
    candidates: list[DecisionMakerCandidate] = []
    if not raw_html:
        return candidates

    soup = BeautifulSoup(raw_html, "html.parser")
    _clean_soup_for_people(soup)

    # 1. Card & container-based detection
    # Common card containers: div, li, article, section
    for card in soup.find_all(["div", "li", "article", "section"]):
        # Discard overly massive containers to maintain adjacency
        card_text = _normalize_ws(card.get_text(separator=" "))
        if len(card_text) > 800 or len(card_text) < 5:
            continue

        # Find tags containing a valid person name
        candidate_name_tags = []
        for t in card.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "strong", "b", "p", "span"]):
            t_text = _normalize_ws(t.get_text(separator=" "))
            if _is_valid_person_name(t_text, config):
                candidate_name_tags.append((t, t_text))

        # If container has multiple names, it's a grid/container of multiple cards, not an individual card
        if len(candidate_name_tags) != 1:
            continue

        n_tag, n_text = candidate_name_tags[0]

        # Look for an adjacent title tag within the same container
        for t_tag in card.find_all(["p", "span", "div", "h4", "h5", "h6", "em", "small"]):
            if t_tag is n_tag:
                continue
            if n_tag in t_tag.descendants:
                continue
            raw_t = _normalize_ws(t_tag.get_text(separator=" "))
            if not raw_t or len(raw_t) > 80:
                continue

            # Invariant: title must NOT start with or contain person's name
            if raw_t.lower().startswith(n_text.lower()):
                cleaned_title = _normalize_ws(raw_t[len(n_text):].lstrip(" -–—|:,/"))
            else:
                cleaned_title = raw_t

            if not cleaned_title or len(cleaned_title) > 80:
                continue

            if _FORMER.search(cleaned_title) or _SUBORDINATE.search(cleaned_title) or title_names_other_org(cleaned_title, domain):
                continue

            cat = categorize_title(cleaned_title, config)
            if cat:
                # Find the smallest container holding both n_tag and t_tag
                smallest_container = None
                for parent in [n_tag.parent] + list(n_tag.parents):
                    if parent is None or parent == soup:
                        continue
                    if t_tag in parent.descendants or parent.find(lambda el: el is t_tag):
                        smallest_container = parent
                        break

                if smallest_container is not None:
                    container_text = _normalize_ws(smallest_container.get_text(separator=" "))
                else:
                    container_text = card_text

                pos_n = container_text.find(n_text)
                pos_t = container_text.find(cleaned_title)

                if pos_n != -1 and pos_t != -1:
                    span_start = min(pos_n, pos_t)
                    span_end = max(pos_n + len(n_text), pos_t + len(cleaned_title))
                    span_len = span_end - span_start

                    if span_len <= 300:
                        if len(container_text) <= 300:
                            ev = container_text
                        else:
                            margin = (300 - span_len) // 2
                            start_idx = max(0, span_start - margin)
                            ev = container_text[start_idx:start_idx + 300].strip()
                            if n_text not in ev or cleaned_title not in ev:
                                ev = container_text[span_start:span_end]

                        status = "verified"
                        reason = ""
                    else:
                        ev = container_text[:300]
                        status = "unverified"
                        reason = "Name and title separated by more than 300 characters in container"
                else:
                    ev = container_text[:300] if container_text else f"{n_text} {cleaned_title}"
                    status = "unverified"
                    reason = "Container does not contain both name and title verbatim"

                candidates.append(DecisionMakerCandidate(
                    full_name=n_text,
                    title=cleaned_title,
                    role_category=cat,
                    source_url=source_url,
                    source_page_id=page_id,
                    evidence_text=ev,
                    confidence=config.confidence_scores.get("card", 0.9),
                    verification_status=status,
                    extraction_method="card",
                    reason=reason,
                ))
                break

    # 2. Inline delimiter patterns: "Jane Doe - Chief Technology Officer" or "Jane Doe, CEO"
    delimiter_pattern = re.compile(
        r"^([A-Z][a-zA-Z\s\.\-]{2,40})\s*[\-–—|:]\s*([A-Za-z\s,\/]{3,60})$"
    )
    for tag in soup.find_all(["p", "li", "span", "h3", "h4"]):
        tag_text = _normalize_ws(tag.get_text(separator=" "))
        m = delimiter_pattern.match(tag_text)
        if m:
            cand_name = m.group(1).strip()
            cand_title = m.group(2).strip()
            if cand_title.lower().startswith(cand_name.lower()):
                cand_title = _normalize_ws(cand_title[len(cand_name):].lstrip(" -–—|:,/"))
            if _is_valid_person_name(cand_name, config) and not title_names_other_org(cand_title, domain):
                cat = categorize_title(cand_title, config)
                if cat:
                    ev = tag_text[:300]
                    if cand_name in ev and cand_title in ev:
                        status = "verified"
                        reason = ""
                    else:
                        status = "unverified"
                        reason = "Evidence text does not contain both name and title"

                    candidates.append(DecisionMakerCandidate(
                        full_name=cand_name,
                        title=cand_title,
                        role_category=cat,
                        source_url=source_url,
                        source_page_id=page_id,
                        evidence_text=ev,
                        confidence=config.confidence_scores.get("card", 0.9),
                        verification_status=status,
                        extraction_method="card",
                        reason=reason,
                    ))

    return candidates


def extract_people_from_prose(
    raw_html: str,
    raw_text: str,
    source_url: str,
    page_id: str,
    domain: str,
    config: DecisionMakerRolesConfig,
) -> list[DecisionMakerCandidate]:
    """Extract persons from prose patterns ("founded by X", "X is the CEO", "led by X")."""
    candidates: list[DecisionMakerCandidate] = []
    text_to_scan = raw_text or ""
    if not text_to_scan and raw_html:
        soup = BeautifulSoup(raw_html, "html.parser")
        _clean_soup_for_people(soup)
        text_to_scan = soup.get_text(separator=" ", strip=True)

    if not text_to_scan:
        return candidates

    # Split into sentences
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text_to_scan) if s.strip()]

    # Pattern: "founded by X", "co-founded by X", "established by X"
    founded_by_re = re.compile(
        r"\b(?:founded|co-?founded|established|started)\s+by\s+([A-Z][a-zA-Z\s\.\-]{2,40})(?:\s+in|\s+with|\s+to|\.|\,|\s+and|\s*$)",
        re.I
    )
    # Pattern: "X is the CEO", "X serves as CTO"
    is_role_re = re.compile(
        r"\b([A-Z][a-zA-Z\s\.\-]{2,40})\s+(?:is|serves as|acts as)\s+(?:the\s+)?([A-Za-z\s]{3,40})(?:\.|\,|\s+at|\s+of|\s*$)",
        re.I
    )
    # Pattern: "led by X, [title]"
    led_by_re = re.compile(
        r"\b(?:led|headed|directed)\s+by\s+([A-Z][a-zA-Z\s\.\-]{2,40})(?:,\s*([A-Za-z\s]{3,40}))?(?:\.|\,|\s+at|\s+of|\s*$)",
        re.I
    )
    # Pattern: "X, [title]" apposition
    apposition_re = re.compile(
        r"\b([A-Z][a-zA-Z\s\.\-]{2,40}),\s+([A-Za-z\s]{3,40})\b",
        re.I
    )

    for sent in sentences:
        if len(sent) > 300:
            continue

        # "founded by X" -> unverified sentence method (AC3)
        for m in founded_by_re.finditer(sent):
            cand_name = m.group(1).strip(" ,.-")
            if _is_valid_person_name(cand_name):
                candidates.append(DecisionMakerCandidate(
                    full_name=cand_name,
                    title="Founder",
                    role_category="founder",
                    source_url=source_url,
                    source_page_id=page_id,
                    evidence_text=sent[:200],
                    confidence=config.confidence_scores.get("sentence", 0.5),
                    verification_status="unverified",
                    extraction_method="sentence",
                ))

        # "X is the CEO"
        for m in is_role_re.finditer(sent):
            cand_name = m.group(1).strip(" ,.-")
            cand_title = m.group(2).strip(" ,.-")
            if _is_valid_person_name(cand_name) and not title_names_other_org(cand_title, domain):
                cat = categorize_title(cand_title, config)
                if cat:
                    candidates.append(DecisionMakerCandidate(
                        full_name=cand_name,
                        title=cand_title,
                        role_category=cat,
                        source_url=source_url,
                        source_page_id=page_id,
                        evidence_text=sent[:200],
                        confidence=config.confidence_scores.get("sentence", 0.5),
                        verification_status="unverified",
                        extraction_method="sentence",
                    ))

        # "led by X, President"
        for m in led_by_re.finditer(sent):
            cand_name = m.group(1).strip(" ,.-")
            cand_title = (m.group(2) or "").strip(" ,.-")
            if _is_valid_person_name(cand_name):
                t = cand_title or "Leader"
                cat = categorize_title(t, config) or "other_leadership"
                if not title_names_other_org(t, domain):
                    candidates.append(DecisionMakerCandidate(
                        full_name=cand_name,
                        title=t,
                        role_category=cat,
                        source_url=source_url,
                        source_page_id=page_id,
                        evidence_text=sent[:200],
                        confidence=config.confidence_scores.get("sentence", 0.5),
                        verification_status="unverified",
                        extraction_method="sentence",
                    ))

        # "X, [title]"
        for m in apposition_re.finditer(sent):
            cand_name = m.group(1).strip(" ,.-")
            cand_title = m.group(2).strip(" ,.-")
            if _is_valid_person_name(cand_name) and not title_names_other_org(cand_title, domain):
                cat = categorize_title(cand_title, config)
                if cat:
                    candidates.append(DecisionMakerCandidate(
                        full_name=cand_name,
                        title=cand_title,
                        role_category=cat,
                        source_url=source_url,
                        source_page_id=page_id,
                        evidence_text=sent[:200],
                        confidence=config.confidence_scores.get("sentence", 0.5),
                        verification_status="unverified",
                        extraction_method="sentence",
                    ))

    return candidates


async def find_decision_makers(
    run_id: str,
    state: StateManager | None = None,
    config_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Execute Milestone 3 decision-maker discovery for an existing research run.
    
    - Generates a new extraction_run_id for additive persistence.
    - Scans source_pages of type 'team', 'about', 'home'.
    - Validates verbatim evidence invariant for every finding.
    - Caps verification_status if company_websites verification_status != 'verified'.
    - Deduplicates by (normalized_name, role_category), retaining secondary URLs in also_seen_urls_json.
    - Produces a single 'unavailable' row if no decision-makers found.
    - Saves stage row 'decision_maker_discovery' in research_stages.
    - Zero writes to prospects, companies, outbox, or observations.
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

    roles_config = load_roles_config(config_path)
    extraction_run_id = _new_id()
    stage_id = _new_id()
    stage_start = _utcnow().isoformat()

    await st.save_research_stage(
        stage_id=stage_id,
        run_id=run_id,
        stage_name="decision_maker_discovery",
        status="running",
        started_at=stage_start,
        details_json=json.dumps({"extraction_run_id": extraction_run_id}),
    )

    # Filter pages to relevant types: team, about, home
    scannable_pages = [p for p in pages if p.get("page_type") in ("team", "about", "home", "services")]
    if not scannable_pages:
        scannable_pages = pages

    extracted_candidates: list[DecisionMakerCandidate] = []
    page_errors: list[dict[str, Any]] = []
    scanned_pages_count = 0
    js_rendered_pages: list[str] = []

    for p in scannable_pages:
        p_id = p.get("id", "")
        p_url = p.get("url", "")
        raw_html = p.get("raw_html", "") or ""
        raw_text = p.get("raw_text", "") or ""

        # Flag empty or JS-rendered pages (< 50 chars of readable text)
        if len(_normalize_ws(raw_text)) < 50 and len(_normalize_ws(raw_html)) < 300:
            js_rendered_pages.append(p_url)

        try:
            scanned_pages_count += 1
            page_candidates: list[DecisionMakerCandidate] = []

            if raw_html:
                # 1. JSON-LD Person
                page_candidates.extend(extract_people_from_jsonld(raw_html, p_url, p_id, target_domain, roles_config))
                # 2. Cards and list structures
                page_candidates.extend(extract_people_from_cards_and_headings(raw_html, p_url, p_id, target_domain, roles_config))

            # 3. Prose sentences (from text or html)
            page_candidates.extend(extract_people_from_prose(raw_html, raw_text, p_url, p_id, target_domain, roles_config))

            # Invariant check: title must not start with or contain full_name
            for cand in page_candidates:
                if cand.title and cand.full_name and cand.full_name != "unavailable":
                    if cand.title.lower().startswith(cand.full_name.lower()):
                        cand.title = _normalize_ws(cand.title[len(cand.full_name):].lstrip(" -–—|:,/"))

            # Verbatim evidence invariant check
            for cand in page_candidates:
                # Ensure evidence text is an actual substring of page HTML or text
                if _is_verbatim_evidence(cand.evidence_text, raw_html, raw_text):
                    # Strict rule: "verified" requires BOTH full_name AND title in evidence_text
                    if cand.verification_status == "verified":
                        norm_name = _normalize_ws(cand.full_name).lower()
                        norm_title = _normalize_ws(cand.title).lower()
                        norm_ev = _normalize_ws(cand.evidence_text).lower()
                        if norm_name not in norm_ev or norm_title not in norm_ev:
                            cand.verification_status = "unverified"
                            cand.reason = "Evidence text does not contain both name and title"

                    # Check surrounding context to verify officer role justification
                    v_stat, v_reason = check_officer_context(cand, raw_html, raw_text, target_domain, roles_config)
                    cand.verification_status = v_stat
                    if v_reason:
                        cand.reason = v_reason

                    extracted_candidates.append(cand)
                elif _is_verbatim_evidence(cand.full_name, raw_html, raw_text):
                    # Fall back to person's full name if card container was slightly wider, but cap at unverified
                    cand.evidence_text = cand.full_name
                    cand.verification_status = "unverified"
                    cand.reason = "Evidence container was wider than raw text; capped to unverified"

                    v_stat, v_reason = check_officer_context(cand, raw_html, raw_text, target_domain, roles_config)
                    cand.verification_status = v_stat
                    if v_reason:
                        cand.reason = v_reason

                    extracted_candidates.append(cand)
                else:
                    logger.debug("Discarded non-verbatim candidate %s on %s", cand.full_name, p_url)

        except Exception as e:
            logger.exception("Error scanning page %s (%s) for decision makers", p_id, p_url)
            page_errors.append({"page_id": p_id, "url": p_url, "error": str(e)})

    # Status capping: if website is not verified, cap all findings at unverified (AC9)
    for c in extracted_candidates:
        if site_status != "verified":
            c.verification_status = "unverified"

    # Deduplication by (normalized_name, role_category) (AC13)
    merged: dict[tuple[str, str], DecisionMakerCandidate] = {}
    for c in extracted_candidates:
        name_key = _normalize_ws(c.full_name).lower()
        key = (name_key, c.role_category)
        if key not in merged:
            merged[key] = c
        else:
            existing = merged[key]
            if c.source_url not in existing.also_seen_urls and c.source_url != existing.source_url:
                existing.also_seen_urls.append(c.source_url)
            # Retain higher confidence
            if c.confidence > existing.confidence:
                if existing.source_url not in c.also_seen_urls and existing.source_url != c.source_url:
                    c.also_seen_urls.append(existing.source_url)
                c.also_seen_urls.extend([u for u in existing.also_seen_urls if u != c.source_url])
                merged[key] = c

    final_people = list(merged.values())

    # Sort candidates by confidence desc, role priority
    role_priority = ["founder", "owner", "ceo", "cto", "cfo", "coo", "cxo", "director", "other_leadership"]
    final_people.sort(key=lambda p: (
        -p.confidence,
        role_priority.index(p.role_category) if p.role_category in role_priority else 99,
        p.full_name
    ))

    # Count real (non-excluded and not unavailable) decision makers
    real_decision_makers = [p for p in final_people if not getattr(p, "excluded_reason", "") and p.verification_status != "unavailable"]

    # If no decision makers found, record exactly ONE unavailable row with precise reason (AC7)
    if not real_decision_makers:
        if any(getattr(p, "excluded_reason", "") for p in final_people):
            reason_msg = "only testimonial/advisor-context names found"
        elif js_rendered_pages:
            reason_msg = f"{scanned_pages_count} pages scanned, no named leadership found (pages empty or client-side JavaScript rendered: {', '.join(js_rendered_pages[:2])})"
        elif scanned_pages_count == 0:
            reason_msg = "0 relevant source pages found for this domain"
        else:
            reason_msg = f"{scanned_pages_count} pages scanned, no named leadership found"

        final_people.append(DecisionMakerCandidate(
            full_name="unavailable",
            title="unavailable",
            role_category="other_leadership",
            source_url="",
            source_page_id="",
            evidence_text="",
            confidence=0.0,
            verification_status="unavailable",
            extraction_method="none",
            reason=reason_msg,
            excluded_reason="",
        ))

    # Persist to database
    for p in final_people:
        p_id = _new_id()
        await st.save_research_person(
            person_id=p_id,
            run_id=run_id,
            extraction_run_id=extraction_run_id,
            source_page_id=p.source_page_id,
            domain=target_domain,
            full_name=p.full_name,
            title=p.title,
            role_category=p.role_category,
            source_url=p.source_url,
            evidence_text=p.evidence_text,
            confidence=p.confidence,
            verification_status=p.verification_status,
            extraction_method=p.extraction_method,
            also_seen_urls_json=json.dumps(p.also_seen_urls),
            reason=p.reason,
            excluded_reason=getattr(p, "excluded_reason", ""),
        )

    stage_end = _utcnow().isoformat()
    stage_status = "completed"
    if page_errors:
        stage_status = "partial"

    # Category counts
    counts_by_category = {}
    for r in role_priority:
        cnt = sum(1 for p in final_people if p.role_category == r and p.verification_status != "unavailable" and not getattr(p, "excluded_reason", ""))
        if cnt > 0:
            counts_by_category[r] = cnt

    await st.save_research_stage(
        stage_id=stage_id,
        run_id=run_id,
        stage_name="decision_maker_discovery",
        status=stage_status,
        started_at=stage_start,
        finished_at=stage_end,
        error_message="" if not page_errors else f"{len(page_errors)} page scanning error(s)",
        details_json=json.dumps({
            "extraction_run_id": extraction_run_id,
            "counts": counts_by_category,
            "total_persisted": len(final_people),
            "scanned_pages_count": scanned_pages_count,
            "js_rendered_pages": js_rendered_pages,
            "errors": page_errors,
        }),
    )

    return await st.get_research_people(run_id, extraction_run_id=extraction_run_id)
