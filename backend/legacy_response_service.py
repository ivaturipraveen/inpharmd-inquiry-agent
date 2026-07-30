"""POST manufacturer responses back to the InpharmD legacy endpoint.

When a manufacturer replies to an inquiry that originated from the InpharmD
platform (i.e. it has a `source_inquiry_uuid`), we send the response back so
the legacy platform can attach it to its own record.

Endpoint (Rails on Heroku):
    POST {INPHARMD_API_BASE_URL}/api/legacy/manufacturing_response
    Header: X-Api-Key: {LEGACY_RESPONSE_API_KEY}
    Content-Type: application/x-www-form-urlencoded
    Form fields:
        inquiry_uuid       str      (required)
        mfr_email_response str      (required)
        mfr_s3_url[]       str[]    (optional — one field per attachment URL;
                                     Rails receives params[:mfr_s3_url] as an
                                     array; legacy fetches from S3 itself)

The base URL is the SAME as the rest of the InpharmD APIs — we reuse
`INPHARMD_API_BASE_URL` (defined in inpharmd_service) so there's a single
knob for switching environments. Only the X-Api-Key is new and lives in
LEGACY_RESPONSE_API_KEY.
"""
from __future__ import annotations

import logging
import os
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy.orm import Session

import inpharmd_service  # reuse the same base-URL resolution

log = logging.getLogger("inquiry.legacy_response")

LEGACY_PATH = "/api/legacy/manufacturing_response"

TIMEOUT_SECONDS = 15.0
MAX_RETRIES = 2
RETRY_BACKOFF = (1.0, 3.0)
RETRY_STATUSES = {408, 425, 429, 500, 502, 503, 504}


def _config() -> tuple[str, Optional[str]]:
    url = inpharmd_service._base_url() + LEGACY_PATH
    key = (os.getenv("LEGACY_RESPONSE_API_KEY") or "").strip() or None
    return url, key


def is_configured() -> bool:
    url, key = _config()
    return bool(url and key)


def post_response(
    *,
    inquiry_uuid: str,
    mfr_email_response: str,
    mfr_s3_urls: Optional[list] = None,
    manufacturer_name: Optional[str] = None,
    medication_name: Optional[str] = None,
) -> bool:
    """POST the response back to legacy as multipart/form-data.

    `mfr_s3_urls` is a list of direct S3 URLs for all attachments — legacy
    fetches from S3 itself. Each URL is sent as a separate `mfr_s3_url[]`
    field so Rails receives them as an array. Pass None or [] when there are
    no attachments.

    Returns True on 2xx, False otherwise. Never raises — failures are
    logged and the inquiry flow continues.
    """
    url, key = _config()
    if not key:
        log.warning(
            "Legacy response post skipped: LEGACY_RESPONSE_API_KEY not set "
            "(uuid=%s)",
            inquiry_uuid,
        )
        log.info(
            "pipeline: legacy POST SKIPPED uuid=%s reason=LEGACY_RESPONSE_API_KEY not set",
            inquiry_uuid,
        )
        return False

    urls = [u for u in (mfr_s3_urls or []) if u]

    # Build as a list of (name, value) tuples so urllib.parse.urlencode can
    # emit repeated mfr_s3_url[] fields — Rails parses these as an array.
    # We encode manually and pass as raw `content` so httpx doesn't touch
    # the field names (httpx's dict/files encoding doesn't support repeated
    # keys with [] suffix reliably across versions).
    params = [
        ("inquiry_uuid", inquiry_uuid),
        ("mfr_email_response", mfr_email_response or ""),
    ]
    if manufacturer_name:
        params.append(("manufacturer_name", manufacturer_name))
    if medication_name:
        params.append(("medication_name", medication_name))
    for s3_url in urls:
        params.append(("mfr_s3_url[]", s3_url))

    encoded_body = urllib.parse.urlencode(params).encode("utf-8")

    log.info(
        "pipeline: legacy POST sending uuid=%s response_chars=%d s3_urls=%d manufacturer=%s medication=%s",
        inquiry_uuid,
        len(mfr_email_response or ""),
        len(urls),
        manufacturer_name or "(none)",
        medication_name or "(none)",
    )

    headers = {
        "X-Api-Key": key,
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    last_status = None
    last_body = ""
    for attempt in range(MAX_RETRIES + 1):
        started = time.monotonic()
        try:
            with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
                res = client.post(url, headers=headers, content=encoded_body)
        except httpx.TimeoutException as e:
            elapsed = (time.monotonic() - started) * 1000
            log.warning(
                "Legacy POST %s timed out after %.0fms (attempt %d/%d): %s",
                url, elapsed, attempt + 1, MAX_RETRIES + 1, e,
            )
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)])
                continue
            return False
        except httpx.HTTPError as e:
            elapsed = (time.monotonic() - started) * 1000
            log.error(
                "Legacy POST %s network error after %.0fms: %s",
                url, elapsed, e,
            )
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)])
                continue
            return False

        elapsed = (time.monotonic() - started) * 1000
        last_status = res.status_code
        last_body = (res.text or "")[:300]

        if res.is_success:
            log.info(
                "✓ Legacy POST uuid=%s status=%s elapsed=%.0fms attempt=%d",
                inquiry_uuid, res.status_code, elapsed, attempt + 1,
            )
            return True

        if res.status_code in RETRY_STATUSES and attempt < MAX_RETRIES:
            log.warning(
                "↻ Legacy POST uuid=%s status=%s (retrying %d/%d): %s",
                inquiry_uuid, res.status_code, attempt + 2, MAX_RETRIES + 1, last_body,
            )
            time.sleep(RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)])
            continue

        log.error(
            "✗ Legacy POST uuid=%s status=%s body=%s",
            inquiry_uuid, res.status_code, last_body,
        )
        return False

    log.error(
        "✗ Legacy POST uuid=%s exhausted retries last_status=%s body=%s",
        inquiry_uuid, last_status, last_body,
    )
    return False


def maybe_post_for_inquiry(db: Session, inquiry) -> bool:
    """Idempotent wrapper that decides whether to POST for the given Inquiry.

    Sends only when:
    - Source UUID is set (forwarded from InpharmD)
    - There's a final_answer or email_response to send
    - It hasn't already been posted (legacy_response_posted_at is None)

    On success, stamps legacy_response_posted_at and commits.
    Returns True if posted, False otherwise.
    """
    log.info(
        "pipeline: maybe_post_for_inquiry inquiry=%s status=%s source_uuid=%s",
        inquiry.id,
        getattr(inquiry, "status", None),
        (getattr(inquiry, "source_inquiry_uuid", None) or "")[:12] or "(none)",
    )
    uuid = (getattr(inquiry, "source_inquiry_uuid", None) or "").strip()
    if not uuid:
        log.info(
            "pipeline: legacy POST + writeback skipped for inquiry %s: no source_inquiry_uuid (not a MUE inquiry)",
            inquiry.id,
        )
        return False

    # Re-load the inquiry with a row-level lock so concurrent callers
    # (Graph poll + SendGrid webhook arriving simultaneously) cannot both
    # pass the already_posted check and double-POST to the legacy API.
    from models import Inquiry as InquiryModel
    locked = db.query(InquiryModel).with_for_update().filter(
        InquiryModel.id == inquiry.id
    ).first()
    if locked is None:
        return False
    inquiry = locked

    response_text = (
        getattr(inquiry, "final_answer", None)
        or getattr(inquiry, "email_response", None)
        or getattr(inquiry, "call_summary", None)
        or ""
    ).strip()
    if not response_text:
        log.info(
            "pipeline: legacy POST skipped for inquiry %s: no response text yet",
            inquiry.id,
        )
        return False

    # Collect all InquiryAttachment URLs for this inquiry, ordered by
    # display_order.  Falls back to the scalar pdf_url for inquiries that
    # pre-date the InquiryAttachment table.
    from models import InquiryAttachment
    s3_urls = [
        att.url
        for att in db.query(InquiryAttachment)
            .filter(InquiryAttachment.inquiry_id == inquiry.id)
            .order_by(InquiryAttachment.display_order)
            .all()
        if att.url
    ]
    if not s3_urls:
        scalar = getattr(inquiry, "pdf_url", None) or None
        s3_urls = [scalar] if scalar else []

    # Allow re-posting when new attachments have arrived since the last POST.
    # Old code sent at most 1 URL; legacy_attachment_url_count defaults to 0
    # for those rows, so any non-empty attachment list triggers a re-post.
    already_posted = getattr(inquiry, "legacy_response_posted_at", None) is not None
    stored_count = getattr(inquiry, "legacy_attachment_url_count", 0) or 0
    if already_posted and len(s3_urls) <= stored_count:
        log.info(
            "pipeline: legacy POST skipped for inquiry %s: already posted at %s "
            "with %d URL(s), still have %d — no change",
            inquiry.id, inquiry.legacy_response_posted_at, stored_count, len(s3_urls),
        )
        return False
    if already_posted:
        log.info(
            "pipeline: re-posting legacy response for inquiry %s: "
            "previously sent %d URL(s), now have %d",
            inquiry.id, stored_count, len(s3_urls),
        )

    mfr = getattr(inquiry, "manufacturer", None)
    mfr_name = (mfr.manufacturer if mfr else None) or None
    med_name = (getattr(inquiry, "medication_name", None) or "").strip() or None

    ok = post_response(
        inquiry_uuid=uuid,
        mfr_email_response=response_text,
        mfr_s3_urls=s3_urls,
        manufacturer_name=mfr_name,
        medication_name=med_name,
    )
    if ok:
        inquiry.legacy_response_posted_at = datetime.now(timezone.utc)
        inquiry.legacy_attachment_url_count = len(s3_urls)
        try:
            db.commit()
        except Exception:
            log.exception(
                "Failed to commit legacy_response_posted_at for inquiry %s",
                inquiry.id,
            )
            db.rollback()

    # MUE inquiries with an Excel attachment also get the updated workbook
    # written + uploaded + POSTed under mfr_s3_url. This is an independent
    # idempotent op — failures here don't roll back the legacy POST above.
    try:
        # Local import to avoid circular module loading at boot.
        import excel_writeback_service
        excel_writeback_service.maybe_writeback_for_inquiry(db, inquiry)
    except Exception:
        log.exception(
            "Excel writeback failed for inquiry %s (continuing)", inquiry.id
        )
    return ok
