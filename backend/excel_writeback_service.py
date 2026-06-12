"""Excel response writeback for MUE inquiries.

When a manufacturer's email reply is captured for an inquiry that came from
an InpharmD MUE spreadsheet, this service:
  1. Downloads the latest version of the workbook (the per-inquiry
     `excel_response_url` if we already updated it once, else the original
     `source_excel_url`).
  2. Writes the response text into the "Manufacturer Response" column at
     that inquiry's saved row.
  3. Uploads the new workbook to our S3 and stamps `excel_response_url` on
     the inquiry.
  4. POSTs the new URL back to the InpharmD legacy endpoint so they ingest
     the updated workbook.

Idempotent — checks `excel_response_posted_at` before posting again.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy.orm import Session

import excel_service
import inpharmd_service
import s3_service
from models import Inquiry, User

log = logging.getLogger("inquiry.excel_writeback")

LEGACY_PATH = "/api/legacy/manufacturing_response"
_DOWNLOAD_TIMEOUT_SECONDS = 30


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _legacy_api_key() -> Optional[str]:
    key = (os.getenv("LEGACY_RESPONSE_API_KEY") or "").strip()
    return key or None


def _pick_latest_excel_url(db: Session, inquiry: Inquiry) -> str:
    """Return the URL of the most-recently-updated workbook copy for this
    MUE source. Falls back to the original source URL if no sibling has
    posted yet.

    Why: all inquiries forwarded from the same MUE Excel share
    `source_inquiry_uuid`. As each reply lands we mutate one row and upload
    a new copy to S3. The next reply must pick up THAT copy (with the prior
    edits) — not the pristine original — or it will overwrite siblings.
    """
    if inquiry.source_inquiry_uuid:
        latest = (
            db.query(Inquiry)
            .filter(Inquiry.source_inquiry_uuid == inquiry.source_inquiry_uuid)
            .filter(Inquiry.excel_response_url.isnot(None))
            .filter(Inquiry.excel_response_posted_at.isnot(None))
            .order_by(Inquiry.excel_response_posted_at.desc())
            .first()
        )
        if latest and latest.excel_response_url:
            return latest.excel_response_url
    return inquiry.excel_response_url or inquiry.source_excel_url


def _pick_user_token(db: Session, inquiry: Inquiry) -> Optional[str]:
    """Pick the staging access_token used to download the original Excel.
    Prefer the inquiry's owner; fall back to any user we have."""
    if inquiry.user_id:
        u = db.get(User, inquiry.user_id)
        if u and u.staging_token:
            return u.staging_token
    u = db.query(User).first()
    return u.staging_token if u else None


def _download(url: str, *, token: Optional[str]) -> bytes:
    params: dict[str, str] = {}
    # Only attach the staging token if the URL points back at InpharmD —
    # presigned S3 URLs (our own re-uploads) reject arbitrary query params.
    if token and ("inpharmd" in url or "mercer-inpharmd" in url) and "access_token=" not in url:
        params["access_token"] = token
    with httpx.Client(timeout=_DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=True) as client:
        res = client.get(url, params=params)
        res.raise_for_status()
        return res.content


def _post_to_legacy(*, inquiry_uuid: str, excel_url: str, response_text: str) -> bool:
    base = inpharmd_service._base_url().rstrip("/")
    url = base + LEGACY_PATH
    key = _legacy_api_key()
    if not key:
        log.info("excel_writeback: no LEGACY_RESPONSE_API_KEY; skipping legacy POST")
        return False
    headers = {"X-Api-Key": key, "Accept": "application/json"}
    data = {
        "inquiry_uuid": inquiry_uuid,
        "mfr_email_response": response_text or "",
        "mfr_s3_url": excel_url,
    }
    log.info("excel_writeback: POST legacy uuid=%s url=%s", inquiry_uuid, excel_url)
    try:
        with httpx.Client(timeout=20) as client:
            # multipart so the Rails endpoint accepts our payload
            res = client.post(url, headers=headers, data=data, files={})
        if res.status_code >= 400:
            log.error("excel_writeback: legacy POST failed %s: %s", res.status_code, res.text[:300])
            return False
        log.info("excel_writeback: legacy POST ok %s", res.status_code)
        return True
    except Exception as e:
        log.exception("excel_writeback: legacy POST error: %s", e)
        return False


def maybe_writeback_for_inquiry(db: Session, inquiry: Inquiry) -> bool:
    """Idempotent writeback. Returns True if we updated the Excel + posted."""
    if not inquiry.source_excel_url or not inquiry.source_excel_row:
        return False
    response_text = (inquiry.email_response or inquiry.final_answer or "").strip()
    if not response_text:
        log.debug("excel_writeback: inquiry %s has no response text yet", inquiry.id)
        return False
    if inquiry.excel_response_posted_at is not None:
        log.debug("excel_writeback: inquiry %s already posted", inquiry.id)
        return False
    if not inquiry.source_inquiry_uuid:
        log.info("excel_writeback: inquiry %s has no source_inquiry_uuid; cannot legacy-post", inquiry.id)
        return False

    # Latest version wins — but "latest" is across ALL siblings sharing this
    # source_inquiry_uuid (one MUE Excel forwarded to N manufacturers). If we
    # only looked at this inquiry's own excel_response_url, later responses
    # would download the original (sans earlier sibling edits) and clobber
    # them on re-upload.
    base_url = _pick_latest_excel_url(db, inquiry)
    token = _pick_user_token(db, inquiry)
    try:
        xlsx_bytes = _download(base_url, token=token)
    except Exception as e:
        log.error("excel_writeback: failed to download %s: %s", base_url, e)
        return False

    try:
        updated_bytes = excel_service.write_response(
            xlsx_bytes,
            row_index=int(inquiry.source_excel_row),
            response_text=response_text,
            sheet_name=inquiry.source_excel_sheet,
        )
    except Exception as e:
        log.error("excel_writeback: failed to write response into row %s: %s",
                  inquiry.source_excel_row, e)
        return False

    # Upload the new copy to our S3.
    file_name = (
        f"inquiry-{inquiry.source_inquiry_uuid[:8]}-row{inquiry.source_excel_row}.xlsx"
    )
    new_url = s3_service.upload_bytes(
        updated_bytes,
        original_name=file_name,
        inquiry_id=inquiry.id,
        content_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        prefix="mue-responses",
    )
    if not new_url:
        log.error("excel_writeback: S3 upload returned no url for inquiry %s", inquiry.id)
        return False

    # Tell the platform the new file is available.
    posted = _post_to_legacy(
        inquiry_uuid=inquiry.source_inquiry_uuid,
        excel_url=new_url,
        response_text=response_text,
    )

    inquiry.excel_response_url = new_url
    if posted:
        inquiry.excel_response_posted_at = _now()
    db.commit()
    return posted
