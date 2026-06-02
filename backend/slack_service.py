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


def is_configured() -> bool:
    return bool(os.getenv("SLACK_WEBHOOK_URL"))


def _truncate(text: str, limit: int = _MAX_TEXT) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _requester_line(name: Optional[str], email: Optional[str]) -> str:
    name = (name or "").strip()
    email = (email or "").strip()
    if name and email:
        return f"{name} <{email}>"
    return name or email or "Unknown"


def _inquiry_url(inquiry_id: int) -> Optional[str]:
    """Deep-link to the inquiry detail in the InpharmD UI, if APP_BASE_URL is set."""
    base = (os.getenv("APP_BASE_URL") or "").rstrip("/")
    if not base:
        return None
    # Hash route + ?id= so the SPA can open the detail panel.
    return f"{base}/#inquiries?id={inquiry_id}"


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
) -> bool:
    """Post a manufacturer-response card to Slack. Returns True if delivered.

    channel: "email" for an email reply, "call" for a phone-call answer.
    pdf_url: optional link to a manufacturer-attached PDF.
    """
    if not is_configured():
        return False

    webhook = os.getenv("SLACK_WEBHOOK_URL")
    requester = _requester_line(requester_name, requester_email)

    inquiry_url = _inquiry_url(inquiry_id)
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

    # Action button row: UI link (transcript for calls / thread for emails)
    # plus a PDF button when an attachment was captured.
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
    if pdf_url:
        action_elements.append({
            "type": "button",
            "text": {
                "type": "plain_text",
                "text": f"\U0001F4CE {pdf_filename or 'Download PDF'}",
                "emoji": True,
            },
            "url": pdf_url,
        })
    if action_elements:
        blocks.append({"type": "actions", "elements": action_elements})

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
