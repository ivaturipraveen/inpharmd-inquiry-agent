"""Webhook receivers for external services (currently: ElevenLabs post-call)."""
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session, joinedload

import summary_service
from database import get_db
from models import Inquiry
from scheduler import schedule_retry_after_failure

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


def _extract_conversation_id(body: Dict[str, Any]) -> Optional[str]:
    """ElevenLabs sometimes nests under `data` or `conversation` — be lenient."""
    return (
        body.get("conversation_id")
        or body.get("conversationId")
        or (body.get("data") or {}).get("conversation_id")
        or (body.get("conversation") or {}).get("conversation_id")
    )


def _extract_summary(body: Dict[str, Any]) -> Optional[str]:
    return (
        body.get("summary")
        or body.get("call_summary")
        or (body.get("analysis") or {}).get("summary")
        or (body.get("data") or {}).get("summary")
    )


def _extract_transcript(body: Dict[str, Any]) -> Optional[str]:
    # ElevenLabs sends a structured turn list; flatten to plain text for storage
    turns = (
        body.get("transcript")
        or (body.get("data") or {}).get("transcript")
        or body.get("messages")
    )
    if isinstance(turns, str):
        return turns
    if isinstance(turns, list):
        lines = []
        for t in turns:
            if not isinstance(t, dict):
                continue
            role = t.get("role") or t.get("speaker") or "agent"
            text = t.get("message") or t.get("text") or t.get("content") or ""
            if text:
                lines.append(f"{role.upper()}: {text}")
        return "\n".join(lines) if lines else None
    return None


@router.post("/elevenlabs/post-call")
async def elevenlabs_post_call(
    request: Request,
    x_webhook_secret: Optional[str] = Header(None, alias="X-Webhook-Secret"),
    db: Session = Depends(get_db),
):
    """Receives ElevenLabs' post-call payload and writes the result back to
    the matching inquiry by `conversation_id`."""
    secret = os.getenv("ELEVENLABS_WEBHOOK_SECRET")
    if secret and x_webhook_secret != secret:
        raise HTTPException(status_code=401, detail="Invalid webhook secret")

    body = await request.json()
    convo_id = _extract_conversation_id(body)
    if not convo_id:
        raise HTTPException(status_code=400, detail="No conversation_id in payload")

    obj = (
        db.query(Inquiry)
        .options(joinedload(Inquiry.manufacturer))
        .filter(Inquiry.call_conversation_id == convo_id)
        .first()
    )
    if not obj:
        # Not one of our inquiries — silently accept so ElevenLabs doesn't retry forever
        return {"matched": False, "conversation_id": convo_id}

    summary = _extract_summary(body)
    transcript = _extract_transcript(body)

    from datetime import datetime, timezone
    obj.call_completed_at = datetime.now(timezone.utc)
    if summary:
        obj.call_summary = summary
        # Only overwrite final_answer if submit_answer didn't already set a structured one
        if not obj.final_answer:
            obj.final_answer = summary
    if transcript:
        obj.call_transcript = transcript

    # ElevenLabs payload may include duration / status — treat short/no-answer calls as voicemail
    provider_status = body.get("status") or "completed"
    duration = (
        body.get("duration_seconds")
        or (body.get("data") or {}).get("duration_seconds")
        or 0
    )
    # If submit_answer already set a meaningful provider_status, don't overwrite it
    if not obj.call_provider_status or obj.call_provider_status == "initiated":
        if duration and duration < 8:
            obj.call_provider_status = "no_answer"
        else:
            obj.call_provider_status = provider_status

    # If submit_answer ran, status is already set; otherwise mark call_completed
    if obj.status == "call_pending":
        obj.status = "call_completed"

    # Auto-retry only if submit_answer didn't capture a real answer AND outcome is retriable
    if obj.call_provider_status in ("voicemail", "no_answer") and not obj.call_summary:
        schedule_retry_after_failure(db, obj, delay_minutes=2)

    # If we have a transcript but no clean answer yet, try LLM extraction
    if obj.call_transcript and not obj.final_answer and summary_service.is_configured():
        try:
            extracted = summary_service.extract_answer_from_transcript(
                question=obj.question,
                manufacturer=obj.manufacturer.manufacturer if obj.manufacturer else "the manufacturer",
                transcript=obj.call_transcript,
            )
            obj.call_summary = obj.call_summary or extracted
            obj.final_answer = extracted
        except summary_service.SummaryConfigError as e:
            # Soft-fail: leave transcript as-is, user can extract manually later
            pass

    db.commit()

    # Post to Slack when the call produced a real answer (mirror of the email path).
    # Denylist the outcomes that are NOT a real manufacturer response; everything
    # else with a final_answer posts. The post-call webhook may set provider_status
    # to ElevenLabs' own string (e.g. "done"/"success") when submit_answer didn't
    # run, so an allowlist would miss those legitimate answers.
    _NO_ANSWER = ("voicemail", "no_answer", "wrong_number", "call_back_later", "follow_up_via_email", "initiated")
    if (
        obj.status == "call_completed"
        and obj.final_answer
        and (obj.call_provider_status or "") not in _NO_ANSWER
    ):
        try:
            import slack_service
            if slack_service.is_configured():
                slack_service.notify_reply(
                    inquiry_id=obj.id,
                    manufacturer=obj.manufacturer.manufacturer if obj.manufacturer else "the manufacturer",
                    subject=obj.subject,
                    question=obj.question,
                    answer=obj.final_answer,
                    requester_name=obj.requester_name,
                    requester_email=obj.requester_email,
                    channel="call",
                )
        except Exception:
            pass

    return {"matched": True, "inquiry_id": obj.id}
