"""Campaign Review & Approval Workflow.

Enables operators to:
1. View generated campaigns and their Step 1 drafts in a dedicated review queue.
2. Open, inspect, and edit subject and body copy before approval.
3. Explicitly approve or reject drafts.
4. Require a non-empty rejection reason on reject and store it in both the
   feedback and actions tables for audit.
5. Keep Step 2 and Step 3 strictly as planned sequence steps without staging them in Outbox.
6. Record full audit history (action, timestamp, previous status, new status, rejection reason).
7. Ensure idempotent approvals and duplicate action prevention.
8. Enforce profile isolation across multi-profile campaigns.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from harvey.gate import pre_send_check
from harvey.state import StateManager

logger = logging.getLogger("harvey.campaign_review")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class CampaignReviewManager:
    """Manages review, approval, rejection, and audit trails for generated campaigns."""

    def __init__(self, state: StateManager):
        self.state = state

    async def get_campaign_review(self, campaign_id: str) -> dict[str, Any]:
        """Fetch full review details for a campaign including Step 1 drafts, planned steps, and audit log."""
        campaign = await self.state.get_campaign(campaign_id)
        if not campaign:
            return {
                "success": False,
                "message": f"Campaign '{campaign_id}' not found.",
                "campaign": None,
                "step_1_drafts": [],
                "planned_steps": [],
                "audit_history": [],
            }

        # Retrieve Step 1 drafts from Outbox with recipient context
        async with self.state._connect() as db:
            db.row_factory = aiosqlite.Row
            query = """
                SELECT o.id, o.prospect_id, o.campaign_id, o.step, o.to_email,
                       o.subject, o.body, o.status, o.approved_by, o.error as rejection_reason,
                       o.send_at, o.created_at, o.updated_at,
                       p.first_name, p.last_name, p.title, p.company, p.source as prospect_source
                FROM outbox o
                LEFT JOIN prospects p ON o.prospect_id = p.id
                WHERE o.campaign_id = ? AND o.step = 1
                ORDER BY o.created_at ASC
            """
            async with db.execute(query, (campaign_id,)) as cursor:
                step_1_rows = await cursor.fetchall()

            # Safety check: count any later steps in outbox (must be 0)
            async with db.execute(
                "SELECT COUNT(*) FROM outbox WHERE campaign_id = ? AND step > 1",
                (campaign_id,),
            ) as cursor:
                later_in_outbox = (await cursor.fetchone())[0]

        step_1_drafts = []
        for r in step_1_rows:
            d = dict(r)
            full_name = f"{d.get('first_name') or ''} {d.get('last_name') or ''}".strip()
            d["recipient_name"] = full_name or d.get("to_email") or "Prospect"
            step_1_drafts.append(d)

        # Planned steps (Steps > 1) from sequence
        planned_steps = []
        for step in (campaign.sequence or []):
            if step.step > 1:
                planned_steps.append({
                    "step": step.step,
                    "delay_days": step.delay_days,
                    "subject": step.subject,
                    "body": step.body,
                    "is_planned_only": True,
                    "in_outbox": False,
                })

        # Chronological audit history
        audit_history = await self.state.get_actions_for_campaign(campaign_id)

        # Summary statistics
        total_drafts = len(step_1_drafts)
        pending_count = sum(1 for d in step_1_drafts if d.get("status") == "pending_review")
        approved_count = sum(1 for d in step_1_drafts if d.get("status") == "approved")
        rejected_count = sum(1 for d in step_1_drafts if d.get("status") == "rejected")

        return {
            "success": True,
            "campaign": {
                "id": campaign.id,
                "name": campaign.name,
                "status": campaign.status,
                "channel": campaign.channel,
                "created_at": campaign.created_at.isoformat() if campaign.created_at else None,
                "prospect_count": len(campaign.prospect_ids or []),
                "total_steps": len(campaign.sequence or []),
            },
            "step_1_drafts": step_1_drafts,
            "planned_steps": planned_steps,
            "audit_history": audit_history,
            "summary": {
                "total_drafts": total_drafts,
                "pending_count": pending_count,
                "approved_count": approved_count,
                "rejected_count": rejected_count,
                "later_steps_in_outbox": later_in_outbox,
            },
        }

    async def approve_draft(
        self, campaign_id: str, draft_id: str, operator: str = "you"
    ) -> dict[str, Any]:
        """Approve a Step 1 draft for a campaign.

        Strict Safety Invariants:
        - Changes status to 'approved' and marks approved_by.
        - Does NOT dispatch or send emails (sending hold remains active, sent_count = 0).
        - Idempotent: repeated calls do not create duplicates or corrupt state.
        - Records reviewer action and audit history in actions table.
        """
        item = await self.state.get_outbox_item(draft_id)
        if not item:
            return {"success": False, "message": "Draft not found.", "status_code": 404}

        if item.get("campaign_id") != campaign_id:
            return {
                "success": False,
                "message": f"Draft does not belong to campaign '{campaign_id}'.",
                "status_code": 400,
            }

        prev_status = item.get("status")

        # Idempotency check: already approved
        if prev_status == "approved":
            return {
                "success": True,
                "already_approved": True,
                "draft_id": draft_id,
                "status": "approved",
                "message": "Draft is already approved.",
            }

        if prev_status in ("sent", "sending"):
            return {
                "success": False,
                "message": f"Cannot approve draft with status '{prev_status}'.",
                "status_code": 409,
            }

        now = _utcnow().isoformat()
        async with self.state._connect() as db:
            await db.execute(
                """UPDATE outbox
                   SET status = 'approved', approved_by = ?, error = '', updated_at = ?
                   WHERE id = ? AND campaign_id = ?""",
                (operator, now, draft_id, campaign_id),
            )
            await db.commit()

        # Audit log
        await self.state.log_action(
            action_type="campaign_draft_approved",
            agent=operator,
            details={
                "campaign_id": campaign_id,
                "draft_id": draft_id,
                "prospect_id": item.get("prospect_id"),
                "to_email": item.get("to_email"),
                "previous_status": prev_status,
                "new_status": "approved",
                "timestamp": now,
            },
        )

        return {
            "success": True,
            "draft_id": draft_id,
            "status": "approved",
            "previous_status": prev_status,
            "operator": operator,
        }

    async def reject_draft(
        self, campaign_id: str, draft_id: str, reason: str, operator: str = "you"
    ) -> dict[str, Any]:
        """Reject a draft with a mandatory rejection reason.

        Stores reason in outbox.error, feedback table, and actions audit log.
        Idempotent: repeating with same reason returns success without side effects.
        """
        clean_reason = (reason or "").strip()
        if not clean_reason:
            return {
                "success": False,
                "message": "A non-empty rejection reason is required.",
                "status_code": 400,
            }

        item = await self.state.get_outbox_item(draft_id)
        if not item:
            return {"success": False, "message": "Draft not found.", "status_code": 404}

        if item.get("campaign_id") != campaign_id:
            return {
                "success": False,
                "message": f"Draft does not belong to campaign '{campaign_id}'.",
                "status_code": 400,
            }

        prev_status = item.get("status")

        # Idempotency check: already rejected
        if prev_status == "rejected" and item.get("error") == clean_reason:
            return {
                "success": True,
                "already_rejected": True,
                "draft_id": draft_id,
                "status": "rejected",
                "reason": clean_reason,
                "message": "Draft is already rejected with this reason.",
            }

        if prev_status in ("sent", "sending"):
            return {
                "success": False,
                "message": "Cannot reject a draft that is already sent or sending.",
                "status_code": 409,
            }

        now = _utcnow().isoformat()
        async with self.state._connect() as db:
            await db.execute(
                """UPDATE outbox
                   SET status = 'rejected', error = ?, updated_at = ?
                   WHERE id = ? AND campaign_id = ?""",
                (clean_reason, now, draft_id, campaign_id),
            )
            await db.commit()

        # Persist feedback note for audit & prompt improvement
        await self.state.add_feedback(
            entity_type="campaign_draft",
            entity_id=draft_id,
            comment=clean_reason,
        )

        # Audit log
        await self.state.log_action(
            action_type="campaign_draft_rejected",
            agent=operator,
            details={
                "campaign_id": campaign_id,
                "draft_id": draft_id,
                "prospect_id": item.get("prospect_id"),
                "to_email": item.get("to_email"),
                "previous_status": prev_status,
                "new_status": "rejected",
                "rejection_reason": clean_reason,
                "timestamp": now,
            },
        )

        return {
            "success": True,
            "draft_id": draft_id,
            "status": "rejected",
            "previous_status": prev_status,
            "reason": clean_reason,
            "operator": operator,
        }

    async def edit_draft(
        self,
        campaign_id: str,
        draft_id: str,
        subject: str,
        body: str,
        operator: str = "you",
    ) -> dict[str, Any]:
        """Edit subject and body of a campaign draft awaiting review.

        Enforces pre-send gate checks (subject length, word count, formatting).
        Updates outbox table and logs edit event in actions audit table.
        """
        clean_subj = (subject or "").strip()
        clean_body = (body or "").strip()
        if not clean_subj or not clean_body:
            return {
                "success": False,
                "message": "Subject and body are both required.",
                "status_code": 400,
            }

        item = await self.state.get_outbox_item(draft_id)
        if not item:
            return {"success": False, "message": "Draft not found.", "status_code": 404}

        if item.get("campaign_id") != campaign_id:
            return {
                "success": False,
                "message": f"Draft does not belong to campaign '{campaign_id}'.",
                "status_code": 400,
            }

        if item.get("status") in ("sent", "sending"):
            return {
                "success": False,
                "message": "Cannot edit a draft that is already sent or sending.",
                "status_code": 409,
            }

        # Apply pre-send safety gate
        to_email = item.get("to_email") or "reviewer@example.com"
        gate = pre_send_check(to_email, clean_subj, clean_body)
        if not gate:
            reason_str = ", ".join(gate.reasons) if gate.reasons else "Failed pre-send checks"
            return {
                "success": False,
                "message": f"Pre-send check failed: {reason_str}",
                "status_code": 400,
            }

        now = _utcnow().isoformat()
        old_subject = item.get("subject", "")
        old_body = item.get("body", "")

        async with self.state._connect() as db:
            await db.execute(
                """UPDATE outbox
                   SET subject = ?, body = ?, updated_at = ?
                   WHERE id = ? AND campaign_id = ?""",
                (clean_subj, clean_body, now, draft_id, campaign_id),
            )
            await db.commit()

        # Audit log
        await self.state.log_action(
            action_type="campaign_draft_edited",
            agent=operator,
            details={
                "campaign_id": campaign_id,
                "draft_id": draft_id,
                "previous_subject": old_subject,
                "new_subject": clean_subj,
                "previous_status": item.get("status"),
                "new_status": item.get("status"),
                "timestamp": now,
            },
        )

        return {
            "success": True,
            "draft_id": draft_id,
            "subject": clean_subj,
            "body": clean_body,
            "status": item.get("status"),
        }

    async def get_audit_history(self, campaign_id: str) -> list[dict[str, Any]]:
        """Retrieve chronological review audit history for a campaign."""
        return await self.state.get_actions_for_campaign(campaign_id)
