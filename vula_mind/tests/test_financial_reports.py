"""Financial statements from the ledger (finance brief, capability 6).

Every report is read from the same balanced journal, so they must agree: P&L net profit equals
income less expenses; the balance sheet balances (assets = liabilities + equity + retained
earnings); the cash flow's closing bank equals the balance sheet's bank; VAT payable is output
less input. Checked against a small real-shaped set of entries across two periods.
"""
from datetime import date
from unittest.mock import patch

import pytest

from vula.commerce import ledger, reports

TID = "off-the-hook"
TYPES = {"bank_cash": "asset", "vat_input": "asset", "vat_output": "liability",
         "accounts_payable": "liability", "sales": "income", "cost_of_sales": "expense",
         "other_expense": "expense", "owner_drawings": "equity"}
ENTRIES = [  # (date, [(account, debit, credit)])
    ("2026-08-01", [("bank_cash", 230000, 0), ("sales", 0, 200000), ("vat_output", 0, 30000)]),
    ("2026-09-10", [("bank_cash", 115000, 0), ("sales", 0, 100000), ("vat_output", 0, 15000)]),
    ("2026-09-15", [("bank_cash", 0, 45000), ("cost_of_sales", 39130, 0), ("vat_input", 5870, 0)]),
    ("2026-09-20", [("bank_cash", 0, 50000), ("owner_drawings", 50000, 0)]),
    ("2026-09-28", [("bank_cash", 0, 23000), ("sales", 20000, 0), ("vat_output", 3000, 0)]),
]


def fake_tb(tenant_id, since=None, until=None):
    rows = {}
    for d, lines in ENTRIES:
        if (since and d < since) or (until and d > until):
            continue
        for code, dr, cr in lines:
            r = rows.setdefault(code, {"code": code, "name": code.replace("_", " ").title(),
                                       "type": TYPES[code], "debit_cents": 0, "credit_cents": 0})
            r["debit_cents"] += dr
            r["credit_cents"] += cr
    return {"accounts": list(rows.values())}


@pytest.fixture(autouse=True)
def _ledger():
    with patch.object(ledger, "trial_balance", side_effect=fake_tb), \
            patch.object(reports, "_cash_by_source", return_value=[]):
        yield


def test_profit_and_loss_for_september():
    p = reports.profit_and_loss(TID, "2026-09-01", "2026-09-30")
    assert p["total_income_cents"] == 80000                     # 100 000 − 20 000 refund
    assert p["total_expense_cents"] == 39130
    assert p["net_profit_cents"] == 40870


def test_vat_payable_is_output_less_input():
    v = reports.vat_summary(TID, "2026-09-01", "2026-09-30")
    assert (v["output_vat_cents"], v["input_vat_cents"], v["vat_payable_cents"]) == (12000, 5870, 6130)


def test_the_balance_sheet_balances_and_carries_all_profit_to_date():
    b = reports.balance_sheet(TID, "2026-09-30")
    assert b["balanced"]
    assert b["retained_earnings_cents"] == 200000 + 40870         # August + September profit
    assert b["total_assets_cents"] == 227000 + 5870               # bank + VAT claimable


def test_cash_flow_closes_on_the_balance_sheet_bank():
    c = reports.cash_flow(TID, "2026-09-01", "2026-09-30")
    assert c["opening_cents"] == 230000
    assert (c["in_cents"], c["out_cents"]) == (115000, 118000)
    bank = next(a for a in reports.balance_sheet(TID, "2026-09-30")["assets"] if a["code"] == "bank_cash")
    assert c["closing_cents"] == bank["cents"] == 227000


def test_every_period_reconciles_with_the_trial_balance():
    for since, until in (("2026-08-01", "2026-08-31"), ("2026-09-01", "2026-09-30"), (None, "2026-09-30")):
        tb = fake_tb(TID, since, until)["accounts"]
        assert sum(a["debit_cents"] for a in tb) == sum(a["credit_cents"] for a in tb)
        p = reports.profit_and_loss(TID, since, until)
        inc = sum(a["credit_cents"] - a["debit_cents"] for a in tb if a["type"] == "income")
        exp = sum(a["debit_cents"] - a["credit_cents"] for a in tb if a["type"] == "expense")
        assert p["net_profit_cents"] == inc - exp


def test_the_summary_quotes_the_computed_figures():
    text = reports.summary_text(reports.build_all(TID, "2026-09-01", "2026-09-30"))
    assert "Net profit R408.70" in text and "Payable R61.30" in text
    assert "R2,300.00 → R2,270.00" in text


def test_the_pdf_renders_with_the_same_figures():
    import fitz
    data = reports.render_pdf("Off <the> Hook", reports.build_all(TID, "2026-09-01", "2026-09-30"))
    text = "\n".join(pg.get_text() for pg in fitz.open(stream=data, filetype="pdf"))
    assert "Off <the> Hook" in text and "R408.70" in text and "R61.30" in text


@pytest.mark.parametrize("period,expected", [
    ("this_month", ("2026-10-01", "2026-10-06")), ("last_month", ("2026-09-01", "2026-09-30")),
    ("tax_year", ("2026-03-01", "2026-10-06")), ("last_tax_year", ("2025-03-01", "2026-02-28")),
])
def test_periods_are_computed_in_code(period, expected):
    assert reports.period_dates(period, date(2026, 10, 6)) == expected


@pytest.mark.asyncio
async def test_the_whatsapp_tool_returns_the_report_as_is(monkeypatch):
    from core.skills.commerce_admin import CommerceAdminSkill
    monkeypatch.setattr(reports, "pdf_link", lambda *a: "https://signed/statements.pdf")
    monkeypatch.setattr("vula.api.tenants.display_name", lambda t: "Off the Hook")
    out = await CommerceAdminSkill()._financial_report(TID, {"since": "2026-09-01", "until": "2026-09-30"})
    assert "Net profit R408.70" in out["reply_verbatim"]
    assert out["reply_verbatim"].endswith("https://signed/statements.pdf") and out["balanced"]
