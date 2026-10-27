"""Fully autonomous InpharmD inquiry intake + dispatch — no human in the loop.

Scheduler tick (see scheduler.py's `_poll_and_auto_dispatch` job) that:
  1. Fetches open MUE inquiries from staging via inpharmd_service.list_inquiries,
     using a single designated poller User's staging_token (INPHARMD_POLLER_USER_EMAIL).
  2. Skips any inquiry_uuid for which a local Inquiry row already exists —
     InpharmD inquiries are immutable once created/sent, so this single existence
     check is a safe, sufficient dedup with no new schema (per explicit product
     decision; see PR description).
  3. For each unseen inquiry, resolves target manufacturers using EXACTLY the
     same two paths the manual Contact Manufacturer page already offers —
     via SHARED functions, not re-implemented logic, so the two flows cannot
     silently diverge:
       a. Excel/MUE workbook attachment (.xlsx/.csv) -> excel_service.
          match_manufacturers_from_url (also used by routers/external_inquiries.py's
          /extract-manufacturers endpoint).
       b. No such attachment -> summary_service.extract_drug_name on the inquiry's own
          title/mue_details text, then dailymed_service.suggest_and_match_manufacturers
          (also used by routers/inquiries.py's /manufacturer-suggestions-preview endpoint).
  4. Every matched manufacturer is auto-contacted, grouped by its own
     preferred_channel (email/call) and dispatched via routers.inquiries.
     bulk_create_inquiries verbatim — the same function the manual flow's
     "Send Email"/"Call" buttons call — so staggered email scheduling,
     business-hours call scheduling, and failure handling are identical to a
     human-triggered dispatch. Manufacturers preferring Web Form, or with no
     supported preferred_channel, are created as drafts only (dispatch_channel=
     "none") — Web Form submission stays human-triggered, by explicit decision.
  5. Unmatched manufacturers get nothing auto-created; they're left entirely for
     the existing manual Contact Manufacturer flow (which already shows an
     unmatched-manufacturer note) — never silently discarded, never auto-created.

No new deduplication mechanism, schema, or matching logic is introduced here —
every matching/dispatch primitive above already existed; this module only
sequences them without a human click.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import dailymed_service
import excel_service
import inpharmd_service
import summary_service
from database import SessionLocal
from models import AutoDispatchIgnoredInquiry, AutoDispatchSetting, Inquiry, ManufacturerContact, User
from schemas import BulkInquiryCreate, BulkTarget, SourceAttachment

log = logging.getLogger("inquiry.auto_dispatch")

_SETTING_ID = 1


class AutoDispatchEnableError(Exception):
    """Raised by enable() when the pre-existing-inquiry snapshot can't be
    taken — the flag must stay off rather than risk auto-processing every
    pre-existing inquiry with an empty baseline."""


def get_setting(db: Session) -> AutoDispatchSetting:
    """Always returns a row (creates the id=1 singleton, disabled, on first
    read). Safe under concurrent first-reads: if two requests race to
    create the row, the loser's IntegrityError on the fixed id=1 primary key
    is caught and it simply re-fetches the winner's row instead of 500ing."""
    setting = db.get(AutoDispatchSetting, _SETTING_ID)
    if setting:
        return setting
    db.add(AutoDispatchSetting(id=_SETTING_ID, enabled=False))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
    return db.get(AutoDispatchSetting, _SETTING_ID)


def enable(db: Session, user: User) -> AutoDispatchSetting:
    """Turn autonomous dispatch on. Atomic: snapshot every currently-open
    InpharmD inquiry_uuid as permanently ineligible for THIS activation, then
    flip the flag — both in one commit. If the snapshot fetch fails for any
    reason, raises AutoDispatchEnableError and makes NO changes; the flag
    must never end up enabled with a stale/empty/partial baseline, since that
    would make pre-existing inquiries eligible by omission."""
    poller = _poller_user(db)
    if not poller:
        raise AutoDispatchEnableError(
            "Cannot enable: INPHARMD_POLLER_USER_EMAIL is not configured, or that user "
            "has no staging_token. The pre-existing-inquiry snapshot requires this account."
        )
    try:
        data: Any = inpharmd_service.list_inquiries(poller.staging_token, page=1, per_page=5000)
    except Exception as e:
        raise AutoDispatchEnableError(f"Could not fetch the current InpharmD inquiry list: {e}") from e
    items = data.get("data") if isinstance(data, dict) else data
    uuids = [item.get("inquiry_uuid") for item in (items or []) if isinstance(item, dict) and item.get("inquiry_uuid")]

    db.query(AutoDispatchIgnoredInquiry).delete()
    if uuids:
        db.bulk_insert_mappings(AutoDispatchIgnoredInquiry, [{"inquiry_uuid": u} for u in uuids])

    setting = get_setting(db)
    setting.enabled = True
    setting.activated_at = _now()
    setting.updated_by_user_id = user.id
    db.commit()
    db.refresh(setting)
    log.info(
        "auto_dispatch: ENABLED by user %s — snapshotted %s pre-existing inquiry(ies) as ineligible",
        user.email, len(uuids),
    )
    return setting


def disable(db: Session, user: User) -> AutoDispatchSetting:
    setting = get_setting(db)
    setting.enabled = False
    setting.updated_by_user_id = user.id
    db.commit()
    db.refresh(setting)
    log.info("auto_dispatch: DISABLED by user %s", user.email)
    return setting


def _now() -> datetime:
    return datetime.now(timezone.utc)

_EXTRACTABLE_ATTACHMENT_SUFFIXES = (".xlsx", ".csv")

# Placeholder only — bulk_create_inquiries always overwrites Inquiry.subject
# with "Drug information request [InpharmD #<id>]" once the row exists.
_PENDING_SUBJECT = "Drug information request"


def _is_extractable(att: dict) -> bool:
    name = (att.get("file_name") or "").lower()
    url = (att.get("doc_url") or "").lower()
    return name.endswith(_EXTRACTABLE_ATTACHMENT_SUFFIXES) or any(
        s in url for s in _EXTRACTABLE_ATTACHMENT_SUFFIXES
    )


def _poller_user(db: Session) -> Optional[User]:
    email = (os.getenv("INPHARMD_POLLER_USER_EMAIL") or "").strip().lower()
    if not email:
        log.warning("auto_dispatch: INPHARMD_POLLER_USER_EMAIL not set; skipping tick")
        return None
    user = db.query(User).filter(User.email == email).first()
    if not user or not user.staging_token:
        log.warning(
            "auto_dispatch: poller user %r not found or has no staging_token; skipping tick",
            email,
        )
        return None
    return user


def _resolved_channel(mfr: ManufacturerContact) -> str:
    """Mirrors frontend/src/utils/channelResolution.ts resolvePreferredChannel."""
    v = (mfr.preferred_channel or "").strip().lower()
    if v == "email":
        return "email"
    if v == "phone":
        return "call"
    if v == "web form":
        return "webform"
    return "unsupported"


async def _match_via_excel(
    item: dict, access_token: str, mfrs: list, db: Session,
) -> tuple[list, Optional[str], Optional[str], Optional[int]]:
    """Try each extractable attachment in order; return matches from the
    first one that parses. Delegates to excel_service.match_manufacturers_from_url
    — the same orchestration /api/external/inquiries/extract-manufacturers uses
    — so the two flows can never diverge. Stops at the first successfully-parsed
    workbook (one MUE workbook per inquiry in practice) — returns (matches,
    source_excel_url, source_excel_sheet, source_excel_attachment_id).
    `mfrs` is the caller's already-fetched ManufacturerContact list (never
    re-queried here)."""
    for att in item.get("attachments") or []:
        if not _is_extractable(att):
            continue
        doc_url = att.get("doc_url")
        if not doc_url:
            continue
        try:
            _xlsx, matches, loc, _extra = await excel_service.match_manufacturers_from_url(
                doc_url, access_token=access_token, manufacturers=mfrs, db=db,
            )
        except Exception:
            log.exception(
                "auto_dispatch: attachment extraction failed for inquiry %s, attachment %s",
                item.get("inquiry_uuid"), att.get("file_name"),
            )
            continue
        return matches, doc_url, loc.sheet_name, att.get("id")
    return [], None, None, None


async def _match_via_drug_name(db: Session, item: dict, mfrs: list) -> list:
    """`mfrs` is the caller's already-fetched ManufacturerContact list (never
    re-queried here). Delegates to dailymed_service.suggest_and_match_manufacturers
    — the same orchestration /api/inquiries/manufacturer-suggestions-preview
    uses — so the two flows can never diverge."""
    if not summary_service.is_configured():
        return []
    title = item.get("title") or ""
    mue_details = item.get("mue_details") or ""
    combined = "\n\n".join(p for p in (title, mue_details) if p and p.strip())
    if not combined.strip():
        return []
    try:
        fields = summary_service.extract_drug_name(combined)
    except Exception:
        log.exception("auto_dispatch: drug-name extraction failed for inquiry %s", item.get("inquiry_uuid"))
        return []
    drug_name = fields.get("drug_name") or ""
    ndc = fields.get("ndc") or ""
    if not drug_name and not ndc:
        return []
    matches, _repackaged_names = await dailymed_service.suggest_and_match_manufacturers(
        db, ndc=ndc, drug_name=drug_name, manufacturers=mfrs,
    )
    return matches


async def _process_one_inquiry(
    db: Session, user: User, item: dict, ignored_uuids: set, already_seen_uuids: set, mfrs: list,
) -> None:
    uuid_ = item.get("inquiry_uuid")
    if not uuid_:
        return
    if uuid_ in ignored_uuids or uuid_ in already_seen_uuids:
        return

    matches, source_excel_url, source_excel_sheet, source_excel_attachment_id = await _match_via_excel(
        item, user.staging_token, mfrs, db,
    )
    if not matches:
        matches = await _match_via_drug_name(db, item, mfrs)
        source_excel_url = source_excel_sheet = None
        source_excel_attachment_id = None

    matched = [m for m in matches if m.matched_id is not None]
    if not matched:
        log.info("auto_dispatch: inquiry %s has no matched manufacturers; leaving untouched", uuid_)
        return

    # mfrs is the same list already fetched once for this whole tick —
    # no second query needed to resolve matched_id -> ManufacturerContact.
    mfrs_by_id = {m.id: m for m in mfrs}

    buckets: dict[str, list] = {"email": [], "call": [], "none": []}
    for m in matched:
        mfr = mfrs_by_id.get(m.matched_id)
        if not mfr:
            continue
        resolved = _resolved_channel(mfr)
        bucket_key = resolved if resolved in ("email", "call") else "none"
        buckets[bucket_key].append(m)

    title = item.get("title") or ""
    mue_details = item.get("mue_details") or ""
    question = f"{title}\n\n{mue_details}" if mue_details.strip() else title
    team_name = (item.get("inquiry_submitter_details") or {}).get("team_name")
    attachments = [
        SourceAttachment(file_name=a.get("file_name") or "", doc_url=a.get("doc_url") or "")
        for a in (item.get("attachments") or [])
    ] or None

    from routers.inquiries import bulk_create_inquiries, send_now

    for channel, bucket in buckets.items():
        if not bucket:
            continue
        targets = [
            BulkTarget(
                manufacturer_id=m.matched_id,
                medication_name=m.medication_name or None,
                pi_storage_data=m.pi_storage or None,
                pi_link=m.pi_link or None,
            )
            for m in bucket
        ]
        payload = BulkInquiryCreate(
            targets=targets,
            subject=_PENDING_SUBJECT,
            question=question,
            fallback_after_hours=0,  # sentinel -> 1-minute fallback (see utils/fallback.ts)
            source_inquiry_uuid=uuid_,
            source_excel_url=source_excel_url,
            source_excel_sheet=source_excel_sheet,
            source_excel_attachment_id=source_excel_attachment_id,
            attachments=attachments,
            mue_details=None,
            team_name=team_name,
            dispatch_channel=channel,
        )
        try:
            result = await bulk_create_inquiries(payload, db=db, current_user=user)
            log.info(
                "auto_dispatch: inquiry %s channel=%s created=%s dispatched=%s failed=%s",
                uuid_, channel, len(result.created), result.dispatched, len(result.failed),
            )
        except Exception:
            log.exception(
                "auto_dispatch: bulk_create_inquiries failed for inquiry %s channel=%s",
                uuid_, channel,
            )
            db.rollback()
            continue

        if channel == "email":
            # bulk_create_inquiries' email branch applies the human-UX
            # stagger delay (EMAIL_SCHEDULE_DELAY_MINUTES) meant for a person
            # reviewing a batch before it goes out — there's no human in
            # this loop, so force immediate delivery via the same send-now
            # path the UI's "Send Now" button already uses, without touching
            # bulk_create_inquiries itself (the manual/human-triggered flow
            # keeps its stagger exactly as before).
            for created in result.created:
                try:
                    send_now(inquiry_id=created.id, db=db, current_user=user)
                except Exception:
                    log.exception(
                        "auto_dispatch: immediate send-now failed for inquiry %s; "
                        "left on its originally scheduled time",
                        created.id,
                    )
                    db.rollback()


async def _run_all(
    db: Session, user: User, items: list, ignored_uuids: set, already_seen_uuids: set, mfrs: list,
) -> None:
    for item in items:
        try:
            await _process_one_inquiry(db, user, item, ignored_uuids, already_seen_uuids, mfrs)
        except Exception:
            log.exception(
                "auto_dispatch: unexpected error processing inquiry %s",
                item.get("inquiry_uuid") if isinstance(item, dict) else None,
            )
            db.rollback()


def poll_and_dispatch() -> None:
    """Scheduler tick entry point (sync, called by APScheduler). Never raises.

    First checks the application-level feature flag (AutoDispatchSetting) —
    if disabled, does nothing at all: no InpharmD fetch, no matching, no
    dispatch. This is the single on/off gate shared by the Manufacturer
    Outreach page UI and this scheduler job (see routers/auto_dispatch.py).

    Everything that would otherwise be a per-inquiry query is batched once
    here, up front, for the whole tick: which of this tick's uuids are
    already-ignored/already-seen (two IN-scoped queries instead of one
    query per inquiry), and the manufacturer list (one fetch instead of up
    to three re-fetches per inquiry)."""
    db = SessionLocal()
    try:
        setting = get_setting(db)
        if not setting.enabled:
            return

        user = _poller_user(db)
        if not user:
            return
        try:
            data: Any = inpharmd_service.list_inquiries(user.staging_token, page=1, per_page=5000)
        except Exception:
            log.exception("auto_dispatch: failed to fetch open MUE inquiries")
            return
        items = data.get("data") if isinstance(data, dict) else data
        if not items:
            return

        all_uuids = [
            item.get("inquiry_uuid")
            for item in items
            if isinstance(item, dict) and item.get("inquiry_uuid")
        ]
        if not all_uuids:
            return

        ignored_uuids = {
            row[0]
            for row in db.query(AutoDispatchIgnoredInquiry.inquiry_uuid)
            .filter(AutoDispatchIgnoredInquiry.inquiry_uuid.in_(all_uuids))
            .all()
        }
        already_seen_uuids = {
            row[0]
            for row in db.query(Inquiry.source_inquiry_uuid)
            .filter(Inquiry.source_inquiry_uuid.in_(all_uuids))
            .distinct()
            .all()
        }
        mfrs = db.query(ManufacturerContact).filter(ManufacturerContact.archived_at.is_(None)).all()

        asyncio.run(_run_all(db, user, items, ignored_uuids, already_seen_uuids, mfrs))
    except Exception:
        log.exception("auto_dispatch: tick failed unexpectedly")
    finally:
        db.close()
