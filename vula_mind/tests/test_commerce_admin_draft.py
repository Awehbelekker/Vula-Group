"""Tests for draft_letter being wired into commerce_admin (commerce-mode tenant owners), reusing
the same shared function draft_admin.py exposes for knowledge-mode tenants. The full generation
+ PDF + WhatsApp-send path was verified live against off-the-hook during development (296-word
fee proposal, correct dispatch return shape) — this file locks in wiring: the tool is always
registered regardless of tenant modules, and dispatch calls the shared function with the right
tenant_id/phone."""
from unittest.mock import AsyncMock, patch

import pytest

from core.skills.commerce_admin import CommerceAdminSkill, _tools_for, _ALL_TOOL_SPECS


def test_draft_letter_always_registered():
    assert "draft_letter" in [t["function"]["name"] for t in _ALL_TOOL_SPECS]
    # Always-on (like marketing) regardless of a tenant's enabled modules.
    assert "draft_letter" in [t["function"]["name"] for t in _tools_for("off-the-hook")]


@pytest.mark.asyncio
async def test_dispatch_draft_letter_calls_shared_function():
    skill = CommerceAdminSkill()
    ctx = {"tenant_id": "off-the-hook", "phone": "27821234567"}
    args = {"document_type": "fee_proposal", "brief": "Catering for 50 guests, R12,000."}

    with patch("core.skills.draft_admin.draft_letter",
              new=AsyncMock(return_value={"draft_id": "abc", "sent_via_whatsapp": True})) as mock_draft:
        result = await skill._dispatch_tool("draft_letter", args, ctx)

    mock_draft.assert_awaited_once_with(args, "off-the-hook", "27821234567")
    assert result == {"draft_id": "abc", "sent_via_whatsapp": True}


@pytest.mark.asyncio
async def test_dispatch_draft_letter_handles_missing_phone():
    """ctx.get("phone") can be None (inp.metadata.get("customer_phone") returns None when
    unset) — must pass "" through, not crash."""
    skill = CommerceAdminSkill()
    ctx = {"tenant_id": "off-the-hook", "phone": None}
    args = {"document_type": "fee_proposal", "brief": "Test brief."}

    with patch("core.skills.draft_admin.draft_letter", new=AsyncMock(return_value={})) as mock_draft:
        await skill._dispatch_tool("draft_letter", args, ctx)

    mock_draft.assert_awaited_once_with(args, "off-the-hook", "")
