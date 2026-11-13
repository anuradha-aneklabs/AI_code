"""
Harvey AI — Email Prioritization, Inbound Lead Qualification, and Reply Generation Engine.

Classifies incoming emails into High, Medium, and Low priority using
multi-factor evaluation: sender, subject, content, urgency, and business relevance.
Factual, explainable lead qualification extracts verified details without hallucination.
Automatically generates grounded, intent-tailored reply drafts for High-priority emails staged as pending_review.

SAFETY INVARIANTS:
- Marketing newsletters, promotions, discounts, automated notifications, and third-party vendor pitches
  are strictly classified as LOW priority with should_draft_reply=False.
- Sales replies are NEVER generated for newsletters, promotions, discounts, automated notifications, or vendor pitches.
- Reply drafts are generated ONLY for genuine, conversational, actionable prospect inquiries.
- Reply drafts are strictly grounded in the actual email content and configured business/industry profile.
  Never invents a clinic, healthcare, or patient workflow for non-clinic prospects.
- If context is insufficient (e.g. trivial acknowledgment), no misleading draft is generated.
- Outbox drafts are strictly staged as pending_review (never approved or sent automatically).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from harvey.integrations.mail_provider import InboundMessage
from harvey.models.conversation import Conversation, Message
from harvey.models.prospect import Prospect
from harvey.state import StateManager

logger = logging.getLogger("harvey.prioritizer")

# Priority Levels
PRIORITY_HIGH = "high"
PRIORITY_MEDIUM = "medium"
PRIORITY_LOW = "low"

# ---------------------------------------------------------------------------
# Multi-Factor Heuristic Patterns
# ---------------------------------------------------------------------------

# Urgency Patterns
URGENCY_PATTERNS = [
    r"\b(urgent|urgently|asap|today|tomorrow|this\s+afternoon|right\s+away|immediately|time-sensitive|deadline|emergency)\b",
    r"\b(call\s+me\s+now|at\s+your\s+earliest|without\s+delay)\b",
]

# Marketing / Newsletter / Promotional Patterns (Discounts, Newsletters, Sales, E-Commerce)
MARKETING_PROMO_PATTERNS = [
    # Discounts, coupons, percentage off, dollar savings, promotions
    r"\b(\d+%\s*off|save\s*\$?\d+|half\s*off|half\s*price|flash\s*sale|limited\s*time\s*(deal|offer|discount)|special\s*offer|exclusive\s*(deal|offer)|deal\s*of\s*the\s*day|coupon|promo\s*code|discount\s*code|best\s*price)\b",
    # Seasonal promotions & sales
    r"\b(halloween(\s+sale|\s+deal|\s+promotion|\s+special)?|black\s*friday|cyber\s*monday|holiday\s*sale|summer\s*sale|spring\s*sale|winter\s*sale|autumn\s*sale|annual\s*sale|weekend\s*sale|anniversary\s*sale|fall\s*sale)\b",
    # Specific promotional broadcasts & publications (e.g. Manning, O'Reilly, Packt, Substack, Beehiiv)
    r"\b(manning(\s+online(\s+pro)?)?|manning\s+publications|\$199\.99|\$99\.99|\$49\.99|meap|early\s*access\s*deal|packt|o'?reilly|substack|medium\s*daily|the\s+ai\s+report)\b",
    # E-Commerce retail purchasing cues
    r"\b(buy\s*now|order\s*now|shop\s*now|free\s*shipping|storewide|add\s*to\s*cart|browse\s*catalog|checkout\s*now)\b",
    # Newsletters, digests, weekly/monthly updates
    r"\b(newsletter|weekly\s*digest|monthly\s*digest|edition\s*#?\d+|issue\s*#?\d+|weekly\s*roundup|weekly\s*update|curated\s*links|top\s*stories|this\s*week\s*in|what'?s\s*new\s*in)\b",
    # Webinars, virtual conferences, workshops
    r"\b(join\s*(our\s*)?webinar|free\s*webinar|upcoming\s*webinar|register\s*for\s*(our\s*)?(webinar|event|conference|summit)|virtual\s*event|virtual\s*summit)\b",
    # Automated broadcast footers & disclaimers
    r"\b(view\s*(this\s*)?(email\s*)?in\s*(your\s*)?browser|view\s*online|manage\s*(your\s*)?(email\s*)?preferences|update\s*(your\s*)?(subscription|preferences)|you\s*(are\s*receiving|received)\s*this\s*email\s*because|sent\s*by\s*.*publications|all\s*rights\s*reserved|unsubscribe\s*(from\s*this\s*(list|newsletter)))\b",
]

# Automated Notifications & System Updates (Security, Receipts, Transactional)
AUTOMATED_NOTIFICATION_PATTERNS = [
    r"\b(security\s*alert|password\s*reset|verification\s*code|verify\s*your\s*email|two-factor|2fa|one-time\s*passcode|otp)\b",
    r"\b(order\s*confirmation|receipt\s*for|invoice\s*#?\d+|payment\s*received|payment\s*confirmation|shipping\s*confirmation|package\s*delivered|track\s*your\s*package)\b",
    r"\b(do\s*not\s*reply\s*to\s*this\s*(email|message)|this\s*is\s*an\s*automated\s*(email|message|notification)|automated\s*response|notification\s*only)\b",
]

# Third-Party Vendor Sales Pitches & Cold Solicitations (Services pitched TO Harvey)
VENDOR_PITCH_PATTERNS = [
    r"\b(seo\s+services|backlinks|guest\s+posts?|rank\s+on\s+google|google\s+ranking|serp\s+rankings?|organic\s+traffic\s+growth)\b",
    r"\b(web\s+design\s+agency|website\s+redesign|wordpress\s+development|shopify\s+development|mobile\s+app\s+development)\b",
    r"\b(offshore\s+(developers?|engineers?|staff|talent)|hire\s+(remote\s+)?(developers?|coders?)|dedicated\s+developers?)\b",
    r"\b(b2b\s+lead\s+lists?|email\s+databases?|verified\s+leads?\s+database|apollo\s+scrape|buy\s+leads)\b",
    r"\b(recruitment\s+agency|staffing\s+services|hiring\s+top\s+talent|placement\s+agency)\b",
    r"\b(our\s+agency\s+helps|we\s+can\s+help\s+you\s+build|we\s+build\s+custom\s+software\s+for|we\s+are\s+a\s+digital\s+agency)\b",
]

# High Priority — Genuine Conversational Buying Intent & Meeting Requests
HIGH_INTENT_PATTERNS = [
    r"\b(interested|pricing|price|cost|quote|proposal|contract|pilot|budget|procurement|terms|packages)\b",
    r"\b(demo|call|schedule|meeting|meet|discuss|book|zoom|calendar|availability|available|window|walkthrough|overview|consultation)\b",
    r"\b(let'?s\s+talk|sounds\s+good|tell\s+me\s+more|send\s+info|send\s+details|send\s+pricing|can\s+we\s+chat|can\s+you\s+call)\b",
    r"\b(how\s+much|what\s+does\s+it\s+cost|ready\s+to\s+try|sign\s+up|onboard|get\s+started)\b",
]

# Medium Priority — Questions, Referrals, Comparatives
MEDIUM_INTENT_PATTERNS = [
    r"\b(speak\s+with|contact\s+my|forwarding\s+to|refer\s+to|colleague|partner|manager|reach\s+out\s+to|wrong\s+person)\b",
    r"\b(who\s+are\s+you|where\s+are\s+you\s+based|what\s+company|what\s+services|tell\s+me\s+about\s+your\s+firm)\b",
    r"\b(how\s+does\s+it\s+compare|competitor|difference|already\s+have|currently\s+use|which\s+software)\b",
    r"\b(not\s+sure|maybe\s+later|next\s+quarter|follow\s+up\s+in|busy\s+right\s+now|check\s+back)\b",
]

# Low Priority — Out-of-Office, Opt-Outs, Rejections, Bounces
LOW_INTENT_PATTERNS = [
    r"\b(out\s+of\s+(the\s+)?office|on\s+leave|away\s+from|automatic\s+reply|auto-reply|vacation|holiday|back\s+on|parental\s+leave|annual\s+leave)\b",
    r"\b(unsubscribe|remove\s+me|take\s+me\s+off|opt\s*out|stop\s+emailing|don'?t\s+contact|leave\s+me\s+alone|lose\s+my\s+(number|email)|delete\s+my\s+data)\b",
    r"\b(not\s+interested|no\s+thank\s*you|no\s+thanks|pass|not\s+for\s+us|please\s+stop|don'?t\s+need|happy\s+with\s+current)\b",
]

DAEMON_OR_TRANSACTIONAL_PREFIXES = (
    "mailer-daemon@",
    "postmaster@",
    "no-reply@",
    "noreply@",
    "donotreply@",
    "do-not-reply@",
    "bounce@",
    "bounces@",
    "notifications@",
    "notification@",
    "alert@",
    "alerts@",
    "daemon@",
    "billing@",
    "orders@",
    "order@",
    "receipts@",
    "security@",
)

MARKETING_OR_NEWSLETTER_PREFIXES = (
    "news@",
    "newsletter@",
    "newsletters@",
    "marketing@",
    "mkt@",
    "promo@",
    "promotions@",
    "offers@",
    "deals@",
    "updates@",
    "digest@",
    "campaign@",
    "mailer@",
    "subscriptions@",
    "events@",
    "enews@",
    "e-news@",
    "editor@",
    "editorial@",
    "press@",
)

KNOWN_MARKETING_DOMAINS = (
    "manning.com",
    "em.manning.com",
    "mail.manning.com",
    "packtpub.com",
    "oreilly.com",
    "substack.com",
    "medium.com",
    "beehiiv.com",
    "mail.beehiiv.com",
    "mailchimp.com",
    "sendgrid.net",
    "hubspot.com",
    "constantcontact.com",
    "convertkit.com",
    "mailerlite.com",
    "klaviyo.com",
)

BULK_HEADER_INDICATORS = (
    "list-unsubscribe",
    "list-id",
    "list-post",
    "list-help",
    "precedence",
    "x-campaign",
    "x-mailer",
    "x-mailgun-tag",
    "x-sendgrid-eid",
    "x-mc-user",
)

DECISION_MAKER_TITLES = (
    "director", "chief", "owner", "founder", "head", "doctor", "dr",
    "partner", "president", "ceo", "coo", "manager", "principal"
)


@dataclass
class InboundLeadQualification:
    """Factual, explainable qualification data extracted from email signals."""
    company: str = "Unknown"
    contact_name: str = "Unknown"
    title: str = "Unknown"
    industry: str = "Unknown"
    intent: str = "general"
    requirement: str = "Unspecified"
    fit_tier: str = "Needs Discovery"  # "High Fit", "Moderate Fit", "Needs Discovery", "Unqualified"
    opportunity_score: int = 50        # 0 to 100
    is_actionable: bool = False
    confidence: float = 0.5            # 0.0 to 1.0
    known_signals: list[str] = field(default_factory=list)
    unknown_signals: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "company": self.company,
            "contact_name": self.contact_name,
            "title": self.title,
            "industry": self.industry,
            "intent": self.intent,
            "requirement": self.requirement,
            "fit_tier": self.fit_tier,
            "opportunity_score": self.opportunity_score,
            "is_actionable": self.is_actionable,
            "confidence": round(self.confidence, 2),
            "known_signals": self.known_signals,
            "unknown_signals": self.unknown_signals,
        }


@dataclass
class PrioritizedEmail:
    priority: str  # "high", "medium", "low"
    intent: str
    urgency: str  # "high", "normal", "low"
    reasoning: str
    should_draft_reply: bool
    inbound: InboundMessage
    business_relevance: str = "normal"  # "high", "normal", "low"
    sender_type: str = "prospect"  # "decision_maker", "prospect", "automated", "newsletter", "external"
    qualification: Optional[InboundLeadQualification] = None
    factors: dict[str, Any] = field(default_factory=dict)


def qualify_inbound_lead(
    inbound: InboundMessage,
    prospect: Any = None,
    classified_intent: str = "general",
    priority: str = "medium",
    active_profile: Optional[dict[str, Any]] = None,
) -> InboundLeadQualification:
    """
    Extract factual lead qualification signals strictly from verified data:
    - Company, Contact, Title, Industry, Requirement, Intent, and Sales Opportunity.
    - Zero hallucination: unverified fields are explicitly marked 'Unknown' or 'Unspecified'.
    """
    known_signals: list[str] = []
    unknown_signals: list[str] = []

    from_email = (inbound.from_email or "").strip().lower()
    subject = (inbound.subject or "").strip()
    body = (inbound.body or "").strip()
    combined = f"{subject} {body}".lower()

    # 1. Company extraction
    company = ""
    company_source = ""
    if prospect and getattr(prospect, "company", None) and prospect.company.strip():
        company = prospect.company.strip()
        company_source = "CRM record"
    else:
        domain = from_email.split("@")[-1] if "@" in from_email else ""
        generic_providers = (
            "gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com",
            "aol.com", "mail.com", "proton.me", "protonmail.com"
        )
        if domain and not any(p in domain for p in generic_providers):
            base = domain.split(".")[0].replace("-", " ").title()
            company = base
            company_source = f"sender domain ({domain})"
        else:
            m_comp = re.search(r"\b(?:at|from|with)\s+([A-Z][a-zA-Z0-9\s&]{2,30}?)(?:\.|\,|\n|!|;)", body)
            if m_comp and m_comp.group(1).lower() not in ("harvey", "your team", "google", "chrome"):
                company = m_comp.group(1).strip()
                company_source = "email body context"

    if company:
        known_signals.append(f"Company: '{company}' ({company_source})")
    else:
        company = "Unknown"
        unknown_signals.append("Company name is unverified (generic sender domain)")

    # 2. Contact Name extraction
    contact_name = ""
    contact_source = ""
    if prospect and (getattr(prospect, "first_name", None) or getattr(prospect, "last_name", None)):
        first = getattr(prospect, "first_name", "") or ""
        last = getattr(prospect, "last_name", "") or ""
        contact_name = f"{first} {last}".strip()
        contact_source = "CRM record"
    else:
        if "<" in inbound.from_email and ">" in inbound.from_email:
            name_part = inbound.from_email.split("<")[0].replace('"', '').strip()
            if name_part and "@" not in name_part and len(name_part) > 2:
                contact_name = name_part
                contact_source = "sender header"

        if not contact_name:
            sig_match = re.search(r"(?:Best regards|Regards|Thanks|Sincerely|Best|Cheers)[\s,]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)", body)
            if sig_match and len(sig_match.group(1).split()) <= 3:
                contact_name = sig_match.group(1).strip()
                contact_source = "email sign-off"

        if not contact_name and "@" in from_email:
            username = from_email.split("@")[0]
            if "." in username and not any(k in username for k in ("news", "info", "admin", "support", "contact", "hello")):
                parts = username.split(".")
                if len(parts) == 2 and parts[0].isalpha() and parts[1].isalpha():
                    contact_name = f"{parts[0].capitalize()} {parts[1].capitalize()}"
                    contact_source = "email address handle"

    if contact_name:
        known_signals.append(f"Contact Name: '{contact_name}' ({contact_source})")
    else:
        contact_name = "Unknown"
        unknown_signals.append("Contact individual name is unverified")

    # 3. Decision Maker Title extraction
    title = ""
    if prospect and getattr(prospect, "title", None) and prospect.title.strip():
        title = prospect.title.strip()
        known_signals.append(f"Role/Title: '{title}' (CRM record)")
    else:
        for t_pattern in [
            r"\b(managing\s+director|clinical\s+director|medical\s+director|practice\s+manager|practice\s+owner|senior\s+partner|managing\s+partner|founder|co-founder|chief\s+executive|chief\s+operating|vp\s+sales|head\s+of\s+growth|head\s+of\s+sales|operations\s+director|dentist|dental\s+surgeon|orthodontist|attorney|cpa|estimator)\b",
        ]:
            m_title = re.search(t_pattern, combined)
            if m_title:
                title = m_title.group(0).title()
                known_signals.append(f"Role/Title: '{title}' (extracted from message)")
                break
        if not title:
            title = "Unknown"
            unknown_signals.append("Executive role/title is unverified")

    # 4. Industry extraction
    industry = ""
    if prospect and getattr(prospect, "industry", None) and prospect.industry.strip():
        industry = prospect.industry.strip()
        known_signals.append(f"Industry: '{industry}' (CRM record)")
    else:
        if any(w in combined for w in ("dental", "dentist", "oral surgery", "orthodont", "teeth", "clinic")):
            industry = "Healthcare / Dental"
            known_signals.append("Industry: 'Healthcare / Dental' (detected from message context)")
        elif any(w in combined for w in ("saas", "software", "pipeline", "sdr", "revops", "engineering team", "observability", "api")):
            industry = "Software / B2B SaaS"
            known_signals.append("Industry: 'Software / B2B SaaS' (detected from message context)")
        elif any(w in combined for w in ("law firm", "attorney", "legal", "counsel", "litigation", "corporate law")):
            industry = "Legal Services / Corporate Law"
            known_signals.append("Industry: 'Legal Services' (detected from message context)")
        elif any(w in combined for w in ("accounting", "tax", "cpa", "bookkeeping", "audit", "financial advisory")):
            industry = "Accounting & Financial Services"
            known_signals.append("Industry: 'Accounting & Financial Services' (detected from message context)")
        elif any(w in combined for w in ("hvac", "facility", "mechanical contractor", "building automation", "energy retrofit")):
            industry = "Commercial & Home Services / HVAC"
            known_signals.append("Industry: 'Commercial Services / HVAC' (detected from message context)")
        elif any(w in combined for w in ("solar", "clean tech", "cleantech", "storage epc", "renewable")):
            industry = "Renewable Energy / CleanTech"
            known_signals.append("Industry: 'CleanTech / Solar' (detected from message context)")
        elif active_profile and active_profile.get("industry"):
            industry = active_profile.get("industry")
            known_signals.append(f"Industry: '{industry}' (active business profile context)")
        else:
            industry = "Unknown"
            unknown_signals.append("Industry is unverified")

    # 5. Requirement / Pain Point extraction
    requirement = ""
    req_patterns = [
        (r"\b(appointment\s+(reminder|scheduling|automation|follow-?up))\b", "Appointment scheduling and patient follow-up automation"),
        (r"\b(pricing\s+(and\s+)?pilot|pilot\s+pricing|cost\s+for\s+our\s+team)\b", "Pilot pricing terms and budget feasibility"),
        (r"\b(demo|walkthrough|overview\s+of\s+harvey)\b", "Interactive product demonstration and feature overview"),
        (r"\b(outreach\s+automation|outbound\s+pipeline|sdr\s+productivity|sequence\s+coordination)\b", "B2B outbound pipeline acceleration and sequence coordination"),
        (r"\b(quote\s+follow-?up|estimate\s+tracking|bid\s+proposals?)\b", "Commercial quote follow-up and proposal acceleration"),
        (r"\b(software\s+compatibility|system\s+integrat\w+|api\s+support)\b", "Software integration and workflow compatibility verification"),
        (r"\b(referral|contact\s+my\s+colleague|forward\s+to)\b", "Colleague redirection to designated decision maker"),
    ]
    for pat, req_desc in req_patterns:
        if re.search(pat, combined):
            requirement = req_desc
            known_signals.append(f"Requirement: '{requirement}'")
            break

    if not requirement:
        requirement = "Unspecified"
        unknown_signals.append("Specific operational requirements unconfirmed")

    # 6. Opportunity Score & Fit Tier
    score = 0
    if classified_intent in ("demo_request", "interested", "meeting_request", "pricing", "pricing_inquiry"):
        score += 40
    elif classified_intent in ("question", "product_question", "referral"):
        score += 25
    elif classified_intent in ("general", "acknowledgment"):
        score += 10
    else:
        score += 0  # marketing, automated, vendor_pitch, ooo, bounce, unsubscribe

    if company != "Unknown":
        score += 20
    if contact_name != "Unknown":
        score += 15
    if title != "Unknown":
        score += 15
    if requirement != "Unspecified":
        score += 10

    score = min(max(score, 0), 100)

    # Determine Fit Tier & Actionability
    if classified_intent in ("marketing", "automated", "vendor_pitch", "bounce", "unsubscribe", "ooo", "not_interested"):
        fit_tier = "Unqualified"
        is_actionable = False
    elif score >= 70:
        fit_tier = "High Fit"
        is_actionable = True
    elif score >= 45:
        fit_tier = "Moderate Fit"
        is_actionable = (priority == "high" or classified_intent in ("demo_request", "pricing", "pricing_inquiry", "meeting_request"))
    else:
        fit_tier = "Needs Discovery"
        is_actionable = False

    confidence = round(len(known_signals) / max(1, len(known_signals) + len(unknown_signals)), 2)

    return InboundLeadQualification(
        company=company,
        contact_name=contact_name,
        title=title,
        industry=industry,
        intent=classified_intent,
        requirement=requirement,
        fit_tier=fit_tier,
        opportunity_score=score,
        is_actionable=is_actionable,
        confidence=confidence,
        known_signals=known_signals,
        unknown_signals=unknown_signals,
    )


def classify_email_priority(
    inbound: InboundMessage,
    prospect: Any = None,
    active_profile: Optional[dict[str, Any]] = None,
) -> PrioritizedEmail:
    """
    Classify an incoming email into High, Medium, or Low priority using
    multi-factor evaluation:
      - Sender: sender address, daemon/newsletter detection, decision-maker status
      - Subject: urgency cues, bounce indicators, marketing signals
      - Content: sales/buying intent, questions, referrals, opt-outs, discounts
      - Urgency: explicit timeline or emergency words
      - Business Relevance: commercial buying fit vs automated/newsletter chatter

    STRICT SAFETY RULE:
    Newsletters, marketing promotions, discounts, notifications, vendor pitches,
    bounces, and opt-outs are strictly classified as LOW priority with should_draft_reply=False.
    """
    subject_raw = (inbound.subject or "").strip()
    body_raw = (inbound.body or "").strip()
    subject = subject_raw.lower()
    body = body_raw.lower()
    combined = f"{subject} {body}"
    from_email = (inbound.from_email or "").strip().lower()
    headers = getattr(inbound, "headers", {}) or {}
    if isinstance(headers, dict):
        normalized_headers = {str(k).lower(): str(v).lower() for k, v in headers.items()}
    else:
        normalized_headers = {}

    # 1. Evaluate Sender & Header Factors
    is_daemon = any(from_email.startswith(prefix) for prefix in DAEMON_OR_TRANSACTIONAL_PREFIXES)
    is_marketing_prefix = any(from_email.startswith(prefix) for prefix in MARKETING_OR_NEWSLETTER_PREFIXES)
    is_marketing_domain = any(from_email.endswith("@" + d) or from_email.endswith("." + d) for d in KNOWN_MARKETING_DOMAINS)
    has_bulk_header = any(h in normalized_headers for h in BULK_HEADER_INDICATORS)
    if "precedence" in normalized_headers:
        prec = normalized_headers["precedence"]
        if any(w in prec for w in ("bulk", "list", "junk")):
            has_bulk_header = True

    prospect_title = (getattr(prospect, "title", "") or "").lower()
    is_decision_maker = any(t in prospect_title for t in DECISION_MAKER_TITLES)

    if is_marketing_prefix or is_marketing_domain or has_bulk_header:
        sender_type = "newsletter"
    elif is_daemon:
        sender_type = "automated"
    elif is_decision_maker:
        sender_type = "decision_maker"
    elif prospect is not None:
        sender_type = "known_prospect"
    else:
        sender_type = "external"

    # 2. Evaluate Urgency Factor
    has_subject_urgency = bool(any(re.search(p, subject) for p in URGENCY_PATTERNS))
    has_body_urgency = bool(any(re.search(p, body) for p in URGENCY_PATTERNS))
    urgency = "high" if (has_subject_urgency or has_body_urgency) else "normal"

    # 3. Check Low Priority / Suppression Signals First (Safety Invariant)

    # 3A. Bounces & Delivery Failures (strictly mailer-daemon, postmaster, bounce)
    is_bounce = (
        inbound.is_bounce
        or any(from_email.startswith(p) for p in ("mailer-daemon@", "postmaster@", "bounce@", "bounces@"))
        or any(k in subject for k in ("delivery status notification", "failure notice", "undeliverable", "returned to sender", "delivery incomplete"))
    )
    if is_bounce:
        factors = {
            "sender": {"email": from_email, "type": "automated"},
            "subject": {"raw": subject_raw, "bounce_signal": True},
            "urgency": "low",
            "business_relevance": "low",
        }
        qualification = qualify_inbound_lead(inbound, prospect, "bounce", PRIORITY_LOW, active_profile)
        return PrioritizedEmail(
            priority=PRIORITY_LOW,
            intent="bounce",
            urgency="low",
            business_relevance="low",
            sender_type="automated",
            reasoning="System bounce or delivery failure notification. No reply needed.",
            should_draft_reply=False,
            inbound=inbound,
            qualification=qualification,
            factors=factors,
        )

    # 3B. Marketing Newsletters, Promotional Discounts, Sales Broadcasts
    marketing_trigger = None
    for pat in MARKETING_PROMO_PATTERNS:
        m = re.search(pat, combined, re.IGNORECASE)
        if m:
            marketing_trigger = m.group(0)
            break

    is_marketing = bool(
        marketing_trigger
        or is_marketing_prefix
        or is_marketing_domain
        or has_bulk_header
    )
    if is_marketing:
        matched_reason = f"matched '{marketing_trigger}'" if marketing_trigger else "sender/bulk mailing list signal"
        factors = {
            "sender": {"email": from_email, "type": "newsletter"},
            "subject": {"raw": subject_raw, "marketing_signal": True},
            "content": {"matched_trigger": marketing_trigger},
            "urgency": "low",
            "business_relevance": "low",
        }
        qualification = qualify_inbound_lead(inbound, prospect, "marketing", PRIORITY_LOW, active_profile)
        return PrioritizedEmail(
            priority=PRIORITY_LOW,
            intent="marketing",
            urgency="low",
            business_relevance="low",
            sender_type="newsletter",
            reasoning=f"Promotional newsletter, marketing advertisement, or discount offer detected ({matched_reason}). Never generate sales reply.",
            should_draft_reply=False,
            inbound=inbound,
            qualification=qualification,
            factors=factors,
        )

    # 3C. Third-Party Vendor Sales Pitches (SEO, offshore dev, lead databases)
    vendor_trigger = None
    for pat in VENDOR_PITCH_PATTERNS:
        m = re.search(pat, combined, re.IGNORECASE)
        if m:
            vendor_trigger = m.group(0)
            break

    if vendor_trigger and sender_type not in ("known_prospect", "decision_maker"):
        factors = {
            "sender": {"email": from_email, "type": "external"},
            "subject": {"raw": subject_raw},
            "content": {"vendor_trigger": vendor_trigger},
            "urgency": "low",
            "business_relevance": "low",
        }
        qualification = qualify_inbound_lead(inbound, prospect, "vendor_pitch", PRIORITY_LOW, active_profile)
        return PrioritizedEmail(
            priority=PRIORITY_LOW,
            intent="vendor_pitch",
            urgency="low",
            business_relevance="low",
            sender_type="external",
            reasoning=f"Third-party vendor sales pitch or service solicitation detected ('{vendor_trigger}'). No reply needed.",
            should_draft_reply=False,
            inbound=inbound,
            qualification=qualification,
            factors=factors,
        )

    # 3D. Opt-out / Unsubscribe from Genuine Prospect
    is_opt_out = bool(re.search(
        r"\b(unsubscribe|remove\s+me|take\s+me\s+off|opt\s*out|stop\s+emailing|don'?t\s+contact|leave\s+me\s+alone|lose\s+my\s+(number|email)|delete\s+my\s+data)\b",
        combined
    ))
    if is_opt_out:
        factors = {
            "sender": {"email": from_email, "type": sender_type},
            "subject": {"raw": subject_raw, "opt_out_signal": True},
            "urgency": "low",
            "business_relevance": "low",
        }
        qualification = qualify_inbound_lead(inbound, prospect, "unsubscribe", PRIORITY_LOW, active_profile)
        return PrioritizedEmail(
            priority=PRIORITY_LOW,
            intent="unsubscribe",
            urgency="low",
            business_relevance="low",
            sender_type=sender_type,
            reasoning="Prospect requested unsubscribe or opt-out. Hard suppression enforced.",
            should_draft_reply=False,
            inbound=inbound,
            qualification=qualification,
            factors=factors,
        )

    # 3E. Automated System Notifications & Alerts (Security, Receipts, 2FA, Transactions)
    automated_trigger = None
    for pat in AUTOMATED_NOTIFICATION_PATTERNS:
        m = re.search(pat, combined, re.IGNORECASE)
        if m:
            automated_trigger = m.group(0)
            break

    is_automated_notification = bool(
        automated_trigger
        or is_daemon
    )
    if is_automated_notification:
        matched_reason = f"matched '{automated_trigger}'" if automated_trigger else "automated system sender"
        factors = {
            "sender": {"email": from_email, "type": "automated"},
            "subject": {"raw": subject_raw, "automated_signal": True},
            "content": {"matched_trigger": automated_trigger},
            "urgency": "low",
            "business_relevance": "low",
        }
        qualification = qualify_inbound_lead(inbound, prospect, "automated", PRIORITY_LOW, active_profile)
        return PrioritizedEmail(
            priority=PRIORITY_LOW,
            intent="automated",
            urgency="low",
            business_relevance="low",
            sender_type="automated",
            reasoning=f"Automated system notification, receipt, or alert detected ({matched_reason}). No reply needed.",
            should_draft_reply=False,
            inbound=inbound,
            qualification=qualification,
            factors=factors,
        )

    # 3F. Out of Office (OOO)
    is_ooo = bool(re.search(
        r"\b(out\s+of\s+(the\s+)?office|on\s+leave|away\s+from|automatic\s+reply|auto-reply|vacation|holiday|back\s+on|parental\s+leave|annual\s+leave)\b",
        combined
    ))
    if is_ooo:
        factors = {
            "sender": {"email": from_email, "type": "automated"},
            "subject": {"raw": subject_raw, "ooo_signal": True},
            "urgency": "low",
            "business_relevance": "low",
        }
        qualification = qualify_inbound_lead(inbound, prospect, "ooo", PRIORITY_LOW, active_profile)
        return PrioritizedEmail(
            priority=PRIORITY_LOW,
            intent="ooo",
            urgency="low",
            business_relevance="low",
            sender_type=sender_type,
            reasoning="Automated out-of-office response. Safely logged without reply draft.",
            should_draft_reply=False,
            inbound=inbound,
            qualification=qualification,
            factors=factors,
        )

    # 3G. Explicit Disinterest / Rejection
    is_rejection = bool(re.search(
        r"\b(not\s+interested|no\s+thank\s*you|no\s+thanks|pass|not\s+for\s+us|please\s+stop|don'?t\s+need|happy\s+with\s+current)\b",
        combined
    ))
    if is_rejection:
        factors = {
            "sender": {"email": from_email, "type": sender_type},
            "subject": {"raw": subject_raw},
            "urgency": "low",
            "business_relevance": "low",
        }
        qualification = qualify_inbound_lead(inbound, prospect, "not_interested", PRIORITY_LOW, active_profile)
        return PrioritizedEmail(
            priority=PRIORITY_LOW,
            intent="not_interested",
            urgency="low",
            business_relevance="low",
            sender_type=sender_type,
            reasoning="Prospect declined outreach. Sequence stopped, no reply drafted.",
            should_draft_reply=False,
            inbound=inbound,
            qualification=qualification,
            factors=factors,
        )

    # 4. Check Referral / Redirection (Medium Priority)
    is_referral = bool(re.search(
        r"\b(speak\s+with|contact\s+my|forwarding\s+to|forward\s+(this|your)\s+details|refer\s+to|reach\s+out\s+to|not\s+the\s+(right\s+)?person)\b",
        combined
    ))
    if is_referral:
        factors = {
            "sender": {"email": from_email, "type": sender_type},
            "subject": {"raw": subject_raw},
            "content": {"is_referral": True},
            "urgency": urgency,
            "business_relevance": "normal",
            "sub_intent": "referral",
        }
        qualification = qualify_inbound_lead(inbound, prospect, "referral", PRIORITY_MEDIUM, active_profile)
        return PrioritizedEmail(
            priority=PRIORITY_MEDIUM,
            intent="referral",
            urgency=urgency,
            business_relevance="normal",
            sender_type=sender_type,
            reasoning="Colleague referral or contact redirection detected.",
            should_draft_reply=False,
            inbound=inbound,
            qualification=qualification,
            factors=factors,
        )

    # 5. Check High Priority (Buying Intent, Demo Request, Pricing, Urgent Consultation)
    # Strictly reserved for genuine conversational prospect inquiries
    high_match = None
    for pat in HIGH_INTENT_PATTERNS:
        m = re.search(pat, combined, re.IGNORECASE)
        if m:
            high_match = m.group(0)
            break

    # Context sufficiency guard: if message is trivial acknowledgment, do not mark HIGH
    words_count = len(combined.split())
    is_trivial = words_count <= 3 and not any(w in combined for w in ("demo", "pricing", "call", "meet", "cost", "pilot"))

    if high_match and sender_type not in ("automated", "newsletter") and not is_trivial:
        # High buying / engagement intent
        if any(k in combined for k in ("demo", "walkthrough")):
            intent = "demo_request"
        elif any(k in combined for k in ("pricing", "price", "cost", "quote", "proposal", "budget")):
            intent = "pricing_inquiry"
        elif any(k in combined for k in ("call", "meeting", "meet", "schedule", "zoom", "calendar", "availability")):
            intent = "meeting_request"
        else:
            intent = "interested"

        factors = {
            "sender": {"email": from_email, "type": sender_type, "is_decision_maker": is_decision_maker},
            "subject": {"raw": subject_raw, "has_urgency": has_subject_urgency},
            "content": {"matched_trigger": high_match, "has_urgency": has_body_urgency},
            "urgency": urgency,
            "business_relevance": "high",
            "sub_intent": intent,
        }
        reasoning = (
            f"High buying interest detected (keyword: '{high_match}'). "
            f"Sender is {sender_type.replace('_', ' ')}. Urgency is {urgency}."
        )
        qualification = qualify_inbound_lead(inbound, prospect, intent, PRIORITY_HIGH, active_profile)
        return PrioritizedEmail(
            priority=PRIORITY_HIGH,
            intent=intent,
            urgency="high",
            business_relevance="high",
            sender_type=sender_type,
            reasoning=reasoning,
            should_draft_reply=True,
            inbound=inbound,
            qualification=qualification,
            factors=factors,
        )

    # 6. Check Medium Priority (Questions, Comparisons, Follow-up requests)
    med_match = None
    for pat in MEDIUM_INTENT_PATTERNS:
        m = re.search(pat, combined, re.IGNORECASE)
        if m:
            med_match = m.group(0)
            break

    if med_match:
        if any(k in combined for k in ("speak with", "contact my", "forwarding to", "refer to", "colleague", "manager", "reach out to")):
            intent = "referral"
        elif any(k in combined for k in ("compare", "competitor", "difference", "already have", "currently use", "which software")):
            intent = "question"
        elif any(k in combined for k in ("not sure", "maybe later", "next quarter", "check back", "busy")):
            intent = "question"
        else:
            intent = "question"

        factors = {
            "sender": {"email": from_email, "type": sender_type},
            "subject": {"raw": subject_raw},
            "content": {"matched_trigger": med_match},
            "urgency": urgency,
            "business_relevance": "normal",
            "sub_intent": intent,
        }
        reasoning = f"Clarification question or referral detected (keyword: '{med_match}')."
        qualification = qualify_inbound_lead(inbound, prospect, intent, PRIORITY_MEDIUM, active_profile)
        return PrioritizedEmail(
            priority=PRIORITY_MEDIUM,
            intent=intent,
            urgency=urgency,
            business_relevance="normal",
            sender_type=sender_type,
            reasoning=reasoning,
            should_draft_reply=False,
            inbound=inbound,
            qualification=qualification,
            factors=factors,
        )

    # 7. Default Fallback
    if sender_type in ("automated", "newsletter"):
        factors = {
            "sender": {"email": from_email, "type": sender_type},
            "subject": {"raw": subject_raw},
            "urgency": "low",
            "business_relevance": "low",
        }
        qualification = qualify_inbound_lead(inbound, prospect, "automated", PRIORITY_LOW, active_profile)
        return PrioritizedEmail(
            priority=PRIORITY_LOW,
            intent="automated",
            urgency="low",
            business_relevance="low",
            sender_type=sender_type,
            reasoning="Automated or broadcast communication without personal inquiry. No reply drafted.",
            should_draft_reply=False,
            inbound=inbound,
            qualification=qualification,
            factors=factors,
        )

    factors = {
        "sender": {"email": from_email, "type": sender_type},
        "subject": {"raw": subject_raw},
        "urgency": urgency,
        "business_relevance": "normal",
    }
    qualification = qualify_inbound_lead(inbound, prospect, "general", PRIORITY_MEDIUM, active_profile)
    return PrioritizedEmail(
        priority=PRIORITY_MEDIUM,
        intent="general",
        urgency=urgency,
        business_relevance="normal",
        sender_type=sender_type,
        reasoning="Standard inbound communication without explicit buying intent or opt-out.",
        should_draft_reply=False,
        inbound=inbound,
        qualification=qualification,
        factors=factors,
    )


# ---------------------------------------------------------------------------
# Prioritization Service
# ---------------------------------------------------------------------------

class EmailPrioritizationService:
    """Service to process, prioritize, qualify, and generate staged reply drafts for inbound mail."""

    def __init__(self, state: StateManager, config: Any = None, brain: Any = None):
        self.state = state
        self.config = config
        self.brain = brain

    async def process_inbound(
        self,
        inbound: InboundMessage,
        campaign_id: str = "",
        auto_draft_high: bool = True,
        is_mock: bool = False,
    ) -> dict[str, Any]:
        """
        Process a single inbound email:
        1. Identify or qualify prospect by email.
        2. Classify into High / Medium / Low priority using multi-factor heuristics.
        3. Perform factual lead qualification (extracting company, contact, need, fit).
        4. Save or update conversation thread with priority and channel metadata.
        5. If High priority and genuinely conversational, generate and stage a grounded draft.
        6. Return structured result with qualification audit.
        """
        await self.state.init_db()
        from_email = (inbound.from_email or "").strip().lower()

        # Find prospect or associate
        prospect = await self.state.get_prospect_by_email(from_email)

        # Retrieve active business profile if available
        active_profile = None
        try:
            if hasattr(self.state, "get_business_profiles"):
                profiles = await self.state.get_business_profiles()
                pid = getattr(prospect, "profile_id", "") or ""
                if pid:
                    active_profile = next((p for p in profiles if p.get("id") == pid), None)
                if not active_profile:
                    active_profile = next((p for p in profiles if p.get("is_default")), (profiles[0] if profiles else None))
        except Exception as e:
            logger.debug(f"Could not load business profiles from state: {e}")

        # Classify priority & perform factual qualification
        classified = classify_email_priority(inbound, prospect=prospect, active_profile=active_profile)
        qual = classified.qualification or qualify_inbound_lead(inbound, prospect, classified.intent, classified.priority, active_profile)

        # If genuine high/medium prospect inquiry from non-existing lead, save prospect record
        if not prospect and qual.fit_tier in ("High Fit", "Moderate Fit") and qual.company != "Unknown":
            first = qual.contact_name.split()[0] if qual.contact_name != "Unknown" else "Contact"
            last = " ".join(qual.contact_name.split()[1:]) if qual.contact_name != "Unknown" and len(qual.contact_name.split()) > 1 else ""
            try:
                new_prospect = Prospect(
                    id=f"inbound_{int(datetime.now(timezone.utc).timestamp())}_{from_email[:6]}",
                    email=from_email,
                    first_name=first,
                    last_name=last,
                    company=qual.company,
                    title=qual.title if qual.title != "Unknown" else "",
                    status="replied",
                )
                await self.state.add_prospect(new_prospect)
                prospect = new_prospect
            except Exception as e:
                logger.debug(f"Could not auto-create prospect for inbound inquiry: {e}")

        prospect_id = prospect.id if prospect else ""

        logger.info(
            f"Prioritizer: {from_email} -> {classified.priority.upper()} priority "
            f"(intent: {classified.intent}, fit: {qual.fit_tier}, opportunity: {qual.opportunity_score}%)"
        )

        # Handle Opt-Out safety immediately (Safety Invariant)
        if classified.intent == "unsubscribe" and prospect:
            await self.state.update_prospect_status(prospect.id, "opted_out")
            await self.state.cancel_pending_outbox_for_prospect(prospect.id)
            await self.state.log_action(
                action_type="opt_out",
                agent="prioritizer",
                details={
                    "prospect_id": prospect.id,
                    "email": from_email,
                    "reason": classified.reasoning,
                    "is_mock": is_mock,
                },
            )

        # Retrieve or create Conversation
        existing_convos = (
            await self.state.get_conversations_by_status("open")
            + await self.state.get_conversations_by_status("needs_human")
        )
        convo = next((c for c in existing_convos if c.prospect_id == prospect_id), None) if prospect_id else None

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        inbound_msg = Message(sender="prospect", content=inbound.body, timestamp=now)
        channel = "mock" if is_mock else "email"

        if convo is None:
            convo = Conversation(
                id="",
                prospect_id=prospect_id,
                campaign_id=campaign_id,
                channel=channel,
                thread=[inbound_msg],
                intent=classified.intent,
                priority=classified.priority,
                status="needs_human" if (classified.priority == PRIORITY_HIGH and qual.is_actionable) else "open",
            )
            convo_id = await self.state.add_conversation(convo)
            convo.id = convo_id
        else:
            convo.thread.append(inbound_msg)
            await self.state.update_conversation(
                convo.id,
                thread_json=convo.thread_json(),
                intent=classified.intent,
                priority=classified.priority,
                status="needs_human" if (classified.priority == PRIORITY_HIGH and qual.is_actionable) else convo.status,
            )
            convo_id = convo.id

        # Update prospect status to 'replied' if active
        if prospect and prospect.status not in ("opted_out", "lost"):
            await self.state.update_prospect_status(prospect.id, "replied")
            await self.state.cancel_pending_outbox_for_prospect(prospect.id)

        draft_id = None
        draft_body = ""

        # 4. Generate grounded reply draft ONLY for genuinely conversational High-Priority inquiries
        # STRICT SAFETY: Never generated for newsletters, promotions, automated notices, vendor pitches, OOO, or opt-outs
        if (
            classified.priority == PRIORITY_HIGH
            and classified.should_draft_reply
            and auto_draft_high
            and qual.is_actionable
            and classified.intent not in ("marketing", "automated", "vendor_pitch", "bounce", "unsubscribe", "ooo", "not_interested")
        ):
            draft_body = await self._generate_high_priority_reply(inbound, prospect, convo, qual)
            if draft_body:
                subject = inbound.subject or "Re: your note"
                if not subject.lower().startswith("re:"):
                    subject = f"Re: {subject}"
                if len(subject) > 55:
                    subject = f"{subject[:52]}..."

                # STRICT SAFETY: Save in outbox as pending_review (never approved or sent)
                draft_id = await self.state.add_outbox_item(
                    prospect_id=prospect_id,
                    conversation_id=convo_id,
                    kind="reply",
                    to_email=from_email,
                    subject=subject,
                    body=draft_body,
                    send_at=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                    status="pending_review",  # strictly held for human review
                )
                logger.info(
                    f"Prioritizer: Staged reply draft {draft_id} in Outbox for high-priority lead {from_email}"
                )
                await self.state.log_action(
                    action_type="draft_generated",
                    agent="prioritizer",
                    details={
                        "draft_id": draft_id,
                        "prospect_email": from_email,
                        "priority": PRIORITY_HIGH,
                        "status": "pending_review",
                        "fit_tier": qual.fit_tier,
                        "is_mock": is_mock,
                    },
                )

        return {
            "from_email": from_email,
            "priority": classified.priority,
            "intent": classified.intent,
            "urgency": classified.urgency,
            "business_relevance": classified.business_relevance,
            "sender_type": classified.sender_type,
            "reasoning": classified.reasoning,
            "qualification": qual.to_dict(),
            "factors": classified.factors,
            "conversation_id": convo_id,
            "draft_id": draft_id,
            "draft_body": draft_body,
            "staged_in_outbox": bool(draft_id),
            "is_mock": is_mock,
        }

    async def _resolve_business_context(self, prospect: Any, from_email: str = "") -> tuple[dict[str, Any], str, bool]:
        """
        Resolve configured business & industry profile grounded in real database/config state.
        Returns:
            - profile: dict with name, industry, product_name, product_description
            - company_ref: clean prospect company reference (NEVER defaults to 'your clinic')
            - is_clinic_context: True ONLY if prospect or profile explicitly belongs to dental/healthcare clinic
        """
        company = (getattr(prospect, "company", "") or "").strip()
        if company:
            company_ref = company
        else:
            domain = from_email.split("@")[-1].lower() if "@" in from_email else ""
            if domain and not any(p in domain for p in ("gmail.", "yahoo.", "hotmail.", "outlook.", "icloud.", "aol.")):
                company_ref = domain.split(".")[0].replace("-", " ").title()
            else:
                company_ref = "your team"

        persona_name = "Harvey"
        if self.config and hasattr(self.config, "persona") and getattr(self.config.persona, "name", None):
            persona_name = self.config.persona.name

        product_name = "Harvey AI"
        if self.config and hasattr(self.config, "product") and getattr(self.config.product, "name", None):
            product_name = self.config.product.name

        product_desc = "Autonomous B2B outreach assistant that drafts personalized correspondence"
        if self.config and hasattr(self.config, "product") and getattr(self.config.product, "description", None):
            product_desc = self.config.product.description

        active_profile = None
        try:
            if hasattr(self.state, "get_business_profiles"):
                profiles = await self.state.get_business_profiles()
                pid = getattr(prospect, "profile_id", "") or ""
                if pid:
                    active_profile = next((p for p in profiles if p.get("id") == pid), None)
                if not active_profile:
                    active_profile = next((p for p in profiles if p.get("is_default")), (profiles[0] if profiles else None))
        except Exception as e:
            logger.debug(f"Could not load business profiles from state: {e}")

        industry_name = "General B2B"
        if active_profile:
            industry_name = active_profile.get("industry") or active_profile.get("name") or "General B2B"
            if active_profile.get("product_name"):
                product_name = active_profile.get("product_name")
            if active_profile.get("product_description"):
                product_desc = active_profile.get("product_description")

        profile_dict = {
            "persona_name": persona_name,
            "product_name": product_name,
            "product_description": product_desc,
            "industry": industry_name,
        }

        prospect_indicators = f"{company_ref} {getattr(prospect, 'title', '')} {getattr(prospect, 'industry', '')}".lower()
        is_clinic_context = (
            any(w in prospect_indicators for w in ("clinic", "dental", "dentist", "oral surgery", "orthodont", "patient"))
            or (any(w in industry_name.lower() for w in ("dental", "clinic")) and getattr(prospect, "profile_id", "") == (active_profile.get("id") if active_profile else ""))
        )

        return profile_dict, company_ref, is_clinic_context

    async def _generate_high_priority_reply(
        self,
        inbound: InboundMessage,
        prospect: Any,
        convo: Conversation,
        qualification: Optional[InboundLeadQualification] = None,
    ) -> str:
        """
        Generate a strictly grounded reply draft adhering to configured company/industry context.
        STRICT GROUNDING: Never invents a clinic, healthcare, or patient context unless
        the recipient explicitly belongs to that domain.
        If context is insufficient, returns empty string to prevent misleading replies.
        """
        body_clean = (inbound.body or "").strip()
        subject_clean = (inbound.subject or "").strip()
        combined_text = f"{subject_clean} {body_clean}".strip()

        # Insufficient context check: do not generate misleading draft for trivial text
        if len(combined_text.split()) < 3 and not any(w in combined_text.lower() for w in ("demo", "pricing", "call", "meet", "cost", "pilot")):
            logger.info("Prioritizer: Insufficient context to generate grounded reply. Skipping draft.")
            return ""

        from_email = (inbound.from_email or getattr(prospect, "email", "") or "").strip().lower()
        profile_dict, company_ref, is_clinic_context = await self._resolve_business_context(prospect, from_email)

        if qualification and qualification.contact_name != "Unknown":
            first_name = qualification.contact_name.split()[0]
        else:
            first_name = (getattr(prospect, "first_name", "") or "").strip()
        if not first_name:
            first_name = "there"

        if qualification and qualification.company != "Unknown":
            company_ref = qualification.company

        persona_name = profile_dict["persona_name"]
        product_name = profile_dict["product_name"]
        product_desc = profile_dict["product_description"]
        industry_name = profile_dict["industry"]

        intent = qualification.intent if qualification else (convo.intent if convo else "interested")
        req = qualification.requirement if (qualification and qualification.requirement != "Unspecified") else ""

        # Build bounded conversation history context
        history_lines = []
        if convo and getattr(convo, "thread", None):
            recent_msgs = convo.thread[-5:]
            for m in recent_msgs:
                sender_label = persona_name if m.sender == "harvey" else (first_name if first_name != "there" else "Prospect")
                content_preview = (m.content or "").strip()
                if len(content_preview) > 280:
                    content_preview = content_preview[:277] + "..."
                history_lines.append(f"- {sender_label}: \"{content_preview}\"")

        history_context = "\n".join(history_lines) if history_lines else "No prior messages in thread."
        body_excerpt = body_clean[:400].strip()

        # Prompt for local Ollama LLM
        prompt = (
            f"You are {persona_name}, an assistant for {product_name} ({product_desc}).\n"
            f"Write a concise, professional, grounded email reply to: {first_name} from {company_ref}.\n"
            f"Configured Industry Context: {industry_name}\n"
            f"Detected Intent: {intent}\n"
            f"Specific Requirement: {req or 'General inquiry'}\n"
            f"Inbound Subject: {inbound.subject}\n\n"
            f"Prior conversation thread history:\n"
            f"{history_context}\n\n"
            f"Latest message from recipient to answer:\n"
            f"\"{body_excerpt}\"\n\n"
            f"Strict Grounding Rules:\n"
            f"- Address their specific question, request, or proposal directly and concisely (under 75 words).\n"
            f"- Ground your reply ONLY in the actual email content and configured business context.\n"
            f"- Do NOT invent a clinic, dental practice, patient workflows, or other unrelated domain unless the recipient is explicitly in that field.\n"
            f"- Do NOT invent pricing numbers, guarantees, or false claims.\n"
            f"- Output ONLY the body text of the reply, no subject line, no preamble, no quotes."
        )

        if self.brain:
            try:
                reply = await self.brain.think(prompt, task="personal_email")
                if reply and len(reply.strip()) > 10:
                    cleaned = reply.strip()
                    if cleaned.startswith("```") and cleaned.endswith("```"):
                        lines = cleaned.split("\n")[1:-1]
                        cleaned = "\n".join(lines).strip()
                    if not is_clinic_context and any(w in cleaned.lower() for w in ("clinic", "patient inquiries", "dental practice")):
                        logger.warning("LLM hallucinated clinic context for non-clinic prospect; falling back to grounded template")
                    else:
                        return cleaned
            except Exception as e:
                logger.warning(f"Prioritizer LLM generation failed ({e}), using grounded template")

        # Grounded Fallback Templates Tailored to Intent
        if is_clinic_context:
            workflow_desc = f"streamlines patient inquiries and consultation scheduling for {company_ref}"
        else:
            workflow_desc = f"supports {company_ref} with client outreach and communication workflows"

        if intent in ("demo_request", "demo"):
            return (
                f"Hi {first_name},\n\n"
                f"Thank you for reaching out. I would be delighted to walk you through {product_name} "
                f"and share how our platform {workflow_desc}.\n\n"
                f"Are you available for a brief 15-minute demo later this week? Please let me know what day works best for you.\n\n"
                f"Best regards,\n"
                f"{persona_name}"
            )
        elif intent in ("pricing", "pricing_inquiry"):
            return (
                f"Hi {first_name},\n\n"
                f"Thank you for asking about our pricing. We offer flexible pilot packages designed around "
                f"{company_ref}'s workflow requirements and team size.\n\n"
                f"I would be glad to share our pilot overview on a quick 10-minute call, or send the details over email. What works best for you?\n\n"
                f"Best regards,\n"
                f"{persona_name}"
            )
        elif intent in ("meeting_request", "meeting"):
            return (
                f"Hi {first_name},\n\n"
                f"Thank you for getting back to me. I would be glad to connect.\n\n"
                f"I have availability later this week—please let me know what day or time suits you best, or feel free to share a window that works.\n\n"
                f"Best regards,\n"
                f"{persona_name}"
            )
        else:
            return (
                f"Hi {first_name},\n\n"
                f"Thank you for following up. I would be glad to share how {product_name} "
                f"{workflow_desc}.\n\n"
                f"Do you have 15 minutes later this week for a brief overview? Please let me know what day works best for you.\n\n"
                f"Best regards,\n"
                f"{persona_name}"
            )
