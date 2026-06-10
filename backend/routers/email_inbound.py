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

import logging
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Form, Request, Response

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


@router.post("/inbound")
async def sendgrid_inbound(request: Request) -> Response:
    """Receive a SendGrid Inbound Parse POST and store the manufacturer's reply."""
    form = await request.form()

    subject: str = form.get("subject", "") or ""
    sender: str = form.get("from", "") or ""
    text_body: str = form.get("text", "") or ""
    html_body: str = form.get("html", "") or ""

    # Pick best body: plain text preferred, fall back to HTML stripped
    body = text_body.strip() if text_body.strip() else _strip_html(html_body)

    m = _SUBJECT_TAG.search(subject)
    if not m:
        log.info("Inbound email from %s has no InpharmD tag in subject; ignoring", sender)
        return Response(status_code=200)

    inquiry_id = int(m.group(1))
    reply = _strip_quoted(body)

    if not reply:
        log.info("Inbound reply for inquiry %s had no extractable body; ignoring", inquiry_id)
        return Response(status_code=200)

    db = SessionLocal()
    try:
        # Import here to avoid circular imports at module load
        from models import Inquiry

        obj = db.get(Inquiry, inquiry_id)
        if not obj:
            log.warning("Inbound reply tagged inquiry %s but no such record", inquiry_id)
            return Response(status_code=200)

        if obj.status == "closed":
            return Response(status_code=200)

        if obj.email_response:
            log.info("Inquiry %s already has a reply; skipping duplicate", inquiry_id)
            return Response(status_code=200)

        obj.email_response = reply
        obj.email_response_at = datetime.now(timezone.utc)
        obj.status = "email_responded"
        obj.next_retry_at = None
        obj.call_scheduled_for = None

        final = reply
        if summary_service.is_configured():
            try:
                mfr_name = obj.manufacturer.manufacturer if obj.manufacturer else "the manufacturer"
                final = summary_service.extract_answer_from_email(
                    question=obj.question,
                    manufacturer=mfr_name,
                    reply_text=reply,
                )
            except summary_service.SummaryConfigError:
                final = reply

        obj.final_answer = final
        db.commit()
        log.info("Stored email reply for inquiry %s from %s", inquiry_id, sender)

        # If this inquiry was forwarded from InpharmD, POST the reply back.
        legacy_response_service.maybe_post_for_inquiry(db, obj)

    except Exception:
        log.exception("Failed to process inbound email for inquiry %s", inquiry_id)
        db.rollback()
    finally:
        db.close()

    return Response(status_code=200)
