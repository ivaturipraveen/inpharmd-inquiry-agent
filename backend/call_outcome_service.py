"""Shared call-outcome state-transition logic.

Used identically by the ElevenLabs post-call webhook (routers/webhooks.py)
and the stuck-call reconciliation job (scheduler._reconcile_stuck_calls), so
a result recovered by polling the provider produces exactly the same
Inquiry state as one delivered via webhook — one implementation, not two
that could drift apart.

Each caller is responsible for parsing its own raw provider payload into the
normalized (provider_status, summary, transcript) parameters this function
accepts — the webhook payload and the GET-conversation response are
different wire shapes from the same provider, so that parsing step is
deliberately kept out of this shared function.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from models import Inquiry


def _now() -> datetime:
    return datetime.now(timezone.utc)


def apply_call_outcome(
    db: Session,
    obj: Inquiry,
    *,
    provider_status: str,
    summary: Optional[str],
    transcript: Optional[str],
    conversation_id: Optional[str] = None,
) -> None:
    """Apply a confirmed call outcome to an inquiry. Caller must already
    hold a row lock (with_for_update) and have verified the row isn't
    already resolved (obj.call_completed_at is None) before calling this —
    it does not re-check either, to keep it a pure state-transition
    function usable from any already-locked context.

    conversation_id should be the caller's own known-correct identifier for
    the call being resolved (the webhook's incoming payload ID, or the ID
    reconciliation just polled) — NOT read from obj.call_conversation_id
    here, because that field can (rarely) still hold a stale, non-matching
    ID at this point (see routers.webhooks' inquiry_id-fallback match path,
    which does not unconditionally overwrite it). Falls back to
    obj.call_conversation_id only when the caller has nothing more precise.
    """
    # Deferred import — scheduler.py imports this module at top level, so an
    # eager import here would be circular.
    from scheduler import schedule_retry_after_failure
    import call_log_service

    now = _now()

    # CallLog is the append-only counterpart to the Inquiry.call_* fields below
    # (see models.CallLog) — recorded first so it's safe even if code below raises.
    _call_log_row, _had_prior_summary = call_log_service.record_terminal_result(
        db, obj,
        conversation_id=conversation_id or obj.call_conversation_id,
        provider_status=provider_status,
        summary=summary,
        transcript=transcript,
        completed_at=now,
    )

    obj.call_outcome_unknown_until = None
    obj.call_completed_at = now
    if summary:
        obj.call_summary = summary
        # Only overwrite final_answer if submit_answer didn't already set one for
        # THIS call (had_prior_summary) — never downgrades an answer already given.
        if not _had_prior_summary:
            obj.final_answer = summary
    if transcript:
        obj.call_transcript = transcript

    # If submit_answer already set a meaningful provider_status, don't overwrite it.
    if not obj.call_provider_status or obj.call_provider_status == "initiated":
        obj.call_provider_status = provider_status

    # needs_attention is included so a late-arriving real result still lands,
    # instead of leaving the inquiry stuck on a "could not confirm" placeholder.
    if obj.status in ("call_pending", "needs_attention"):
        obj.status = "call_completed"

    # Fallback calls (email_sent_at set) are one-shot — voicemail/no_answer goes
    # straight to needs_attention; normal calls retry only if no answer yet.
    if obj.call_provider_status in ("voicemail", "no_answer"):
        if obj.email_sent_at:
            if obj.status != "closed":
                obj.status = "needs_attention"
            obj.next_retry_at = None
        elif not obj.call_summary:
            schedule_retry_after_failure(db, obj, delay_minutes=2)

    # Resolved — no longer eligible for stuck-call reconciliation polling.
    obj.call_reconcile_failure_count = 0
    obj.call_reconcile_next_attempt_at = None
