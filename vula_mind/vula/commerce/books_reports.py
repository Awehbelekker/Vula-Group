"""vula/commerce/books_reports.py — the reports, read from the one book (Phase 1 PR4 of Xero-quality books).

18 Nov 2026 (Ian: "reports read the journal"). For a business that keeps the one trusted book
(`books_v2`, vula/commerce/ledger_v2.py) every report is built here from its journal, on one view:

  - accrual: a sale when it is invoiced, a cost when the bill is recorded;
  - cash: the same entries as the money moved.

A journal line tagged `basis` counts in that view only; untagged counts in both, so each view
balances on its own (migration 288). The trial balance, P&L, VAT, general ledger, balance sheet,
cash flow, tax-year figures and the VAT cross-check all come from these lines, so they agree with
each other by construction.

A business not on the one book keeps today's reports exactly as they were: every caller checks
`ledger_v2.is_on` and only then comes here. Reads only; money is integer cents, worked out in code.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Tuple

from vula.commerce import ledger_v2

log = logging.getLogger(__name__)

SOURCE = "journal"
CASH_CODES = ("bank_cash", "cash_on_hand")
_DEBIT_NORMAL = ("asset", "expense")
VIEW_LABEL = {"accrual": "accrual view", "cash": "cash view"}


def _client():
    from vula.commerce import service
    return service._client()


def basis_of(value: Optional[str]) -> str:
    """The view a report is asked for: accrual unless cash is named. Anything else is refused."""
    v = (value or "").strip().lower() or "accrual"
    if v not in ledger_v2.VIEWS:
        raise ValueError("basis is accrual or cash")
    return v


def settings(tenant_id: str) -> Dict[str, Any]:
    try:
        rows = (_client().table("commerce_books_settings").select("*").eq("tenant_id", tenant_id)
                .limit(1).execute().data or [])
        return rows[0] if rows else {}
    except Exception:  # noqa: BLE001
        return {}


def vat_basis(tenant_id: str) -> str:
    """The view VAT is worked out on: invoice basis → accrual, payments basis → cash."""
    return "cash" if (settings(tenant_id).get("vat_basis") or "invoice") == "payments" else "accrual"


def natural(acct: Dict[str, Any]) -> int:
    """Balance on the account's own side: debit for assets and expenses, credit for the rest."""
    dr, cr = int(acct.get("debit_cents") or 0), int(acct.get("credit_cents") or 0)
    return dr - cr if acct.get("type") in _DEBIT_NORMAL else cr - dr


def _mask(number: Any) -> Optional[str]:
    return f"…{str(number)[-4:]}" if number else None


def bank_labels(tenant_id: str) -> Dict[str, str]:
    """Each bank account as a person may see it: its bank or name and the last four digits."""
    try:
        rows = (_client().table("commerce_bank_accounts").select("id,account_number,bank,name")
                .eq("tenant_id", tenant_id).execute().data or [])
    except Exception:  # noqa: BLE001
        return {}
    return {str(r["id"]): " ".join(x for x in ((r.get("name") or r.get("bank") or "Bank"),
                                               _mask(r.get("account_number"))) if x) for r in rows}


# ── totals per account ───────────────────────────────────────────────────────

def trial_balance(tenant_id: str, since: Optional[str] = None, until: Optional[str] = None,
                  basis: str = "accrual") -> Dict[str, Any]:
    """Debits and credits per account for one view, in the shape the old trial balance had (so the
    statements and the screens read it unchanged), with whether the view balances."""
    basis = basis_of(basis)
    per: Dict[str, Dict[str, Any]] = {}
    for r in ledger_v2.balances(tenant_id, since=since, until=until, basis=basis):
        code = r.get("account_code") or "unknown"
        acc = per.setdefault(code, {"code": code, "name": r.get("account_name") or code,
                                    "type": r.get("account_type") or "", "debit_cents": 0, "credit_cents": 0})
        acc["debit_cents"] += r["debit_cents"]
        acc["credit_cents"] += r["credit_cents"]
    rows = sorted(per.values(), key=lambda a: a["code"])
    dr = sum(a["debit_cents"] for a in rows)
    cr = sum(a["credit_cents"] for a in rows)
    return {"since": since, "until": until, "basis": basis, "source": SOURCE, "accounts": rows,
            "total_debit_cents": dr, "total_credit_cents": cr, "balanced": dr == cr}


# ── the lines themselves ─────────────────────────────────────────────────────

def lines(tenant_id: str, since: Optional[str], until: Optional[str], basis: str,
          codes: Optional[Iterable[str]] = None) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """(entry, line) for every journal line in the view between two dates, oldest first, each line
    with its account's code, name and type. `codes` keeps only lines on those accounts."""
    from vula.commerce.ledger import _all_pages
    basis = basis_of(basis)
    db = _client()

    def heads_q():
        q = (db.table("journal_entries")
             .select("id,entry_date,description,source_type,source_id,version,reverses_entry_id,doc_id,bank_txn_id")
             .eq("tenant_id", tenant_id))
        if since:
            q = q.gte("entry_date", since)
        if until:
            q = q.lte("entry_date", until)
        return q.order("id")

    heads = {h["id"]: h for h in _all_pages(heads_q)}
    accounts = {a["id"]: a for a in (db.table("commerce_accounts").select("id,code,name,type")
                                     .eq("tenant_id", tenant_id).execute().data or [])}
    keep = set(codes) if codes else None
    out: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    ids = list(heads)
    for i in range(0, len(ids), 150):
        for ln in _all_pages(lambda c=ids[i:i + 150]: db.table("journal_lines")
                             .select("id,journal_entry_id,account_id,debit_cents,credit_cents,memo,basis,bank_account_id")
                             .in_("journal_entry_id", c).order("id")):
            if ln.get("basis") not in (None, basis):
                continue
            acct = accounts.get(ln["account_id"]) or {}
            if keep is not None and acct.get("code") not in keep:
                continue
            out.append((heads[ln["journal_entry_id"]],
                        {**ln, "code": acct.get("code") or "unknown", "name": acct.get("name") or acct.get("code") or "",
                         "type": acct.get("type") or "", "debit_cents": int(ln.get("debit_cents") or 0),
                         "credit_cents": int(ln.get("credit_cents") or 0)}))
    out.sort(key=lambda el: (str(el[0].get("entry_date")), str(el[0]["id"]), str(el[1]["id"])))
    return out


def general_ledger(tenant_id: str, since: Optional[str] = None, until: Optional[str] = None,
                   basis: str = "accrual") -> List[Dict[str, Any]]:
    """One row per journal line in the view: what an accountant ticks against. A corrected entry and
    its reversal both appear (the journal is never edited), and they cancel."""
    basis = basis_of(basis)
    banks = bank_labels(tenant_id)
    rows = []
    for head, ln in lines(tenant_id, since, until, basis):
        rows.append({"date": str(head.get("entry_date"))[:10], "entry_id": head["id"],
                     "version": int(head.get("version") or 1), "description": head.get("description") or "",
                     "account_code": ln["code"], "account": ln["name"], "account_type": ln["type"],
                     "debit_cents": ln["debit_cents"], "credit_cents": ln["credit_cents"],
                     "source_type": head.get("source_type"), "source_id": head.get("source_id"),
                     "doc_id": head.get("doc_id"), "bank_txn_id": head.get("bank_txn_id"),
                     "reverses_entry_id": head.get("reverses_entry_id"),
                     "bank_account": banks.get(str(ln.get("bank_account_id"))) if ln.get("bank_account_id") else None,
                     "view": ln.get("basis") or "both", "memo": ln.get("memo")})
    return rows


# ── VAT ──────────────────────────────────────────────────────────────────────

def vat(tenant_id: str, since: Optional[str] = None, until: Optional[str] = None,
        basis: Optional[str] = None) -> Dict[str, Any]:
    """VAT collected (output) and claimable (input) from the VAT accounts, per month and in total,
    on the business's VAT basis unless another view is asked for."""
    from vula.commerce.accounting import is_vat_registered
    view = basis_of(basis) if basis else vat_basis(tenant_id)
    base = {"since": since, "until": until, "basis": view, "source": SOURCE}
    if not is_vat_registered(tenant_id):
        return {"vat_registered": False, **base}
    months: Dict[str, Dict[str, int]] = {}
    for head, ln in lines(tenant_id, since, until, view, codes=("vat_output", "vat_input")):
        m = months.setdefault(str(head.get("entry_date"))[:7], {"output": 0, "input": 0})
        if ln["code"] == "vat_output":
            m["output"] += ln["credit_cents"] - ln["debit_cents"]
        else:
            m["input"] += ln["debit_cents"] - ln["credit_cents"]
    by_month = [{"month": k, "output_cents": v["output"], "input_cents": v["input"],
                 "net_cents": v["output"] - v["input"]} for k, v in sorted(months.items())]
    out = sum(m["output_cents"] for m in by_month)
    inp = sum(m["input_cents"] for m in by_month)
    label = "invoice basis" if view == "accrual" else "payments basis"
    return {"vat_registered": True, **base, "output_vat_cents": out, "input_vat_cents": inp,
            "net_vat_cents": out - inp, "vat_payable_cents": out - inp, "by_month": by_month,
            "note": f"Net VAT payable to SARS (positive) or refundable (negative), from your books ({label})."}


# ── cash ─────────────────────────────────────────────────────────────────────

def cash_by_counter(tenant_id: str, since: str, until: str, basis: str = "accrual") -> List[Dict[str, Any]]:
    """What moved the cash in the period, by the account on the other side of each entry (sales,
    payments cleared, wages, VAT, bank charges…). Each view balances, so in an entry that moves cash
    the other lines add up to exactly that movement: the rows add up to the cash movement to the cent."""
    by_entry: Dict[str, List[Dict[str, Any]]] = {}
    for head, ln in lines(tenant_id, since, until, basis):
        by_entry.setdefault(head["id"], []).append(ln)
    sums: Dict[Tuple[str, str], int] = {}
    for lns in by_entry.values():
        if not any(ln["code"] in CASH_CODES for ln in lns):
            continue
        for ln in lns:
            if ln["code"] in CASH_CODES:
                continue
            k = (ln["code"], ln["name"])
            sums[k] = sums.get(k, 0) + ln["credit_cents"] - ln["debit_cents"]
    return [{"source": name or code, "code": code, "net_cents": v}
            for (code, name), v in sorted(sums.items(), key=lambda kv: -abs(kv[1])) if v]


def cash_by_account(tenant_id: str, since: str, until: str, basis: str = "accrual") -> List[Dict[str, Any]]:
    """Opening, in, out and closing per bank account (and cash on hand) for the period."""
    from vula.commerce.reports import _day_before
    banks = bank_labels(tenant_id)

    def per(rows):
        out: Dict[Tuple[str, Optional[str]], Dict[str, int]] = {}
        for r in rows:
            if r.get("account_code") not in CASH_CODES:
                continue
            k = (r["account_code"], str(r["bank_account_id"]) if r.get("bank_account_id") else None)
            t = out.setdefault(k, {"dr": 0, "cr": 0})
            t["dr"] += r["debit_cents"]
            t["cr"] += r["credit_cents"]
        return out

    before = per(ledger_v2.balances(tenant_id, until=_day_before(since), basis=basis_of(basis)))
    during = per(ledger_v2.balances(tenant_id, since=since, until=until, basis=basis_of(basis)))
    rows = []
    for k in sorted(set(before) | set(during), key=lambda k: (k[0], k[1] or "")):
        code, acct = k
        o = before.get(k, {"dr": 0, "cr": 0})
        d = during.get(k, {"dr": 0, "cr": 0})
        opening = o["dr"] - o["cr"]
        label = ("Cash on hand" if code == "cash_on_hand"
                 else banks.get(acct) if acct else "Bank (no account number)")
        rows.append({"account": label or "Bank", "code": code, "opening_cents": opening, "in_cents": d["dr"],
                     "out_cents": d["cr"], "closing_cents": opening + d["dr"] - d["cr"]})
    return rows


# ── the tax year ─────────────────────────────────────────────────────────────

def _edge_date(tenant_id: str, start: str, end: str, last: bool) -> Optional[date]:
    try:
        rows = (_client().table("journal_entries").select("entry_date").eq("tenant_id", tenant_id)
                .gte("entry_date", start).lte("entry_date", end).order("entry_date", desc=last)
                .limit(1).execute().data or [])
        return date.fromisoformat(str(rows[0]["entry_date"])[:10]) if rows else None
    except Exception:  # noqa: BLE001
        return None


def year_figures(tenant_id: str, start: date, end: date) -> Dict[str, Any]:
    """tax.year_figures from the book (accrual view): income, deductible expenses, what's still to
    review (the suspense account), and the dates the book covers."""
    from vula.commerce.accounting import ensure_chart
    try:
        deductible = {a["code"]: bool(a.get("deductible", True)) for a in ensure_chart(tenant_id)}
    except Exception:  # noqa: BLE001
        deductible = {}
    tb = trial_balance(tenant_id, start.isoformat(), end.isoformat(), "accrual")
    income = sum(natural(a) for a in tb["accounts"] if a["type"] == "income")
    expense = sum(natural(a) for a in tb["accounts"] if a["type"] == "expense" and deductible.get(a["code"], True))
    sus = next((a for a in tb["accounts"] if a["code"] == "suspense"), {"debit_cents": 0, "credit_cents": 0})
    first = _edge_date(tenant_id, start.isoformat(), end.isoformat(), last=False)
    accrual_from = settings(tenant_id).get("accrual_from")
    if accrual_from:
        try:
            af = date.fromisoformat(str(accrual_from)[:10])
            if start <= af <= end:
                first = min(first, af) if first else af
        except ValueError:
            pass
    last = _edge_date(tenant_id, start.isoformat(), end.isoformat(), last=True)
    months = round(((last - first).days + 1) / 30.44, 1) if first and last else 0.0
    return {"income_cents": income, "expense_cents": expense, "profit_cents": income - expense,
            "uncategorised_in_cents": int(sus["credit_cents"]), "uncategorised_out_cents": int(sus["debit_cents"]),
            "first": first, "last": last, "months": max(months, 0.0), "source": SOURCE}


def sales_by_month(tenant_id: str, since: Optional[str], until: Optional[str],
                   basis: str = "accrual") -> Dict[str, int]:
    """Sales (the `sales` account, net of credit notes) per month."""
    out: Dict[str, int] = {}
    for head, ln in lines(tenant_id, since, until, basis, codes=("sales",)):
        m = str(head.get("entry_date"))[:7]
        out[m] = out.get(m, 0) + ln["credit_cents"] - ln["debit_cents"]
    return out


# ── creditors ────────────────────────────────────────────────────────────────

def control(tenant_id: str, code: str, as_at: Optional[str] = None) -> int:
    """A control account's balance on the accrual view (debtors: accounts_receivable, creditors:
    accounts_payable), on its own side."""
    tb = trial_balance(tenant_id, None, as_at, "accrual")
    acct = next((a for a in tb["accounts"] if a["code"] == code), None)
    return natural(acct) if acct else 0
