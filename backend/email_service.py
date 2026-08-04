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
    extra_medications: Optional[list] = None,
) -> tuple[str, str]:
    """Return (plain_text, html) tuple for the email body."""
    import html as html_lib

    requester_line_plain = ""
    requester_line_html = ""
    if requester_name and requester_email:
        requester_line_plain = f"\nRequested by: {requester_name} <{requester_email}>\n"
        requester_line_html = f"<p>Requested by: {html_lib.escape(requester_name)} &lt;{html_lib.escape(requester_email)}&gt;</p>"
    elif requester_email:
        requester_line_plain = f"\nRequested by: {requester_email}\n"
        requester_line_html = f"<p>Requested by: {html_lib.escape(requester_email)}</p>"
    elif requester_name:
        requester_line_plain = f"\nRequested by: {requester_name}\n"
        requester_line_html = f"<p>Requested by: {html_lib.escape(requester_name)}</p>"

    # Build unified list of all medications (primary + siblings).
    all_meds: list[dict] = []
    if medication_name or pi_storage_data or pi_link:
        all_meds.append({"medication_name": medication_name, "pi_storage_data": pi_storage_data, "pi_link": pi_link})
    for m in (extra_medications or []):
        if m.get("medication_name") or m.get("pi_storage_data") or m.get("pi_link"):
            all_meds.append(m)

    product_section_plain = ""
    product_section_html = ""
    if all_meds:
        multi = len(all_meds) > 1
        count_label = f" ({len(all_meds)} medications)" if multi else ""
        lines_plain = [f"PRODUCT DETAILS{count_label}:"]
        lines_html = [f"<p><strong>PRODUCT DETAILS{count_label}:</strong></p>"]
        for i, med in enumerate(all_meds):
            prefix_plain = f"{i + 1}. " if multi else ""
            prefix_html = f"<strong>{i + 1}.</strong> " if multi else ""
            lines_html.append("<p>")
            if med.get("medication_name"):
                lines_plain.append(f"{prefix_plain}Medication/Vaccine: {med['medication_name']}")
                lines_html.append(f"{prefix_html}<strong>Medication/Vaccine:</strong> {html_lib.escape(med['medication_name'])}<br>")
                prefix_plain = "   " if multi else ""
                prefix_html = "&nbsp;&nbsp;&nbsp;"
            if med.get("pi_storage_data"):
                lines_plain.append(f"{prefix_plain}PI Storage Information: {med['pi_storage_data']}")
                lines_html.append(f"{prefix_html}<strong>PI Storage Information:</strong> {html_lib.escape(med['pi_storage_data'])}<br>")
                prefix_plain = "   " if multi else ""
                prefix_html = "&nbsp;&nbsp;&nbsp;"
            if med.get("pi_link"):
                lines_plain.append(f"{prefix_plain}Prescribing Information: {med['pi_link']}")
                lines_html.append(f'{prefix_html}<strong>Prescribing Information:</strong> <a href="{html_lib.escape(med["pi_link"])}">{html_lib.escape(med["pi_link"])}</a><br>')
            lines_html.append("</p>")
        product_section_plain = "\n" + "\n".join(lines_plain) + "\n"
        product_section_html = "\n".join(lines_html)

    plain = f"""\
Hello,

This is a medical-information inquiry from InpharmD on behalf of a pharmacist
seeking clinical information about {manufacturer_name}.

QUESTION:
{question}
{product_section_plain}
We would appreciate your written response at your earliest convenience.
{requester_line_plain}
To help us route your reply, please keep the subject line intact
(it contains the inquiry reference [InpharmD #{inquiry_id}]).

Thank you for your time,
InpharmD Medical Information Team

—
This message was generated by the InpharmD inquiry system.
If this email reached you in error, please disregard.
"""

    html = f"""\
<html><body style="font-family:Arial,sans-serif;font-size:14px;color:#222;line-height:1.6;">
<p>Hello,</p>
<p>This is a medical-information inquiry from InpharmD on behalf of a pharmacist
seeking clinical information about {html_lib.escape(manufacturer_name)}.</p>
<p><strong>QUESTION:</strong></p>
<p>{html_lib.escape(question).replace(chr(10), '<br>')}</p>
{product_section_html}
<p>We would appreciate your written response at your earliest convenience.</p>
{requester_line_html}
<p>To help us route your reply, please keep the subject line intact
(it contains the inquiry reference [InpharmD #{inquiry_id}]).</p>
<p>Thank you for your time,<br>InpharmD Medical Information Team</p>
<p style="color:#888;font-size:12px;">—<br>
This message was generated by the InpharmD inquiry system.<br>
If this email reached you in error, please disregard.</p>
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
    extra_medications: Optional[list] = None,
) -> str:
    """Send the inquiry email via the SendGrid API.

    Returns SendGrid's X-Message-Id header (used to correlate replies / events).
    Replies are routed back to EMAIL_FROM so IMAP polling can capture them.
    """
    cfg = SendGridConfig.from_env()

    tagged_subject = f"[InpharmD #{inquiry_id}] {subject}"
    plain, html = _build_body(
        inquiry_id=inquiry_id,
        manufacturer_name=manufacturer_name,
        question=question,
        requester_name=requester_name,
        requester_email=requester_email,
        medication_name=medication_name,
        pi_storage_data=pi_storage_data,
        pi_link=pi_link,
        extra_medications=extra_medications,
    )

    payload = {
        "personalizations": [{"to": [{"email": to_email}]}],
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
