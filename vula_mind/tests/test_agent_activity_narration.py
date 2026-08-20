"""Tests for the Agent Activity narration layer (vula/api/commerce.py::_narrate and the
/{tenant_id}/admin/agent-activity route) — turning raw tool-call telemetry into
business-readable text, with a graceful raw-args fallback for tools without a template.
"""
from unittest.mock import patch

import pytest

from vula.api import commerce as commerce_api


def test_narrate_update_stock_confirmed():
    text = commerce_api._narrate("update_stock", {"product": "Hake Fillets", "quantity": "20",
                                                    "confirm": "True"})
    assert text == "Confirmed: set Hake Fillets stock to 20"


def test_narrate_update_stock_preview_not_confirmed():
    text = commerce_api._narrate("update_stock", {"product": "Hake Fillets", "quantity": "20"})
    assert text.startswith("Previewed (not yet confirmed):")


def test_narrate_send_purchase_order():
    text = commerce_api._narrate("send_purchase_order",
                                  {"po_ref": "abc12345", "channel": "email", "confirm": "True"})
    assert "abc12345" in text and "email" in text


def test_narrate_record_payment_includes_amount():
    text = commerce_api._narrate(
        "record_payment", {"invoice_number": "OTH-INV-00001", "amount_rands": "500",
                            "confirm": "True"})
    assert "500" in text and "OTH-INV-00001" in text


def test_narrate_unknown_tool_returns_none():
    assert commerce_api._narrate("some_new_tool_no_template", {"x": "1"}) is None


def test_narrate_never_raises_on_malformed_args():
    # args is normally a dict, but the function must not crash the whole feed if it's not —
    # a malformed telemetry row shouldn't take down the rest of the activity feed; it degrades
    # to the tool's generic template with placeholder values rather than raising.
    assert commerce_api._narrate("update_stock", None) is not None
    assert commerce_api._narrate("update_stock", ["not", "a", "dict"]) is not None


def test_consequential_tools_set_matches_step2_gated_tools():
    for tool in ("update_stock", "create_purchase_order", "send_purchase_order",
                 "update_po_status", "create_discount_code", "update_discount_code",
                 "delete_discount_code", "cancel_booking", "delete_supplier", "record_payment"):
        assert tool in commerce_api._CONSEQUENTIAL_TOOLS
    assert "sales_summary" not in commerce_api._CONSEQUENTIAL_TOOLS


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def table(self, *a):
        return self

    def select(self, *a):
        return self

    def eq(self, *a):
        return self

    def in_(self, *a):
        return self

    def order(self, *a, **kw):
        return self

    def limit(self, *a):
        return self

    def execute(self):
        return type("R", (), {"data": self._rows})()


TID = "test-tenant"


@pytest.mark.asyncio
async def test_agent_activity_route_narrates_known_tool_and_flags_consequential():
    rows = [{"system": "vula-agent-tool", "task": "update_stock", "outcome": "admin",
             "reason": None, "escalated": False,
             "extra": {"args": {"product": "Hake", "quantity": "5", "confirm": "True"}},
             "created_at": "2026-08-20T00:00:00Z"}]
    with patch("vula.commerce.service._client", return_value=_FakeQuery(rows)):
        result = await commerce_api.admin_agent_activity(TID)
    ev = result["events"][0]
    assert ev["consequential"] is True
    assert ev["narrated"] == "Confirmed: set Hake stock to 5"


@pytest.mark.asyncio
async def test_agent_activity_route_falls_back_gracefully_for_unknown_tool():
    rows = [{"system": "vula-agent-tool", "task": "totally_new_tool", "outcome": "admin",
             "reason": None, "escalated": False,
             "extra": {"args": {"foo": "bar"}}, "created_at": "2026-08-20T00:00:00Z"}]
    with patch("vula.commerce.service._client", return_value=_FakeQuery(rows)):
        result = await commerce_api.admin_agent_activity(TID)
    ev = result["events"][0]
    assert ev["narrated"] is None
    assert ev["args"] == {"foo": "bar"}
    assert ev["consequential"] is False


@pytest.mark.asyncio
async def test_agent_activity_route_still_handles_router_events():
    rows = [{"system": "vula-llm-router", "task": "reasoning", "outcome": "answered",
             "reason": "local", "escalated": False, "extra": {"backend": "ollama"},
             "created_at": "2026-08-20T00:00:00Z"}]
    with patch("vula.commerce.service._client", return_value=_FakeQuery(rows)):
        result = await commerce_api.admin_agent_activity(TID)
    ev = result["events"][0]
    assert ev["kind"] == "route" and ev["backend"] == "ollama"
