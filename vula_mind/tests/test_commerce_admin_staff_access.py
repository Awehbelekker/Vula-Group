"""A restricted staff member's WhatsApp agent only gets tools for the dashboard scopes they
were granted (vula_team_members.access); owners keep everything; owners get reminders
(2026-09-25 review)."""
from unittest.mock import MagicMock, patch

import core.skills.commerce_admin as ca


def _names(tools):
    return {t["function"]["name"] for t in tools}


def _db(rows):
    q = MagicMock()
    q.select.return_value = q
    q.eq.return_value = q
    q.execute.return_value = MagicMock(data=rows)
    db = MagicMock()
    db.table.return_value = q
    return db


def test_restricted_staff_lose_finance_and_stock_tools():
    tools = ca._ALL_TOOL_SPECS
    out = _names(ca._restrict_to_access(tools, ["orders"]))
    assert "recent_orders" in out and "update_order_status" in out
    assert "finance_insights" not in out and "update_stock" not in out and "send_broadcast" not in out
    assert "draft_letter" in out or "lookup_business_info" in out   # general tools stay


def test_member_access_lookup():
    rows = [{"whatsapp": "082 111 2222", "role": "staff", "access": ["orders"]},
            {"whatsapp": "+27833334444", "role": "manager", "access": ["orders"]},
            {"whatsapp": "27845556666", "role": "staff", "access": []}]
    with patch.object(ca.service, "_client", return_value=_db(rows)):
        assert ca._member_access("t", "27821112222") == ["orders"]
        assert ca._member_access("t", "0833334444") is None      # manager -> full
        assert ca._member_access("t", "27845556666") is None     # empty list -> full
        assert ca._member_access("t", "27999999999") is None     # unknown -> unchanged


def test_owner_toolset_includes_reminders():
    with patch("vula.api.tenants.enabled_modules", return_value=[]):
        names = _names(ca._tools_for("t"))
    assert {t["function"]["name"] for t in ca.REMINDER_TOOLS} <= names
