"""Shared CallLog read/write helpers.

Used by every call-creation site (trigger_call, bulk-call dispatch,
test_call_preview, scheduler's auto-retry and fallback placement) and every
call-completion site (routers.webhooks, scheduler._reconcile_stuck_calls via
call_outcome_service.apply_call_outcome, routers.agent_tools.submit_answer,
routers.inquiries.record_call_result) so a single call's history is always
represented by exactly one CallLog row, appended to rather than overwritten,
regardless of which of those paths eventually resolves it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from models import CallLog, Inquiry


def _now() -> datetime:
    return datetime.now(timezone.utc)


def start_call_log(
    db: Session,
    obj: Inquiry,
    *,
    conversation_id: Optional[str],
    provider_status: Optional[str] = None,
    started_at: Optional[datetime] = None,
) -> CallLog:
    """Called by every call-placement site right after a call is confirmed
    placed (trigger_call, bulk-call dispatch, test_call_preview, scheduler's
    auto-retry and fallback placement) — always creates a NEW row, never
    updates an existing one, so each physical call gets its own permanent
    history entry."""
    row = CallLog(
        inquiry_id=obj.id,
        conversation_id=conversation_id,
        is_test_call=bool(obj.is_test_call),
        started_at=started_at or _now(),
        provider_status=provider_status,
    )
    db.add(row)
    db.flush()
    return row


def find_call_log_for_completion(
    db: Session, obj: Inquiry, conversation_id: Optional[str]
) -> CallLog:
    """Find the CallLog row a completion write should apply to. Used by
    every completion site (submit_answer, apply_call_outcome, manual
    record_call_result, and reconciliation's force-close path).

    Matched by conversation_id first — the stable identifier for "this
    specific call" — deliberately NOT by completed_at/resolved_at, because
    submit_answer sets completed_at on this same row before the fuller
    post-call webhook arrives, and matching on "still open" would then miss
    it. Falls back to the inquiry's most recent still-open row when
    conversation_id is unknown (manual entry supplies none) or unmatched
    (the rare case where a call's conversation_id was only ever learned via
    the inquiry_id webhook fallback — see routers.webhooks). Creates a new
    row as a last resort so a completion write is never silently dropped —
    a defensive backstop for legacy/edge-case data, not the expected path.
    """
    row: Optional[CallLog] = None
    if conversation_id:
        row = (
            db.query(CallLog)
            .filter(CallLog.inquiry_id == obj.id, CallLog.conversation_id == conversation_id)
            .order_by(CallLog.id.desc())
            .with_for_update()
            .first()
        )
    if row is None:
        row = (
            db.query(CallLog)
            .filter(CallLog.inquiry_id == obj.id, CallLog.completed_at.is_(None))
            .order_by(CallLog.started_at.desc(), CallLog.id.desc())
            .with_for_update()
            .first()
        )
    if row is None:
        row = CallLog(
            inquiry_id=obj.id,
            conversation_id=conversation_id or obj.call_conversation_id,
            is_test_call=bool(obj.is_test_call),
            started_at=obj.call_scheduled_for or _now(),
        )
        db.add(row)
        db.flush()
    elif conversation_id and not row.conversation_id:
        row.conversation_id = conversation_id
    return row


def record_submit_answer_result(
    db: Session,
    obj: Inquiry,
    *,
    provider_status: str,
    summary: Optional[str],
    completed_at: datetime,
) -> CallLog:
    """submit_answer's mid-call structured result — partial by nature (no
    transcript; the agent reports this live, before the call ends).
    Deliberately does NOT set resolved_at, so the fuller post-call webhook
    that normally follows is still free to fill in the transcript on this
    same row instead of being mistaken for a duplicate delivery."""
    row = find_call_log_for_completion(db, obj, obj.call_conversation_id)
    row.provider_status = provider_status
    if summary:
        row.summary = summary
    row.completed_at = completed_at
    return row


def record_terminal_result(
    db: Session,
    obj: Inquiry,
    *,
    conversation_id: Optional[str],
    provider_status: Optional[str],
    summary: Optional[str],
    transcript: Optional[str],
    completed_at: datetime,
) -> tuple[CallLog, bool]:
    """A confirmed terminal outcome from the post-call webhook or
    reconciliation's ElevenLabs poll (call_outcome_service.apply_call_outcome
    is the sole caller). Sets resolved_at — this call is done, no further
    update is expected for it.

    Returns (row, had_prior_summary) — had_prior_summary reflects whether
    THIS SAME call's row already carried a summary before this write (i.e.
    submit_answer already ran for it), captured before it's overwritten
    below. The caller uses this to decide whether to update
    Inquiry.final_answer: a call that submit_answer already answered must
    not have its structured answer downgraded by this webhook's own
    extracted summary, but a genuinely fresh call (including a follow-up
    call on an inquiry that already has an OLDER final_answer) must be
    allowed to update it — see call_outcome_service.apply_call_outcome.
    """
    row = find_call_log_for_completion(db, obj, conversation_id or obj.call_conversation_id)
    had_prior_summary = bool(row.summary)
    if provider_status:
        row.provider_status = provider_status
    if summary:
        row.summary = summary
    if transcript:
        row.transcript = transcript
    row.completed_at = completed_at
    row.resolved_at = completed_at
    return row, had_prior_summary


def record_manual_result(
    db: Session,
    obj: Inquiry,
    *,
    transcript: Optional[str],
    summary: Optional[str],
    completed_at: datetime,
) -> CallLog:
    """A human manually recording a call's result (routers.inquiries.
    record_call_result) — deliberately allowed to supersede an automated
    writer (same override semantics as the Inquiry-level fields), so this
    always sets resolved_at even if the row was already resolved."""
    row = find_call_log_for_completion(db, obj, obj.call_conversation_id)
    if transcript is not None:
        row.transcript = transcript
    if summary is not None:
        row.summary = summary
    row.completed_at = completed_at
    row.resolved_at = completed_at
    return row


def force_close_call_log(db: Session, obj: Inquiry, *, message: Optional[str] = None) -> CallLog:
    """Marks the currently-open CallLog as resolved with NO confirmed
    outcome — used when stuck-call reconciliation gives up (24h ceiling or
    provider NOT_FOUND) without ever confirming a result. Never fabricates
    transcript/summary; only fills provider_status as a fallback label when
    the call never reported one at all."""
    row = find_call_log_for_completion(db, obj, obj.call_conversation_id)
    row.provider_status = row.provider_status or "unresolved"
    row.completed_at = row.completed_at or _now()
    row.resolved_at = row.completed_at
    return row
