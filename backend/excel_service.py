"""Excel parsing + write-back for InpharmD MUE attachments.

The InpharmD platform attaches an .xlsx sheet to MUE inquiries. The sheet has
two relevant columns we care about:

  - "Medication/Vaccine Manufacturer"  → we READ this to auto-select targets
  - "Manufacturer Response"            → we WRITE this once a reply comes back

Both column header strings can vary slightly between sheets (case, punctuation,
extra whitespace) so we match by normalized substring.
"""
from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

import httpx
from openpyxl import load_workbook
from openpyxl.workbook import Workbook

log = logging.getLogger("inquiry.excel")

# Header search patterns are ordered — the parser picks the FIRST tier that
# matches anywhere in the workbook. We prefer the Medication/Vaccine column
# over a generic "Manufacturer" so we don't grab e.g. "Fridge/Freezer
# Manufacturer" by accident in stability-excursion templates.
MANUFACTURER_HEADER_TIERS: tuple[tuple[set[str], tuple[str, ...]], ...] = (
    # Tier 1 — Medication/Vaccine Manufacturer (most specific)
    (
        {"medication", "manufacturer"},
        ("medicationmanufacturer", "vaccinemanufacturer", "medicationvaccinemanufacturer"),
    ),
    (
        {"vaccine", "manufacturer"},
        (),
    ),
    # Tier 2 — Drug / Product Manufacturer
    (
        {"drug", "manufacturer"},
        ("drugmanufacturer", "productmanufacturer"),
    ),
    # Tier 3 — bare "Manufacturer" / fuzzy fallbacks
    (
        {"manufacturer"},
        ("manufacturer", "mfr", "supplier", "vendor"),
    ),
)

# Response column — match exact phrase first to avoid grabbing
# "Manufacturer Stability Site Data" or similar by accident.
RESPONSE_HEADER_TIERS: tuple[tuple[set[str], tuple[str, ...]], ...] = (
    ({"manufacturer", "response"}, ("manufacturerresponse", "mfrresponse")),
    ({"response"}, ("reply",)),
)

_DOWNLOAD_TIMEOUT_SECONDS = 30


def _normalize(s: Any) -> str:
    """Lower-case alphanumeric squashed string for matching."""
    if s is None:
        return ""
    return re.sub(r"[^a-z0-9]+", "", str(s).lower())


def _tokenize(s: Any) -> set[str]:
    """Lower-case word set, used for fuzzy header / name matching."""
    if s is None:
        return set()
    return {t for t in re.split(r"[^a-z0-9]+", str(s).lower()) if t}


def _is_inpharmd_url(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return False
    return "inpharmd" in host or "mercer-inpharmd" in host


def download_excel(doc_url: str, access_token: Optional[str] = None) -> bytes:
    """Fetch the .xlsx bytes. Includes access_token only when the URL points
    at the InpharmD staging app — public/CDN URLs are downloaded as-is."""
    params: dict[str, str] = {}
    if access_token and _is_inpharmd_url(doc_url) and "access_token=" not in doc_url:
        params["access_token"] = access_token
    log.info("excel.download url=%s token=%s", doc_url, "yes" if params else "no")
    with httpx.Client(timeout=_DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=True) as client:
        res = client.get(doc_url, params=params)
        res.raise_for_status()
        return res.content


@dataclass
class ColumnLocation:
    sheet_name: str
    header_row: int  # 1-based
    col: int          # 1-based
    header_value: str


def _find_column_in_tier(
    workbook: Workbook,
    required_tokens: set[str],
    substrings: tuple[str, ...] = (),
    *,
    max_header_scan_rows: int = 20,
) -> Optional[ColumnLocation]:
    """Single-tier scan. Returns the first cell whose tokens match or whose
    normalized text contains a substring."""
    for ws in workbook.worksheets:
        for r_idx, row in enumerate(
            ws.iter_rows(min_row=1, max_row=max_header_scan_rows), start=1
        ):
            for c_idx, cell in enumerate(row, start=1):
                if cell.value is None:
                    continue
                toks = _tokenize(cell.value)
                norm = _normalize(cell.value)
                if required_tokens and required_tokens.issubset(toks):
                    return ColumnLocation(ws.title, r_idx, c_idx, str(cell.value))
                if substrings and any(s in norm for s in substrings):
                    return ColumnLocation(ws.title, r_idx, c_idx, str(cell.value))
    return None


def _find_column(
    workbook: Workbook,
    tiers: tuple[tuple[set[str], tuple[str, ...]], ...],
    *,
    max_header_scan_rows: int = 20,
) -> Optional[ColumnLocation]:
    """Walk the tiers in order. Returns the first match — so
    'Medication/Vaccine Manufacturer' wins over 'Fridge/Freezer Manufacturer'
    in a stability-excursion template."""
    for tokens, substrings in tiers:
        hit = _find_column_in_tier(
            workbook, tokens, substrings, max_header_scan_rows=max_header_scan_rows
        )
        if hit is not None:
            return hit
    return None


def _scan_headers(workbook: Workbook, *, max_rows: int = 20) -> list[str]:
    """Return every non-empty cell from the first `max_rows` of each sheet —
    used to build a helpful error message when no manufacturer column found."""
    found: list[str] = []
    for ws in workbook.worksheets:
        for r_idx, row in enumerate(
            ws.iter_rows(min_row=1, max_row=max_rows), start=1
        ):
            for c_idx, cell in enumerate(row, start=1):
                v = cell.value
                if v is None:
                    continue
                s = str(v).strip()
                if not s:
                    continue
                found.append(f"{ws.title}!R{r_idx}C{c_idx}={s[:60]}")
                if len(found) >= 60:
                    return found
    return found


@dataclass
class ExtractedRow:
    row_index: int       # 1-based Excel row
    raw_name: str        # value as it appeared in the cell


def extract_manufacturer_rows(xlsx_bytes: bytes) -> tuple[list[ExtractedRow], ColumnLocation]:
    """Open the workbook in read-only mode and return every non-empty cell
    under the 'Medication/Vaccine Manufacturer' column.

    Raises ValueError if the column can't be found.
    """
    # First-pass uses read_only for speed. If we can't find the column we open
    # again in normal mode so _scan_headers can produce a useful error.
    wb = load_workbook(filename=io.BytesIO(xlsx_bytes), read_only=True, data_only=True)
    try:
        loc = _find_column(wb, MANUFACTURER_HEADER_TIERS)
        if loc is None:
            wb.close()
            wb2 = load_workbook(filename=io.BytesIO(xlsx_bytes), data_only=True)
            seen = _scan_headers(wb2)
            wb2.close()
            preview = "; ".join(seen[:10]) if seen else "(no header cells found)"
            raise ValueError(
                "Could not find a Manufacturer column in the workbook. "
                f"Headers I saw: {preview}"
            )
        ws = wb[loc.sheet_name]
        rows: list[ExtractedRow] = []
        # iter_rows is much cheaper than ws.cell() on big sheets in read-only mode
        for r_idx, row in enumerate(
            ws.iter_rows(min_row=loc.header_row + 1, min_col=loc.col, max_col=loc.col),
            start=loc.header_row + 1,
        ):
            cell = row[0]
            val = cell.value
            if val is None:
                continue
            name = str(val).strip()
            if not name:
                continue
            rows.append(ExtractedRow(row_index=r_idx, raw_name=name))
        return rows, loc
    finally:
        wb.close()


# ──────────────────────────── Matching ────────────────────────────


@dataclass
class ManufacturerMatch:
    row_index: int
    raw_name: str
    matched_id: Optional[int]
    matched_name: Optional[str]
    confidence: str  # "exact" | "partial" | "loose" | "none"


def match_manufacturers(
    rows: Iterable[ExtractedRow],
    manufacturers: list,  # list of ManufacturerContact rows
) -> list[ManufacturerMatch]:
    """Pair each Excel name with the best matching manufacturer in our DB.

    Strategy (in order, first hit wins):
      1. Exact normalized match on manufacturer name OR parent_owner
      2. Either side is a substring of the other (normalized)
      3. All Excel words are present in the manufacturer's name+parent
    """
    # Pre-index for O(N) match
    by_norm: dict[str, Any] = {}
    indexed: list[tuple[str, set[str], Any]] = []
    for m in manufacturers:
        for source in (m.manufacturer, m.parent_owner):
            if source:
                key = _normalize(source)
                if key and key not in by_norm:
                    by_norm[key] = m
        toks_name = _tokenize(m.manufacturer)
        toks_parent = _tokenize(m.parent_owner) if m.parent_owner else set()
        indexed.append((_normalize(m.manufacturer), toks_name | toks_parent, m))

    out: list[ManufacturerMatch] = []
    for row in rows:
        nkey = _normalize(row.raw_name)
        rtoks = _tokenize(row.raw_name)
        if not nkey:
            out.append(ManufacturerMatch(row.row_index, row.raw_name, None, None, "none"))
            continue

        # 1) exact
        m = by_norm.get(nkey)
        if m:
            out.append(
                ManufacturerMatch(
                    row.row_index, row.raw_name, m.id, m.manufacturer, "exact"
                )
            )
            continue

        # 2) substring either way
        sub_hit = None
        for mnorm, mtoks, candidate in indexed:
            if not mnorm:
                continue
            if mnorm in nkey or nkey in mnorm:
                sub_hit = candidate
                break
        if sub_hit:
            out.append(
                ManufacturerMatch(
                    row.row_index, row.raw_name, sub_hit.id, sub_hit.manufacturer, "partial"
                )
            )
            continue

        # 3) token superset
        loose_hit = None
        for mnorm, mtoks, candidate in indexed:
            if rtoks and rtoks.issubset(mtoks):
                loose_hit = candidate
                break
        if loose_hit:
            out.append(
                ManufacturerMatch(
                    row.row_index, row.raw_name, loose_hit.id, loose_hit.manufacturer, "loose"
                )
            )
            continue

        out.append(ManufacturerMatch(row.row_index, row.raw_name, None, None, "none"))
    return out


# ─────────────────────────── Writeback ───────────────────────────


def write_response(
    xlsx_bytes: bytes,
    *,
    row_index: int,
    response_text: str,
    sheet_name: Optional[str] = None,
) -> bytes:
    """Open the workbook, locate (or create) the 'Manufacturer Response' column,
    write `response_text` into the cell at `row_index`, and return the updated
    bytes. Pure in-memory — caller decides where to upload."""
    wb = load_workbook(filename=io.BytesIO(xlsx_bytes), data_only=False)
    try:
        loc = _find_column(wb, RESPONSE_HEADER_TIERS)
        if loc is None:
            # Fall back to the same sheet as the Manufacturer column, add a
            # new "Manufacturer Response" column to the right.
            mcol = _find_column(wb, MANUFACTURER_HEADER_TIERS)
            if mcol is None:
                raise ValueError(
                    "Cannot find a 'Manufacturer Response' column and no anchor"
                    " 'Manufacturer' column to append next to."
                )
            ws = wb[sheet_name or mcol.sheet_name]
            new_col = (ws.max_column or mcol.col) + 1
            ws.cell(row=mcol.header_row, column=new_col, value="Manufacturer Response")
            ws.cell(row=row_index, column=new_col, value=response_text)
        else:
            ws = wb[sheet_name or loc.sheet_name]
            ws.cell(row=row_index, column=loc.col, value=response_text)
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()
    finally:
        wb.close()
