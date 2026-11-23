"""Entity cross-checking module for batch research.

Verifies whether an input company_name_hint matches website page content,
titles, or JSON-LD metadata.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_ENTITY_RULES: dict[str, list[str]] = {
    "stopwords": [
        "the", "of", "and", "&", "a", "an", "for", "in", "on", "at", "to", "by", "with",
    ],
    "legal_suffixes": [
        "inc", "incorporated", "llc", "l.l.c.", "corp", "corporation",
        "co", "company", "ltd", "limited", "lp", "llp", "pllc", "pc", "p.c.",
    ],
}


def load_entity_check_rules(config_path: str | Path | None = None) -> dict[str, list[str]]:
    """Load entity cross-check stopwords and legal suffixes from config."""
    p = Path(config_path or "config/entity_cross_check.json")
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("Failed to load entity check rules from %s: %s", p, e)
    return DEFAULT_ENTITY_RULES


def check_entity_match(
    company_name_hint: str,
    pages: list[dict[str, Any]] | None = None,
    website: dict[str, Any] | None = None,
    rules: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Cross-check company_name_hint against website pages, title, and metadata.
    
    Returns (status, evidence_snippet):
      - status: 'match', 'no_match', or 'no_hint'
      - evidence_snippet: ~100-200 char context snippet on match, or empty string.
    """
    hint = (company_name_hint or "").strip()
    if not hint:
        return ("no_hint", "")

    r = rules or load_entity_check_rules()
    stopwords = set(s.lower() for s in r.get("stopwords", []))
    legal_suffixes = set(s.lower().replace(".", "") for s in r.get("legal_suffixes", []))

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
    if website:
        for k in ("title", "company_name", "description"):
            v = website.get(k)
            if v and isinstance(v, str):
                corpus_parts.append(v)

    if pages:
        for p in pages:
            for k in ("title", "readable_text", "meta_description", "raw_html"):
                v = p.get(k)
                if v and isinstance(v, str):
                    corpus_parts.append(v)

    corpus = " \n ".join(corpus_parts)
    if not corpus.strip():
        return ("no_match", "")

    corpus_lower = corpus.lower()

    # 1. Check exact phrase of significant tokens
    sig_phrase = " ".join(sig_tokens)
    idx = corpus_lower.find(sig_phrase)
    if idx != -1:
        start = max(0, idx - 40)
        end = min(len(corpus), idx + len(sig_phrase) + 80)
        snippet = re.sub(r"\s+", " ", corpus[start:end]).strip()
        return ("match", snippet)

    # 2. Check if all significant tokens exist in corpus (or >= 80% if 3+ tokens)
    matched = [t for t in sig_tokens if re.search(rf"\b{re.escape(t)}\b", corpus_lower)]
    threshold = len(sig_tokens) if len(sig_tokens) <= 2 else max(2, int(0.75 * len(sig_tokens)))

    if len(matched) >= threshold:
        # Find location of first matched token for snippet
        first_token = matched[0]
        m = re.search(rf"\b{re.escape(first_token)}\b", corpus_lower)
        if m:
            start = max(0, m.start() - 40)
            end = min(len(corpus), m.end() + 100)
            snippet = re.sub(r"\s+", " ", corpus[start:end]).strip()
            return ("match", snippet)
        return ("match", f"Matched tokens: {', '.join(matched)}")

    return ("no_match", "")
