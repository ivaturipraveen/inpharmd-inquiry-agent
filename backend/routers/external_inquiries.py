"""Pass-through proxy for staging InpharmD inquiry endpoints, with cache.

Frontend talks to /api/external/inquiries (and /…/{id}) using the
X-Session-Token header; the backend looks up the user, pulls their
staging access_token from the DB, and forwards the request upstream.

Cache strategy (in-process, see `cache_service.py`):
- Cache key: `external:list:<user_id>` and `external:detail:<user_id>:<id>`
- List TTL: 5 min by default (configurable via INPHARMD_LIST_TTL_SECONDS env)
- Detail TTL: 10 min (small, cheap, doesn't change often)
- `?fresh=true` query param busts the cache entry before fetching
- If staging fails AND we have any stale cache entry, serve stale with
  `X-Cache: STALE` and the upstream error in `X-Upstream-Error`. The
  browser keeps working even when Heroku is down.

Every response carries:
- `X-Cache: HIT | MISS | STALE`
- `X-Cache-Age: <seconds>` (only on HIT/STALE)
- `X-Upstream-Error: <reason>` (only on STALE)
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response

import cache_service
import inpharmd_service
from models import User
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


def _cache_key_list(user_id: int) -> str:
    return f"external:list:{user_id}"


def _cache_key_detail(user_id: int, inquiry_id: str) -> str:
    return f"external:detail:{user_id}:{inquiry_id}"


@router.get("")
def list_external_inquiries(
    response: Response,
    status: Optional[str] = Query(None, description="Optional status filter passed through to staging."),
    page: Optional[int] = Query(None, ge=1),
    per_page: Optional[int] = Query(None, ge=1, le=200),
    fresh: bool = Query(False, description="Set true to bypass cache."),
    current: User = Depends(get_current_user),
) -> Any:
    key = _cache_key_list(current.id)
    # Note: server-side filtering+pagination params would compose into the
    # cache key, but staging ignores them today, so we cache the whole list.
    log.info(
        "external.list user_id=%s email=%s status=%s page=%s per_page=%s fresh=%s",
        current.id, current.email, status, page, per_page, fresh,
    )

    if fresh:
        cache_service.invalidate(key)

    cached = cache_service.get(key)
    if cached is not None:
        value, age = cached
        log.info("external.list cache HIT key=%s age=%.0fs", key, age)
        response.headers["X-Cache"] = "HIT"
        response.headers["X-Cache-Age"] = str(int(age))
        return value

    try:
        data = inpharmd_service.list_inquiries(
            current.staging_token,
            status=status, page=page, per_page=per_page,
        )
        cache_service.set(key, data, ttl_seconds=_list_ttl())
        response.headers["X-Cache"] = "MISS"
        return data
    except inpharmd_service.InpharmdAPIError as e:
        log.error("external.list upstream error status=%s body=%s", e.status_code, e.body)
        # Stale fallback so the user keeps working when Heroku is down.
        stale = cache_service.get_stale_ok(key)
        if stale is not None:
            value, age = stale
            log.warning(
                "external.list serving STALE key=%s age=%.0fs after upstream %s",
                key, age, e.status_code,
            )
            response.headers["X-Cache"] = "STALE"
            response.headers["X-Cache-Age"] = str(int(age))
            response.headers["X-Upstream-Error"] = f"{e.status_code} {e.message}"[:200]
            return value
        if e.status_code in (401, 403):
            raise HTTPException(status_code=401, detail="Staging access token expired. Please log in again.")
        # No cache to fall back on — propagate the real reason so the UI
        # can show it instead of a generic "Bad Gateway".
        raise HTTPException(
            status_code=502,
            detail=f"Staging error {e.status_code}: {e.message}. No cached data available yet.",
        )


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


# Small debug endpoint so the frontend (and you) can see what's cached.
@router.get("/_debug/cache")
def cache_state(current: User = Depends(get_current_user)) -> Any:
    return cache_service.stats()
