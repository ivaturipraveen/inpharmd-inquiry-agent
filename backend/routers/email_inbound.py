"""SendGrid Inbound Parse webhook — receives manufacturer email replies.

When a manufacturer replies to druginfo@inpharmd.com, SendGrid catches it and
POSTs the parsed email here as multipart/form-data.  We extract the inquiry ID
from the subject tag [InpharmD #N], strip quoted text, run GPT cleanup, and
store the answer so it appears on the dashboard immediately.

Setup (one-time in SendGrid):
    Mail Settings → Inbound Parse → Add Host & URL
    MX host : inpharmd.com  (or the sub-domain you point MX records at)
    URL     : https://inpharmd-inquiry-api.onrender.com/api/email/inbound
    Check "POST the raw, full MIME message" = OFF (default form-data is fine)
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request, Response
from sqlalchemy.exc import IntegrityError
from starlette.datastructures import UploadFile

import inbound_attachment_service
import legacy_response_service
import summary_service
from database import SessionLocal

log = logging.getLogger("inquiry.email_inbound")
router = APIRouter(prefix="/api/email", tags=["email"])

_SUBJECT_TAG = re.compile(r"\[InpharmD #(\d+)\]", re.IGNORECASE)

# Patterns that mark where the quoted original message starts — cut everything below.
_QUOTE_MARKERS = (
    re.compile(r"^On .+wrote:$", re.IGNORECASE | re.DOTALL),
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}", re.IGNORECASE),
    re.compile(r"^_{5,}$"),
    re.compile(r"^From:\s", re.IGNORECASE),
    re.compile(r"^Sent from my ", re.IGNORECASE),
    re.compile(r"^\[InpharmD #\d+\]", re.IGNORECASE),
)


def _strip_quoted(text: str) -> str:
    lines = text.replace("\r\n", "\n").split("\n")
    kept = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(">"):
            break
        if any(m.match(stripped) for m in _QUOTE_MARKERS):
            break
        kept.append(line)
    cleaned = "\n".join(kept).strip()
    return cleaned if cleaned else text.strip()


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _parse_smtp_message_id(raw_headers: str) -> Optional[str]:
    """Extract the RFC 2822 Message-ID value from the raw SMTP headers string
    that SendGrid includes as form["headers"]."""
    for line in (raw_headers or "").splitlines():
        if line.lower().startswith("message-id:"):
            value = line.split(":", 1)[1].strip()
            return value if value else None
    return None


async def _collect_sendgrid_attachments(form) -> list[dict]:
    """Extract document attachments from a SendGrid Inbound Parse form.

    SendGrid delivers attachments as:
        attachments       — count (string)
        attachment-info   — JSON: {"attachment1": {"filename": "...", "type": "..."}, ...}
        attachment1..N    — binary UploadFile fields

    Returns a list of {bytes, name, content_type} for each attachment present.
    """
    n = int(form.get("attachments") or "0")
    if n == 0:
        return []

    att_info: dict = {}
    raw_info = form.get("attachment-info")
    if raw_info:
        try:
            att_info = json.loads(raw_info)
        except Exception:
            log.warning("Could not parse attachment-info JSON: %r", raw_info[:200])

    results = []
    cap = min(n, inbound_attachment_service.MAX_ATTACHMENTS)
    for i in range(1, cap + 1):
        key = f"attachment{i}"
        upload = form.get(key)
        if not isinstance(upload, UploadFile):
            continue
        meta = att_info.get(key, {})
        filename = (meta.get("filename") or meta.get("name") or key).replace("\x00", "").strip()[:512]
        content_type = meta.get("type") or upload.content_type or "application/octet-stream"
        content = await upload.read()
        if content:
            results.append({"bytes": content, "name": filename, "content_type": content_type})
    return results


@router.post("/inbound")
async def sendgrid_inbound(request: Request) -> Response:
    """Receive a SendGrid Inbound Parse POST and store the manufacturer's reply."""
    form = await request.form()

    subject: str = form.get("subject", "") or ""
    sender: str = form.get("from", "") or ""
    text_body: str = form.get("text", "") or ""
    html_body: str = form.get("html", "") or ""
    raw_headers: str = form.get("headers", "") or ""
    smtp_message_id: Optional[str] = _parse_smtp_message_id(raw_headers)
    log.info(
        "pipeline: inbound webhook hit from=%s subject=%r text_len=%d html_len=%d smtp_mid=%s",
        sender, subject[:120], len(text_body), len(html_body), bool(smtp_message_id),
    )

    # Pick best body: plain text preferred, fall back to HTML stripped
    body = text_body.strip() if text_body.strip() else _strip_html(html_body)

    m = _SUBJECT_TAG.search(subject)
    if not m:
        log.info("Inbound email from %s has no InpharmD tag in subject; ignoring", sender)
        return Response(status_code=200)

    inquiry_id = int(m.group(1))
    reply = _strip_quoted(body)
    raw_atts = await _collect_sendgrid_attachments(form)
    log.info(
        "pipeline: inbound parsed tag inquiry=%s body_chars=%d reply_chars=%d "
        "sender=%s attachments=%d",
        inquiry_id, len(body or ""), len(reply or ""), sender, len(raw_atts),
    )

    if not reply and not raw_atts:
        log.info("Inbound reply for inquiry %s had no body and no attachments; ignoring", inquiry_id)
        return Response(status_code=200)

    db = SessionLocal()
    try:
        # Import here to avoid circular imports at module load
        from models import EmailReply, Inquiry

        obj = db.query(Inquiry).filter(Inquiry.id == inquiry_id).with_for_update().first()
        if not obj:
            log.warning("Inbound reply tagged inquiry %s but no such record", inquiry_id)
            return Response(status_code=200)

        if obj.status == "closed":
            log.info("pipeline: inbound skip inquiry=%s already closed", inquiry_id)
            return Response(status_code=200)

        # Cross-path dedup: if Graph or IMAP already processed this exact email,
        # skip before doing any S3 or GPT work.
        if smtp_message_id and db.query(EmailReply).filter(
            EmailReply.inquiry_id == inquiry_id,
            EmailReply.smtp_message_id == smtp_message_id,
        ).first():
            log.info(
                "pipeline: inbound skip inquiry=%s — smtp_message_id already processed by another path",
                inquiry_id,
            )
            return Response(status_code=200)

        # Coarse dedup for emails with no Message-ID header (shouldn't happen in
        # practice, but protects against duplicate webhook deliveries).
        if not smtp_message_id and obj.email_response:
            log.info(
                "pipeline: inbound skip inquiry=%s duplicate (email_response already set, no smtp_mid)",
                inquiry_id,
            )
            return Response(status_code=200)

        mfr_name = obj.manufacturer.manufacturer if obj.manufacturer else "the manufacturer"

        final = reply
        if reply and summary_service.is_configured():
            try:
                final = summary_service.extract_answer_from_email(
                    question=obj.question,
                    manufacturer=mfr_name,
                    reply_text=reply,
                )
            except summary_service.SummaryConfigError:
                final = reply

        # Create the EmailReply row; flush to get its id before linking attachments.
        email_reply = EmailReply(
            inquiry_id=inquiry_id,
            direction="inbound",
            sender_email=sender,
            body=reply or "",
            sent_at=datetime.now(timezone.utc),
            smtp_message_id=smtp_message_id,
        )
        db.add(email_reply)
        db.flush()

        uploaded_atts = inbound_attachment_service.process_attachments(
            db=db,
            inquiry_id=inquiry_id,
            reply_id=email_reply.id,
            raw_attachments=raw_atts,
            question=obj.question,
            manufacturer_name=mfr_name,
        )

        first_att = uploaded_atts[0] if uploaded_atts else {}
        pdf_url: Optional[str] = first_att.get("url")
        pdf_filename: Optional[str] = first_att.get("filename")
        pdf_summary: Optional[str] = first_att.get("summary")

        # Use attachment summary as body/final_answer fallback when there was no text.
        if not email_reply.body and pdf_summary:
            email_reply.body = pdf_summary
        if not final and pdf_summary:
            final = pdf_summary

        # Only update inquiry scalar fields on the first reply.
        is_first_reply = not obj.email_response
        if is_first_reply:
            obj.email_response = reply or pdf_summary or ""
            obj.email_response_at = email_reply.sent_at
            obj.status = "email_responded"
            obj.next_retry_at = None
            obj.call_scheduled_for = None
            obj.final_answer = final or pdf_summary or ""
            if pdf_url:
                obj.pdf_url = pdf_url
                obj.pdf_filename = pdf_filename
                obj.pdf_summary = pdf_summary

        db.commit()
        log.info("Stored email reply for inquiry %s from %s", inquiry_id, sender)
        log.info(
            "pipeline: inbound stored email_response inquiry=%s status=%s "
            "final_answer_chars=%d pdf_attached=%s is_first=%s",
            inquiry_id, obj.status, len(final or ""), bool(pdf_url), is_first_reply,
        )

        # If this inquiry was forwarded from InpharmD, POST the reply back.
        legacy_response_service.maybe_post_for_inquiry(db, obj)

        # Slack — same as the Graph poll path.
        try:
            import slack_service
            if slack_service.is_configured():
                log.info(
                    "pipeline: slack notify_reply firing for inquiry %s (via SendGrid webhook)",
                    inquiry_id,
                )
                slack_service.notify_reply(
                    inquiry_id=inquiry_id,
                    manufacturer=mfr_name,
                    subject=obj.subject,
                    question=obj.question,
                    answer=obj.final_answer or "(See attached PDF.)",
                    requester_name=obj.requester_name,
                    requester_email=obj.requester_email,
                    sender_email=sender,
                )
            else:
                log.info(
                    "pipeline: slack notify SKIPPED for inquiry %s: SLACK_WEBHOOK_URL not configured",
                    inquiry_id,
                )
        except Exception:
            log.exception("pipeline: slack notify FAILED for inquiry %s", inquiry_id)

        log.info(
            "pipeline: COMPLETE inquiry=%s path=sendgrid_webhook "
            "legacy_posted=%s sheet_posted=%s",
            inquiry_id,
            "yes" if obj.legacy_response_posted_at is not None else "no",
            "yes" if obj.excel_response_posted_at is not None else "no",
        )

    except IntegrityError:
        # Expected outcome when two paths race on the same email — the unique
        # index on (inquiry_id, smtp_message_id) rejects the duplicate INSERT.
        # Roll back and return 200 so SendGrid doesn't retry.
        db.rollback()
        log.info(
            "pipeline: inbound dedup inquiry=%s — concurrent delivery rejected by unique constraint",
            inquiry_id,
        )
    except Exception:
        log.exception("Failed to process inbound email for inquiry %s", inquiry_id)
        db.rollback()
    finally:
        db.close()

    return Response(status_code=200)
