"""HTTP tools the ElevenLabs voice agent calls *during* a live call.

These endpoints exist so the agent can report structured results back to
us without waiting for the post-call webhook. Authentication is a shared
secret in the `X-Agent-Secret` header (set in ElevenLabs tool config + in
backend/.env as AGENT_TOOLS_SECRET). When AGENT_TOOLS_SECRET is unset,
the endpoint accepts unauthenticated requests (a startup warning is logged
so this isn't accidental in production)."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

import legacy_response_service
from database import get_db
from models import Inquiry
from scheduler import schedule_retry_after_failure

log = logging.getLogger("inquiry.agent_tools")

router = APIRouter(prefix="/api/agent-tools", tags=["agent-tools"])

if not os.getenv("AGENT_TOOLS_SECRET"):
    log.warning(
        "AGENT_TOOLS_SECRET is not set — /api/agent-tools/* endpoints are "
        "PUBLIC. Anyone who knows the URL can submit answers. Set the env "
        "var to re-enable shared-secret auth."
    )


def _check_secret(provided: Optional[str]) -> None:
    expected = os.getenv("AGENT_TOOLS_SECRET")
    if expected and provided != expected:
        raise HTTPException(status_code=401, detail="Invalid agent secret")


CallOutcome = Literal[
    "answered",                 # Got a clinical answer
    "follow_up_via_email",      # Rep promised to email; no answer yet
    "no_answer",                # Reached a person but they couldn't / wouldn't answer
    "voicemail",                # Left a voicemail
    "wrong_number",             # Manufacturer routed us to the wrong place
    "call_back_later",          # Rep asked to call back at a specific time
]


class SubmitAnswerPayload(BaseModel):
    inquiry_id: int = Field(..., description="Our internal inquiry ID (the agent receives this as a dynamic variable)")
    outcome: CallOutcome = Field(..., description="One-word status describing how the call ended")
    answer: Optional[str] = Field(
        default=None,
        description="The clinical answer the rep provided, in their words. Required when outcome='answered'.",
    )
    rep_name: Optional[str] = Field(default=None, description="Name of the person the agent spoke with, if given")
    rep_reference: Optional[str] = Field(
        default=None,
        description="Any reference number / case ID / document name the rep cited",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Anything else worth recording (caveats, off-label disclaimer, escalation path)",
    )


@router.post("/submit-answer")
def submit_answer(
    payload: SubmitAnswerPayload,
    x_agent_secret: Optional[str] = Header(None, alias="X-Agent-Secret"),
    db: Session = Depends(get_db),
):
    """Agent calls this once it has captured an answer (or determined the
    call won't yield one). Updates the inquiry status, stores the structured
    answer, and timestamps the call completion."""
    _check_secret(x_agent_secret)

    obj = (
        db.query(Inquiry)
        .options(joinedload(Inquiry.manufacturer))
        .filter(Inquiry.id == payload.inquiry_id)
        .first()
    )
    if not obj:
        raise HTTPException(status_code=404, detail=f"Inquiry {payload.inquiry_id} not found")

    now = datetime.now(timezone.utc)

    # Compose a human-readable summary from the structured fields
    parts: list[str] = []
    if payload.answer:
        parts.append(payload.answer.strip())
    if payload.rep_name:
        parts.append(f"(Per {payload.rep_name})")
    if payload.rep_reference:
        parts.append(f"Reference: {payload.rep_reference}")
    if payload.notes:
        parts.append(f"Notes: {payload.notes}")
    summary = "\n\n".join(parts) if parts else None

    obj.call_completed_at = now
    obj.call_provider_status = payload.outcome
    if summary:
        obj.call_summary = summary

    if payload.outcome == "answered":
        obj.status = "call_completed"
        if summary:
            obj.final_answer = summary
    elif payload.outcome == "follow_up_via_email":
        # Rep will email — keep status as call_completed but flag it in summary
        obj.status = "call_completed"
        followup_note = f"Rep promised to follow up via email to {obj.requester_email or 'requester'}."
        obj.final_answer = f"{summary}\n\n{followup_note}" if summary else followup_note
    elif payload.outcome in ("voicemail", "wrong_number", "no_answer", "call_back_later"):
        # Call attempted but no useful info
        obj.status = "call_completed"
        obj.final_answer = summary or f"Call ended without an answer ({payload.outcome})."
        # Auto-retry only on voicemail / no_answer (transient failures).
        # wrong_number and call_back_later are deliberate human signals — don't auto-retry.
        if payload.outcome in ("voicemail", "no_answer"):
            if obj.email_sent_at:
                # Fallback call — one-shot, don't retry
                obj.status = "needs_attention"
                obj.next_retry_at = None
            else:
                schedule_retry_after_failure(db, obj, delay_minutes=2)

    db.commit()

    # Forward to legacy if this inquiry came from InpharmD (no-op otherwise).
    if payload.outcome in ("answered", "follow_up_via_email"):
        legacy_response_service.maybe_post_for_inquiry(db, obj)

    return {
        "success": True,
        "inquiry_id": obj.id,
        "status": obj.status,
        "message": (
            "Got it — answer saved."
            if payload.outcome == "answered"
            else f"Call result recorded as '{payload.outcome}'."
        ),
    }
