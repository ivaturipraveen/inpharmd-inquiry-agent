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
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Optional
from xml.etree import ElementTree as ET

import httpx
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

_DAILYMED_API = "https://dailymed.nlm.nih.gov/dailymed/services/v2"
_DAILYMED_UI  = "https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm"
_HOW_SUPPLIED_CODE = "34069-5"   # HL7/LOINC code for HOW SUPPLIED SECTION
_HL7_NS = "urn:hl7-org:v3"
_CACHE_TTL_DAYS = 30
_MAX_CONCURRENT = 5
_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY = 1.0           # seconds; doubled on each retry


# ─────────────────────── NDC normalisation ───────────────────────

def _normalize_ndc(ndc: str) -> str:
    """Strip whitespace and Excel float suffixes (.0, .00, …).

    Hyphens are preserved — DailyMed accepts the standard 5-4-2 hyphenated
    format and normalizing them out is not required.
    """
    s = ndc.strip()
    s = re.sub(r"\.0+$", "", s)   # "0143-9504-01.0" → "0143-9504-01"
    return s


# ─────────────────────────── DB cache ────────────────────────────

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
    return {r.ndc: (r.pi_link, r.pi_storage) for r in rows}


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


# ──────────────────────── XML parsing ────────────────────────────

def _extract_storage_text(xml_bytes: bytes) -> Optional[str]:
    """Extract the full text content of section 34069-5 (HOW SUPPLIED SECTION)
    from an HL7 SPL XML document.

    Returns None if the section is absent or the XML cannot be parsed.
    Uses only stdlib xml.etree.ElementTree — no third-party parser required.
    """
    try:
        # The SPL XML contains a <?xml-stylesheet …?> processing instruction
        # that ElementTree rejects. Strip it before parsing.
        cleaned = re.sub(rb"<\?xml-stylesheet[^?]*\?>", b"", xml_bytes)
        root = ET.fromstring(cleaned)
    except ET.ParseError as exc:
        log.debug("dailymed: XML parse error: %s", exc)
        return None

    ns = {"h": _HL7_NS}

    def _all_text(el) -> list[str]:
        """Recursive text extraction including mixed-content children."""
        parts: list[str] = []
        if el.text:
            parts.append(el.text)
        for child in el:
            parts.extend(_all_text(child))
            if child.tail:
                parts.append(child.tail)
        return parts

    for sec in root.findall(".//h:section", ns):
        code_el = sec.find("h:code", ns)
        if code_el is None or code_el.get("code") != _HOW_SUPPLIED_CODE:
            continue
        text_el = sec.find("h:text", ns)
        if text_el is None:
            return None
        raw_parts = [p.strip() for p in _all_text(text_el) if p.strip()]
        raw = " ".join(raw_parts)
        # Collapse runs of whitespace introduced by nested tags
        storage = re.sub(r"\s{2,}", " ", raw).strip()
        if len(storage) > 2000:
            # Truncate at a word boundary so the text remains readable
            storage = storage[:2000].rsplit(" ", 1)[0] + "…"
        return storage or None

    return None   # section 34069-5 not found in this SPL


# ─────────────────────── DailyMed API calls ──────────────────────

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
        # Step 1: resolve NDC → setid via JSON search endpoint
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

        # Step 2: fetch the full SPL XML and extract section 34069-5
        xml_r = await _http_get(client, f"{_DAILYMED_API}/spls/{setid}.xml")
        xml_r.raise_for_status()

        pi_storage = _extract_storage_text(xml_r.content)
        if not pi_storage:
            log.debug("dailymed: setid=%s has no section %s", setid, _HOW_SUPPLIED_CODE)

        return setid, pi_link, pi_storage

    except Exception as exc:   # noqa: BLE001
        log.warning("dailymed: lookup failed for NDC %r: %s", ndc, exc)
        return None, None, None


# ──────────────────────── Public interface ───────────────────────

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
    # ── Step 1: collect work ──────────────────────────────────────
    ndcs_to_rows: dict[str, list] = {}
    for row in rows:
        ndc = getattr(row, "ndc", None)
        if not ndc:
            continue
        # Skip rows that are already fully populated
        if getattr(row, "pi_link", None) and getattr(row, "pi_storage", None):
            continue
        norm = _normalize_ndc(ndc)
        if norm:
            ndcs_to_rows.setdefault(norm, []).append(row)

    if not ndcs_to_rows:
        return

    log.info("dailymed.enrich: %d unique NDCs to resolve", len(ndcs_to_rows))

    # ── Step 2: batch cache check ─────────────────────────────────
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

    # ── Step 3: concurrent HTTP fetches ──────────────────────────
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

    # ── Step 4: apply and collect for cache write ─────────────────
    for outcome in outcomes:
        if isinstance(outcome, Exception):
            log.warning("dailymed.enrich: unexpected gather error: %s", outcome)
            continue
        ndc_norm, (setid, link, storage) = outcome
        new_results[ndc_norm] = (setid, link, storage)
        _apply(to_fetch[ndc_norm], link, storage)

    # ── Step 5: persist to DB cache ───────────────────────────────
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
