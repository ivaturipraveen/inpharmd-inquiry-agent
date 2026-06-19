"""POST manufacturer responses back to the InpharmD legacy endpoint.

When a manufacturer replies to an inquiry that originated from the InpharmD
platform (i.e. it has a `source_inquiry_uuid`), we send the response back so
the legacy platform can attach it to its own record.

Endpoint (Rails on Heroku):
    POST {INPHARMD_API_BASE_URL}/api/legacy/manufacturing_response
    Header: X-Api-Key: {LEGACY_RESPONSE_API_KEY}
    Content-Type: multipart/form-data
    Form fields:
        inquiry_uuid       str   (required)
        mfr_email_response str   (required)
        mfr_s3_url         str   (optional — direct S3 URL of the attachment;
                                  legacy fetches from S3 itself, so we don't
                                  have to download + re-upload)

The base URL is the SAME as the rest of the InpharmD APIs — we reuse
`INPHARMD_API_BASE_URL` (defined in inpharmd_service) so there's a single
knob for switching environments. Only the X-Api-Key is new and lives in
LEGACY_RESPONSE_API_KEY.
"""
from __future__ import annotations

import logging
import os
import time
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
    mfr_s3_url: Optional[str] = None,
) -> bool:
    """POST the response back to legacy as multipart/form-data.

    `mfr_s3_url` is the direct S3 URL of the PDF attachment — legacy fetches
    from S3 itself, so we don't have to download and re-upload it. Pass None
    when there's no attachment.

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

    # Multipart form fields — Rails endpoint requires multipart even when
    # there's no file part (it rejects pure JSON with 500).
    data = {
        "inquiry_uuid": inquiry_uuid,
        "mfr_email_response": mfr_email_response or "",
    }
    if mfr_s3_url:
        data["mfr_s3_url"] = mfr_s3_url
    log.info(
        "pipeline: legacy POST sending uuid=%s response_chars=%d has_s3_url=%s",
        inquiry_uuid,
        len(mfr_email_response or ""),
        bool(mfr_s3_url),
    )

    # Empty `files` dict forces httpx to use multipart encoding even without
    # an actual file part — keeps the wire format identical regardless of
    # whether an attachment URL is present.
    headers = {"X-Api-Key": key, "Accept": "application/json"}

    last_status = None
    last_body = ""
    for attempt in range(MAX_RETRIES + 1):
        started = time.monotonic()
        try:
            with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
                res = client.post(url, headers=headers, data=data, files={})
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
    if getattr(inquiry, "legacy_response_posted_at", None) is not None:
        log.info(
            "pipeline: legacy POST skipped for inquiry %s: already posted at %s (writeback will still run if not yet posted)",
            inquiry.id,
            inquiry.legacy_response_posted_at,
        )
        # NB: original behavior was to return False here without writeback.
        # We preserve that for safety — writeback re-runs are gated by its
        # own excel_response_posted_at check, but we don't want to introduce
        # a behavior change in this logging-only PR.
        return False

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

    s3_url = getattr(inquiry, "pdf_url", None) or None

    ok = post_response(
        inquiry_uuid=uuid,
        mfr_email_response=response_text,
        mfr_s3_url=s3_url,
    )
    if ok:
        inquiry.legacy_response_posted_at = datetime.now(timezone.utc)
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
