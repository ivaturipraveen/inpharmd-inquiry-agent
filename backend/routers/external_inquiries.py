"""Pass-through proxy for staging InpharmD inquiry endpoints, with cache.

Frontend talks to /api/external/inquiries (and /…/{id}) using the
X-Session-Token header; the backend looks up the user, pulls their
staging access_token from the DB, and forwards the request upstream.

Cache strategy (in-process, see `cache_service.py`):
- Full list key: `external:full:<user_id>` — entire dataset from staging,
  fetched once with a large per_page and cached for 5 min (INPHARMD_LIST_TTL_SECONDS).
  Filtering and pagination are applied in-process against this cached list so
  that search / type / attachment filters work across the full dataset, not just
  the current page.
- Detail key: `external:detail:<user_id>:<id>` — single inquiry, 10 min TTL.
- `?fresh=true` invalidates the full-list cache and re-fetches from staging.
- Stale fallback: if staging is unreachable and a stale full-list entry exists,
  filtering + pagination still work against the stale data.

Every list response carries:
- `X-Cache: HIT | MISS | STALE`
- `X-Cache-Age: <seconds>` (HIT/STALE only)
- `X-Upstream-Error: <reason>` (STALE only)
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

import cache_service
import dailymed_service
import excel_service
import excel_writeback_service
import inpharmd_service
import s3_service
from database import get_db
from models import ManufacturerContact, User
from routers.auth import get_current_user

log = logging.getLogger("inquiry.external")

router = APIRouter(prefix="/api/external/inquiries", tags=["external-inquiries"])


def _list_ttl() -> int:
    try:
        return int(os.getenv("INPHARMD_LIST_TTL_SECONDS", "300"))
    except ValueError:
        return 300


def _detail_ttl() -> int:
    try:
        return int(os.getenv("INPHARMD_DETAIL_TTL_SECONDS", "600"))
    except ValueError:
        return 600


def _cache_key_detail(user_id: int, inquiry_id: str) -> str:
    return f"external:detail:{user_id}:{inquiry_id}"


def _full_list_cache_key(user_id: int) -> str:
    return f"external:full:{user_id}"


# Fetch the entire staging dataset in one shot. Staging has no apparent cap on
# per_page and we confirmed values of 500 work fine. 5 000 is a safe ceiling
# for the foreseeable future; if the dataset ever exceeds it the meta.total_pages
# check below will log a warning so it's easy to spot.
_FULL_FETCH_PER_PAGE = 5000


def _fetch_full_list(token: str, user_id: int, fresh: bool) -> tuple[list, str, int]:
    """Return (all_items, cache_status, cache_age_seconds)."""
    key = _full_list_cache_key(user_id)
    if fresh:
        cache_service.invalidate(key)

    cached = cache_service.get(key)
    if cached is not None:
        items, age = cached
        return items, "HIT", int(age)

    data = inpharmd_service.list_inquiries(token, page=1, per_page=_FULL_FETCH_PER_PAGE)
    if isinstance(data, dict):
        items = data.get("data") or []
        meta = data.get("meta") or {}
        total_pages = meta.get("total_pages", 1)
        if total_pages > 1:
            log.warning(
                "external.full_fetch: staging has %s pages at per_page=%s — "
                "increase _FULL_FETCH_PER_PAGE to capture all records",
                total_pages, _FULL_FETCH_PER_PAGE,
            )
    else:
        items = data if isinstance(data, list) else []

    cache_service.set(key, items, ttl_seconds=_list_ttl())
    return items, "MISS", 0


def _matches_search(item: dict, q: str) -> bool:
    det = item.get("inquiry_submitter_details") or {}
    parts = [
        item.get("title") or "",
        item.get("inquiry_submitter") or "",
        det.get("email") or "",
        det.get("first_name") or "",
        det.get("last_name") or "",
        item.get("inquiry_uuid") or "",
    ]
    for att in (item.get("attachments") or []):
        parts.append(att.get("file_name") or "")
    for t in (item.get("project_types") or item.get("inquiry_types") or []):
        parts.append(t)
    return q in " ".join(parts).lower()


@router.get("")
def list_external_inquiries(
    response: Response,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=200),
    search: Optional[str] = Query(None),
    inquiry_type: Optional[str] = Query(None),
    with_attachments: bool = Query(False),
    fresh: bool = Query(False, description="Set true to bypass cache."),
    current: User = Depends(get_current_user),
) -> Any:
    log.info(
        "external.list user_id=%s page=%s per_page=%s search=%r inquiry_type=%s "
        "with_attachments=%s fresh=%s",
        current.id, page, per_page, search, inquiry_type, with_attachments, fresh,
    )

    key = _full_list_cache_key(current.id)

    try:
        all_items, cache_status, cache_age = _fetch_full_list(
            current.staging_token, current.id, fresh
        )
    except inpharmd_service.InpharmdAPIError as e:
        log.error("external.list upstream error status=%s body=%s", e.status_code, e.body)
        stale = cache_service.get_stale_ok(key)
        if stale is not None:
            all_items, stale_age = stale
            log.warning("external.list serving STALE age=%.0fs after upstream %s", stale_age, e.status_code)
            cache_status, cache_age = "STALE", int(stale_age)
            response.headers["X-Upstream-Error"] = f"{e.status_code} {e.message}"[:200]
        elif e.status_code in (401, 403):
            raise HTTPException(status_code=401, detail="Staging access token expired. Please log in again.")
        else:
            raise HTTPException(
                status_code=502,
                detail=f"Staging error {e.status_code}: {e.message}. No cached data available yet.",
            )

    # Filter in-process against the full cached list.
    filtered = all_items
    if search:
        q = search.strip().lower()
        if q:
            filtered = [i for i in filtered if _matches_search(i, q)]
    if inquiry_type:
        filtered = [i for i in filtered if inquiry_type in (
            i.get("project_types") or i.get("inquiry_types") or []
        )]
    if with_attachments:
        filtered = [i for i in filtered if i.get("attachments")]

    # Paginate the filtered result.
    total_entries = len(filtered)
    total_pages = max(1, (total_entries + per_page - 1) // per_page)
    page = min(page, total_pages)
    start = (page - 1) * per_page
    page_items = filtered[start: start + per_page]

    response.headers["X-Cache"] = cache_status
    if cache_age:
        response.headers["X-Cache-Age"] = str(cache_age)

    return {
        "data": page_items,
        "meta": {
            "page": page,
            "per_page": per_page,
            "total_entries": total_entries,
            "total_pages": total_pages,
        },
    }


@router.get("/{inquiry_id}")
def get_external_inquiry(
    response: Response,
    inquiry_id: str,
    fresh: bool = Query(False),
    current: User = Depends(get_current_user),
) -> Any:
    key = _cache_key_detail(current.id, inquiry_id)
    log.info("external.detail user_id=%s inquiry_id=%s fresh=%s", current.id, inquiry_id, fresh)

    if fresh:
        cache_service.invalidate(key)

    cached = cache_service.get(key)
    if cached is not None:
        value, age = cached
        log.info("external.detail cache HIT key=%s age=%.0fs", key, age)
        response.headers["X-Cache"] = "HIT"
        response.headers["X-Cache-Age"] = str(int(age))
        return value

    try:
        data = inpharmd_service.get_inquiry_submitter_details(current.staging_token, inquiry_id)
        cache_service.set(key, data, ttl_seconds=_detail_ttl())
        response.headers["X-Cache"] = "MISS"
        return data
    except inpharmd_service.InpharmdAPIError as e:
        log.error("external.detail upstream error id=%s status=%s body=%s", inquiry_id, e.status_code, e.body)
        stale = cache_service.get_stale_ok(key)
        if stale is not None:
            value, age = stale
            log.warning("external.detail serving STALE id=%s age=%.0fs", inquiry_id, age)
            response.headers["X-Cache"] = "STALE"
            response.headers["X-Cache-Age"] = str(int(age))
            response.headers["X-Upstream-Error"] = f"{e.status_code} {e.message}"[:200]
            return value
        if e.status_code == 404:
            raise HTTPException(status_code=404, detail=f"Inquiry {inquiry_id} not found in staging.")
        if e.status_code in (401, 403):
            raise HTTPException(status_code=401, detail="Staging access token expired. Please log in again.")
        raise HTTPException(status_code=502, detail=f"Staging error {e.status_code}: {e.message}")


class ExtractManufacturersRequest(BaseModel):
    doc_url: str
    inquiry_uuid: Optional[str] = None


class ExtractedManufacturerRow(BaseModel):
    row_index: int
    raw_name: str
    matched_id: Optional[int] = None
    matched_name: Optional[str] = None
    confidence: str
    medication_name: str = ""
    pi_storage: str = ""
    ndc: str = ""
    pi_link: str = ""   # filled in by DailyMed enrichment when ndc is present


class ExtractManufacturersResponse(BaseModel):
    sheet_name: str
    header_row: int
    header_value: str
    rows: list[ExtractedManufacturerRow]
    total: int
    matched: int
    medication_col_header: Optional[str] = None   # None = column not found in file
    pi_storage_col_header: Optional[str] = None   # None = column not found in file
    ndc_col_header: Optional[str] = None           # None = NDC column not present in file
    # Our own S3 copy of the workbook — the bulk_create endpoint stamps this
    # as `source_excel_url` on every inquiry so the response-writeback path
    # always operates on our copy (no dependence on the 10s InpharmD signed URL).
    excel_s3_url: Optional[str] = None


@router.post("/extract-manufacturers", response_model=ExtractManufacturersResponse)
async def extract_manufacturers(
    payload: ExtractManufacturersRequest = Body(...),
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """Download the .xlsx at `doc_url`, find the 'Manufacturer' column, and
    pair every row's value with the best match from our manufacturer DB.

    Called by the Contact-Manufacturer page so the user gets a pre-populated
    multi-select instead of typing each name."""
    log.info(
        "external.extract user_id=%s email=%s doc=%s",
        current.id, current.email, payload.doc_url,
    )
    try:
        xlsx = excel_service.download_excel(
            payload.doc_url, access_token=current.staging_token
        )
    except Exception as e:
        log.error("external.extract download failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Failed to download attachment: {e}")

    try:
        rows, loc, extra_cols = excel_service.extract_manufacturer_rows(xlsx)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        log.exception("external.extract parse failed")
        raise HTTPException(status_code=422, detail=f"Could not parse spreadsheet: {e}")

    mfrs = db.query(ManufacturerContact).all()
    matches = excel_service.match_manufacturers(rows, mfrs)

    # DailyMed NDC enrichment — fills pi_link + pi_storage on matched rows
    # that carry an NDC but are missing those fields. Runs concurrently
    # (semaphore-capped), writes results to the persistent dailymed_cache table,
    # and never raises (failures are logged and the row is left unchanged).
    await dailymed_service.enrich_rows(matches, db)

    # Mirror the workbook into our own S3 right now, before any responses
    # arrive. Every inquiry created from this dispatch will point at this
    # URL so writeback always works against our copy.
    s3_url: Optional[str] = None
    try:
        from urllib.parse import urlparse, unquote
        fname = unquote(urlparse(payload.doc_url).path.rsplit("/", 1)[-1] or "mue.xlsx")
        if not fname.lower().endswith(".xlsx"):
            fname = f"{fname}.xlsx"
        s3_url = s3_service.upload_bytes(
            xlsx,
            original_name=fname,
            inquiry_id=0,  # not yet associated with an inquiry; key includes a uuid
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            prefix="mue-source",
        )
    except Exception as e:
        log.warning("external.extract S3 upload failed (will fall back to InpharmD url): %s", e)

    # Write PI link + storage data back into the workbook for DailyMed-enriched
    # rows, re-upload to S3 under mue-pi-enriched/, and POST to staging so the
    # platform sees the enriched sheet immediately. If this succeeds, use the
    # enriched URL as source_excel_url so future response writeback builds on
    # the PI-enriched copy rather than the pristine original.
    if s3_url and payload.inquiry_uuid and any(
        getattr(m, "pi_link", None) or getattr(m, "pi_storage", None) for m in matches
    ):
        try:
            enriched_url = excel_writeback_service.writeback_pi_enrichment(
                xlsx,
                matches,
                inquiry_uuid=payload.inquiry_uuid,
                access_token=current.staging_token,
            )
            if enriched_url:
                s3_url = enriched_url
        except Exception as e:
            log.warning("external.extract PI writeback failed (non-fatal): %s", e)

    out_rows = [
        ExtractedManufacturerRow(
            row_index=m.row_index,
            raw_name=m.raw_name,
            matched_id=m.matched_id,
            matched_name=m.matched_name,
            confidence=m.confidence,
            medication_name=m.medication_name,
            pi_storage=m.pi_storage,
            ndc=m.ndc,
            pi_link=m.pi_link,
        )
        for m in matches
    ]
    return ExtractManufacturersResponse(
        sheet_name=loc.sheet_name,
        header_row=loc.header_row,
        header_value=loc.header_value,
        rows=out_rows,
        total=len(out_rows),
        matched=sum(1 for r in out_rows if r.matched_id is not None),
        medication_col_header=extra_cols.medication_col_header,
        pi_storage_col_header=extra_cols.pi_storage_col_header,
        ndc_col_header=extra_cols.ndc_col_header,
        excel_s3_url=s3_url,
    )


# Small debug endpoint so the frontend (and you) can see what's cached.
@router.get("/_debug/cache")
def cache_state(current: User = Depends(get_current_user)) -> Any:
    return cache_service.stats()
