"""Slack notifier — posts a formatted card when a manufacturer reply is captured.

Uses a Slack Incoming Webhook (no OAuth scopes needed). Create one at
https://api.slack.com/messaging/webhooks, pick the target channel, and set the
resulting URL as SLACK_WEBHOOK_URL.

Required env vars:
    SLACK_WEBHOOK_URL   Incoming webhook URL for the target channel

Optional env vars:
    APP_BASE_URL        Dashboard base URL; if set, the card title links to it
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

log = logging.getLogger("inquiry.slack")

# Slack hard-limits a single text object to 3000 chars; stay under it.
_MAX_TEXT = 2900
# Slack hard-limits plain_text inside a button to 75 chars — overshooting
# returns 400 invalid_blocks for the whole message.
_MAX_BUTTON_LABEL = 70
# Slack allows max 25 elements in an actions block and max 50 blocks per
# message. Cap attachment rendering well below both limits. With 5 fixed
# header/body blocks + up to 10 summary blocks + 1 actions block (1 "View"
# button + up to 10 attachment buttons), we stay at most 17 blocks / 11
# action elements — comfortably within all limits.
_MAX_ATT_IN_SLACK = 10


def is_configured() -> bool:
    return bool(os.getenv("SLACK_WEBHOOK_URL"))


def _truncate(text: str, limit: int = _MAX_TEXT) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _post(payload: dict, *, log_context: str) -> bool:
    """Shared webhook POST used by the bulk-batch notifications below."""
    if not is_configured():
        return False
    webhook = os.getenv("SLACK_WEBHOOK_URL")
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(webhook, json=payload)
            if resp.status_code >= 400:
                log.warning("Slack post failed (%s): %s %s", log_context, resp.status_code, resp.text[:200])
                return False
    except Exception as e:
        log.warning("Slack post error (%s): %s", log_context, e)
        return False
    log.info("Posted Slack %s", log_context)
    return True


def notify_bulk_scheduled(batch_id: str, items: list) -> bool:
    """Post one Slack notification listing every inquiry scheduled together
    in one bulk_create_inquiries email dispatch.

    items: list of dicts with keys inquiry_id, manufacturer, medication_name
    (optional), email_scheduled_for (datetime).
    """
    lines = []
    for item in items:
        scheduled = item.get("email_scheduled_for")
        scheduled_str = scheduled.strftime("%Y-%m-%d %H:%M UTC") if scheduled else "unknown"
        medication = (item.get("medication_name") or "").strip() or "—"
        lines.append(
            f"• *#{item['inquiry_id']}* — {item['manufacturer']} — {medication} — {scheduled_str}"
        )
    body = _truncate("\n".join(lines))

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "\U0001F4C5 Bulk email batch scheduled", "emoji": True},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{len(items)} inquir{'y' if len(items) == 1 else 'ies'} scheduled:*\n{body}"},
        },
    ]
    payload = {
        "text": f"Bulk email batch scheduled ({len(items)} inquiries)",
        "blocks": blocks,
    }
    return _post(payload, log_context=f"bulk-scheduled card for batch {batch_id}")


def notify_bulk_completed(
    batch_id: str, *, total_count: int, sent_count: int, cancelled_items: list
) -> bool:
    """Post one Slack notification summarizing a finished bulk email batch —
    every inquiry has either sent or been manually cancelled (draft).

    cancelled_items: list of dicts with keys inquiry_id, manufacturer,
    medication_name (optional).
    """
    cancelled_count = len(cancelled_items)
    summary = f"Bulk batch completed: {sent_count} of {total_count} emails sent successfully."

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "✅ Bulk email batch complete", "emoji": True},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": summary},
        },
    ]

    if cancelled_count:
        lines = [
            f"• *#{item['inquiry_id']}* — {item['manufacturer']} — {(item.get('medication_name') or '').strip() or '—'}"
            for item in cancelled_items
        ]
        body = _truncate("\n".join(lines))
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{cancelled_count} email{'s' if cancelled_count != 1 else ''} cancelled:*\n{body}",
            },
        })

    payload = {
        "text": f"{summary}" + (f" {cancelled_count} cancelled." if cancelled_count else ""),
        "blocks": blocks,
    }
    return _post(payload, log_context=f"bulk-completed card for batch {batch_id}")


def _requester_line(name: Optional[str], email: Optional[str]) -> str:
    name = (name or "").strip()
    email = (email or "").strip()
    if name and email:
        return f"{name} <{email}>"
    return name or email or "Unknown"


def _inquiry_url(inquiry_id: int, *, focus: Optional[str] = None) -> Optional[str]:
    """Deep-link to the inquiry detail in the InpharmD UI, if APP_BASE_URL is set.

    focus='transcript' auto-expands and scrolls to the call transcript.
    """
    base = (os.getenv("APP_BASE_URL") or "").rstrip("/")
    if not base:
        return None
    suffix = f"&focus={focus}" if focus else ""
    return f"{base}/#inquiries?id={inquiry_id}{suffix}"


def notify_reply(
    *,
    inquiry_id: int,
    manufacturer: str,
    subject: str,
    question: str,
    answer: str,
    requester_name: Optional[str] = None,
    requester_email: Optional[str] = None,
    sender_email: Optional[str] = None,
    channel: str = "email",
    pdf_url: Optional[str] = None,
    pdf_filename: Optional[str] = None,
    pdf_summary: Optional[str] = None,
    inbound_attachments: Optional[list] = None,
) -> bool:
    """Post a manufacturer-response card to Slack. Returns True if delivered.

    channel: "email" for an email reply, "call" for a phone-call answer.
    pdf_url: optional link to a manufacturer-attached PDF.
    """
    if not is_configured():
        return False

    webhook = os.getenv("SLACK_WEBHOOK_URL")
    requester = _requester_line(requester_name, requester_email)

    # Call links jump straight to the transcript; email links open the thread view.
    inquiry_url = _inquiry_url(
        inquiry_id, focus="transcript" if channel == "call" else None
    )
    label = f"Inquiry #{inquiry_id} — {manufacturer}"
    title = f"<{inquiry_url}|{label}>" if inquiry_url else f"*{label}*"

    if channel == "call":
        header = "\U0001F4DE New response received from manufacturer (phone call)"
    else:
        header = "\U0001F4E9 New response received from manufacturer"

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": header, "emoji": True},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": title},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Manufacturer:*\n{manufacturer}"},
                {"type": "mrkdwn", "text": f"*Asked by:*\n{requester}"},
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Question*\n{_truncate(question)}"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Manufacturer response*\n{_truncate(answer)}"},
        },
    ]

    # Build effective attachment list: prefer inbound_attachments, fall back to legacy scalars.
    if inbound_attachments:
        _atts_all = inbound_attachments
    elif pdf_url:
        _atts_all = [{"url": pdf_url, "filename": pdf_filename, "summary": pdf_summary}]
    else:
        _atts_all = []

    # Cap to stay within Slack's 50-block and 25-actions-element limits.
    _overflow = len(_atts_all) - _MAX_ATT_IN_SLACK
    _atts = _atts_all[:_MAX_ATT_IN_SLACK]

    # One summary block per attachment that has one (and whose summary differs from the answer body).
    for _att in _atts:
        _att_summary = (_att.get("summary") or "").strip()
        if _att_summary and _att_summary != (answer or "").strip():
            _att_label = _att.get("filename") or "Attachment"
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Attachment summary — {_att_label}*\n{_truncate(_att_summary)}",
                },
            })

    # Action button row: UI link (transcript for calls / thread for emails)
    # plus one button per attachment when files were captured.
    action_elements = []
    if inquiry_url:
        action_elements.append({
            "type": "button",
            "text": {
                "type": "plain_text",
                "text": "View transcript" if channel == "call" else "View thread",
                "emoji": True,
            },
            "url": inquiry_url,
            "style": "primary",
        })
    for _att in _atts:
        _att_url = _att.get("url")
        if not _att_url:
            continue
        _att_name = _att.get("filename") or "Download attachment"
        raw_label = f"\U0001F4CE {_att_name}"
        if len(raw_label) > _MAX_BUTTON_LABEL:
            head, _, ext = (_att_name).rpartition(".")
            ext_tail = f".{ext}" if ext else ""
            keep = _MAX_BUTTON_LABEL - len(ext_tail) - 4
            short_name = (head or "").strip("_")[:keep].rstrip("_-. ") + "…"
            raw_label = f"\U0001F4CE {short_name}{ext_tail}"
        action_elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": raw_label, "emoji": True},
            "url": _att_url,
        })
    if action_elements:
        blocks.append({"type": "actions", "elements": action_elements})

    if _overflow > 0:
        blocks.append({
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": f"_+{_overflow} more attachment{'s' if _overflow != 1 else ''} — open the thread to view all_",
            }],
        })

    if sender_email:
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"Replied by {sender_email}"}],
            }
        )

    payload = {
        "text": f"Manufacturer reply received for Inquiry #{inquiry_id} ({manufacturer})",
        "blocks": blocks,
    }

    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(webhook, json=payload)
            if resp.status_code >= 400:
                log.warning("Slack post failed: %s %s", resp.status_code, resp.text[:200])
                return False
    except Exception as e:
        log.warning("Slack post error: %s", e)
        return False

    log.info("Posted Slack reply card for inquiry %s", inquiry_id)
    return True
