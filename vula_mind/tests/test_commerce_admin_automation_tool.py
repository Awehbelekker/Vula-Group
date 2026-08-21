"""Tests for commerce_admin.py's create_automation_rule tool — the WhatsApp/dashboard-chat
reachable entry point for teaching-mode rule authoring. Mirrors the confirm=true two-layer
gate's own test style: a preview call must never touch the LLM/DB, only a confirmed call does.
"""
from unittest.mock import AsyncMock, patch

import pytest

from core.skills.commerce_admin import CommerceAdminSkill

TID = "test-tenant"


@pytest.fixture
def skill():
    return CommerceAdminSkill()


@pytest.mark.asyncio
async def test_create_automation_rule_without_confirm_returns_preview_and_never_calls_llm(skill):
    with patch("vula.commerce.automations.parse_rule_from_text", new=AsyncMock()) as mock_parse:
        result = await skill._create_automation_rule(
            TID, "when an order is dispatched, message the customer", confirm=False)
    assert result.get("preview") is True
    mock_parse.assert_not_called()


@pytest.mark.asyncio
async def test_create_automation_rule_confirmed_calls_parser_and_reports_created(skill):
    with patch("vula.commerce.automations.parse_rule_from_text",
               new=AsyncMock(return_value={"name": "Dispatch notice", "trigger_type": "order_status",
                                            "action_type": "whatsapp_customer"})) as mock_parse:
        result = await skill._create_automation_rule(
            TID, "when an order is dispatched, message the customer", confirm=True)
    mock_parse.assert_awaited_once_with(TID, "when an order is dispatched, message the customer")
    assert result.get("created") is True
    assert result.get("automation") == "Dispatch notice"


@pytest.mark.asyncio
async def test_create_automation_rule_confirmed_propagates_parser_error(skill):
    with patch("vula.commerce.automations.parse_rule_from_text",
               new=AsyncMock(return_value={"error": "I can only automate on: order_status, low_stock."})):
        result = await skill._create_automation_rule(TID, "email me every hour", confirm=True)
    assert "error" in result
    assert result.get("created") is None


@pytest.mark.asyncio
async def test_create_automation_rule_empty_description_is_an_error(skill):
    result = await skill._create_automation_rule(TID, "   ", confirm=True)
    assert "error" in result


def test_create_automation_rule_excluded_from_sales_rep_toolset():
    from core.skills.commerce_admin import _REP_TOOL_SPECS
    names = {t["function"]["name"] for t in _REP_TOOL_SPECS}
    assert "create_automation_rule" not in names


def test_create_automation_rule_present_when_automations_module_enabled():
    from unittest.mock import patch
    from core.skills.commerce_admin import _tools_for
    with patch("vula.api.tenants.enabled_modules", return_value=["invoices", "automations"]):
        names = {t["function"]["name"] for t in _tools_for(TID, role=None)}
    assert "create_automation_rule" in names


def test_create_automation_rule_hidden_when_automations_module_not_enabled():
    """A tenant without the automations module (e.g. trades/services/health presets, which
    don't include it by default) has no dashboard tab to review pending firings — the tool must
    not be reachable via WhatsApp/chat either, or a rule could be created with nowhere to
    approve/reject its matches."""
    from unittest.mock import patch
    from core.skills.commerce_admin import _tools_for
    with patch("vula.api.tenants.enabled_modules", return_value=["invoices", "bookings"]):
        names = {t["function"]["name"] for t in _tools_for(TID, role=None)}
    assert "create_automation_rule" not in names


def test_create_automation_rule_present_when_tenant_has_no_config_yet():
    """No config at all → show_all behavior (matches every other gated tool group's own
    fallback) — a brand-new tenant isn't locked out before onboarding sets real modules."""
    from unittest.mock import patch
    from core.skills.commerce_admin import _tools_for
    with patch("vula.api.tenants.enabled_modules", return_value=[]):
        names = {t["function"]["name"] for t in _tools_for(TID, role=None)}
    assert "create_automation_rule" in names
