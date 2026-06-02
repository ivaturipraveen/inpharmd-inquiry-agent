from datetime import datetime, timedelta, timezone
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

import call_service
import email_service
import summary_service
from database import get_db
from models import Inquiry, ManufacturerContact
from schemas import (
    CallResultPayload,
    EmailResponsePayload,
    InquiryCreate,
    InquiryOut,
    InquiryUpdate,
)


class TestCallPayload(BaseModel):
    phone_number: str = Field(..., min_length=7, description="Number to dial in E.164 format, e.g. +17705551234")

router = APIRouter(prefix="/api/inquiries", tags=["inquiries"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_or_404(db: Session, inquiry_id: int) -> Inquiry:
    obj = (
        db.query(Inquiry)
        .options(joinedload(Inquiry.manufacturer))
        .filter(Inquiry.id == inquiry_id)
        .first()
    )
    if not obj:
        raise HTTPException(status_code=404, detail="Inquiry not found")
    return obj


@router.get("", response_model=List[InquiryOut])
def list_inquiries(
    status: Optional[str] = Query(None),
    manufacturer_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(Inquiry).options(joinedload(Inquiry.manufacturer))
    if status:
        q = q.filter(Inquiry.status == status)
    if manufacturer_id:
        q = q.filter(Inquiry.manufacturer_id == manufacturer_id)
    return q.order_by(Inquiry.created_at.desc()).all()


@router.get("/{inquiry_id}", response_model=InquiryOut)
def get_inquiry(inquiry_id: int, db: Session = Depends(get_db)):
    return _get_or_404(db, inquiry_id)


DEFAULT_REQUESTER_NAME = "Leah"
DEFAULT_REQUESTER_EMAIL = "druginfo@inpharmd.com"


@router.post("", response_model=InquiryOut, status_code=201)
def create_inquiry(payload: InquiryCreate, db: Session = Depends(get_db)):
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
    obj = Inquiry(**data, status="draft")
    db.add(obj)
    db.commit()
    return _get_or_404(db, obj.id)


@router.put("/{inquiry_id}", response_model=InquiryOut)
def update_inquiry(
    inquiry_id: int, payload: InquiryUpdate, db: Session = Depends(get_db)
):
    obj = _get_or_404(db, inquiry_id)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    db.commit()
    return _get_or_404(db, inquiry_id)


@router.delete("/{inquiry_id}", status_code=204)
def delete_inquiry(inquiry_id: int, db: Session = Depends(get_db)):
    obj = _get_or_404(db, inquiry_id)
    db.delete(obj)
    db.commit()
    return None


# ---------- Lifecycle transitions ----------

@router.post("/{inquiry_id}/send-email", response_model=InquiryOut)
def send_email(inquiry_id: int, db: Session = Depends(get_db)):
    """Send the inquiry via SendGrid to the manufacturer's MI email and schedule
    the fallback call window. Replies come back to our mailbox and are captured
    automatically by the IMAP poller."""
    obj = _get_or_404(db, inquiry_id)
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
    return _get_or_404(db, inquiry_id)


@router.post("/{inquiry_id}/record-email-response", response_model=InquiryOut)
def record_email_response(
    inquiry_id: int,
    payload: EmailResponsePayload,
    db: Session = Depends(get_db),
):
    obj = _get_or_404(db, inquiry_id)
    obj.status = "email_responded"
    obj.email_response = payload.response
    obj.email_response_at = _now()
    obj.final_answer = payload.response
    db.commit()
    return _get_or_404(db, inquiry_id)


@router.post("/{inquiry_id}/business-hours")
def business_hours_check(inquiry_id: int, db: Session = Depends(get_db)):
    """Returns whether the manufacturer is in business hours right now,
    based on the parsed `mi_phone_hours` text. Used by the UI to warn
    before placing a call."""
    obj = _get_or_404(db, inquiry_id)
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
):
    """Place an outbound ElevenLabs call to the manufacturer with this
    inquiry's context. Updates the inquiry's status, scheduled time, and
    stores the ElevenLabs `conversation_id` for the post-call webhook."""
    obj = _get_or_404(db, inquiry_id)
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
    return _get_or_404(db, inquiry_id)


@router.post("/{inquiry_id}/test-call", response_model=InquiryOut)
async def test_call(
    inquiry_id: int,
    payload: TestCallPayload,
    db: Session = Depends(get_db),
):
    """Dial an arbitrary phone number using THIS inquiry's question/manufacturer
    context. Lets the team test how the agent would speak to a real MI desk
    without bothering the manufacturer. Does not change inquiry status."""
    obj = _get_or_404(db, inquiry_id)
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
    return _get_or_404(db, inquiry_id)


@router.post("/{inquiry_id}/extract-answer", response_model=InquiryOut)
def extract_answer(inquiry_id: int, db: Session = Depends(get_db)):
    """Manually trigger LLM extraction of a clean answer from the call transcript.
    Useful when the agent's submit_answer didn't fire and you want the AI to
    summarize what was said."""
    obj = _get_or_404(db, inquiry_id)
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
    return _get_or_404(db, inquiry_id)


@router.post("/{inquiry_id}/reset-retries", response_model=InquiryOut)
def reset_retries(inquiry_id: int, db: Session = Depends(get_db)):
    """Manual override — clear the retry counter so this inquiry can auto-retry
    again. Useful when a user manually edits the inquiry and wants a fresh chance."""
    obj = _get_or_404(db, inquiry_id)
    obj.retry_count = 0
    obj.next_retry_at = None
    if obj.status == "needs_attention":
        obj.status = "draft"
    db.commit()
    return _get_or_404(db, inquiry_id)


@router.post("/{inquiry_id}/record-call-result", response_model=InquiryOut)
def record_call_result(
    inquiry_id: int,
    payload: CallResultPayload,
    db: Session = Depends(get_db),
):
    """Manual entry point: lets a human (or another integration) attach the
    call result without going through the ElevenLabs webhook."""
    obj = _get_or_404(db, inquiry_id)
    obj.status = "call_completed"
    obj.call_completed_at = _now()
    if payload.transcript is not None:
        obj.call_transcript = payload.transcript
    if payload.summary is not None:
        obj.call_summary = payload.summary
        obj.final_answer = payload.summary
    db.commit()
    return _get_or_404(db, inquiry_id)


@router.post("/{inquiry_id}/close", response_model=InquiryOut)
def close_inquiry(inquiry_id: int, db: Session = Depends(get_db)):
    obj = _get_or_404(db, inquiry_id)
    obj.status = "closed"
    db.commit()
    return _get_or_404(db, inquiry_id)


@router.post("/{inquiry_id}/reprocess-pdf", response_model=InquiryOut)
def reprocess_pdf(inquiry_id: int, db: Session = Depends(get_db)):
    """Backfill: re-search the InpharmD mailbox for the original reply that
    matches this inquiry's subject tag, pull the first PDF attachment, upload
    + summarize it, and patch the inquiry. Use this if the email was already
    processed but the PDF capture failed (e.g. earlier bug, S3 misconfig)."""
    import os, httpx, base64
    import graph_service
    import s3_service
    import summary_service

    obj = _get_or_404(db, inquiry_id)

    if not graph_service.is_configured():
        raise HTTPException(status_code=503, detail="Graph API not configured")

    mailbox = os.getenv("GRAPH_MAILBOX") or os.getenv("EMAIL_FROM", "druginfo@inpharmd.com")
    token = graph_service._get_token()
    headers = {"Authorization": f"Bearer {token}"}
    tag = f"[InpharmD #{inquiry_id}]"

    # Find any message whose subject contains the tag. Don't filter by isRead;
    # we want messages we've already marked read too. Use params= so httpx
    # URL-encodes the search value (# is a URL fragment separator).
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
            raise HTTPException(status_code=502, detail=f"Graph search failed: {r.status_code} {r.text[:200]}")
        msgs = r.json().get("value", [])

    target = next((m for m in msgs if m.get("hasAttachments")), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"No message with attachments found for {tag}")

    pdf = graph_service._fetch_pdf_attachment(token, mailbox, target["id"])
    if not pdf:
        raise HTTPException(status_code=404, detail="Message has attachments but no PDF")

    obj.pdf_filename = pdf["name"]
    obj.pdf_url = s3_service.upload_pdf(
        pdf["bytes"], original_name=pdf["name"], inquiry_id=inquiry_id
    )
    if summary_service.is_configured():
        text = summary_service.extract_pdf_text(pdf["bytes"])
        if text:
            try:
                obj.pdf_summary = summary_service.summarize_pdf(
                    question=obj.question,
                    manufacturer=obj.manufacturer.manufacturer if obj.manufacturer else "the manufacturer",
                    pdf_text=text,
                )
            except Exception:
                pass

    db.commit()
    return _get_or_404(db, inquiry_id)
