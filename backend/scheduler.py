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
from sqlalchemy.orm import joinedload

import call_service
from database import SessionLocal
from models import Inquiry, ManufacturerContact

log = logging.getLogger("inquiry.scheduler")

# How often to scan for due retries (seconds)
_TICK_SECONDS = int(os.getenv("INQUIRY_RETRY_TICK_SECONDS", "60"))

# Delay between "send email" button click and actual dispatch (minutes).
# Exposed here so routers can import it rather than reading the env var themselves.
EMAIL_SCHEDULE_DELAY_MINUTES = int(os.getenv("EMAIL_SCHEDULE_DELAY_MINUTES", "30"))

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
    except Exception as e:
        # If ElevenLabs is unreachable, leave next_retry_at unchanged so we'll try again next tick
        log.exception("Retry call for inquiry %s failed at place_call: %s", obj.id, e)
        return

    obj.retry_count = (obj.retry_count or 0) + 1
    obj.status = "call_pending"
    obj.call_scheduled_for = _now()
    obj.call_conversation_id = (
        resp.get("conversation_id") or resp.get("conversationId")
    )
    obj.call_provider_status = "initiated"
    obj.next_retry_at = None  # cleared; will be re-set when the new call finishes if it also fails
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
            if mfr.fallback_call_enabled:
                fallback_delta = timedelta(minutes=5) if locked.fallback_after_hours == 0 else timedelta(hours=locked.fallback_after_hours)
                locked.call_scheduled_for = sent_at + fallback_delta
            db2.commit()
            log.info("Scheduled email sent for inquiry %s", locked.id)
        except Exception:
            log.exception("Unexpected error processing inquiry %s; rolling back", obj.id)
            db2.rollback()
        finally:
            db2.close()


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
        if not mfr.fallback_call_enabled:
            log.info("Inquiry %s manufacturer '%s' has fallback disabled; skipping", obj.id, mfr.manufacturer)
            continue
        if not mfr.mi_phone:
            log.warning("Inquiry %s manufacturer '%s' has no phone; skipping fallback call", obj.id, mfr.manufacturer)
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
                log.info(
                    "Inquiry %s manufacturer '%s' has fallback disabled (re-checked); skipping",
                    locked.id, fresh_mfr.manufacturer,
                )
                db2.rollback()
                db2.close()
                continue
            if not fresh_mfr.mi_phone:
                log.warning(
                    "Inquiry %s manufacturer '%s' has no phone (re-checked); skipping",
                    locked.id, fresh_mfr.manufacturer,
                )
                db2.rollback()
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
            except Exception:
                log.exception("Fallback call failed for inquiry %s; will retry on next tick", locked.id)
                db2.rollback()
                db2.close()
                continue

            locked.status = "call_pending"
            locked.call_scheduled_for = _now()
            locked.call_conversation_id = resp.get("conversation_id") or resp.get("conversationId")
            locked.call_provider_status = resp.get("status") or "initiated"
            locked.next_retry_at = None
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


def _scan_and_retry() -> None:
    """One scheduler tick: place retries for everything that's due."""
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
    _scheduler.start()
    log.info(
        "Schedulers started (retry every %ss, email poll every %ss, scheduled send every %ss)",
        _TICK_SECONDS,
        _poll_seconds,
        _email_send_seconds,
    )


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    _scheduler.shutdown(wait=False)
    _scheduler = None
    log.info("Inquiry retry scheduler stopped")
