"""vula/ingestion/fnb_statement.py — deterministic parser for FNB account statements.

2 Oct 2026 (Ian: "let's reimport and compare"): DIGG's weekly FNB statements had been read by the
LLM path in bank_rec.extract_transactions. Against the real PDFs, 50 lines came out a factor
of ten too small (Nelitho R82,000 booked as R8,200, R10,000 as R1,000) and the per-line accrued
bank charge column was booked as extra R3/R8 debits. The running-balance check flagged it, but
the rows were saved anyway.

The statement is machine-generated with a rigid layout, so no LLM is needed. PyMuPDF text gives
each transaction as a date line, description line(s), the amount, then the running balance, and
sometimes the accrued charge:

    22 Sep Payshap Account Off-Us Hpc Paint
    30,000.00              ← amount (a credit carries "Cr")
    128,564.51Cr           ← running balance ("Cr" = in credit; no suffix = overdrawn)
    3.00                   ← accrued bank charge for this line (NOT a transaction)

parse() returns transactions only when every line's running balance reconciles from the
opening balance to the closing balance. Otherwise it returns None and the LLM path takes over
as before. Checked against 11 of DIGG's statements (22 Jun – 30 Sep 2026, 328 lines), all of
which reconciled to the cent.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

_MON = {m: i for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}
_NUM = re.compile(r"^[\d,]+\.\d{2}(Cr|Dr)?$")
_DATE = re.compile(r"^(\d{2}) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b ?(.*)$")
_PERIOD = re.compile(r"Statement Period\s*:\s*\d{1,2} (\w+) (\d{4}) to \d{1,2} (\w+) (\d{4})")
_BAL = r"\n([\d,]+\.\d{2}) ?(Cr|Dr)?"


def _cents(s: str) -> int:
    return int(round(float(s.replace(",", "").removesuffix("Cr").removesuffix("Dr")) * 100))


def _signed(s: str, suffix: Optional[str]) -> int:
    return _cents(s) * (1 if suffix == "Cr" else -1)


def is_fnb_statement(text: str) -> bool:
    return bool(text) and "fnb.co.za" in text and "Statement Period" in text \
        and "Transactions in RAND" in text


def pdf_text(pdf_path: Path, password: Optional[str] = None) -> str:
    import pymupdf
    doc = pymupdf.open(str(pdf_path))
    if doc.needs_pass and not doc.authenticate(password or ""):
        raise ValueError("statement is password-protected")
    return "\n".join(page.get_text() for page in doc)


def parse(text: str) -> Optional[Dict[str, Any]]:
    """{"opening_cents", "closing_cents", "period", "transactions": [...]}, or None when the
    text isn't an FNB statement or doesn't reconcile."""
    if not is_fnb_statement(text):
        return None
    period = _PERIOD.search(text)
    ob = re.search(r"Opening Balance" + _BAL, text)
    cb = re.search(r"Closing Balance" + _BAL, text)
    if not (period and ob and cb):
        return None
    start_mon, start_year, end_year = period.group(1)[:3], int(period.group(2)), int(period.group(4))
    opening, closing = _signed(ob.group(1), ob.group(2)), _signed(cb.group(1), cb.group(2))

    lines = [ln.strip() for ln in text.splitlines()]
    txns: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    in_table = False
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("Transactions in RAND"):
            in_table = False              # page header — the table restarts after "Charges"
        elif ln == "Charges" and not in_table:
            in_table = True
        elif ln.startswith("Closing Balance") and in_table:
            break
        elif in_table:
            m = _DATE.match(ln)
            if m and cur is None:
                mon = _MON[m.group(2)]
                # a statement spanning New Year: December lines belong to the start year
                year = start_year if (start_year == end_year or m.group(2) == start_mon
                                      or mon >= _MON.get(start_mon, 1)) else end_year
                cur = {"date": f"{year}-{mon:02d}-{int(m.group(1)):02d}",
                       "desc": [m.group(3)] if m.group(3) else [], "nums": []}
            elif cur is not None:
                if _NUM.match(ln):
                    cur["nums"].append(ln)
                    if len(cur["nums"]) == 2:
                        nxt = lines[i + 1] if i + 1 < len(lines) else ""
                        if _NUM.match(nxt) and not nxt.endswith(("Cr", "Dr")):
                            i += 1                # the accrued-charge column, not a transaction
                        amt, bal = cur["nums"]
                        txns.append({
                            "date": cur["date"],
                            "description": " ".join(d for d in cur["desc"] if d).strip()[:300],
                            "amount_cents": _cents(amt),
                            "direction": "in" if amt.endswith("Cr") else "out",
                            "balance_cents": _cents(bal) * (1 if bal.endswith("Cr") else -1),
                            "reference": None,
                        })
                        cur = None
                else:
                    cur["desc"].append(ln)
        i += 1

    # FNB's own charges print with a date and an amount but no description.
    # Two identical lines on one day (two R120 Dez Wood card swipes) are two transactions; the
    # books' key is (date, amount, description), so the repeat gets a "(2)" to stay distinct.
    seen: Dict[tuple, int] = {}
    for t in txns:
        if not t["description"]:
            t["description"] = "FNB bank charges"
        k = (t["date"], t["amount_cents"], t["direction"], t["description"])
        seen[k] = seen.get(k, 0) + 1
        if seen[k] > 1:
            t["description"] = f"{t['description']} ({seen[k]})"

    bal = opening
    for t in txns:
        bal += t["amount_cents"] if t["direction"] == "in" else -t["amount_cents"]
        if bal != t["balance_cents"]:
            return None
    if bal != closing:
        return None
    return {"opening_cents": opening, "closing_cents": closing,
            "period": (txns[0]["date"] if txns else None, txns[-1]["date"] if txns else None),
            "transactions": txns}
