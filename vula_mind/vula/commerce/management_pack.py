"""vula/commerce/management_pack.py — the month's management accounts (23 Nov 2026; Ian: "Management
accounts next", DIGG only started trading this year).

How the business did in a month, as an owner reads it:

  Profit and loss   the month, last month and the change, the same month last year (only when the business
                    was trading then), and the year to date (from the start of the financial year, or the
                    first day it traded, whichever is later)
  Cash              per bank account: opening, money in, money out, closing at the month end
  Owed              what customers owed the business and what it owed its suppliers at the month end
                    (finances.aged_as_at), the five largest each way
  VAT               the month, for a VAT-registered business
  Key figures       gross and net margin, debtor days, cash runway — each only when its base isn't nothing
  Notes             short sentences built in code from the figures above; never a model

One source per business, never mixed (as the year-end pack): the one book's journal (accrual view) for a
business on it, its bank lines otherwise. `figures` gives one dict; `workbook` (every amount read back
from the saved file), `pdf` and `summary_text` are built from that dict only. Account numbers appear by
their last 4 digits. Reads only; integer cents; percentages in Decimal.
"""
from __future__ import annotations

import html
import io
import logging
import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional, Tuple

from openpyxl import Workbook

from vula.commerce.xlsx_check import mismatches

log = logging.getLogger(__name__)

MONTH_NAMES = ("January February March April May June July August September October November December").split()
DIRECT_COSTS = ("cost_of_sales", "casual_labour", "subcontractors")
_MONTH = re.compile(r"^(\d{4})-(\d{2})$")


def _client():
    from vula.commerce import service
    return service._client()


def _today() -> date:
    return date.today()


def _r(cents: Optional[int]) -> str:
    if cents is None:
        return "—"
    sign = "−" if cents < 0 else ""
    return f"{sign}R{abs(int(cents)) / 100:,.2f}"


def pct(part: int, whole: int) -> Optional[float]:
    """part as a percentage of whole, to one decimal place; None when there's nothing to divide by."""
    if not whole:
        return None
    return float((Decimal(int(part)) * 100 / Decimal(int(whole))).quantize(Decimal("0.1"), ROUND_HALF_UP))


def _month_bounds(y: int, m: int) -> Tuple[date, date]:
    first = date(y, m, 1)
    nxt = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
    return first, nxt - timedelta(days=1)


def _shift(y: int, m: int, months: int) -> Tuple[int, int]:
    n = y * 12 + (m - 1) + months
    return n // 12, n % 12 + 1


def _day(iso: Optional[str]) -> str:
    """2026-10-31 → 31 Oct 2026."""
    try:
        d = date.fromisoformat(str(iso)[:10])
    except (TypeError, ValueError):
        return str(iso or "")
    return f"{d.day} {MONTH_NAMES[d.month - 1][:3]} {d.year}"


def _label(d: date) -> str:
    return f"{MONTH_NAMES[d.month - 1]} {d.year}"


def first_trading_day(tenant_id: str, one_book: bool) -> Optional[date]:
    """The first day the business has money in its books: its first live bank line (or, on the one book,
    its first journal entry). None for a business with nothing yet."""
    db = _client()
    try:
        if one_book:
            rows = (db.table("journal_entries").select("entry_date").eq("tenant_id", tenant_id)
                    .order("entry_date").limit(1).execute().data or [])
            return date.fromisoformat(str(rows[0]["entry_date"])[:10]) if rows else None
        rows = (db.table("commerce_bank_transactions").select("txn_date,match_status,source_file,amount_cents")
                .eq("tenant_id", tenant_id).order("txn_date").limit(200).execute().data or [])
        from vula.commerce import ledger_events as ev
        for r in rows:
            if ev.is_live(r) and r.get("txn_date"):
                return date.fromisoformat(str(r["txn_date"])[:10])
    except Exception as exc:  # noqa: BLE001
        log.warning("management pack: first trading day not read for %s: %s", tenant_id, exc)
    return None


def period(tenant_id: str, month: Optional[str] = None, today: Optional[date] = None,
           one_book: Optional[bool] = None) -> Dict[str, Any]:
    """The month asked for (YYYY-MM; the last full month by default) and the periods it's compared with.
    ValueError for a month that's badly written, still to come, or before the business traded."""
    from vula.commerce import ledger_v2, statement_files
    from vula.commerce.year_end_pack import _year_end_month
    today = today or _today()
    if one_book is None:
        one_book = ledger_v2.is_on(tenant_id)
    if month:
        mm = _MONTH.match(month.strip())
        if not mm or not 1 <= int(mm.group(2)) <= 12:
            raise ValueError("the month is written YYYY-MM, e.g. 2026-10")
        y, m = int(mm.group(1)), int(mm.group(2))
    else:
        y, m = _shift(today.year, today.month, -1)
    start, end = _month_bounds(y, m)
    if start > today:
        raise ValueError("that month hasn't started yet")
    first = first_trading_day(tenant_id, one_book)
    if first is None:
        raise ValueError("there's nothing in the books yet — upload a bank statement first")
    if end < first:
        raise ValueError(f"the business's books start in {_label(first)}")
    until = min(end, today)

    def p(a: date, b: date, label: str) -> Dict[str, Any]:
        return {"from": a.isoformat(), "to": b.isoformat(), "label": label}

    py, pm = _shift(y, m, -1)
    ps, pe = _month_bounds(py, pm)
    ls, le = _month_bounds(y - 1, m)
    fy = statement_files.year_start(end, _year_end_month(tenant_id))
    ytd_from = max(fy, first)
    return {"month": f"{y:04d}-{m:02d}", "this": p(start, until, _label(start)),
            "ended": end < today, "first_trading_day": first.isoformat(),
            "first_month": f"{first.year:04d}-{first.month:02d}",
            "previous": p(ps, pe, _label(ps)) if pe >= first else None,
            "last_year": p(ls, le, _label(ls)) if le >= first else None,
            "ytd": p(ytd_from, until, f"{ytd_from.strftime('%-d %b %Y')} to {until.strftime('%-d %b %Y')}"),
            "year_start": fy.isoformat(), "first_year": first >= fy,
            "one_book": one_book}


# ── the figures ──────────────────────────────────────────────────────────────

def _direct_names(tenant_id: str) -> set:
    from vula.commerce import accounting
    try:
        return {a.get("name") for a in accounting.ensure_chart(tenant_id) if a.get("code") in DIRECT_COSTS}
    except Exception:  # noqa: BLE001
        return set()


def _pnl(tenant_id: str, per: Optional[Dict[str, Any]], one_book: bool) -> Optional[Dict[str, Any]]:
    from vula.commerce import accounting
    if not per:
        return None
    return accounting.pnl(tenant_id, per["from"], per["to"], "accrual" if one_book else None)


def _pnl_table(cols: Dict[str, Optional[Dict[str, Any]]], direct: set) -> Dict[str, Any]:
    """Rows per account (income, then costs) with an amount per column; gross profit = income less the
    direct costs (cost of sales, casual labour, subcontractors)."""
    keys = [k for k, v in cols.items() if v is not None]
    out: Dict[str, Any] = {"columns": keys}
    for kind in ("income", "expenses"):
        names: List[Tuple[str, Optional[str]]] = []
        for k in keys:
            for r in cols[k][kind]:
                n = (r["account"], r.get("code"))
                if n[0] not in [x[0] for x in names]:
                    names.append(n)
        rows = []
        for name, code in names:
            amounts = {k: sum(int(r["amount_cents"]) for r in cols[k][kind] if r["account"] == name) for k in keys}
            is_direct = (code in DIRECT_COSTS) if code else name in direct
            rows.append({"account": name, "code": code, "direct": kind == "expenses" and is_direct, **{"amounts": amounts}})
        rows.sort(key=lambda r: -r["amounts"].get("this", 0))
        out[kind] = rows
    totals = {}
    for k in keys:
        inc = int(cols[k]["total_income_cents"])
        exp = int(cols[k]["total_expense_cents"])
        dc = sum(r["amounts"][k] for r in out["expenses"] if r["direct"])
        if sum(r["amounts"][k] for r in out["income"]) != inc or sum(r["amounts"][k] for r in out["expenses"]) != exp:
            raise RuntimeError("the profit and loss rows don't add up to their totals")
        totals[k] = {"income_cents": inc, "direct_cents": dc, "gross_cents": inc - dc, "expense_cents": exp,
                     "overheads_cents": exp - dc, "net_cents": inc - exp}
    out["totals"] = totals
    return out


def _bank_rows(tenant_id: str) -> List[Dict[str, Any]]:
    from vula.commerce.year_end_pack import _bank_rows as rows
    return rows(tenant_id)


def _cash_from_lines(rows: List[Dict[str, Any]], a: str, b: str) -> Dict[str, Any]:
    """Per bank account, from its statement lines: the balance before the month, money in and out in it,
    and the last balance on or before the month end. An account with no lines in the month carries its
    last balance. Lines on no account number are counted apart, never put on an account."""
    from vula.commerce import bank_recon
    from vula.commerce import ledger_events as ev
    live = [r for r in rows if ev.is_live(r) and str(r.get("txn_date"))[:10] <= b]
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for r in live:
        groups.setdefault(bank_recon._key(r), []).append(r)
    accounts = []
    for acct, lines in sorted(groups.items()):
        if acct == bank_recon.UNASSIGNED:
            continue
        ordered = bank_recon.order_lines(lines)
        before = [r for r in ordered if str(r.get("txn_date"))[:10] < a]
        during = [r for r in ordered if str(r.get("txn_date"))[:10] >= a]
        if not before and not during:
            continue
        moved_in = sum(bank_recon._signed(r) for r in during if bank_recon._signed(r) > 0)
        moved_out = -sum(bank_recon._signed(r) for r in during if bank_recon._signed(r) < 0)
        if before and before[-1].get("balance_cents") is not None:
            opening = int(before[-1]["balance_cents"])
        elif during and during[0].get("balance_cents") is not None:
            opening = int(during[0]["balance_cents"]) - bank_recon._signed(during[0])
        else:
            opening = None
        last = (during or before)[-1]
        closing = int(last["balance_cents"]) if last.get("balance_cents") is not None else None
        worked = None if opening is None else opening + moved_in - moved_out
        accounts.append({"account": f"…{acct[-4:]}", "number": acct, "lines": len(during),
                         "opening_cents": opening, "in_cents": moved_in, "out_cents": moved_out,
                         "closing_cents": closing, "as_at": str(last.get("txn_date"))[:10],
                         "adds_up": worked is not None and closing is not None and worked == closing})
    un = [r for r in groups.get(bank_recon.UNASSIGNED, []) if str(r.get("txn_date"))[:10] >= a]
    return {"accounts": accounts,
            "unassigned": {"lines": len(un),
                           "in_cents": sum(bank_recon._signed(r) for r in un if bank_recon._signed(r) > 0),
                           "out_cents": -sum(bank_recon._signed(r) for r in un if bank_recon._signed(r) < 0)}}


def _cash(tenant_id: str, a: str, b: str, one_book: bool, rows: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    if one_book:
        from vula.commerce import books_reports
        accts = [{"account": r["account"], "opening_cents": r["opening_cents"], "in_cents": r["in_cents"],
                  "out_cents": r["out_cents"], "closing_cents": r["closing_cents"], "as_at": b, "adds_up": True,
                  "lines": None} for r in books_reports.cash_by_account(tenant_id, a, b, "accrual")]
        out = {"accounts": accts, "unassigned": {"lines": 0, "in_cents": 0, "out_cents": 0}}
    else:
        out = _cash_from_lines(rows or [], a, b)
        try:
            from vula.commerce import books_reports
            labels = books_reports.bank_labels(tenant_id)
            ids = {x["account_number"]: str(x["id"]) for x in (_client().table("commerce_bank_accounts")
                   .select("id,account_number").eq("tenant_id", tenant_id).execute().data or [])
                   if x.get("account_number")}
            for acc in out["accounts"]:
                acc["account"] = labels.get(ids.get(acc.pop("number"), ""), acc["account"])
        except Exception:  # noqa: BLE001
            for acc in out["accounts"]:
                acc.pop("number", None)
    known = [x["closing_cents"] for x in out["accounts"] if x["closing_cents"] is not None]
    out["closing_cents"] = sum(known) if known else None
    out["in_cents"] = sum(x["in_cents"] for x in out["accounts"]) + out["unassigned"]["in_cents"]
    out["out_cents"] = sum(x["out_cents"] for x in out["accounts"]) + out["unassigned"]["out_cents"]
    return out


def _owed(tenant_id: str, as_at: str) -> Dict[str, Any]:
    from vula.commerce import finances
    out = {}
    for key, direction in (("customers", "outbound"), ("suppliers", "inbound")):
        a = finances.aged_as_at(tenant_id, as_at, direction)
        ag = a["aging"]
        out[key] = {"total_cents": a["total_cents"], "count": a["count"], "aging": ag,
                    "over_60_cents": int(ag.get("days_61_90", 0)) + int(ag.get("days_90_plus", 0)),
                    "top": [{"name": p["name"], "owed_cents": p["owed_cents"]} for p in a["parties"][:5]]}
    return out


def _vat(tenant_id: str, per: Dict[str, Any], registered: bool) -> Optional[Dict[str, Any]]:
    if not registered:
        return None
    from vula.commerce import accounting
    v = accounting.vat_return(tenant_id, per["from"], per["to"])
    out_c, in_c = int(v.get("output_vat_cents") or 0), int(v.get("input_vat_cents") or 0)
    return {"output_cents": out_c, "input_cents": in_c, "net_cents": out_c - in_c, "note": v.get("note") or ""}


def _recent_months(p: Dict[str, Any], n: int = 3) -> List[Tuple[str, str]]:
    """Up to `n` whole months ending with this one, none before the first trading month."""
    y, m = (int(x) for x in p["month"].split("-"))
    first = p["first_month"]
    out = []
    for i in range(n):
        yy, mm = _shift(y, m, -i)
        if f"{yy:04d}-{mm:02d}" < first:
            break
        a, b = _month_bounds(yy, mm)
        out.append((a.isoformat(), min(b, date.fromisoformat(p["this"]["to"])).isoformat()))
    return out


def figures(tenant_id: str, month: Optional[str] = None, today: Optional[date] = None) -> Dict[str, Any]:
    """Everything the pack shows, in one dict (integer cents)."""
    from vula.commerce import accounting, books_review, finance_folders, statement_files
    p = period(tenant_id, month, today)
    one_book = p["one_book"]
    rows = None if one_book else _bank_rows(tenant_id)
    cols = {"this": _pnl(tenant_id, p["this"], one_book), "previous": _pnl(tenant_id, p["previous"], one_book),
            "last_year": _pnl(tenant_id, p["last_year"], one_book), "ytd": _pnl(tenant_id, p["ytd"], one_book)}
    table = _pnl_table(cols, _direct_names(tenant_id))
    a, b = p["this"]["from"], p["this"]["to"]
    cash = _cash(tenant_id, a, b, one_book, rows)
    owed = _owed(tenant_id, b)
    registered = accounting.is_vat_registered(tenant_id)
    vat = _vat(tenant_id, p["this"], registered)

    # key figures
    t = table["totals"]["this"]
    recent = _recent_months(p)
    sales_recent = sum(int(_pnl(tenant_id, {"from": x, "to": y}, one_book)["total_income_cents"])
                       for x, y in recent[1:]) + t["income_cents"]
    days_recent = sum((date.fromisoformat(y) - date.fromisoformat(x)).days + 1 for x, y in recent)
    debtors = owed["customers"]["total_cents"]
    debtor_days = (int((Decimal(debtors) * days_recent / Decimal(sales_recent)).quantize(Decimal("1"), ROUND_HALF_UP))
                   if debtors and sales_recent > 0 else None)
    moves = []
    for x, y in recent:
        if x == a:
            moves.append(cash["in_cents"] - cash["out_cents"])
        else:
            c = _cash(tenant_id, x, y, one_book, rows)
            moves.append(c["in_cents"] - c["out_cents"])
    avg_move = (Decimal(sum(moves)) / len(moves)) if moves else Decimal(0)
    runway = None
    if avg_move < 0 and cash["closing_cents"] and cash["closing_cents"] > 0:
        runway = float((Decimal(cash["closing_cents"]) / -avg_move).quantize(Decimal("0.1"), ROUND_HALF_UP))
    kpis = {"gross_margin_pct": pct(t["gross_cents"], t["income_cents"]),
            "net_margin_pct": pct(t["net_cents"], t["income_cents"]),
            "debtor_days": debtor_days, "debtor_days_window": days_recent,
            "average_monthly_cash_cents": int(avg_move.quantize(Decimal("1"), ROUND_HALF_UP)) if moves else None,
            "months_averaged": len(moves), "cash_runway_months": runway}

    try:
        audit = books_review.latest(tenant_id)
    except Exception:  # noqa: BLE001
        audit = None
    missing, waiting = [], []
    try:
        cov = statement_files.coverage(tenant_id, date.fromisoformat(a), date.fromisoformat(b))
        missing = [x["account"] for x in cov.get("accounts") or [] if p["month"] in (x.get("missing") or [])]
    except Exception:  # noqa: BLE001
        pass
    try:
        # an official statement compared but not applied: its lines aren't in the books yet
        for c in (_client().table("vula_statement_checks").select("account_number,period_from,period_to,status")
                  .eq("tenant_id", tenant_id).eq("status", "proposed").execute().data or []):
            if (c.get("account_number") and str(c.get("period_from") or "9")[:10] <= b
                    and str(c.get("period_to") or "0")[:10] >= a):
                acc = f"…{str(c['account_number'])[-4:]}"
                if acc not in waiting:
                    waiting.append(acc)
    except Exception:  # noqa: BLE001
        pass
    f = {"tenant_id": tenant_id, "business": finance_folders._business(tenant_id) or tenant_id, "period": p,
         "source": "journal" if one_book else "bank", "vat_registered": registered, "pnl": table,
         "cash": cash, "owed": owed, "vat": vat, "kpis": kpis, "statements_missing": missing,
         "statements_waiting": waiting,
         "books_check": {"run": bool(audit), "agreed": bool((audit or {}).get("agreed_by")),
                         "signed_off": bool((audit or {}).get("signed_off_by"))}}
    f["notes"] = notes(f)
    return f


def position(tenant_id: str, today: Optional[date] = None) -> Dict[str, Any]:
    """Where the business stands now, for the Home card: money in the bank per account (its last
    statement balance), what customers owe and what's owed to suppliers today, profit so far this month
    and, for a VAT-registered business, the month's VAT. Lighter than `figures`."""
    from vula.commerce import accounting, finances, ledger_v2
    today = today or _today()
    one_book = ledger_v2.is_on(tenant_id)
    a, b = today.replace(day=1).isoformat(), today.isoformat()
    cash = _cash(tenant_id, a, b, one_book, None if one_book else _bank_rows(tenant_id))
    month = accounting.pnl(tenant_id, a, b, "accrual" if one_book else None)
    registered = accounting.is_vat_registered(tenant_id)
    vat = _vat(tenant_id, {"from": a, "to": b}, registered)
    return {"as_at": b, "source": "journal" if one_book else "bank", "month": _label(today.replace(day=1)),
            "bank": {"total_cents": cash["closing_cents"],
                     "accounts": [{"account": x["account"], "closing_cents": x["closing_cents"], "as_at": x["as_at"]}
                                  for x in cash["accounts"]],
                     "lines_on_no_account": cash["unassigned"]["lines"]},
            "owed_to_you_cents": finances.aged_as_at(tenant_id, b, "outbound")["total_cents"],
            "you_owe_cents": finances.aged_as_at(tenant_id, b, "inbound")["total_cents"],
            "sales_month_cents": int(month["total_income_cents"]), "profit_month_cents": int(month["net_profit_cents"]),
            "vat_month_cents": vat["net_cents"] if vat else None}


# ── notes, each one thing, built from the figures ───────────────────────────

def _change_words(now: int, before: int, label: str) -> str:
    c = pct(now - before, abs(before)) if before else None
    if c is None or now == before:
        return ""
    return f" — {'up' if now > before else 'down'} {abs(c):g}% on {label.split()[0]}"


def notes(f: Dict[str, Any]) -> List[str]:
    p, t = f["period"], f["pnl"]["totals"]
    this = t["this"]
    prev = t.get("previous")
    month = p["this"]["label"].split()[0]
    out: List[str] = []
    if not p["ended"]:
        out.append(f"{month} isn't over yet — figures to {p['this']['to']}.")
    out.append(f"Sales {_r(this['income_cents'])}" + (_change_words(this["income_cents"], prev["income_cents"],
                                                                     p["previous"]["label"]) if prev else "") + ".")
    if this["net_cents"] >= 0:
        out.append(f"Profit for {month}: {_r(this['net_cents'])}.")
    else:
        out.append(f"{month} made a loss of {_r(-this['net_cents'])}.")
    costs = [r for r in f["pnl"]["expenses"] if r["amounts"].get("this")]
    if costs:
        top = max(costs, key=lambda r: r["amounts"]["this"])
        out.append(f"Largest cost: {top['account']} {_r(top['amounts']['this'])}"
                   + (f" ({pct(top['amounts']['this'], this['expense_cents']):g}% of costs)." if this["expense_cents"] else "."))
    cu = f["owed"]["customers"]
    if cu["total_cents"]:
        out.append(f"Customers owe {_r(cu['total_cents'])}"
                   + (f", {_r(cu['over_60_cents'])} of it over 60 days past due." if cu["over_60_cents"] else "."))
    su = f["owed"]["suppliers"]
    if su["total_cents"]:
        out.append(f"You owe suppliers {_r(su['total_cents'])}"
                   + (f", {_r(su['over_60_cents'])} of it over 60 days past due." if su["over_60_cents"] else "."))
    k = f["kpis"]
    if k["cash_runway_months"] is not None:
        out.append(f"Cash went down by about {_r(-k['average_monthly_cash_cents'])} a month over the last "
                   f"{k['months_averaged']} month{'s' if k['months_averaged'] != 1 else ''}: at that rate the money in "
                   f"the bank lasts about {k['cash_runway_months']:g} months.")
    if f["vat"]:
        v = f["vat"]["net_cents"]
        out.append(f"VAT for {month}: {_r(v)} to pay SARS." if v >= 0 else f"VAT for {month}: {_r(-v)} to claim back.")
    for acc in f["statements_missing"]:
        out.append(f"No statement on file for {acc} in {month} — these figures may be short.")
    waiting = f.get("statements_waiting") or []
    if waiting:
        names = ", ".join(waiting[:-1]) + (" and " if len(waiting) > 1 else "") + waiting[-1]
        out.append(f"The official {month} statement{'s' if len(waiting) > 1 else ''} for {names} "
                   f"{'have' if len(waiting) > 1 else 'has'} been compared but not applied, so lines from "
                   f"{'them' if len(waiting) > 1 else 'it'} may be missing here — apply "
                   f"{'them' if len(waiting) > 1 else 'it'} in Money › Books review › Statements.")
    if f["source"] == "bank":
        for acc in f["cash"]["accounts"]:
            if acc.get("lines") == 0 and not any(acc["account"].endswith(w) for w in waiting):
                out.append(f"No bank lines for {acc['account']} in {month}: its balance is the last one on file, "
                           f"from {_day(acc['as_at'])}.")
    un = f["cash"]["unassigned"]
    if un["lines"]:
        out.append(f"{un['lines']} bank line{'s' if un['lines'] != 1 else ''} this month {'are' if un['lines'] != 1 else 'is'} "
                   "on no account number, so they're in the totals but not on an account.")
    for acc in f["cash"]["accounts"]:
        if acc.get("lines") and not acc.get("adds_up"):
            out.append(f"The statement lines for {acc['account']} don't add up to its closing balance — "
                       "a line may be missing or set aside by mistake (Money › Books review).")
    if not p["last_year"]:
        out.append(f"No comparison with {month} last year — the business wasn't trading yet "
                   f"(its books start {_day(p['first_trading_day'])}).")
    if f["source"] == "bank":
        bc = f["books_check"]
        out.append("Figures are from the bank statements (money in and out, by category)"
                   + ("; the books check isn't agreed yet." if not bc["agreed"] else "."))
    return out


# ── the workbook ─────────────────────────────────────────────────────────────



def _columns(f: Dict[str, Any]) -> List[Tuple[str, str]]:
    p = f["period"]
    out = [("this", p["this"]["label"])]
    if p["previous"]:
        out += [("previous", p["previous"]["label"]), ("change", "Change")]
    if p["last_year"]:
        out.append(("last_year", p["last_year"]["label"]))
    out.append(("ytd", "Year to date"))
    return out


def _source_words(f: Dict[str, Any]) -> str:
    return ("From the books (accrual view: sales when invoiced, costs when billed)" if f["source"] == "journal"
            else "From the bank statements: money in and out, by category")


def workbook(f: Dict[str, Any]) -> bytes:
    """The pack as Excel — every amount and formula read back from the saved file to the cent."""
    from openpyxl.utils import get_column_letter
    from vula.commerce.year_end_pack import Sheet
    wb = Workbook()
    wb.remove(wb.active)
    p = f["period"]
    sheets: List[Sheet] = []

    sh = Sheet(wb, "Summary")
    sh.heading(f"{f['business']} — management accounts, {p['this']['label']}", _source_words(f))
    t = f["pnl"]["totals"]["this"]
    k = f["kpis"]
    for label, cents in (("Sales", t["income_cents"]), ("Gross profit", t["gross_cents"]),
                         ("Profit for the month", t["net_cents"]), ("In the bank at the month end", f["cash"]["closing_cents"]),
                         ("Customers owe", f["owed"]["customers"]["total_cents"]),
                         ("You owe suppliers", f["owed"]["suppliers"]["total_cents"])):
        sh.text(1, label, bold=True)
        if cents is None:
            sh.text(2, "not known")
        else:
            sh.money(2, cents)
        sh.row += 1
    for label, v in (("Gross margin", f"{k['gross_margin_pct']:g}%" if k["gross_margin_pct"] is not None else "—"),
                     ("Net margin", f"{k['net_margin_pct']:g}%" if k["net_margin_pct"] is not None else "—"),
                     ("Debtor days", str(k["debtor_days"]) if k["debtor_days"] is not None else "—"),
                     ("Cash runway (months)", f"{k['cash_runway_months']:g}" if k["cash_runway_months"] is not None else "—")):
        sh.text(1, label, bold=True)
        sh.text(2, v)
        sh.row += 1
    sh.row += 1
    sh.text(1, "Notes", bold=True)
    sh.row += 1
    for n in f["notes"]:
        sh.note(n)
    sh.ws.column_dimensions["A"].width = 34
    sh.ws.column_dimensions["B"].width = 90
    sheets.append(sh)

    sh = Sheet(wb, "Profit and loss")
    sh.heading("Profit and loss", _source_words(f))
    cols = _columns(f)
    sh.header(["Account"] + [c[1] for c in cols], [36] + [18] * len(cols))
    pos = {key: 2 + i for i, (key, _l) in enumerate(cols)}
    tot = f["pnl"]["totals"]

    def block(kind: str, title: str) -> Tuple[int, int]:
        sh.text(1, title, bold=True)
        sh.row += 1
        first = sh.row
        for r in f["pnl"][kind]:
            sh.text(1, r["account"] + (" (direct cost)" if r.get("direct") else ""))
            for key, _l in cols:
                if key == "change":
                    continue
                if key in r["amounts"]:
                    sh.money(pos[key], r["amounts"][key])
            if "change" in pos:
                th, pv = r["amounts"].get("this", 0), r["amounts"].get("previous", 0)
                sh.formula(pos["change"], f"={get_column_letter(pos['this'])}{sh.row}-{get_column_letter(pos['previous'])}{sh.row}",
                           th - pv, bold=False)
            sh.row += 1
        return first, sh.row - 1

    fi, li = block("income", "Income")
    sh.text(1, "Total income", bold=True)
    inc_row = sh.row
    for key, _l in cols:
        if key == "change":
            sh.formula(pos[key], f"={get_column_letter(pos['this'])}{sh.row}-{get_column_letter(pos['previous'])}{sh.row}",
                       tot["this"]["income_cents"] - tot["previous"]["income_cents"])
        else:
            sh.sum_col(pos[key], fi, li, tot[key]["income_cents"])
    sh.row += 2
    fe, le = block("expenses", "Costs")
    sh.text(1, "Total costs", bold=True)
    exp_row = sh.row
    for key, _l in cols:
        if key == "change":
            sh.formula(pos[key], f"={get_column_letter(pos['this'])}{sh.row}-{get_column_letter(pos['previous'])}{sh.row}",
                       tot["this"]["expense_cents"] - tot["previous"]["expense_cents"])
        else:
            sh.sum_col(pos[key], fe, le, tot[key]["expense_cents"])
    sh.row += 2
    sh.text(1, "Gross profit (income less direct costs)", bold=True)
    for key, _l in cols:
        if key == "change":
            continue
        sh.money(pos[key], tot[key]["gross_cents"], bold=True)
    sh.row += 1
    sh.text(1, "Net profit (loss)", bold=True)
    for key, _l in cols:
        L = get_column_letter(pos[key])
        want = (tot["this"]["net_cents"] - tot["previous"]["net_cents"]) if key == "change" else tot[key]["net_cents"]
        sh.formula(pos[key], f"={L}{inc_row}-{L}{exp_row}", want)
    sheets.append(sh)

    sh = Sheet(wb, "Cash")
    sh.heading("Cash", f"Per bank account, {_day(p['this']['from'])} to {_day(p['this']['to'])}")
    first = sh.header(["Account", "Opening (R)", "Money in (R)", "Money out (R)", "Closing (R)", "As at"],
                      [34, 18, 18, 18, 18, 12])
    known = 0
    for acc in f["cash"]["accounts"]:
        sh.text(1, acc["account"])
        o = sh.money(2, acc["opening_cents"])
        i = sh.money(3, acc["in_cents"])
        out = sh.money(4, acc["out_cents"])
        if acc["closing_cents"] is not None:
            sh.money(5, acc["closing_cents"])
            known += acc["closing_cents"]
        sh.text(6, acc["as_at"])
        del o, i, out
        sh.row += 1
    sh.text(1, "Total", bold=True)
    sh.sum_col(5, first, sh.row - 1, known)
    un = f["cash"]["unassigned"]
    if un["lines"]:
        sh.row += 2
        sh.text(1, f"{un['lines']} bank lines on no account number this month", bold=True)
        sh.row += 1
        sh.text(1, "Money in")
        sh.money(2, un["in_cents"])
        sh.row += 1
        sh.text(1, "Money out")
        sh.money(2, un["out_cents"])
    sheets.append(sh)

    sh = Sheet(wb, "Owed")
    sh.heading("What's owed", f"At {_day(p['this']['to'])}, by how far past due")
    buckets = [("current", "Not yet due"), ("days_1_30", "1–30 days"), ("days_31_60", "31–60 days"),
               ("days_61_90", "61–90 days"), ("days_90_plus", "Over 90 days")]
    first = sh.header(["Past due", "Customers owe (R)", "You owe suppliers (R)"], [28, 20, 22])
    cu, su = f["owed"]["customers"], f["owed"]["suppliers"]
    for key, label in buckets:
        sh.text(1, label)
        sh.money(2, int(cu["aging"].get(key, 0)))
        sh.money(3, int(su["aging"].get(key, 0)))
        sh.row += 1
    sh.text(1, "Total", bold=True)
    sh.sum_col(2, first, sh.row - 1, cu["total_cents"])
    sh.sum_col(3, first, sh.row - 1, su["total_cents"])
    for title, who in (("Largest amounts customers owe", cu), ("Largest amounts owed to suppliers", su)):
        sh.row += 2
        sh.text(1, title, bold=True)
        sh.row += 1
        for t5 in who["top"]:
            sh.text(1, t5["name"])
            sh.money(2, t5["owed_cents"])
            sh.row += 1
    sheets.append(sh)

    sh = Sheet(wb, "VAT")
    sh.heading("VAT", p["this"]["label"])
    if not f["vat"]:
        sh.note("The business is not set as VAT registered in Vula, so no VAT is worked out.")
    else:
        v = f["vat"]
        for label, cents in (("VAT on sales", v["output_cents"]), ("VAT on costs", v["input_cents"])):
            sh.text(1, label)
            sh.money(2, cents)
            sh.row += 1
        sh.text(1, "Net to SARS", bold=True)
        sh.formula(2, f"=B{sh.row - 2}-B{sh.row - 1}", v["net_cents"])
        sh.row += 1
        sh.note(v["note"])
    sheets.append(sh)

    buf = io.BytesIO()
    wb.save(buf)
    data = buf.getvalue()
    for s in sheets:
        bad = mismatches(data, s.want, sheet=s.title)
        if bad:
            raise RuntimeError(f"the {s.title} sheet didn't read back: {bad[:3]}")
    return data


# ── the PDF ──────────────────────────────────────────────────────────────────

def _html(f: Dict[str, Any]) -> str:
    e = html.escape
    p = f["period"]
    cols = _columns(f)
    tot = f["pnl"]["totals"]
    k = f["kpis"]
    t = tot["this"]

    def cell(r_amounts: Dict[str, int], key: str) -> str:
        if key == "change":
            return _r(r_amounts.get("this", 0) - r_amounts.get("previous", 0))
        return _r(r_amounts.get(key, 0))

    head = "".join(f"<th class=n>{e(label)}</th>" for _k, label in cols)

    def rows(kind: str) -> str:
        return "".join(f"<tr><td>{e(r['account'])}{' <span class=m>direct</span>' if r.get('direct') else ''}</td>"
                       + "".join(f"<td class=n>{cell(r['amounts'], key)}</td>" for key, _l in cols) + "</tr>"
                       for r in f["pnl"][kind])

    def total(label: str, field: str) -> str:
        vals = {key: tot[key][field] for key, _l in cols if key != "change"}
        return (f"<tr class=t><td>{e(label)}</td>"
                + "".join(f"<td class=n>{cell(vals, key)}</td>" for key, _l in cols) + "</tr>")

    tiles = [("Sales", _r(t["income_cents"])), ("Gross profit", _r(t["gross_cents"])),
             ("Profit for the month", _r(t["net_cents"])), ("In the bank", _r(f["cash"]["closing_cents"])),
             ("Customers owe", _r(f["owed"]["customers"]["total_cents"])),
             ("You owe suppliers", _r(f["owed"]["suppliers"]["total_cents"]))]
    kpi = [("Gross margin", f"{k['gross_margin_pct']:g}%" if k["gross_margin_pct"] is not None else "—"),
           ("Net margin", f"{k['net_margin_pct']:g}%" if k["net_margin_pct"] is not None else "—"),
           ("Debtor days", str(k["debtor_days"]) if k["debtor_days"] is not None else "—"),
           ("Cash runway", f"{k['cash_runway_months']:g} months" if k["cash_runway_months"] is not None else "—")]
    tile_html = "".join(f"<td><div class=l>{e(a)}</div><div class=v>{e(b)}</div></td>" for a, b in tiles)
    kpi_html = "".join(f"<td><div class=l>{e(a)}</div><div class=v>{e(b)}</div></td>" for a, b in kpi)
    cash = "".join(f"<tr><td>{e(a['account'])}</td><td class=n>{_r(a['opening_cents'])}</td><td class=n>{_r(a['in_cents'])}</td>"
                   f"<td class=n>{_r(a['out_cents'])}</td><td class=n>{_r(a['closing_cents'])}</td><td>{e(a['as_at'])}</td></tr>"
                   for a in f["cash"]["accounts"]) or "<tr><td colspan=6>No bank accounts on file.</td></tr>"

    def top(who: Dict[str, Any]) -> str:
        return "".join(f"<tr><td>{e(x['name'])}</td><td class=n>{_r(x['owed_cents'])}</td></tr>" for x in who["top"]) \
            or "<tr><td>Nothing owed.</td><td></td></tr>"
    cu, su = f["owed"]["customers"], f["owed"]["suppliers"]
    vat = ""
    if f["vat"]:
        v = f["vat"]
        vat = (f"<h2>VAT</h2><table><tr><td>VAT on sales</td><td class=n>{_r(v['output_cents'])}</td></tr>"
               f"<tr><td>VAT on costs</td><td class=n>{_r(v['input_cents'])}</td></tr>"
               f"<tr class=t><td>Net to SARS</td><td class=n>{_r(v['net_cents'])}</td></tr></table>")
    notes_html = "".join(f"<li>{e(n)}</li>" for n in f["notes"])
    return f"""<html><head><meta charset="utf-8"><style>
@page{{size:A4;margin:16mm 14mm}} body{{font-family:sans-serif;font-size:10px;color:#222}}
h1{{font-size:17px;margin:0}} h2{{font-size:12px;margin:16px 0 4px;border-bottom:1px solid #ccc}}
table{{width:100%;border-collapse:collapse}} td,th{{padding:3px 4px;vertical-align:top}} th{{text-align:left;
border-bottom:1px solid #999}} .n{{text-align:right;white-space:nowrap}} .t td{{font-weight:bold;border-top:1px solid #999}}
.tiles td{{border:1px solid #ddd;width:16%}} .l{{color:#666;font-size:9px}} .v{{font-size:13px;font-weight:bold}}
.m{{color:#777;font-size:8px}} .sub{{color:#555}} ul{{margin:4px 0 0 16px;padding:0}} li{{margin:2px 0}}
</style></head><body>
<h1>{e(f['business'])} — management accounts</h1>
<p class=sub>{e(p['this']['label'])} ({e(_day(p['this']['from']))} to {e(_day(p['this']['to']))}) · {e(_source_words(f))} · prepared by Vula</p>
<table class=tiles><tr>{tile_html}</tr></table>
<table class=tiles style="margin-top:6px"><tr>{kpi_html}</tr></table>
<h2>Notes</h2><ul>{notes_html}</ul>
<h2>Profit and loss</h2>
<table><tr><th>Account</th>{head}</tr>
<tr><td colspan={len(cols) + 1}><b>Income</b></td></tr>{rows('income')}{total('Total income', 'income_cents')}
<tr><td colspan={len(cols) + 1}><b>Costs</b></td></tr>{rows('expenses')}{total('Total costs', 'expense_cents')}
{total('Gross profit (income less direct costs)', 'gross_cents')}{total('Net profit (loss)', 'net_cents')}</table>
<h2>Cash at {e(_day(p['this']['to']))}</h2>
<table><tr><th>Account</th><th class=n>Opening</th><th class=n>Money in</th><th class=n>Money out</th><th class=n>Closing</th><th>As at</th></tr>
{cash}<tr class=t><td>Total</td><td></td><td></td><td></td><td class=n>{_r(f['cash']['closing_cents'])}</td><td></td></tr></table>
<h2>What's owed at {e(_day(p['this']['to']))}</h2>
<table><tr><td style="width:50%"><b>Customers owe {_r(cu['total_cents'])}</b><table>{top(cu)}</table></td>
<td><b>You owe suppliers {_r(su['total_cents'])}</b><table>{top(su)}</table></td></tr></table>
{vat}
</body></html>"""


def pdf(f: Dict[str, Any]) -> bytes:
    from weasyprint import HTML
    return HTML(string=_html(f)).write_pdf()


def summary_text(f: Dict[str, Any]) -> str:
    """A short WhatsApp message to go with the PDF (no markdown links; *bold* only)."""
    p = f["period"]
    t = f["pnl"]["totals"]["this"]
    lines = [f"*{f['business']} — management accounts, {p['this']['label']}*",
             (f"Profit for the month: {_r(t['net_cents'])}" if t["net_cents"] >= 0
              else f"Loss for the month: {_r(-t['net_cents'])}") + f" on sales of {_r(t['income_cents'])}."]
    if f["cash"]["closing_cents"] is not None:
        lines.append(f"In the bank at {_day(p['this']['to'])}: {_r(f['cash']['closing_cents'])}.")
    lines.append(f"Customers owe you {_r(f['owed']['customers']['total_cents'])}; "
                 f"you owe suppliers {_r(f['owed']['suppliers']['total_cents'])}.")
    said = ("Sales ", "Profit for", "Customers owe", "You owe suppliers")   # already in the lines above
    extra = [n for n in f["notes"] if not n.startswith(said) and "made a loss" not in n][:2]
    lines += [f"• {n}" for n in extra]
    lines.append("The full pack is attached.")
    return "\n".join(lines)


def filename(f: Dict[str, Any], kind: str) -> str:
    return f"Management_accounts_{f['period']['month']}.{kind}"


def public(f: Dict[str, Any]) -> Dict[str, Any]:
    """The dict as the dashboard reads it."""
    return {k: v for k, v in f.items() if k != "tenant_id"}
