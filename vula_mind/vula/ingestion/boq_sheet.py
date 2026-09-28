"""
vula/ingestion/boq_sheet.py — read a Bill of Quantities spreadsheet row by row, no LLM.

2026-09-28, real digg-demo data: "HPC Cape Town Extra air.xlsx" (a R1.35M BOQ) was filed with
4 line items and "HPC_CapeTown_Interior_BOQ_1.xlsx" with none — the only reader was the LLM
document pass, which sees the first 6,000 characters of a sheet flattened to text and returns
a summary-sized line list. A BOQ sheet is already a table: find the header row (Description /
Qty / Unit / Rate / Amount) on each sheet, treat a row with a description and no figures as a
section heading, and every row with a rate or an amount is a line. Every figure comes straight
from its cell — nothing is inferred beyond rate = amount ÷ qty when the rate column is blank.

Money comes back as integer cents (the sheet holds Rands).
"""
from __future__ import annotations

import csv
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

SHEET_SUFFIXES = (".xlsx", ".xlsm", ".csv")

_HEADERS = {
    "desc": re.compile(r"^(item\s+)?(description|particulars|details|work(s)?|scope|item description|"
                       r"description of work(s)?|element)$"),
    "qty": re.compile(r"^(qty|qnty|quantity|quantities|qty\.)$"),
    "unit": re.compile(r"^(unit|units|uom)$"),
    "rate": re.compile(r"^(rate|unit rate|unit price|price|rate \(r\)|rate r|unit cost)$"),
    "amount": re.compile(r"^(amount|total|amount \(r\)|total \(r\)|amount r|line total|value|cost|"
                         r"sub ?total|budget|original budget)$"),
    "code": re.compile(r"^(item|item no|no|ref|code|nr|#)$"),
}
_SKIP = re.compile(r"^\s*(sub[\s-]*total|total|carried (forward|to)|brought forward|"
                   r"vat|grand total|summary)\b", re.IGNORECASE)


def _clean(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v if v is not None else "")).strip()


def _num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[R\s,]", "", str(v)).replace(" ", "")
    try:
        return float(s)
    except ValueError:
        return None


def _header_map(row: List[Any]) -> Optional[Dict[str, int]]:
    cols: Dict[str, int] = {}
    for i, cell in enumerate(row):
        label = _clean(cell).lower().rstrip(":")
        for key, pat in _HEADERS.items():
            if key not in cols and pat.match(label):
                cols[key] = i
                break
    if "desc" in cols and ("rate" in cols or "amount" in cols):
        return cols
    return None


def _cell(row: List[Any], cols: Dict[str, int], key: str) -> Any:
    i = cols.get(key)
    return row[i] if i is not None and i < len(row) else None


def parse_rows(rows: List[List[Any]], sheet: str = "") -> List[Dict[str, Any]]:
    """Lines from one sheet's rows (a list of cell-value lists)."""
    cols = None
    section = None
    out: List[Dict[str, Any]] = []
    for row in rows:
        row = list(row or [])
        if cols is None:
            cols = _header_map(row)
            continue
        again = _header_map(row)
        if again:                       # a repeated header (a new page/bill) — re-map
            cols = again
            continue
        desc = _clean(_cell(row, cols, "desc"))
        qty, rate, amount = (_num(_cell(row, cols, "qty")), _num(_cell(row, cols, "rate")),
                             _num(_cell(row, cols, "amount")))
        if not desc or not re.search(r"[A-Za-z]", desc):
            continue
        if _SKIP.match(desc):
            continue
        if qty is None and rate is None and amount is None:
            if len(desc) <= 90:
                section = desc          # a heading row: "Airconditioning", "BILL 3 — FINISHES"
            continue
        if rate is None and amount is not None and qty:
            rate = amount / qty
        if amount is None and rate is not None:
            amount = rate * (qty if qty is not None else 1)
        if not rate and not amount:
            continue
        unit = _clean(_cell(row, cols, "unit")) or None
        out.append({
            "description": desc[:300],
            "quantity": qty if qty is not None else 1,
            "unit": unit[:20] if unit else None,
            "unit_price_cents": int(round((rate or 0) * 100)) if rate else None,
            "total_cents": int(round(amount * 100)) if amount is not None else None,
            "section": section or (sheet or None),
            "code": _clean(_cell(row, cols, "code")) or None,
        })
    return out


def parse(path: Path) -> List[Dict[str, Any]]:
    """Every priced line of a BOQ workbook/CSV. [] when no sheet has a recognisable header."""
    path = Path(path)
    suffix = path.suffix.lower()
    try:
        if suffix == ".csv":
            with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
                return parse_rows(list(csv.reader(fh)), path.stem)
        if suffix in (".xlsx", ".xlsm"):
            from openpyxl import load_workbook
            wb = load_workbook(path, data_only=True, read_only=True)
            lines: List[Dict[str, Any]] = []
            for ws in wb.worksheets:
                lines.extend(parse_rows([list(r) for r in ws.iter_rows(values_only=True)], ws.title))
            wb.close()
            return lines
    except Exception as exc:
        log.warning("BOQ sheet parse failed for %s: %s", path.name, exc)
    return []


def section_budgets(lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """[{"section", "budget_cents"}] in sheet order — vula_project_boq.sections' shape."""
    order: List[str] = []
    sums: Dict[str, int] = {}
    for li in lines:
        s = li.get("section") or "General"
        if s not in sums:
            order.append(s)
            sums[s] = 0
        sums[s] += int(li.get("total_cents") or 0)
    return [{"section": s, "budget_cents": sums[s]} for s in order]


def apply_to_fields(fields: Dict[str, Any], lines: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Put the sheet's lines on an analysis's fields when the sheet read found more than the
    LLM did. A total the LLM read is kept (it came from the document); a missing one becomes
    the sum of the lines."""
    fields = dict(fields or {})
    if len(lines) <= len(fields.get("line_items") or []):
        return fields
    fields["line_items"] = lines
    fields["sections"] = section_budgets(lines)
    fields["line_items_source"] = "sheet"
    # A flag on the model's own line list no longer applies — the lines now come from cells.
    flagged = [u for u in fields.get("_unverified_figures") or []
               if not str(u.get("field", "")).startswith("line_items")]
    if flagged:
        fields["_unverified_figures"] = flagged
    else:
        fields.pop("_unverified_figures", None)
    if not fields.get("total_cents"):
        fields["total_cents"] = sum(int(li.get("total_cents") or 0) for li in lines)
    return fields
