"""Webhook receivers for external services (currently: ElevenLabs post-call)."""
import json
import logging
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session, joinedload

import legacy_response_service
import summary_service
from call_outcome_service import apply_call_outcome
from database import get_db
from models import CallLog, Inquiry, UnmatchedCallWebhook

log = logging.getLogger("inquiry.webhooks")

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


def _extract_conversation_id(body: Dict[str, Any]) -> Optional[str]:
    """ElevenLabs sometimes nests under `data` or `conversation` — be lenient."""
    return (
        body.get("conversation_id")
        or body.get("conversationId")
        or (body.get("data") or {}).get("conversation_id")
        or (body.get("conversation") or {}).get("conversation_id")
    )


def _extract_inquiry_id(body: Dict[str, Any]) -> Optional[int]:
    """Fallback identifier when call_conversation_id doesn't match anything —
    we set `inquiry_id` as a dynamic variable on every call we place, so it
    should be echoed back under conversation_initiation_client_data.
    Defensive about nesting the same way _extract_conversation_id is: checks
    top-level, `data.*`, and `conversation.*`, and within each of those both
    a top-level `dynamic_variables` key and one nested under
    `conversation_initiation_client_data`."""
    def _dv_candidates(container: Dict[str, Any]) -> list:
        cid_client_data = container.get("conversation_initiation_client_data")
        return [
            container.get("dynamic_variables"),
            cid_client_data.get("dynamic_variables") if isinstance(cid_client_data, dict) else None,
        ]

    containers = [body, body.get("data") or {}, body.get("conversation") or {}]
    for container in containers:
        if not isinstance(container, dict):
            continue
        for dv in _dv_candidates(container):
            if isinstance(dv, dict) and dv.get("inquiry_id") is not None:
                try:
                    return int(dv["inquiry_id"])
                except (TypeError, ValueError):
                    continue
    return None


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
    matched_via_inquiry_id = False
    if not obj:
        # call_conversation_id didn't match — the conversation may belong to an
        # inquiry whose row currently holds a *different* call's id (e.g. after an
        # ambiguous-timeout retry placed a second real call). Fall back to the
        # inquiry_id dynamic variable we send on every outbound call.
        fallback_inquiry_id = _extract_inquiry_id(body)
        if fallback_inquiry_id is not None:
            obj = (
                db.query(Inquiry)
                .options(joinedload(Inquiry.manufacturer))
                .filter(Inquiry.id == fallback_inquiry_id)
                .first()
            )
            matched_via_inquiry_id = obj is not None

    if not obj:
        # Truly unattributable — persist instead of silently discarding.
        db.add(
            UnmatchedCallWebhook(
                conversation_id=convo_id,
                raw_payload=json.dumps(body),
                reason="no_inquiry_id_in_payload" if _extract_inquiry_id(body) is None else "inquiry_id_not_found",
            )
        )
        db.commit()
        log.error("Unmatched ElevenLabs post-call webhook persisted for review (conversation_id=%s)", convo_id)
        return {"matched": False, "conversation_id": convo_id}

    # Re-fetch with a row lock before mutating — so a concurrent reconciliation
    # poll or manual call-result entry for the same inquiry can't race with
    # this webhook (see call_outcome_service.apply_call_outcome, used by all
    # three writers).
    locked = (
        db.query(Inquiry)
        .options(joinedload(Inquiry.manufacturer))
        .filter(Inquiry.id == obj.id)
        .with_for_update()
        .first()
    )
    if locked is None:
        # Row was deleted between the initial match and the lock — extremely
        # unlikely, but don't crash on it.
        return {"matched": False, "conversation_id": convo_id}
    obj = locked

    # Matched by the incoming conversation_id, not obj.call_completed_at —
    # that Inquiry-level field is also set by submit_answer's mid-call
    # partial result, which must NOT block this webhook from still filling
    # in the fuller transcript for the SAME call. CallLog.resolved_at is set
    # only by a terminal writer (this webhook, reconciliation, or manual
    # entry), never by submit_answer, so it precisely distinguishes "this
    # call's webhook already fired" from "the agent already reported an
    # in-call answer but the real webhook hasn't arrived yet".
    existing_log = (
        db.query(CallLog)
        .filter(CallLog.inquiry_id == obj.id, CallLog.conversation_id == convo_id)
        .with_for_update()
        .first()
    )
    already_resolved = (
        (existing_log is not None and existing_log.resolved_at is not None)
        # Defensive fallback for a row with no CallLog counterpart at all
        # (shouldn't happen once the one-time backfill migration has run) —
        # never overwrite a confirmed result blindly.
        or (existing_log is None and obj.call_completed_at is not None)
    )
    if already_resolved:
        log.info(
            "Inquiry %s already has a recorded call result; ignoring duplicate/late webhook (conversation_id=%s)",
            obj.id, convo_id,
        )
        return {"matched": True, "conversation_id": convo_id, "already_resolved": True}

    if matched_via_inquiry_id:
        log.warning(
            "Inquiry %s matched via inquiry_id fallback, not call_conversation_id "
            "(stored=%s, incoming=%s) — backfilling",
            obj.id, obj.call_conversation_id, convo_id,
        )
        obj.call_conversation_id = obj.call_conversation_id or convo_id

    summary = _extract_summary(body)
    transcript = _extract_transcript(body)

    # ElevenLabs payload may include duration / status — treat short/no-answer calls as voicemail
    raw_provider_status = body.get("status") or "completed"
    duration = (
        body.get("duration_seconds")
        or (body.get("data") or {}).get("duration_seconds")
        or 0
    )
    provider_status = "no_answer" if duration and duration < 8 else raw_provider_status

    # Core outcome — committed immediately, before the optional LLM-extraction
    # step below, so a failure there can never discard the confirmed result
    # ElevenLabs just gave us.
    apply_call_outcome(
        db, obj,
        provider_status=provider_status, summary=summary, transcript=transcript,
        conversation_id=convo_id,
    )
    db.commit()

    # If we have a transcript but no clean answer yet, try LLM extraction.
    # Best-effort only: any failure here (config, API error, timeout, etc.)
    # must never roll back or discard the core result committed above.
    if obj.call_transcript and not obj.final_answer and summary_service.is_configured():
        try:
            extracted = summary_service.extract_answer_from_transcript(
                question=obj.question,
                manufacturer=obj.manufacturer.manufacturer if obj.manufacturer else "the manufacturer",
                transcript=obj.call_transcript,
            )
            obj.call_summary = obj.call_summary or extracted
            obj.final_answer = extracted
            db.commit()
        except Exception:
            log.exception(
                "LLM answer extraction failed for inquiry %s (core call result already saved)",
                obj.id,
            )
            db.rollback()

    # Test calls must not trigger downstream manufacturer workflows.
    # Transcript and status are still written above so the call is viewable in Outreach.
    is_test = getattr(obj, "is_test_call", False)

    # Forward to legacy if this inquiry came from InpharmD (real calls only).
    if not is_test:
        try:
            legacy_response_service.maybe_post_for_inquiry(
                db, obj, f"call:{obj.call_conversation_id}",
                direct_response_text=obj.call_summary,
            )
        except Exception:
            log.exception("Legacy POST failed for inquiry %s (call result stored)", obj.id)

    # Post to Slack when the call produced a real answer (mirror of the email path).
    # Denylist the outcomes that are NOT a real manufacturer response; everything
    # else with a final_answer posts. The post-call webhook may set provider_status
    # to ElevenLabs' own string (e.g. "done"/"success") when submit_answer didn't
    # run, so an allowlist would miss those legitimate answers.
    # "closed" is included alongside "call_completed" because apply_call_outcome
    # deliberately leaves status at "closed" for a follow-up call on a closed
    # inquiry (see call_outcome_service) — without it, a genuinely answered
    # follow-up call would satisfy every other condition here yet never notify.
    _NO_ANSWER = ("voicemail", "no_answer", "wrong_number", "call_back_later", "follow_up_via_email", "initiated")
    if (
        not is_test
        and obj.status in ("call_completed", "closed")
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
            else:
                log.info("Slack not configured; skipping call card for inquiry %s", obj.id)
        except Exception:
            log.exception("Slack notify failed for inquiry %s", obj.id)
    else:
        log.info(
            "Call for inquiry %s not posted to Slack (status=%s provider_status=%s has_answer=%s)",
            obj.id, obj.status, obj.call_provider_status, bool(obj.final_answer),
        )

    return {"matched": True, "inquiry_id": obj.id}
