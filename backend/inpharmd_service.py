"""Thin client for the upstream InpharmD platform API.

We talk to the staging instance at `INPHARMD_API_BASE_URL` (defaults to the
public staging host). Every request is logged with method, URL, status,
and duration so the user can trace what we sent and what came back.

Three endpoints in scope today:

- POST /api/v2/login                                  → exchange email+password for an access_token
- GET  /api/v2/inquiries/open_mue_inquiries?access_token=…  → list open MUE inquiries
- GET  /api/v2/inquiries/{id}/submitter_details?…           → fetch a single inquiry's submitter details (legacy)

Anything that fails upstream is re-raised as `InpharmdAPIError` with the
upstream status code and body so the router can turn it into a clean 4xx
for the browser instead of leaking a 500 with a tracebacka.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional

import httpx

log = logging.getLogger("inquiry.inpharmd")


DEFAULT_BASE_URL = "https://staging-mercer-inpharmd.herokuapp.com"

# Heroku free-tier can take a long time to wake up + the inquiries list
# is ~4MB. Different timeouts per endpoint, with retries on transient
# upstream failures (502/503/504/network/timeout).
DEFAULT_TIMEOUT_SECONDS = 20.0
LIST_TIMEOUT_SECONDS = 60.0
MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = (1.0, 3.0)  # one entry per retry attempt
RETRY_STATUSES = {502, 503, 504}


def _base_url() -> str:
    return os.getenv("INPHARMD_API_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


class InpharmdAPIError(Exception):
    def __init__(self, status_code: int, message: str, body: Any = None):
        super().__init__(f"{status_code}: {message}")
        self.status_code = status_code
        self.message = message
        self.body = body


def _short(s: Any, n: int = 240) -> str:
    """Truncate for logging; keep enough to be useful."""
    s = str(s)
    return s if len(s) <= n else s[:n] + f"… ({len(s)} chars)"


def _call(
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    data: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Any:
    url = f"{_base_url()}{path}"
    safe_params = dict(params or {})
    # Redact the access_token in logs so it doesn't end up in shared logs.
    if "access_token" in safe_params:
        tok = str(safe_params["access_token"])
        safe_params["access_token"] = tok[:6] + "…" if len(tok) > 6 else "…"
    log.info("→ %s %s params=%s timeout=%.0fs", method, url, safe_params, timeout)

    last_exc: Optional[InpharmdAPIError] = None
    for attempt in range(MAX_RETRIES + 1):
        started = time.monotonic()
        try:
            with httpx.Client(timeout=timeout) as client:
                res = client.request(
                    method,
                    url,
                    params=params,
                    data=data,
                    headers={"Accept": "application/json", **(headers or {})},
                )
        except httpx.TimeoutException as e:
            duration_ms = (time.monotonic() - started) * 1000
            log.warning(
                "⌛ %s %s timeout after %.0fms (attempt %d/%d): %s",
                method, url, duration_ms, attempt + 1, MAX_RETRIES + 1, e,
            )
            last_exc = InpharmdAPIError(
                status_code=504,
                message=f"Staging timed out after {duration_ms:.0f}ms (Heroku may be cold-starting). {e}",
            )
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)])
                continue
            raise last_exc
        except httpx.HTTPError as e:
            duration_ms = (time.monotonic() - started) * 1000
            log.error(
                "✗ %s %s network error after %.0fms (attempt %d/%d): %s",
                method, url, duration_ms, attempt + 1, MAX_RETRIES + 1, e,
            )
            last_exc = InpharmdAPIError(
                status_code=502, message=f"Network error talking to staging InpharmD: {e}"
            )
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)])
                continue
            raise last_exc

        duration_ms = (time.monotonic() - started) * 1000
        log.info(
            "← %s %s status=%s duration=%.0fms attempt=%d body=%s",
            method, url, res.status_code, duration_ms, attempt + 1, _short(res.text),
        )

        if res.status_code in RETRY_STATUSES and attempt < MAX_RETRIES:
            log.warning(
                "↻ %s %s got %s — retrying (attempt %d/%d)",
                method, url, res.status_code, attempt + 2, MAX_RETRIES + 1,
            )
            time.sleep(RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)])
            continue

        if res.status_code >= 400:
            try:
                body = res.json()
            except Exception:
                body = res.text
            raise InpharmdAPIError(
                status_code=res.status_code,
                message=res.reason_phrase or f"HTTP {res.status_code}",
                body=body,
            )

        if not res.content:
            return None
        try:
            return res.json()
        except ValueError:
            return res.text

    # We should have returned or raised already, but be defensive.
    if last_exc:
        raise last_exc
    raise InpharmdAPIError(status_code=502, message="Unknown error after retries")


# ─────────────────────────── Public API ───────────────────────────


def login(email: str, password: str, channel_id: Optional[str] = None) -> Dict[str, Any]:
    """Exchange email + password for an InpharmD access_token. Returns the
    raw upstream JSON so the caller can decide what to persist."""
    payload: Dict[str, Any] = {"email": email, "password": password}
    if channel_id:
        payload["channel_id"] = channel_id
    return _call("POST", "/api/v2/login", data=payload)


_2FA_HEADERS = {"Accept": "application/vnd.api+json"}


def login_2fa(email: str, password: str, channel_id: Optional[str] = None) -> Dict[str, Any]:
    """Initiate 2FA login. Returns either a normal session response or
    {"code": "otp_required", "email_token": "...", "message": "...", "email": "..."}."""
    payload: Dict[str, Any] = {"email": email, "password": password}
    if channel_id:
        payload["channel_id"] = channel_id
    return _call("POST", "/api/v2/login/2fa_login", data=payload, headers=_2FA_HEADERS)


def verify_otp(email_token: str, otp: str, channel_id: Optional[str] = None) -> Dict[str, Any]:
    """Submit the OTP code. Returns the full JSON:API session response on success
    (same shape as a normal login: data.attributes.access-token, user-id, email, …)."""
    payload: Dict[str, Any] = {"email_token": email_token, "otp": otp}
    if channel_id:
        payload["channel_id"] = channel_id
    return _call("POST", "/api/v2/login/verify_otp", data=payload, headers=_2FA_HEADERS)


def resend_otp(email_token: str) -> Dict[str, Any]:
    """Request a new OTP code. Returns {"code":"sent", "email_token": <new>, "message": …, "email": …}."""
    return _call(
        "POST", "/api/v2/login/resend_otp",
        data={"email_token": email_token},
        headers=_2FA_HEADERS,
    )


def list_inquiries(access_token: str, **extra_params: Any) -> Any:
    params = {"access_token": access_token, **{k: v for k, v in extra_params.items() if v is not None}}
    # Open Medication Use Evaluation (MUE) inquiries with submitter attachments.
    # Response shape (per swagger):
    #   { data: [ { inquiry_uuid, title, inquiry_submitter, inquiry_types[],
    #               attachments: [{id, file_name, doc_url}],
    #               inquiry_submitter_details: {…} } ] }
    return _call("GET", "/api/v2/inquiries/open_mue_inquiries", params=params, timeout=LIST_TIMEOUT_SECONDS)


def get_inquiry_submitter_details(access_token: str, inquiry_id: str) -> Any:
    return _call(
        "GET",
        f"/api/v2/inquiries/{inquiry_id}/submitter_details",
        params={"access_token": access_token},
    )
