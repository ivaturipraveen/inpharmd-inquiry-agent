"""Public, unauthenticated redirect for InpharmD's "Contact Manufacturer"
button — forwards to the SPA's uuid-only deep link; no inquiry data here."""
from __future__ import annotations

import os
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

router = APIRouter(tags=["redirects"])


@router.get("/go/contact-manufacturer/{inquiry_uuid}")
def go_contact_manufacturer(inquiry_uuid: str) -> RedirectResponse:
    base = os.getenv("APP_BASE_URL")
    if not base:
        # Fail clearly rather than silently redirecting a real user to
        # localhost — this endpoint is public-facing in every deployed env.
        raise HTTPException(status_code=500, detail="APP_BASE_URL is not configured on this server.")
    target = f"{base.rstrip('/')}/#contact-manufacturer?uuid={quote(inquiry_uuid, safe='')}"
    return RedirectResponse(url=target, status_code=302)
