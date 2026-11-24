"""Entity cross-checking module for batch research.

Verifies whether an input company_name_hint matches website page content,
titles, or JSON-LD metadata according to generic rules:
- match: all significant tokens found in page corpus.
- partial_match: brand token (first significant token) equals domain SLD,
  appears in page title/JSON-LD organization name, or domain label equals
  an abbreviation built from prefixes (>=2 chars) of significant tokens.
  Softer warning note: 'domain appears to belong to a related/parent brand'.
- no_match: otherwise, with stronger filer/consultant warning.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_ENTITY_RULES: dict[str, Any] = {
    "stopwords": [
        "the", "of", "and", "&", "a", "an", "for", "in", "on", "at", "to", "by", "with",
    ],
    "legal_suffixes": [
        "inc", "incorporated", "llc", "l.l.c.", "corp", "corporation",
        "co", "company", "ltd", "limited", "lp", "llp", "pllc", "pc", "p.c.",
    ],
    "matching_rules": {
        "match": "all_significant_tokens_found",
        "partial_match_rules": [
            "brand_token_equals_domain_sld",
            "brand_token_in_page_title_or_jsonld_org",
            "domain_label_equals_prefix_abbreviation",
        ],
        "partial_match_note": "domain appears to belong to a related/parent brand",
        "no_match_note": "domain may belong to a filer, consultant or parent organisation",
    },
}


def load_entity_check_rules(config_path: str | Path | None = None) -> dict[str, Any]:
    """Load entity cross-check stopwords, legal suffixes, and matching rules from config."""
    p = Path(config_path or "config/entity_cross_check.json")
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("Failed to load entity check rules from %s: %s", p, e)
    return DEFAULT_ENTITY_RULES


def extract_domain_sld(domain_or_url: str | None) -> str:
    """Extract Second-Level Domain (SLD) or primary brand name from domain/URL."""
    if not domain_or_url:
        return ""
    clean = str(domain_or_url).lower().strip()
    if "://" in clean:
        from urllib.parse import urlparse
        clean = urlparse(clean).netloc or ""
    clean = clean.split(":")[0]
    if clean.startswith("www."):
        clean = clean[4:]
    parts = clean.split(".")
    if len(parts) >= 2:
        return parts[0]
    return clean


def check_entity_match(
    company_name_hint: str,
    pages: list[dict[str, Any]] | None = None,
    website: dict[str, Any] | None = None,
    rules: dict[str, Any] | None = None,
    domain: str | None = None,
) -> tuple[str, str]:
    """Cross-check company_name_hint against website pages, title, domain SLD, and metadata.
    
    Returns (status, evidence_snippet):
      - status: 'match', 'partial_match', 'no_match', or 'no_hint'
      - evidence_snippet: descriptive snippet on match/partial_match, or empty string on no_match.
    """
    hint = (company_name_hint or "").strip()
    if not hint:
        return ("no_hint", "")

    r = rules or load_entity_check_rules()
    stopwords = set(s.lower() for s in r.get("stopwords", []))
    legal_suffixes = set(s.lower().replace(".", "") for s in r.get("legal_suffixes", []))
    matching_cfg = r.get("matching_rules", {})
    partial_note = matching_cfg.get("partial_match_note", "domain appears to belong to a related/parent brand")

    # Tokenize hint into alphanumeric tokens
    tokens = [t.lower() for t in re.findall(r"[a-z0-9]+", hint.lower())]
    sig_tokens = [
        t for t in tokens
        if t not in stopwords and t.replace(".", "") not in legal_suffixes and len(t) >= 2
    ]

    if not sig_tokens:
        return ("no_hint", "")

    # Build corpus from website title/description and page texts
    corpus_parts: list[str] = []
    page_titles: list[str] = []
    json_ld_orgs: list[str] = []

    if website:
        for k in ("title", "company_name", "description"):
            v = website.get(k)
            if v and isinstance(v, str):
                corpus_parts.append(v)
                if k in ("title", "company_name"):
                    page_titles.append(v)

    if pages:
        for p in pages:
            for k in ("title", "readable_text", "meta_description", "raw_html"):
                v = p.get(k)
                if v and isinstance(v, str):
                    corpus_parts.append(v)
                    if k == "title":
                        page_titles.append(v)
            raw = p.get("raw_html", "")
            if raw and "application/ld+json" in raw:
                try:
                    for ld_match in re.finditer(r'<script[^>]*type=[\'"]application/ld\+json[\'"][^>]*>(.*?)</script>', raw, re.DOTALL | re.IGNORECASE):
                        ld_data = json.loads(ld_match.group(1))
                        if isinstance(ld_data, dict):
                            org = ld_data.get("name") or ld_data.get("legalName")
                            if org and isinstance(org, str):
                                json_ld_orgs.append(org)
                except Exception:
                    pass

    corpus = " \n ".join(corpus_parts)
    corpus_lower = corpus.lower()
    title_meta_corpus = " ".join(page_titles + json_ld_orgs).lower()

    # 1. Check exact phrase of significant tokens in corpus
    sig_phrase = " ".join(sig_tokens)
    idx = corpus_lower.find(sig_phrase)
    if idx != -1:
        start = max(0, idx - 40)
        end = min(len(corpus), idx + len(sig_phrase) + 80)
        snippet = re.sub(r"\s+", " ", corpus[start:end]).strip()
        return ("match", snippet)

    # 2. Check if all significant tokens exist in corpus
    all_matched = all(re.search(rf"\b{re.escape(t)}\b", corpus_lower) for t in sig_tokens)
    if all_matched and corpus.strip():
        first_token = sig_tokens[0]
        m = re.search(rf"\b{re.escape(first_token)}\b", corpus_lower)
        if m:
            start = max(0, m.start() - 40)
            end = min(len(corpus), m.end() + 100)
            snippet = re.sub(r"\s+", " ", corpus[start:end]).strip()
            return ("match", snippet)
        return ("match", f"All entity tokens {sig_tokens} verified on website")

    # 3. Check Domain SLD against significant tokens (brand/parent relationship)
    dom_candidate = domain or ""
    if not dom_candidate and website:
        dom_candidate = website.get("domain") or website.get("final_url") or ""
    sld = extract_domain_sld(dom_candidate)
    if sld:
        if sld in sig_tokens:
            return ("match", f"Domain SLD '{sld}' matches entity brand token '{sld}' (brand/parent relationship)")
        for tok in sig_tokens:
            if len(tok) >= 3 and len(sld) >= 3 and (tok == sld or (len(tok) >= 4 and (tok in sld or sld in tok))):
                return ("match", f"Domain SLD '{sld}' matches entity brand token '{tok}' (brand/parent relationship)")

    # 4. If 2 tokens (e.g. Beacon Telecom) and primary brand token (Beacon) matched in corpus
    matched = [t for t in sig_tokens if re.search(rf"\b{re.escape(t)}\b", corpus_lower)]
    if len(sig_tokens) == 2 and len(matched) >= 1:
        primary_token = sig_tokens[0]
        if primary_token in matched and len(primary_token) >= 3:
            m = re.search(rf"\b{re.escape(primary_token)}\b", corpus_lower)
            if m:
                start = max(0, m.start() - 40)
                end = min(len(corpus), m.end() + 100)
                snippet = re.sub(r"\s+", " ", corpus[start:end]).strip()
                return ("match", snippet)

    # 5. Partial Match Rules
    brand_token = sig_tokens[0]

    # (a) Domain label equals prefix abbreviation of significant tokens (e.g. fedex for Federal Express)
    if sld and len(sig_tokens) >= 2:
        prefix_combos = [
            sig_tokens[0][:3] + sig_tokens[1][:2],  # fed + ex
            sig_tokens[0][:2] + sig_tokens[1][:2],  # fe + ex
            sig_tokens[0][:3] + sig_tokens[1][:3],  # fed + exp
            "".join(t[0] for t in sig_tokens),       # acronym e.g. bnsf
        ]
        if sld in prefix_combos:
            return ("partial_match", f"Domain label '{sld}' matches prefix abbreviation of '{hint}'; {partial_note}.")

    # (b) Brand token appears in page title or JSON-LD organization name
    if title_meta_corpus and re.search(rf"\b{re.escape(brand_token)}\b", title_meta_corpus):
        return ("partial_match", f"Primary brand token '{brand_token}' confirmed in title/metadata; {partial_note}.")

    # 6. No Match
    return ("no_match", "")
