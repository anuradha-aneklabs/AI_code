"""AI-Powered Personalized Outreach Email Drafting Engine.

Generates relevant, consultative, evidence-grounded outreach email drafts
for qualified leads using verified company research, ICP criteria, and
grounded product knowledge.

Enforces:
1. Strict Grounding: Uses ONLY facts supported by verified research provenance.
2. Anti-Hallucination: Does not invent claims, funding rounds, mutual contacts,
   metrics, or personal recipient details.
3. Deliverability Honesty: Disqualifies or flags guessed/risky emails and addresses
   teams neutrally when personal contact is unverified.
4. Qualification Guard: Rejects outreach draft generation for unqualified leads.
5. Review Workflow: Supports generate, preview, regenerate, edit, save,
   mark ready for manual review, and cancel actions.
6. Safety Controls: Staged drafts in Outbox remain pending_review without automated sending.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from harvey.brain import Brain
from harvey.config import HarveyConfig, load_config
from harvey.state import StateManager
from harvey.untrusted import UNTRUSTED_RULE, fence

logger = logging.getLogger("harvey.outreach_drafts")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def is_valid_contact_name(name: str | None) -> bool:
    """Check if recipient name is an actual personal contact name rather than a placeholder."""
    if not name:
        return False
    n = name.strip().lower()
    invalid_tokens = ("unknown", "team", "general inbox", "unverified", "n/a", "none", "null")
    if n in invalid_tokens:
        return False
    if "unknown" in n or "unverified" in n:
        return False
    return True


@dataclass
class OutreachDraftContext:
    """Consolidated research and profile context for drafting outreach."""
    lead_id: str = ""
    prospect_id: str = ""
    company_id: str = ""
    profile_id: str = ""
    company_name: str = ""
    domain: str = ""
    website: str = ""
    industry: str = ""
    location: str = ""
    city: str = ""
    state_region: str = ""
    country: str = ""
    company_size: str = ""
    description: str = ""
    recipient_name: str = ""
    recipient_title: str = ""
    recipient_email: str = ""
    email_status: str = "unverified"  # verified, guess, risky, unverified
    is_qualified: bool = True
    score: int = 80
    tier: str = "High Fit"
    confidence: float = 0.85
    verified_facts: list[dict[str, Any]] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    potential_needs: list[str] = field(default_factory=list)
    research_sources: list[dict[str, Any]] = field(default_factory=list)
    product_name: str = "Harvey AI"
    product_description: str = "Autonomous sales agent for prospect discovery and outreach"

    def to_dict(self) -> dict[str, Any]:
        return {
            "lead_id": self.lead_id,
            "prospect_id": self.prospect_id,
            "company_id": self.company_id,
            "profile_id": self.profile_id,
            "company_name": self.company_name,
            "domain": self.domain,
            "website": self.website,
            "industry": self.industry,
            "location": self.location,
            "city": self.city,
            "state_region": self.state_region,
            "country": self.country,
            "company_size": self.company_size,
            "description": self.description,
            "recipient_name": self.recipient_name,
            "recipient_title": self.recipient_title,
            "recipient_email": self.recipient_email,
            "email_status": self.email_status,
            "is_qualified": self.is_qualified,
            "score": self.score,
            "tier": self.tier,
            "confidence": self.confidence,
            "verified_facts": self.verified_facts,
            "missing_fields": self.missing_fields,
            "potential_needs": self.potential_needs,
            "research_sources": self.research_sources,
            "product_name": self.product_name,
            "product_description": self.product_description,
        }


class OutreachDraftGenerator:
    """Orchestrates grounded, verified outreach email drafting and lifecycle."""

    def __init__(
        self,
        state: StateManager,
        brain: Brain | None = None,
        config: HarveyConfig | None = None,
    ):
        self.state = state
        self.config = config
        self.brain = brain
        if self.brain is None:
            try:
                cfg = self.config or load_config()
                self.brain = Brain(state, brain_config=cfg.brain)
            except Exception as e:
                logger.warning(f"OutreachDraftGenerator: Brain initialization skipped or failed: {e}")
                self.brain = None

    async def build_context(
        self,
        lead_id: str | None = None,
        prospect_id: str | None = None,
        profile_id: str | None = None,
    ) -> OutreachDraftContext:
        """Collect verified research facts and ICP criteria for a lead or prospect."""
        ctx = OutreachDraftContext(lead_id=lead_id or "", prospect_id=prospect_id or "", profile_id=profile_id or "")

        # 1. Load Business Profile product info
        profile = None
        if profile_id:
            profile = await self.state.get_business_profile(profile_id)
        if not profile:
            profiles = await self.state.get_business_profiles()
            profile = next((p for p in profiles if p.get("is_default")), profiles[0] if profiles else {})

        if profile:
            ctx.profile_id = profile.get("id", "")
            ctx.product_name = profile.get("product_name") or "Harvey AI"
            ctx.product_description = profile.get("product_description") or "Autonomous sales agent for prospect discovery and outreach"

        # 2. Case A: Built from Discovered Lead (Discovery Tab)
        if lead_id:
            lead = await self.state.get_discovered_lead(lead_id)
            if not lead:
                raise ValueError(f"Discovered lead '{lead_id}' not found.")

            ctx.company_name = lead.get("company_name") or "Organization"
            ctx.domain = lead.get("domain") or ""
            ctx.website = lead.get("website") or (f"https://{ctx.domain}" if ctx.domain else "")
            ctx.industry = lead.get("industry") or ""
            ctx.location = lead.get("location") or ""
            ctx.city = lead.get("city") or ""
            ctx.state_region = lead.get("state_region") or ""
            ctx.country = lead.get("country") or ""
            ctx.company_size = lead.get("company_size") or ""
            ctx.description = lead.get("description") or ""

            # Qualification check
            ctx.score = int(lead.get("score") or 0)
            ctx.tier = lead.get("tier") or "Scored"
            ctx.confidence = float(lead.get("confidence") or 0.85)
            # Must meet minimum qualification threshold (score >= 60 and not explicitly disqualified)
            ctx.is_qualified = bool(lead.get("is_qualified", ctx.score >= 60)) and (ctx.score >= 60)

            # Decision maker extraction
            dm = lead.get("decision_maker") or {}
            ctx.recipient_name = dm.get("name") or ""
            ctx.recipient_title = dm.get("title") or ""
            ctx.recipient_email = dm.get("email") or ""
            ctx.email_status = dm.get("email_status") or "unverified"

            # Facts and sources
            raw_sources = lead.get("research_sources") or lead.get("sources") or []
            ctx.research_sources = [{"title": s.get("source_title") or s.get("source", "Public Registry"), "url": s.get("source_url") or s.get("url", "#")} for s in raw_sources if isinstance(s, dict)]
            ctx.potential_needs = lead.get("potential_needs") or []
            ctx.missing_fields = list(lead.get("missing_fields") or [])

            # Compile verified facts
            if ctx.company_name:
                ctx.verified_facts.append({"field": "Company Name", "value": ctx.company_name, "source": "Public Registry"})
            if ctx.location:
                ctx.verified_facts.append({"field": "Location", "value": ctx.location, "source": "Municipal / Business Directory"})
            if ctx.description:
                ctx.verified_facts.append({"field": "Public Business Focus", "value": ctx.description[:180], "source": "Official Website"})
            if ctx.potential_needs:
                ctx.verified_facts.append({"field": "Verified Operational Use Cases", "value": "; ".join(ctx.potential_needs[:3]), "source": "Services Directory"})

        # 3. Case B: Built from Prospect & Company (CRM Pipeline Tab)
        elif prospect_id:
            prospect = await self.state.get_prospect(prospect_id)
            if not prospect:
                raise ValueError(f"Prospect '{prospect_id}' not found.")

            ctx.prospect_id = prospect.id
            ctx.company_id = prospect.company_id or ""
            ctx.recipient_name = prospect.full_name() or ""
            ctx.recipient_title = prospect.title or ""
            ctx.recipient_email = prospect.email or ""
            ctx.email_status = prospect.email_status or ("verified" if prospect.email_verified else "unverified")

            # Check company
            company = await self.state.get_company(prospect.company_id) if prospect.company_id else None
            if company:
                ctx.company_name = company.name or prospect.company or "Organization"
                ctx.domain = company.domain or ""
                ctx.website = company.website or (f"https://{ctx.domain}" if ctx.domain else "")
                ctx.industry = company.industry or prospect.industry or ""
                ctx.location = company.location or ""
                ctx.company_size = company.company_size or prospect.company_size or ""
                ctx.description = company.description or ""
            else:
                ctx.company_name = prospect.company or "Organization"
                ctx.industry = prospect.industry or ""
                ctx.company_size = prospect.company_size or ""

            # Check research & qualification
            research = await self.state.get_lead_research(prospect_id)
            if research:
                ctx.score = int(research.get("fit_score") or prospect.score or 0)
                ctx.tier = research.get("fit_tier") or "Scored"
                ctx.confidence = float(research.get("confidence") or 0.85)
                ctx.is_qualified = ctx.score >= 60
                ctx.potential_needs = research.get("potential_needs") or []
                ctx.missing_fields = list(research.get("unknown_fields") or [])
                raw_sources = research.get("sources") or []
                ctx.research_sources = [{"title": s.get("source_title") or s.get("source", "Directory"), "url": s.get("source_url") or s.get("url", "#")} for s in raw_sources if isinstance(s, dict)]
            else:
                ctx.score = int(prospect.score or 0)
                ctx.tier = "High Fit" if ctx.score >= 70 else ("Moderate Fit" if ctx.score >= 40 else "Low Fit")
                ctx.confidence = 0.85
                ctx.is_qualified = ctx.score >= 60

            if ctx.company_name:
                ctx.verified_facts.append({"field": "Company Name", "value": ctx.company_name, "source": "CRM Record"})
            if ctx.location:
                ctx.verified_facts.append({"field": "Location", "value": ctx.location, "source": "CRM Record"})
            if ctx.description:
                ctx.verified_facts.append({"field": "Public Business Focus", "value": ctx.description[:180], "source": "Official Website"})

        # Flag missing or risky email deliverability
        if ctx.email_status in ("guess", "risky"):
            ctx.missing_fields.append(f"Recipient email ({ctx.recipient_email}) is unconfirmed / risky deliverability (manual verification required before sending)")

        if not is_valid_contact_name(ctx.recipient_name):
            ctx.missing_fields.append("Personal recipient contact name unverified (addressed neutrally as team)")

        return ctx

    async def generate_draft(
        self,
        context: OutreachDraftContext,
        custom_instructions: str = "",
        force_ai: bool = True,
        fallback_mode: bool = False,
    ) -> dict[str, Any]:
        """Generate an evidence-grounded outreach email draft for a qualified lead.

        Enforces strict hallucination prevention and graceful error handling when
        AI providers are unavailable or malformed.
        """
        # 1. Qualification Pre-Condition Guard
        if not context.is_qualified and context.score < 60:
            return {
                "success": False,
                "error": f"Lead '{context.company_name}' is not qualified (ICP fit score: {context.score}/100, Tier: {context.tier}). Outreach drafts can only be generated for qualified leads meeting ICP criteria.",
                "code": "lead_unqualified",
                "score": context.score,
                "tier": context.tier,
            }

        # 2. Format Grounded Facts for Prompt
        facts_list = []
        for f in context.verified_facts:
            facts_list.append(f"- {f['field']}: {f['value']} (Source: {f['source']})")
        if is_valid_contact_name(context.recipient_name):
            facts_list.append(f"- Contact Name: {context.recipient_name}")
            if context.recipient_title:
                facts_list.append(f"- Contact Title: {context.recipient_title}")
        else:
            facts_list.append("- Contact: Unknown person. Addressed as team/organization.")

        if context.potential_needs:
            facts_list.append(f"- Verified Operational Needs: {', '.join(context.potential_needs)}")

        facts_block = "\n".join(facts_list) if facts_list else "- Organization: " + context.company_name

        missing_block = "\n".join(f"- {m}" for m in context.missing_context if hasattr(context, "missing_context")) if hasattr(context, "missing_context") else ""
        if context.missing_fields:
            missing_block += "\n" + "\n".join(f"- {m}" for m in context.missing_fields)

        # 3. Handle Deterministic Fallback Mode (for offline test validation or when AI is bypassed)
        if fallback_mode:
            draft_res = self._craft_grounded_fallback(context, custom_instructions)
            return {
                "success": True,
                "draft": draft_res,
                "mode": "deterministic_grounded",
            }

        # 4. Strict Grounding AI Prompt Construction
        system_instructions = (
            "You are Harvey, a consultative, highly professional B2B sales outreach agent.\n"
            "You write authentic, personalized cold emails grounded strictly in verified research facts.\n\n"
            "STRICT GROUNDING & ANTI-HALLUCINATION RULES:\n"
            "1. Use ONLY the verified facts provided below. Do NOT invent customer names, past conversations, mutual contacts, awards, metrics, or revenue figures.\n"
            "2. Never pretend that a contact has previously interacted with our company or visited our website.\n"
            "3. If personal recipient name is unverified, open with 'Hi [Company Name] Team,' or 'Hello,'. Do not make up a contact name.\n"
            "4. Connect our product/service value proposition to their verified public focus or workflow needs in 1-2 concise sentences.\n"
            "5. Keep the email body natural, professional, and concise (under 120 words). No pushy hype, buzzwords, or token-swap templates.\n"
            "6. Provide a low-friction, consultative call to action (e.g. asking a relevant workflow question or a brief 10-minute exchange).\n"
            "7. Explain why this email was personalized specifically for this company based on verified research."
        )

        user_prompt = f"""Write ONE personalized outreach email to this prospective organization.

VERIFIED RESEARCH FACTS (Use ONLY these facts; do not assume anything unlisted):
{fence(facts_block, "verified_research_facts")}

MISSING / UNVERIFIED CONTEXT (Do not invent or guess any of these):
{fence(missing_block or "None explicitly noted.", "missing_context")}

OUR PRODUCT / SERVICE OFFERING:
- Product: {context.product_name}
- Focus: {context.product_description}

{f"OPERATOR SPECIAL INSTRUCTIONS: {custom_instructions}" if custom_instructions else ""}

{UNTRUSTED_RULE}

Return ONLY a valid JSON object matching this schema:
{{
  "subject": "concise lowercase subject line (2-5 words)",
  "body": "natural, personalized email body under 120 words with greeting and consultative sign-off",
  "value_proposition": "1-2 sentence tailored value proposition relevant to their verified focus",
  "call_to_action": "low-friction consultative call to action",
  "personalization_explanation": "1-2 sentences explaining exactly why this email was tailored to this company's verified research"
}}"""

        # 5. Execute via Brain with Graceful Error Handling
        if self.brain is None:
            return {
                "success": False,
                "error": "AI provider is not configured or unavailable. Please configure an AI provider in harvey.yaml.",
                "code": "ai_unavailable",
            }

        try:
            full_prompt = system_instructions + "\n\n" + user_prompt
            ai_response = await self.brain.think_json(
                full_prompt,
                session_id=f"outreach_{context.company_name.lower().replace(' ', '_')[:20]}",
                agent="writer",
                task="outreach_draft",
            )
        except Exception as e:
            logger.error(f"AI provider failed during draft generation: {e}")
            return {
                "success": False,
                "error": f"AI provider '{getattr(self.brain, 'provider', 'active')}' returned an error: {str(e)}",
                "code": "ai_unavailable",
            }

        # Check for unavailable provider or empty response
        if getattr(self.brain, "last_reply_empty", False) or not ai_response:
            return {
                "success": False,
                "error": f"AI provider '{getattr(self.brain, 'provider', 'active')}' is currently unavailable or returned an empty response. Please verify provider connectivity and credentials.",
                "code": "ai_unavailable",
            }

        # Validate response structure
        if not isinstance(ai_response, dict):
            return {
                "success": False,
                "error": "AI provider returned malformed response (expected JSON object). Please regenerate.",
                "code": "malformed_response",
            }

        subject = str(ai_response.get("subject") or "").strip()
        body = str(ai_response.get("body") or "").strip()
        value_prop = str(ai_response.get("value_proposition") or "").strip()
        cta = str(ai_response.get("call_to_action") or "").strip()
        explanation = str(ai_response.get("personalization_explanation") or "").strip()

        if not subject or not body:
            return {
                "success": False,
                "error": "AI provider returned incomplete draft output (missing subject or body).",
                "code": "malformed_response",
            }

        draft = {
            "lead_id": context.lead_id,
            "prospect_id": context.prospect_id,
            "company_id": context.company_id,
            "profile_id": context.profile_id,
            "company_name": context.company_name,
            "recipient_name": context.recipient_name if is_valid_contact_name(context.recipient_name) else (f"{context.company_name} Team" if context.company_name else "Team"),
            "recipient_email": context.recipient_email,
            "email_status": context.email_status,
            "subject": subject[:120],
            "body": body[:2000],
            "value_proposition": value_prop or f"Tailored solutions for {context.industry}",
            "call_to_action": cta or "Are you open to a brief 10-minute exploratory conversation this week?",
            "personalization_explanation": explanation or f"Referenced verified location ({context.location}) and specialization in {context.industry}.",
            "research_sources": context.research_sources,
            "verified_facts": context.verified_facts,
            "missing_fields": context.missing_fields,
            "status": "draft",
            "generation_method": "ai",
            "generated_at": _utcnow().isoformat(),
        }

        return {
            "success": True,
            "draft": draft,
            "mode": "ai",
        }

    def _craft_grounded_fallback(
        self,
        context: OutreachDraftContext,
        custom_instructions: str = "",
    ) -> dict[str, Any]:
        """Deterministic, evidence-grounded draft generator for offline validation."""
        # Salutation
        if is_valid_contact_name(context.recipient_name):
            salutation = f"Hi {context.recipient_name},"
            rec_name = context.recipient_name
        else:
            salutation = f"Hi {context.company_name} Team,"
            rec_name = f"{context.company_name} Team" if context.company_name else "Team"

        # Personalization observation
        focus_snippet = context.description[:100].strip() if context.description else f"{context.industry} operations"
        loc_phrase = f" in {context.city}" if context.city else (f" in {context.location}" if context.location else "")

        # Value proposition
        if context.potential_needs:
            need = context.potential_needs[0]
            val_prop = f"We help organizations like {context.company_name} streamline {need.lower()} using {context.product_name}."
            explanation = f"Personalized based on verified practice focus in {context.industry}{loc_phrase} and identified priority for {need.lower()}."
        else:
            val_prop = f"{context.product_name} helps teams in {context.industry} automate prospect engagement while maintaining strict oversight."
            explanation = f"Personalized based on public registry records for {context.company_name}{loc_phrase} and focus on {focus_snippet}."

        cta = "Would you be open to a brief 10-minute workflow exchange later this week?"
        subject = f"question regarding {context.company_name.lower()}{' in ' + context.city.lower() if context.city else ''}"

        body = (
            f"{salutation}\n\n"
            f"I came across {context.company_name}{loc_phrase} and noticed your work in {focus_snippet}.\n\n"
            f"{val_prop}\n\n"
            f"{cta}\n\n"
            f"Best regards,\n"
            f"Harvey AI Support Team"
        )

        return {
            "lead_id": context.lead_id,
            "prospect_id": context.prospect_id,
            "company_id": context.company_id,
            "profile_id": context.profile_id,
            "company_name": context.company_name,
            "recipient_name": rec_name,
            "recipient_email": context.recipient_email,
            "email_status": context.email_status,
            "subject": subject[:120],
            "body": body,
            "value_proposition": val_prop,
            "call_to_action": cta,
            "personalization_explanation": explanation,
            "research_sources": context.research_sources,
            "verified_facts": context.verified_facts,
            "missing_fields": context.missing_fields,
            "status": "draft",
            "generation_method": "deterministic_grounded",
            "generated_at": _utcnow().isoformat(),
        }

    async def save_draft(
        self,
        draft_data: dict[str, Any],
        force: bool = False,
    ) -> dict[str, Any]:
        """Save an outreach draft and update the CRM pipeline to 'Outreach Draft Ready'."""
        lead_id = draft_data.get("lead_id")
        prospect_id = draft_data.get("prospect_id")

        # 1. Check for existing active or approved draft
        if lead_id or prospect_id:
            existing_drafts = await self.state.get_outreach_drafts(lead_id=lead_id, prospect_id=prospect_id)
            active_draft = next((d for d in existing_drafts if d.get("status") in ("ready_for_review", "approved")), None)

            if active_draft and not force:
                return {
                    "success": False,
                    "error": f"An existing draft ({active_draft['id']}) is already in '{active_draft['status']}' status. Confirm overwrite with force=True to replace.",
                    "code": "draft_exists",
                    "existing_draft_id": active_draft["id"],
                    "existing_status": active_draft["status"],
                }

        # 2. If lead is from Discovery and not yet in CRM, promote to CRM pipeline
        if lead_id and not prospect_id:
            try:
                res = await self.state.add_discovered_lead_to_pipeline(lead_id)
                prospect_id = res.get("prospect_id")
                draft_data["prospect_id"] = prospect_id
                draft_data["company_id"] = res.get("company_id")
            except Exception as e:
                logger.warning(f"Could not link discovered lead to CRM: {e}")

        # 3. Update CRM pipeline stage to 'Outreach Draft Ready'
        if prospect_id:
            try:
                await self.state.update_prospect_pipeline(
                    prospect_id,
                    stage="Outreach Draft Ready",
                    notes=f"Personalized outreach draft saved: '{draft_data.get('subject', '')[:50]}'",
                    next_action="Operator manual review and approval of email draft",
                )
            except Exception as e:
                logger.warning(f"Could not update pipeline stage for prospect {prospect_id}: {e}")

        # 4. Save into outreach_drafts table
        draft_id = await self.state.save_outreach_draft(draft_data)

        return {
            "success": True,
            "draft_id": draft_id,
            "status": draft_data.get("status", "draft"),
            "pipeline_stage": "Outreach Draft Ready",
            "prospect_id": prospect_id,
        }

    async def mark_ready_for_review(self, draft_id: str) -> dict[str, Any]:
        """Transition draft status to 'ready_for_review' and queue an Outbox item."""
        draft = await self.state.get_outreach_draft(draft_id)
        if not draft:
            return {"success": False, "error": f"Draft '{draft_id}' not found.", "code": "not_found"}

        recipient_email = draft.get("recipient_email") or ""
        if not recipient_email:
            return {
                "success": False,
                "error": "Cannot mark draft ready for review: Recipient email address is missing.",
                "code": "missing_email",
            }

        # Stage or update canonical Outbox item with status 'pending_review'
        outbox_id = draft.get("outbox_id")
        if outbox_id:
            await self.state.edit_outbox_draft(outbox_id, draft.get("subject", ""), draft.get("body", ""))
            # Ensure it remains pending_review
            async with self.state._connect() as db:
                await db.execute("UPDATE outbox SET status = 'pending_review' WHERE id = ?", (outbox_id,))
                await db.commit()
        else:
            outbox_id = await self.state.add_outbox_item(
                prospect_id=draft.get("prospect_id") or "",
                to_email=recipient_email,
                subject=draft.get("subject") or "Outreach Inquiry",
                body=draft.get("body") or "",
                send_at=_utcnow().isoformat(),
                status="pending_review",
                kind="lead_outreach",
            )
            draft["outbox_id"] = outbox_id

        await self.state.update_outreach_draft(
            draft_id,
            status="ready_for_review",
            outbox_id=outbox_id or "",
        )

        return {
            "success": True,
            "draft_id": draft_id,
            "outbox_id": outbox_id,
            "status": "ready_for_review",
        }

    async def edit_draft(
        self,
        draft_id: str,
        subject: str,
        body: str,
        recipient_email: str | None = None,
    ) -> dict[str, Any]:
        """Edit subject and body copy in draft and linked Outbox item."""
        draft = await self.state.get_outreach_draft(draft_id)
        if not draft:
            return {"success": False, "error": f"Draft '{draft_id}' not found.", "code": "not_found"}

        updates: dict[str, Any] = {"subject": subject.strip(), "body": body.strip()}
        if recipient_email is not None:
            updates["recipient_email"] = recipient_email.strip()

        await self.state.update_outreach_draft(draft_id, **updates)

        outbox_id = draft.get("outbox_id")
        if outbox_id:
            await self.state.edit_outbox_draft(outbox_id, subject.strip(), body.strip())

        return {"success": True, "draft_id": draft_id}

    async def cancel_draft(self, draft_id: str, reason: str = "") -> dict[str, Any]:
        """Cancel an outreach draft and its associated outbox item."""
        ok = await self.state.cancel_outreach_draft(draft_id, reason=reason)
        if not ok:
            return {"success": False, "error": f"Draft '{draft_id}' not found.", "code": "not_found"}

        return {"success": True, "draft_id": draft_id, "status": "cancelled"}
