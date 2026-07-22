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


def _mue_response_file_name(source_inquiry_uuid: str, ext: str) -> str:
    """Deterministic S3 object name — one file per MUE source inquiry."""
    short = (source_inquiry_uuid or "").strip()[:8]
    return f"inquiry-{short}.{ext}"


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


def writeback_pi_enrichment(
    xlsx_bytes: bytes,
    matches: list,
    *,
    inquiry_uuid: str,
    access_token: str,
) -> Optional[str]:
    """Write DailyMed PI link + storage data into the workbook for enriched rows,
    upload to S3 under mue-pi-enriched/, and POST to staging. Returns the new
    S3 URL on success, or None if there was nothing to write or a step failed.

    Called once per extract-manufacturers request, right after enrich_rows.
    Does NOT touch any Inquiry DB rows — operates purely on bytes + uuid."""
    pi_rows = [
        (m.row_index, getattr(m, "pi_link", None) or None, getattr(m, "pi_storage", None) or None)
        for m in matches
        if (getattr(m, "pi_link", None) or getattr(m, "pi_storage", None))
    ]
    if not pi_rows:
        log.info("pi_writeback: no enriched rows for uuid=%s — skipping", (inquiry_uuid or "")[:8])
        return None

    short = (inquiry_uuid or "").strip()[:8]
    log.info("pi_writeback: writing PI data for %d rows uuid=%s", len(pi_rows), short)

    try:
        updated_bytes = excel_service.write_pi_data(xlsx_bytes, pi_rows)
    except Exception as e:
        log.error("pi_writeback: write failed uuid=%s: %s", short, e)
        return None

    fmt = "xlsx" if updated_bytes[:4] == b"PK\x03\x04" else "csv"
    ext = fmt
    ct = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if fmt == "xlsx"
        else "text/csv"
    )
    file_name = f"inquiry-{short}.{ext}"
    key = f"mue-pi-enriched/{file_name}"
    log.info("pi_writeback: uploading uuid=%s key=%s", short, key)
    new_url = s3_service.upload_bytes(
        updated_bytes,
        original_name=file_name,
        inquiry_id=0,
        content_type=ct,
        key_override=key,
    )
    if not new_url:
        log.error("pi_writeback: S3 upload failed for uuid=%s", short)
        return None
    log.info("pi_writeback: uploaded uuid=%s url=%s", short, new_url[:200])

    posted = _post_v2_sheet(
        inquiry_uuid=inquiry_uuid,
        excel_url=new_url,
        access_token=access_token,
    )
    if posted:
        log.info("pi_writeback: v2 POST ok uuid=%s", short)
    else:
        log.warning(
            "pi_writeback: v2 POST failed uuid=%s — workbook uploaded to %s but staging rejected it",
            short, new_url[:200],
        )

    return new_url


def maybe_writeback_for_inquiry(db: Session, inquiry: Inquiry) -> bool:
    """Idempotent writeback. Returns True if we updated the Excel + posted."""
    log.info(
        "pipeline: writeback maybe_writeback_for_inquiry inquiry=%s "
        "source_excel_url=%s source_excel_row=%s source_uuid=%s "
        "already_posted=%s",
        inquiry.id,
        "yes" if inquiry.source_excel_url else "no",
        inquiry.source_excel_row,
        (inquiry.source_inquiry_uuid or "")[:12] or "(none)",
        "yes" if inquiry.excel_response_posted_at is not None else "no",
    )
    if not inquiry.source_excel_url or not inquiry.source_excel_row:
        log.info(
            "pipeline: writeback skipped for inquiry %s: not a MUE inquiry "
            "(missing source_excel_url or source_excel_row)",
            inquiry.id,
        )
        return False
    response_text = (inquiry.email_response or inquiry.final_answer or "").strip()
    if not response_text:
        log.info(
            "pipeline: writeback skipped for inquiry %s: no response text yet",
            inquiry.id,
        )
        return False
    if inquiry.excel_response_posted_at is not None:
        log.info(
            "pipeline: writeback skipped for inquiry %s: already posted at %s (idempotent)",
            inquiry.id, inquiry.excel_response_posted_at,
        )
        return False
    if not inquiry.source_inquiry_uuid:
        log.info(
            "pipeline: writeback skipped for inquiry %s: no source_inquiry_uuid; cannot v2-post",
            inquiry.id,
        )
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
            "pipeline: writeback skipped for inquiry %s: no staging access_token on owning user; cannot v2-post",
            inquiry.id,
        )
        return False
    log.info(
        "pipeline: writeback step 1/4 (download) inquiry=%s base_url=%s",
        inquiry.id, base_url,
    )
    try:
        xlsx_bytes = _download(base_url, token=token)
    except Exception as e:
        log.error("pipeline: writeback download FAILED for inquiry %s url=%s: %s",
                  inquiry.id, base_url, e)
        return False
    fmt = "xlsx" if xlsx_bytes[:4] == b"PK\x03\x04" else "csv"
    log.info(
        "pipeline: writeback step 1/4 OK inquiry=%s downloaded=%d bytes detected_format=%s",
        inquiry.id, len(xlsx_bytes), fmt,
    )

    log.info(
        "pipeline: writeback step 2/4 (write) inquiry=%s row=%s sheet=%s",
        inquiry.id, inquiry.source_excel_row, inquiry.source_excel_sheet or "(default)",
    )
    try:
        updated_bytes = excel_service.write_response(
            xlsx_bytes,
            row_index=int(inquiry.source_excel_row),
            response_text=response_text,
            sheet_name=inquiry.source_excel_sheet,
        )
    except Exception as e:
        log.error(
            "pipeline: writeback write FAILED for inquiry %s row=%s: %s",
            inquiry.id, inquiry.source_excel_row, e,
        )
        return False
    log.info(
        "pipeline: writeback step 2/4 OK inquiry=%s out_bytes=%d",
        inquiry.id, len(updated_bytes),
    )

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
    file_name = _mue_response_file_name(inquiry.source_inquiry_uuid, ext)
    log.info(
        "pipeline: writeback step 3/4 (upload) inquiry=%s key=mue-responses/%s content_type=%s",
        inquiry.id, file_name, ct,
    )
    new_url = s3_service.upload_bytes(
        updated_bytes,
        original_name=file_name,
        inquiry_id=inquiry.id,
        content_type=ct,
        key_override=f"mue-responses/{file_name}",
    )
    if not new_url:
        log.error("pipeline: writeback upload FAILED for inquiry %s: s3 returned no url", inquiry.id)
        return False
    log.info(
        "pipeline: writeback step 3/4 OK inquiry=%s s3_url=%s",
        inquiry.id, new_url[:200],
    )

    # Tell the platform the new file is available.
    log.info(
        "pipeline: writeback step 4/4 (v2 POST /sheet) inquiry=%s uuid=%s",
        inquiry.id, inquiry.source_inquiry_uuid,
    )
    posted = _post_v2_sheet(
        inquiry_uuid=inquiry.source_inquiry_uuid,
        excel_url=new_url,
        access_token=token,
    )
    if posted:
        log.info("pipeline: writeback step 4/4 OK inquiry=%s — all 4 steps complete", inquiry.id)
    else:
        log.error(
            "pipeline: writeback step 4/4 FAILED inquiry=%s — workbook was uploaded to %s but v2 POST did not 2xx; will retry on next reply",
            inquiry.id, new_url[:200],
        )

    inquiry.excel_response_url = new_url
    if posted:
        inquiry.excel_response_posted_at = _now()
    db.commit()
    return posted
