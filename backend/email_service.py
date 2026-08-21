"""SendGrid wrapper for sending manufacturer-MI inquiry emails.

Sends are made through the SendGrid v3 Web API (no SMTP). Replies come back to
the same mailbox (EMAIL_FROM) and are picked up by `imap_service.py`.

Configure via env vars (set in backend/.env and on Render):
    SENDGRID_API_KEY   your SendGrid API key (starts with "SG.")
    EMAIL_FROM         the constant From address, e.g. druginfo@inpharmd.com
    EMAIL_FROM_NAME    optional display name; defaults to "InpharmD Medical Information"
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

import httpx

log = logging.getLogger("inquiry.email")

SENDGRID_SEND_URL = "https://api.sendgrid.com/v3/mail/send"
DEFAULT_FROM = "druginfo@inpharmd.com"
DEFAULT_FROM_NAME = "InpharmD Medical Information"


class EmailConfigError(RuntimeError):
    """Raised when SendGrid env vars are missing or invalid."""


@dataclass
class SendGridConfig:
    api_key: str
    from_addr: str
    from_name: str

    @classmethod
    def from_env(cls) -> "SendGridConfig":
        api_key = os.getenv("SENDGRID_API_KEY")
        if not api_key:
            raise EmailConfigError(
                "SendGrid not configured. Missing env var SENDGRID_API_KEY. "
                "Add it to backend/.env."
            )
        from_addr = os.getenv("EMAIL_FROM") or DEFAULT_FROM
        from_name = os.getenv("EMAIL_FROM_NAME") or DEFAULT_FROM_NAME
        return cls(api_key=api_key, from_addr=from_addr, from_name=from_name)


def _build_body(
    *,
    inquiry_id: int,
    manufacturer_name: str,
    question: str,
    requester_name: Optional[str],
    requester_email: Optional[str],
    medication_name: Optional[str] = None,
    pi_storage_data: Optional[str] = None,
    pi_link: Optional[str] = None,
    team_name: Optional[str] = None,
) -> tuple[str, str]:
    """Return (plain_text, html) tuple for the email body.

    inquiry_id/manufacturer_name/pi_storage_data/pi_link/requester_name/
    requester_email are accepted (and still passed by every caller) but are
    not rendered — the client-approved template only varies by `question`,
    `team_name`, and `medication_name` (rendered as a "Drug Name:" line
    above QUESTION when present). Kept as parameters so no call site needs
    to change.
    """
    import html as html_lib

    team = (team_name or "").strip() or None
    drug_name = (medication_name or "").strip() or None

    greeting_plain = (
        f"Hello, this is a drug information request from a pharmacist at {team}."
        if team
        else "Hello, this is a drug information request from a pharmacist."
    )
    greeting_html = (
        f"<p>Hello, this is a drug information request from a pharmacist at {html_lib.escape(team)}.</p>"
        if team
        else "<p>Hello, this is a drug information request from a pharmacist.</p>"
    )

    sig_lines_plain = ["Requested by:", "Leah Mueller, PharmD", "Pharmacist"]
    sig_lines_html = ["Requested by:", "Leah Mueller, PharmD", "Pharmacist"]
    if team:
        sig_lines_plain.append(f"For {team}")
        sig_lines_html.append(f"For {html_lib.escape(team)}")
    sig_lines_plain.append("3423 Piedmont Rd NE, Atlanta, GA 30305")
    sig_lines_html.append("3423 Piedmont Rd NE, Atlanta, GA 30305")
    signature_plain = "\n".join(sig_lines_plain)
    signature_html = "<p>" + "<br>\n".join(sig_lines_html) + "</p>"

    drug_name_line_plain = f"Drug Name: {drug_name}\n\n" if drug_name else ""
    drug_name_line_html = (
        f"<p><strong>Drug Name:</strong> {html_lib.escape(drug_name)}</p>\n" if drug_name else ""
    )

    plain = f"""\
{greeting_plain}

{drug_name_line_plain}QUESTION:
{question}

{signature_plain}
"""

    html = f"""\
<html><body style="font-family:Arial,sans-serif;font-size:14px;color:#222;line-height:1.6;">
{greeting_html}
{drug_name_line_html}<p><strong>QUESTION:</strong></p>
<p>{html_lib.escape(question).replace(chr(10), '<br>')}</p>
{signature_html}
</body></html>
"""
    return plain, html


def send_inquiry_email(
    *,
    inquiry_id: int,
    manufacturer_name: str,
    to_email: str,
    subject: str,
    question: str,
    requester_name: Optional[str] = None,
    requester_email: Optional[str] = None,
    medication_name: Optional[str] = None,
    pi_storage_data: Optional[str] = None,
    pi_link: Optional[str] = None,
    team_name: Optional[str] = None,
) -> str:
    """Send the inquiry email via the SendGrid API.

    Returns SendGrid's X-Message-Id header (used to correlate replies / events).
    Replies are routed back to EMAIL_FROM so IMAP polling can capture them.
    """
    cfg = SendGridConfig.from_env()

    # Inquiry.subject is the single source of truth — the caller always
    # passes the current, already-tag-guaranteed value (see
    # routers.inquiries._with_subject_tag), so it's sent verbatim here. Do
    # not rebuild it: users can freely edit it after creation, and that edit
    # must be what actually goes out in the email.
    tagged_subject = subject
    plain, html = _build_body(
        inquiry_id=inquiry_id,
        manufacturer_name=manufacturer_name,
        question=question,
        requester_name=requester_name,
        requester_email=requester_email,
        medication_name=medication_name,
        pi_storage_data=pi_storage_data,
        pi_link=pi_link,
        team_name=team_name,
    )

    payload = {
        "personalizations": [
            {
                "to": [{"email": to_email}],
                "cc": [{"email": "sharanya@brightcone.com"}],
                "bcc": [
                    {"email": "tulsee@brightcone.com"},
                    {"email": "chinna@brightcone.com"},
                ],
            }
        ],
        "from": {"email": cfg.from_addr, "name": cfg.from_name},
        # Replies must land in our mailbox so imap_service can read them.
        "reply_to": {"email": cfg.from_addr, "name": cfg.from_name},
        "subject": tagged_subject,
        # Send both plain text (fallback) and HTML (bold headings).
        # Per RFC 2046 the last entry is the preferred version — HTML goes last.
        "content": [
            {"type": "text/plain", "value": plain},
            {"type": "text/html", "value": html},
        ],
    }

    headers = {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=30) as client:
        resp = client.post(SENDGRID_SEND_URL, headers=headers, json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"SendGrid rejected the send: {resp.status_code} {resp.text}"
            )
        message_id = resp.headers.get("X-Message-Id") or ""

    log.info("Sent inquiry %s to %s via SendGrid (msg id %s)", inquiry_id, to_email, message_id)
    return message_id
