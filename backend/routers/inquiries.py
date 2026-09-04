import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload, selectinload

import call_log_service
import call_service
import email_service
import legacy_response_service
import slack_service
import summary_service
from database import get_db
from models import BulkEmailBatch, EmailReply, Inquiry, InquiryAttachment, ManufacturerContact, User
from routers.auth import get_current_user
from scheduler import EMAIL_SCHEDULE_DELAY_MINUTES
from schemas import (
    BulkInquiryCreate,
    BulkInquiryResult,
    CallResultPayload,
    EmailResponsePayload,
    FollowupEmailPayload,
    INQUIRY_SUBJECT_MAX_LENGTH,
    InquiryCreate,
    InquiryOut,
    InquiryUpdate,
    ScheduledEmailContentUpdate,
    TestCallPreviewPayload,
)


class TestCallPayload(BaseModel):
    phone_number: str = Field(..., min_length=7, description="Number to dial in E.164 format, e.g. +17705551234")

log = logging.getLogger("inquiry.inquiries")

router = APIRouter(prefix="/api/inquiries", tags=["inquiries"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _default_subject(inquiry_id: int) -> str:
    """The standardized subject every inquiry gets on creation."""
    return f"Drug information request [InpharmD #{inquiry_id}]"


def _with_subject_tag(subject: str, inquiry_id: int) -> str:
    """Guarantee the [InpharmD #<id>] reply-matching tag survives a user edit.

    Inquiry.subject is fully user-editable after creation and is used
    verbatim as the outbound email subject — but reply matching (imap_service
    / graph_service / routers.email_inbound) depends on this exact tag being
    present somewhere in the subject. If the user's edit already contains it
    (any case), it's kept as entered; otherwise it's appended.

    The edit inputs allow up to INQUIRY_SUBJECT_MAX_LENGTH characters (the
    same limit as the `subject` column), so appending the tag to a
    near-limit edit could push the result past that limit. When that would
    happen, only the user-entered portion is truncated — the tag itself is
    never shortened or dropped.
    """
    tag = f"[InpharmD #{inquiry_id}]"
    subject = (subject or "").strip()
    if tag.lower() in subject.lower():
        return subject
    if not subject:
        return tag
    combined = f"{subject} {tag}"
    if len(combined) <= INQUIRY_SUBJECT_MAX_LENGTH:
        return combined
    available = INQUIRY_SUBJECT_MAX_LENGTH - len(tag) - 1  # 1 for the joining space
    if available <= 0:
        return tag[:INQUIRY_SUBJECT_MAX_LENGTH]
    truncated = subject[:available].rstrip()
    return f"{truncated} {tag}" if truncated else tag


def _call_in_flight(obj: Inquiry) -> bool:
    """True when the most recently placed call for this inquiry hasn't
    finished yet — deliberately independent of `status`, so it still works
    when status is intentionally left as "closed" (a follow-up call on a
    closed inquiry never changes status, so status alone can't be used to
    detect a duplicate in-flight call there). Reuses two fields already
    stamped by every call-placement site (trigger_call, the bulk-call
    branch, fallback placement, retries) and every completion site (the
    post-call webhook, submit_answer): call_scheduled_for is re-stamped to
    "now" at every placement; call_completed_at is set only when a call
    actually finishes. If completion is missing or predates the most recent
    placement, that call is still outstanding. No new column needed."""
    if obj.call_conversation_id is None or obj.call_scheduled_for is None:
        return False
    return obj.call_completed_at is None or obj.call_completed_at < obj.call_scheduled_for


def _get_or_404(
    db: Session, inquiry_id: int, current_user: Optional[User] = None
) -> Inquiry:
    """Fetch inquiry by id. When `current_user` is given, enforce ownership
    (returns 404, not 403, so we don't leak existence to other users)."""
    q = (
        db.query(Inquiry)
        .options(
            joinedload(Inquiry.manufacturer),
            selectinload(Inquiry.inbound_attachments),
            selectinload(Inquiry.email_replies).selectinload(EmailReply.attachments),
            selectinload(Inquiry.call_logs),
        )
        .filter(Inquiry.id == inquiry_id)
    )
    if current_user is not None:
        q = q.filter(Inquiry.user_id == current_user.id)
    obj = q.first()
    if not obj:
        raise HTTPException(status_code=404, detail="Inquiry not found")
    return obj


@router.get("", response_model=List[InquiryOut])
def list_inquiries(
    status: Optional[str] = Query(None),
    manufacturer_id: Optional[int] = Query(None),
    source_inquiry_uuid: Optional[str] = Query(None),
    all_users: bool = Query(False, description="Return inquiries from all users (not just the caller's own)."),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(Inquiry).options(
        joinedload(Inquiry.manufacturer),
        selectinload(Inquiry.inbound_attachments),
        selectinload(Inquiry.email_replies).selectinload(EmailReply.attachments),
        selectinload(Inquiry.call_logs),
    )
    if not all_users:
        q = q.filter(Inquiry.user_id == current_user.id)
    if status:
        q = q.filter(Inquiry.status == status)
    if manufacturer_id:
        q = q.filter(Inquiry.manufacturer_id == manufacturer_id)
    if source_inquiry_uuid:
        q = q.filter(Inquiry.source_inquiry_uuid == source_inquiry_uuid)
    inquiries = q.order_by(Inquiry.created_at.desc()).all()

    # Attach creator display names when returning all-user results so the
    # frontend can show "by <name>" without a separate user lookup.
    if all_users:
        user_ids = {i.user_id for i in inquiries if i.user_id}
        users = {u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()}
        result = []
        for inq in inquiries:
            out = InquiryOut.model_validate(inq)
            u = users.get(inq.user_id)
            if u:
                out.created_by = u.display_name or u.email
            result.append(out)
        return result

    return inquiries


@router.get("/{inquiry_id}", response_model=InquiryOut)
def get_inquiry(
    inquiry_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _get_or_404(db, inquiry_id, current_user)


DEFAULT_REQUESTER_NAME = "Leah"
DEFAULT_REQUESTER_EMAIL = "druginfo@inpharmd.com"


@router.post("", response_model=InquiryOut, status_code=201)
def create_inquiry(
    payload: InquiryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    mfr = db.get(ManufacturerContact, payload.manufacturer_id)
    if not mfr:
        raise HTTPException(status_code=400, detail="Unknown manufacturer_id")
    data = payload.model_dump()
    # Inquiries always come from the InpharmD Drug Info inbox — backfill
    # defaults so older clients / API consumers don't have to send them.
    if not (data.get("requester_name") or "").strip():
        data["requester_name"] = DEFAULT_REQUESTER_NAME
    if not (data.get("requester_email") or "").strip():
        data["requester_email"] = DEFAULT_REQUESTER_EMAIL
    data["team_name"] = (data.get("team_name") or "").strip() or None
    obj = Inquiry(**data, status="draft", user_id=current_user.id)
    db.add(obj)
    db.flush()
    # Backend is the single source of truth for the outbound subject tag —
    # discard whatever free-text subject the client sent once the real id
    # exists, mirroring send_inquiry_email's own subject override.
    obj.subject = _default_subject(obj.id)
    db.commit()
    return _get_or_404(db, obj.id, current_user)


@router.post("/bulk", response_model=BulkInquiryResult, status_code=201)
async def bulk_create_inquiries(
    payload: BulkInquiryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create one Inquiry per target manufacturer with shared subject/question.

    Used by the Contact-Manufacturer page after we auto-detect manufacturers
    from the MUE Excel attachment. All created inquiries share the same
    `source_inquiry_uuid` and `source_excel_url`, with a per-target
    `source_excel_row` so the response-writeback can find the right row.

    Dispatch:
      - email → email all created inquiries
      - call  → trigger ElevenLabs voice agent for all created inquiries
      - none  → leave as drafts
    """
    if not payload.targets:
        raise HTTPException(status_code=422, detail="At least one target is required")

    # Resolve dispatch channel — keep the legacy `send_email` field working.
    channel = (payload.dispatch_channel or "email").strip().lower()
    if payload.send_email is False and channel == "email":
        channel = "none"
    if channel not in ("email", "call", "none"):
        raise HTTPException(
            status_code=422,
            detail=f"Unknown dispatch_channel '{channel}'. Use email|call|none.",
        )

    requester_name = (payload.requester_name or "").strip() or DEFAULT_REQUESTER_NAME
    requester_email = (payload.requester_email or "").strip() or DEFAULT_REQUESTER_EMAIL

    created_objs: list[Inquiry] = []
    failed: list[dict] = []

    for tgt in payload.targets:
        mfr = db.get(ManufacturerContact, tgt.manufacturer_id)
        if not mfr:
            failed.append({"manufacturer_id": tgt.manufacturer_id, "error": "Unknown manufacturer"})
            continue
        obj = Inquiry(
            manufacturer_id=tgt.manufacturer_id,
            subject=payload.subject,
            question=payload.question,
            requester_name=requester_name,
            requester_email=requester_email,
            # Per-target override wins when provided (manual multi-manufacturer
            # flow); the Excel/MUE flow never sets this, so it always falls
            # back to the batch-level value — unchanged for that flow.
            fallback_after_hours=(
                tgt.fallback_after_hours
                if tgt.fallback_after_hours is not None
                else payload.fallback_after_hours
            ),
            source_inquiry_uuid=payload.source_inquiry_uuid,
            source_excel_url=payload.source_excel_url,
            source_excel_sheet=payload.source_excel_sheet,
            source_excel_row=tgt.source_excel_row,
            team_name=(payload.team_name or "").strip() or None,
            medication_name=tgt.medication_name or None,
            pi_storage_data=tgt.pi_storage_data or None,
            pi_link=tgt.pi_link or None,
            status="draft",
            user_id=current_user.id,
        )
        db.add(obj)
        db.flush()
        # Same backend-authoritative subject override as create_inquiry —
        # each inquiry in the batch gets its own id-specific subject.
        obj.subject = _default_subject(obj.id)
        created_objs.append(obj)

    db.commit()

    dispatched = 0

    if channel == "email":
        # Every inquiry gets its own stagger slot in selection order.
        # No two inquiries share the same email_scheduled_for, even if they
        # target the same manufacturer or recipient address.
        # First email: T+EMAIL_SCHEDULE_DELAY_MINUTES. Each subsequent email:
        # +1 minute after the previous one (slot is 1-indexed, so slot 1 adds
        # zero extra minutes, slot 2 adds one, etc.)
        bulk_base = _now()
        slot = 0
        batch_id: Optional[str] = None
        batch_items: list[dict] = []
        for obj in list(created_objs):
            # Idempotency: if this inquiry was already dispatched (e.g. client
            # retry after a network blip), skip it.
            if obj.email_sent_at is not None or obj.status == "email_pending":
                continue
            mfr = db.get(ManufacturerContact, obj.manufacturer_id)
            if not mfr:
                failed.append({"manufacturer_id": obj.manufacturer_id, "error": "Manufacturer disappeared after create"})
                continue
            to_email = mfr.official_mi_email or mfr.team_verified_email
            if not to_email:
                failed.append({
                    "manufacturer_id": obj.manufacturer_id,
                    "error": f"{mfr.manufacturer} has no email address on file",
                })
                continue
            slot += 1
            if batch_id is None:
                # Only mint a batch id once we know at least one inquiry in
                # this call is actually being scheduled.
                batch_id = uuid.uuid4().hex
            obj.status = "email_pending"
            obj.email_scheduled_for = bulk_base + timedelta(minutes=EMAIL_SCHEDULE_DELAY_MINUTES + (slot - 1))
            obj.bulk_batch_id = batch_id
            batch_items.append({
                "inquiry_id": obj.id,
                "manufacturer": mfr.manufacturer,
                "medication_name": obj.medication_name,
                "email_scheduled_for": obj.email_scheduled_for,
            })
            dispatched += 1
        db.commit()

        if batch_id and batch_items:
            # The inquiries above are already committed and successfully
            # scheduled regardless of what happens next — a failure here must
            # never turn a successful dispatch into a misleading API failure.
            try:
                db.add(BulkEmailBatch(batch_id=batch_id))
                db.commit()
            except Exception:
                db.rollback()
                log.exception(
                    "Failed to create BulkEmailBatch tracking row for batch %s "
                    "(%d inquiries already scheduled); the completion notification "
                    "will not be possible for this batch",
                    batch_id, len(batch_items),
                )
            try:
                slack_service.notify_bulk_scheduled(
                    batch_id, batch_items,
                    question=payload.question,
                    source_inquiry_uuid=payload.source_inquiry_uuid,
                )
            except Exception:
                log.exception("Slack bulk-scheduled notification failed for batch %s", batch_id)

    elif channel == "call":
        # Group by phone number so multiple MUE rows resolving to the same
        # manufacturer place ONE call, not N. All siblings share the returned
        # conversation_id so any inbound outcome updates every row.
        call_groups: dict[str, list[Inquiry]] = {}
        for obj in list(created_objs):
            if obj.call_conversation_id:
                # Idempotency: already dispatched (e.g. client retry).
                continue
            mfr = db.get(ManufacturerContact, obj.manufacturer_id)
            if not mfr:
                failed.append({"manufacturer_id": obj.manufacturer_id, "error": "Manufacturer disappeared after create"})
                continue
            if not mfr.mi_phone:
                failed.append({
                    "manufacturer_id": obj.manufacturer_id,
                    "error": f"{mfr.manufacturer} has no MI phone number on file",
                })
                continue
            in_hours = call_service.is_within_business_hours(mfr.mi_phone_hours)
            if in_hours is False:
                failed.append({
                    "manufacturer_id": obj.manufacturer_id,
                    "error": f"{mfr.manufacturer} is outside business hours ({mfr.mi_phone_hours})",
                })
                continue
            call_groups.setdefault(mfr.mi_phone.strip(), []).append(obj)

        for phone, siblings in call_groups.items():
            primary = siblings[0]
            mfr = db.get(ManufacturerContact, primary.manufacturer_id)
            try:
                resp = await call_service.place_inquiry_call(
                    inquiry_id=primary.id,
                    to_number=phone,
                    manufacturer_name=mfr.manufacturer,
                    subject=primary.subject,
                    question=primary.question,
                    requester_name=primary.requester_name,
                    requester_email=primary.requester_email,
                )
            except Exception as e:
                for sib in siblings:
                    failed.append({
                        "manufacturer_id": sib.manufacturer_id,
                        "error": f"Call failed: {e}",
                    })
                continue
            conv_id = resp.get("conversation_id") or resp.get("conversationId")
            provider_status = resp.get("status") or "initiated"
            now = _now()
            for sib in siblings:
                sib.status = "call_pending"
                sib.call_scheduled_for = now
                sib.call_conversation_id = conv_id
                sib.call_provider_status = provider_status
                sib.next_retry_at = None
                if sib.first_contacted_at is None:
                    sib.first_contacted_at = now
                # One CallLog row per sibling inquiry, even though they all
                # share the same physical call/conversation_id — each
                # sibling is its own Inquiry row with its own Timeline.
                call_log_service.start_call_log(
                    db, sib,
                    conversation_id=conv_id,
                    provider_status=provider_status,
                    started_at=now,
                )
            dispatched += 1  # unique calls placed, not inquiries stamped
        db.commit()

    refreshed = [_get_or_404(db, obj.id, current_user) for obj in created_objs]
    return BulkInquiryResult(
        created=refreshed,
        failed=failed,
        dispatch_channel=channel,
        dispatched=dispatched,
    )


@router.put("/{inquiry_id}", response_model=InquiryOut)
def update_inquiry(
    inquiry_id: int,
    payload: InquiryUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    obj = _get_or_404(db, inquiry_id, current_user)
    for k, v in payload.model_dump(exclude_unset=True).items():
        if k == "subject" and v is not None:
            v = _with_subject_tag(v, obj.id)
        setattr(obj, k, v)
    db.commit()
    return _get_or_404(db, inquiry_id, current_user)


@router.delete("/{inquiry_id}", status_code=204)
def delete_inquiry(
    inquiry_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    obj = _get_or_404(db, inquiry_id, current_user)
    db.delete(obj)
    db.commit()
    return None


# ---------- Lifecycle transitions ----------

@router.post("/{inquiry_id}/send-email", response_model=InquiryOut)
def send_email(
    inquiry_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Schedule the inquiry email for delivery after EMAIL_SCHEDULE_DELAY_MINUTES.

    The inquiry moves to `email_pending`. The background scheduler sends it
    and transitions to `email_sent`. Use POST /send-now to send immediately,
    or POST /cancel-scheduled-email to revert to draft."""
    obj = _get_or_404(db, inquiry_id, current_user)
    if obj.status not in ("draft", "failed"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot schedule email from status '{obj.status}'",
        )

    mfr = db.get(ManufacturerContact, obj.manufacturer_id)
    if not mfr:
        raise HTTPException(status_code=400, detail="Manufacturer missing")
    to_email = mfr.official_mi_email or mfr.team_verified_email
    if not to_email:
        raise HTTPException(
            status_code=400,
            detail=f"{mfr.manufacturer} has no email address on file",
        )

    obj.status = "email_pending"
    obj.email_scheduled_for = _now() + timedelta(minutes=EMAIL_SCHEDULE_DELAY_MINUTES)
    db.commit()
    return _get_or_404(db, inquiry_id, current_user)


@router.post("/{inquiry_id}/cancel-scheduled-email", response_model=InquiryOut)
def cancel_scheduled_email(
    inquiry_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Cancel a scheduled email and revert the inquiry to draft.

    Uses SELECT FOR UPDATE so a concurrent scheduler tick (SKIP LOCKED) will
    skip this row; if the scheduler already committed email_sent we re-check
    and return 409 rather than silently downgrading the status."""
    _get_or_404(db, inquiry_id, current_user)  # ownership check
    locked = (
        db.query(Inquiry)
        .with_for_update()
        .filter(Inquiry.id == inquiry_id, Inquiry.status == "email_pending")
        .first()
    )
    if locked is None:
        raise HTTPException(
            status_code=409,
            detail="Cannot cancel: inquiry is no longer in the scheduling window (it may have already been sent).",
        )
    locked.status = "draft"
    locked.email_scheduled_for = None
    db.commit()
    return _get_or_404(db, inquiry_id, current_user)


@router.patch("/{inquiry_id}/scheduled-email-content", response_model=InquiryOut)
def edit_scheduled_email_content(
    inquiry_id: int,
    payload: ScheduledEmailContentUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update subject and question while the email is in the scheduling window.

    Uses SELECT FOR UPDATE for the same reason as cancel: prevents a race where
    the scheduler commits email_sent between the status check and the UPDATE."""
    _get_or_404(db, inquiry_id, current_user)  # ownership check
    locked = (
        db.query(Inquiry)
        .with_for_update()
        .filter(Inquiry.id == inquiry_id, Inquiry.status == "email_pending")
        .first()
    )
    if locked is None:
        raise HTTPException(
            status_code=409,
            detail="Cannot edit: inquiry is no longer in the scheduling window (it may have already been sent).",
        )
    locked.subject = _with_subject_tag(payload.subject, locked.id)
    locked.question = payload.question
    db.commit()
    return _get_or_404(db, inquiry_id, current_user)


@router.post("/{inquiry_id}/send-now", response_model=InquiryOut)
def send_now(
    inquiry_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Send the scheduled email immediately, bypassing the delay window.

    Uses SELECT FOR UPDATE so a concurrent scheduler tick (which uses
    SKIP LOCKED) will skip this row, guaranteeing exactly-once delivery."""
    obj = _get_or_404(db, inquiry_id, current_user)

    # Re-fetch with row lock to serialize against the scheduler tick.
    locked = (
        db.query(Inquiry)
        .with_for_update()
        .filter(Inquiry.id == inquiry_id, Inquiry.status == "email_pending")
        .first()
    )
    if locked is None:
        raise HTTPException(
            status_code=409,
            detail="Email is no longer scheduled (it may have already been sent).",
        )

    mfr = db.get(ManufacturerContact, locked.manufacturer_id)
    if not mfr:
        raise HTTPException(status_code=400, detail="Manufacturer missing")
    to_email = mfr.official_mi_email or mfr.team_verified_email
    if not to_email:
        raise HTTPException(
            status_code=400,
            detail=f"{mfr.manufacturer} has no email address on file",
        )

    try:
        message_id = email_service.send_inquiry_email(
            inquiry_id=locked.id,
            manufacturer_name=mfr.manufacturer,
            to_email=to_email,
            subject=locked.subject,
            question=locked.question,
            requester_name=locked.requester_name,
            requester_email=locked.requester_email,
            medication_name=locked.medication_name,
            pi_storage_data=locked.pi_storage_data,
            pi_link=locked.pi_link,
            team_name=locked.team_name,
        )
    except email_service.EmailConfigError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Email send failed: {e}")

    now = _now()
    locked.status = "email_sent"
    locked.email_sent_at = now
    locked.email_message_id = message_id
    locked.email_scheduled_for = None
    if locked.first_contacted_at is None:
        locked.first_contacted_at = now
    if mfr.fallback_call_enabled and mfr.mi_phone:
        fallback_delta = timedelta(minutes=5) if locked.fallback_after_hours == 0 else timedelta(hours=locked.fallback_after_hours)
        locked.call_scheduled_for = now + fallback_delta
    elif mfr.fallback_call_enabled and not mfr.mi_phone:
        log.warning("Inquiry %s: fallback skipped — manufacturer '%s' has no MI phone", locked.id, mfr.manufacturer)

    db.commit()
    return _get_or_404(db, inquiry_id, current_user)


_MANUAL_SMTP_ID = "__manual__"  # sentinel for manually-logged email responses


@router.post("/{inquiry_id}/record-email-response", response_model=InquiryOut)
def record_email_response(
    inquiry_id: int,
    payload: EmailResponsePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Ownership check + 404 guard.
    _get_or_404(db, inquiry_id, current_user)

    # Acquire row-level lock and refresh attributes from DB — populate_existing()
    # ensures the in-memory object reflects the current DB state even if the
    # session already held a pre-lock snapshot from _get_or_404 above.
    # Mirrors cancel_scheduled_email, send_now, trigger_call in this file.
    obj = (
        db.query(Inquiry)
        .options(joinedload(Inquiry.manufacturer))
        .populate_existing()
        .filter(Inquiry.id == inquiry_id)
        .with_for_update()
        .first()
    )

    if obj.status == "closed":
        raise HTTPException(status_code=409, detail="Inquiry is closed")

    new_text = (payload.response or "").strip()

    # Find the single manual EmailReply for this inquiry, identified by the
    # "__manual__" sentinel in smtp_message_id. Using a non-null sentinel (rather
    # than NULL) prevents collision with rare real SendGrid replies that arrive
    # without a Message-ID header, and lets the existing partial unique index
    # on (inquiry_id, smtp_message_id) enforce one manual reply per inquiry.
    existing_manual = (
        db.query(EmailReply)
        .filter(
            EmailReply.inquiry_id == inquiry_id,
            EmailReply.direction == "inbound",
            EmailReply.smtp_message_id == _MANUAL_SMTP_ID,
        )
        .first()
    )

    text_changed = (
        new_text != (existing_manual.body or "").strip()
        if existing_manual else True
    )

    now = _now()
    if existing_manual:
        existing_manual.body = new_text
        existing_manual.sent_at = now
        email_reply = existing_manual
    else:
        email_reply = EmailReply(
            inquiry_id=inquiry_id,
            direction="inbound",
            sender_email=None,
            body=new_text,
            sent_at=now,
            graph_message_id=None,
            smtp_message_id=_MANUAL_SMTP_ID,
        )
        db.add(email_reply)
        db.flush()  # populate email_reply.id before using it as event_key

    # Update inquiry scalar fields — mirrors first-reply handling in real paths.
    obj.status = "email_responded"
    obj.email_response = new_text
    obj.email_response_at = now
    obj.final_answer = new_text
    obj.next_retry_at = None
    obj.call_scheduled_for = None

    if text_changed:
        # Clear the legacy POST guard only when it already points at this exact
        # reply's event_key. If it points to a different event (real email, call)
        # the new event_key already differs and maybe_post_for_inquiry fires
        # without needing the guard cleared.
        if obj.legacy_last_event_key == f"email:{email_reply.id}":
            obj.legacy_last_event_key = None
        # Allow Excel writeback to re-run with the corrected text.
        # _pick_latest_excel_url always fetches the newest sibling's combined
        # file, so only this inquiry's row is overwritten; siblings are safe.
        obj.excel_response_posted_at = None

    db.commit()

    # Legacy POST via the email path — body read from EmailReply row,
    # attachments scoped to reply_id (empty for manual entries, no files).
    try:
        legacy_response_service.maybe_post_for_inquiry(
            db, obj, f"email:{email_reply.id}",
            email_reply_id=email_reply.id,
        )
    except Exception:
        log.exception(
            "Legacy POST failed for inquiry %s (manual email response stored)",
            inquiry_id,
        )

    # Slack — only when text changed to avoid notification spam on identical re-saves.
    if text_changed:
        try:
            mfr = obj.manufacturer
            mfr_name = mfr.manufacturer if mfr else "the manufacturer"
            if slack_service.is_configured():
                slack_service.notify_reply(
                    inquiry_id=obj.id,
                    manufacturer=mfr_name,
                    subject=obj.subject,
                    question=obj.question,
                    answer=obj.final_answer or new_text,
                    requester_name=obj.requester_name,
                    requester_email=obj.requester_email,
                    sender_email=None,
                )
        except Exception:
            log.exception(
                "Slack notify failed for inquiry %s (manual email response)",
                inquiry_id,
            )

    return _get_or_404(db, inquiry_id, current_user)


@router.post("/{inquiry_id}/send-followup-email", response_model=InquiryOut)
def send_followup_email(
    inquiry_id: int,
    payload: FollowupEmailPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Send an additional, manually-drafted email for this inquiry —
    available regardless of status (including closed/completed), unlike
    send_email/send_now which model the one-time original dispatch and are
    intentionally left unchanged. Reuses the exact same send mechanism
    (email_service.send_inquiry_email) and the inquiry's existing, already
    reply-matching-tagged subject, so replies continue to be found the same
    way they always have. Does not touch status, email_sent_at, or
    email_message_id — those continue to represent the original send.
    Recorded as an outbound EmailReply so the popup's email thread shows a
    real history of every follow-up, distinct from manufacturer replies."""
    obj = _get_or_404(db, inquiry_id, current_user)
    mfr = db.get(ManufacturerContact, obj.manufacturer_id)
    if not mfr:
        raise HTTPException(status_code=400, detail="Manufacturer missing")
    to_email = mfr.official_mi_email or mfr.team_verified_email
    if not to_email:
        raise HTTPException(
            status_code=400,
            detail=f"{mfr.manufacturer} has no email address on file",
        )

    body = payload.body.strip()
    if not body:
        raise HTTPException(status_code=422, detail="Follow-up message cannot be empty")

    try:
        message_id = email_service.send_inquiry_email(
            inquiry_id=obj.id,
            manufacturer_name=mfr.manufacturer,
            to_email=to_email,
            subject=obj.subject,
            question=body,
            requester_name=obj.requester_name,
            requester_email=obj.requester_email,
            medication_name=obj.medication_name,
            pi_storage_data=obj.pi_storage_data,
            pi_link=obj.pi_link,
            team_name=obj.team_name,
            is_followup=True,
        )
    except email_service.EmailConfigError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Email send failed: {e}")

    db.add(
        EmailReply(
            inquiry_id=obj.id,
            direction="outbound",
            sender_email=None,
            body=body,
            sent_at=_now(),
            smtp_message_id=message_id or None,
        )
    )
    db.commit()
    return _get_or_404(db, inquiry_id, current_user)


@router.post("/{inquiry_id}/business-hours")
def business_hours_check(
    inquiry_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Returns whether the manufacturer is in business hours right now,
    based on the parsed `mi_phone_hours` text. Used by the UI to warn
    before placing a call."""
    obj = _get_or_404(db, inquiry_id, current_user)
    if not obj.manufacturer:
        return {"known": False, "reason": "no manufacturer"}
    # ManufacturerSummary may not include hours; fetch fully
    mfr = db.get(ManufacturerContact, obj.manufacturer_id)
    hours_text = mfr.mi_phone_hours if mfr else None
    result = call_service.is_within_business_hours(hours_text)
    return {
        "known": result is not None,
        "in_hours": result,
        "hours_text": hours_text,
        "phone": mfr.mi_phone if mfr else None,
    }


@router.post("/{inquiry_id}/trigger-call", response_model=InquiryOut)
async def trigger_call(
    inquiry_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Place an outbound ElevenLabs call to the manufacturer with this
    inquiry's context. Updates the inquiry's status, scheduled time, and
    stores the ElevenLabs `conversation_id` for the post-call webhook."""
    obj = _get_or_404(db, inquiry_id, current_user)
    if obj.is_test_call:
        raise HTTPException(
            status_code=409,
            detail="Cannot trigger a production call on a test call inquiry.",
        )
    # Deliberately no "status == closed" guard: a follow-up call must remain
    # possible for a closed inquiry (see status-preservation handling below,
    # where `locked.status` is left untouched when it's already "closed").
    if _call_in_flight(obj):
        raise HTTPException(
            status_code=409,
            detail="A call is already in progress for this inquiry. Wait for it to complete before placing another.",
        )
    # Deliberately no "already successfully answered" guard: a follow-up
    # call must remain possible even after a completed, answered call (e.g.
    # the user has a further question) — the in-flight check above is the
    # only duplicate-prevention needed.

    # Manual call placement is intentionally independent of the email/fallback
    # workflow — an inquiry that already has an email sent or a fallback call
    # scheduled can still be called manually at any time. (Duplicate-call,
    # closed, phone-number, test-call, and business-hours protections above
    # and below still apply.)

    mfr = db.get(ManufacturerContact, obj.manufacturer_id)
    if not mfr:
        raise HTTPException(status_code=400, detail="Manufacturer missing")
    if not mfr.mi_phone:
        raise HTTPException(
            status_code=400,
            detail=f"{mfr.manufacturer} has no MI phone number on file",
        )

    in_hours = call_service.is_within_business_hours(mfr.mi_phone_hours)
    if in_hours is False:
        hours_str = f" ({mfr.mi_phone_hours})" if mfr.mi_phone_hours else ""
        raise HTTPException(
            status_code=409,
            detail=f"{mfr.manufacturer} is currently outside business hours{hours_str}.",
        )

    # Re-fetch with a row lock before placing the call so two concurrent requests
    # for the same inquiry cannot both pass the status checks above and both dial out.
    locked = (
        db.query(Inquiry)
        .with_for_update()
        .filter(Inquiry.id == inquiry_id)
        .first()
    )
    if locked is None:
        raise HTTPException(status_code=404, detail="Inquiry not found")
    # Re-check the guards on the now-locked row in case state changed between the
    # initial read and the lock acquisition. Uses the same status-independent
    # in-flight check as above, so this still catches a concurrent duplicate
    # even when status is (and remains) "closed".
    if _call_in_flight(locked):
        raise HTTPException(
            status_code=409,
            detail="A call is already in progress for this inquiry. Wait for it to complete before placing another.",
        )

    try:
        resp = await call_service.place_inquiry_call(
            inquiry_id=locked.id,
            to_number=mfr.mi_phone,
            manufacturer_name=mfr.manufacturer,
            subject=locked.subject,
            question=locked.question,
            requester_name=locked.requester_name,
            requester_email=locked.requester_email,
        )
    except call_service.CallConfigError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502,
            detail=f"ElevenLabs rejected the call: {e.response.status_code} {e.response.text}",
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to place call: {e}")

    # Moving off "email_sent" here also cancels any pending fallback call:
    # the background scanner (scheduler._scan_and_trigger_fallback_calls)
    # only ever matches status == "email_sent", so once this manual call is
    # placed the fallback job can never pick this inquiry up again, even if
    # a fallback was already scheduled.
    now = _now()
    # A closed inquiry stays closed — a follow-up call must never silently
    # reopen it. call_scheduled_for/call_conversation_id/call_provider_status
    # are still updated unconditionally below, both so the post-call webhook
    # can find and record against this row, and so _call_in_flight() (which
    # doesn't look at status at all) correctly detects this call as
    # in-progress even while status stays "closed".
    if locked.status != "closed":
        locked.status = "call_pending"
    locked.call_scheduled_for = now
    locked.call_conversation_id = (
        resp.get("conversation_id") or resp.get("conversationId")
    )
    locked.call_provider_status = resp.get("status") or "initiated"
    # Cleared on every new placement, not just the first — call_completed_at
    # otherwise keeps holding the PRIOR call's completion timestamp for a
    # follow-up call (status intentionally doesn't change for a closed
    # inquiry, so it can't be used to tell "new call placed" apart from
    # "old call already resolved"). Left stale, this new call's own webhook
    # would be misread as a duplicate/already-resolved delivery by both
    # routers.webhooks' guard and scheduler._reconcile_stuck_calls, and
    # silently ignored.
    locked.call_completed_at = None
    locked.next_retry_at = None  # manual trigger cancels any pending auto-retry
    # Only the manufacturer's actual first contact counts — if email was
    # already sent, that (not this call) was first contact, so this is a
    # no-op guard, not an overwrite.
    if locked.first_contacted_at is None:
        locked.first_contacted_at = now
    call_log_service.start_call_log(
        db, locked,
        conversation_id=locked.call_conversation_id,
        provider_status=locked.call_provider_status,
        started_at=locked.call_scheduled_for,
    )
    db.commit()
    return _get_or_404(db, inquiry_id, current_user)


@router.post("/test-call-preview", response_model=InquiryOut, status_code=201)
async def test_call_preview(
    payload: TestCallPreviewPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Place a test call and create a test inquiry to capture the transcript.

    Creates an Inquiry with is_test_call=True and status=call_pending so the
    post-call webhook can write the transcript back via call_conversation_id.
    All production workflows (retries, Slack, legacy POST) are blocked by the
    is_test_call flag. manufacturer_id is set only when the dialed number
    matches a known manufacturer; otherwise it is NULL and test_call_phone
    is the sole identifier shown in Outreach.
    """
    mfr = None
    mfr_name = "the manufacturer"
    if payload.manufacturer_id:
        mfr = db.get(ManufacturerContact, payload.manufacturer_id)
        if mfr:
            mfr_name = mfr.manufacturer

    obj = Inquiry(
        manufacturer_id=mfr.id if mfr else None,
        test_call_phone=payload.phone_number,
        subject=payload.subject,
        question=payload.question,
        status="call_pending",
        is_test_call=True,
        user_id=current_user.id,
        fallback_after_hours=0,
    )
    db.add(obj)
    db.flush()
    obj.subject = _default_subject(obj.id)

    try:
        resp = await call_service.place_inquiry_call(
            inquiry_id=obj.id,
            to_number=payload.phone_number,
            manufacturer_name=mfr_name,
            subject=payload.subject,
            question=payload.question,
            is_test=True,
        )
    except call_service.CallConfigError as e:
        db.rollback()
        raise HTTPException(status_code=503, detail=str(e))
    except httpx.HTTPStatusError as e:
        db.rollback()
        raise HTTPException(
            status_code=502,
            detail=f"ElevenLabs rejected the call: {e.response.status_code} {e.response.text}",
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=502, detail=f"Failed to place test call: {e}")

    conv_id = resp.get("conversation_id") or resp.get("conversationId")
    if not conv_id:
        db.rollback()
        raise HTTPException(
            status_code=502,
            detail="ElevenLabs did not return a conversation_id; the transcript cannot be captured.",
        )
    obj.call_conversation_id = conv_id
    obj.call_provider_status = resp.get("status") or "initiated"
    call_log_service.start_call_log(
        db, obj,
        conversation_id=conv_id,
        provider_status=obj.call_provider_status,
        started_at=_now(),
    )
    db.commit()

    return _get_or_404(db, obj.id, current_user)


@router.post("/{inquiry_id}/test-call", response_model=InquiryOut)
async def test_call(
    inquiry_id: int,
    payload: TestCallPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Dial an arbitrary phone number using THIS inquiry's question/manufacturer
    context. Lets the team test how the agent would speak to a real MI desk
    without bothering the manufacturer. Does not change inquiry status."""
    obj = _get_or_404(db, inquiry_id, current_user)
    mfr = db.get(ManufacturerContact, obj.manufacturer_id)
    if not mfr:
        raise HTTPException(status_code=400, detail="Manufacturer missing")

    try:
        await call_service.place_inquiry_call(
            inquiry_id=obj.id,
            to_number=payload.phone_number,
            manufacturer_name=mfr.manufacturer,
            subject=obj.subject,
            question=obj.question,
            requester_name=obj.requester_name,
            requester_email=obj.requester_email,
            is_test=True,
        )
    except call_service.CallConfigError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502,
            detail=f"ElevenLabs rejected the call: {e.response.status_code} {e.response.text}",
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to place test call: {e}")

    # Deliberately do NOT mutate obj.status or store conversation_id — test calls
    # should not interfere with the real inquiry's lifecycle.
    return _get_or_404(db, inquiry_id, current_user)


@router.post("/{inquiry_id}/extract-answer", response_model=InquiryOut)
def extract_answer(
    inquiry_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manually trigger LLM extraction of a clean answer from the call transcript.
    Useful when the agent's submit_answer didn't fire and you want the AI to
    summarize what was said."""
    obj = _get_or_404(db, inquiry_id, current_user)
    if not obj.call_transcript:
        raise HTTPException(
            status_code=400,
            detail="No transcript available to extract from",
        )
    if not summary_service.is_configured():
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY not set. Add it to backend/.env to enable AI extraction.",
        )
    try:
        extracted = summary_service.extract_answer_from_transcript(
            question=obj.question,
            manufacturer=obj.manufacturer.manufacturer if obj.manufacturer else "the manufacturer",
            transcript=obj.call_transcript,
        )
    except summary_service.SummaryConfigError as e:
        raise HTTPException(status_code=503, detail=str(e))
    obj.call_summary = extracted
    obj.final_answer = extracted
    db.commit()
    legacy_response_service.maybe_post_for_inquiry(
        db, obj, f"call-extract:{obj.call_conversation_id}",
        direct_response_text=extracted,
    )
    return _get_or_404(db, inquiry_id, current_user)


@router.post("/{inquiry_id}/reset-retries", response_model=InquiryOut)
def reset_retries(
    inquiry_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manual override — clear the retry counter so this inquiry can auto-retry
    again. Useful when a user manually edits the inquiry and wants a fresh chance."""
    obj = _get_or_404(db, inquiry_id, current_user)
    obj.retry_count = 0
    obj.next_retry_at = None
    if obj.status == "needs_attention":
        obj.status = "draft"
    db.commit()
    return _get_or_404(db, inquiry_id, current_user)


@router.post("/{inquiry_id}/record-call-result", response_model=InquiryOut)
def record_call_result(
    inquiry_id: int,
    payload: CallResultPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manual entry point: lets a human (or another integration) attach the
    call result without going through the ElevenLabs webhook."""
    _get_or_404(db, inquiry_id, current_user)  # ownership/404 check
    # Row lock: a manual entry can race with the webhook or the stuck-call
    # reconciliation job resolving the same inquiry concurrently. Unlike
    # those two automated writers, manual entry deliberately does NOT skip
    # on obj.call_completed_at IS NOT NULL — a human explicitly recording a
    # result is an intentional override, not a race, and is allowed to
    # supersede an automated one.
    obj = (
        db.query(Inquiry)
        .filter(Inquiry.id == inquiry_id)
        .with_for_update()
        .first()
    )
    # Same closed-inquiry status preservation as trigger_call/webhooks —
    # a manually-recorded result for a closed inquiry's follow-up call
    # must not reopen it.
    if obj.status != "closed":
        obj.status = "call_completed"
    now = _now()
    obj.call_completed_at = now
    if payload.transcript is not None:
        obj.call_transcript = payload.transcript
    if payload.summary is not None:
        obj.call_summary = payload.summary
        obj.final_answer = payload.summary
    call_log_service.record_manual_result(
        db, obj,
        transcript=payload.transcript,
        summary=payload.summary,
        completed_at=now,
    )
    # Resolved — no longer eligible for stuck-call reconciliation polling.
    obj.call_reconcile_failure_count = 0
    obj.call_reconcile_next_attempt_at = None
    db.commit()
    if not obj.is_test_call:
        legacy_response_service.maybe_post_for_inquiry(
            db, obj, f"call:{obj.call_conversation_id}",
            direct_response_text=payload.summary,
        )
    return _get_or_404(db, inquiry_id, current_user)


@router.post("/{inquiry_id}/close", response_model=InquiryOut)
def close_inquiry(
    inquiry_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    obj = _get_or_404(db, inquiry_id, current_user)
    obj.status = "closed"
    obj.email_scheduled_for = None  # cancel any pending schedule
    # Write-once — closing an already-closed inquiry (should not normally
    # happen; the frontend hides the button once closed) must not move the
    # timestamp forward.
    if obj.closed_at is None:
        obj.closed_at = _now()
    db.commit()
    return _get_or_404(db, inquiry_id, current_user)


@router.post("/{inquiry_id}/reprocess-pdf", response_model=InquiryOut)
async def reprocess_pdf(
    inquiry_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Backfill: re-search the InpharmD mailbox for the original reply that
    matches this inquiry's subject tag, pull all supported document attachments,
    upload + summarize each one, and replace the stored attachment rows.

    If no attachments can be uploaded (e.g. S3 outage), the operation is fully
    rolled back — original attachment rows and scalar fields are preserved."""
    import asyncio
    import os
    import httpx
    import graph_service
    import s3_service
    import summary_service

    obj = _get_or_404(db, inquiry_id, current_user)

    if not graph_service.is_configured():
        raise HTTPException(status_code=503, detail="Graph API not configured")

    mailbox = os.getenv("GRAPH_MAILBOX") or os.getenv("EMAIL_FROM", "druginfo@inpharmd.com")
    token = await asyncio.to_thread(graph_service._get_token)
    headers = {"Authorization": f"Bearer {token}"}
    tag = f"[InpharmD #{inquiry_id}]"

    search_url = f"https://graph.microsoft.com/v1.0/users/{mailbox}/messages"
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            search_url,
            headers={**headers, "ConsistencyLevel": "eventual"},
            params={
                "$search": f'"{tag}"',
                "$select": "id,subject,hasAttachments",
                "$top": "10",
            },
        )
        if r.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Graph search failed: {r.status_code} {r.text[:200]}",
            )
        msgs = r.json().get("value", [])

    target = next((m for m in msgs if m.get("hasAttachments")), None)
    if not target:
        raise HTTPException(
            status_code=404,
            detail=f"No message with attachments found for {tag}",
        )

    docs = await asyncio.to_thread(
        graph_service._fetch_all_document_attachments, token, mailbox, target["id"]
    )
    if not docs:
        raise HTTPException(
            status_code=404,
            detail="Message has attachments but none are a supported document type (PDF, DOCX, XLSX, CSV, …)",
        )

    mfr_name = obj.manufacturer.manufacturer if obj.manufacturer else "the manufacturer"

    # Stage the delete inside the transaction — only committed if at least one
    # upload succeeds. If everything fails, we never call db.commit() and the
    # get_db finally-close rolls the delete back, leaving the original rows intact.
    db.query(InquiryAttachment).filter(InquiryAttachment.inquiry_id == inquiry_id).delete()

    # Clear backward-compat scalars now; they'll be set from the first
    # successfully-uploaded attachment. If all uploads fail, the transaction
    # rolls back and these assignments are discarded too.
    obj.pdf_url = None
    obj.pdf_filename = None
    obj.pdf_summary = None

    uploaded_count = 0
    for order, doc in enumerate(docs):
        url = await asyncio.to_thread(
            s3_service.upload_bytes,
            doc["bytes"],
            original_name=doc["name"],
            inquiry_id=inquiry_id,
            content_type=doc["content_type"],
        )
        if url is None:
            log.warning(
                "S3 upload returned None for inquiry %s attachment %d '%s'; skipping",
                inquiry_id, order, doc["name"],
            )
            continue
        uploaded_count += 1
        att_summary = None
        if summary_service.is_configured():
            text = await asyncio.to_thread(
                summary_service.extract_document_text, doc["name"], doc["bytes"]
            )
            if text:
                try:
                    att_summary = await asyncio.to_thread(
                        summary_service.summarize_pdf,
                        question=obj.question,
                        manufacturer=mfr_name,
                        pdf_text=text,
                    )
                except Exception as e:
                    log.warning(
                        "Attachment summary unavailable for inquiry %s attachment %d: %s",
                        inquiry_id, order, e,
                    )
        log.info(
            "Inquiry %s attachment %d '%s' (%d bytes) uploaded",
            inquiry_id, order, doc["name"], len(doc["bytes"]),
        )
        db.add(InquiryAttachment(
            inquiry_id=inquiry_id,
            url=url,
            filename=doc["name"],
            content_type=doc["content_type"],
            summary=att_summary,
            display_order=order,
        ))
        # Backward-compat scalars point to the first successfully-uploaded
        # attachment (not necessarily order==0 if earlier uploads failed).
        if uploaded_count == 1:
            obj.pdf_url = url
            obj.pdf_filename = doc["name"]
            obj.pdf_summary = att_summary

    if uploaded_count == 0:
        # Nothing uploaded — do not commit. The transaction rolls back on
        # db.close(), restoring the original InquiryAttachment rows exactly.
        raise HTTPException(
            status_code=503,
            detail="All attachment uploads failed; original attachments preserved. Check S3/R2 configuration.",
        )

    db.commit()
    return _get_or_404(db, obj.id, current_user)
