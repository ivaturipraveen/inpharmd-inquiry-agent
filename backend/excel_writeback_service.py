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
  4. POSTs the new URL to the InpharmD v2 `/sheet` endpoint so they ingest
     the updated workbook.

Idempotent — checks `excel_response_posted_at` before posting again.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy.orm import Session

import excel_service
import inpharmd_service
import s3_service
from models import Inquiry, User

log = logging.getLogger("inquiry.excel_writeback")

# POST /api/v2/inquiries/open_mue_inquiries/{inquiry_uuid}/sheet
#   ?access_token=<user staging token>
#   form field: s3_url=<re-uploaded xlsx URL>
V2_SHEET_PATH_TEMPLATE = "/api/v2/inquiries/open_mue_inquiries/{uuid}/sheet"
_DOWNLOAD_TIMEOUT_SECONDS = 30


def _now() -> datetime:
    return datetime.now(timezone.utc)


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
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    final_url = url
    # Inject access_token ONLY for InpharmD's app host (heroku/localhost).
    # Skip *.amazonaws.com — presigned URLs depend on the exact query string
    # so merging extra params via httpx clobbers AWSAccessKeyId/Signature
    # /Expires and the request 403s. Our own bucket inpharmd-assistant.s3
    # matches the loose "inpharmd in host" test, which is the bug this fixes.
    if (
        token
        and not host.endswith(".amazonaws.com")
        and ("inpharmd" in host or "mercer-inpharmd" in host)
        and "access_token=" not in url
    ):
        sep = "&" if "?" in url else "?"
        final_url = f"{url}{sep}access_token={token}"
    with httpx.Client(timeout=_DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=True) as client:
        res = client.get(final_url)
        res.raise_for_status()
        return res.content


def _post_v2_sheet(*, inquiry_uuid: str, excel_url: str, access_token: str) -> bool:
    """POST the updated workbook URL to the v2 open_mue_inquiries/{uuid}/sheet
    endpoint. Auth is the per-user staging access_token (query param), payload
    is multipart form with a single `s3_url` field — matches the API doc."""
    base = inpharmd_service._base_url().rstrip("/")
    url = base + V2_SHEET_PATH_TEMPLATE.format(uuid=inquiry_uuid)
    params = {"access_token": access_token}
    # multipart form (files={} alone would make this an empty multipart;
    # passing data + files={} forces multipart/form-data over urlencoded).
    data = {"s3_url": excel_url}
    log.info(
        "excel_writeback: POST v2 sheet uuid=%s url=%s",
        inquiry_uuid,
        excel_url,
    )
    try:
        with httpx.Client(timeout=20) as client:
            res = client.post(
                url,
                params=params,
                data=data,
                files={},
                headers={"Accept": "application/json"},
            )
        if res.status_code >= 400:
            log.error(
                "excel_writeback: v2 sheet POST failed %s: %s",
                res.status_code,
                res.text[:300],
            )
            return False
        log.info("excel_writeback: v2 sheet POST ok %s", res.status_code)
        return True
    except Exception as e:
        log.exception("excel_writeback: v2 sheet POST error: %s", e)
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
    if not token:
        log.info(
            "excel_writeback: no staging access_token for inquiry %s; cannot v2-post",
            inquiry.id,
        )
        return False
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

    # Upload the updated workbook to a SINGLE deterministic key per MUE
    # inquiry. Every reply for the same source_inquiry_uuid overwrites the
    # same object — one file in S3 per MUE inquiry, not N.
    # The presigned URL returned here changes per call (fresh signature),
    # but it always points at the same key holding the latest cumulative
    # state. Format-aware: a CSV source stays a CSV on the way out.
    fmt = "csv" if updated_bytes[:4] != b"PK\x03\x04" else "xlsx"
    if fmt == "csv":
        ext = "csv"
        ct = "text/csv"
    else:
        ext = "xlsx"
        ct = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    file_name = f"inquiry-{inquiry.source_inquiry_uuid}.{ext}"
    new_url = s3_service.upload_bytes(
        updated_bytes,
        original_name=file_name,
        inquiry_id=inquiry.id,
        content_type=ct,
        key_override=f"mue-responses/{file_name}",
    )
    if not new_url:
        log.error("excel_writeback: S3 upload returned no url for inquiry %s", inquiry.id)
        return False

    # Tell the platform the new file is available.
    posted = _post_v2_sheet(
        inquiry_uuid=inquiry.source_inquiry_uuid,
        excel_url=new_url,
        access_token=token,
    )

    inquiry.excel_response_url = new_url
    if posted:
        inquiry.excel_response_posted_at = _now()
    db.commit()
    return posted
