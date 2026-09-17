"""
DailyMed NDC enrichment service.

Pipeline (called once per extract-manufacturers request):
  1. Collect rows that carry an NDC but are missing pi_link / pi_storage.
  2. Batch-check the DB cache (dailymed_cache table) — one SELECT IN query.
  3. For cache misses, fetch DailyMed concurrently (semaphore-capped).
  4. Persist new results to the DB cache.
  5. Apply link + storage text to the row objects in-place.

DailyMed API (no HTML scraping, no JS rendering):
  GET /dailymed/services/v2/spls.json?ndc=<NDC>&pagesize=1
      → JSON: { "data": [{ "setid": "...", "title": "...", "published_date": "..." }] }
  GET /dailymed/services/v2/spls/<setid>.xml
      → HL7 CDA/SPL XML (application/xml)

Storage text lives in section code 34069-5 ("HOW SUPPLIED SECTION").
Note: LOINC 44425-7 does NOT appear in current DailyMed SPLs; 34069-5 is correct.

Canonical product URL: https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid=<setid>
This is constructed from the setid — it is not embedded in the XML.

Multiple results: DailyMed returns results newest-published-first. When an NDC
resolves to multiple SPLs (rare — occurs for repackager labels), index 0 is used
(most recently updated label). In practice, NDC-specific searches return 0 or 1.

Cache TTL: 30 days. NDC labels change rarely; a monthly refresh is sufficient.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Optional
from xml.etree import ElementTree as ET

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

_DAILYMED_API = "https://dailymed.nlm.nih.gov/dailymed/services/v2"
_DAILYMED_UI  = "https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm"
_HOW_SUPPLIED_CODE  = "34069-5"  # HL7/LOINC code for HOW SUPPLIED SECTION
_STORAGE_CODE       = "44425-7"  # STORAGE AND HANDLING SECTION (nested inside 34069-5)
_HL7_NS = "urn:hl7-org:v3"
_CACHE_TTL_DAYS = 30
_MAX_CONCURRENT = 5
_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY = 1.0           # seconds; doubled on each retry


def _normalize_ndc(ndc: str) -> str:
    """Strip whitespace and Excel float suffixes (.0, .00, …).

    Hyphens are preserved — DailyMed accepts the standard 5-4-2 hyphenated
    format and normalizing them out is not required.
    """
    s = ndc.strip()
    s = re.sub(r"\.0+$", "", s)   # "0143-9504-01.0" → "0143-9504-01"
    return s


def _load_cache(
    ndcs: list[str], db: Session
) -> dict[str, tuple[Optional[str], Optional[str]]]:
    """Batch-load cache entries for *ndcs* that are still within the TTL.

    Returns a dict keyed by normalized NDC → (pi_link, pi_storage).
    Entries older than _CACHE_TTL_DAYS are ignored (treated as misses).
    """
    from models import DailymedCache  # local import to avoid circular at module load

    if not ndcs:
        return {}
    cutoff = datetime.now(timezone.utc) - timedelta(days=_CACHE_TTL_DAYS)
    rows = (
        db.query(DailymedCache)
        .filter(
            DailymedCache.ndc.in_(ndcs),
            DailymedCache.fetched_at >= cutoff,
        )
        .all()
    )
    def _strip(s: Optional[str]) -> Optional[str]:
        return _STORAGE_HEADING_STRIP.sub("", s).strip() if s else s

    return {r.ndc: (r.pi_link, _strip(r.pi_storage)) for r in rows}


def _save_cache(
    results: dict[str, tuple[Optional[str], Optional[str], Optional[str]]],
    db: Session,
) -> None:
    """Upsert (ndc, setid, pi_link, pi_storage, fetched_at) rows.

    *results* maps normalized NDC → (setid, pi_link, pi_storage).
    Uses merge so re-fetching an existing NDC overwrites the stale entry.
    """
    from models import DailymedCache

    now = datetime.now(timezone.utc)
    for ndc, (setid, pi_link, pi_storage) in results.items():
        entry = DailymedCache(
            ndc=ndc,
            setid=setid,
            pi_link=pi_link,
            pi_storage=pi_storage,
            fetched_at=now,
        )
        db.merge(entry)          # INSERT … ON CONFLICT DO UPDATE via session.merge
    try:
        db.commit()
    except Exception as exc:     # noqa: BLE001
        db.rollback()
        log.warning("dailymed: cache write failed: %s", exc)


_STORAGE_HEADING_RE    = re.compile(r"^\s*storage(\s+conditions?)?\s*$", re.IGNORECASE)
_STORAGE_HEADING_STRIP = re.compile(r"^storage(\s+conditions?)?\s*", re.IGNORECASE)


def _extract_storage_text(xml_bytes: bytes) -> Optional[str]:
    """Extract storage conditions text from an HL7 SPL XML document.

    Strategy (first match wins):

    1. Section 44425-7 (STORAGE AND HANDLING SECTION) — nested inside 34069-5
       in modern SPLs. Contains only storage text, no packaging tables.
       Example: Doxorubicin HCl.

    2. Inline paragraph scan within 34069-5 — for SPLs where subheadings
       ("Storage", "Storage Conditions") are <content styleCode="underline|bold">
       elements inside a <paragraph>, with the storage text in the same paragraph
       and any continuation paragraphs before the next heading.
       Example: Bendamustine (BENDEKA).

    3. Returns None if neither strategy finds storage text — no polluted
       fallback to the full section 16 text.

    Uses only stdlib xml.etree.ElementTree.
    """
    try:
        cleaned = re.sub(rb"<\?xml-stylesheet[^?]*\?>", b"", xml_bytes)
        root = ET.fromstring(cleaned)
    except ET.ParseError as exc:
        log.debug("dailymed: XML parse error: %s", exc)
        return None

    ns = {"h": _HL7_NS}

    def _all_text(el) -> list[str]:
        parts: list[str] = []
        if el.text:
            parts.append(el.text)
        for child in el:
            parts.extend(_all_text(child))
            if child.tail:
                parts.append(child.tail)
        return parts

    def _clean(parts: list[str]) -> str:
        raw = " ".join(p.strip() for p in parts if p.strip())
        text = re.sub(r"\s{2,}", " ", raw).strip()
        if len(text) > 2000:
            text = text[:2000].rsplit(" ", 1)[0] + "…"
        return text

    def _paragraph_heading(para_el) -> Optional[str]:
        """Return heading text if paragraph starts with an underlined/bold
        <content> label, else None."""
        for child in para_el:
            tag = child.tag.split("}")[-1]
            if tag == "content":
                style = child.get("styleCode", "").lower()
                if "underline" in style or "bold" in style:
                    return (child.text or "").strip()
            break  # only check the very first child element
        return None

    for sec in root.findall(".//h:section", ns):
        code_el = sec.find("h:code", ns)
        if code_el is not None and code_el.get("code") == _STORAGE_CODE:
            text_el = sec.find("h:text", ns)
            if text_el is not None:
                result = _STORAGE_HEADING_STRIP.sub("", _clean(_all_text(text_el))).strip()
                if result:
                    log.debug("dailymed: storage from 44425-7")
                    return result

    # Some SPLs embed "Storage" as an underlined <content> heading inside a
    # <paragraph>, rather than a separate 44425-7 subsection.
    for sec in root.findall(".//h:section", ns):
        code_el = sec.find("h:code", ns)
        if code_el is None or code_el.get("code") != _HOW_SUPPLIED_CODE:
            continue
        text_el = sec.find("h:text", ns)
        if text_el is None:
            continue

        storage_parts: list[str] = []
        in_storage = False

        for child in text_el:
            tag = child.tag.split("}")[-1]
            if tag != "paragraph":
                # Skip tables, lists, br — they contain NDC/packaging data.
                continue

            heading = _paragraph_heading(child)
            if heading:
                if _STORAGE_HEADING_RE.match(heading):
                    in_storage = True
                    storage_parts.append(_clean(_all_text(child)))
                elif in_storage:
                    break  # hit a new non-storage heading — stop
            elif in_storage:
                part = _clean(_all_text(child))
                if part:
                    storage_parts.append(part)

        if storage_parts:
            log.debug("dailymed: storage from inline paragraph scan in 34069-5")
            combined = " ".join(storage_parts)
            return _STORAGE_HEADING_STRIP.sub("", combined).strip()

    return None


async def _http_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: Optional[dict] = None,
) -> httpx.Response:
    """GET with exponential-backoff retry on transient errors (429, 503, timeout)."""
    delay = _RETRY_BASE_DELAY
    last_exc: Optional[Exception] = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            r = await client.get(url, params=params, timeout=15.0)
            if r.status_code in (429, 503) and attempt < _RETRY_ATTEMPTS - 1:
                log.debug("dailymed: %s → %s, retrying in %.1fs", url, r.status_code, delay)
                await asyncio.sleep(delay)
                delay *= 2
                continue
            return r
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            last_exc = exc
            if attempt < _RETRY_ATTEMPTS - 1:
                log.debug("dailymed: network error %s, retrying in %.1fs", exc, delay)
                await asyncio.sleep(delay)
                delay *= 2
    raise last_exc or RuntimeError("unreachable")


async def _lookup_ndc_api(
    ndc: str, client: httpx.AsyncClient
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Call the DailyMed API for a single NDC.

    Returns (setid, pi_link, pi_storage). All three are None on any failure
    (network error, NDC not found, section absent). This function never raises.
    """
    try:
        r = await _http_get(
            client,
            f"{_DAILYMED_API}/spls.json",
            params={"ndc": ndc, "pagesize": "1"},
        )
        if r.status_code == 404:
            log.debug("dailymed: NDC %r not found (404)", ndc)
            return None, None, None
        r.raise_for_status()

        data = r.json().get("data", [])
        if not data:
            log.debug("dailymed: NDC %r → 0 results", ndc)
            return None, None, None

        # Index 0 is the most-recently-published SPL when multiple exist.
        record = data[0]
        setid = record.get("setid")
        if not setid:
            return None, None, None

        pi_link = f"{_DAILYMED_UI}?setid={setid}"
        log.debug("dailymed: NDC %r → setid=%s title=%r", ndc, setid, record.get("title", "")[:60])

        xml_r = await _http_get(client, f"{_DAILYMED_API}/spls/{setid}.xml")
        xml_r.raise_for_status()

        pi_storage = _extract_storage_text(xml_r.content)
        if not pi_storage:
            log.debug("dailymed: setid=%s has no section %s", setid, _HOW_SUPPLIED_CODE)

        return setid, pi_link, pi_storage

    except Exception as exc:   # noqa: BLE001
        log.warning("dailymed: lookup failed for NDC %r: %s", ndc, exc)
        return None, None, None


async def enrich_rows(rows: list, db: Session) -> None:
    """Fill in *pi_link* and *pi_storage* in-place for rows that carry an NDC
    but are missing those fields.

    *rows* is any list of objects with attributes: ndc, pi_link, pi_storage.
    In practice these are ManufacturerMatch instances from excel_service.

    Steps:
      1. Group rows by normalized NDC (skipping rows without NDC or already complete).
      2. Batch-check the DB cache for all NDCs at once.
      3. Fetch the remainder concurrently, capped at _MAX_CONCURRENT.
      4. Write new results to the DB cache (upsert).
      5. Apply link + storage to the rows.

    Never raises — any per-NDC failure is logged and that row is left unchanged.
    """
    ndcs_to_rows: dict[str, list] = {}
    for row in rows:
        ndc = getattr(row, "ndc", None)
        if not ndc:
            continue
        if getattr(row, "pi_link", None) and getattr(row, "pi_storage", None):
            continue
        norm = _normalize_ndc(ndc)
        if norm:
            ndcs_to_rows.setdefault(norm, []).append(row)

    if not ndcs_to_rows:
        return

    log.info("dailymed.enrich: %d unique NDCs to resolve", len(ndcs_to_rows))

    cache = _load_cache(list(ndcs_to_rows.keys()), db)
    to_fetch: dict[str, list] = {}

    for ndc_norm, rows_for_ndc in ndcs_to_rows.items():
        if ndc_norm in cache:
            link, storage = cache[ndc_norm]
            _apply(rows_for_ndc, link, storage)
        else:
            to_fetch[ndc_norm] = rows_for_ndc

    if not to_fetch:
        log.info("dailymed.enrich: all %d NDCs served from cache", len(ndcs_to_rows))
        return

    log.info("dailymed.enrich: %d cache misses → fetching from DailyMed", len(to_fetch))

    sem = asyncio.Semaphore(_MAX_CONCURRENT)

    async def fetch_one(ndc_norm: str):
        async with sem:
            return ndc_norm, await _lookup_ndc_api(ndc_norm, client)

    new_results: dict[str, tuple[Optional[str], Optional[str], Optional[str]]] = {}
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers={"User-Agent": "InpharmD-DailyMed/1.0 (contact: druginfo@inpharmd.com)"},
    ) as client:
        outcomes = await asyncio.gather(
            *[fetch_one(n) for n in to_fetch],
            return_exceptions=True,
        )

    for outcome in outcomes:
        if isinstance(outcome, Exception):
            log.warning("dailymed.enrich: unexpected gather error: %s", outcome)
            continue
        ndc_norm, (setid, link, storage) = outcome
        new_results[ndc_norm] = (setid, link, storage)
        _apply(to_fetch[ndc_norm], link, storage)

    if new_results:
        _save_cache(new_results, db)
        log.info("dailymed.enrich: cached %d new entries", len(new_results))


def _apply(
    rows: list,
    pi_link: Optional[str],
    pi_storage: Optional[str],
) -> None:
    """Write link/storage onto each row, respecting existing values."""
    for row in rows:
        if pi_link and not getattr(row, "pi_link", None):
            row.pi_link = pi_link
        if pi_storage and not getattr(row, "pi_storage", None):
            row.pi_storage = pi_storage


# Drug/NDC → manufacturer suggestion (Manual Contact Mfr auto-select).
# Independent of the NDC → PI/storage pipeline above: separate cache, inputs, outputs.

_HTML_SEARCH_URL = "https://dailymed.nlm.nih.gov/dailymed/search.cfm"
_JSON_SEARCH_PAGESIZE = 200
_HTML_SEARCH_PAGESIZE = 200
_DRUGNAME_CACHE_TTL_DAYS = 30
_DRUGNAME_CACHE_LEASE_MINUTES = 2

_SETID_RE = re.compile(r"drugInfo\.cfm\?setid=([0-9a-fA-F-]{36})")
_REPACKAGED_MARK = "This is a repackaged label."
_INACTIVATED_NDC_MARK = "Contains inactivated NDC Code(s)"
_LABELER_RE = re.compile(r"Labeler\s*-\s*</span>\s*([^(<\r\n]+)")


def _normalize_drug_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def _parse_labeler(html: str) -> Optional[str]:
    m = _LABELER_RE.search(html)
    return m.group(1).strip() if m else None


async def _lookup_ndc_for_manufacturer(
    ndc: str, client: httpx.AsyncClient
) -> Optional[tuple[Optional[str], bool]]:
    """CASE A. Returns (labeler_name, is_repackaged_label) — no exclusion applied.
    Returns None on any failure or not-found."""
    ndc_norm = _normalize_ndc(ndc)
    try:
        r = await _http_get(
            client, f"{_DAILYMED_API}/spls.json", params={"ndc": ndc_norm, "pagesize": "1"}
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data:
            return None
        setid = data[0].get("setid")
        if not setid:
            return None
        detail_r = await _http_get(client, _DAILYMED_UI, params={"setid": setid})
        detail_r.raise_for_status()
        html = detail_r.text
    except Exception as exc:  # noqa: BLE001
        log.warning("dailymed.ndc_manufacturer: lookup failed for NDC %r: %s", ndc, exc)
        return None
    return _parse_labeler(html), _REPACKAGED_MARK in html


def _split_html_result_blocks(html: str) -> list[tuple[str, str]]:
    """Split into (setid, block_text) pairs keyed by setid — HTML row order
    does not match the JSON API's order for the same query."""
    matches = list(_SETID_RE.finditer(html))
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for i, m in enumerate(matches):
        setid = m.group(1)
        if setid in seen:
            continue
        seen.add(setid)
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(html)
        out.append((setid, html[start:end]))
    return out


async def _crawl_json_setids(name: str, client: httpx.AsyncClient) -> tuple[list[str], bool]:
    """Paginate the JSON drug-name search to completion. ok=False means a
    core failure — not a genuine zero-result answer."""
    setids: list[str] = []
    page = 1
    while True:
        try:
            r = await _http_get(
                client,
                f"{_DAILYMED_API}/spls.json",
                params={"drug_name": name, "pagesize": str(_JSON_SEARCH_PAGESIZE), "page": str(page)},
            )
            r.raise_for_status()
            payload = r.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("dailymed.drugname: JSON search failed for %r page %d: %s", name, page, exc)
            return [], False
        for rec in payload.get("data", []):
            setid = rec.get("setid")
            if setid:
                setids.append(setid)
        next_page_url = payload.get("metadata", {}).get("next_page_url")
        if not next_page_url or next_page_url == "null":
            break
        page += 1
    return setids, True


async def _crawl_html_repackaged_map(
    name: str, client: httpx.AsyncClient
) -> tuple[dict[str, bool], bool]:
    """Paginate the HTML search-results page to completion, building a
    setid -> is_repackaged_label map. Returns ({}, False) on a core failure."""
    result: dict[str, bool] = {}
    page = 1
    while True:
        try:
            r = await _http_get(
                client,
                _HTML_SEARCH_URL,
                params={
                    "query": name,
                    "searchdb": "all",
                    "labeltype": "all",
                    "audience": "professional",
                    "pagesize": str(_HTML_SEARCH_PAGESIZE),
                    "page": str(page),
                },
            )
            r.raise_for_status()
            html = r.text
        except Exception as exc:  # noqa: BLE001
            log.warning("dailymed.drugname: HTML search failed for %r page %d: %s", name, page, exc)
            return {}, False

        blocks = _split_html_result_blocks(html)
        for setid, block in blocks:
            result[setid] = _REPACKAGED_MARK in block

        total_m = re.search(r"([\d,]+)\s+[Rr]esults", html)
        total = int(total_m.group(1).replace(",", "")) if total_m else len(result)
        if not blocks or len(result) >= total or len(blocks) < _HTML_SEARCH_PAGESIZE:
            break
        page += 1
    return result, True


async def _fetch_detail_flags(
    setid: str, client: httpx.AsyncClient
) -> Optional[tuple[Optional[str], bool]]:
    """Returns (labeler_name, is_inactivated_ndc), or None on failure —
    caller treats None as a failed, excluded candidate."""
    try:
        r = await _http_get(client, _DAILYMED_UI, params={"setid": setid})
        r.raise_for_status()
        html = r.text
    except Exception as exc:  # noqa: BLE001
        log.warning("dailymed.drugname: detail page failed for setid=%s: %s", setid, exc)
        return None
    return _parse_labeler(html), _INACTIVATED_NDC_MARK in html


async def _crawl_drug_name(name: str, client: httpx.AsyncClient) -> tuple[list[str], bool]:
    """CASE B core crawl. complete=False on any core or candidate failure —
    caller must not cache an incomplete result."""
    setids, json_ok = await _crawl_json_setids(name, client)
    if not json_ok:
        return [], False

    repackaged_map, html_ok = await _crawl_html_repackaged_map(name, client)
    if not html_ok:
        return [], False

    survivors: list[str] = []
    complete = True
    sem = asyncio.Semaphore(_MAX_CONCURRENT)

    async def check_one(setid: str) -> Optional[str]:
        nonlocal complete
        is_repackaged = repackaged_map.get(setid)
        if is_repackaged is None:
            # Missing from the HTML map — fail closed, unverifiable.
            complete = False
            return None
        if is_repackaged:
            return None
        async with sem:
            detail = await _fetch_detail_flags(setid, client)
        if detail is None:
            complete = False
            return None
        labeler, is_inactivated = detail
        if is_inactivated or not labeler:
            return None
        return labeler

    results = await asyncio.gather(*(check_one(s) for s in setids), return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            complete = False
            continue
        if r:
            survivors.append(r)
    return survivors, complete


def _claim_drugname(db: Session, name_norm: str) -> bool:
    """Atomically claim the right to crawl `name_norm` via a short lease —
    no DB connection is held during the external crawl itself."""
    row = db.execute(
        text(
            """
            INSERT INTO dailymed_drugname_cache (drug_name_normalized, claimed_at)
            VALUES (:name, now())
            ON CONFLICT (drug_name_normalized) DO UPDATE
              SET claimed_at = now()
              WHERE (
                    dailymed_drugname_cache.fetched_at IS NULL
                 OR dailymed_drugname_cache.fetched_at < now() - make_interval(days => :ttl_days)
              )
              AND (
                    dailymed_drugname_cache.claimed_at IS NULL
                 OR dailymed_drugname_cache.claimed_at < now() - make_interval(mins => :lease_mins)
              )
            RETURNING drug_name_normalized
            """
        ),
        {"name": name_norm, "ttl_days": _DRUGNAME_CACHE_TTL_DAYS, "lease_mins": _DRUGNAME_CACHE_LEASE_MINUTES},
    ).first()
    db.commit()
    return row is not None


def _get_fresh_drugname_cache(db: Session, name_norm: str) -> Optional[list[str]]:
    row = db.execute(
        text(
            """
            SELECT labeler_names FROM dailymed_drugname_cache
            WHERE drug_name_normalized = :name
              AND fetched_at IS NOT NULL
              AND fetched_at >= now() - make_interval(days => :ttl_days)
            """
        ),
        {"name": name_norm, "ttl_days": _DRUGNAME_CACHE_TTL_DAYS},
    ).first()
    if not row or not row[0]:
        return None
    try:
        return json.loads(row[0])
    except (ValueError, TypeError):
        return None


def _finalize_drugname_cache(db: Session, name_norm: str, labeler_names: Optional[list[str]]) -> None:
    """Release the lease. labeler_names=None means an incomplete crawl —
    leaves any prior cache untouched and writes nothing new."""
    if labeler_names is not None:
        db.execute(
            text(
                """
                UPDATE dailymed_drugname_cache
                SET labeler_names = :names, fetched_at = now(), claimed_at = NULL
                WHERE drug_name_normalized = :name
                """
            ),
            {"name": name_norm, "names": json.dumps(labeler_names)},
        )
    else:
        db.execute(
            text("UPDATE dailymed_drugname_cache SET claimed_at = NULL WHERE drug_name_normalized = :name"),
            {"name": name_norm},
        )
    db.commit()


async def _suggest_by_drug_name(db: Session, name: str, client: httpx.AsyncClient) -> dict:
    name_norm = _normalize_drug_name(name)
    cached = _get_fresh_drugname_cache(db, name_norm)
    if cached is not None:
        return {"labeler_names": cached, "repackaged_labeler_names": []}

    won_claim = _claim_drugname(db, name_norm)
    if not won_claim:
        # Someone else is already crawling — don't duplicate the work.
        cached = _get_fresh_drugname_cache(db, name_norm)
        return {"labeler_names": cached or [], "repackaged_labeler_names": []}

    try:
        labeler_names, complete = await _crawl_drug_name(name, client)
    except Exception:  # noqa: BLE001
        log.warning("dailymed.drugname: crawl failed unexpectedly for %r", name, exc_info=True)
        labeler_names, complete = [], False

    _finalize_drugname_cache(db, name_norm, labeler_names if complete else None)
    return {"labeler_names": labeler_names, "repackaged_labeler_names": []}


async def suggest_manufacturers(db: Session, *, ndc: str, drug_name: str) -> dict:
    """Entry point for the Manual Contact Mfr DailyMed suggestion feature.
    Never raises. Multi-NDC (semicolon-joined) is out of scope — no lookup."""
    ndc_val = (ndc or "").strip()
    if ";" in ndc_val:
        return {"labeler_names": [], "repackaged_labeler_names": []}

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            headers={"User-Agent": "InpharmD-DailyMed/1.0 (contact: druginfo@inpharmd.com)"},
        ) as client:
            if ndc_val:
                result = await _lookup_ndc_for_manufacturer(ndc_val, client)
                if not result or not result[0]:
                    return {"labeler_names": [], "repackaged_labeler_names": []}
                labeler, is_repackaged = result
                return {
                    "labeler_names": [labeler],
                    "repackaged_labeler_names": [labeler] if is_repackaged else [],
                }

            name = (drug_name or "").strip()
            if not name:
                return {"labeler_names": [], "repackaged_labeler_names": []}
            return await _suggest_by_drug_name(db, name, client)
    except Exception:  # noqa: BLE001
        log.warning("dailymed.suggest_manufacturers: unexpected failure", exc_info=True)
        return {"labeler_names": [], "repackaged_labeler_names": []}
