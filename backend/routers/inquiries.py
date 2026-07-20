import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload, selectinload

import call_service
import email_service
import legacy_response_service
import summary_service
from database import get_db
from models import EmailReply, Inquiry, InquiryAttachment, ManufacturerContact, User
from routers.auth import get_current_user
from schemas import (
    BulkInquiryCreate,
    BulkInquiryResult,
    CallResultPayload,
    EmailResponsePayload,
    InquiryCreate,
    InquiryOut,
    InquiryUpdate,
)


class TestCallPayload(BaseModel):
    phone_number: str = Field(..., min_length=7, description="Number to dial in E.164 format, e.g. +17705551234")

log = logging.getLogger("inquiry.inquiries")

router = APIRouter(prefix="/api/inquiries", tags=["inquiries"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


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
    obj = Inquiry(**data, status="draft", user_id=current_user.id)
    db.add(obj)
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
      - email     → email all created inquiries
      - call      → trigger ElevenLabs voice agent for all created inquiries
      - test_call → dial `test_call_to_number` once using the first
                    created inquiry's context (manufacturers are NOT contacted)
      - none      → leave as drafts
    """
    if not payload.targets:
        raise HTTPException(status_code=422, detail="At least one target is required")

    # Resolve dispatch channel — keep the legacy `send_email` field working.
    channel = (payload.dispatch_channel or "email").strip().lower()
    if payload.send_email is False and channel == "email":
        channel = "none"
    if channel not in ("email", "call", "test_call", "none"):
        raise HTTPException(
            status_code=422,
            detail=f"Unknown dispatch_channel '{channel}'. Use email|call|test_call|none.",
        )
    if channel == "test_call" and not (payload.test_call_to_number or "").strip():
        raise HTTPException(
            status_code=422,
            detail="test_call_to_number is required when dispatch_channel='test_call'",
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
            fallback_after_hours=payload.fallback_after_hours,
            source_inquiry_uuid=payload.source_inquiry_uuid,
            source_excel_url=payload.source_excel_url,
            source_excel_sheet=payload.source_excel_sheet,
            source_excel_row=tgt.source_excel_row,
            medication_name=tgt.medication_name or None,
            pi_storage_data=tgt.pi_storage_data or None,
            pi_link=tgt.pi_link or None,
            status="draft",
            user_id=current_user.id,
        )
        db.add(obj)
        db.flush()
        created_objs.append(obj)

    db.commit()

    dispatched = 0
    test_call_inquiry_id: Optional[int] = None
    test_call_to: Optional[str] = None

    if channel == "email":
        # Group inquiries by destination email so we send ONE email per unique
        # recipient. Multiple Excel rows can resolve to the same manufacturer
        # (e.g. 25 MUE rows all directed at Fresenius Kabi) — without this
        # dedup we'd fire 25 identical emails. Every sibling inquiry in a
        # group still gets email_sent_at stamped so the response-writeback
        # path (which fans out over source_inquiry_uuid siblings) updates
        # every Excel row when the reply lands.
        groups: dict[str, list[Inquiry]] = {}
        for obj in list(created_objs):
            # Idempotency: if this inquiry was already dispatched (e.g. client
            # retry after a network blip), skip it.
            if obj.email_sent_at is not None:
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
            groups.setdefault(to_email.strip().lower(), []).append(obj)

        for to_email_key, siblings in groups.items():
            primary = siblings[0]
            mfr = db.get(ManufacturerContact, primary.manufacturer_id)
            # Use the un-lowercased address from the manufacturer record for the
            # actual send (SMTP is case-insensitive but users may care).
            to_email = mfr.official_mi_email or mfr.team_verified_email
            try:
                message_id = email_service.send_inquiry_email(
                    inquiry_id=primary.id,
                    manufacturer_name=mfr.manufacturer,
                    to_email=to_email,
                    subject=primary.subject,
                    question=primary.question,
                    requester_name=primary.requester_name,
                    requester_email=primary.requester_email,
                    medication_name=primary.medication_name,
                    pi_storage_data=primary.pi_storage_data,
                    pi_link=primary.pi_link,
                )
            except Exception as e:
                for sib in siblings:
                    failed.append({
                        "manufacturer_id": sib.manufacturer_id,
                        "error": f"Email send failed: {e}",
                    })
                continue
            now = _now()
            for sib in siblings:
                sib.status = "email_sent"
                sib.email_sent_at = now
                sib.email_message_id = message_id
                sib.call_scheduled_for = now + timedelta(hours=sib.fallback_after_hours)
            dispatched += 1  # unique emails sent, not inquiries stamped
        db.commit()

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
            dispatched += 1  # unique calls placed, not inquiries stamped
        db.commit()

    elif channel == "test_call":
        # Dial the user's own number using ONE inquiry's context. No manufacturer
        # is contacted; status of every created inquiry stays "draft" so the
        # user can dispatch them for real later from the Outreach tab.
        first = created_objs[0] if created_objs else None
        if first is not None:
            mfr = db.get(ManufacturerContact, first.manufacturer_id)
            if not mfr:
                failed.append({
                    "manufacturer_id": first.manufacturer_id,
                    "error": "Manufacturer disappeared after create",
                })
            else:
                try:
                    await call_service.place_inquiry_call(
                        inquiry_id=first.id,
                        to_number=payload.test_call_to_number,
                        manufacturer_name=mfr.manufacturer,
                        subject=first.subject,
                        question=first.question,
                        requester_name=first.requester_name,
                        requester_email=first.requester_email,
                        is_test=True,
                    )
                    test_call_inquiry_id = first.id
                    test_call_to = payload.test_call_to_number
                    dispatched = 1
                except Exception as e:
                    failed.append({
                        "manufacturer_id": first.manufacturer_id,
                        "error": f"Test call failed: {e}",
                    })

    refreshed = [_get_or_404(db, obj.id, current_user) for obj in created_objs]
    return BulkInquiryResult(
        created=refreshed,
        failed=failed,
        dispatch_channel=channel,
        dispatched=dispatched,
        test_call_inquiry_id=test_call_inquiry_id,
        test_call_to=test_call_to,
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
    """Send the inquiry via SendGrid to the manufacturer's MI email and schedule
    the fallback call window. Replies come back to our mailbox and are captured
    automatically by the IMAP poller."""
    obj = _get_or_404(db, inquiry_id, current_user)
    if obj.status not in ("draft", "email_sent", "failed"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot send email from status '{obj.status}'",
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

    try:
        message_id = email_service.send_inquiry_email(
            inquiry_id=obj.id,
            manufacturer_name=mfr.manufacturer,
            to_email=to_email,
            subject=obj.subject,
            question=obj.question,
            requester_name=obj.requester_name,
            requester_email=obj.requester_email,
            medication_name=obj.medication_name,
            pi_storage_data=obj.pi_storage_data,
        )
    except email_service.EmailConfigError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Email send failed: {e}")

    now = _now()
    obj.status = "email_sent"
    obj.email_sent_at = now
    obj.email_message_id = message_id
    obj.call_scheduled_for = now + timedelta(hours=obj.fallback_after_hours)
    db.commit()
    return _get_or_404(db, inquiry_id, current_user)


@router.post("/{inquiry_id}/record-email-response", response_model=InquiryOut)
def record_email_response(
    inquiry_id: int,
    payload: EmailResponsePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    obj = _get_or_404(db, inquiry_id, current_user)
    obj.status = "email_responded"
    obj.email_response = payload.response
    obj.email_response_at = _now()
    obj.final_answer = payload.response
    db.commit()
    legacy_response_service.maybe_post_for_inquiry(db, obj)
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
    hours_text = obj.manufacturer.mi_phone_hours if hasattr(obj.manufacturer, "mi_phone_hours") else None
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
    force: bool = Query(False, description="Place the call even if outside business hours"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Place an outbound ElevenLabs call to the manufacturer with this
    inquiry's context. Updates the inquiry's status, scheduled time, and
    stores the ElevenLabs `conversation_id` for the post-call webhook."""
    obj = _get_or_404(db, inquiry_id, current_user)
    if obj.status in ("email_responded", "closed"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot trigger call from status '{obj.status}'",
        )
    if obj.status == "call_pending":
        raise HTTPException(
            status_code=409,
            detail="A call is already in progress for this inquiry. Wait for it to complete before placing another.",
        )
    # call_completed (with non-answered outcome) and needs_attention are valid retry sources
    if obj.status == "call_completed" and obj.call_provider_status == "answered":
        raise HTTPException(
            status_code=409,
            detail="This inquiry already has an answer. Reopen it before retrying.",
        )

    mfr = db.get(ManufacturerContact, obj.manufacturer_id)
    if not mfr:
        raise HTTPException(status_code=400, detail="Manufacturer missing")
    if not mfr.mi_phone:
        raise HTTPException(
            status_code=400,
            detail=f"{mfr.manufacturer} has no MI phone number on file",
        )

    # Business-hours guard (skippable via ?force=true)
    in_hours = call_service.is_within_business_hours(mfr.mi_phone_hours)
    if in_hours is False and not force:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "out_of_hours",
                "message": f"{mfr.manufacturer} is outside business hours ({mfr.mi_phone_hours}). "
                           "Retry with ?force=true to call anyway.",
                "hours": mfr.mi_phone_hours,
            },
        )

    try:
        resp = await call_service.place_inquiry_call(
            inquiry_id=obj.id,
            to_number=mfr.mi_phone,
            manufacturer_name=mfr.manufacturer,
            subject=obj.subject,
            question=obj.question,
            requester_name=obj.requester_name,
            requester_email=obj.requester_email,
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

    obj.status = "call_pending"
    obj.call_scheduled_for = _now()
    obj.call_conversation_id = (
        resp.get("conversation_id") or resp.get("conversationId")
    )
    obj.call_provider_status = resp.get("status") or "initiated"
    obj.next_retry_at = None  # manual trigger cancels any pending auto-retry
    db.commit()
    return _get_or_404(db, inquiry_id, current_user)


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
    legacy_response_service.maybe_post_for_inquiry(db, obj)
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
    obj = _get_or_404(db, inquiry_id, current_user)
    obj.status = "call_completed"
    obj.call_completed_at = _now()
    if payload.transcript is not None:
        obj.call_transcript = payload.transcript
    if payload.summary is not None:
        obj.call_summary = payload.summary
        obj.final_answer = payload.summary
    db.commit()
    legacy_response_service.maybe_post_for_inquiry(db, obj)
    return _get_or_404(db, inquiry_id, current_user)


@router.post("/{inquiry_id}/close", response_model=InquiryOut)
def close_inquiry(
    inquiry_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    obj = _get_or_404(db, inquiry_id, current_user)
    obj.status = "closed"
    db.commit()
    return _get_or_404(db, inquiry_id, current_user)


@router.post("/{inquiry_id}/reprocess-pdf", response_model=InquiryOut)
def reprocess_pdf(
    inquiry_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Backfill: re-search the InpharmD mailbox for the original reply that
    matches this inquiry's subject tag, pull all supported document attachments,
    upload + summarize each one, and replace the stored attachment rows.

    If no attachments can be uploaded (e.g. S3 outage), the operation is fully
    rolled back — original attachment rows and scalar fields are preserved."""
    import os
    import httpx
    import graph_service
    import s3_service
    import summary_service

    obj = _get_or_404(db, inquiry_id, current_user)

    if not graph_service.is_configured():
        raise HTTPException(status_code=503, detail="Graph API not configured")

    mailbox = os.getenv("GRAPH_MAILBOX") or os.getenv("EMAIL_FROM", "druginfo@inpharmd.com")
    token = graph_service._get_token()
    headers = {"Authorization": f"Bearer {token}"}
    tag = f"[InpharmD #{inquiry_id}]"

    search_url = f"https://graph.microsoft.com/v1.0/users/{mailbox}/messages"
    with httpx.Client(timeout=20) as client:
        r = client.get(
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

    docs = graph_service._fetch_all_document_attachments(token, mailbox, target["id"])
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
        url = s3_service.upload_bytes(
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
            text = summary_service.extract_document_text(doc["name"], doc["bytes"])
            if text:
                try:
                    att_summary = summary_service.summarize_pdf(
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
