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

import summary_service
from database import SessionLocal

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


def _get_body(msg: dict) -> str:
    body = msg.get("body", {})
    content = body.get("content", "")
    content_type = body.get("contentType", "text")
    if content_type == "html":
        return _strip_html(content)
    return content


def _process_message(db, token: str, mailbox: str, msg: dict) -> Optional[dict]:
    """Process one Graph message. Returns reply data if the inquiry was updated, else None."""
    subject = msg.get("subject", "") or ""
    m = _SUBJECT_TAG.search(subject)
    if not m:
        return None

    inquiry_id = int(m.group(1))

    from models import Inquiry
    obj = db.get(Inquiry, inquiry_id)
    if not obj:
        log.info("Reply tagged inquiry %s but no such record; skipping", inquiry_id)
        return None
    if obj.status == "closed":
        return None
    if obj.email_response:
        _mark_read(token, mailbox, msg["id"])
        return None

    body = _get_body(msg)
    reply = _strip_quoted(body)
    if not reply:
        log.info("Inquiry %s reply had no extractable body; skipping", inquiry_id)
        return None

    sender = msg.get("from", {}).get("emailAddress", {}).get("address", "unknown")
    mfr_name = obj.manufacturer.manufacturer if obj.manufacturer else "the manufacturer"

    obj.email_response = reply
    obj.email_response_at = datetime.now(timezone.utc)
    obj.status = "email_responded"
    obj.next_retry_at = None
    obj.call_scheduled_for = None

    final = reply
    if summary_service.is_configured():
        try:
            final = summary_service.extract_answer_from_email(
                question=obj.question,
                manufacturer=mfr_name,
                reply_text=reply,
            )
        except summary_service.SummaryConfigError:
            final = reply

    obj.final_answer = final
    log.info("Captured Graph email reply for inquiry %s from %s", inquiry_id, sender)
    return {
        "inquiry_id": inquiry_id,
        "manufacturer": mfr_name,
        "subject": obj.subject,
        "question": obj.question,
        "answer": final,
        "requester_name": obj.requester_name,
        "requester_email": obj.requester_email,
        "sender_email": sender,
    }


def _mark_read(token: str, mailbox: str, message_id: str) -> None:
    """Mark message read so the next poll skips it. Requires Mail.ReadWrite (optional)."""
    url = f"{_GRAPH_BASE}/users/{mailbox}/messages/{message_id}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.patch(url, headers=headers, json={"isRead": True})
            if resp.status_code == 403:
                log.debug("Cannot mark message read (add Mail.ReadWrite in Azure to enable)")
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
        f"&$select=id,subject,from,body"
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
                try:
                    import slack_service
                    if slack_service.is_configured():
                        slack_service.notify_reply(**changed)
                except Exception:
                    log.exception("Slack notify failed for inquiry %s", changed.get("inquiry_id"))
    finally:
        db.close()

    if updated:
        log.info("Graph poll updated %s inquir%s", updated, "y" if updated == 1 else "ies")
    return updated
