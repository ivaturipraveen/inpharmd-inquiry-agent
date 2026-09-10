"""Best-effort, on-demand extraction of temperature-excursion / product
fields from an inquiry's original platform attachment(s), for the outbound
stability-excursion manufacturer email (see email_service.py).

Deliberately isolated from every other attachment mechanism in this app:
- Does NOT touch InquiryAttachment, S3, or inbound_attachment_service.py —
  those belong exclusively to inbound manufacturer-reply capture
  (graph_service.py / imap_service.py / routers/email_inbound.py).
- Does NOT touch excel_service.py's MUE workbook parsing or
  dailymed_service.py's PI enrichment — both are unrelated, unchanged.
- Nothing here is persisted except the final result cache on the Inquiry
  row (see models.Inquiry.attachment_extraction_cache), and only on a
  clean success (including a clean "nothing found" result) — never on a
  timeout or technical failure, so a transient issue doesn't permanently
  block retrying on the inquiry's next send attempt.
- No concurrency (no thread pools/executors): every I/O call below has its
  own explicit, short, hard timeout, enforced by the underlying library
  (httpx / the OpenAI SDK) on the same synchronous call stack the caller
  is already running on — nothing is ever left running in the background.
- All text-extractable attachments on the inquiry are processed — no fixed
  count cap. Total added latency therefore scales with the number of
  attachments (each still individually bounded by _DOWNLOAD_TIMEOUT_SECONDS,
  and one attachment's failure never blocks the others or the LLM call).
"""
from __future__ import annotations

import json
import logging
from typing import Optional

import httpx

import summary_service
from models import Inquiry, User

log = logging.getLogger("inquiry.attachment_extraction")

_DOWNLOAD_TIMEOUT_SECONDS = 10
# Only types extract_document_text() can turn into text — images excluded
# (no OCR/vision anywhere in this app).
_TEXT_EXTRACTABLE_EXTENSIONS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv")

_EMPTY_FIELDS = {
    "excursion_details": "", "temperature_range": "", "duration": "",
    "num_excursions": "", "strength": "", "dosage_form": "", "ndc": "",
    "lot_number": "", "expiration_date": "", "quantity_affected": "",
}


def _extractable_attachments(source_attachments_json: Optional[str]) -> list[dict]:
    """All attachments on the inquiry whose file type can be turned into
    text (see _TEXT_EXTRACTABLE_EXTENSIONS) — no count limit. Unsupported
    types (images, anything else extract_document_text can't handle) are
    filtered out here, not truncated by position."""
    if not source_attachments_json:
        return []
    try:
        items = json.loads(source_attachments_json)
    except (ValueError, TypeError):
        return []
    if not isinstance(items, list):
        return []
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = (item.get("file_name") or "").lower()
        if name.endswith(_TEXT_EXTRACTABLE_EXTENSIONS) and item.get("doc_url"):
            out.append(item)
    return out


def _download(doc_url: str, access_token: Optional[str]) -> bytes:
    """Fetch attachment bytes with a short, hard timeout. Appending
    access_token as a query param (harmless no-op for a public/S3 URL) is
    what lets a background scheduler tick — which has no live user
    session — still authenticate against a protected InpharmD-hosted URL,
    using the same permanently-stored User.staging_token the interactive
    request path already relies on (see routers/auth.py)."""
    final_url = doc_url
    if access_token and "access_token=" not in doc_url:
        sep = "&" if "?" in doc_url else "?"
        final_url = f"{doc_url}{sep}access_token={access_token}"
    with httpx.Client(timeout=_DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=True) as client:
        resp = client.get(final_url)
        resp.raise_for_status()
        return resp.content


def get_or_extract(db, obj: Inquiry, *, manufacturer_name: Optional[str] = None) -> dict:
    """Return the 10 structured fields for obj's stability-excursion email.

    Checks the persisted cache first (never re-downloads/re-calls the LLM
    once a clean result — success or "nothing found" — already exists).
    On any failure anywhere in the pipeline (no attachments, download
    error, extraction error, LLM/timeout error, malformed response), logs
    and returns an all-empty dict WITHOUT writing the cache, so the next
    send attempt for this inquiry gets a fresh attempt. Never raises —
    the caller (email_service via routers.inquiries/scheduler) must be
    able to proceed with the send regardless of what happens here.

    manufacturer_name should be the already-resolved ManufacturerContact
    name (the caller already looks this up before sending) — passed
    through, along with obj.medication_name, as disambiguating context to
    summary_service.extract_structured_excursion_fields so a shared,
    multi-product attachment (e.g. a pharmacy-wide MUE tracking sheet)
    resolves to the correct row(s) instead of the model declining to guess
    among many unrelated products.
    """
    if obj.attachment_extraction_cache:
        try:
            cached = json.loads(obj.attachment_extraction_cache)
            if isinstance(cached, dict):
                return {**_EMPTY_FIELDS, **cached}
        except (ValueError, TypeError):
            pass  # fall through and re-extract if the cached value is somehow corrupt

    attachments = _extractable_attachments(obj.source_attachments_json)
    if not attachments:
        return dict(_EMPTY_FIELDS)

    try:
        owner = db.get(User, obj.user_id) if obj.user_id else None
        access_token = owner.staging_token if owner else None

        text_parts = [obj.question or ""]
        for att in attachments:
            try:
                raw = _download(att["doc_url"], access_token)
                text = summary_service.extract_document_text(att.get("file_name") or "", raw)
                if text:
                    text_parts.append(text)
            except Exception:
                log.warning(
                    "attachment_extraction: skipping unreadable attachment %s for inquiry %s",
                    att.get("file_name"), obj.id, exc_info=True,
                )
                continue  # one bad attachment must not abort the others

        combined = "\n\n".join(p for p in text_parts if p)
        fields = summary_service.extract_structured_excursion_fields(
            combined,
            drug_name=obj.medication_name,
            manufacturer_name=manufacturer_name,
        )

        obj.attachment_extraction_cache = json.dumps(fields)
        db.commit()
        return fields
    except Exception:
        log.warning(
            "attachment_extraction: extraction failed for inquiry %s (email will send without it)",
            obj.id, exc_info=True,
        )
        db.rollback()
        return dict(_EMPTY_FIELDS)
