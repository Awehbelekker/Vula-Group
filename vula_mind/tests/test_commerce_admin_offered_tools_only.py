"""commerce_admin only runs a tool that was offered to this caller (2026-09-25 review).

The model can name any of the skill's ~67 tools; a sales rep's restricted toolset (or a
prompt injected via history/a document) could otherwise reach owner-only tools.
"""
from unittest.mock import AsyncMock, patch

import pytest

import core.skills.commerce_admin as ca
from core.skills.commerce_admin import CommerceAdminSkill
from tests.test_known_bad_transcripts import _fake_route, _resp, _tool_call


@pytest.mark.asyncio
async def test_tool_outside_offered_set_is_refused_not_run():
    calls = iter([
        _resp(tool_calls=[_tool_call("c1", "update_stock", '{"product": "Hake", "quantity": 0, "confirm": true}')]),
        _resp(content="Sorry, I can't change stock."),
    ])

    async def fake_completion(*a, **kw):
        return next(calls)

    dispatch = AsyncMock(return_value={"ok": True})
    rep_tools = [t for t in ca._ALL_TOOL_SPECS if t["function"]["name"] == "recent_orders"]
    with (
        patch.object(ca, "resolve_generation_route", new=_fake_route),
        patch.object(ca, "escalate_to_cloud", return_value=("openrouter/test", "k", None)),
        patch.object(CommerceAdminSkill, "_dispatch_tool", new=dispatch),
        patch("litellm.acompletion", new=fake_completion),
    ):
        out = await CommerceAdminSkill()._agent_loop("s", "", "set hake to 0", {"tenant_id": "t"},
                                                     tools=rep_tools)
    dispatch.assert_not_awaited()
    assert "can't" in out


@pytest.mark.asyncio
async def test_offered_tool_still_runs():
    calls = iter([
        _resp(tool_calls=[_tool_call("c1", "recent_orders", '{}')]),
        _resp(content="Here are your orders."),
    ])

    async def fake_completion(*a, **kw):
        return next(calls)

    dispatch = AsyncMock(return_value=[{"order": "OTH-1"}])
    rep_tools = [t for t in ca._ALL_TOOL_SPECS if t["function"]["name"] == "recent_orders"]
    with (
        patch.object(ca, "resolve_generation_route", new=_fake_route),
        patch.object(ca, "escalate_to_cloud", return_value=("openrouter/test", "k", None)),
        patch.object(CommerceAdminSkill, "_dispatch_tool", new=dispatch),
        patch("litellm.acompletion", new=fake_completion),
    ):
        await CommerceAdminSkill()._agent_loop("s", "", "orders?", {"tenant_id": "t"}, tools=rep_tools)
    dispatch.assert_awaited_once()
