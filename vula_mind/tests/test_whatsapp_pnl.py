"""Gap audit 3.1: "send me this month's P&L" on WhatsApp returns the statements with a PDF link,
for people allowed to see the money, and not for staff limited to other areas."""
import pytest

import core.skills.commerce_admin as ca
from core.skills.commerce_admin import CommerceAdminSkill


def _names(tools):
    return {t["function"]["name"] for t in tools}


def test_the_owner_is_offered_the_report_for_a_pnl_request(monkeypatch):
    monkeypatch.setattr("vula.api.tenants.enabled_modules", lambda tid: ["invoices"])
    assert "financial_report" in _names(ca._tools_for("acme", "owner", "send me this month's P&L"))


def test_staff_limited_to_projects_never_get_the_statements():
    tools = ca._restrict_to_access(list(ca._ALL_TOOL_SPECS), ["projects"])
    assert not {"financial_report", "finance_settings", "send_payment_reminders"} & _names(tools)
    money = ca._restrict_to_access(list(ca._ALL_TOOL_SPECS), ["finances"])
    assert "financial_report" in _names(money)


@pytest.mark.asyncio
async def test_the_reply_carries_the_figures_and_the_pdf_link(monkeypatch):
    from vula.commerce import reports
    asked = {}

    def build_all(tid, since, until):
        asked.update(since=since, until=until)
        return {"pnl": {"net_profit_cents": 1234500}, "balance": {"balanced": True}}

    monkeypatch.setattr(reports, "period_dates", lambda p: ("2026-10-01", "2026-10-31"))
    monkeypatch.setattr(reports, "build_all", build_all)
    monkeypatch.setattr(reports, "summary_text", lambda d: "📊 October: net profit R12,345.00")
    monkeypatch.setattr(reports, "pdf_link", lambda tid, name, d: "https://example.test/r/abc.pdf")
    monkeypatch.setattr("vula.api.tenants.display_name", lambda tid: "Acme")
    out = await CommerceAdminSkill()._financial_report("acme", {"period": "this_month"})
    assert asked == {"since": "2026-10-01", "until": "2026-10-31"}
    assert "R12,345.00" in out["reply_verbatim"]
    assert "https://example.test/r/abc.pdf" in out["reply_verbatim"]   # raw URL: WhatsApp has no markdown links
    assert "](" not in out["reply_verbatim"]


@pytest.mark.asyncio
async def test_no_pdf_is_said_plainly(monkeypatch):
    from vula.commerce import reports
    monkeypatch.setattr(reports, "build_all", lambda *a: {"pnl": {"net_profit_cents": 0}, "balance": {"balanced": True}})
    monkeypatch.setattr(reports, "summary_text", lambda d: "📊 Nothing booked yet")
    monkeypatch.setattr(reports, "pdf_link", lambda *a: None)
    monkeypatch.setattr("vula.api.tenants.display_name", lambda tid: "Acme")
    out = await CommerceAdminSkill()._financial_report("acme", {})
    assert "couldn't be made" in out["reply_verbatim"]
