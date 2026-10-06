"""
vula/commerce/tax.py — income-tax estimates, worked in code from dated SARS tables.

2026-10-06 (Ian, DIGG): "HPC project profit and also after tax" got "I do not have the tools to
calculate the profit after tax". Vula had general tax knowledge (training/business_content.py)
but nothing that applied the rates to the business's own figures. Here every figure comes from
the bank lines and a table below — never from a model — and every answer is labelled an
estimate: Vula sees the bank, not the whole tax return.

How the business is taxed lives in its business profile (vula_business_profile.answers
["tax_regime"], free text the owner gave): Turnover Tax, a (Pty) Ltd company, or a sole
proprietor. Until it's known the tool asks, rather than guessing.

Tables (update each February budget; the year is the one ending on the last day of February):
- Turnover Tax, year ending Feb 2027: qualifying turnover up to R2.3m (was R1m); 0% to R600k,
  1% to R1m, R4,000 + 2% to R1.5m, R14,000 + 3% to R2.3m (Budget 2026).
- Turnover Tax, year ending Feb 2026: up to R1m; 0% to R335k, 1% to R500k, R1,650 + 2% to R750k,
  R6,650 + 3% to R1m.
- Company tax: 27% of taxable income.
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# (limit_cents, [(from_cents, base_cents, rate)]) — tax = base + rate × (turnover − from).
TURNOVER_TAX: Dict[int, Tuple[int, List[Tuple[int, int, float]]]] = {
    2027: (230_000_000, [(0, 0, 0.0), (60_000_000, 0, 0.01), (100_000_000, 400_000, 0.02),
                         (150_000_000, 1_400_000, 0.03)]),
    2026: (100_000_000, [(0, 0, 0.0), (33_500_000, 0, 0.01), (50_000_000, 165_000, 0.02),
                         (75_000_000, 665_000, 0.03)]),
}
COMPANY_RATE = 0.27

NOTE = ("This is an estimate from the bank lines Vula has — confirm with your accountant before "
        "paying SARS.")


def _r(cents: Optional[int]) -> str:
    return "—" if cents is None else f"R{cents / 100:,.2f}"


def tax_year(d: Optional[date] = None) -> Tuple[int, date, date]:
    """(year it ends in, 1 March start, last day of February end)."""
    d = d or date.today()
    end_year = d.year + 1 if d.month >= 3 else d.year
    start = date(end_year - 1, 3, 1)
    end = date(end_year, 3, 1) - timedelta(days=1)
    return end_year, start, end


def regime(tenant_id: str) -> Optional[str]:
    """'turnover', 'company' or 'sole_prop' from the owner's answer, or None if not known."""
    try:
        from vula.commerce.business_profile import get_answers
        text = (get_answers(tenant_id).get("tax_regime") or "").lower()
    except Exception:
        text = ""
    if re.search(r"turnover", text):
        return "turnover"
    if re.search(r"\(?pty\)?|company|ltd|close corporation|\bcc\b", text):
        return "company"
    if re.search(r"sole|own name|personal|individual|freelance", text):
        return "sole_prop"
    return None


def turnover_tax_cents(turnover_cents: int, year: int) -> Optional[int]:
    """Turnover Tax on a year's qualifying turnover; None over the limit (the business no longer
    qualifies) or when there's no table for that year."""
    table = TURNOVER_TAX.get(year)
    if not table:
        return None
    limit, bands = table
    if turnover_cents > limit:
        return None
    base_from, base, rate = next(b for b in reversed(bands) if turnover_cents > b[0] or b[0] == 0)
    return base + int(round((turnover_cents - base_from) * rate))


def turnover_so_far(tenant_id: str, start: date, end: date) -> Dict[str, Any]:
    """Money in on income accounts this tax year, and which dates the statements cover."""
    from vula.commerce.job_costing import _client
    from vula.commerce.ledger import _all_pages
    try:
        from vula.commerce.accounting import ensure_chart
        income = {a["code"] for a in ensure_chart(tenant_id) if a.get("type") == "income"}
    except Exception:
        income = set()
    income = income or {"sales", "other_income"}

    def make():
        return (_client().table("commerce_bank_transactions")
                .select("txn_date,amount_cents,direction,account_code,match_status")
                .eq("tenant_id", tenant_id).gte("txn_date", start.isoformat())
                .lte("txn_date", end.isoformat()).order("txn_date"))
    try:
        rows = [r for r in _all_pages(make) if r.get("match_status") != "ignored"]
    except Exception as exc:
        log.debug("turnover read failed: %s", exc)
        rows = []
    ins = [r for r in rows if r.get("direction") == "in" and r.get("account_code") in income]
    dates = sorted(str(r["txn_date"])[:10] for r in rows if r.get("txn_date"))
    return {"turnover_cents": sum(int(r.get("amount_cents") or 0) for r in ins),
            "first": dates[0] if dates else None, "last": dates[-1] if dates else None}


def estimate(tenant_id: str, profit_cents: Optional[int] = None,
             project_received_cents: Optional[int] = None, label: str = "") -> Dict[str, Any]:
    """The tax on this year's figures under the business's regime, with the working; with a
    project's profit and received, also that project's share and its profit after tax."""
    kind = regime(tenant_id)
    if kind is None:
        return {"status": "need_info",
                "message": "To work out tax I need to know how the business is taxed: Turnover "
                           "Tax, a (Pty) Ltd company, or a sole proprietor (your own name)? "
                           "Tell me once and I'll remember."}
    year, start, end = tax_year()
    lines: List[str] = []
    out: Dict[str, Any] = {"regime": kind, "tax_year_end": end.isoformat()}

    if kind == "turnover":
        t = turnover_so_far(tenant_id, start, end)
        limit, _ = TURNOVER_TAX.get(year, (None, None))
        turnover = t["turnover_cents"]
        out.update(turnover_cents=turnover, limit_cents=limit)
        cover = f" (bank statements from {t['first']} to {t['last']})" if t["first"] else ""
        lines.append(f"Turnover Tax, year {start.year}/{str(year)[2:]}: money in so far "
                     f"{_r(turnover)}{cover}.")
        if limit is None:
            lines.append("I don't have this year's Turnover Tax table yet.")
        elif turnover > limit:
            out["over_limit"] = True
            lines.append(f"That's over the {_r(limit)} Turnover Tax limit, so the business may no "
                         "longer qualify for Turnover Tax and would be taxed as a normal company "
                         "or sole proprietor (and may need to register for VAT). Some money in "
                         "may not count as turnover (e.g. client money passed straight to "
                         "suppliers) — ask your accountant to check this now.")
        else:
            tax = turnover_tax_cents(turnover, year) or 0
            out["tax_cents"] = tax
            lines.append(f"Turnover Tax on {_r(turnover)}: {_r(tax)}.")
            if project_received_cents and turnover:
                share = int(round(tax * project_received_cents / turnover))
                out["project_tax_cents"] = share
                lines.append(f"{label or 'The project'}'s share ({_r(project_received_cents)} of "
                             f"the turnover): {_r(share)}.")
                if profit_cents is not None:
                    out["profit_after_tax_cents"] = profit_cents - share
                    lines.append(f"Profit {_r(profit_cents)} − {_r(share)} = "
                                 f"{_r(profit_cents - share)} after Turnover Tax.")
        lines.append("Turnover Tax is on money in, not profit, and replaces income tax.")
    elif kind == "company":
        if profit_cents is None:
            lines.append("Company tax is 27% of the year's taxable profit — tell me which profit "
                         "figure to use (a project, or the year so far).")
        else:
            tax = int(round(profit_cents * COMPANY_RATE)) if profit_cents > 0 else 0
            out.update(tax_cents=tax, profit_after_tax_cents=profit_cents - tax)
            lines.append(f"Company tax at 27%: {_r(profit_cents)} × 27% = {_r(tax)}, so "
                         f"{_r(profit_cents - tax)} after tax.")
            lines.append("Tax is on the whole year's taxable profit; a single project's figure "
                         "is a guide.")
    else:
        lines.append("As a sole proprietor, tax is on your total income for the year at the "
                     "personal rates, after rebates — one project's profit can't be taxed on its "
                     "own. Your accountant (or SARS eFiling's calculator) works it out from the "
                     "year's figures.")
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
