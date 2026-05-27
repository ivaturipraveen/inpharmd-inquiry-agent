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
from datetime import datetime, timezone
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
        resp = call_service.place_inquiry_call(
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
    _scheduler.start()
    log.info("Inquiry retry scheduler started (every %ss)", _TICK_SECONDS)


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    _scheduler.shutdown(wait=False)
    _scheduler = None
    log.info("Inquiry retry scheduler stopped")
