"""Tests for the commerce_assistant skill and its WhatsApp handler wiring."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.skills.base import SkillInput, SkillOutput
from core.skills.commerce_assistant import CommerceAssistantSkill

TENANT = "off-the-hook"
CTX = {"tenant_id": TENANT, "session_id": "+27821234567", "customer_phone": "+27821234567"}


def _msg(content="", tool_calls=None):
    """Build a fake litellm choices[0].message."""
    resp = MagicMock()
    resp.choices = [SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))]
    return resp


# ── Tool executors / dispatch ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_list_products_formats_prices():
    skill = CommerceAssistantSkill()
    products = [{"slug": "snoek", "name": "Fresh Snoek", "price_cents": 18500, "category": "linefish", "description": ""}]
    with patch("core.skills.commerce_assistant.service.list_products", new=AsyncMock(return_value=products)):
        out = await skill._dispatch_tool("list_products", {}, CTX)
    assert out == [{"slug": "snoek", "name": "Fresh Snoek", "price": "R185.00", "category": "linefish"}]


@pytest.mark.asyncio
async def test_dispatch_add_to_cart_by_slug():
    skill = CommerceAssistantSkill()
    product = {"id": "p1", "name": "Fresh Snoek", "price_cents": 18500}
    with (
        patch("core.skills.commerce_assistant.service.get_product_by_slug", new=AsyncMock(return_value=product)),
        patch("core.skills.commerce_assistant.service.get_or_create_cart", new=AsyncMock(return_value={"id": "c1"})),
        patch("core.skills.commerce_assistant.service.add_to_cart", new=AsyncMock()) as mock_add,
    ):
        out = await skill._dispatch_tool("add_to_cart", {"product": "snoek", "quantity": 2}, CTX)
    mock_add.assert_awaited_once_with("c1", "p1", 2)
    assert out == {"added": "Fresh Snoek", "quantity": 2, "unit_price": "R185.00"}


@pytest.mark.asyncio
async def test_dispatch_add_to_cart_unknown_product():
    skill = CommerceAssistantSkill()
    with (
        patch("core.skills.commerce_assistant.service.get_product_by_slug", new=AsyncMock(return_value=None)),
        patch("core.skills.commerce_assistant.service.list_products", new=AsyncMock(return_value=[])),
    ):
        out = await skill._dispatch_tool("add_to_cart", {"product": "unicorn"}, CTX)
    assert "error" in out


@pytest.mark.asyncio
async def test_dispatch_track_order_found():
    skill = CommerceAssistantSkill()
    orders = [{"display_id": "OTH-00042", "status": "dispatched", "total_cents": 25000}]
    with patch("core.skills.commerce_assistant.service.list_orders", new=AsyncMock(return_value=orders)):
        out = await skill._dispatch_tool("track_order", {"order_id": "oth-00042"}, CTX)
    assert out == {"order_id": "OTH-00042", "status": "dispatched", "total": "R250.00"}


@pytest.mark.asyncio
async def test_dispatch_create_quote_from_explicit_items():
    skill = CommerceAssistantSkill()
    product = {"id": "p1", "name": "Fresh Snoek", "price_cents": 18500}
    created = {"invoice_number": "OFF-QTE-00001", "total_cents": 37000}
    with (
        patch("core.skills.commerce_assistant.service.get_product_by_slug", new=AsyncMock(return_value=product)),
        patch("core.skills.commerce_assistant.service.create_invoice", new=AsyncMock(return_value=created)) as mock_create,
    ):
        out = await skill._dispatch_tool(
            "create_quote", {"items": [{"product": "snoek", "quantity": 2}]}, CTX
        )
    assert out == {"quote_number": "OFF-QTE-00001", "items": 1, "total": "R370.00"}
    payload = mock_create.await_args.args[1]
    assert payload["doc_type"] == "quote"
    assert payload["customer_phone"] == CTX["customer_phone"]
    assert payload["line_items"][0] == {
        "description": "Fresh Snoek",
        "quantity": 2,
        "unit_price_cents": 18500,
        "product_id": "p1",
    }


@pytest.mark.asyncio
async def test_dispatch_create_quote_from_cart_when_no_items():
    skill = CommerceAssistantSkill()
    cart = {
        "id": "c1",
        "commerce_cart_items": [
            {"product_id": "p1", "quantity": 3, "unit_price_cents": 18500,
             "commerce_products": {"name": "Fresh Snoek"}},
        ],
    }
    created = {"invoice_number": "OFF-QTE-00002", "total_cents": 55500}
    with (
        patch("core.skills.commerce_assistant.service.get_or_create_cart", new=AsyncMock(return_value=cart)),
        patch("core.skills.commerce_assistant.service.create_invoice", new=AsyncMock(return_value=created)) as mock_create,
    ):
        out = await skill._dispatch_tool("create_quote", {}, CTX)
    assert out["quote_number"] == "OFF-QTE-00002"
    assert out["items"] == 1
    assert mock_create.await_args.args[1]["line_items"][0]["quantity"] == 3


@pytest.mark.asyncio
async def test_dispatch_create_quote_empty_returns_error():
    skill = CommerceAssistantSkill()
    with (
        patch("core.skills.commerce_assistant.service.get_or_create_cart", new=AsyncMock(return_value={"id": "c1", "commerce_cart_items": []})),
        patch("core.skills.commerce_assistant.service.create_invoice", new=AsyncMock()) as mock_create,
    ):
        out = await skill._dispatch_tool("create_quote", {}, CTX)
    assert "error" in out
    mock_create.assert_not_awaited()


def test_start_checkout_uses_store_url():
    skill = CommerceAssistantSkill()
    with patch("core.skills.commerce_assistant.settings") as mock_settings:
        mock_settings.store_urls = {TENANT: "https://offthehook.co.za"}
        out = skill._exec_start_checkout(TENANT)
    assert out == {"checkout_url": "https://offthehook.co.za/cart"}


# ── Agent loop + fallback ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_loop_returns_content_without_tool_calls():
    skill = CommerceAssistantSkill()
    with patch("litellm.acompletion", new=AsyncMock(return_value=_msg("Howzit! 🐟"))):
        answer = await skill._agent_loop("sys", "", "hi", CTX)
    assert answer == "Howzit! 🐟"


@pytest.mark.asyncio
async def test_run_falls_back_when_loop_fails():
    skill = CommerceAssistantSkill()
    inp = SkillInput(question="what fish do you have?", tenant_id=TENANT, metadata=CTX)
    with (
        patch.object(skill, "_retrieve_kb", new=AsyncMock(return_value=("", []))),
        patch.object(skill, "_agent_loop", new=AsyncMock(side_effect=RuntimeError("no tools"))),
        patch("core.skills.commerce_assistant.service.list_products", new=AsyncMock(return_value=[])),
        patch("litellm.acompletion", new=AsyncMock(return_value=_msg("We have fresh snoek today."))),
    ):
        out = await skill.run(inp)
    assert out.success
    assert out.answer == "We have fresh snoek today."
    assert out.confidence < 0.6  # fallback confidence band


# ── WhatsApp handler wiring ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_commerce_assistant_persists_both_turns():
    from vula.api.whatsapp import _run_commerce_assistant

    mock_skill = AsyncMock(return_value=SkillOutput(answer="Fresh snoek is R185.", skill_name="commerce_assistant"))
    with (
        patch("core.skills.loader.get_skill", return_value=mock_skill),
        patch("vula.commerce.service.get_or_create_session", new=AsyncMock(return_value={"id": "sess-1"})),
        patch("vula.commerce.service.get_recent_messages", new=AsyncMock(return_value=[])),
        patch("vula.commerce.service.append_message", new=AsyncMock()) as mock_append,
        patch("vula.api.whatsapp._send_reply", new=AsyncMock(return_value=True)) as mock_send,
    ):
        handled = await _run_commerce_assistant("+27821234567", "snoek price?", TENANT)

    assert handled is True
    mock_send.assert_awaited_once_with("+27821234567", "Fresh snoek is R185.", TENANT)
    assert mock_append.await_count == 2


@pytest.mark.asyncio
async def test_run_commerce_assistant_returns_false_on_empty_answer():
    from vula.api.whatsapp import _run_commerce_assistant

    mock_skill = AsyncMock(return_value=SkillOutput(answer="", skill_name="commerce_assistant", error="boom"))
    with (
        patch("core.skills.loader.get_skill", return_value=mock_skill),
        patch("vula.commerce.service.get_or_create_session", new=AsyncMock(return_value={"id": "sess-1"})),
        patch("vula.commerce.service.get_recent_messages", new=AsyncMock(return_value=[])),
        patch("vula.api.whatsapp._send_reply", new=AsyncMock()) as mock_send,
    ):
        handled = await _run_commerce_assistant("+27821234567", "snoek price?", TENANT)

    assert handled is False
    mock_send.assert_not_awaited()
