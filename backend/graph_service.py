"""Microsoft Graph API poller — reads manufacturer email replies from the mailbox.

Uses OAuth2 client-credentials (no user password) to access the druginfo@inpharmd.com
mailbox. Finds unread messages whose subject contains [InpharmD #N], extracts the
reply text, runs GPT cleanup, and writes the answer onto the matching inquiry so it
appears on the dashboard automatically.

Required env vars:
    AZURE_TENANT_ID       Directory (tenant) ID from Azure app registration
    AZURE_CLIENT_ID       Application (client) ID from Azure app registration
    AZURE_CLIENT_SECRET   Client secret value from Certificates & secrets

Optional env vars:
    GRAPH_MAILBOX         Mailbox to poll (default: value of EMAIL_FROM)
    GRAPH_POLL_SECONDS    Poll interval in seconds (default: 60)
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional

import httpx

import legacy_response_service
import s3_service
import summary_service
from database import SessionLocal
from models import Inquiry

log = logging.getLogger("inquiry.graph")

_TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_SUBJECT_TAG = re.compile(r"\[InpharmD #(\d+)\]", re.IGNORECASE)

_QUOTE_MARKERS = (
    re.compile(r"^On .+wrote:$", re.IGNORECASE | re.DOTALL),
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}", re.IGNORECASE),
    re.compile(r"^_{5,}$"),
    re.compile(r"^From:\s", re.IGNORECASE),
    re.compile(r"^Sent from my ", re.IGNORECASE),
    re.compile(r"^\[InpharmD #\d+\]", re.IGNORECASE),
)

# Lines that mark the start of a sign-off / signature block. Once we see one,
# we cut from there to the end (assuming the meaningful body is above it).
_SIGNATURE_MARKERS = (
    re.compile(r"^--\s*$"),                            # RFC standard separator
    re.compile(r"^(thanks|thank you|regards|best regards|best|sincerely|kind regards|cheers|warm regards|respectfully)[,!.]?\s*$", re.IGNORECASE),
    re.compile(r"^(this (e-?mail|message) (is intended|contains|and any attachments))", re.IGNORECASE),
    re.compile(r"^(confidentiality\s+notice|disclaimer)\b", re.IGNORECASE),
    re.compile(r"^\*+\s*confidentiality", re.IGNORECASE),
)


def is_configured() -> bool:
    return bool(
        os.getenv("AZURE_TENANT_ID")
        and os.getenv("AZURE_CLIENT_ID")
        and os.getenv("AZURE_CLIENT_SECRET")
    )


def _get_token() -> str:
    """Fetch a fresh OAuth2 access token using client credentials."""
    tenant_id = os.getenv("AZURE_TENANT_ID")
    client_id = os.getenv("AZURE_CLIENT_ID")
    client_secret = os.getenv("AZURE_CLIENT_SECRET")

    url = _TOKEN_URL.format(tenant_id=tenant_id)
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "https://graph.microsoft.com/.default",
    }
    with httpx.Client(timeout=15) as client:
        resp = client.post(url, data=data)
        if resp.status_code >= 400:
            try:
                err = resp.json()
                desc = err.get("error_description") or err.get("error") or resp.text
            except Exception:
                desc = resp.text
            log.error("Azure token request failed (%s): %s", resp.status_code, desc)
            raise RuntimeError(f"Azure token failed: {desc}") from None
        return resp.json()["access_token"]


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


def _strip_signature(text: str) -> str:
    """Cut everything from the first signature marker onward. Keeps the body
    above it. Falls back to the original text if nothing matched."""
    lines = text.split("\n")
    cut_at = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if any(m.match(stripped) for m in _SIGNATURE_MARKERS):
            cut_at = i
            break
    if cut_at is None:
        return text.strip()
    cleaned = "\n".join(lines[:cut_at]).strip()
    return cleaned if cleaned else text.strip()


def _collapse_blank_lines(text: str) -> str:
    """Collapse 3+ consecutive blank lines down to a single blank line."""
    return re.sub(r"\n{3,}", "\n\n", text).strip()


_SIG_LEFTOVER_PATTERNS = (
    re.compile(r"\bPharm\.?\s*D\.?\b", re.IGNORECASE),
    re.compile(r"\bM\.?\s*D\.?\b"),
    re.compile(r"\bMedical (Information|Affairs)\b", re.IGNORECASE),
    re.compile(r"\bCONFIDENTIAL\b"),
    re.compile(r"\bDISCLAIMER\b", re.IGNORECASE),
    re.compile(r"\b\(\d{3}\)\s*\d{3}-\d{4}\b"),    # phone like (404) 555-1212
    re.compile(r"\b\d{3}-\d{3}-\d{4}\b"),          # phone like 404-555-1212
)


def _signature_likely_remains(text: str) -> bool:
    """Heuristic: did the manual strip miss a signature block?"""
    return sum(1 for p in _SIG_LEFTOVER_PATTERNS if p.search(text)) >= 2


def clean_reply_body(text: str) -> str:
    """Full pipeline: drop quoted history, drop signature, tidy whitespace.

    If OpenAI is configured AND signature markers still remain after the
    regex pass, hand off to summary_service.strip_signature_with_ai for a
    second pass. The AI is instructed to preserve the answer text verbatim
    — it only removes the signature/disclaimer block.
    """
    cleaned = _collapse_blank_lines(_strip_signature(_strip_quoted(text)))
    if cleaned and summary_service.is_configured() and _signature_likely_remains(cleaned):
        cleaned = summary_service.strip_signature_with_ai(cleaned)
    return cleaned


def _get_body(msg: dict) -> str:
    body = msg.get("body", {})
    content = body.get("content", "")
    content_type = body.get("contentType", "text")
    if content_type == "html":
        return _strip_html(content)
    return content


def _process_message(db, token: str, mailbox: str, msg: dict) -> Optional[dict]:
    """Process one Graph message. Returns reply data if the inquiry was updated, else None."""
    # When Graph mark-read is unavailable (missing Mail.ReadWrite), messages
    # stay unread and re-appear on every poll. Skip anything we've already
    # touched this process lifetime so we don't spam logs / API calls.
    if msg["id"] in _PROCESSED_MESSAGE_IDS:
        return None
    subject = msg.get("subject", "") or ""
    log.info(
        "pipeline: graph processing msg subject=%r has_attachments=%s",
        subject[:120], bool(msg.get("hasAttachments")),
    )
    m = _SUBJECT_TAG.search(subject)
    if not m:
        log.info("pipeline: graph skip — no [InpharmD #N] tag in subject")
        _mark_read(token, mailbox, msg["id"])
        return None

    inquiry_id = int(m.group(1))

    from models import Inquiry
    obj = db.get(Inquiry, inquiry_id)
    if not obj:
        log.info("Reply tagged inquiry %s but no such record; skipping", inquiry_id)
        _mark_read(token, mailbox, msg["id"])
        return None
    if obj.status == "closed":
        _mark_read(token, mailbox, msg["id"])
        return None
    if obj.email_response:
        _mark_read(token, mailbox, msg["id"])
        return None

    body = _get_body(msg)
    reply = clean_reply_body(body)

    sender = msg.get("from", {}).get("emailAddress", {}).get("address", "unknown")
    mfr_name = obj.manufacturer.manufacturer if obj.manufacturer else "the manufacturer"
    log.info(
        "pipeline: graph parsed inquiry=%s sender=%s mfr=%s body_chars=%d reply_chars=%d",
        inquiry_id, sender, mfr_name, len(body or ""), len(reply or ""),
    )

    # ---- Document attachment (optional: PDF / DOCX / DOC / XLSX / XLS / CSV) ----
    pdf_url: Optional[str] = None
    pdf_filename: Optional[str] = None
    pdf_summary: Optional[str] = None

    if msg.get("hasAttachments"):
        doc = _fetch_document_attachment(token, mailbox, msg["id"])
        if doc:
            log.info("Inquiry %s reply has attachment '%s' (%d bytes)",
                     inquiry_id, doc["name"], len(doc["bytes"]))
            pdf_filename = doc["name"]
            pdf_url = s3_service.upload_bytes(
                doc["bytes"],
                original_name=doc["name"],
                inquiry_id=inquiry_id,
                content_type=doc["content_type"],
            )
            if summary_service.is_configured():
                doc_text = summary_service.extract_document_text(doc["name"], doc["bytes"])
                if doc_text:
                    try:
                        pdf_summary = summary_service.summarize_pdf(
                            question=obj.question,
                            manufacturer=mfr_name,
                            pdf_text=doc_text,
                        )
                    except Exception as e:
                        log.warning("Attachment summary unavailable for inquiry %s: %s", inquiry_id, e)

    # Keep the rep's actual reply text as the primary answer — even if it's
    # short. The PDF summary is supplemental context, not a replacement.
    # Only fall back to the PDF summary when there's literally no reply body
    # (e.g. blank email with only an attachment).
    final_answer = reply or pdf_summary or ""

    if not final_answer and not pdf_url:
        log.info("Inquiry %s reply had no extractable body and no PDF; skipping", inquiry_id)
        return None

    obj.email_response = reply or pdf_summary or ""
    obj.email_response_at = datetime.now(timezone.utc)
    obj.status = "email_responded"
    obj.next_retry_at = None
    obj.call_scheduled_for = None
    obj.final_answer = final_answer
    obj.pdf_url = pdf_url
    obj.pdf_filename = pdf_filename
    obj.pdf_summary = pdf_summary
    log.info(
        "Captured Graph email reply for inquiry %s from %s (reply=%d chars, pdf=%s)",
        inquiry_id, sender, len(reply or ""), bool(pdf_url),
    )
    log.info(
        "pipeline: graph stored email_response inquiry=%s status=email_responded "
        "final_answer_chars=%d pdf_attached=%s",
        inquiry_id, len(final_answer or ""), bool(pdf_url),
    )
    return {
        "inquiry_id": inquiry_id,
        "manufacturer": mfr_name,
        "subject": obj.subject,
        "question": obj.question,
        "answer": obj.final_answer or "(See attached PDF.)",
        "pdf_summary": pdf_summary,
        "requester_name": obj.requester_name,
        "requester_email": obj.requester_email,
        "sender_email": sender,
        "pdf_url": pdf_url,
        "pdf_filename": pdf_filename,
    }


_SUPPORTED_ATTACHMENT_TYPES = {
    # (content-type fragment, extension) pairs we recognise
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "text/csv": ".csv",
    "application/csv": ".csv",
}
_SUPPORTED_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv"}

_CONTENT_TYPE_FOR_EXT = {
    ".pdf":  "application/pdf",
    ".doc":  "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls":  "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv":  "text/csv",
}


def _fetch_document_attachment(token: str, mailbox: str, message_id: str) -> Optional[dict]:
    """Return the first supported document attachment from the message (if any).

    Supported formats: PDF, DOCX, DOC, XLSX, XLS, CSV.
    Returns a dict {'name': str, 'bytes': bytes, 'content_type': str} or None.
    """
    url = (
        f"{_GRAPH_BASE}/users/{mailbox}/messages/{message_id}/attachments"
        f"?$select=id,name,contentType,size"
    )
    headers = {"Authorization": f"Bearer {token}"}
    try:
        with httpx.Client(timeout=30) as client:
            r = client.get(url, headers=headers)
            r.raise_for_status()
            items = r.json().get("value", [])
    except Exception as e:
        log.warning("Failed to list attachments for message %s: %s", message_id, e)
        return None

    chosen = None
    for a in items:
        if a.get("@odata.type") != "#microsoft.graph.fileAttachment":
            continue
        name = (a.get("name") or "").lower()
        ctype = (a.get("contentType") or "").lower()
        ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
        if ext in _SUPPORTED_EXTENSIONS or any(ctype.startswith(k) for k in _SUPPORTED_ATTACHMENT_TYPES):
            chosen = a
            break
    if not chosen:
        return None

    att_id = chosen["id"]
    full_url = f"{_GRAPH_BASE}/users/{mailbox}/messages/{message_id}/attachments/{att_id}"
    try:
        with httpx.Client(timeout=60) as client:
            r = client.get(full_url, headers=headers)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        log.warning("Failed to fetch attachment %s: %s", att_id, e)
        return None

    import base64
    try:
        raw = base64.b64decode(data.get("contentBytes", ""))
    except Exception as e:
        log.warning("Could not b64-decode attachment %s: %s", att_id, e)
        return None
    if not raw:
        return None

    original_name = data.get("name") or chosen.get("name") or "attachment"
    ext = "." + original_name.rsplit(".", 1)[-1].lower() if "." in original_name else ""
    content_type = _CONTENT_TYPE_FOR_EXT.get(ext, "application/octet-stream")
    return {"name": original_name, "bytes": raw, "content_type": content_type}


_MARK_READ_DISABLED = False  # flips to True on first 403 to stop hammering Graph
_PROCESSED_MESSAGE_IDS: set[str] = set()  # in-memory skip list when we can't mark read


def _mark_read(token: str, mailbox: str, message_id: str) -> None:
    """Mark message read so the next poll skips it. Requires Mail.ReadWrite (optional).

    Falls back gracefully when the Graph app registration is missing
    Mail.ReadWrite: we cache the message id in-memory so the same process
    doesn't keep re-processing (and 403-PATCH'ing) the same messages every
    tick. A restart resets the cache — that's fine; the untagged messages
    are cheap to re-scan once."""
    global _MARK_READ_DISABLED
    _PROCESSED_MESSAGE_IDS.add(message_id)
    if _MARK_READ_DISABLED:
        return
    url = f"{_GRAPH_BASE}/users/{mailbox}/messages/{message_id}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.patch(url, headers=headers, json={"isRead": True})
            if resp.status_code == 403:
                _MARK_READ_DISABLED = True
                log.warning(
                    "Graph mark-read got 403 — disabling for this process. "
                    "Add Mail.ReadWrite to the Azure app registration to persist read state across restarts."
                )
            elif resp.status_code >= 400:
                log.warning("Mark-read failed: %s %s", resp.status_code, resp.text[:120])
    except Exception as e:
        log.debug("Mark-read failed: %s", e)


def poll_once() -> int:
    """Fetch unread tagged messages, store replies, return count updated."""
    if not is_configured():
        return 0

    mailbox = os.getenv("GRAPH_MAILBOX") or os.getenv("EMAIL_FROM", "druginfo@inpharmd.com")

    try:
        token = _get_token()
    except Exception as e:
        log.exception("Failed to get Graph token: %s", e)
        return 0

    headers = {"Authorization": f"Bearer {token}"}
    url = (
        f"{_GRAPH_BASE}/users/{mailbox}/messages"
        f"?$filter=isRead eq false"
        f"&$select=id,subject,from,body,hasAttachments"
        f"&$top=25"
    )

    try:
        with httpx.Client(timeout=20) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            messages = resp.json().get("value", [])
    except Exception as e:
        log.exception("Graph messages fetch failed: %s", e)
        return 0

    if not messages:
        return 0

    updated = 0
    db = SessionLocal()
    try:
        for msg in messages:
            try:
                changed = _process_message(db, token, mailbox, msg)
            except Exception as e:
                log.exception("Failed to process Graph message %s: %s", msg.get("id"), e)
                continue
            if changed:
                db.commit()
                updated += 1
                _mark_read(token, mailbox, msg["id"])
                # Forward to legacy if this inquiry came from InpharmD.
                try:
                    obj = db.get(Inquiry, changed.get("inquiry_id"))
                    if obj is not None:
                        legacy_response_service.maybe_post_for_inquiry(db, obj)
                except Exception:
                    log.exception(
                        "Legacy POST after Graph poll failed for inquiry %s",
                        changed.get("inquiry_id"),
                    )
                try:
                    import slack_service
                    if slack_service.is_configured():
                        log.info(
                            "pipeline: slack notify_reply firing for inquiry %s (via graph poll)",
                            changed.get("inquiry_id"),
                        )
                        slack_service.notify_reply(**changed)
                    else:
                        log.info(
                            "pipeline: slack notify SKIPPED for inquiry %s: SLACK_WEBHOOK_URL not configured",
                            changed.get("inquiry_id"),
                        )
                except Exception:
                    log.exception(
                        "pipeline: slack notify FAILED for inquiry %s",
                        changed.get("inquiry_id"),
                    )

                # One-line summary — grep `pipeline: COMPLETE inquiry=N` to
                # see every reply's outcome at a glance.
                try:
                    inquiry_id = changed.get("inquiry_id")
                    refreshed = db.get(Inquiry, inquiry_id)
                    if refreshed is not None:
                        log.info(
                            "pipeline: COMPLETE inquiry=%s path=graph_poll "
                            "legacy_posted=%s sheet_posted=%s",
                            inquiry_id,
                            "yes" if refreshed.legacy_response_posted_at is not None else "no",
                            "yes" if refreshed.excel_response_posted_at is not None else "no",
                        )
                except Exception:
                    log.exception(
                        "pipeline: failed to log COMPLETE summary for inquiry %s",
                        changed.get("inquiry_id"),
                    )
    finally:
        db.close()

    if updated:
        log.info("Graph poll updated %s inquir%s", updated, "y" if updated == 1 else "ies")
    return updated
