import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import auto_dispatch_service
from database import get_db
from models import AutoDispatchSetting, User
from routers.auth import get_current_user
from schemas import AutoDispatchStatus

log = logging.getLogger("inquiry.auto_dispatch_api")

router = APIRouter(prefix="/api/auto-dispatch", tags=["auto-dispatch"])


def _build_status(db: Session, setting: AutoDispatchSetting, *, actor: Optional[User] = None) -> AutoDispatchStatus:
    """actor is the user who just made this change (enable/disable), when
    known — passing it skips a redundant User lookup, since the caller
    already has it. GET /status has no actor and looks up
    setting.updated_by_user_id instead."""
    updated_by = None
    if actor:
        updated_by = actor.display_name or actor.email
    elif setting.updated_by_user_id:
        u = db.get(User, setting.updated_by_user_id)
        if u:
            updated_by = u.display_name or u.email
    return AutoDispatchStatus(
        enabled=setting.enabled,
        activated_at=setting.activated_at,
        updated_at=setting.updated_at,
        updated_by=updated_by,
    )


@router.get("/status", response_model=AutoDispatchStatus)
def get_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    setting = auto_dispatch_service.get_setting(db)
    return _build_status(db, setting)


@router.post("/enable", response_model=AutoDispatchStatus)
def enable(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Snapshots every currently-open InpharmD inquiry as permanently
    ineligible for this activation, then flips the flag on — atomically.
    Fails closed (502) with no state change if the snapshot can't be taken."""
    try:
        setting = auto_dispatch_service.enable(db, current_user)
    except auto_dispatch_service.AutoDispatchEnableError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return _build_status(db, setting, actor=current_user)


@router.post("/disable", response_model=AutoDispatchStatus)
def disable(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    setting = auto_dispatch_service.disable(db, current_user)
    return _build_status(db, setting, actor=current_user)
