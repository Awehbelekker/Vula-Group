"""vula/ingestion/supplier_statement.py — a supplier's statement of account, read line by line in code.

20 Nov 2026 (Ian, DIGG): Quicktint's statements came in on WhatsApp and were filed as "Account Statement" with
only the balance read — while none of the 32 invoices on them were in Vula, so the books said DIGG owed
Quicktint nothing and the statements said R36,832.07. A supplier statement is the accountant's creditors check;
this reads every line of the common "STATEMENT – Activity" layout (Xero — Date, Activity, Reference, Due
Date, Invoice Amount, Payments, Balance) from the PDF's own text. No model.

  parse(text) -> {"from", "to", "account_name", "supplier", "lines": [...], "balance_cents", "reconciled"} | None

Kept only when it adds up to the cent: opening balance + invoices − payments − credits gives every running
balance on the statement and the balance due. Anything else is None, and the old balance-only read stands.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

_MONEY = re.compile(r"^\(?-?\d{1,3}(?:,\d{3})*\.\d{2}\)?$")
_DATE = re.compile(r"^(\d{1,2} [A-Z][a-z]{2} \d{4})\b")
_ROW = re.compile(r"(Opening Balance|Payment on Invoice # ?\S+|Payment on Credit Note # ?\S+|Credit Note # ?\S+|"
                  r"Invoice # ?\S+|Overpayment|Prepayment|Payment)\b")
_DUE = re.compile(r"BALANCE DUE[^\d(]*(\(?[\d,]+\.\d{2}\)?)", re.I)


def looks_like(text: str) -> bool:
    t = text or ""
    return ("STATEMENT" in t.upper() and "Invoice Amount" in t and "Payments" in t and "Balance" in t
            and bool(_DUE.search(t)))


def _cents(s: str) -> int:
    s = s.strip()
    neg = s.startswith("(") or s.startswith("-")
    v = int(round(float(s.strip("()-").replace(",", "")) * 100))
    return -v if neg else v


def _iso(s: str) -> Optional[str]:
    try:
        return datetime.strptime(s.strip(), "%d %b %Y").date().isoformat()
    except ValueError:
        return None


def _after(lines: List[str], label: str) -> Optional[str]:
    for i, ln in enumerate(lines[:-1]):
        if ln.strip().lower() == label.lower():
            return lines[i + 1].strip()
    return None


def parse(text: str) -> Optional[Dict[str, Any]]:
    """Every line of an activity statement, or None when it isn't one or doesn't add up to the cent."""
    if not looks_like(text):
        return None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    rows: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    last_date: Optional[str] = None
    for ln in lines:
        if _DUE.search(ln):
            break
        m_date = _DATE.match(ln)
        m_row = _ROW.search(ln)
        if m_row:
            if cur:
                rows.append(cur)
            day = _iso(m_date.group(1)) if m_date else last_date
            act = m_row.group(1)
            ref = re.search(r"#\s?(\S+)", act)
            kind = ("opening" if act.startswith("Opening") else
                    "payment" if act.startswith(("Payment", "Overpayment", "Prepayment")) else
                    "credit" if act.startswith("Credit") else "invoice")
            cur = {"date": day, "kind": kind, "number": ref.group(1) if ref else None, "money": [], "text": act}
            last_date = None
            continue
        if m_date and not _MONEY.match(ln):
            if ln == m_date.group(1):
                last_date = _iso(m_date.group(1))    # a date on its own line, before its activity
            continue
        if cur is not None and _MONEY.match(ln):
            cur["money"].append(_cents(ln))
    if cur:
        rows.append(cur)
    if not rows or rows[0]["kind"] != "opening" or not rows[0]["money"]:
        return None
    opening = rows[0]["money"][-1]
    running, out = opening, []
    for r in rows[1:]:
        if len(r["money"]) < 2:
            return None
        amount, balance = r["money"][-2], r["money"][-1]
        running += amount if r["kind"] == "invoice" else -amount
        if running != balance:
            return None
        out.append({"date": r["date"], "kind": r["kind"], "number": r["number"], "amount_cents": amount,
                    "balance_cents": balance})
    due = _DUE.search(text)
    balance_due = _cents(due.group(1)) if due else None
    if balance_due is None or balance_due != running:
        return None
    sup = re.search(r"Name\.?\s*:\s*(.+)", text)
    return {"from": _iso(_after(lines, "From Date") or ""), "to": _iso(_after(lines, "To Date") or ""),
            "account_name": (lines[lines.index(next(x for x in lines if x.upper().startswith("STATEMENT"))) + 1]
                             if any(x.upper().startswith("STATEMENT") for x in lines) else None),
            "supplier": sup.group(1).strip() if sup else None,
            "opening_cents": opening, "lines": out, "balance_cents": balance_due, "reconciled": True}
