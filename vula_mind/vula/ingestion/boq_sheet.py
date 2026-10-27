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

2026-10-26, DIGG's Belladonna project bill: an "Item | Unit | Qty | Trade | Budget | Actual |
Invoiced | Outstanding" sheet was never found (a bare "Item" only counted as the code column), so
the LLM's 5-line read stood. "Item" is the description when there is no description column, a
Room / Area column is the line's section, and a cost-tracking sheet's Trade / Actual / Invoiced /
Outstanding cells are kept on each line.

Later that day, Judy's own sheets (read from DIGG's Drive): room and trade headings sit in column
A, not under "Item", so a single-cell row anywhere is a heading; a heading beats the Area column
("Whole flat" on every line); "Site Management subtotal" rows are never lines; and "Paid to
Date", "Trade / Responsible" and "Room / Area" columns are read.

And the QS's priced bill for HPC ("ITEM NO | | | | QUANTITY | RATE | AMOUNT", JBCC layout): the
description and unit columns carry no heading, so they are found from the rows below the header
(the longest text, the short "m²"/"No"/"Item" cells); "R-" is zero, so a 0% "Profit on the above
item" never becomes a copy of its provisional sum; and the SUMMARY page that repeats every bill's
total ends the bill, so nothing is counted twice.

Its payment certificates (PC No. 10, 6 Oct) put several AMOUNT columns under group headings in
the row above — CONTRACT BOQ, CUMULATIVE AMOUNT CERTIFIED TO DATE, ANTICIPATED FINAL ACCOUNT. The
contract column stays the amount; the certified and anticipated ones are kept on each line as
`certified_cents` / `anticipated_cents`, and a "BILL NO 1 - PRELIMINARIES" row carrying the bill's
totals is that bill's heading, never a line.

Read in full (26 Oct), that certificate also showed what a valuation adds to a bill: rows coded
"NEW" are variations (new rates, extra work) and uncoded rows are notes, quote breakdowns and the
contingency / fee / VAT / retention recap. In a bill whose items carry codes, only numbered items
are the contract (they add up to each bill's stated total); "NEW" rows are kept as variations
(`variation_cents`), as is an uncoded row with nothing in the contract but money certified
("Sealant"); other uncoded rows are dropped. A "BOQ Summary" tab restates the bill and is skipped
once the bill is read. Its area tabs (Bio, Physio, Common, Café) repeat the Complete tab's items,
so a tab whose items are already read is skipped, and a TRUE/FALSE check cell or an item code
alone on a row is never a heading. Read this way, all 12 bills of PC No. 10 match the
certificate's own bill totals (contract, certified, anticipated) to the cent.
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
    # A cost-tracking sheet's columns, kept on the line when present (never the amount).
    "room": re.compile(r"^(room|area|section|zone|location|space|room ?/ ?area|area ?/ ?room)$"),
    "trade": re.compile(r"^(trade|trades|sub-?contractor|contractor|supplier|responsible|"
                        r"trade ?/ ?responsible|trade ?/ ?supplier)$"),
    "actual": re.compile(r"^(actual|actuals|actual cost|actual spend|actual \(r\)|spent|cost to date)$"),
    "invoiced": re.compile(r"^(invoiced|invoiced \(r\)|invoiced to date|billed|claimed)$"),
    "outstanding": re.compile(r"^(outstanding|outstanding \(r\)|balance|balance due|to invoice|"
                              r"outstanding to finish|still to pay)$"),
    "paid": re.compile(r"^(paid|paid to date|amount paid|paid \(r\))$"),
}
# Kept as `<key>_cents` on each line when the sheet has the column.
_TRACKED = ("actual", "invoiced", "outstanding", "paid", "certified", "anticipated")
# A valuation's group headings, in the row above the column headings: the AMOUNT column under each.
_GROUPS = {"certified": re.compile(r"cum[mu]*lative.*certifi|certified to date", re.IGNORECASE),
           "anticipated": re.compile(r"anticipated final", re.IGNORECASE)}
_SKIP = re.compile(r"^\s*(sub[\s-]*total|total|carried (forward|to)|brought forward|"
                   r"vat|grand total|summary)\b", re.IGNORECASE)
_SUBTOTAL = re.compile(r"\bsub[\s-]*totals?\b|\btotals?\s*$", re.IGNORECASE)


def _clean(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v if v is not None else "")).strip()


# "BILL NO 2" on its own row: the next heading is that bill's title, and the headings inside it
# ("Brickwork", "LOCKS") are sub-headings, not new sections.
_BILL_NO = re.compile(r"^\s*bill\s*(no\.?|nr\.?|number)?\s*\d+\s*$", re.IGNORECASE)
_BILL_TITLED = re.compile(r"^\s*bill\s*(no\.?|nr\.?|number)?\s*\d+\s*[-–—:]\s*(?P<title>.+)$",
                          re.IGNORECASE)
_DASH = re.compile(r"^\s*R?\s*[-–—]\s*$")      # "R-", " - ": an accountant's zero


def _num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if _DASH.match(str(v)):
        return 0.0
    s = re.sub(r"[R\s,]", "", str(v)).replace(" ", "")
    try:
        return float(s)
    except ValueError:
        return None


# A heading's unit is not part of its name: "Budget (R)", "Actual (ZAR)", "Spent to date R".
_UNIT_SUFFIX = re.compile(r"\s*(?:\((?:r|zar|rand|incl\.? vat|excl\.? vat)\)|\br\b)\s*$", re.IGNORECASE)
# Wordings DIGG-style cost statements use for the same columns (2026-10-26).
_ALIASES = {"budget allowance": "budget", "allowance": "budget", "budgeted": "budget",
            "spent to date": "actual", "actual to date": "actual", "actual spend to date": "actual",
            "work item": "description", "item / description": "description",
            "item/description": "description", "description of item": "description"}


def _infer_columns(cols: Dict[str, int], below: List[List[Any]]) -> None:
    """A QS bill heads only ITEM NO / QUANTITY / RATE / AMOUNT: the description is the unheaded
    column with the most text below, the unit the unheaded one of short labels ("m²", "No",
    "Item") nearest before the quantity."""
    taken = set(cols.values())
    text: Dict[int, int] = {}
    short: Dict[int, int] = {}
    for r in below:
        for i, c in enumerate(r or []):
            v = _clean(c)
            if i in taken or not v or _num(v) is not None or not re.search(r"[A-Za-z]", v):
                continue
            text[i] = text.get(i, 0) + len(v)
            if len(v) <= 6:
                short[i] = short.get(i, 0) + 1
    if "desc" not in cols and "code" in cols and text:
        cols["desc"] = max(text, key=text.get)
    q = cols.get("qty")
    if "unit" not in cols and q is not None:
        cands = [i for i in short if i < q and i not in cols.values() and short[i] >= 2]
        if cands:
            cols["unit"] = max(cands)


def _header_map(row: List[Any], below: Optional[List[List[Any]]] = None) -> Optional[Dict[str, int]]:
    cols: Dict[str, int] = {}
    for i, cell in enumerate(row):
        label = _UNIT_SUFFIX.sub("", _clean(cell).lower().rstrip(":")).strip()
        label = _ALIASES.get(label, label)
        for key, pat in _HEADERS.items():
            if key not in cols and pat.match(label):
                cols[key] = i
                break
    if "desc" not in cols and "code" in cols and _clean(row[cols["code"]]).lower() == "item":
        cols["desc"] = cols.pop("code")     # "Item" heads the description column
    if (below and sum(k in cols for k in ("qty", "rate", "amount")) >= 2
            and (("desc" not in cols and "code" in cols) or "unit" not in cols)):
        _infer_columns(cols, below)
    if "desc" in cols and ("rate" in cols or "amount" in cols):
        return cols
    return None


def _cell(row: List[Any], cols: Dict[str, int], key: str) -> Any:
    i = cols.get(key)
    return row[i] if i is not None and i < len(row) else None


def _group_columns(cols: Dict[str, int], above: List[Any], header: List[Any]) -> None:
    """Map each valuation group heading in `above` to the first AMOUNT column at or after it."""
    amounts = [i for i, c in enumerate(header) if re.match(r"^amount\b", _clean(c).lower())]
    for i, c in enumerate(above or []):
        for key, pat in _GROUPS.items():
            if key not in cols and pat.search(_clean(c)):
                after = [a for a in amounts if a >= i]
                if after:
                    cols[key] = after[0]


def parse_rows(rows: List[List[Any]], sheet: str = "") -> List[Dict[str, Any]]:
    """Lines from one sheet's rows (a list of cell-value lists)."""
    cols = None
    section = None
    summary = False
    bills = False          # the sheet is numbered bills ("BILL NO 1")
    title_next = False     # the next heading names the bill
    out: List[Dict[str, Any]] = []
    for n, row in enumerate(rows):
        row = list(row or [])
        if cols is None:
            cols = _header_map(row, rows[n + 1:n + 60])
            for above in rows[max(0, n - 3):n] if cols else []:   # a blank row may sit between
                _group_columns(cols, list(above or []), row)
            continue
        again = _header_map(row, rows[n + 1:n + 60])
        if again:                       # a repeated header (a new page/bill) — re-map
            for above in rows[max(0, n - 3):n]:
                _group_columns(again, list(above or []), row)
            cols, summary = again, False
            continue
        if summary:                     # the summary page repeats each bill's total
            continue
        desc = _clean(_cell(row, cols, "desc"))
        qty, rate, amount = (_num(_cell(row, cols, "qty")), _num(_cell(row, cols, "rate")),
                             _num(_cell(row, cols, "amount")))
        if not desc:
            # A heading in another column ("Site Management" in column A, the description in B):
            # a row with one text cell and nothing else.
            filled = [(i, _clean(c)) for i, c in enumerate(row)
                      if _clean(c) and not isinstance(c, bool)]
            if (len(filled) == 1 and filled[0][0] != cols.get("code")
                    and re.search(r"[A-Za-z]", filled[0][1]) and len(filled[0][1]) <= 90
                    and _num(filled[0][1]) is None and not _SKIP.match(filled[0][1])):
                section = filled[0][1]
            continue
        if not re.search(r"[A-Za-z]", desc):
            continue
        if re.match(r"^\s*(bills? )?summary\b", desc, re.IGNORECASE):
            summary = True
            continue
        if _SKIP.match(desc) or _SUBTOTAL.search(desc):
            continue
        titled = _BILL_TITLED.match(desc)
        if titled:                      # "BILL NO 2 - MASONRY" (with the bill's totals): a heading
            bills, title_next, section = True, False, titled.group("title").strip()[:90]
            continue
        tracked = {k: _num(_cell(row, cols, k)) for k in _TRACKED if k in cols}
        unit = _clean(_cell(row, cols, "unit")) or None
        trade = _clean(_cell(row, cols, "trade")) or None
        room = _clean(_cell(row, cols, "room")) or None
        spent = any(v for v in tracked.values())
        if qty is None and rate is None and amount is None and not spent:
            if unit or trade:
                continue                # a scoped line nobody priced yet — not a heading
            if _BILL_NO.match(desc):
                bills, title_next = True, True
            elif len(desc) <= 90 and (title_next or not bills):
                section = desc          # a heading row: "Airconditioning", "BILL 3 — FINISHES"
                title_next = False
            continue
        if amount == 0 and not qty and not spent:
            continue                    # "Profit on the above item, 0%, R-": nothing priced
        if rate is None and amount is not None and qty:
            rate = amount / qty
        if amount is None and rate is not None:
            amount = rate * (qty if qty is not None else 1)
        if not rate and not amount and not spent:
            continue
        line = {
            "description": desc[:300],
            "quantity": qty if qty is not None else 1,
            "unit": unit[:20] if unit else None,
            "unit_price_cents": int(round((rate or 0) * 100)) if rate else None,
            "total_cents": int(round(amount * 100)) if amount is not None else None,
            # A heading row names the part of the job; an Area column ("Whole flat") is detail.
            "section": section or room or (sheet or None),
            "code": _clean(_cell(row, cols, "code")) or None,
        }
        if "trade" in cols:
            line["trade"] = trade[:80] if trade else None
        if room and section and room != section:
            line["area"] = room[:80]
        for k, v in tracked.items():
            line[f"{k}_cents"] = int(round(v * 100)) if v is not None else None
        out.append(line)
    out = _costs_positive(out)
    return _contract_items(out) if "code" in (cols or {}) else out


def _costs_positive(lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """A spend log that enters every cost as a negative (money out) reads as costs, not credits.
    2026-10-27: Judy's Porterfield "Bouwerk" sheets (Bathroom Bizarre -7,480.10, Builders
    Warehouse -6,971.00 …) read as a budget of -R311,441.02 with every section negative. Only
    when nearly every amount is negative — a real BOQ's credit or discount line stays negative."""
    amounts = [li["total_cents"] for li in lines if li.get("total_cents")]
    if len(amounts) < 5 or sum(1 for a in amounts if a < 0) * 10 < len(amounts) * 8:
        return lines
    for li in lines:
        for k in ("total_cents", "unit_price_cents", *(f"{t}_cents" for t in _TRACKED)):
            if li.get(k):
                li[k] = -li[k]
    return lines


_ITEM_NO = re.compile(r"^\d+(\.\d+)*$")


def _contract_items(lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """In a bill whose items carry codes, the numbered items are the contract; a "NEW" row is a
    variation, kept with its amount as `variation_cents`; an uncoded row is a note or a recap."""
    numbered = [li for li in lines if _ITEM_NO.match(li.get("code") or "")]
    if not numbered or len(numbered) * 3 < len(lines):
        return lines                   # codes are occasional here, not the bill's structure
    out = []
    for li in lines:
        code = li.get("code") or ""
        if _ITEM_NO.match(code):
            out.append(li)
        elif code:
            out.append({**li, "variation": True, "variation_cents": li.get("total_cents"),
                        "total_cents": None})
        elif not li.get("total_cents") and (li.get("certified_cents") or li.get("anticipated_cents")):
            # Uncoded extra work with nothing in the contract but money certified ("Sealant").
            out.append({**li, "variation": True, "total_cents": None,
                        "variation_cents": li.get("anticipated_cents") or li.get("certified_cents")})
    return out


def _seen(lines: List[Dict[str, Any]], already: set) -> bool:
    """Most of a sheet's items were read on an earlier sheet (an area tab of a Complete bill)."""
    keys = {(li.get("description") or "").lower() for li in lines}
    return bool(keys) and len(keys & already) * 10 >= len(keys) * 7


def _log_unrecognised(filename: str, sheet: str, rows: List[List[Any]]) -> None:
    """Say which headings a sheet had when no header row was recognised, so the reason is in the
    log instead of a silent "0 lines" (2026-10-26, DIGG's Belladonna project bill). Only short
    text cells from the first rows — headings, not figures or descriptions."""
    labels = []
    for row in rows[:15]:
        for cell in row or []:
            v = _clean(cell)
            if v and len(v) <= 30 and re.search(r"[A-Za-z]", v) and v not in labels:
                labels.append(v)
    log.info("BOQ sheet %s / %s: no header row recognised; first labels: %s",
             filename, sheet, "; ".join(labels[:20]) or "(none)")


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
            already: set = set()
            for ws in wb.worksheets:
                rows = [list(r) for r in ws.iter_rows(values_only=True)]
                got = parse_rows(rows, ws.title)
                if not got:
                    _log_unrecognised(path.name, ws.title, rows)
                elif already and re.search(r"\b(summary|cost split)\b", ws.title, re.IGNORECASE):
                    log.info("BOQ sheet %s / %s: a summary of the bill; skipped", path.name, ws.title)
                    continue
                elif _seen(got, already):
                    log.info("BOQ sheet %s / %s: repeats items already read; skipped",
                             path.name, ws.title)
                    continue
                already |= {(li.get("description") or "").lower() for li in got}
                lines.extend(got)
            wb.close()
            return lines
    except Exception as exc:
        log.warning("BOQ sheet parse failed for %s: %s", path.name, exc)
    return []


def section_budgets(lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """[{"section", "budget_cents"}] in sheet order — vula_project_boq.sections' shape. A
    cost-tracking sheet's sections also carry `actual_cents` / `invoiced_cents` / `paid_cents` sums."""
    order: List[str] = []
    sums: Dict[str, Dict[str, int]] = {}
    extra = [k for k in ("actual_cents", "invoiced_cents", "paid_cents", "certified_cents",
                         "anticipated_cents", "variation_cents") if any(k in li for li in lines)]
    for li in lines:
        s = li.get("section") or "General"
        if s not in sums:
            order.append(s)
            sums[s] = {"budget_cents": 0, **{k: 0 for k in extra}}
        sums[s]["budget_cents"] += int(li.get("total_cents") or 0)
        for k in extra:
            sums[s][k] += int(li.get(k) or 0)
    return [{"section": s, **sums[s]} for s in order]


def apply_to_fields(fields: Dict[str, Any], lines: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Put the sheet's lines on an analysis's fields when the sheet read found more than the
    LLM did. A total the LLM read is kept (a QS bill's selling price with fee and VAT is more
    than its lines); a missing one becomes the sum of the line budgets.

    2026-10-26, DIGG: the LLM read of Judy's Belladonna bill took the Actual column's R569,162 as
    the total and flagged it as excluding VAT, so the project's contract value became R654,535.75
    (DIGG isn't VAT registered; the budget is R461,195). A read total that is just another
    column's sum (actual, paid, invoiced, …) is that column, not the bill's total: the budget
    sum replaces it, and nothing is added for VAT the sheet doesn't show."""
    fields = dict(fields or {})
    # A newer read of the cells replaces an older one (the reader improves; 26 Oct, HPC's PC No. 10
    # was first read as 482 doubled lines, correctly 152). An LLM's list is replaced only by more.
    if not lines or (fields.get("line_items_source") != "sheet"
                     and len(lines) <= len(fields.get("line_items") or [])):
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
    budget = sum(int(li.get("total_cents") or 0) for li in lines)
    read = int(fields.get("total_cents") or 0)
    other_columns = {sum(int(li.get(f"{k}_cents") or 0) for li in lines) for k in _TRACKED
                     if any(li.get(f"{k}_cents") is not None for li in lines)}
    if budget > 0 and (not read or any(abs(read - c) <= 100 for c in other_columns if c)):
        fields["total_cents"] = budget
        fields["amounts_exclude_vat"] = False
        fields.pop("vat_cents", None)
        fields.pop("subtotal_cents", None)
    return fields
