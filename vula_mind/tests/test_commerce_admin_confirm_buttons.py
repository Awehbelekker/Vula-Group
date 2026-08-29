"""Tests for commerce_admin's WhatsApp reply-button confirm flow (migration 146), added
2026-08-25 after a real transcript showed the free-text version of this exchange fail three
different ways in a row for the same owner request: a misread confirmation, a blind retry, and
eventually a fully fabricated "invoice created" success for an invoice that was never actually
made (see tests/test_known_bad_transcripts.py incidents #5/#6). A button tap sends back an
exact, unambiguous payload — there's nothing left for the model to misinterpret.
"""
from unittest.mock import MagicMock, patch

import pytest

from core.skills.commerce_admin import (
    CommerceAdminSkill, ConfirmationRequired, _preview_summary,
)
from core.skills.base import SkillInput

TID = "off-the-hook"


@pytest.fixture
def skill():
    return CommerceAdminSkill()


# ── _preview_summary ─────────────────────────────────────────────────────────────

def test_preview_summary_builds_readable_text_from_fields():
    result = {"preview": True, "product": "Hake fillets", "current_stock": 12, "new_stock": 20,
              "message": "Confirm to apply (call again with confirm=true)."}
    summary = _preview_summary(result)
    assert "Product: Hake fillets" in summary
    assert "Current stock: 12" in summary
    assert "New stock: 20" in summary
    # The internal model-facing instruction must never leak into what the owner reads.
    assert "confirm=true" not in summary


def test_preview_summary_falls_back_when_no_usable_fields():
    assert _preview_summary({"preview": True}) == "Confirm this action?"


def test_preview_summary_drops_empty_fields():
    result = {"preview": True, "supplier": "Acme", "contact_email": None, "notes": ""}
    summary = _preview_summary(result)
    assert "Supplier: Acme" in summary
    assert "contact_email" not in summary.lower().replace("_", " ")
    assert "notes" not in summary.lower()


def test_confirmation_required_does_not_collide_with_exception_args():
    # Caught by this exact test before shipping: self.args on an Exception subclass collides
    # with BaseException's own .args (set by super().__init__()), silently clobbering it back to
    # a 1-tuple of the message string. Must use .tool_args instead.
    exc = ConfirmationRequired("update_stock", {"product": "Hake fillets"}, {"preview": True})
    assert exc.tool_args == {"product": "Hake fillets"}
    assert exc.args == ("confirmation required for update_stock",)  # the real Exception.args


# ── ConfirmationRequired raised from _agent_loop ─────────────────────────────────

@pytest.mark.asyncio
async def test_agent_loop_raises_confirmation_required_on_preview(skill):
    import core.skills.commerce_admin as ca
    from tests.test_known_bad_transcripts import _resp, _tool_call, _fake_route

    async def fake_completion(*a, **kw):
        return _resp(tool_calls=[_tool_call("c1", "update_stock",
                                            '{"product": "Hake fillets", "quantity": 20}')])

    preview_result = {"preview": True, "product": "Hake fillets", "current_stock": 12,
                      "new_stock": 20, "message": "Confirm to apply (call again with confirm=true)."}

    with (
        patch.object(ca, "resolve_generation_route", new=_fake_route),
        patch.object(ca, "escalate_to_cloud", return_value=("openrouter/test", "k", None)),
        patch.object(CommerceAdminSkill, "_dispatch_tool", return_value=preview_result),
        patch("litellm.acompletion", new=fake_completion),
    ):
        with pytest.raises(ConfirmationRequired) as exc_info:
            await skill._agent_loop("system", "", "set hake fillets to 20", {"tenant_id": TID},
                                    tools=None)

    assert exc_info.value.tool_name == "update_stock"
    assert exc_info.value.tool_args == {"product": "Hake fillets", "quantity": 20}
    assert exc_info.value.result == preview_result


# ── run() catches it and returns a real confirm_request ──────────────────────────

@pytest.mark.asyncio
async def test_run_returns_confirm_request_on_preview(skill):
    import core.skills.commerce_admin as ca

    preview_result = {"preview": True, "product": "Hake fillets", "new_stock": 20}

    async def raise_confirmation(*a, **kw):
        raise ConfirmationRequired("update_stock", {"product": "Hake fillets", "quantity": 20},
                                   preview_result)

    mock_insert = MagicMock()
    mock_insert.execute.return_value = MagicMock(data=[{"id": "pending-123"}])

    with (
        patch.object(skill, "_agent_loop", new=raise_confirmation),
        patch.object(ca, "service") as mock_service,
    ):
        mock_service._client.return_value.table.return_value.insert.return_value = mock_insert
        inp = SkillInput(question="set hake fillets to 20", tenant_id=TID,
                         conversation_history="", metadata={"customer_phone": "27737815979"})
        out = await skill.run(inp)

    assert out.confirm_request is not None
    assert out.confirm_request["id"] == "pending-123"
    assert "Product: Hake fillets" in out.confirm_request["summary"]
    assert out.confirm_request["confirm_label"] == "Confirm"
    assert out.confirm_request["cancel_label"] == "Cancel"
    # The row actually inserted should carry the real tool + args, not just the preview text.
    inserted_row = mock_service._client.return_value.table.return_value.insert.call_args[0][0]
    assert inserted_row["tool_name"] == "update_stock"
    assert inserted_row["tool_args"] == {"product": "Hake fillets", "quantity": 20}
    assert inserted_row["tenant_id"] == TID
    assert inserted_row["phone"] == "27737815979"


@pytest.mark.asyncio
async def test_run_falls_back_to_free_text_when_migration_not_applied(skill):
    # Insert fails (table doesn't exist yet) — must fail open to the old free-text confirm
    # rather than losing the preview entirely.
    import core.skills.commerce_admin as ca

    preview_result = {"preview": True, "product": "Hake fillets", "new_stock": 20}

    async def raise_confirmation(*a, **kw):
        raise ConfirmationRequired("update_stock", {"product": "Hake fillets"}, preview_result)

    with (
        patch.object(skill, "_agent_loop", new=raise_confirmation),
        patch.object(ca, "service") as mock_service,
    ):
        mock_service._client.side_effect = RuntimeError("relation does not exist")
        inp = SkillInput(question="set hake fillets to 20", tenant_id=TID,
                         conversation_history="", metadata={"customer_phone": "27737815979"})
        out = await skill.run(inp)

    assert out.confirm_request is None
    assert "Product: Hake fillets" in out.answer
    assert "yes" in out.answer.lower()
