"""
vula/commerce/tax.py — tax for every tenant, worked in code from dated SARS tables.

2026-10-06 (Ian, DIGG): "HPC project profit and also after tax" got "I do not have the tools to
calculate the profit after tax", and then: Vula "should be able to help tenants increase income,
manage tax and increase profits". Vula had general tax knowledge (training/business_content.py)
but nothing that applied the rates to the business's own figures, no VAT period or due date, no
provisional tax and no deadline reminders.

Every figure here comes from the bank lines (the same source as job costing) and a table below —
never from a model — and every answer is labelled an estimate: Vula sees the bank, not the whole
tax return.

How the business is taxed is the owner's answer in the business profile ("tax_regime"):
Turnover Tax, a (Pty) Ltd company (optionally a small business corporation), or a sole
proprietor. VAT registration is commerce_invoice_settings.vat_registered; the VAT period
category (A/B/C) is a profile answer ("vat_category"). Until something is known, the tool asks.

Tables — TABLES_AS_OF. Update every February budget (the year is the one ending in February;
companies are assumed to have a February year-end). Checked against published Budget 2026
summaries; each figure is pinned by tests/test_tax_estimate.py.
"""
from __future__ import annotations

import calendar
import logging
import re
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

TABLES_AS_OF = "Budget 2026 (25 Feb 2026)"

Band = Tuple[int, int, float]         # (from_cents, base_cents, rate): base + rate × (x − from)

# Turnover Tax: (qualifying-turnover limit, bands).
TURNOVER_TAX: Dict[int, Tuple[int, List[Band]]] = {
    2027: (230_000_000, [(0, 0, 0.0), (60_000_000, 0, 0.01), (100_000_000, 400_000, 0.02),
                         (150_000_000, 1_400_000, 0.03)]),
    2026: (100_000_000, [(0, 0, 0.0), (33_500_000, 0, 0.01), (50_000_000, 165_000, 0.02),
                         (75_000_000, 665_000, 0.03)]),
}
COMPANY_RATE = 0.27
# Small business corporations (qualifying (Pty) Ltd / CC — the owner says so).
SBC: Dict[int, List[Band]] = {
    2027: [(0, 0, 0.0), (9_900_000, 0, 0.07), (36_500_000, 1_862_000, 0.21),
           (55_000_000, 5_747_000, 0.27)],
}
# Individuals (sole proprietors): brackets, primary rebate (under 65).
PERSONAL: Dict[int, Tuple[List[Band], int]] = {
    2027: ([(0, 0, 0.18), (24_510_000, 4_411_800, 0.26), (38_310_000, 7_999_800, 0.31),
            (53_020_000, 12_559_900, 0.36), (69_580_000, 18_521_500, 0.39),
            (88_700_000, 25_978_300, 0.41), (187_860_000, 66_633_900, 0.45)], 1_782_000),
}
VAT_RATE = 0.15
# (from, compulsory registration threshold, voluntary minimum) — 12-month taxable supplies.
VAT_THRESHOLDS: List[Tuple[date, int, int]] = [
    (date(2026, 4, 1), 230_000_000, 12_000_000),
    (date(2010, 3, 1), 100_000_000, 5_000_000),
]

NOTE = ("This is an estimate from the bank lines Vula has — confirm with your accountant before "
        "paying SARS.")
_NAMES = {"turnover": "Turnover Tax", "company": "a (Pty) Ltd company",
          "sbc": "a small business corporation", "sole_prop": "a sole proprietor"}


def _r(cents: Optional[int]) -> str:
    return "—" if cents is None else f"R{cents / 100:,.2f}"


def _bands(amount_cents: int, bands: List[Band]) -> int:
    if amount_cents <= 0:
        return 0
    start, base, rate = next(b for b in reversed(bands) if amount_cents > b[0] or b[0] == 0)
    return base + int(round((amount_cents - start) * rate))


# ── dates ─────────────────────────────────────────────────────────────────────

def tax_year(d: Optional[date] = None) -> Tuple[int, date, date]:
    """(year it ends in, 1 March start, last day of February end)."""
    d = d or date.today()
    end_year = d.year + 1 if d.month >= 3 else d.year
    return end_year, date(end_year - 1, 3, 1), date(end_year, 3, 1) - timedelta(days=1)


def _month_end(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def last_business_day(d: date) -> date:
    """d, or the Friday before it if it's a weekend (public holidays aren't modelled)."""
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _table_year(year: int, tables: Dict[int, Any]) -> int:
    """The table for `year`, else the latest one we have (said in the reply)."""
    return year if year in tables else max(tables)


# ── what's known about the business ───────────────────────────────────────────

def _answers(tenant_id: str) -> Dict[str, str]:
    try:
        from vula.commerce.business_profile import get_answers
        return get_answers(tenant_id)
    except Exception:
        return {}


def regime(tenant_id: str) -> Optional[str]:
    """'turnover', 'sbc', 'company' or 'sole_prop' from the owner's answer; None if not known."""
    text = (_answers(tenant_id).get("tax_regime") or "").lower()
    if re.search(r"turnover", text):
        return "turnover"
    if re.search(r"small business corp|\bsbc\b", text):
        return "sbc"
    if re.search(r"\(?pty\)?|company|ltd|close corporation|\bcc\b", text):
        return "company"
    if re.search(r"sole|own name|personal|individual|freelance", text):
        return "sole_prop"
    return None


def vat_status(tenant_id: str) -> Optional[bool]:
    """True/False from the invoice settings; None when the business never said."""
    try:
        from vula.commerce.job_costing import _client
        rows = (_client().table("commerce_invoice_settings").select("vat_registered")
                .eq("tenant_id", tenant_id).limit(1).execute().data or [])
    except Exception:
        return None
    v = rows[0].get("vat_registered") if rows else None
    return None if v is None else bool(v)


def vat_category(tenant_id: str) -> Optional[str]:
    m = re.search(r"\b([abc])\b", (_answers(tenant_id).get("vat_category") or "").lower())
    return m.group(1).upper() if m else None


# ── the business's year, from the bank ────────────────────────────────────────

def year_figures(tenant_id: str, start: date, end: date) -> Dict[str, Any]:
    """Income and deductible expenses from the bank lines between start and end, with the dates
    the statements cover. Drawings, transfers and VAT accounts aren't income or expenses."""
    from vula.commerce.accounting import _txns, ensure_chart
    try:
        chart = {a["code"]: a for a in ensure_chart(tenant_id)}
    except Exception:
        chart = {}
    rows = _txns(tenant_id, start.isoformat(), end.isoformat())
    income = expense = uncat_in = uncat_out = 0
    for r in rows:
        amt = int(r.get("amount_cents") or 0)
        acc = chart.get(r.get("account_code") or "")
        if acc is None:
            if r.get("direction") == "in":
                uncat_in += amt
            else:
                uncat_out += amt
        elif acc.get("type") == "income":
            income += amt
        elif acc.get("type") == "expense" and acc.get("deductible", True):
            expense += amt
    dates = sorted(str(r["txn_date"])[:10] for r in rows if r.get("txn_date"))
    first = date.fromisoformat(dates[0]) if dates else None
    last = date.fromisoformat(dates[-1]) if dates else None
    months = round(((last - first).days + 1) / 30.44, 1) if first else 0.0
    return {"income_cents": income, "expense_cents": expense, "profit_cents": income - expense,
            "uncategorised_in_cents": uncat_in, "uncategorised_out_cents": uncat_out,
            "first": first, "last": last, "months": max(months, 0.0)}


def turnover_so_far(tenant_id: str, start: date, end: date) -> Dict[str, Any]:
    f = year_figures(tenant_id, start, end)
    return {"turnover_cents": f["income_cents"],
            "first": f["first"].isoformat() if f["first"] else None,
            "last": f["last"].isoformat() if f["last"] else None, "months": f["months"]}


def _annualise(cents: int, months: float) -> int:
    return int(round(cents * 12 / months)) if months and months < 12 else cents


def _cover(first: Optional[date], last: Optional[date], months: float) -> str:
    if not first:
        return " (no bank statements on file for this tax year)"
    rough = " — under 3 months, so a rough guide" if months < 3 else ""
    return f" (bank statements {first.isoformat()} to {last.isoformat()}, {months:g} months{rough})"


# ── the taxes ─────────────────────────────────────────────────────────────────

def turnover_tax_cents(turnover_cents: int, year: int) -> Optional[int]:
    """Turnover Tax on a year's qualifying turnover; None over the limit (the business no longer
    qualifies) or when there's no table for that year."""
    table = TURNOVER_TAX.get(year)
    if not table:
        return None
    limit, bands = table
    if turnover_cents > limit:
        return None
    return _bands(turnover_cents, bands)


def company_tax_cents(profit_cents: int, year: int, sbc: bool = False) -> int:
    if sbc:
        return _bands(profit_cents, SBC[_table_year(year, SBC)])
    return int(round(max(profit_cents, 0) * COMPANY_RATE))


def personal_tax_cents(income_cents: int, year: int) -> int:
    bands, rebate = PERSONAL[_table_year(year, PERSONAL)]
    return max(0, _bands(income_cents, bands) - rebate)


def _marginal_rate(income_cents: int, bands: List[Band]) -> float:
    return next(b for b in reversed(bands) if income_cents > b[0] or b[0] == 0)[2]


def vat_threshold(d: Optional[date] = None) -> Tuple[int, int]:
    """(compulsory, voluntary) registration thresholds in force on d."""
    d = d or date.today()
    _, comp, vol = next(t for t in VAT_THRESHOLDS if d >= t[0])
    return comp, vol


def provisional(annual_tax_cents: int, year: int) -> List[Dict[str, Any]]:
    """The two provisional payments for a February year-end: half the year's estimate by the
    end of August, the rest by the end of February."""
    first = annual_tax_cents // 2
    return [{"due": last_business_day(_month_end(year - 1, 8)), "cents": first, "label": "1st"},
            {"due": last_business_day(_month_end(year, 2)), "cents": annual_tax_cents - first,
             "label": "2nd"}]


def estimate(tenant_id: str, profit_cents: Optional[int] = None,
             project_received_cents: Optional[int] = None, label: str = "",
             today: Optional[date] = None) -> Dict[str, Any]:
    """This tax year's tax under the business's regime, with the working; with a project's profit
    and received, also that project's share and its profit after tax."""
    kind = regime(tenant_id)
    if kind is None:
        return {"status": "need_info",
                "message": "To work out tax I need to know how the business is taxed: Turnover "
                           "Tax, a (Pty) Ltd company, or a sole proprietor (your own name)? "
                           "Tell me once and I'll remember."}
    year, start, end = tax_year(today)
    f = year_figures(tenant_id, start, min(end, today or date.today()))
    cover = _cover(f["first"], f["last"], f["months"])
    lines: List[str] = []
    out: Dict[str, Any] = {"regime": kind, "tax_year_end": end.isoformat(), "figures": {
        k: (v.isoformat() if isinstance(v, date) else v) for k, v in f.items()}}
    ty = f"{start.year}/{str(year)[2:]}"

    if kind == "turnover":
        turnover = f["income_cents"]
        limit = (TURNOVER_TAX.get(year) or (None, None))[0]
        out.update(turnover_cents=turnover, limit_cents=limit)
        lines.append(f"Turnover Tax, tax year {ty}: money in so far {_r(turnover)}{cover}.")
        if limit is None:
            lines.append("I don't have this year's Turnover Tax table yet.")
        elif turnover > limit:
            out["over_limit"] = True
            lines.append(f"That's over the {_r(limit)} Turnover Tax limit, so the business may no "
                         "longer qualify for Turnover Tax and would be taxed as a company or sole "
                         "proprietor — and VAT registration becomes compulsory over the same "
                         "amount. Some money in may not count as turnover (e.g. client money "
                         "passed straight to suppliers) — ask your accountant to check this now.")
        else:
            tax = turnover_tax_cents(turnover, year) or 0
            yearly = _annualise(turnover, f["months"])
            out["tax_cents"] = tax
            lines.append(f"Turnover Tax on {_r(turnover)}: {_r(tax)}.")
            if yearly > turnover:
                yearly_tax = turnover_tax_cents(yearly, year)
                lines.append(f"At this pace the year comes to about {_r(yearly)}"
                             + (f" — Turnover Tax about {_r(yearly_tax)}." if yearly_tax is not None
                                else f", over the {_r(limit)} limit."))
            if project_received_cents and turnover:
                share = int(round(tax * project_received_cents / turnover))
                out["project_tax_cents"] = share
                lines.append(f"{label or 'The project'}'s share ({_r(project_received_cents)} of "
                             f"the turnover): {_r(share)}.")
                if profit_cents is not None:
                    out["profit_after_tax_cents"] = profit_cents - share
                    lines.append(f"Profit {_r(profit_cents)} − {_r(share)} = "
                                 f"{_r(profit_cents - share)} after Turnover Tax.")
        lines.append("Turnover Tax is on money in, not profit; interim payments are due at the "
                     "end of August and the end of February.")
    else:
        profit_ytd = f["profit_cents"]
        yearly = _annualise(profit_ytd, f["months"])
        lines.append(f"Tax year {ty}{cover}: income {_r(f['income_cents'])} − deductible costs "
                     f"{_r(f['expense_cents'])} = profit {_r(profit_ytd)} so far"
                     + (f"; about {_r(yearly)} for the full year at this pace." if yearly != profit_ytd
                        else "."))
        if kind == "sole_prop":
            annual_tax = personal_tax_cents(yearly, year)
            bands, rebate = PERSONAL[_table_year(year, PERSONAL)]
            rate = _marginal_rate(yearly, bands)
            lines.append(f"Personal income tax on {_r(yearly)} (SARS tables, less the "
                         f"{_r(rebate)} primary rebate): about {_r(annual_tax)}. Other income you "
                         "have isn't included.")
        else:
            sbc = kind == "sbc"
            annual_tax = company_tax_cents(yearly, year, sbc=sbc)
            rate = (_marginal_rate(yearly, SBC[_table_year(year, SBC)]) if sbc else COMPANY_RATE)
            lines.append(f"{'Small business corporation rates' if sbc else 'Company tax at 27%'} on "
                         f"{_r(yearly)}: about {_r(annual_tax)}"
                         + ("." if sbc else f" ({_r(yearly)} × 27%).")
                         + ("" if sbc else " If the company qualifies as a small business "
                            "corporation, the first R99,000 is tax-free and the rate is lower up "
                            "to R550,000 — tell me and I'll use those rates."))
        out.update(tax_cents=annual_tax, annual_profit_cents=yearly)
        pays = provisional(annual_tax, year)
        out["provisional"] = [{**p, "due": p["due"].isoformat()} for p in pays]
        lines.append("Provisional tax: " + "; ".join(
            f"{p['label']} payment {_r(p['cents'])} by {p['due'].isoformat()}" for p in pays) + ".")
        if profit_cents is not None:
            share = int(round(max(profit_cents, 0) * rate))
            out.update(project_tax_cents=share, profit_after_tax_cents=profit_cents - share)
            lines.append(f"{label or 'That profit'}: {_r(profit_cents)} × {rate * 100:g}% = "
                         f"{_r(share)} tax → {_r(profit_cents - share)} after tax.")
        if f["uncategorised_out_cents"]:
            lines.append(f"{_r(f['uncategorised_out_cents'])} of payments aren't categorised yet, "
                         "so they aren't counted as costs — categorising them can lower the tax.")
    if year not in PERSONAL:
        lines.append(f"(Using the latest tables I have: {TABLES_AS_OF}.)")
    lines.append(NOTE)
    out["text"] = "\n".join(lines)
    return out


def project_tax(tenant_id: str, project: Optional[str] = None) -> Dict[str, Any]:
    """The agent's tool: tax on the year so far, and — with a project — that project's profit
    after tax."""
    profit = received = None
    label = ""
    if project:
        from vula.commerce.job_costing import costing, find_project
        p = find_project(costing(tenant_id), project)
        if p:
            profit, received, label = p["profit_cents"], p["received_cents"], p["project"]
    return estimate(tenant_id, profit, received, label)


# ── VAT ───────────────────────────────────────────────────────────────────────

_PERIOD_ENDS = {"A": (1, 3, 5, 7, 9, 11), "B": (2, 4, 6, 8, 10, 12), "C": tuple(range(1, 13))}


def vat_periods(category: str, today: date) -> Tuple[Tuple[date, date], Tuple[date, date]]:
    """(current period, last completed period) as (start, end) for category A/B (two-monthly)
    or C (monthly)."""
    ends = _PERIOD_ENDS[category]
    length = 1 if category == "C" else 2

    def period_ending(y: int, m: int) -> Tuple[date, date]:
        sm, sy = m - length + 1, y
        if sm <= 0:
            sm, sy = sm + 12, y - 1
        return date(sy, sm, 1), _month_end(y, m)
    y, m = today.year, today.month
    while m not in ends:
        m += 1
        if m > 12:
            m, y = 1, y + 1
    current = period_ending(y, m)
    prev_end = current[0] - timedelta(days=1)
    return current, period_ending(prev_end.year, prev_end.month)


def vat_due(period_end: date) -> date:
    """VAT201 by eFiling: the last business day of the month after the period."""
    nxt = period_end + timedelta(days=1)
    return last_business_day(_month_end(nxt.year, nxt.month))


def vat_position(tenant_id: str, today: Optional[date] = None) -> Dict[str, Any]:
    from vula.commerce.cross_check import vat
    today = today or date.today()
    registered = vat_status(tenant_id)
    if registered is None:
        return {"status": "need_info",
                "message": "Is the business registered for VAT? If it is, send me the VAT number "
                           "and whether you submit every two months (category A or B) or "
                           "monthly — I'll remember it."}
    lines: List[str] = []
    out: Dict[str, Any] = {"vat_registered": registered}
    if registered:
        cat = vat_category(tenant_id)
        assumed = cat is None
        cat = cat or "B"
        current, last = vat_periods(cat, today)
        for key, (s, e) in (("last_period", last), ("current_period", current)):
            v = vat(tenant_id, s.isoformat(), min(e, today).isoformat())
            out[key] = {"start": s.isoformat(), "end": e.isoformat(), "due": vat_due(e).isoformat(),
                        "output_cents": v["output_cents"], "input_cents": v["input_claimable_cents"],
                        "net_cents": v["net_cents"],
                        "missing_number_cents": v["input_no_vat_number_cents"]}
        for key, title in (("last_period", "Last VAT period"), ("current_period", "This period so far")):
            p = out[key]
            net = p["net_cents"]
            lines.append(f"{title} ({p['start']} – {p['end']}, VAT201 due {p['due']}): VAT on "
                         f"sales {_r(p['output_cents'])} − VAT you can claim {_r(p['input_cents'])} "
                         f"= {_r(abs(net))} {'to pay' if net >= 0 else 'back from SARS'}.")
        missing = out["last_period"]["missing_number_cents"] + out["current_period"]["missing_number_cents"]
        if missing:
            lines.append(f"{_r(missing)} of VAT is on suppliers' bills without their VAT number on "
                         "file — add the numbers (they're on the tax invoices) and you can claim it.")
        if assumed:
            lines.append("I've assumed category B (periods ending Feb, Apr, Jun, Aug, Oct, Dec) — "
                         "tell me if you're on A or monthly.")
    else:
        v = vat(tenant_id)
        comp, vol = vat_threshold(today)
        sales = v["sales_12m_cents"]
        out.update(sales_12m_cents=sales, compulsory_cents=comp, voluntary_cents=vol,
                   could_claim_cents=v["input_claimable_cents"])
        lines.append(f"Not VAT registered. Sales in the last 12 months: {_r(sales)} (from the bank "
                     f"lines on file); registration is compulsory over {_r(comp)} and you may "
                     f"register voluntarily from {_r(vol)}.")
        if sales > comp:
            out["over_threshold"] = True
            lines.append("That's over the compulsory threshold — the business must register for "
                         "VAT (within 21 days of passing it). Speak to your accountant now.")
        else:
            months = (date.fromisoformat(v["months"][-1]["month"] + "-01") -
                      date.fromisoformat(v["months"][0]["month"] + "-01")).days / 30.44 + 1 \
                if v.get("months") else 0
            if months and sales:
                pace = sales / months
                left = (comp - sales) / pace if pace else None
                if left is not None and left <= 12:
                    lines.append(f"At about {_r(int(pace))} a month you'd pass it in roughly "
                                 f"{max(1, round(left))} month{'s' if round(left) != 1 else ''}.")
        lines.append(f"If registered, you could have claimed {_r(v['input_claimable_cents'])} of "
                     "VAT on suppliers' tax invoices in the last 12 months (and you'd charge 15% "
                     "on sales). Whether that's worth it depends on whether your customers can "
                     "claim VAT back.")
    lines.append(NOTE)
    out["text"] = "\n".join(lines)
    return out


# ── the calendar ──────────────────────────────────────────────────────────────

def _pays_workers(tenant_id: str) -> bool:
    try:
        from vula.commerce.job_costing import _client
        return bool(_client().table("commerce_workers").select("id").eq("tenant_id", tenant_id)
                    .limit(1).execute().data)
    except Exception:
        return False


def deadlines(tenant_id: str, today: Optional[date] = None, days: int = 60) -> List[Dict[str, Any]]:
    """Tax deadlines for this business in the next `days` days, soonest first."""
    today = today or date.today()
    horizon = today + timedelta(days=days)
    out: List[Dict[str, Any]] = []
    kind = regime(tenant_id)
    if vat_status(tenant_id):
        cat = vat_category(tenant_id) or "B"
        ends = _PERIOD_ENDS[cat]
        for k in range(-2, 4):                      # period ends around today
            y, m = today.year, today.month + k
            while m <= 0:
                m, y = m + 12, y - 1
            while m > 12:
                m, y = m - 12, y + 1
            if m in ends:
                period_end = _month_end(y, m)
                out.append({"due": vat_due(period_end),
                            "what": f"VAT201 for the period ending {period_end.isoformat()}",
                            "kind": "vat"})
    if kind in ("company", "sbc", "sole_prop", "turnover"):
        for yr in (tax_year(today)[0], tax_year(today)[0] + 1):
            for d, which in ((last_business_day(_month_end(yr - 1, 8)), "1st"),
                             (last_business_day(_month_end(yr, 2)), "2nd")):
                what = (f"Turnover Tax {which} interim payment" if kind == "turnover"
                        else f"Provisional tax (IRP6) {which} payment")
                out.append({"due": d, "what": what, "kind": "provisional"})
    if _pays_workers(tenant_id):
        for k in range(0, 3):
            y, m = today.year, today.month + k
            if m > 12:
                m, y = m - 12, y + 1
            out.append({"due": last_business_day(date(y, m, 7)),
                        "what": "EMP201 (PAYE/UIF/SDL) — if you're registered as an employer",
                        "kind": "payroll"})
    seen, result = set(), []
    for d in sorted(out, key=lambda x: x["due"]):
        key = (d["due"], d["what"])
        if today <= d["due"] <= horizon and key not in seen:
            seen.add(key)
            result.append(d)
    return result


def calendar_text(tenant_id: str, today: Optional[date] = None) -> Dict[str, Any]:
    today = today or date.today()
    items = deadlines(tenant_id, today)
    lines = [f"• {d['due'].isoformat()} — {d['what']}" for d in items]
    head = "Tax dates in the next 60 days:" if items else "No tax deadlines in the next 60 days."
    extra = []
    if regime(tenant_id) is None:
        extra.append("Tell me how the business is taxed (Turnover Tax, company or sole "
                     "proprietor) and I'll add the income-tax dates.")
    if vat_status(tenant_id) is None:
        extra.append("Tell me whether you're VAT registered and I'll add the VAT dates.")
    extra.append("Dates falling on a public holiday move to the business day before.")
    return {"deadlines": [{**d, "due": d["due"].isoformat()} for d in items],
            "text": "\n".join([head, *lines, *extra])}


def digest_lines(tenant_id: str, today: date) -> List[str]:
    """For the morning digest: a deadline 7 days out and 2 days out (moved to the weekday before
    when that falls on a weekend) — twice per deadline, never every morning."""
    out = []
    for d in deadlines(tenant_id, today, days=10):
        for lead in (7, 2):
            if last_business_day(d["due"] - timedelta(days=lead)) == today:
                out.append(f"• {d['what']} — due {d['due'].isoformat()}")
    return out
