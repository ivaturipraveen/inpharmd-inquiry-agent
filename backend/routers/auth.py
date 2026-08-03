"""Auth router — proxies login to staging InpharmD, mints a local session
token, never exposes the upstream access_token to the browser.

Frontend flow (2FA):
1. POST /api/auth/login {email, password}
   → {session_token, user}          normal success (2FA not required)
   → {code:"otp_required", ...}     2FA challenge — frontend shows OTP screen
2. POST /api/auth/verify-otp {email_token, otp}
   → {session_token, user}          OTP accepted, session created
3. POST /api/auth/resend-otp {email_token}
   → {code:"sent", email_token, message, email}  new code sent
4. Subsequent requests include `X-Session-Token: <token>` header
5. POST /api/auth/logout             → clears the session row
6. GET  /api/auth/me                 → returns current user
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

import inpharmd_service
from database import get_db
from models import User

log = logging.getLogger("inquiry.auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _mint_session_token() -> str:
    # 32 bytes of entropy → 64-char hex string. Plenty for a session id.
    return secrets.token_hex(32)


def _user_to_dict(u: User) -> dict:
    return {
        "id": u.id,
        "email": u.email,
        "display_name": u.display_name,
        "staging_user_id": u.staging_user_id,
        "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
    }


def get_current_user(
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
    db: Session = Depends(get_db),
) -> User:
    """FastAPI dependency. Raises 401 if the token is missing or unknown."""
    if not x_session_token:
        raise HTTPException(status_code=401, detail="Missing X-Session-Token header")
    user = db.query(User).filter(User.session_token == x_session_token).first()
    if not user:
        log.info("auth.lookup miss for token=%s…", x_session_token[:6])
        raise HTTPException(status_code=401, detail="Session expired or invalid")
    return user


class LoginIn(BaseModel):
    email: str  # not EmailStr — staging does its own validation
    password: str
    channel_id: Optional[str] = None


class VerifyOtpIn(BaseModel):
    email_token: str
    otp: str
    channel_id: Optional[str] = None


class ResendOtpIn(BaseModel):
    email_token: str


def _complete_login(resp: dict, email: str, db: Session) -> dict:
    """Parse a successful staging session response, upsert the User row,
    and return {session_token, user}. Shared by login and verify-otp."""
    if not isinstance(resp, dict):
        log.error("auth._complete_login unexpected response type: %s", type(resp).__name__)
        raise HTTPException(status_code=502, detail="Unexpected response from staging login.")

    data = resp.get("data") or {}
    attrs = data.get("attributes") or {}

    def _pick(*keys):
        for src in (attrs, data, resp):
            for k in keys:
                v = src.get(k)
                if v not in (None, ""):
                    return v
        return None

    access_token = _pick("access-token", "access_token", "token")
    if not access_token:
        log.error(
            "auth._complete_login no access-token. top_keys=%s data_keys=%s attr_keys=%s",
            list(resp.keys()), list(data.keys()), list(attrs.keys()),
        )
        raise HTTPException(
            status_code=502,
            detail=(
                "Staging login succeeded but no access-token returned. "
                f"Top keys: {list(resp.keys())}, data keys: {list(data.keys())}, "
                f"attr keys: {list(attrs.keys())}."
            ),
        )

    raw_user_id = _pick("user-id", "user_id", "id")
    staging_user_id = str(raw_user_id) if raw_user_id is not None else None

    display_name = _pick("name", "full-name", "full_name", "display_name") or (
        f"{(_pick('first-name', 'first_name') or '')} "
        f"{(_pick('last-name', 'last_name') or '')}"
    ).strip() or None

    user_blob = attrs or data

    user = db.query(User).filter(User.email == email).first()
    if user is None:
        user = User(email=email, session_token=_mint_session_token(), staging_token=access_token)
        db.add(user)
        log.info("auth._complete_login new user email=%s", email)
    else:
        user.session_token = _mint_session_token()
        user.staging_token = access_token
        log.info("auth._complete_login refreshed user_id=%s email=%s", user.id, email)

    user.staging_user_id = staging_user_id
    user.display_name = display_name
    import json as _json
    try:
        user.profile_json = _json.dumps(user_blob)[:8000] if user_blob else None
    except Exception:
        user.profile_json = None
    user.last_login_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(user)
    log.info("auth._complete_login success user_id=%s session=%s…", user.id, user.session_token[:6])
    return {"session_token": user.session_token, "user": _user_to_dict(user)}


@router.post("/login")
def login(payload: LoginIn, db: Session = Depends(get_db)):
    log.info("auth.login attempt email=%s", payload.email)

    try:
        resp = inpharmd_service.login_2fa(payload.email, payload.password, payload.channel_id)
    except inpharmd_service.InpharmdAPIError as e:
        log.warning("auth.login upstream rejected email=%s status=%s body=%s", payload.email, e.status_code, e.body)
        raise HTTPException(
            status_code=401 if e.status_code in (401, 422) else 502,
            detail="Invalid email or password." if e.status_code in (401, 422) else "Upstream login failed.",
        )

    # 2FA challenge — pass through to frontend, which will show the OTP screen.
    if isinstance(resp, dict) and resp.get("code") == "otp_required":
        log.info("auth.login 2FA required email=%s", payload.email)
        return {
            "code": resp.get("code"),
            "message": resp.get("message", ""),
            "email_token": resp.get("email_token", ""),
            "email": resp.get("email", payload.email),
        }

    # Normal success — create/refresh the local session.
    return _complete_login(resp, payload.email, db)


@router.post("/verify-otp")
def verify_otp(payload: VerifyOtpIn, db: Session = Depends(get_db)):
    """Submit the OTP. On success, mints a local session exactly like /login."""
    log.info("auth.verify-otp attempt email_token=%s…", payload.email_token[:8])
    try:
        resp = inpharmd_service.verify_otp(payload.email_token, payload.otp, payload.channel_id)
    except inpharmd_service.InpharmdAPIError as e:
        log.warning("auth.verify-otp rejected status=%s body=%s", e.status_code, e.body)
        raise HTTPException(
            status_code=401 if e.status_code in (400, 401, 422) else 502,
            detail="Invalid verification code. Please try again.",
        )

    # Email lives in data.attributes.email in the JSON:API response.
    # Top-level resp.get("email") is a fallback for any flatter variant.
    email = (
        resp.get("data", {}).get("attributes", {}).get("email")
        or resp.get("email")
        or ""
    )
    if not email:
        log.error("auth.verify-otp could not extract email from staging response keys=%s", list(resp.keys()))
        raise HTTPException(status_code=502, detail="Could not determine user email from OTP verification response.")
    return _complete_login(resp, email, db)


@router.post("/resend-otp")
def resend_otp(payload: ResendOtpIn):
    """Request a new OTP code. Returns {code:"sent", email_token, message, email}."""
    log.info("auth.resend-otp email_token=%s…", payload.email_token[:8])
    try:
        resp = inpharmd_service.resend_otp(payload.email_token)
    except inpharmd_service.InpharmdAPIError as e:
        log.warning("auth.resend-otp rejected status=%s body=%s", e.status_code, e.body)
        raise HTTPException(
            status_code=400 if e.status_code in (400, 422) else 502,
            detail="Could not resend verification code." if e.status_code < 500 else "Resend failed.",
        )

    return {
        "code": resp.get("code", "otp_required"),
        "message": resp.get("message", ""),
        "email_token": resp.get("email_token", payload.email_token),
        "email": resp.get("email", ""),
    }


@router.post("/logout")
def logout(current: User = Depends(get_current_user), db: Session = Depends(get_db)):
    log.info("auth.logout user_id=%s email=%s", current.id, current.email)
    # Rotate the session token so the old one is dead. We keep the user row
    # itself (and the staging token, in case we want to re-validate later).
    current.session_token = _mint_session_token()
    db.commit()
    return {"ok": True}


@router.get("/me")
def me(current: User = Depends(get_current_user)):
    return {"user": _user_to_dict(current)}
