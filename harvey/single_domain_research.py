"""Single-Domain Research Foundation.

Implements Milestone 1 and 1.1 pipeline:
1. Email validation & normalization (RFC 5322 syntax, free/consumer mail detection from config, optional MX check).
2. Domain extraction (normalization, IDN handling, free-mail skip).
3. Website verification (SSRF-guarded DNS resolution, HTTPS/HTTP probe, redirect tracking, registrable domain comparison).
4. Page discovery (whole URL path segment classification, signals/exclusions from config, generic locale stripping, depth <= 2 preference, nav/footer priority, sitemap.xml fallback, honest 'no about/team page found' reporting).
5. Page fetching (SSRF protection via public_http, robots.txt politeness, readable text extraction, SHA-256 hash, independent error handling).
6. Progressive SQLite persistence via StateManager across all stages.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import re
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
import idna
from bs4 import BeautifulSoup

from harvey.integrations.email_finder import validate_email_syntax
from harvey.public_http import UnsafeURLError, public_http_client
from harvey.state import StateManager, _new_id, _utcnow

logger = logging.getLogger(__name__)

# Common multi-part country code second-level domains
_TWO_PART_TLDS = frozenset({
    "co.uk", "org.uk", "gov.uk", "ac.uk", "me.uk", "ltd.uk", "plc.uk",
    "co.in", "net.in", "org.in", "gen.in", "firm.in", "ind.in",
    "com.au", "net.au", "org.au", "edu.au", "gov.au",
    "co.nz", "net.nz", "org.nz", "govt.nz",
    "com.br", "org.br", "net.br", "gov.br",
    "co.jp", "ne.jp", "or.jp", "go.jp",
    "co.za", "org.za", "net.za", "gov.za",
    "com.sg", "edu.sg", "gov.sg", "net.sg",
    "com.mx", "org.mx", "edu.mx", "gob.mx",
    "com.ar", "org.ar", "net.ar", "gov.ar",
    "com.tr", "org.tr", "net.tr", "gov.tr",
    "co.kr", "ne.kr", "re.kr", "go.kr",
})

_CACHED_PUBLIC_SUFFIXES: set[str] | None = None


def load_public_suffixes(config_path: str | Path | None = None) -> set[str]:
    """Load multi-part public suffixes from configuration file."""
    paths_to_check: list[Path] = []
    if config_path:
        paths_to_check.append(Path(config_path))
    env_path = os.environ.get("PUBLIC_SUFFIXES_FILE")
    if env_path:
        paths_to_check.append(Path(env_path))

    project_root = Path(__file__).resolve().parent.parent
    paths_to_check.extend([
        project_root / "config" / "public_suffixes.txt",
        Path("config/public_suffixes.txt"),
    ])

    for p in paths_to_check:
        if p.exists() and p.is_file():
            try:
                suffixes = set()
                for line in p.read_text(encoding="utf-8").splitlines():
                    line = line.strip().lower()
                    if line and not line.startswith("#"):
                        suffixes.add(line)
                if suffixes:
                    return suffixes
            except Exception as e:
                logger.warning("Failed to load public suffixes from %s: %s", p, e)

    return set(_TWO_PART_TLDS)


def get_registrable_domain(host: str, suffixes: set[str] | None = None) -> str:
    """Extract the registrable domain (domain + public suffix) from a hostname or URL.
    
    Examples:
        sub.example.com -> example.com
        https://example.com -> example.com
        www.example.co.uk -> example.co.uk
        a.b.example.co.in -> example.co.in
        example.com:8080 -> example.com
    """
    global _CACHED_PUBLIC_SUFFIXES
    if suffixes is None:
        if _CACHED_PUBLIC_SUFFIXES is None:
            _CACHED_PUBLIC_SUFFIXES = load_public_suffixes()
        sfx_set = _CACHED_PUBLIC_SUFFIXES
    else:
        sfx_set = suffixes

    clean = (host or "").strip().lower()
    clean = re.sub(r"^https?://", "", clean)
    clean = clean.split("/")[0].split(":")[0].strip(".")
    if clean.startswith("www."):
        clean = clean[4:]

    parts = clean.split(".")
    if len(parts) <= 2:
        return clean

    # Check multi-part suffix candidates (e.g. co.uk, com.au, co.in)
    for part_count in (3, 2):
        if len(parts) > part_count:
            cand = ".".join(parts[-part_count:])
            if cand in sfx_set:
                return f"{parts[-part_count - 1]}.{cand}"

    return f"{parts[-2]}.{parts[-1]}"


def is_same_registrable_domain(host_a: str, host_b: str, suffixes: set[str] | None = None) -> bool:
    """Check if two hosts share the same registrable domain or www alias."""
    reg_a = get_registrable_domain(host_a, suffixes=suffixes)
    reg_b = get_registrable_domain(host_b, suffixes=suffixes)
    if not reg_a or not reg_b:
        return False
    return reg_a == reg_b


def load_free_email_providers(config_path: str | Path | None = None) -> set[str]:
    """Load consumer/free email domains from a configuration file.
    
    Does NOT hardcode domains inside application logic.
    """
    paths_to_check: list[Path] = []
    if config_path:
        paths_to_check.append(Path(config_path))
    
    env_path = os.environ.get("FREE_EMAIL_PROVIDERS_FILE")
    if env_path:
        paths_to_check.append(Path(env_path))
    
    # Standard location in workspace
    project_root = Path(__file__).resolve().parent.parent
    paths_to_check.extend([
        project_root / "config" / "free_email_providers.txt",
        project_root / "data" / "free_email_providers.txt",
        Path("config/free_email_providers.txt"),
        Path("data/free_email_providers.txt"),
    ])
    
    for p in paths_to_check:
        if p.exists() and p.is_file():
            try:
                domains = set()
                for line in p.read_text(encoding="utf-8").splitlines():
                    line = line.strip().lower()
                    if line and not line.startswith("#"):
                        domains.add(line)
                if domains:
                    return domains
            except Exception as e:
                logger.warning("Failed to load free email providers from %s: %s", p, e)
    
    return set()


@dataclass
class PageSignalsConfig:
    """Configured URL path segment signals and exclusion list."""
    about_signals: set[str] = field(default_factory=lambda: {
        "about", "about-us", "company", "who-we-are", "our-story", "public-about",
    })
    team_signals: set[str] = field(default_factory=lambda: {
        "team", "our-team", "leadership", "management", "management-team",
        "people", "board", "founders", "team-broken", "private-team",
    })
    services_signals: set[str] = field(default_factory=lambda: {
        "services", "products", "solutions", "what-we-do", "offerings", "platform",
    })
    contact_signals: set[str] = field(default_factory=lambda: {
        "contact", "contact-us",
    })
    exclude_segments: set[str] = field(default_factory=lambda: {
        "customers", "customer-stories", "case-studies", "guides", "blog",
        "news", "press", "resources", "docs", "pricing", "careers", "jobs",
        "legal", "privacy", "terms", "support", "help",
    })


def load_page_signals(config_path: str | Path | None = None) -> PageSignalsConfig:
    """Load whole path segment signals and exclusions from config file."""
    paths_to_check: list[Path] = []
    if config_path:
        paths_to_check.append(Path(config_path))

    env_path = os.environ.get("RESEARCH_PAGE_SIGNALS_FILE")
    if env_path:
        paths_to_check.append(Path(env_path))

    project_root = Path(__file__).resolve().parent.parent
    paths_to_check.extend([
        project_root / "config" / "research_page_signals.json",
        Path("config/research_page_signals.json"),
    ])

    for p in paths_to_check:
        if p.exists() and p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                return PageSignalsConfig(
                    about_signals=set(data.get("about_signals", [])),
                    team_signals=set(data.get("team_signals", [])),
                    services_signals=set(data.get("services_signals", [
                        "services", "products", "solutions", "what-we-do", "offerings", "platform",
                    ])),
                    contact_signals=set(data.get("contact_signals", [])),
                    exclude_segments=set(data.get("exclude_segments", [])),
                )
            except Exception as e:
                logger.warning("Failed to load research page signals from %s: %s", p, e)

    return PageSignalsConfig()


@dataclass
class ResearchConfig:
    """Politeness and execution settings for single-domain research."""
    max_pages: int = 8
    timeout_seconds: float = 10.0
    min_interval_seconds: float = 1.0  # At most 1 request/second per domain
    max_retries: int = 2
    max_redirects: int = 5
    user_agent: str = "HarveySalesAgent/1.0 (open-source sales agent)"
    free_providers_file: str | None = None
    page_signals_file: str | None = None
    public_suffixes_file: str | None = None


class DomainRateLimiter:
    """Ensures at most 1 request per second per target domain."""

    def __init__(self, min_interval_seconds: float = 1.0):
        self.min_interval = min_interval_seconds
        self._last_request: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def wait(self, domain: str):
        if self.min_interval <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            last = self._last_request.get(domain, 0.0)
            elapsed = now - last
            if elapsed < self.min_interval:
                sleep_time = self.min_interval - elapsed
                await asyncio.sleep(sleep_time)
            self._last_request[domain] = time.monotonic()


# ── SSRF Utilities ────────────────────────────────────────────────────

_TRANSLATION_NETWORKS = (
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("64:ff9b:1::/48"),
)


def _is_private_or_loopback_ip(ip_str: str) -> bool:
    """Check if an IP string is loopback, private, link-local, or cloud metadata.
    
    Accepts public IPv4 addresses embedded in NAT64 translation networks.
    """
    try:
        addr = ipaddress.ip_address(ip_str)
        if isinstance(addr, ipaddress.IPv6Address):
            for network in _TRANSLATION_NETWORKS:
                if addr in network:
                    embedded_v4 = ipaddress.ip_address(addr.packed[-4:])
                    return (
                        not embedded_v4.is_global
                        or embedded_v4.is_private
                        or embedded_v4.is_loopback
                        or embedded_v4.is_link_local
                        or embedded_v4.is_reserved
                        or embedded_v4.is_multicast
                        or embedded_v4.is_unspecified
                    )
            if addr.is_site_local or addr.ipv4_mapped or addr.sixtofour or addr.teredo:
                return True
        if not addr.is_global or addr.is_loopback or addr.is_private or addr.is_link_local or addr.is_reserved or addr.is_unspecified:
            return True
        return False
    except ValueError:
        return False


def _check_host_ssrf_safety(host: str) -> tuple[bool, str]:
    """Pre-flight safety check against loopback, private IPs, or localhost."""
    clean = (host or "").split(":")[0].strip().lower().rstrip(".")
    if clean in ("localhost", "127.0.0.1", "::1") or clean.endswith(".localhost"):
        return False, f"SSRF blocked: loopback or localhost destination ({clean})"
    if _is_private_or_loopback_ip(clean):
        return False, f"SSRF blocked: non-public IP literal ({clean})"
    return True, ""


# ── Stage 1: Email Validation ─────────────────────────────────────────

@dataclass
class EmailValidationResult:
    valid_syntax: bool
    normalized_email: str
    domain: str
    is_free_mail: bool
    mx_info: dict[str, Any] | None = None
    error: str = ""


def validate_and_normalize_email(
    email: str,
    free_mail_domains: set[str],
    lookup_mx: bool = True,
) -> EmailValidationResult:
    """Validate email syntax (RFC 5322), check consumer mail list, and inspect MX."""
    raw = (email or "").strip()
    if not raw or not validate_email_syntax(raw):
        return EmailValidationResult(
            valid_syntax=False,
            normalized_email=raw.lower(),
            domain="",
            is_free_mail=False,
            error="Invalid RFC 5322 email syntax",
        )
    
    normalized = raw.lower()
    if "@" not in normalized:
        return EmailValidationResult(
            valid_syntax=False,
            normalized_email=normalized,
            domain="",
            is_free_mail=False,
            error="Invalid RFC 5322 email syntax",
        )
    
    local, _, raw_domain = normalized.partition("@")
    raw_domain = raw_domain.strip().lower()
    
    # Check consumer / free mail list from config
    is_free = raw_domain in free_mail_domains or any(
        raw_domain.endswith("." + d) for d in free_mail_domains
    )
    
    # Optional DNS MX resolution (informational only; never probe SMTP)
    mx_info = _lookup_mx_records(raw_domain) if lookup_mx else None
    
    return EmailValidationResult(
        valid_syntax=True,
        normalized_email=normalized,
        domain=raw_domain,
        is_free_mail=is_free,
        mx_info=mx_info,
    )


def _lookup_mx_records(domain: str) -> dict[str, Any] | None:
    """Perform informational DNS MX lookup using dnspython or socket."""
    try:
        import dns.resolver
        answers = dns.resolver.resolve(domain, "MX", lifetime=3.0)
        records = sorted([(r.preference, str(r.exchange).rstrip(".")) for r in answers])
        if records:
            return {
                "status": "found",
                "primary_mx": records[0][1],
                "preference": records[0][0],
                "count": len(records),
            }
        return {"status": "none_found", "count": 0, "reason": f"No MX records returned for {domain}"}
    except Exception as e:
        return {"status": "none_found", "count": 0, "reason": f"DNS MX lookup failed for {domain}: {type(e).__name__}"}


# ── Stage 2: Domain Extraction ────────────────────────────────────────

@dataclass
class DomainExtractionResult:
    status: str  # "completed" | "skipped: not a company domain" | "failed"
    domain: str
    error: str = ""


def extract_domain(val_result: EmailValidationResult) -> DomainExtractionResult:
    """Extract and normalize the company domain from a validated email."""
    if not val_result.valid_syntax:
        return DomainExtractionResult(
            status="failed",
            domain="",
            error="Cannot extract domain from invalid email",
        )
    
    if val_result.is_free_mail:
        return DomainExtractionResult(
            status="skipped: not a company domain",
            domain="",
            error="Skipped: consumer email provider domain",
        )
    
    raw = val_result.domain
    try:
        # Handle internationalized domain names (IDN) safely
        ascii_domain = idna.encode(raw).decode("ascii").lower().strip()
        return DomainExtractionResult(
            status="completed",
            domain=ascii_domain,
        )
    except Exception as e:
        return DomainExtractionResult(
            status="failed",
            domain=raw,
            error=f"IDN encoding failed for domain: {e}",
        )


# ── Stage 3: Website Verification ─────────────────────────────────────

@dataclass
class WebsiteVerificationResult:
    dns_resolved: bool
    candidate_url: str
    final_url: str
    http_status: int
    verification_status: str  # "verified" | "unverified" | "unavailable"
    confidence: float
    evidence_text: str
    html_content: str = ""
    content_type: str = ""


async def verify_website(
    domain: str,
    client: httpx.AsyncClient,
    config: ResearchConfig,
    rate_limiter: DomainRateLimiter,
) -> WebsiteVerificationResult:
    """Verify company website via DNS and direct HTTP probe with SSRF boundary."""
    if not domain or "." not in domain:
        return WebsiteVerificationResult(
            dns_resolved=False,
            candidate_url="",
            final_url="",
            http_status=0,
            verification_status="unavailable",
            confidence=0.0,
            evidence_text="Invalid domain string",
        )

    # Pre-flight SSRF check on hostname/literal
    safe, ssrf_err = _check_host_ssrf_safety(domain)
    if not safe:
        return WebsiteVerificationResult(
            dns_resolved=False,
            candidate_url=f"https://{domain}",
            final_url="",
            http_status=0,
            verification_status="unavailable",
            confidence=0.0,
            evidence_text=ssrf_err,
        )

    # 1. DNS Resolution check (with SSRF guard and apex/www fallback)
    dns_res = await _check_dns(domain)
    if isinstance(dns_res, (tuple, list)) and len(dns_res) >= 3:
        dns_ok, dns_err_code, dns_err_detail = dns_res[0], dns_res[1], dns_res[2]
        dns_err = f"{dns_err_code}: {dns_err_detail}".strip(": ")
    elif isinstance(dns_res, (tuple, list)) and len(dns_res) == 2:
        dns_ok, dns_err = dns_res[0], dns_res[1]
    else:
        dns_ok, dns_err = False, str(dns_res)

    if not dns_ok:
        return WebsiteVerificationResult(
            dns_resolved=False,
            candidate_url=f"https://{domain}",
            final_url="",
            http_status=0,
            verification_status="unavailable",
            confidence=0.0,
            evidence_text=f"DNS resolution failure: {dns_err}",
        )

    # 2. HTTP Probing: Try candidates in order
    candidates = [
        f"https://{domain}",
        f"https://www.{domain}",
        f"http://{domain}",
        f"http://www.{domain}",
    ]

    last_error_code = "unavailable"
    last_error_detail = ""
    last_status = 0
    headers = {"User-Agent": config.user_agent}

    for cand_url in candidates:
        await rate_limiter.wait(domain)
        try:
            resp = await client.get(
                cand_url,
                headers=headers,
                timeout=config.timeout_seconds,
                follow_redirects=True,
            )
            final_url = str(resp.url)
            final_host = urlparse(final_url).netloc.split(":")[0].lower()
            http_status = resp.status_code
            content_type = resp.headers.get("content-type", "").lower()
            text_preview = resp.text[:2000] if "text/html" in content_type else ""

            # Classify: HTTP 2xx
            if 200 <= http_status < 300:
                # Check registrable domain alignment
                if is_same_registrable_domain(domain, final_host):
                    if "text/html" in content_type and len(text_preview.strip()) >= 50:
                        return WebsiteVerificationResult(
                            dns_resolved=True,
                            candidate_url=cand_url,
                            final_url=final_url,
                            http_status=http_status,
                            verification_status="verified",
                            confidence=1.0,
                            evidence_text=f"Reachable via HTTP {http_status}, same registrable domain ({final_host}), HTML content verified.",
                            html_content=resp.text,
                            content_type=content_type,
                        )
                    else:
                        return WebsiteVerificationResult(
                            dns_resolved=True,
                            candidate_url=cand_url,
                            final_url=final_url,
                            http_status=http_status,
                            verification_status="unverified",
                            confidence=0.3,
                            evidence_text=f"Reachable ({http_status}) but ambiguous content: content-type='{content_type}', length={len(text_preview)} chars.",
                            html_content=resp.text,
                            content_type=content_type,
                        )
                else:
                    return WebsiteVerificationResult(
                        dns_resolved=True,
                        candidate_url=cand_url,
                        final_url=final_url,
                        http_status=http_status,
                        verification_status="unverified",
                        confidence=0.2,
                        evidence_text=f"Redirects to a different registrable domain: '{final_host}' (original: '{domain}').",
                        html_content=resp.text,
                        content_type=content_type,
                    )
            elif 400 <= http_status < 600:
                last_status = http_status
                text_lower = resp.text[:4000].lower()
                is_bot = (http_status == 403 and any(
                    m in text_lower for m in (
                        "just a moment", "attention required", "cloudflare",
                        "bot verification", "access denied", "ddos-guard", "cf-mitigated", "captcha"
                    )
                )) or "cf-ray" in resp.headers or "cf-mitigated" in resp.headers
                if is_bot or http_status == 403:
                    last_error_code = "bot_challenge" if is_bot else "http_403"
                    last_error_detail = f"HTTP {http_status} (Cloudflare/Bot challenge)" if is_bot else f"HTTP {http_status} Forbidden"
                    if is_bot:
                        logger.info("Bot challenge / Cloudflare detected on %s; stopping further URL candidate retries.", cand_url)
                        break
                else:
                    last_error_code = f"http_{http_status}"
                    last_error_detail = f"HTTP {http_status}"
                continue

        except UnsafeURLError as e:
            return WebsiteVerificationResult(
                dns_resolved=False,
                candidate_url=cand_url,
                final_url="",
                http_status=0,
                verification_status="unavailable",
                confidence=0.0,
                evidence_text=f"SSRF blocked (blocked_ssrf): {e}",
            )
        except httpx.ConnectTimeout as e:
            last_error_code = "timeout"
            last_error_detail = f"Connection timeout: {e}"
            continue
        except httpx.ReadTimeout as e:
            last_error_code = "timeout"
            last_error_detail = f"Read timeout: {e}"
            continue
        except httpx.ConnectError as e:
            err_str = str(e)
            if "ssl" in err_str.lower() or "certificate" in err_str.lower():
                last_error_code = "tls_error"
                last_error_detail = f"TLS error: {e}"
            else:
                last_error_code = "timeout" if "timed out" in err_str.lower() else "unavailable"
                last_error_detail = f"Connect error: {e}"
            continue
        except httpx.RequestError as e:
            err_str = str(e)
            if "ssl" in err_str.lower() or "certificate" in err_str.lower():
                last_error_code = "tls_error"
                last_error_detail = f"TLS error: {e}"
            elif "timed out" in err_str.lower():
                last_error_code = "timeout"
                last_error_detail = f"Timeout: {e}"
            else:
                last_error_code = "unavailable"
                last_error_detail = f"{type(e).__name__}: {e}"
            continue

    # If all candidates exhausted without 2xx
    return WebsiteVerificationResult(
        dns_resolved=True,
        candidate_url=candidates[0],
        final_url="",
        http_status=last_status,
        verification_status="unavailable",
        confidence=0.0,
        evidence_text=f"{last_error_code}: {last_error_detail or 'All candidate URLs failed'}",
    )


async def _check_dns(domain: str) -> tuple[bool, str, str]:
    """Asynchronously verify domain DNS resolution with SSRF validation.
    
    Returns (ok, error_code, error_detail).
    error_code is one of: "", "dns_nxdomain", "blocked_ssrf", "timeout".
    """
    clean = domain.split(":")[0].strip().lower().rstrip(".")
    safe, err = _check_host_ssrf_safety(clean)
    if not safe:
        return False, "blocked_ssrf", err

    loop = asyncio.get_running_loop()
    hosts_to_try = [clean]
    if not clean.startswith("www."):
        hosts_to_try.append(f"www.{clean}")

    last_error_code = "dns_nxdomain"
    last_error_detail = ""

    for host in hosts_to_try:
        try:
            answers = await asyncio.wait_for(
                loop.getaddrinfo(host, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM),
                timeout=5.0,
            )
            if not answers:
                last_error_code = "dns_nxdomain"
                last_error_detail = f"DNS returned no addresses for {host}"
                continue

            public_ips = []
            has_private_ip = False
            for row in answers:
                ip_str = row[4][0]
                if _is_private_or_loopback_ip(ip_str):
                    has_private_ip = True
                else:
                    public_ips.append(ip_str)

            if public_ips:
                return True, "", ""
            elif has_private_ip:
                last_error_code = "blocked_ssrf"
                last_error_detail = f"SSRF blocked: {host} resolves only to non-public destinations"
            else:
                last_error_code = "dns_nxdomain"
                last_error_detail = f"No public IP addresses for {host}"
        except asyncio.TimeoutError:
            last_error_code = "timeout"
            last_error_detail = f"DNS resolution timed out for {host}"
        except socket.gaierror as e:
            last_error_code = "dns_nxdomain"
            last_error_detail = f"DNS NXDOMAIN / lookup error for {host}: {e}"
        except Exception as e:
            last_error_code = "dns_nxdomain"
            last_error_detail = str(e)

    return False, last_error_code, last_error_detail


# ── Stage 4: Page Discovery ───────────────────────────────────────────

@dataclass
class DiscoveredPageTarget:
    url: str
    page_type: str  # "home" | "about" | "team" | "leadership" | "contact" | "other"
    title: str = ""


_ASSET_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico",
    ".pdf", ".zip", ".tar", ".gz", ".doc", ".docx", ".xls", ".xlsx",
    ".css", ".js", ".json", ".xml", ".mp4", ".mp3", ".avi",
)


def strip_locale_prefix(path: str) -> tuple[str, str]:
    """Generic locale prefix handler (e.g., /in/, /en-us/, /fr-ca/, /de/)."""
    m = re.match(r"^/([a-z]{2}(?:[-_][a-z0-9]{2,4})?)(?:/|$)", path, re.I)
    if m:
        locale = m.group(1).lower()
        remainder = path[len(m.group(0)) - 1:]
        return locale, remainder
    return "", path


def get_path_segments(path: str) -> list[str]:
    """Extract clean URL path segments after any locale prefix, removing web file extensions."""
    _, remaining = strip_locale_prefix(path)
    raw_segments = [s.strip().lower() for s in remaining.split("/") if s.strip()]
    if not raw_segments:
        return []
    raw_segments[-1] = re.sub(r"\.(html?|php|aspx?|jsp)$", "", raw_segments[-1], flags=re.I)
    return raw_segments


def classify_page_type(url: str, signals: PageSignalsConfig) -> tuple[str, bool, int]:
    """Classify page_type by exact path-segment equality against config lists.
    
    Priority: team, services, about, contact
    Returns:
        (page_type, is_excluded, depth)
    """
    parsed = urlparse(url)
    segments = get_path_segments(parsed.path)
    if not segments:
        return ("home", False, 0)

    # Check if any segment is in exclude_segments
    for seg in segments:
        if seg in signals.exclude_segments:
            return ("other", True, len(segments))

    depth = len(segments)

    # Check whole path segments by exact equality against config lists
    # Priority: team/leadership, services/products, about, contact
    for seg in segments:
        if seg in signals.team_signals:
            return ("team", False, depth)

    for seg in segments:
        if seg in signals.services_signals:
            return ("services", False, depth)

    for seg in segments:
        if seg in signals.about_signals:
            return ("about", False, depth)

    for seg in segments:
        if seg in signals.contact_signals:
            return ("contact", False, depth)

    return ("other", False, depth)


async def discover_pages(
    homepage_url: str,
    homepage_html: str,
    domain: str,
    max_pages: int = 8,
    client: httpx.AsyncClient | None = None,
    config: ResearchConfig | None = None,
    rate_limiter: DomainRateLimiter | None = None,
    signals: PageSignalsConfig | None = None,
) -> list[DiscoveredPageTarget]:
    """Discover same-domain key pages from the verified homepage.
    
    Uses whole path segments, generic locale handling, depth preference (depth <= 2),
    nav/footer priority, and sitemap.xml fallback.
    """
    sig = signals or load_page_signals(config.page_signals_file if config else None)
    parsed_home = urlparse(homepage_url)
    targets: list[DiscoveredPageTarget] = []
    seen_urls: set[str] = set()

    # 1. Always include homepage as first target
    norm_home = f"{parsed_home.scheme}://{parsed_home.netloc}{parsed_home.path}".rstrip("/")
    if not norm_home.endswith(parsed_home.netloc):
        norm_home_entry = homepage_url
    else:
        norm_home_entry = f"{parsed_home.scheme}://{parsed_home.netloc}/"

    targets.append(DiscoveredPageTarget(url=norm_home_entry, page_type="home", title="Homepage"))
    seen_urls.add(norm_home.lower())
    seen_urls.add(norm_home_entry.lower())
    seen_urls.add(homepage_url.lower())

    candidates: list[tuple[str, str, str, int, bool]] = []  # (url, page_type, text, depth, is_nav)

    if homepage_html:
        soup = BeautifulSoup(homepage_html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a.get("href", "").strip()
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
                continue

            full_url = urljoin(homepage_url, href)
            parsed = urlparse(full_url)
            cand_host = parsed.netloc.split(":")[0].lower()
            if not is_same_registrable_domain(domain, cand_host):
                continue
            if parsed.scheme not in ("http", "https"):
                continue

            path_lower = parsed.path.lower()
            if any(path_lower.endswith(ext) for ext in _ASSET_EXTENSIONS):
                continue

            norm_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
            if not norm_url or norm_url.lower() in seen_urls:
                continue

            ptype, is_excluded, depth = classify_page_type(norm_url, sig)
            # Excluded pages are 'other' and MUST NOT take a slot in the fetch cap
            if is_excluded or ptype == "other":
                continue

            # Prefer shallow paths (depth <= 2 after locale prefix)
            if depth > 2:
                continue

            # Check if inside nav, header, or footer
            parent_tags = [p.name.lower() for p in a.parents if p.name]
            is_nav = any(t in ("nav", "header", "footer") for t in parent_tags)
            if not is_nav:
                classes = " ".join(filter(None, [str(p.get("class", "")) for p in a.parents])).lower()
                is_nav = any(kw in classes for kw in ("nav", "menu", "header", "footer"))

            link_text = a.get_text(strip=True)[:60]
            seen_urls.add(norm_url.lower())
            candidates.append((norm_url, ptype, link_text, depth, is_nav))

    # If genuine candidates found fewer than max_pages and client is available, probe sitemap.xml
    if len(candidates) < max_pages and client is not None:
        sitemap_url = f"{parsed_home.scheme}://{parsed_home.netloc}/sitemap.xml"
        try:
            if rate_limiter:
                await rate_limiter.wait(domain)
            sm_resp = await client.get(
                sitemap_url,
                headers={"User-Agent": (config.user_agent if config else "Harvey")},
                timeout=5.0,
                follow_redirects=True,
            )
            if sm_resp.status_code == 200 and ("xml" in sm_resp.headers.get("content-type", "") or "<urlset" in sm_resp.text):
                import warnings
                from bs4 import XMLParsedAsHTMLWarning
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
                    soup_sm = BeautifulSoup(sm_resp.text, "html.parser")
                for loc in soup_sm.find_all("loc"):
                    loc_url = loc.get_text(strip=True)
                    loc_parsed = urlparse(loc_url)
                    if not is_same_registrable_domain(domain, loc_parsed.netloc.split(":")[0].lower()):
                        continue
                    norm_sm_url = f"{loc_parsed.scheme}://{loc_parsed.netloc}{loc_parsed.path}".rstrip("/")
                    if norm_sm_url.lower() in seen_urls:
                        continue
                    ptype, is_excluded, depth = classify_page_type(norm_sm_url, sig)
                    if not is_excluded and ptype != "other" and depth <= 2:
                        seen_urls.add(norm_sm_url.lower())
                        candidates.append((norm_sm_url, ptype, "", depth, False))
        except Exception as e:
            logger.debug("Sitemap check failed for %s: %s", domain, e)

    # Sort candidates: nav/footer links first, then shallowest depth, then type priority (team, services, about, contact)
    type_priority = {"team": 1, "services": 2, "about": 3, "contact": 4, "other": 10}
    candidates.sort(key=lambda c: (
        0 if c[4] else 1,            # nav/footer first
        c[3],                        # shallow depth first
        type_priority.get(c[1], 10), # team, services, about, contact
    ))

    # Add genuine candidates up to max_pages
    for c_url, c_type, c_title, _, _ in candidates:
        if len(targets) >= max_pages:
            break
        targets.append(DiscoveredPageTarget(url=c_url, page_type=c_type, title=c_title))

    return targets


# ── Stage 5: Page Fetching ────────────────────────────────────────────

@dataclass
class PageFetchResult:
    url: str
    page_type: str
    http_status: int
    content_type: str
    content_hash: str
    raw_text: str
    extraction_status: str  # "fetched" | "failed" | "blocked_by_robots" | "unavailable"
    raw_html: str = ""
    error_message: str = ""
    fetched_at: str = ""


async def fetch_pages(
    targets: list[DiscoveredPageTarget],
    domain: str,
    client: httpx.AsyncClient,
    config: ResearchConfig,
    rate_limiter: DomainRateLimiter,
    homepage_html: str = "",
) -> list[PageFetchResult]:
    """Fetch discovered pages respecting robots.txt and rate limits."""
    results: list[PageFetchResult] = []
    if not targets:
        return results

    # 1. Fetch and parse robots.txt for the domain
    base_target = targets[0].url
    parsed = urlparse(base_target)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    robot_parser = await _fetch_robots_txt(robots_url, client, config, rate_limiter, domain)

    headers = {"User-Agent": config.user_agent}

    for target in targets:
        now_iso = _utcnow().isoformat()
        
        # Check robots.txt permission
        if robot_parser is not None:
            try:
                allowed = robot_parser.can_fetch(config.user_agent, target.url)
            except Exception:
                allowed = True
            if not allowed:
                results.append(PageFetchResult(
                    url=target.url,
                    page_type=target.page_type,
                    http_status=0,
                    content_type="",
                    content_hash="",
                    raw_text="",
                    raw_html="",
                    extraction_status="blocked_by_robots",
                    error_message="Disallowed by robots.txt",
                    fetched_at=now_iso,
                ))
                continue

        # If this is the homepage and we already have its HTML from Stage 3
        if target.page_type == "home" and homepage_html:
            raw_text = _extract_readable_text(homepage_html)
            chash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
            results.append(PageFetchResult(
                url=target.url,
                page_type=target.page_type,
                http_status=200,
                content_type="text/html",
                content_hash=chash,
                raw_text=raw_text,
                raw_html=(homepage_html or "")[:500_000],
                extraction_status="fetched",
                fetched_at=now_iso,
            ))
            continue

        # Fetch external subpage with politeness rate limit and bounded retry
        await rate_limiter.wait(domain)
        fetch_res = await _fetch_single_page(target.url, client, headers, config)
        fetch_res.page_type = target.page_type
        fetch_res.fetched_at = now_iso
        results.append(fetch_res)

    return results


async def _fetch_robots_txt(
    robots_url: str,
    client: httpx.AsyncClient,
    config: ResearchConfig,
    rate_limiter: DomainRateLimiter,
    domain: str,
) -> RobotFileParser | None:
    """Fetch and parse robots.txt if available."""
    await rate_limiter.wait(domain)
    try:
        resp = await client.get(
            robots_url,
            headers={"User-Agent": config.user_agent},
            timeout=config.timeout_seconds,
            follow_redirects=True,
        )
        if resp.status_code == 200 and resp.text:
            rp = RobotFileParser()
            rp.parse(resp.text.splitlines())
            return rp
    except Exception:
        pass
    return None


async def _fetch_single_page(
    url: str,
    client: httpx.AsyncClient,
    headers: dict[str, str],
    config: ResearchConfig,
) -> PageFetchResult:
    """Fetch an individual page handling retries, SSRF, and errors independently."""
    retries = 0
    last_err = ""
    last_status = 0
    
    while retries <= config.max_retries:
        try:
            resp = await client.get(url, headers=headers, timeout=config.timeout_seconds, follow_redirects=True)
            last_status = resp.status_code
            content_type = resp.headers.get("content-type", "").lower()
            
            if 200 <= resp.status_code < 300:
                raw_text = _extract_readable_text(resp.text)
                chash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
                return PageFetchResult(
                    url=url,
                    page_type="",
                    http_status=resp.status_code,
                    content_type=content_type,
                    content_hash=chash,
                    raw_text=raw_text,
                    extraction_status="fetched",
                    raw_html=(resp.text or "")[:500_000],
                )
            elif 400 <= resp.status_code < 500:
                return PageFetchResult(
                    url=url,
                    page_type="",
                    http_status=resp.status_code,
                    content_type=content_type,
                    content_hash="",
                    raw_text="",
                    extraction_status="failed",
                    error_message=f"HTTP {resp.status_code}",
                )
            else:
                last_err = f"HTTP {resp.status_code}"
        except UnsafeURLError as e:
            return PageFetchResult(
                url=url,
                page_type="",
                http_status=0,
                content_type="",
                content_hash="",
                raw_text="",
                extraction_status="unavailable",
                error_message=f"SSRF blocked: {e}",
            )
        except httpx.RequestError as e:
            last_err = str(e)
        
        retries += 1
        if retries <= config.max_retries:
            await asyncio.sleep(0.5 * retries)

    return PageFetchResult(
        url=url,
        page_type="",
        http_status=last_status,
        content_type="",
        content_hash="",
        raw_text="",
        extraction_status="failed",
        error_message=last_err or "Fetch failed after retries",
    )


def _extract_readable_text(html: str) -> str:
    """Strip script, style, and navigation tags, returning clean readable text."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for el in soup(["script", "style", "noscript", "svg", "header", "footer", "nav", "aside", "form"]):
        el.decompose()
    
    text = soup.get_text(separator=" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


# ── Orchestrator ──────────────────────────────────────────────────────

async def run_single_email_research(
    email: str,
    *,
    state: StateManager | None = None,
    config: ResearchConfig | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Execute the complete Milestone 1 & 1.1 single-domain research pipeline.
    
    Persists progressively after each stage.
    """
    cfg = config or ResearchConfig()
    st = state or StateManager()
    await st.init_db()

    run_id = _new_id()
    started_at = _utcnow().isoformat()
    norm_email = (email or "").strip().lower()

    # Create run entry in DB
    await st.save_research_run(
        run_id=run_id,
        input_email=email,
        normalized_email=norm_email,
        status="running",
        started_at=started_at,
    )

    free_mail_domains = load_free_email_providers(cfg.free_providers_file)
    page_signals = load_page_signals(cfg.page_signals_file)
    rate_limiter = DomainRateLimiter(min_interval_seconds=cfg.min_interval_seconds)

    async def _execute_pipeline(http_client: httpx.AsyncClient) -> dict[str, Any]:
        stages_dict: dict[str, Any] = {}
        error_summary = ""
        final_run_status = "completed"

        # ── Stage 1: Email Validation ──
        st1_id = _new_id()
        st1_start = _utcnow().isoformat()
        val_res = validate_and_normalize_email(email, free_mail_domains)
        st1_end = _utcnow().isoformat()

        if val_res.valid_syntax:
            await st.save_research_stage(
                stage_id=st1_id,
                run_id=run_id,
                stage_name="email_validation",
                status="completed",
                started_at=st1_start,
                finished_at=st1_end,
                details_json=json.dumps({
                    "normalized_email": val_res.normalized_email,
                    "is_free_mail": val_res.is_free_mail,
                    "mx_info": val_res.mx_info,
                }),
            )
            stages_dict["email_validation"] = {"status": "completed", "error": ""}
        else:
            await st.save_research_stage(
                stage_id=st1_id,
                run_id=run_id,
                stage_name="email_validation",
                status="failed",
                started_at=st1_start,
                finished_at=st1_end,
                error_message=val_res.error,
                details_json=json.dumps({"error": val_res.error}),
            )
            stages_dict["email_validation"] = {"status": "failed", "error": val_res.error}
            
            # Skip later stages
            for remaining in ("domain_extraction", "website_verification", "page_discovery", "page_fetch"):
                rem_id = _new_id()
                await st.save_research_stage(
                    stage_id=rem_id,
                    run_id=run_id,
                    stage_name=remaining,
                    status="skipped",
                    started_at=st1_end,
                    finished_at=st1_end,
                    error_message="Skipped: email validation failed",
                )
                stages_dict[remaining] = {"status": "skipped", "error": "Skipped: email validation failed"}

            await st.update_research_run(
                run_id=run_id,
                status="failed",
                finished_at=_utcnow().isoformat(),
                error_summary=val_res.error,
            )
            return await st.get_research_run_full(run_id)

        # ── Stage 2: Domain Extraction ──
        st2_id = _new_id()
        st2_start = _utcnow().isoformat()
        dom_res = extract_domain(val_res)
        st2_end = _utcnow().isoformat()

        if dom_res.status == "completed":
            await st.save_research_stage(
                stage_id=st2_id,
                run_id=run_id,
                stage_name="domain_extraction",
                status="completed",
                started_at=st2_start,
                finished_at=st2_end,
                details_json=json.dumps({"domain": dom_res.domain}),
            )
            await st.update_research_run(run_id=run_id, domain=dom_res.domain)
            stages_dict["domain_extraction"] = {"status": "completed", "domain": dom_res.domain}
        elif dom_res.status.startswith("skipped"):
            await st.save_research_stage(
                stage_id=st2_id,
                run_id=run_id,
                stage_name="domain_extraction",
                status="skipped",
                started_at=st2_start,
                finished_at=st2_end,
                error_message=dom_res.error or dom_res.status,
                details_json=json.dumps({"reason": dom_res.status}),
            )
            stages_dict["domain_extraction"] = {"status": "skipped", "reason": dom_res.status}
            
            # Skip subsequent web verification & fetching for consumer email
            for remaining in ("website_verification", "page_discovery", "page_fetch"):
                rem_id = _new_id()
                await st.save_research_stage(
                    stage_id=rem_id,
                    run_id=run_id,
                    stage_name=remaining,
                    status="skipped",
                    started_at=st2_end,
                    finished_at=st2_end,
                    error_message="Skipped: consumer email provider domain",
                )
                stages_dict[remaining] = {"status": "skipped", "error": "Skipped: consumer email provider domain"}

            await st.update_research_run(
                run_id=run_id,
                status="completed",
                finished_at=_utcnow().isoformat(),
                error_summary="Consumer mail provider: domain research skipped",
            )
            return await st.get_research_run_full(run_id)
        else:
            await st.save_research_stage(
                stage_id=st2_id,
                run_id=run_id,
                stage_name="domain_extraction",
                status="failed",
                started_at=st2_start,
                finished_at=st2_end,
                error_message=dom_res.error,
                details_json=json.dumps({"error": dom_res.error}),
            )
            stages_dict["domain_extraction"] = {"status": "failed", "error": dom_res.error}

            for remaining in ("website_verification", "page_discovery", "page_fetch"):
                rem_id = _new_id()
                await st.save_research_stage(
                    stage_id=rem_id,
                    run_id=run_id,
                    stage_name=remaining,
                    status="skipped",
                    started_at=st2_end,
                    finished_at=st2_end,
                    error_message="Skipped: domain extraction failed",
                )
                stages_dict[remaining] = {"status": "skipped", "error": "Skipped: domain extraction failed"}

            await st.update_research_run(run_id=run_id, status="failed", finished_at=_utcnow().isoformat(), error_summary=dom_res.error)
            return await st.get_research_run_full(run_id)

        target_domain = dom_res.domain

        # ── Stage 3: Website Verification ──
        st3_id = _new_id()
        st3_start = _utcnow().isoformat()
        web_res = await verify_website(target_domain, http_client, cfg, rate_limiter)
        st3_end = _utcnow().isoformat()

        web_row_id = _new_id()
        await st.save_company_website(
            website_id=web_row_id,
            run_id=run_id,
            domain=target_domain,
            candidate_url=web_res.candidate_url,
            final_url=web_res.final_url,
            http_status=web_res.http_status,
            dns_resolved=web_res.dns_resolved,
            verification_status=web_res.verification_status,
            confidence=web_res.confidence,
            evidence_text=web_res.evidence_text,
            checked_at=st3_end,
        )

        st3_status = "completed" if web_res.verification_status in ("verified", "unverified") else "failed"
        await st.save_research_stage(
            stage_id=st3_id,
            run_id=run_id,
            stage_name="website_verification",
            status=st3_status,
            started_at=st3_start,
            finished_at=st3_end,
            error_message="" if st3_status == "completed" else web_res.evidence_text,
            details_json=json.dumps({
                "verification_status": web_res.verification_status,
                "confidence": web_res.confidence,
                "dns_resolved": web_res.dns_resolved,
                "final_url": web_res.final_url,
                "evidence": web_res.evidence_text,
            }),
        )
        stages_dict["website_verification"] = {
            "status": st3_status,
            "verification_status": web_res.verification_status,
            "final_url": web_res.final_url,
        }

        # If website is unavailable, skip page discovery & fetching
        if web_res.verification_status == "unavailable":
            for remaining in ("page_discovery", "page_fetch"):
                rem_id = _new_id()
                await st.save_research_stage(
                    stage_id=rem_id,
                    run_id=run_id,
                    stage_name=remaining,
                    status="skipped",
                    started_at=st3_end,
                    finished_at=st3_end,
                    error_message="Skipped: website verification unavailable",
                )
                stages_dict[remaining] = {"status": "skipped", "error": "Skipped: website verification unavailable"}

            await st.update_research_run(
                run_id=run_id,
                status="partial",
                finished_at=_utcnow().isoformat(),
                error_summary=web_res.evidence_text,
            )
            return await st.get_research_run_full(run_id)

        # ── Stage 4: Page Discovery ──
        st4_id = _new_id()
        st4_start = _utcnow().isoformat()
        discovered_targets = await discover_pages(
            homepage_url=web_res.final_url or web_res.candidate_url,
            homepage_html=web_res.html_content,
            domain=target_domain,
            max_pages=cfg.max_pages,
            client=http_client,
            config=cfg,
            rate_limiter=rate_limiter,
            signals=page_signals,
        )
        st4_end = _utcnow().isoformat()

        genuine_pages = [t for t in discovered_targets if t.page_type in ("about", "team", "leadership", "contact", "services")]
        st4_details: dict[str, Any] = {
            "count": len(discovered_targets),
            "genuine_count": len(genuine_pages),
            "targets": [{"url": t.url, "type": t.page_type} for t in discovered_targets],
        }
        if not genuine_pages:
            st4_details["message"] = "no about/team page found"

        await st.save_research_stage(
            stage_id=st4_id,
            run_id=run_id,
            stage_name="page_discovery",
            status="completed",
            started_at=st4_start,
            finished_at=st4_end,
            details_json=json.dumps(st4_details),
        )
        stages_dict["page_discovery"] = {
            "status": "completed",
            "count": len(discovered_targets),
            "genuine_count": len(genuine_pages),
            "message": st4_details.get("message", ""),
        }

        # ── Stage 5: Page Fetching ──
        st5_id = _new_id()
        st5_start = _utcnow().isoformat()
        fetched_pages = await fetch_pages(
            targets=discovered_targets,
            domain=target_domain,
            client=http_client,
            config=cfg,
            rate_limiter=rate_limiter,
            homepage_html=web_res.html_content,
        )
        st5_end = _utcnow().isoformat()

        failed_count = 0
        for p in fetched_pages:
            p_row_id = _new_id()
            await st.save_source_page(
                page_id=p_row_id,
                run_id=run_id,
                domain=target_domain,
                url=p.url,
                page_type=p.page_type,
                http_status=p.http_status,
                content_type=p.content_type,
                fetched_at=p.fetched_at,
                content_hash=p.content_hash,
                raw_text=p.raw_text,
                raw_html=p.raw_html,
                extraction_status=p.extraction_status,
                error_message=p.error_message,
            )
            if p.extraction_status in ("failed", "unavailable"):
                failed_count += 1

        st5_status = "completed"
        if failed_count > 0:
            if failed_count == len(fetched_pages):
                st5_status = "failed"
                final_run_status = "partial"
                error_summary = f"All {failed_count} discovered pages failed to fetch."
            else:
                final_run_status = "partial"
                error_summary = f"{failed_count} of {len(fetched_pages)} pages failed to fetch."

        await st.save_research_stage(
            stage_id=st5_id,
            run_id=run_id,
            stage_name="page_fetch",
            status=st5_status,
            started_at=st5_start,
            finished_at=st5_end,
            error_message=error_summary if st5_status != "completed" else "",
            details_json=json.dumps({
                "total_pages": len(fetched_pages),
                "failed_count": failed_count,
            }),
        )
        stages_dict["page_fetch"] = {
            "status": st5_status,
            "total_pages": len(fetched_pages),
            "failed_count": failed_count,
        }

        # Finalize run status in DB
        await st.update_research_run(
            run_id=run_id,
            status=final_run_status,
            finished_at=_utcnow().isoformat(),
            error_summary=error_summary,
        )

        return await st.get_research_run_full(run_id)

    if client is not None:
        return await _execute_pipeline(client)
    else:
        async with public_http_client(
            timeout=cfg.timeout_seconds,
            headers={"User-Agent": cfg.user_agent},
        ) as default_client:
            return await _execute_pipeline(default_client)
