"""
vula/commerce/reports.py — the financial statements, every figure read from the ledger.

Profit & loss, VAT summary, balance sheet and cash flow are all built from the same posted
journal (ledger.trial_balance over journal_entries/journal_lines), so they reconcile with each
other and with the trial balance by construction:

  * P&L net profit      = income credits − debits  −  expense debits − credits   (period)
  * VAT payable         = VAT output (credit-normal) − VAT input (debit-normal)  (period)
  * Balance sheet       : assets = liabilities + equity + retained earnings      (as at a date)
                          retained earnings = cumulative net profit to that date
  * Cash flow           : opening bank + money in − money out = closing bank     (period)

Before this the P&L and VAT report read bank transactions (accounting.pnl / vat_return) while the
trial balance read the ledger, so the two could disagree. All money is integer cents; the
WhatsApp summary and the PDF are rendered from these dicts in code, never by a model.
"""
from __future__ import annotations

import html
import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

_DEBIT_NORMAL = ("asset", "expense")


def _client():
    from vula.commerce import service
    return service._client()


def _tb(tenant_id: str, since: Optional[str], until: Optional[str]) -> List[Dict[str, Any]]:
    from vula.commerce import ledger
    tb = ledger.trial_balance(tenant_id, since=since, until=until)
    if tb.get("error"):
        raise RuntimeError(tb["error"])
    return tb.get("accounts") or []


def _bal(acct: Dict[str, Any]) -> int:
    """Natural-side balance: debit-normal for assets/expenses, credit-normal for the rest."""
    dr, cr = int(acct.get("debit_cents") or 0), int(acct.get("credit_cents") or 0)
    return dr - cr if acct.get("type") in _DEBIT_NORMAL else cr - dr


def _day_before(d: str) -> str:
    return (date.fromisoformat(d[:10]) - timedelta(days=1)).isoformat()


def profit_and_loss(tenant_id: str, since: Optional[str] = None, until: Optional[str] = None) -> Dict[str, Any]:
    accts = _tb(tenant_id, since, until)
    income = [{"code": a["code"], "name": a["name"], "cents": _bal(a)} for a in accts
              if a.get("type") == "income" and _bal(a)]
    expense = [{"code": a["code"], "name": a["name"], "cents": _bal(a)} for a in accts
               if a.get("type") == "expense" and _bal(a)]
    ti, te = sum(i["cents"] for i in income), sum(e["cents"] for e in expense)
    return {"since": since, "until": until, "income": income, "expenses": expense,
            "total_income_cents": ti, "total_expense_cents": te, "net_profit_cents": ti - te}


def vat_summary(tenant_id: str, since: Optional[str] = None, until: Optional[str] = None) -> Dict[str, Any]:
    by = {a["code"]: a for a in _tb(tenant_id, since, until)}
    out = _bal(by["vat_output"]) if "vat_output" in by else 0
    inp = _bal(by["vat_input"]) if "vat_input" in by else 0
    return {"since": since, "until": until, "output_vat_cents": out, "input_vat_cents": inp,
            "vat_payable_cents": out - inp}


def balance_sheet(tenant_id: str, as_of: Optional[str] = None) -> Dict[str, Any]:
    accts = _tb(tenant_id, None, as_of)
    pick = lambda t: [{"code": a["code"], "name": a["name"], "cents": _bal(a)}  # noqa: E731
                      for a in accts if a.get("type") == t and _bal(a)]
    assets, liabilities, equity = pick("asset"), pick("liability"), pick("equity")
    retained = (sum(_bal(a) for a in accts if a.get("type") == "income")
                - sum(_bal(a) for a in accts if a.get("type") == "expense"))
    ta = sum(a["cents"] for a in assets)
    tl = sum(a["cents"] for a in liabilities)
    te = sum(a["cents"] for a in equity) + retained
    return {"as_of": as_of, "assets": assets, "liabilities": liabilities, "equity": equity,
            "retained_earnings_cents": retained, "total_assets_cents": ta,
            "total_liabilities_cents": tl, "total_equity_cents": te,
            "balanced": ta == tl + te}


def cash_flow(tenant_id: str, since: str, until: str) -> Dict[str, Any]:
    """Bank/cash account movement for the period, split by what caused it."""
    opening = sum(_bal(a) for a in _tb(tenant_id, None, _day_before(since)) if a["code"] == "bank_cash")
    period = [a for a in _tb(tenant_id, since, until) if a["code"] == "bank_cash"]
    money_in = sum(int(a.get("debit_cents") or 0) for a in period)
    money_out = sum(int(a.get("credit_cents") or 0) for a in period)
    return {"since": since, "until": until, "opening_cents": opening, "in_cents": money_in,
            "out_cents": money_out, "closing_cents": opening + money_in - money_out,
            "by_source": _cash_by_source(tenant_id, since, until)}


_SOURCE_LABELS = {"invoice_paid": "Invoices paid", "invoice_payment": "Invoice payments",
                  "order_paid": "Orders paid", "order_refund": "Order refunds",
                  "invoice_refund": "Credit-note refunds", "expense": "Expenses",
                  "supplier_invoice_paid": "Supplier bills paid"}


def _cash_by_source(tenant_id: str, since: str, until: str) -> List[Dict[str, Any]]:
    from vula.commerce.ledger import _all_pages
    db = _client()
    try:
        bank = (db.table("commerce_accounts").select("id").eq("tenant_id", tenant_id)
                .eq("code", "bank_cash").limit(1).execute().data or [])
        if not bank:
            return []
        entries = _all_pages(lambda: db.table("journal_entries").select("id,source_type")
                             .eq("tenant_id", tenant_id).gte("entry_date", since)
                             .lte("entry_date", until).order("id"))
        kind = {e["id"]: e.get("source_type") or "other" for e in entries}
        sums: Dict[str, int] = {}
        ids = list(kind)
        for i in range(0, len(ids), 150):
            for ln in _all_pages(lambda c=ids[i:i + 150]: db.table("journal_lines")
                                 .select("journal_entry_id,debit_cents,credit_cents")
                                 .in_("journal_entry_id", c).eq("account_id", bank[0]["id"])
                                 .order("id")):
                k = kind.get(ln["journal_entry_id"], "other")
                sums[k] = sums.get(k, 0) + int(ln.get("debit_cents") or 0) - int(ln.get("credit_cents") or 0)
    except Exception as exc:
        log.warning("cash-flow breakdown skipped for %s: %s", tenant_id, exc)
        return []
    return [{"source": _SOURCE_LABELS.get(k, k.replace("_", " ").capitalize()), "net_cents": v}
            for k, v in sorted(sums.items(), key=lambda kv: -abs(kv[1])) if v]


PERIODS = ("this_month", "last_month", "this_year", "tax_year", "last_tax_year", "last_12_months")


def period_dates(period: str, today: Optional[date] = None) -> tuple:
    """(since, until) as ISO dates for a named period, computed here — never by a model. The
    SA tax year runs 1 March to the end of February."""
    t = today or date.today()
    first = t.replace(day=1)
    if period == "last_month":
        end = first - timedelta(days=1)
        return end.replace(day=1).isoformat(), end.isoformat()
    if period == "this_year":
        return t.replace(month=1, day=1).isoformat(), t.isoformat()
    if period in ("tax_year", "last_tax_year"):
        start = date(t.year if t.month >= 3 else t.year - 1, 3, 1)
        if period == "last_tax_year":
            return date(start.year - 1, 3, 1).isoformat(), (start - timedelta(days=1)).isoformat()
        return start.isoformat(), t.isoformat()
    if period == "last_12_months":
        return (t - timedelta(days=365)).isoformat(), t.isoformat()
    return first.isoformat(), t.isoformat()


# ── presentation (code, not a model) ──────────────────────────────────────────

def _r(cents: int) -> str:
    sign = "−" if cents < 0 else ""
    return f"{sign}R{abs(cents) / 100:,.2f}"


def build_all(tenant_id: str, since: str, until: str) -> Dict[str, Any]:
    return {"since": since, "until": until,
            "pnl": profit_and_loss(tenant_id, since, until),
            "vat": vat_summary(tenant_id, since, until),
            "balance": balance_sheet(tenant_id, until),
            "cash": cash_flow(tenant_id, since, until)}


def summary_text(d: Dict[str, Any]) -> str:
    p, v, b, c = d["pnl"], d["vat"], d["balance"], d["cash"]
    lines = [f"📊 *Your numbers, {d['since']} to {d['until']}*",
             f"\n*Profit & loss*\nIncome {_r(p['total_income_cents'])}\nExpenses "
             f"{_r(p['total_expense_cents'])}\n*Net profit {_r(p['net_profit_cents'])}*"]
    top = sorted(p["expenses"], key=lambda e: -e["cents"])[:3]
    if top:
        lines.append("Biggest costs: " + ", ".join(f"{e['name']} {_r(e['cents'])}" for e in top))
    lines.append(f"\n*Cash*\nIn {_r(c['in_cents'])} · Out {_r(c['out_cents'])}\n"
                 f"Bank: {_r(c['opening_cents'])} → {_r(c['closing_cents'])}")
    lines.append(f"\n*VAT*\nCollected {_r(v['output_vat_cents'])} · Claimable "
                 f"{_r(v['input_vat_cents'])} · *Payable {_r(v['vat_payable_cents'])}*")
    lines.append(f"\n*Balance sheet at {d['until']}*\nAssets {_r(b['total_assets_cents'])} · "
                 f"Liabilities {_r(b['total_liabilities_cents'])} · Equity "
                 f"{_r(b['total_equity_cents'])}")
    lines.append("\n_From the books Vula keeps (cash basis) — your accountant files the returns._")
    return "\n".join(lines)


def render_pdf(business: str, d: Dict[str, Any]) -> bytes:
    from weasyprint import HTML
    e = html.escape

    def table(rows, total_label, total):
        body = "".join(f"<tr><td>{e(r['name'])}</td><td class=n>{_r(r['cents'])}</td></tr>" for r in rows)
        return f"<table>{body}<tr class=t><td>{e(total_label)}</td><td class=n>{_r(total)}</td></tr></table>"

    p, v, b, c = d["pnl"], d["vat"], d["balance"], d["cash"]
    cash_rows = [{"name": r["source"], "cents": r["net_cents"]} for r in c["by_source"]]
    doc = f"""<html><head><meta charset="utf-8"><style>
body{{font-family:sans-serif;font-size:11px;color:#222}} h1{{font-size:18px}} h2{{font-size:13px;
margin-top:18px;border-bottom:1px solid #ccc}} table{{width:100%;border-collapse:collapse}}
td{{padding:3px 0}} .n{{text-align:right}} .t td{{font-weight:bold;border-top:1px solid #999}}
</style></head><body><h1>{e(business)} — financial statements</h1>
<p>{e(d['since'])} to {e(d['until'])} · cash basis · prepared by Vula from the general ledger</p>
<h2>Profit &amp; loss</h2>{table(p['income'], 'Total income', p['total_income_cents'])}
{table(p['expenses'], 'Total expenses', p['total_expense_cents'])}
<table><tr class=t><td>Net profit</td><td class=n>{_r(p['net_profit_cents'])}</td></tr></table>
<h2>Cash flow</h2><table><tr><td>Opening bank</td><td class=n>{_r(c['opening_cents'])}</td></tr></table>
{table(cash_rows, 'Closing bank', c['closing_cents'])}
<h2>VAT</h2><table><tr><td>Output VAT (collected)</td><td class=n>{_r(v['output_vat_cents'])}</td></tr>
<tr><td>Input VAT (claimable)</td><td class=n>{_r(v['input_vat_cents'])}</td></tr>
<tr class=t><td>VAT payable</td><td class=n>{_r(v['vat_payable_cents'])}</td></tr></table>
<h2>Balance sheet at {e(d['until'])}</h2>{table(b['assets'], 'Total assets', b['total_assets_cents'])}
{table(b['liabilities'], 'Total liabilities', b['total_liabilities_cents'])}
{table(b['equity'] + [{'name': 'Retained earnings', 'cents': b['retained_earnings_cents']}],
       'Total equity', b['total_equity_cents'])}
</body></html>"""
    return HTML(string=doc).write_pdf()


def pdf_link(tenant_id: str, business: str, d: Dict[str, Any]) -> Optional[str]:
    """Render, store privately (documents bucket) and return a signed link that lasts a week."""
    try:
        from vula.api.whatsapp import _upload_to_storage
        from vula.storage_links import SHARE_TTL, signed
        data = render_pdf(business, d)
        url = _upload_to_storage("documents", f"{tenant_id}/reports/statements_{d['since']}_{d['until']}.pdf",
                                 data, "application/pdf")
        return signed(url, SHARE_TTL) if url else None
    except Exception as exc:
        log.warning("report PDF failed for %s: %s", tenant_id, exc)
        return None
