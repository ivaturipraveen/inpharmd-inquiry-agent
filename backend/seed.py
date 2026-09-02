"""Seed the manufacturer_contacts table from the source Excel workbook.

Usage:
    python seed.py            # only insert if table is empty
    python seed.py --force    # wipe and reload
"""
import argparse
import os
import sys
from datetime import date, datetime
from pathlib import Path

import openpyxl

from database import Base, SessionLocal, engine
from models import ManufacturerContact

WORKBOOK_NAME = "Manufacturer_MI_Contact_Database (1).xlsx"
SHEET = "Manufacturer Contacts"


def resolve_excel_path() -> Path:
    """Locate the seed workbook, checked in order:

      1. $SEED_EXCEL_PATH — explicit override
      2. next to this file
      3. the repository root — where the workbook actually lives, and what
         works for a local run or a Render deploy (rootDir=backend, but the
         whole repo is checked out)

    Heroku is the case that needs the override: its monorepo buildpack
    promotes $APP_BASE to the build root and discards everything above it, so
    the repo-root copy is NOT in the slug and `heroku run python seed.py`
    cannot find it. Seed from a local checkout with DATABASE_URL pointed at
    the target database instead, or set SEED_EXCEL_PATH.
    """
    override = (os.getenv("SEED_EXCEL_PATH") or "").strip()
    if override:
        p = Path(override).expanduser()
        if not p.is_file():
            raise SystemExit(f"SEED_EXCEL_PATH={p} — no such file.")
        return p

    here = Path(__file__).resolve()
    searched = [here.parent / WORKBOOK_NAME, here.parents[1] / WORKBOOK_NAME]
    for candidate in searched:
        if candidate.is_file():
            return candidate

    raise SystemExit(
        f"Could not find {WORKBOOK_NAME!r}. Looked in:\n"
        + "".join(f"  {c.parent}\n" for c in searched)
        + "If this is a Heroku dyno, the workbook is not in the slug — the "
        "monorepo buildpack keeps only $APP_BASE. Run this script from a "
        "local checkout with DATABASE_URL pointed at the target database, or "
        "set SEED_EXCEL_PATH to a readable copy."
    )

COLUMN_MAP = {
    1: "manufacturer",
    2: "parent_owner",
    3: "preferred_channel",
    4: "official_mi_email",
    5: "team_verified_email",
    6: "email_deliverable",
    7: "mi_web_form_url",
    8: "mi_phone",
    9: "mi_phone_hours",
    10: "mi_fax",
    11: "hcp_portal_url",
    12: "hcp_registration_required",
    13: "typical_response_sla",
    14: "last_outreach_date",
    15: "last_outreach_status",
    16: "notes",
}

PLACEHOLDERS = {None, "", "—", "-", "N/A", "n/a"}


def clean(value):
    if value in PLACEHOLDERS:
        return None
    if isinstance(value, str):
        value = value.strip()
        if value in PLACEHOLDERS:
            return None
    return value


def to_date(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
    return None


def load_rows(excel_path: Path):
    wb = openpyxl.load_workbook(excel_path, data_only=True)
    ws = wb[SHEET]
    rows = list(ws.iter_rows(values_only=True))
    for raw in rows[1:]:
        if not raw or raw[1] is None:
            continue
        record = {}
        for idx, field in COLUMN_MAP.items():
            val = clean(raw[idx])
            if field == "last_outreach_date":
                val = to_date(val)
            record[field] = val
        if record.get("manufacturer"):
            yield record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Wipe table before seeding")
    args = parser.parse_args()

    # Resolve the workbook first — a missing file should fail before we touch
    # the database, not after creating tables.
    excel_path = resolve_excel_path()
    print(f"Seeding from {excel_path}")

    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        existing = db.query(ManufacturerContact).count()
        if existing and not args.force:
            print(f"Table already has {existing} rows. Use --force to reload.")
            return
        if args.force:
            db.query(ManufacturerContact).delete()
            db.commit()
            print("Cleared existing rows.")

        inserted = 0
        for rec in load_rows(excel_path):
            db.add(ManufacturerContact(**rec))
            inserted += 1
        db.commit()
        print(f"Inserted {inserted} manufacturer contact rows.")


if __name__ == "__main__":
    sys.exit(main())
