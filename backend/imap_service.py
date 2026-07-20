"""IMAP poller that pulls manufacturer email replies into inquiries.

Sends go out via SendGrid (see email_service.py) from EMAIL_FROM, so replies land
back in that same mailbox. This module logs into that mailbox over IMAP, finds
unseen messages whose subject carries the [InpharmD #N] tag, extracts the reply
text, runs an LLM cleanup, and writes the answer onto the matching inquiry so it
shows up on the dashboard.

Env vars:
    IMAP_HOST        e.g. imap.gmail.com (default)
    IMAP_PORT        993 (default, implicit SSL)
    IMAP_USERNAME    the mailbox to read, e.g. druginfo@inpharmd.com
    IMAP_PASSWORD    app password for that mailbox
    IMAP_MAILBOX     folder to read (default INBOX)
"""
from __future__ import annotations

import email
import imaplib
import logging
import os
import re
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.message import Message
from typing import Optional

import legacy_response_service
import s3_service
import summary_service
from database import SessionLocal
from models import Inquiry, InquiryAttachment

log = logging.getLogger("inquiry.imap")

_SUBJECT_TAG = re.compile(r"\[InpharmD #(\d+)\]", re.IGNORECASE)

# Markers where quoted/original text begins in a reply — we cut everything below.
_QUOTE_MARKERS = (
    re.compile(r"^On .+wrote:$", re.IGNORECASE),
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}", re.IGNORECASE),
    re.compile(r"^_{5,}$"),
    re.compile(r"^From:\s.+", re.IGNORECASE),
    re.compile(r"^Sent from my ", re.IGNORECASE),
    re.compile(r"^\[InpharmD #\d+\]", re.IGNORECASE),
)


_IMAP_SUPPORTED_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv"}
_IMAP_CONTENT_TYPE_FOR_EXT = {
    ".pdf":  "application/pdf",
    ".doc":  "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls":  "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv":  "text/csv",
}
# Include application/csv — a non-standard but common alternative MIME type for CSV.
# Matches graph_service which also accepts it. Without this, CSV files sent with
# Content-Type: application/csv (and no file extension) would be silently dropped.
_IMAP_SUPPORTED_CONTENT_TYPES = set(_IMAP_CONTENT_TYPE_FOR_EXT.values()) | {"application/csv"}

# Mirror the same cap used by graph_service so both paths behave identically.
_MAX_ATTACHMENTS_PER_EMAIL = 20


def is_configured() -> bool:
    return bool(os.getenv("IMAP_USERNAME") and os.getenv("IMAP_PASSWORD"))


def _decode(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _get_plain_text(msg: Message) -> str:
    """Return the best plain-text body from an email message."""
    if msg.is_multipart():
        # Prefer text/plain, fall back to text/html (crudely stripped)
        plain = None
        html = None
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            if "attachment" in disp.lower():
                continue
            try:
                payload = part.get_payload(decode=True)
            except Exception:
                continue
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                text = payload.decode(charset, errors="replace")
            except (LookupError, TypeError):
                text = payload.decode("utf-8", errors="replace")
            if ctype == "text/plain" and plain is None:
                plain = text
            elif ctype == "text/html" and html is None:
                html = text
        if plain is not None:
            return plain
        if html is not None:
            return _strip_html(html)
        return ""
    else:
        payload = msg.get_payload(decode=True)
        if payload is None:
            return ""
        charset = msg.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except (LookupError, TypeError):
            text = payload.decode("utf-8", errors="replace")
        if msg.get_content_type() == "text/html":
            return _strip_html(text)
        return text


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _strip_quoted(text: str) -> str:
    """Drop quoted original message + signature so only the new reply remains."""
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
    # If stripping removed everything useful, fall back to the raw text.
    return cleaned if cleaned else text.strip()


def _collect_attachments(msg: email.message.Message) -> list[dict]:
    """Extract all supported document attachments from an email Message.

    Returns a list of {'name': str, 'bytes': bytes, 'content_type': str}
    in the order they appear in the message.
    """
    results = []
    if not msg.is_multipart():
        return results
    for part in msg.walk():
        if len(results) >= _MAX_ATTACHMENTS_PER_EMAIL:
            log.warning("Email has >%d supported attachments; truncating", _MAX_ATTACHMENTS_PER_EMAIL)
            break
        disp = str(part.get("Content-Disposition") or "")
        if "attachment" not in disp.lower():
            continue
        filename = (part.get_filename() or "").replace("\x00", "").strip()[:512]
        ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        ctype = (part.get_content_type() or "").lower()
        if ext not in _IMAP_SUPPORTED_EXTENSIONS and not any(
            ctype.startswith(k) for k in _IMAP_SUPPORTED_CONTENT_TYPES
        ):
            continue
        try:
            payload = part.get_payload(decode=True)
        except Exception:
            continue
        if not payload:
            continue
        content_type = _IMAP_CONTENT_TYPE_FOR_EXT.get(ext, ctype or "application/octet-stream")
        results.append({"name": filename or "attachment", "bytes": payload, "content_type": content_type})
    return results


def _process_message(db, raw_bytes: bytes) -> Optional[int]:
    """Parse one raw email; if it matches an inquiry, store the reply. Returns
    the inquiry id if matched/processed, else None."""
    msg = email.message_from_bytes(raw_bytes)
    subject = _decode(msg.get("Subject"))
    m = _SUBJECT_TAG.search(subject or "")
    if not m:
        return None

    inquiry_id = int(m.group(1))
    obj = db.query(Inquiry).filter(Inquiry.id == inquiry_id).with_for_update().first()
    if not obj:
        log.info("Reply tagged inquiry %s but no such inquiry; skipping", inquiry_id)
        return None
    if obj.status == "closed":
        return None
    if obj.email_response:
        # Already captured a reply for this inquiry — don't reprocess.
        return inquiry_id

    sender = _decode(msg.get("From"))
    body = _get_plain_text(msg)
    reply = _strip_quoted(body)
    if not reply:
        log.info("Inquiry %s reply had no extractable body; skipping", inquiry_id)
        return inquiry_id

    obj.email_response = reply
    obj.email_response_at = datetime.now(timezone.utc)
    obj.status = "email_responded"
    # A real reply means we no longer need the fallback call / pending retries.
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
            final = reply  # soft-fail to raw reply
    obj.final_answer = final

    # Upload any document attachments and write InquiryAttachment rows.
    attachments = _collect_attachments(msg)
    uploaded_count = 0
    for order, att in enumerate(attachments):
        url = s3_service.upload_bytes(
            att["bytes"],
            original_name=att["name"],
            inquiry_id=inquiry_id,
            content_type=att["content_type"],
        )
        if url is None:
            log.warning(
                "S3 upload returned None for inquiry %s attachment %d '%s'; skipping",
                inquiry_id, order, att["name"],
            )
            continue
        uploaded_count += 1
        att_summary = None
        if summary_service.is_configured():
            att_text = summary_service.extract_document_text(att["name"], att["bytes"])
            if att_text:
                try:
                    att_summary = summary_service.summarize_pdf(
                        question=obj.question,
                        manufacturer=obj.manufacturer.manufacturer if obj.manufacturer else "the manufacturer",
                        pdf_text=att_text,
                    )
                except Exception as e:
                    log.warning(
                        "Attachment summary unavailable for inquiry %s attachment %d: %s",
                        inquiry_id, order, e,
                    )
        log.info(
            "Inquiry %s attachment %d '%s' (%d bytes) uploaded",
            inquiry_id, order, att["name"], len(att["bytes"]),
        )
        db.add(InquiryAttachment(
            inquiry_id=inquiry_id,
            url=url,
            filename=att["name"],
            content_type=att["content_type"],
            summary=att_summary,
            display_order=order,
        ))
        # Backward-compat scalars — first successfully-uploaded attachment wins.
        if uploaded_count == 1:
            obj.pdf_url = url
            obj.pdf_filename = att["name"]
            obj.pdf_summary = att_summary

    log.info("Captured email reply for inquiry %s from %s", inquiry_id, sender)
    return inquiry_id


def poll_once() -> int:
    """Connect, read unseen tagged replies, write them to inquiries.

    Returns the number of inquiries updated. Safe to call repeatedly; only
    messages we successfully match are marked \\Seen.
    """
    if not is_configured():
        return 0

    host = os.getenv("IMAP_HOST", "imap.gmail.com")
    port = int(os.getenv("IMAP_PORT", "993"))
    username = os.getenv("IMAP_USERNAME")
    password = os.getenv("IMAP_PASSWORD")
    mailbox = os.getenv("IMAP_MAILBOX", "INBOX")

    updated = 0
    conn: Optional[imaplib.IMAP4_SSL] = None
    try:
        conn = imaplib.IMAP4_SSL(host, port)
        conn.login(username, password)
        conn.select(mailbox)

        typ, data = conn.search(None, "UNSEEN")
        if typ != "OK" or not data or not data[0]:
            return 0

        msg_ids = data[0].split()
        db = SessionLocal()
        try:
            for mid in msg_ids:
                typ, msg_data = conn.fetch(mid, "(RFC822)")
                if typ != "OK" or not msg_data or not msg_data[0]:
                    continue
                raw_bytes = msg_data[0][1]
                try:
                    matched = _process_message(db, raw_bytes)
                except Exception as e:
                    log.exception("Failed to process message %s: %s", mid, e)
                    db.rollback()  # discard any partial db.add() calls from this message
                    continue
                if matched is not None:
                    db.commit()
                    updated += 1
                    # Forward to legacy if this inquiry came from InpharmD.
                    try:
                        obj = db.get(Inquiry, matched)
                        if obj is not None:
                            legacy_response_service.maybe_post_for_inquiry(db, obj)
                    except Exception:
                        log.exception(
                            "Legacy POST after IMAP poll failed for inquiry %s",
                            matched,
                        )
                    # Mark as read so we don't reprocess it next tick.
                    conn.store(mid, "+FLAGS", "\\Seen")
                # Unmatched messages are left unseen for a human to handle.
        finally:
            db.close()
    except Exception as e:
        log.exception("IMAP poll failed: %s", e)
    finally:
        if conn is not None:
            try:
                conn.logout()
            except Exception:
                pass

    if updated:
        log.info("IMAP poll updated %s inquir%s", updated, "y" if updated == 1 else "ies")
    return updated
