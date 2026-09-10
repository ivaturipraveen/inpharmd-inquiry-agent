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


def _sig_lines(team: Optional[str], *, escape=None) -> list[str]:
    """Shared signature-block lines (plain text, or HTML when `escape` is
    given) — used by both the follow-up branch and the stability-excursion
    template below so the signature is defined in exactly one place."""
    esc = escape or (lambda s: s)
    lines = ["Requested by:", "Leah Mueller, PharmD", "Pharmacist"]
    if team:
        lines.append(f"For {esc(team)}")
    lines.append("3423 Piedmont Rd NE, Atlanta, GA 30305")
    return lines


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
    is_followup: bool = False,
    # Appended as an unlabeled 2nd paragraph inside the existing "Additional
    # details" bullet — never its own bullet; skipped when is_followup=True.
    mue_details: Optional[str] = None,
    # Product/excursion fields for the new template (see
    # attachment_extraction_service.py) — render as "Not provided" when absent.
    strength: Optional[str] = None,
    dosage_form: Optional[str] = None,
    ndc: Optional[str] = None,
    lot_number: Optional[str] = None,
    expiration_date: Optional[str] = None,
    quantity_affected: Optional[str] = None,
    excursion_details: Optional[str] = None,
    temperature_range: Optional[str] = None,
    duration: Optional[str] = None,
    num_excursions: Optional[str] = None,
) -> tuple[str, str]:
    """Return (plain_text, html) tuple for the email body.

    is_followup=True keeps the ORIGINAL, unchanged behavior (greeting +
    "FOLLOW-UP MESSAGE:" + the free-text follow-up body + signature) — see
    routers.inquiries.send_followup_email. That branch does not use any of
    the new excursion/product fields and is untouched by the template
    below.

    is_followup=False renders the client-approved stability-excursion
    template: greeting, two fixed explanatory paragraphs, a "Temperature
    excursion details" section, a "Product information" section, and the
    signature. Health System name (`team_name`) appears in both the
    greeting and the signature, reusing the same field/logic as before.
    `question` (the pharmacist's free-text description) is rendered under
    "Additional details" in the Temperature excursion details section
    rather than dropped, so no existing inquiry data is lost.
    `medication_name` -> Drug name, `manufacturer_name` -> Manufacturer,
    and `pi_storage_data` -> Additional product information reuse fields
    that were already being passed into this function. Every other
    product/excursion field has no existing structured source and renders
    as "Not provided" unless attachment_extraction_service supplied one.
    """
    import html as html_lib

    team = (team_name or "").strip() or None
    drug_name = (medication_name or "").strip() or None

    if is_followup:
        greeting_lead = "Hello, this is a follow-up regarding a drug information request from a pharmacist"
        body_label = "FOLLOW-UP MESSAGE"

        greeting_plain = f"{greeting_lead} at {team}." if team else f"{greeting_lead}."
        greeting_html = (
            f"<p>{greeting_lead} at {html_lib.escape(team)}.</p>" if team else f"<p>{greeting_lead}.</p>"
        )

        sig_lines_plain = _sig_lines(team)
        sig_lines_html = _sig_lines(team, escape=html_lib.escape)
        signature_plain = "\n".join(sig_lines_plain)
        signature_html = "<p>" + "<br>\n".join(sig_lines_html) + "</p>"

        drug_name_line_plain = f"Drug Name: {drug_name}\n\n" if drug_name else ""
        drug_name_line_html = (
            f"<p><strong>Drug Name:</strong> {html_lib.escape(drug_name)}</p>\n" if drug_name else ""
        )

        plain = f"""\
{greeting_plain}

{drug_name_line_plain}{body_label}:
{question}

{signature_plain}
"""

        html = f"""\
<html><body style="font-family:Arial,sans-serif;font-size:14px;color:#222;line-height:1.6;">
{greeting_html}
{drug_name_line_html}<p><strong>{body_label}:</strong></p>
<p>{html_lib.escape(question).replace(chr(10), '<br>')}</p>
{signature_html}
</body></html>
"""
        return plain, html

    # --- Stability-excursion template (new inquiries; is_followup=False) ---
    NOT_PROVIDED = "Not provided"
    fv = lambda v: (v or "").strip() or NOT_PROVIDED  # noqa: E731

    greeting_plain = (
        f"Hello, I am a pharmacist at {team} writing to request stability information "
        "regarding a temperature excursion for one of our medications."
        if team
        else "Hello, I am a pharmacist writing to request stability information "
        "regarding a temperature excursion for one of our medications."
    )
    greeting_html = f"<p>{html_lib.escape(greeting_plain)}</p>"

    sig_lines_plain = _sig_lines(team)
    sig_lines_html = _sig_lines(team, escape=html_lib.escape)
    signature_plain = "\n".join(sig_lines_plain)
    signature_html = "<p>" + "<br>\n".join(sig_lines_html) + "</p>"

    mue_text = (mue_details or "").strip() or None
    additional_details_value = f"{question}\n{mue_text}" if mue_text else question

    excursion_fields = [
        ("Excursion(s)", fv(excursion_details)),
        ("Temperature range", fv(temperature_range)),
        ("Duration", fv(duration)),
        ("Number of excursion events", fv(num_excursions)),
        ("Additional details", fv(additional_details_value)),
    ]
    product_fields = [
        ("Drug name", fv(drug_name)),
        ("Strength", fv(strength)),
        ("Dosage form", fv(dosage_form)),
        ("Manufacturer", fv(manufacturer_name)),
        ("NDC", fv(ndc)),
        ("Lot number", fv(lot_number)),
        ("Expiration date", fv(expiration_date)),
        ("Quantity affected (if applicable)", fv(quantity_affected)),
        ("Additional product information", fv(pi_storage_data)),
    ]

    excursion_plain = "\n".join(f"- {label}: {value}" for label, value in excursion_fields)
    product_plain = "\n".join(f"- {label}: {value}" for label, value in product_fields)
    excursion_html = "\n".join(
        f"<li><strong>{label}:</strong> {html_lib.escape(value).replace(chr(10), '<br>')}</li>"
        for label, value in excursion_fields
    )
    product_html = "\n".join(
        f"<li><strong>{label}:</strong> {html_lib.escape(value)}</li>" for label, value in product_fields
    )

    plain = f"""\
{greeting_plain}

Could you please review the information below and provide any available stability data or \
recommendations for the reported excursion? Specifically, based on the information provided, \
can the product continue to be used, or should it be discarded? If available, please include \
any supporting stability data, internal studies, validation data, or manufacturer \
recommendations related to this excursion.

If additional information is needed to complete your assessment, please let me know.

Temperature excursion details
{excursion_plain}

Product information
{product_plain}

{signature_plain}
"""

    html = f"""\
<html><body style="font-family:Arial,sans-serif;font-size:14px;color:#222;line-height:1.6;">
{greeting_html}
<p>Could you please review the information below and provide any available stability data or \
recommendations for the reported excursion? Specifically, based on the information provided, \
can the product continue to be used, or should it be discarded? If available, please include \
any supporting stability data, internal studies, validation data, or manufacturer \
recommendations related to this excursion.</p>
<p>If additional information is needed to complete your assessment, please let me know.</p>
<p><strong>Temperature excursion details</strong></p>
<ul>
{excursion_html}
</ul>
<p><strong>Product information</strong></p>
<ul>
{product_html}
</ul>
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
    is_followup: bool = False,
    mue_details: Optional[str] = None,
    strength: Optional[str] = None,
    dosage_form: Optional[str] = None,
    ndc: Optional[str] = None,
    lot_number: Optional[str] = None,
    expiration_date: Optional[str] = None,
    quantity_affected: Optional[str] = None,
    excursion_details: Optional[str] = None,
    temperature_range: Optional[str] = None,
    duration: Optional[str] = None,
    num_excursions: Optional[str] = None,
) -> str:
    """Send the inquiry email via the SendGrid API.

    Returns SendGrid's X-Message-Id header (used to correlate replies / events).
    Replies are routed back to EMAIL_FROM so IMAP polling can capture them.

    is_followup=True renders `question` as a follow-up message rather than
    the original inquiry (see _build_body) — used by
    routers.inquiries.send_followup_email. Default False, unchanged for
    every other existing caller.

    strength/dosage_form/ndc/lot_number/expiration_date/quantity_affected/
    excursion_details/temperature_range/duration/num_excursions are all
    optional pass-throughs to _build_body's stability-excursion template —
    see attachment_extraction_service.py for how callers obtain them.
    Every existing caller that doesn't pass these keeps sending the exact
    same "Not provided" placeholders it always has (no behavior change
    unless a caller explicitly supplies real values).
    """
    cfg = SendGridConfig.from_env()

    # Sent verbatim — the caller already guarantees the tag (see
    # routers.inquiries._with_subject_tag); don't rebuild it or user edits are lost.
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
        is_followup=is_followup,
        mue_details=mue_details,
        strength=strength,
        dosage_form=dosage_form,
        ndc=ndc,
        lot_number=lot_number,
        expiration_date=expiration_date,
        quantity_affected=quantity_affected,
        excursion_details=excursion_details,
        temperature_range=temperature_range,
        duration=duration,
        num_excursions=num_excursions,
    )

    payload = {
        "personalizations": [
            {
                "to": [{"email": to_email}],
                "cc": [{"email": "Leah@inpharmd.com"}],
                "bcc": [
                    {"email": "tulsee@inpharmd.com"},
                    {"email": "chinna@inpharmd.com"},
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
