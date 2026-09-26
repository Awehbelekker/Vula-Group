"""Tests for the WhatsApp side of commerce_admin's confirm-button flow (migration 146):
_send_wa_buttons and _handle_admin_confirm_reply. See tests/test_commerce_admin_confirm_buttons.py
for the skill-side half (ConfirmationRequired / run()).
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

TID = "off-the-hook"
PHONE = "27737815979"


class _PendingChain:
    """Records every .eq() filter on commerce_pending_confirmations' claim UPDATE and returns
    the given rows only if the claim was scoped to the tapping phone."""
    def __init__(self, rows):
        self.rows, self.filters = rows, {}

    def table(self, _name):
        return self

    def update(self, _patch):
        return self

    def eq(self, col, val):
        self.filters[col] = val
        return self

    def gt(self, *_a):
        return self

    def execute(self):
        return MagicMock(data=self.rows if self.filters.get("phone") == PHONE else [])


def _mock_pending_row_chain(returned_rows):
    return _PendingChain(returned_rows)


@pytest.mark.asyncio
async def test_send_wa_buttons_sends_correct_payload():
    from vula.api.whatsapp import _send_wa_buttons

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("vula.api.whatsapp.httpx.AsyncClient", return_value=mock_client):
        ok = await _send_wa_buttons(
            {"phone_id": "123", "token": "tok"}, "27821234567", "Product: Hake fillets",
            [{"id": "admin_confirm:abc", "title": "Confirm"}, {"id": "admin_cancel:abc", "title": "Cancel"}],
        )

    assert ok is True
    payload = mock_client.post.call_args.kwargs["json"]
    assert payload["type"] == "interactive"
    assert payload["interactive"]["type"] == "button"
    assert payload["interactive"]["body"]["text"] == "Product: Hake fillets"
    buttons = payload["interactive"]["action"]["buttons"]
    assert len(buttons) == 2
    assert buttons[0]["reply"]["id"] == "admin_confirm:abc"
    assert buttons[1]["reply"]["id"] == "admin_cancel:abc"


@pytest.mark.asyncio
async def test_send_wa_buttons_caps_at_three():
    from vula.api.whatsapp import _send_wa_buttons

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("vula.api.whatsapp.httpx.AsyncClient", return_value=mock_client):
        await _send_wa_buttons(
            {"phone_id": "123", "token": "tok"}, "27821234567", "body",
            [{"id": f"b{i}", "title": f"T{i}"} for i in range(5)],
        )

    buttons = mock_client.post.call_args.kwargs["json"]["interactive"]["action"]["buttons"]
    assert len(buttons) == 3


@pytest.mark.asyncio
async def test_handle_admin_confirm_reply_confirm_path_redispatches_with_confirm_true():
    from vula.api.whatsapp import _handle_admin_confirm_reply
    import vula.api.whatsapp as wa

    row = {"id": "p1", "tool_name": "update_stock",
           "tool_args": {"product": "Hake fillets", "quantity": 20}}
    real_result = {"updated": "Hake fillets", "stock_quantity": 20, "verified": True}

    dispatch_mock = AsyncMock(return_value=real_result)
    sent = {}

    async def fake_send_reply(phone, message, tenant_id=""):
        sent["phone"] = phone
        sent["message"] = message
        return True

    with (
        patch("vula.commerce.service._client", return_value=_mock_pending_row_chain([row])),
        patch("core.skills.commerce_admin.CommerceAdminSkill") as MockSkill,
        patch.object(wa, "_send_reply", new=fake_send_reply),
        patch("litellm.acompletion", new=AsyncMock(side_effect=RuntimeError("no cloud in test"))),
    ):
        MockSkill.return_value._dispatch_tool = dispatch_mock
        with patch("vula.commerce.service.get_or_create_session",
                   new=AsyncMock(return_value={"id": "sess1"})), \
             patch("vula.commerce.service.append_message", new=AsyncMock()):
            await _handle_admin_confirm_reply(PHONE, "admin_confirm:p1", TID)

    dispatch_mock.assert_awaited_once()
    call_args = dispatch_mock.call_args[0]
    assert call_args[0] == "update_stock"
    assert call_args[1] == {"product": "Hake fillets", "quantity": 20, "confirm": True}
    # LLM summarise failed (as mocked) -> deterministic fallback must still produce a real reply.
    assert sent["phone"] == PHONE
    assert "Hake fillets" in sent["message"] or "Done" in sent["message"]


@pytest.mark.asyncio
async def test_handle_admin_confirm_reply_cancel_path_never_dispatches():
    from vula.api.whatsapp import _handle_admin_confirm_reply
    import vula.api.whatsapp as wa

    row = {"id": "p1", "tool_name": "update_stock", "tool_args": {}}
    dispatch_mock = AsyncMock()
    sent = {}

    async def fake_send_reply(phone, message, tenant_id=""):
        sent["message"] = message
        return True

    with (
        patch("vula.commerce.service._client", return_value=_mock_pending_row_chain([row])),
        patch("core.skills.commerce_admin.CommerceAdminSkill") as MockSkill,
        patch.object(wa, "_send_reply", new=fake_send_reply),
    ):
        MockSkill.return_value._dispatch_tool = dispatch_mock
        await _handle_admin_confirm_reply(PHONE, "admin_cancel:p1", TID)

    dispatch_mock.assert_not_called()
    assert "cancel" in sent["message"].lower()


@pytest.mark.asyncio
async def test_handle_admin_confirm_reply_already_resolved_or_expired():
    from vula.api.whatsapp import _handle_admin_confirm_reply
    import vula.api.whatsapp as wa

    sent = {}

    async def fake_send_reply(phone, message, tenant_id=""):
        sent["message"] = message
        return True

    with (
        patch("vula.commerce.service._client", return_value=_mock_pending_row_chain([])),  # no match
        patch.object(wa, "_send_reply", new=fake_send_reply),
    ):
        await _handle_admin_confirm_reply(PHONE, "admin_confirm:p1", TID)

    assert "already been handled" in sent["message"] or "expired" in sent["message"]


@pytest.mark.asyncio
async def test_handle_admin_confirm_reply_ignores_malformed_id():
    from vula.api.whatsapp import _handle_admin_confirm_reply

    with patch("vula.commerce.service._client") as mock_client:
        await _handle_admin_confirm_reply(PHONE, "admin_confirm:", TID)  # no id after colon
    mock_client.assert_not_called()


@pytest.mark.asyncio
async def test_confirm_tap_is_scoped_to_the_requesting_phone_and_keeps_caller_role():
    from vula.api.whatsapp import _handle_admin_confirm_reply
    import vula.api.whatsapp as wa

    row = {"id": "p1", "tool_name": "update_stock",
           "tool_args": {"product": "Hake", "quantity": 5, "_caller": {"role": "sales_rep", "name": "Sam"}}}
    chain = _mock_pending_row_chain([row])
    dispatch_mock = AsyncMock(return_value={"updated": "Hake"})

    with (
        patch("vula.commerce.service._client", return_value=chain),
        patch("core.skills.commerce_admin.CommerceAdminSkill") as MockSkill,
        patch.object(wa, "_send_reply", new=AsyncMock(return_value=True)),
        patch("litellm.acompletion", new=AsyncMock(side_effect=RuntimeError("no cloud"))),
        patch("vula.commerce.service.get_or_create_session", new=AsyncMock(return_value={"id": "s"})),
        patch("vula.commerce.service.append_message", new=AsyncMock()),
    ):
        MockSkill.return_value._dispatch_tool = dispatch_mock
        await _handle_admin_confirm_reply(PHONE, "admin_confirm:p1", TID)

    assert chain.filters["phone"] == PHONE
    name, args, ctx = dispatch_mock.call_args[0]
    assert args == {"product": "Hake", "quantity": 5, "confirm": True}
    assert ctx["caller_role"] == "sales_rep" and ctx["caller_name"] == "Sam"


@pytest.mark.asyncio
async def test_confirm_tap_from_another_number_applies_nothing():
    from vula.api.whatsapp import _handle_admin_confirm_reply
    import vula.api.whatsapp as wa

    row = {"id": "p1", "tool_name": "update_stock", "tool_args": {"product": "Hake"}}
    dispatch_mock = AsyncMock()
    with (
        patch("vula.commerce.service._client", return_value=_mock_pending_row_chain([row])),
        patch("core.skills.commerce_admin.CommerceAdminSkill") as MockSkill,
        patch.object(wa, "_send_reply", new=AsyncMock(return_value=True)),
    ):
        MockSkill.return_value._dispatch_tool = dispatch_mock
        await _handle_admin_confirm_reply("27820000000", "admin_confirm:p1", TID)
    dispatch_mock.assert_not_awaited()
