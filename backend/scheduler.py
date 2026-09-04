"""APScheduler background job for auto-retrying failed inquiry calls.

Runs in-process inside the FastAPI app. Every 60 seconds it:
  1. Finds inquiries with `next_retry_at <= now` that still have retries left
  2. Triggers another ElevenLabs call for each
  3. Clears `next_retry_at` (re-set when the retry call finishes if it also fails)

Lifecycle is managed by FastAPI's startup/shutdown events in main.py.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session, joinedload

import call_service
import slack_service
from database import SessionLocal
from models import BulkEmailBatch, Inquiry, ManufacturerContact

log = logging.getLogger("inquiry.scheduler")

# How often to scan for due retries (seconds)
_TICK_SECONDS = int(os.getenv("INQUIRY_RETRY_TICK_SECONDS", "60"))

# Delay between "send email" button click and actual dispatch (minutes).
# Exposed here so routers can import it rather than reading the env var themselves.
EMAIL_SCHEDULE_DELAY_MINUTES = int(os.getenv("EMAIL_SCHEDULE_DELAY_MINUTES", "30"))

# How often to scan for "no response after 48h" candidates (seconds). A
# 48-hour-granularity check doesn't need the 60s cadence the other jobs use.
_NO_RESPONSE_TICK_SECONDS = int(os.getenv("NO_RESPONSE_TICK_SECONDS", "1800"))
# How long after first_contacted_at, with no response and no automated
# outreach pending, before the Slack notice fires.
NO_RESPONSE_ALERT_HOURS = int(os.getenv("NO_RESPONSE_ALERT_HOURS", "48"))

# Stuck-call reconciliation (see _reconcile_stuck_calls). A call_pending
# inquiry with no confirming webhook is otherwise stuck forever — this job
# polls ElevenLabs directly using call_conversation_id to recover.
CALL_RECONCILE_TICK_SECONDS = int(os.getenv("CALL_RECONCILE_TICK_SECONDS", "300"))
CALL_STUCK_THRESHOLD_MINUTES = int(os.getenv("CALL_STUCK_THRESHOLD_MINUTES", "10"))
CALL_RECONCILE_MAX_AGE_HOURS = int(os.getenv("CALL_RECONCILE_MAX_AGE_HOURS", "24"))
CALL_RECONCILE_BATCH_SIZE = int(os.getenv("CALL_RECONCILE_BATCH_SIZE", "25"))
# Exponential backoff for consecutive provider-poll failures (timeout,
# network error, invalid response) — NOT applied when the provider
# successfully confirms the call is still in progress, which keeps the full
# 5-minute cadence instead.
_CALL_RECONCILE_BACKOFF_BASE_MINUTES = 5
_CALL_RECONCILE_BACKOFF_CAP_MINUTES = 180
CALL_UNRESOLVED_MESSAGE = (
    "Call outcome could not be confirmed automatically after 24 hours — please verify manually."
)
CALL_NOT_FOUND_MESSAGE = (
    "Call outcome could not be confirmed — the call provider has no record of this "
    "conversation. Please verify manually."
)

_scheduler: Optional[BackgroundScheduler] = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _due_inquiries(db):
    """Inquiries whose retry window has elapsed and that still have retries left."""
    now = _now()
    return (
        db.query(Inquiry)
        .options(joinedload(Inquiry.manufacturer))
        .filter(
            and_(
                Inquiry.next_retry_at.isnot(None),
                Inquiry.next_retry_at <= now,
                Inquiry.retry_count < Inquiry.max_retries,
                # Only retry call-based failures; not email or closed
                Inquiry.status == "call_completed",
                Inquiry.call_provider_status.in_(("voicemail", "no_answer")),
                # Never auto-retry test calls — they must not dial manufacturer numbers
                Inquiry.is_test_call.isnot(True),
                # Fallback calls (email_sent_at set) are one-shot; the webhook marks
                # them needs_attention directly rather than feeding the retry loop.
                Inquiry.email_sent_at.is_(None),
                # A prior attempt's outcome is unresolved (HTTP timeout, no response) —
                # unconditionally excluded regardless of how long ago that was; only a
                # webhook match or _resolve_ambiguous_call_timeouts clears this.
                Inquiry.call_outcome_unknown_until.is_(None),
            )
        )
        .all()
    )


def _place_retry(db, obj: Inquiry) -> None:
    """Place one retry call for an inquiry. Updates retry_count + status."""
    mfr: ManufacturerContact = obj.manufacturer
    if not mfr or not mfr.mi_phone:
        log.warning("Inquiry %s has no phone; marking needs_attention", obj.id)
        obj.status = "needs_attention"
        obj.next_retry_at = None
        return

    try:
        resp = call_service.place_inquiry_call_sync(
            inquiry_id=obj.id,
            to_number=mfr.mi_phone,
            manufacturer_name=mfr.manufacturer,
            subject=obj.subject,
            question=obj.question,
            requester_name=obj.requester_name,
            requester_email=obj.requester_email,
        )
    except call_service.CallOutcomeUnknown:
        # The request timed out with no response — we cannot tell whether ElevenLabs
        # already placed the call. Do NOT leave this eligible for another automatic
        # retry; park it until a webhook resolves it or the window expires to needs_attention.
        obj.call_outcome_unknown_until = _now() + timedelta(minutes=10)
        log.warning(
            "Retry call for inquiry %s timed out with unknown outcome; parked until %s",
            obj.id, obj.call_outcome_unknown_until.isoformat(),
        )
        return
    except Exception as e:
        # If ElevenLabs is unreachable, leave next_retry_at unchanged so we'll try again next tick
        log.exception("Retry call for inquiry %s failed at place_call: %s", obj.id, e)
        return

    import call_log_service

    obj.retry_count = (obj.retry_count or 0) + 1
    obj.status = "call_pending"
    obj.call_scheduled_for = _now()
    obj.call_conversation_id = (
        resp.get("conversation_id") or resp.get("conversationId")
    )
    obj.call_provider_status = "initiated"
    obj.call_completed_at = None  # this retry is a NEW call; any prior completion no longer applies
    obj.next_retry_at = None  # cleared; will be re-set when the new call finishes if it also fails
    call_log_service.start_call_log(
        db, obj,
        conversation_id=obj.call_conversation_id,
        provider_status=obj.call_provider_status,
        started_at=obj.call_scheduled_for,
    )
    log.info(
        "Auto-retry #%s placed for inquiry %s (conv %s)",
        obj.retry_count,
        obj.id,
        obj.call_conversation_id,
    )


def _scan_and_send_pending_emails() -> None:
    """Scheduler tick: send emails whose scheduled delivery time has elapsed.

    Strategy:
    1. Read all due email_pending rows (no lock) to resolve manufacturer data.
    2. For each inquiry, re-fetch with SELECT FOR UPDATE SKIP LOCKED so
       concurrent instances / the send-now endpoint can't double-send.
    3. Each inquiry is committed independently so a SendGrid failure on one
       does not roll back the others.
    One inquiry always produces exactly one outbound email.
    """
    import email_service

    _notify_completed_bulk_batches()

    db = SessionLocal()
    try:
        now = _now()
        pending = (
            db.query(Inquiry)
            .options(joinedload(Inquiry.manufacturer))
            .filter(
                Inquiry.status == "email_pending",
                Inquiry.email_scheduled_for.isnot(None),
                Inquiry.email_scheduled_for <= now,
            )
            .order_by(Inquiry.email_scheduled_for.asc())
            .all()
        )
    except Exception:
        log.exception("Scheduled email scan query failed")
        return
    finally:
        db.close()

    if not pending:
        return

    log.info("Scheduled email scan: %s due", len(pending))

    # Build a map of inquiry_id → manufacturer from the initial unlocked read.
    # This avoids re-joining in the FOR UPDATE query below — PostgreSQL rejects
    # SELECT FOR UPDATE on the nullable side of an outer join (which joinedload
    # produces), so the per-group locked query must not use joinedload.
    mfr_by_inquiry: dict[int, ManufacturerContact] = {}
    for obj in pending:
        if obj.manufacturer:
            mfr_by_inquiry[obj.id] = obj.manufacturer

    for obj in pending:
        mfr = mfr_by_inquiry.get(obj.id)
        if not mfr:
            log.warning("Inquiry %s has no manufacturer; skipping scheduled send", obj.id)
            continue
        to_email_send = (mfr.official_mi_email or mfr.team_verified_email or "").strip()
        if not to_email_send:
            log.warning("Inquiry %s manufacturer '%s' has no email; skipping", obj.id, mfr.manufacturer)
            continue

        db2 = SessionLocal()
        try:
            locked = (
                db2.query(Inquiry)
                .filter(Inquiry.id == obj.id, Inquiry.status == "email_pending")
                .with_for_update(skip_locked=True)
                .first()
            )
            if not locked:
                db2.close()
                continue

            try:
                message_id = email_service.send_inquiry_email(
                    inquiry_id=locked.id,
                    manufacturer_name=mfr.manufacturer,
                    to_email=to_email_send,
                    subject=locked.subject,
                    question=locked.question,
                    requester_name=locked.requester_name,
                    requester_email=locked.requester_email,
                    medication_name=locked.medication_name,
                    pi_storage_data=locked.pi_storage_data,
                    pi_link=locked.pi_link,
                    team_name=locked.team_name,
                )
            except Exception:
                log.exception(
                    "Scheduled email send failed for inquiry %s; will retry on next tick",
                    locked.id,
                )
                db2.rollback()
                db2.close()
                continue

            sent_at = _now()
            locked.status = "email_sent"
            locked.email_sent_at = sent_at
            locked.email_message_id = message_id
            locked.email_scheduled_for = None
            if locked.first_contacted_at is None:
                locked.first_contacted_at = sent_at
            if mfr.fallback_call_enabled and mfr.mi_phone:
                fallback_delta = timedelta(minutes=5) if locked.fallback_after_hours == 0 else timedelta(hours=locked.fallback_after_hours)
                locked.call_scheduled_for = sent_at + fallback_delta
            elif mfr.fallback_call_enabled and not mfr.mi_phone:
                log.warning("Inquiry %s: fallback skipped — manufacturer '%s' has no MI phone", locked.id, mfr.manufacturer)
            db2.commit()
            log.info("Scheduled email sent for inquiry %s", locked.id)
        except Exception:
            log.exception("Unexpected error processing inquiry %s; rolling back", obj.id)
            db2.rollback()
        finally:
            db2.close()


def _notify_completed_bulk_batches() -> None:
    """One scheduler tick: for each bulk email batch not yet notified as
    complete, check whether every inquiry in it has left email_pending —
    i.e. each has reached a final state, whether that's an actual send
    (email_sent_at set) or the email never went out (draft via
    cancel_scheduled_email, or closed before its scheduled send fired). A
    non-sent inquiry must not block completion forever; it's reported in the
    summary instead. Sends the Slack completion notice and ONLY THEN marks it
    notified — a Slack failure leaves the row eligible for the next tick
    instead of being lost, and the row lock prevents a duplicate send."""
    db = SessionLocal()
    try:
        # Self-heal: bulk_create_inquiries may have failed to persist the
        # BulkEmailBatch tracking row for a batch that was otherwise scheduled
        # successfully (see the try/except around that insert). Backfill any
        # bulk_batch_id present on Inquiry rows with no corresponding
        # BulkEmailBatch row, so a transient failure there doesn't
        # permanently strand the batch without a completion notification.
        existing_batch_ids = {b[0] for b in db.query(BulkEmailBatch.batch_id).all()}
        referenced_batch_ids = {
            b[0] for b in db.query(Inquiry.bulk_batch_id)
            .filter(Inquiry.bulk_batch_id.isnot(None))
            .distinct()
            .all()
        }
        for missing_batch_id in referenced_batch_ids - existing_batch_ids:
            try:
                db.add(BulkEmailBatch(batch_id=missing_batch_id))
                db.commit()
                log.warning(
                    "Backfilled missing BulkEmailBatch tracking row for batch %s",
                    missing_batch_id,
                )
            except Exception:
                db.rollback()
                log.exception(
                    "Failed to backfill BulkEmailBatch row for %s; will retry next tick",
                    missing_batch_id,
                )

        batches = (
            db.query(BulkEmailBatch)
            .filter(BulkEmailBatch.completed_notified_at.is_(None))
            .all()
        )
        for batch in batches:
            still_pending = (
                db.query(Inquiry)
                .filter(
                    Inquiry.bulk_batch_id == batch.batch_id,
                    Inquiry.status == "email_pending",
                )
                .first()
            )
            if still_pending:
                continue

            members = (
                db.query(Inquiry)
                .options(joinedload(Inquiry.manufacturer))
                .filter(Inquiry.bulk_batch_id == batch.batch_id)
                .all()
            )
            if not members:
                continue

            # email_sent_at is only ever set at the moment of an actual send
            # (below, and in POST /{id}/send-now) — unlike status, which can
            # reach "closed" without an email ever going out (see
            # close_inquiry, which sets status="closed" unconditionally).
            cancelled = [m for m in members if m.email_sent_at is None]
            sent_members = [m for m in members if m.email_sent_at is not None]
            sent_count = len(sent_members)
            cancelled_items = [
                {
                    "inquiry_id": m.id,
                    "manufacturer": m.manufacturer.manufacturer if m.manufacturer else "Unknown",
                    "medication_name": m.medication_name,
                }
                for m in cancelled
            ]
            sent_items = [
                {
                    "inquiry_id": m.id,
                    "manufacturer": m.manufacturer.manufacturer if m.manufacturer else "Unknown",
                    "medication_name": m.medication_name,
                    "email_sent_at": m.email_sent_at,
                }
                for m in sent_members
            ]

            db2 = SessionLocal()
            try:
                locked = (
                    db2.query(BulkEmailBatch)
                    .filter(
                        BulkEmailBatch.batch_id == batch.batch_id,
                        BulkEmailBatch.completed_notified_at.is_(None),
                    )
                    .with_for_update(skip_locked=True)
                    .first()
                )
                if not locked:
                    db2.close()
                    continue

                notified = slack_service.notify_bulk_completed(
                    batch.batch_id,
                    total_count=len(members),
                    sent_count=sent_count,
                    sent_items=sent_items,
                    cancelled_items=cancelled_items,
                    question=members[0].question,
                    source_inquiry_uuid=members[0].source_inquiry_uuid,
                )
                if notified:
                    locked.completed_notified_at = _now()
                    db2.commit()
                    log.info("Bulk batch %s marked complete-notified", batch.batch_id)
                else:
                    log.warning(
                        "Bulk batch %s ready for completion notice but Slack post failed; will retry next tick",
                        batch.batch_id,
                    )
                    db2.rollback()
            except Exception:
                log.exception("Bulk batch completion check failed for %s; rolling back", batch.batch_id)
                db2.rollback()
            finally:
                db2.close()
    except Exception:
        log.exception("Bulk batch completion scan failed")
    finally:
        db.close()


def _scan_and_trigger_fallback_calls() -> None:
    """Scheduler tick: place fallback calls for emails that hit their SLA without a reply."""
    import call_service

    db = SessionLocal()
    try:
        now = _now()
        pending = (
            db.query(Inquiry)
            .options(joinedload(Inquiry.manufacturer))
            .filter(
                Inquiry.status == "email_sent",
                Inquiry.call_scheduled_for.isnot(None),
                Inquiry.call_scheduled_for <= now,
                Inquiry.is_test_call.isnot(True),
                # See _due_inquiries: unresolved prior attempt, unconditionally excluded.
                Inquiry.call_outcome_unknown_until.is_(None),
            )
            .all()
        )
    except Exception:
        log.exception("Fallback call scan query failed")
        return
    finally:
        db.close()

    if not pending:
        return

    log.info("Fallback call scan: %s due", len(pending))

    mfr_by_inquiry: dict[int, ManufacturerContact] = {}
    for obj in pending:
        if obj.manufacturer:
            mfr_by_inquiry[obj.id] = obj.manufacturer

    for obj in pending:
        mfr = mfr_by_inquiry.get(obj.id)
        if not mfr:
            log.warning("Inquiry %s has no manufacturer; skipping fallback call", obj.id)
            continue
        db2 = SessionLocal()
        try:
            locked = (
                db2.query(Inquiry)
                .filter(
                    Inquiry.id == obj.id,
                    Inquiry.status == "email_sent",
                    Inquiry.call_scheduled_for.isnot(None),
                    Inquiry.call_scheduled_for <= _now(),
                    Inquiry.call_outcome_unknown_until.is_(None),
                )
                .with_for_update(skip_locked=True)
                .first()
            )
            if not locked:
                db2.close()
                continue

            # Re-fetch manufacturer in db2 for an authoritative, current read.
            fresh_mfr = db2.get(ManufacturerContact, locked.manufacturer_id)
            if not fresh_mfr:
                log.warning("Inquiry %s manufacturer disappeared; skipping", locked.id)
                db2.rollback()
                db2.close()
                continue
            if not fresh_mfr.fallback_call_enabled:
                log.warning(
                    "Inquiry %s manufacturer '%s' has fallback disabled (re-checked); marking needs_attention",
                    locked.id, fresh_mfr.manufacturer,
                )
                locked.status = "needs_attention"
                locked.call_scheduled_for = None
                locked.next_retry_at = None
                db2.commit()
                db2.close()
                continue
            if not fresh_mfr.mi_phone:
                log.warning(
                    "Inquiry %s manufacturer '%s' has no phone (re-checked); marking needs_attention",
                    locked.id, fresh_mfr.manufacturer,
                )
                locked.status = "needs_attention"
                locked.call_scheduled_for = None
                locked.next_retry_at = None
                db2.commit()
                db2.close()
                continue

            try:
                resp = call_service.place_inquiry_call_sync(
                    inquiry_id=locked.id,
                    to_number=fresh_mfr.mi_phone,
                    manufacturer_name=fresh_mfr.manufacturer,
                    subject=locked.subject,
                    question=locked.question,
                    requester_name=locked.requester_name,
                    requester_email=locked.requester_email,
                )
            except call_service.CallOutcomeUnknown:
                # The request timed out with no response — ElevenLabs may have already
                # placed the call. Do NOT leave this row eligible for another automatic
                # fallback attempt; park it until a webhook resolves it or the window
                # expires to needs_attention (see _resolve_ambiguous_call_timeouts).
                locked.call_outcome_unknown_until = _now() + timedelta(minutes=10)
                db2.commit()
                log.warning(
                    "Fallback call for inquiry %s timed out with unknown outcome; parked until %s",
                    locked.id, locked.call_outcome_unknown_until.isoformat(),
                )
                db2.close()
                continue
            except Exception:
                log.exception("Fallback call failed for inquiry %s; will retry on next tick", locked.id)
                db2.rollback()
                db2.close()
                continue

            import call_log_service

            locked.status = "call_pending"
            locked.call_scheduled_for = _now()
            locked.call_conversation_id = resp.get("conversation_id") or resp.get("conversationId")
            locked.call_provider_status = resp.get("status") or "initiated"
            locked.call_completed_at = None
            locked.next_retry_at = None
            call_log_service.start_call_log(
                db2, locked,
                conversation_id=locked.call_conversation_id,
                provider_status=locked.call_provider_status,
                started_at=locked.call_scheduled_for,
            )
            db2.commit()
            log.info("Fallback call placed for inquiry %s (conv %s)", locked.id, locked.call_conversation_id)
        except Exception:
            log.exception("Unexpected error during fallback call for inquiry %s; rolling back", obj.id)
            db2.rollback()
        finally:
            db2.close()


def _poll_email_replies() -> None:
    """One scheduler tick: pull any new manufacturer email replies from the inbox."""
    import graph_service

    if not graph_service.is_configured():
        return
    try:
        graph_service.poll_once()
    except Exception:
        log.exception("email poll tick failed")


def _resolve_ambiguous_call_timeouts() -> None:
    """One scheduler tick: give up on calls whose outcome has been unknown for too
    long. This is the only place that compares call_outcome_unknown_until to the
    current time — the fallback/retry eligibility filters exclude any row where
    that column is non-null, full stop, regardless of elapsed time. So a row can
    never become eligible for another automatic call just because the window
    passed; it can only leave the unresolved state via a webhook match (clears
    the column) or here (moves to needs_attention in the same commit that clears
    it), which is why there is no ordering dependency between this function and
    the fallback/retry scans.
    """
    db = SessionLocal()
    try:
        now = _now()
        stuck = (
            db.query(Inquiry)
            .filter(
                Inquiry.call_outcome_unknown_until.isnot(None),
                Inquiry.call_outcome_unknown_until <= now,
                Inquiry.status.in_(("email_sent", "call_pending")),
            )
            .all()
        )
        if not stuck:
            return
        for obj in stuck:
            log.warning(
                "Inquiry %s: call outcome still unknown after grace window; marking needs_attention",
                obj.id,
            )
            obj.status = "needs_attention"
            obj.call_scheduled_for = None
            obj.next_retry_at = None
            obj.call_outcome_unknown_until = None
        db.commit()
    except Exception:
        log.exception("Ambiguous-call-timeout resolution tick failed; rolling back")
        db.rollback()
    finally:
        db.close()


def _no_response_eligible(obj: Inquiry) -> bool:
    """True when an inquiry is in a terminal-for-automation state: no further
    automated outreach attempt (fallback call, retry) will ever happen for
    it. Kept as its own function so the initial scan and the per-row locked
    re-check use byte-identical logic — see the module docstring in
    slack_service.notify_no_response for the Slack-message side of this.

    final_answer is deliberately NOT checked for needs_attention: every
    needs_attention-setting site in this codebase (agent_tools.submit_answer,
    this module's fallback/retry-exhaustion paths, the post-call webhook's
    fallback-voicemail branch) represents a failure/no-answer/exhausted
    condition, never a real reply — but submit_answer legitimately stores a
    placeholder like "Call ended without an answer (voicemail)" in
    final_answer for exactly these outcomes, so treating a non-null
    final_answer as "answered" here would wrongly suppress the notification
    for the majority of real no-response calls. final_answer only remains a
    valid discriminator for the email_sent branch, since every path that
    sets final_answer for an email reply also transitions status to
    email_responded in the same operation (record_email_response,
    graph_service, email_inbound) — a row still sitting in email_sent with a
    non-null final_answer would indicate something else entirely, not a
    reply, but excluding it here is the conservative, correct choice."""
    if obj.status == "needs_attention":
        return True
    if obj.status == "email_sent" and obj.call_scheduled_for is None and obj.final_answer is None:
        return True
    return False


def _notify_no_response() -> None:
    """One scheduler tick: Slack-notify once per inquiry that has had zero
    manufacturer response NO_RESPONSE_ALERT_HOURS after first_contacted_at,
    with no automated outreach attempt still pending (fallback call or call
    retry). call_completed is intentionally never eligible here, even with
    no final_answer — see routers/webhooks.py's own denylist for why a
    completed call with no captured answer isn't treated as proof of
    silence. Mirrors _notify_completed_bulk_batches' send-first-then-mark
    idempotency pattern exactly."""
    cutoff = _now() - timedelta(hours=NO_RESPONSE_ALERT_HOURS)

    db = SessionLocal()
    try:
        candidates = (
            db.query(Inquiry)
            .filter(
                Inquiry.no_response_notified_at.is_(None),
                Inquiry.is_test_call.isnot(True),
                Inquiry.first_contacted_at.isnot(None),
                Inquiry.first_contacted_at <= cutoff,
                or_(
                    Inquiry.status == "needs_attention",
                    and_(
                        Inquiry.status == "email_sent",
                        Inquiry.call_scheduled_for.is_(None),
                        Inquiry.final_answer.is_(None),
                    ),
                ),
            )
            .all()
        )
    except Exception:
        log.exception("No-response scan query failed")
        return
    finally:
        db.close()

    if not candidates:
        return

    log.info("No-response scan: %s candidate(s)", len(candidates))

    for obj in candidates:
        db2 = SessionLocal()
        try:
            locked = (
                db2.query(Inquiry)
                .options(joinedload(Inquiry.manufacturer))
                .filter(
                    Inquiry.id == obj.id,
                    Inquiry.no_response_notified_at.is_(None),
                    Inquiry.is_test_call.isnot(True),
                    Inquiry.first_contacted_at.isnot(None),
                    Inquiry.first_contacted_at <= _now() - timedelta(hours=NO_RESPONSE_ALERT_HOURS),
                )
                .with_for_update(skip_locked=True)
                .first()
            )
            # _no_response_eligible() is the single authoritative check for
            # status + final_answer nuance (see its docstring) — not
            # duplicated in this query's WHERE clause.
            if not locked or not _no_response_eligible(locked):
                db2.close()
                continue

            mfr = locked.manufacturer
            fallback_attempted = bool(locked.email_sent_at and locked.call_conversation_id)
            contact_method = "Email" if locked.email_sent_at else "Call"
            if locked.status == "needs_attention":
                status_reason = (
                    "Fallback call after email received no answer"
                    if fallback_attempted
                    else "Call retries exhausted with no answer"
                )
            else:
                status_reason = "Email sent, no reply, no fallback call configured"

            sent = slack_service.notify_no_response(
                locked.id,
                manufacturer=mfr.manufacturer if mfr else "Unknown manufacturer",
                medication_name=locked.medication_name,
                contact_method=contact_method,
                first_contacted_at=locked.first_contacted_at,
                fallback_attempted=fallback_attempted,
                status_reason=status_reason,
            )
            if sent:
                locked.no_response_notified_at = _now()
                db2.commit()
                log.info("No-response Slack notice sent for inquiry %s", locked.id)
            else:
                db2.rollback()
        except Exception:
            log.exception("Unexpected error during no-response notify for inquiry %s; rolling back", obj.id)
            db2.rollback()
        finally:
            db2.close()


def _reconcile_stuck_calls() -> None:
    """One scheduler tick: recover inquiries stuck in call_pending when the
    ElevenLabs post-call webhook was never received.

    Two phases, both capped at CALL_RECONCILE_BATCH_SIZE combined, both
    ordered oldest-call_scheduled_for-first so a large backlog drains
    fairly across ticks rather than starving newer rows (or vice versa):

    Phase 1 — rows already past the 24h ceiling: force-resolved to
    needs_attention immediately, with NO ElevenLabs call. This is the same
    code path regardless of whether a row got here through a long run of
    persisted backoff failures, or was already >24h old the moment this
    feature was deployed — there is no special-casing between "backlog" and
    "aged out organically", by design.

    Phase 2 — rows past the 10-minute stuck threshold but not yet at the
    ceiling: poll call_service.get_conversation_status_sync(). A confirmed
    still-in-progress result keeps the full 5-minute cadence; anything else
    that isn't a definite outcome (timeout, network error, invalid
    response) increments a persisted failure counter and backs off
    exponentially (5, 10, 20, 40, 80, 160, capped at 180 min) — persisted on
    the row itself (call_reconcile_failure_count /
    call_reconcile_next_attempt_at), not in memory, so progress survives a
    scheduler/server restart.
    """
    from call_outcome_service import apply_call_outcome
    import call_log_service

    now = _now()
    ceiling_cutoff = now - timedelta(hours=CALL_RECONCILE_MAX_AGE_HOURS)
    stuck_cutoff = now - timedelta(minutes=CALL_STUCK_THRESHOLD_MINUTES)

    def _force_unresolved(db_: Session, locked: Inquiry, message: str) -> None:
        # A closed inquiry stays closed — an unresolved follow-up call must
        # never reopen it into needs_attention. final_answer is still
        # recorded (only as a fallback — see the `or` below — so an
        # existing real answer from before the inquiry was closed is never
        # overwritten) and reconciliation tracking is still cleared either
        # way, matching the same pattern used everywhere else in this
        # feature (apply_call_outcome, routers.inquiries, agent_tools).
        if locked.status != "closed":
            locked.status = "needs_attention"
        locked.final_answer = locked.final_answer or message
        locked.call_reconcile_failure_count = 0
        locked.call_reconcile_next_attempt_at = None
        # Marks the CallLog for this specific call resolved too, with no
        # fabricated transcript/summary — otherwise it would stay "open"
        # forever and could be mismatched onto by a later, genuinely new
        # follow-up call's completion write.
        call_log_service.force_close_call_log(db_, locked)

    # ---- Phase 1: past the 24h ceiling — no polling, ever. ----
    db = SessionLocal()
    try:
        ceiling_ids = [
            r[0]
            for r in db.query(Inquiry.id)
            .filter(
                # "closed" is included alongside "call_pending" because a
                # follow-up call placed on a closed inquiry deliberately
                # never changes status away from "closed" (see
                # routers.inquiries.trigger_call) — without it here, such a
                # call would never be eligible for reconciliation at all if
                # its webhook is missed. call_completed_at IS NULL is what
                # actually identifies an outstanding call now that
                # trigger_call clears it on every new placement (including
                # follow-up calls, which previously left a prior call's
                # stale completion timestamp in place).
                Inquiry.status.in_(("call_pending", "closed")),
                Inquiry.call_conversation_id.isnot(None),
                Inquiry.call_completed_at.is_(None),
                Inquiry.is_test_call.isnot(True),
                Inquiry.call_scheduled_for <= ceiling_cutoff,
            )
            .order_by(Inquiry.call_scheduled_for.asc())
            .limit(CALL_RECONCILE_BATCH_SIZE)
            .all()
        ]
    except Exception:
        log.exception("Call-reconciliation ceiling-candidate query failed")
        return
    finally:
        db.close()

    resolved_count = 0
    for inquiry_id in ceiling_ids:
        db2 = SessionLocal()
        try:
            locked = (
                db2.query(Inquiry)
                .filter(
                    Inquiry.id == inquiry_id,
                    Inquiry.status.in_(("call_pending", "closed")),
                    Inquiry.call_scheduled_for <= ceiling_cutoff,
                )
                .with_for_update(skip_locked=True)
                .first()
            )
            if not locked or locked.call_completed_at is not None:
                db2.close()
                continue
            _force_unresolved(db2, locked, CALL_UNRESOLVED_MESSAGE)
            db2.commit()
            resolved_count += 1
            log.warning(
                "Inquiry %s: call_pending exceeded the %sh reconciliation ceiling with no "
                "confirmed outcome; marked needs_attention (no ElevenLabs call made)",
                inquiry_id, CALL_RECONCILE_MAX_AGE_HOURS,
            )
        except Exception:
            log.exception("Failed to force-resolve ceiling inquiry %s", inquiry_id)
            db2.rollback()
        finally:
            db2.close()

    remaining_capacity = CALL_RECONCILE_BATCH_SIZE - resolved_count
    if remaining_capacity <= 0:
        return

    # ---- Phase 2: due for a genuine reconciliation poll. ----
    db = SessionLocal()
    try:
        poll_ids = [
            r[0]
            for r in db.query(Inquiry.id)
            .filter(
                # See the matching comment in Phase 1 above — "closed" covers
                # follow-up calls placed on closed inquiries.
                Inquiry.status.in_(("call_pending", "closed")),
                Inquiry.call_conversation_id.isnot(None),
                Inquiry.call_completed_at.is_(None),
                Inquiry.is_test_call.isnot(True),
                Inquiry.call_scheduled_for <= stuck_cutoff,
                Inquiry.call_scheduled_for > ceiling_cutoff,
                or_(
                    Inquiry.call_reconcile_next_attempt_at.is_(None),
                    Inquiry.call_reconcile_next_attempt_at <= now,
                ),
            )
            .order_by(Inquiry.call_scheduled_for.asc())
            .limit(remaining_capacity)
            .all()
        ]
    except Exception:
        log.exception("Call-reconciliation poll-candidate query failed")
        return
    finally:
        db.close()

    if not poll_ids:
        return

    log.info("Call reconciliation: %s candidate(s) to poll", len(poll_ids))

    for inquiry_id in poll_ids:
        db2 = SessionLocal()
        try:
            locked = (
                db2.query(Inquiry)
                .options(joinedload(Inquiry.manufacturer))
                .filter(Inquiry.id == inquiry_id, Inquiry.status.in_(("call_pending", "closed")))
                .with_for_update(skip_locked=True)
                .first()
            )
            if not locked or not locked.call_conversation_id or locked.call_completed_at is not None:
                db2.close()
                continue

            # Re-check the ceiling under lock — time may have advanced past
            # it since the outer query ran (or another tick's phase 1 missed
            # it due to the batch cap). Done as a query-level comparison
            # (matching every other datetime check in this module) rather
            # than a raw Python comparison against the already-fetched
            # attribute, since SQLite (tests) round-trips
            # DateTime(timezone=True) as tz-naive and a direct Python
            # comparison against an aware `_now()` would raise.
            past_ceiling = (
                db2.query(Inquiry.id)
                .filter(
                    Inquiry.id == inquiry_id,
                    Inquiry.call_scheduled_for <= _now() - timedelta(hours=CALL_RECONCILE_MAX_AGE_HOURS),
                )
                .first()
                is not None
            )
            if past_ceiling:
                _force_unresolved(db2, locked, CALL_UNRESOLVED_MESSAGE)
                db2.commit()
                db2.close()
                continue

            conversation_id = locked.call_conversation_id
            result = call_service.get_conversation_status_sync(conversation_id)

            if result.outcome == call_service.CallPollOutcome.STILL_IN_PROGRESS:
                # Provider successfully reached and confirmed ongoing — keep
                # the full cadence, reset any prior failure streak.
                locked.call_reconcile_failure_count = 0
                locked.call_reconcile_next_attempt_at = _now() + timedelta(
                    minutes=_CALL_RECONCILE_BACKOFF_BASE_MINUTES
                )
                log.info("Inquiry %s: ElevenLabs confirmed call still in progress", inquiry_id)

            elif result.outcome == call_service.CallPollOutcome.POLL_FAILED:
                # Could NOT confirm anything — distinct from STILL_IN_PROGRESS.
                # Exponential backoff, persisted so it survives a restart.
                locked.call_reconcile_failure_count = (locked.call_reconcile_failure_count or 0) + 1
                delay_minutes = min(
                    _CALL_RECONCILE_BACKOFF_BASE_MINUTES * (2 ** (locked.call_reconcile_failure_count - 1)),
                    _CALL_RECONCILE_BACKOFF_CAP_MINUTES,
                )
                locked.call_reconcile_next_attempt_at = _now() + timedelta(minutes=delay_minutes)
                log.warning(
                    "Inquiry %s: could not reach/interpret ElevenLabs (%s); next attempt in %s min "
                    "(consecutive failure #%s)",
                    inquiry_id, result.fail_reason, delay_minutes, locked.call_reconcile_failure_count,
                )

            elif result.outcome == call_service.CallPollOutcome.NOT_FOUND:
                # Never fabricate a result — explicit, distinct message from
                # a real voicemail/no-answer outcome.
                _force_unresolved(db2, locked, CALL_NOT_FOUND_MESSAGE)
                log.warning(
                    "Inquiry %s: conversation %s not found at provider; marked needs_attention",
                    inquiry_id, conversation_id,
                )

            elif result.outcome == call_service.CallPollOutcome.TERMINAL:
                apply_call_outcome(
                    db2, locked,
                    provider_status=result.provider_status,
                    summary=result.summary,
                    transcript=result.transcript,
                    conversation_id=conversation_id,
                )
                log.info(
                    "Inquiry %s: resolved via reconciliation poll (provider_status=%s)",
                    inquiry_id, result.provider_status,
                )

            db2.commit()
        except Exception:
            log.exception("Reconciliation poll failed for inquiry %s", inquiry_id)
            db2.rollback()
        finally:
            db2.close()


def _scan_and_retry() -> None:
    """One scheduler tick: place retries for everything that's due."""
    _resolve_ambiguous_call_timeouts()

    db = SessionLocal()
    try:
        due = _due_inquiries(db)
        if not due:
            return
        log.info("Processing %s due retry(ies)", len(due))
        for obj in due:
            _place_retry(db, obj)
        db.commit()
    except Exception:
        log.exception("Retry tick failed; rolling back")
        db.rollback()
    finally:
        db.close()


def schedule_retry_after_failure(db, obj: Inquiry, delay_minutes: int = 2) -> None:
    """Called from the call-result handlers when a call ends with a bad outcome.
    Sets next_retry_at if retries remain, otherwise flips to needs_attention."""
    from datetime import timedelta

    # Test calls must never enter the retry/needs_attention workflow.
    if getattr(obj, "is_test_call", False):
        log.info("Inquiry %s is a test call; skipping retry scheduling", obj.id)
        return

    # A closed inquiry must never be silently reopened by an automatic
    # retry — a follow-up call on a closed inquiry is a one-off manual
    # action, not the start of a retry loop. Called from both
    # routers/agent_tools.py (submit_answer) and routers/webhooks.py
    # (post-call webhook), so guarding here covers both call sites.
    if obj.status == "closed":
        log.info("Inquiry %s is closed; not scheduling an automatic retry", obj.id)
        return

    if obj.retry_count >= obj.max_retries:
        obj.status = "needs_attention"
        obj.next_retry_at = None
        log.info("Inquiry %s exhausted retries; marked needs_attention", obj.id)
        return
    obj.next_retry_at = _now() + timedelta(minutes=delay_minutes)
    log.info(
        "Inquiry %s scheduled retry #%s at %s",
        obj.id,
        obj.retry_count + 1,
        obj.next_retry_at.isoformat(),
    )


def start_scheduler() -> None:
    """Called from FastAPI startup. Idempotent."""
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        _scan_and_retry,
        "interval",
        seconds=_TICK_SECONDS,
        id="inquiry_retry_scan",
        max_instances=1,
        coalesce=True,
    )
    _poll_seconds = int(os.getenv("IMAP_POLL_SECONDS", "60"))
    _scheduler.add_job(
        _poll_email_replies,
        "interval",
        seconds=_poll_seconds,
        id="email_reply_poll",
        max_instances=1,
        coalesce=True,
    )
    _email_send_seconds = int(os.getenv("EMAIL_SEND_TICK_SECONDS", "60"))
    _scheduler.add_job(
        _scan_and_send_pending_emails,
        "interval",
        seconds=_email_send_seconds,
        id="scheduled_email_send",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        _scan_and_trigger_fallback_calls,
        "interval",
        seconds=_TICK_SECONDS,
        id="fallback_call_scan",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        _notify_no_response,
        "interval",
        seconds=_NO_RESPONSE_TICK_SECONDS,
        id="no_response_notify",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        _reconcile_stuck_calls,
        "interval",
        seconds=CALL_RECONCILE_TICK_SECONDS,
        id="call_reconciliation",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    log.info(
        "Schedulers started (retry every %ss, email poll every %ss, scheduled send every %ss, "
        "no-response scan every %ss, call reconciliation every %ss)",
        _TICK_SECONDS,
        _poll_seconds,
        _email_send_seconds,
        _NO_RESPONSE_TICK_SECONDS,
        CALL_RECONCILE_TICK_SECONDS,
    )


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    _scheduler.shutdown(wait=False)
    _scheduler = None
    log.info("Inquiry retry scheduler stopped")
