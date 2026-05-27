from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from database import get_db
from models import ManufacturerContact
from schemas import (
    ManufacturerContactCreate,
    ManufacturerContactOut,
    ManufacturerContactUpdate,
)

router = APIRouter(prefix="/api/manufacturers", tags=["manufacturers"])


@router.get("", response_model=List[ManufacturerContactOut])
def list_manufacturers(
    q: Optional[str] = Query(None, description="Search by manufacturer / parent / notes"),
    db: Session = Depends(get_db),
):
    query = db.query(ManufacturerContact)
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                ManufacturerContact.manufacturer.ilike(like),
                ManufacturerContact.parent_owner.ilike(like),
                ManufacturerContact.notes.ilike(like),
            )
        )
    return query.order_by(ManufacturerContact.manufacturer.asc()).all()


@router.get("/{contact_id}", response_model=ManufacturerContactOut)
def get_manufacturer(contact_id: int, db: Session = Depends(get_db)):
    obj = db.get(ManufacturerContact, contact_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Manufacturer not found")
    return obj


@router.post("", response_model=ManufacturerContactOut, status_code=201)
def create_manufacturer(payload: ManufacturerContactCreate, db: Session = Depends(get_db)):
    obj = ManufacturerContact(**payload.model_dump(exclude_unset=True))
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@router.put("/{contact_id}", response_model=ManufacturerContactOut)
def update_manufacturer(
    contact_id: int,
    payload: ManufacturerContactUpdate,
    db: Session = Depends(get_db),
):
    obj = db.get(ManufacturerContact, contact_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Manufacturer not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(obj, key, value)
    db.commit()
    db.refresh(obj)
    return obj


@router.delete("/{contact_id}", status_code=204)
def delete_manufacturer(contact_id: int, db: Session = Depends(get_db)):
    obj = db.get(ManufacturerContact, contact_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Manufacturer not found")
    db.delete(obj)
    db.commit()
    return None
