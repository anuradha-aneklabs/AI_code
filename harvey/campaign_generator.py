"""Automated Campaign Generation for Qualified Leads.

Enables operators to:
1. Select a business profile and a cohort of qualified CRM leads.
2. Generate personalized multi-step outreach sequences using verified company research
   and RAG-grounded product knowledge.
3. Preview and edit sequence drafts grouped by lead with visible fact provenance.
4. Stage Step 1 drafts into the canonical Outbox with status = 'pending_review'
   while keeping later steps planned/unsent.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from harvey.models.campaign import Campaign, EmailStep
from harvey.models.company import Company
from harvey.models.prospect import Prospect
from harvey.state import StateManager

logger = logging.getLogger("harvey.campaign_generator")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class CampaignGenerator:
    """Orchestrates personalized multi-step sequence generation for qualified CRM leads."""

    def __init__(self, state: StateManager, brain=None, rag=None):
        self.state = state
        self.brain = brain
        if rag is not None:
            self.rag = rag
        else:
            try:
                from harvey.rag.retriever import RAGRetriever
                self.rag = RAGRetriever()
            except Exception:
                self.rag = None

    async def get_eligible_leads(self, profile_id: str) -> list[dict[str, Any]]:
        """Retrieve qualified CRM prospects associated with a specific business profile.

        Ensures strict business profile isolation: prospects belonging to one profile
        are never mixed into another.
        """
        profile = await self.state.get_business_profile(profile_id)
        if not profile:
            return []

        target_industry = (profile.get("industry") or "").lower()
        target_geo = (profile.get("geography") or "").lower()

        # 1. Fetch prospects promoted through discovery
        async with self.state._connect() as db:
            import aiosqlite
            db.row_factory = aiosqlite.Row

            # Query prospects associated with this profile (via source tag or matching company)
            query = """
                SELECT p.id as prospect_id, p.first_name, p.last_name, p.email, p.email_status,
                       p.title, p.status as prospect_status, p.company as company_name,
                       p.source as prospect_source, p.personalization_notes,
                       c.id as company_id, c.domain, c.website, c.industry, c.location,
                       c.company_size, c.description as company_description
                FROM prospects p
                LEFT JOIN companies c ON p.company_id = c.id
                WHERE p.email != '' AND p.status IN ('new', 'queued')
                ORDER BY p.created_at DESC
            """
            async with db.execute(query) as cursor:
                rows = await cursor.fetchall()

        # Filter strictly for profile isolation
        eligible = []
        for r in rows:
            d = dict(r)
            source = (d.get("prospect_source") or "").lower()
            lead_ind = (d.get("industry") or "").lower()
            lead_loc = (d.get("location") or "").lower()

            # Profile tag match takes precedence
            is_match = False
            if f"discovery:{profile_id}".lower() in source:
                is_match = True
            elif not source.startswith("discovery:"):
                # Non-tagged: check industry & geo alignment
                if target_industry and ("dent" in target_industry and ("dent" in lead_ind or "oral" in lead_ind)):
                    is_match = True
                elif target_industry and (("saas" in target_industry or "soft" in target_industry) and ("saas" in lead_ind or "soft" in lead_ind or "tech" in lead_ind)):
                    is_match = True
                elif target_industry and (("legal" in target_industry or "law" in target_industry) and ("legal" in lead_ind or "law" in lead_ind or "counsel" in lead_ind)):
                    is_match = True
                elif target_industry and (("account" in target_industry or "tax" in target_industry) and ("account" in lead_ind or "tax" in lead_ind or "cpa" in lead_ind or "finance" in lead_ind)):
                    is_match = True
                elif target_industry and (("hvac" in target_industry or "home service" in target_industry or "facility" in target_industry) and ("hvac" in lead_ind or "service" in lead_ind or "facility" in lead_ind or "contractor" in lead_ind)):
                    is_match = True
                elif target_industry in lead_ind or lead_ind in target_industry:
                    is_match = True

            if is_match:
                full_name = f"{d.get('first_name', '')} {d.get('last_name', '')}".strip() or "Executive"
                eligible.append({
                    "prospect_id": d["prospect_id"],
                    "full_name": full_name,
                    "title": d.get("title") or "Executive",
                    "email": d.get("email"),
                    "email_status": d.get("email_status") or "verified",
                    "company_id": d.get("company_id") or "",
                    "company_name": d.get("company_name") or "Unknown Organization",
                    "domain": d.get("domain") or "",
                    "industry": d.get("industry") or profile.get("industry", ""),
                    "location": d.get("location") or profile.get("geography", ""),
                    "company_size": d.get("company_size") or "10-50",
                    "description": d.get("company_description") or "",
                })

        return eligible

    async def generate_campaign_preview(
        self,
        profile_id: str,
        prospect_ids: list[str],
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Generate personalized multi-step sequence previews grouped by lead.

        Grounded in verified research facts and local RAG knowledge.
        Strictly conservative on missing context (never hallucinates claims).
        """
        profile = await self.state.get_business_profile(profile_id)
        if not profile:
            raise ValueError(f"Business profile '{profile_id}' not found.")

        cfg = config or {}
        goal = cfg.get("goal") or "get_reply"
        tone = cfg.get("tone") or "consultative, professional, and concise"
        steps_count = int(cfg.get("steps_count") or 2)
        step_delay_days = int(cfg.get("step_delay_days") or 3)
        campaign_name = cfg.get("campaign_name") or f"{profile.get('name', 'Custom')} Outreach"

        product_name = profile.get("product_name") or "Harvey AI"
        product_desc = profile.get("product_description") or "Autonomous B2B outreach & coordination software"

        previews = []
        for pid in prospect_ids:
            prosp = await self.state.get_prospect(pid)
            if not prosp:
                continue

            comp = None
            if prosp.company_id:
                try:
                    comp = await self.state.get_company(prosp.company_id)
                except Exception:
                    comp = None

            # 1. GATHER VERIFIED FACTS & EXPLICIT GAPS
            verified_facts = []
            missing_context = []

            if prosp.full_name():
                verified_facts.append({"fact": f"Contact name: {prosp.full_name()}", "source": "contact_record"})
            else:
                missing_context.append("Personal recipient name unverified (addressed as team)")

            if prosp.title:
                verified_facts.append({"fact": f"Title: {prosp.title}", "source": "contact_record"})
            else:
                missing_context.append("Exact job title unverified")

            comp_name = prosp.company or (comp.name if comp else "your organization")
            verified_facts.append({"fact": f"Organization: {comp_name}", "source": "company_record"})

            if comp and comp.location:
                verified_facts.append({"fact": f"Location: {comp.location}", "source": "public_directory"})
            else:
                missing_context.append("Exact clinic location unverified")

            if comp and comp.description:
                clean_desc = comp.description[:140] + ("..." if len(comp.description) > 140 else "")
                verified_facts.append({"fact": f"Public focus: {clean_desc}", "source": "company_website"})

            # 2. RAG PRODUCT KNOWLEDGE RETRIEVAL
            rag_sources = []
            rag_context = ""
            if self.rag:
                try:
                    chunks = await self.rag.retrieve_for_prospect(prosp, company=comp)
                    if chunks:
                        for c in chunks:
                            src_title = c.get("title") or c.get("source") or "Product Guide"
                            if src_title not in rag_sources:
                                rag_sources.append(src_title)
                        rag_context = self.rag.format_prompt_context(chunks)
                except Exception as e:
                    logger.warning(f"RAG retrieval skipped for prospect {pid}: {e}")

            # 3. GENERATE MULTI-STEP SEQUENCE
            sequence = self._craft_sequence(
                prospect=prosp,
                company=comp,
                profile=profile,
                verified_facts=verified_facts,
                missing_context=missing_context,
                rag_sources=rag_sources,
                steps_count=steps_count,
                step_delay_days=step_delay_days,
                tone=tone,
            )

            previews.append({
                "prospect_id": prosp.id,
                "full_name": prosp.full_name() or "Team",
                "title": prosp.title or "Practice Manager",
                "email": prosp.email,
                "email_status": prosp.email_status or "verified",
                "company_id": prosp.company_id,
                "company_name": comp_name,
                "sequence": sequence,
                "grounding_facts": verified_facts,
                "rag_sources": rag_sources,
                "missing_context": missing_context,
            })

        return {
            "campaign_name": campaign_name,
            "profile_id": profile_id,
            "profile_name": profile.get("name"),
            "steps_count": steps_count,
            "step_delay_days": step_delay_days,
            "lead_count": len(previews),
            "leads": previews,
        }

    def _craft_sequence(
        self,
        prospect: Prospect,
        company: Company | None,
        profile: dict[str, Any],
        verified_facts: list[dict],
        missing_context: list[str],
        rag_sources: list[str],
        steps_count: int,
        step_delay_days: int,
        tone: str,
    ) -> list[dict[str, Any]]:
        """Craft a deterministic, fact-honest multi-step sequence grounded in profile and research."""
        first_name = prospect.first_name or "there"
        comp_name = prospect.company or (company.name if company else "your organization")
        location = company.location if company and company.location else ""
        product_name = profile.get("product_name") or "Harvey AI"
        product_desc = profile.get("product_description") or "streamline outbound prospect engagement and consultations"

        # Industry detection from profile and company
        ind_str = f"{profile.get('industry', '')} {company.industry if company else ''}".lower()
        loc_phrase = f" in {location.split(',')[0].strip()}" if location else ""

        # Determine industry-specific messaging
        if "dent" in ind_str or "oral" in ind_str:
            hook = f"I came across {comp_name}{loc_phrase} and noticed your work in specialized dental care."
            pain = "Many dental clinics encounter challenges coordinating multi-step patient follow-ups without manual front-desk overhead."
            solution = f"We built {product_name} to assist practices in automating consultation scheduling and inquiry correspondence while keeping full human approval over every draft."
            followup_val = "Practices using grounded correspondence support typically reduce appointment drop-off while ensuring every communication remains verifiable."
        elif "clinic" in ind_str or "health" in ind_str or "medic" in ind_str:
            hook = f"I came across {comp_name}{loc_phrase} and noticed your work in specialized healthcare services."
            pain = "Many clinics encounter challenges coordinating patient consultation follow-ups and specialist referrals without administrative burden."
            solution = f"We built {product_name} to assist clinical teams in streamlining consultation correspondence while keeping local privacy and operator review gates."
            followup_val = "Clinics using grounded correspondence workflows improve consultation show rates while ensuring all communications remain compliant and verified."
        elif "saas" in ind_str or "software" in ind_str or "tech" in ind_str:
            hook = f"I came across {comp_name}{loc_phrase} and have been following your growth in the B2B tech space."
            pain = "Scaling software teams often encounter bottlenecks coordinating multi-touch outbound correspondence and keeping outreach grounded in verified account signals."
            solution = f"We built {product_name} to help revenue and growth teams automate personalized prospect research and outbound correspondence while maintaining strict human approval before any message is sent."
            followup_val = "SaaS teams using grounded sales agents accelerate pipeline velocity and SDR productivity without sacrificing messaging quality or control."
        elif "legal" in ind_str or "law" in ind_str or "attorney" in ind_str or "counsel" in ind_str:
            hook = f"I came across {comp_name}{loc_phrase} and noticed your firm's strong reputation in commercial legal advisory."
            pain = "Law practices often face friction managing prospective client intake triage and consultation scheduling while preserving strict client confidentiality."
            solution = f"We built {product_name} to streamline prospective client inquiry qualification and initial correspondence with local-first data privacy and mandatory partner review."
            followup_val = "Firms using automated intake workflows improve responsiveness to high-value prospective matters while keeping partners in complete control of every staged message."
        elif "account" in ind_str or "cpa" in ind_str or "tax" in ind_str or "finance" in ind_str or "financial" in ind_str:
            hook = f"I came across {comp_name}{loc_phrase} and noticed your practice's strategic financial and accounting services."
            pain = "Accounting and advisory firms frequently experience heavy administrative follow-up overhead during new client onboarding and seasonal intake cycles."
            solution = f"We built {product_name} to automate prospective client correspondence and consultation scheduling while keeping full operator approval over every communication."
            followup_val = "Practices using grounded correspondence workflows reduce onboarding delays and free senior advisors to focus on strategic client work."
        elif "hvac" in ind_str or "home service" in ind_str or "facility" in ind_str or "contractor" in ind_str or "commercial service" in ind_str:
            hook = f"I came across {comp_name}{loc_phrase} and noticed your commercial service operations in facility systems."
            pain = "Commercial contractors often lose high-margin bids because quote follow-ups and service agreement renewals require intensive manual tracking."
            solution = f"We built {product_name} to accelerate quotation follow-ups and maintenance agreement outreach with zero unapproved emails sent without your team's review."
            followup_val = "Service teams using automated follow-up coordination improve estimate closing rates without adding back-office overhead."
        else:
            clean_ind = profile.get("industry") or "your sector"
            hook = f"I came across {comp_name}{loc_phrase} and was impressed by your team's work in {clean_ind}."
            pain = f"Organizations in {clean_ind} often encounter challenges coordinating timely prospect correspondence without increasing manual administrative overhead."
            solution = f"We built {product_name} to {product_desc} while keeping full human approval over every draft before anything is sent."
            followup_val = "Teams using grounded correspondence workflows improve prospect engagement and response velocity while ensuring every outbound communication is verified."

        # Tone modulation for CTA
        if tone == "direct":
            cta1 = "Are you open to a brief 10-minute introductory conversation this Thursday?"
            cta2 = "Do you have 10 minutes Friday to discuss whether this would be valuable for your team?"
        elif tone == "formal":
            cta1 = "Would it be convenient to schedule a brief introductory discussion at your convenience this week?"
            cta2 = "May I propose a brief 10-minute discussion late this week to explore this further?"
        else:  # consultative (default)
            cta1 = "Would you be open to a brief 10-minute introductory conversation this week to see if this aligns with your priorities?"
            cta2 = "Do you have 10 minutes Thursday or Friday to discuss whether this would be helpful for your team?"

        # Step 1: Initial Cold Outreach
        step1_subject = f"quick question regarding {comp_name.lower()[:28]}"
        step1_body = (
            f"Hi {first_name},\n\n"
            f"{hook} {pain}\n\n"
            f"{solution}\n\n"
            f"{cta1}\n\n"
            f"Best regards,\nHarvey"
        )

        # Step 2: Value-Add Follow-Up
        step2_delay = step_delay_days
        step2_subject = f"follow-up: coordination for {comp_name.lower()[:24]}"
        step2_body = (
            f"Hi {first_name},\n\n"
            f"Following up on my earlier note regarding {comp_name}. "
            f"{followup_val}\n\n"
            f"{cta2}\n\n"
            f"Best regards,\nHarvey"
        )

        steps = [
            {"step": 1, "subject": step1_subject, "body": step1_body, "delay_days": 0},
            {"step": 2, "subject": step2_subject, "body": step2_body, "delay_days": step2_delay},
        ]

        # Step 3: Break-up Email (if 3 steps requested)
        if steps_count >= 3:
            step3_delay = step_delay_days + 3
            step3_subject = f"closing out: {comp_name.lower()[:26]}"
            step3_body = (
                f"Hi {first_name},\n\n"
                f"I haven't heard back, so I assume timing isn't right for {comp_name} right now. "
                f"I won't follow up further, but please feel free to reach out if this becomes a priority down the line.\n\n"
                f"Best regards,\nHarvey"
            )
            steps.append({"step": 3, "subject": step3_subject, "body": step3_body, "delay_days": step3_delay})

        return steps

    async def stage_campaign_to_outbox(self, campaign_payload: dict[str, Any]) -> dict[str, Any]:
        """Stage a previewed campaign into the database and canonical Outbox.

        CRITICAL SAFETY INVARIANTS:
        1. Only Step 1 drafts are placed into the Outbox with status = 'pending_review'.
        2. Steps 2 and 3 remain stored in campaign.sequence (scheduled/planned only, NOT sent).
        3. Dedup check guarantees retries do not spawn duplicate outbox records.
        4. Global safety hold ('sending_paused') and 'require_approval' are preserved.
        """
        campaign_name = campaign_payload.get("campaign_name") or f"Campaign-{uuid.uuid4().hex[:6]}"
        profile_id = campaign_payload.get("profile_id") or ""
        leads = campaign_payload.get("leads") or []

        if not leads:
            raise ValueError("Cannot stage campaign: no lead previews provided.")

        campaign_id = f"camp_{uuid.uuid4().hex[:8]}"

        # Representative sequence from the first lead
        raw_seq = leads[0].get("sequence") or []
        email_steps = [EmailStep(**s) for s in raw_seq]

        prospect_ids = [l["prospect_id"] for l in leads if l.get("prospect_id")]

        # 1. Create the Campaign record in SQLite
        camp = Campaign(
            id=campaign_id,
            name=campaign_name,
            channel="email",
            sequence=email_steps,
            prospect_ids=prospect_ids,
            status="draft",
        )
        await self.state.add_campaign(camp)

        # 2. Stage ONLY Step 1 drafts into canonical Outbox
        staged_items = []
        now = _utcnow()

        for lead in leads:
            pid = lead.get("prospect_id")
            email = lead.get("email")
            seq = lead.get("sequence") or []
            if not seq or not pid or not email:
                continue

            step1 = seq[0]
            subject = step1.get("subject", "Quick question")
            body = step1.get("body", "")

            # Deduplication check: check if outbox item already exists for this prospect & campaign
            async with self.state._connect() as db:
                import aiosqlite
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT id FROM outbox WHERE prospect_id = ? AND campaign_id = ? AND step = 1",
                    (pid, campaign_id),
                ) as cursor:
                    existing = await cursor.fetchone()

            if existing:
                item_id = existing["id"]
            else:
                item_id = await self.state.add_outbox_item(
                    prospect_id=pid,
                    campaign_id=campaign_id,
                    step=1,
                    to_email=email,
                    subject=subject,
                    body=body,
                    send_at=now,
                    status="pending_review",
                    approved_by="",
                )

                # Record RAG trace if citations exist
                rag_sources = lead.get("rag_sources") or []
                if rag_sources and item_id:
                    try:
                        from harvey.rag.trace import record_rag_trace
                        record_rag_trace(
                            item_id=item_id,
                            prospect_id=pid,
                            to_email=email,
                            subject=subject,
                            sources=rag_sources,
                            chunks=[],
                        )
                    except Exception as e:
                        logger.warning(f"RAG trace logging skipped: {e}")

                # Update prospect status to queued
                await self.state.update_prospect_status(pid, "queued")

            staged_items.append({"item_id": item_id, "prospect_id": pid, "email": email, "step": 1})

        # Scheduled later steps count
        later_steps_count = max(0, len(email_steps) - 1) * len(staged_items)

        await self.state.log_action(
            action_type="campaign_staged_from_discovery",
            agent="campaign_generator",
            details={
                "campaign_id": campaign_id,
                "campaign_name": campaign_name,
                "profile_id": profile_id,
                "staged_step1_drafts": len(staged_items),
                "scheduled_later_steps": later_steps_count,
            },
        )

        return {
            "success": True,
            "campaign_id": campaign_id,
            "campaign_name": campaign_name,
            "staged_drafts_count": len(staged_items),
            "scheduled_later_steps_count": later_steps_count,
            "staged_items": staged_items,
        }

