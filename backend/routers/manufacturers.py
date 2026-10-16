import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from database import get_db
from models import ManufacturerContact, User
from routers.auth import get_current_user
from schemas import (
    ManufacturerContactCreate,
    ManufacturerContactOut,
    ManufacturerContactUpdate,
)

log = logging.getLogger("inquiry.manufacturers")

router = APIRouter(prefix="/api/manufacturers", tags=["manufacturers"])


def _resolve_actor_names(db: Session, rows: List[ManufacturerContact]) -> None:
    """Batch-resolve archived_by/restored_by display names in-place, mirroring
    routers.inquiries.list_inquiries' created_by resolution for all_users=true."""
    user_ids = {r.archived_by_user_id for r in rows if r.archived_by_user_id}
    user_ids |= {r.restored_by_user_id for r in rows if r.restored_by_user_id}
    if not user_ids:
        return
    users = {u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()}
    for r in rows:
        if r.archived_by_user_id:
            u = users.get(r.archived_by_user_id)
            if u:
                r._archived_by_name = u.display_name or u.email
        if r.restored_by_user_id:
            u = users.get(r.restored_by_user_id)
            if u:
                r._restored_by_name = u.display_name or u.email


def _to_out(obj: ManufacturerContact) -> ManufacturerContactOut:
    out = ManufacturerContactOut.model_validate(obj)
    out.archived_by = getattr(obj, "_archived_by_name", None)
    out.restored_by = getattr(obj, "_restored_by_name", None)
    return out


@router.get("", response_model=List[ManufacturerContactOut])
def list_manufacturers(
    q: Optional[str] = Query(None, description="Search by manufacturer / parent / notes"),
    include_archived: bool = Query(False, description="Include archived manufacturers"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(ManufacturerContact)
    if not include_archived:
        query = query.filter(ManufacturerContact.archived_at.is_(None))
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                ManufacturerContact.manufacturer.ilike(like),
                ManufacturerContact.parent_owner.ilike(like),
                ManufacturerContact.notes.ilike(like),
            )
        )
    rows = query.order_by(ManufacturerContact.manufacturer.asc()).all()
    _resolve_actor_names(db, rows)
    return [_to_out(r) for r in rows]


@router.get("/{contact_id}", response_model=ManufacturerContactOut)
def get_manufacturer(
    contact_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    obj = db.get(ManufacturerContact, contact_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Manufacturer not found")
    _resolve_actor_names(db, [obj])
    return _to_out(obj)


@router.post("", response_model=ManufacturerContactOut, status_code=201)
def create_manufacturer(
    payload: ManufacturerContactCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    obj = ManufacturerContact(**payload.model_dump(exclude_unset=True))
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return _to_out(obj)


@router.put("/{contact_id}", response_model=ManufacturerContactOut)
def update_manufacturer(
    contact_id: int,
    payload: ManufacturerContactUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    obj = db.get(ManufacturerContact, contact_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Manufacturer not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(obj, key, value)
    db.commit()
    db.refresh(obj)
    _resolve_actor_names(db, [obj])
    return _to_out(obj)


@router.post("/{contact_id}/archive", response_model=ManufacturerContactOut)
def archive_manufacturer(
    contact_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Soft-delete — never calls db.delete(), so related records are preserved.
    Idempotent: re-archiving an already-archived manufacturer is a no-op."""
    obj = db.get(ManufacturerContact, contact_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Manufacturer not found")
    if obj.archived_at is None:
        obj.archived_at = datetime.now(timezone.utc)
        obj.archived_by_user_id = current_user.id
        obj.restored_at = None
        obj.restored_by_user_id = None
        db.commit()
        db.refresh(obj)
        log.info(
            "Manufacturer %s (%r) archived by user %s",
            obj.id, obj.manufacturer, current_user.id,
        )
    _resolve_actor_names(db, [obj])
    return _to_out(obj)


@router.post("/{contact_id}/restore", response_model=ManufacturerContactOut)
def restore_manufacturer(
    contact_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Clears the archive flag. Idempotent: restoring an already-active
    manufacturer is a no-op success."""
    obj = db.get(ManufacturerContact, contact_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Manufacturer not found")
    if obj.archived_at is not None:
        obj.archived_at = None
        obj.archived_by_user_id = None
        obj.restored_at = datetime.now(timezone.utc)
        obj.restored_by_user_id = current_user.id
        db.commit()
        db.refresh(obj)
        log.info(
            "Manufacturer %s (%r) restored by user %s",
            obj.id, obj.manufacturer, current_user.id,
        )
    _resolve_actor_names(db, [obj])
    return _to_out(obj)
