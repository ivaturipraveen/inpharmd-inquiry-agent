"""Webhook receivers for external services (currently: ElevenLabs post-call)."""
import json
import logging
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session, joinedload

import call_log_service
import legacy_response_service
import summary_service
from call_outcome_service import apply_call_outcome
from call_service import IN_PROGRESS_STATUSES, classify_terminal_provider_status
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
        # call_conversation_id didn't match (e.g. after an ambiguous-timeout retry
        # placed a second call) — fall back to the inquiry_id dynamic variable.
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

    # Row-locks before mutating so a concurrent reconciliation poll or manual
    # entry can't race this webhook (all three share apply_call_outcome).
    # joinedload + FOR UPDATE fails on this nullable relation (Postgres); obj.manufacturer lazy-loads below instead.
    locked = (
        db.query(Inquiry)
        .filter(Inquiry.id == obj.id)
        .with_for_update()
        .first()
    )
    if locked is None:
        # Row was deleted between the initial match and the lock — extremely
        # unlikely, but don't crash on it.
        return {"matched": False, "conversation_id": convo_id}
    obj = locked

    # Matched by conversation_id, not call_completed_at (also set by submit_answer's
    # partial result) — CallLog.resolved_at alone marks this call's webhook as fired.
    existing_log = (
        db.query(CallLog)
        .filter(CallLog.inquiry_id == obj.id, CallLog.conversation_id == convo_id)
        .with_for_update()
        .first()
    )
    already_resolved = (
        (existing_log is not None and existing_log.resolved_at is not None)
        # Defensive fallback for a row with no CallLog counterpart (shouldn't
        # happen post-backfill) — never overwrite a confirmed result blindly.
        or (existing_log is None and obj.call_completed_at is not None)
    )
    if already_resolved:
        # Transcript arrived later than the resolving webhook — backfill it and
        # retry the legacy POST; event_key dedup still blocks a re-post if one succeeded.
        incoming_transcript = _extract_transcript(body)
        existing_transcript = existing_log.transcript if existing_log else obj.call_transcript
        if incoming_transcript and not existing_transcript:
            log.info(
                "Inquiry %s already resolved but had no transcript; backfilling from this delivery (conversation_id=%s)",
                obj.id, convo_id,
            )
            if existing_log is not None:
                existing_log.transcript = incoming_transcript
            obj.call_transcript = incoming_transcript
            db.commit()
            if not getattr(obj, "is_test_call", False):
                try:
                    legacy_response_service.maybe_post_for_inquiry(
                        db, obj, f"call:{obj.call_conversation_id}",
                        direct_response_text=incoming_transcript,
                    )
                except Exception:
                    log.exception("Legacy POST failed for inquiry %s (transcript backfilled)", obj.id)
            return {"matched": True, "conversation_id": convo_id, "already_resolved": True, "transcript_backfilled": True}

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

    # Real ElevenLabs post-call payload nests everything under `data` (verified against a live captured delivery) — status/duration/analysis are NOT top-level. Checking top-level too is lenient backward-compat for any flatter payload shape, matching _extract_summary/_extract_transcript's style.
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    raw_status = data.get("status") or body.get("status")
    analysis = data.get("analysis") if isinstance(data.get("analysis"), dict) else (
        body.get("analysis") if isinstance(body.get("analysis"), dict) else {}
    )
    # Duration can legitimately be 0 (an unanswered call) — use explicit None-checks rather than `or` chaining, which would incorrectly treat a real 0 as "missing" and fall through to a less-specific source.
    duration = None
    for candidate in (
        (data.get("metadata") or {}).get("call_duration_secs") if isinstance(data.get("metadata"), dict) else None,
        (body.get("metadata") or {}).get("call_duration_secs") if isinstance(body.get("metadata"), dict) else None,
        body.get("duration_seconds"),
        data.get("duration_seconds"),
    ):
        if candidate is not None:
            duration = candidate
            break
    # This is a post-call webhook — ElevenLabs only sends it once a call has ended — but defensively refuse to fabricate a terminal outcome if the payload's own status still says the call is ongoing.
    if raw_status in IN_PROGRESS_STATUSES:
        log.warning(
            "Inquiry %s: post-call webhook reported non-terminal status %r; ignoring "
            "without changing any state (conversation_id=%s)",
            obj.id, raw_status, convo_id,
        )
        return {"matched": True, "conversation_id": convo_id, "ignored_non_terminal_status": raw_status}

    # A missing/unrecognized status (malformed payload) falls through to the same call_successful/duration heuristic a "done" call would use — the only status value forced to a specific outcome is a real "failed".
    provider_status = classify_terminal_provider_status(
        status=raw_status,
        call_successful=analysis.get("call_successful"),
        duration_seconds=duration,
    )

    # Committed immediately, before optional LLM extraction below, so a failure there can never discard the confirmed result ElevenLabs gave us.
    apply_call_outcome(
        db, obj,
        provider_status=provider_status, summary=summary, transcript=transcript,
        conversation_id=convo_id,
    )
    db.commit()

    # Per-call summary for THIS call's Slack card — generated unconditionally
    # whenever a transcript exists and OpenAI is configured, regardless of
    # whether Inquiry.final_answer already holds a value from an unrelated
    # event (email, an earlier call). `call_summary_for_slack` is a local
    # variable scoped to this one request — it is never read from anywhere
    # persisted, so it cannot carry a stale cross-event value. Stays None
    # if extraction doesn't run or fails — no placeholder, no fallback.
    call_summary_for_slack = None
    if obj.call_transcript and summary_service.is_configured():
        try:
            extracted = summary_service.extract_answer_from_transcript(
                question=obj.question,
                manufacturer=obj.manufacturer.manufacturer if obj.manufacturer else "the manufacturer",
                transcript=obj.call_transcript,
            )
            call_summary_for_slack = extracted
            # Persist onto the specific CallLog row for this physical call —
            # matched by conversation_id, the same row apply_call_outcome's
            # record_terminal_result already wrote moments earlier. Existing
            # per-call field, no schema change.
            call_log_row = call_log_service.find_call_log_for_completion(db, obj, convo_id)
            call_log_row.summary = extracted
            # Inquiry-level final_answer aggregation is unchanged/untouched —
            # same "don't downgrade an existing answer" behavior as before.
            if not obj.final_answer:
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
                direct_response_text=transcript,
            )
        except Exception:
            log.exception("Legacy POST failed for inquiry %s (call result stored)", obj.id)

    # Denylist (not allowlist) so webhook-set legitimate statuses still notify; "closed" is included since a closed inquiry's follow-up call stays closed.
    _NO_ANSWER = ("voicemail", "no_answer", "wrong_number", "call_back_later", "follow_up_via_email", "initiated")
    if (
        not is_test
        and obj.status in ("call_completed", "closed")
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
                    answer=call_summary_for_slack or "Summary unavailable — see transcript",
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
            "Call for inquiry %s not posted to Slack (status=%s provider_status=%s)",
            obj.id, obj.status, obj.call_provider_status,
        )

    return {"matched": True, "inquiry_id": obj.id}
