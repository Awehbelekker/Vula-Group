"""Gap audit 2.5: a voice note asking for a quote ends as a draft the owner sees, and nothing
reaches the customer without the owner confirming a preview.

1. A voice note's transcript goes down exactly the typed-message path; a language Whisper can't
   transcribe reliably gets an honest "please type it".
2. create_invoice only ever makes a draft.
3. The model can't skip the preview: confirm=true on a customer-facing tool with no preview shown
   to this phone is dropped, so the owner gets the preview and the Confirm button."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.skills.commerce_admin as ca
from core.skills.commerce_admin import CommerceAdminSkill, ConfirmationRequired

TID = "acme"


# ── 1. voice → same path as typed ────────────────────────────────────────────

@pytest.fixture
def voice(monkeypatch):
    from vula.api import whatsapp as wa
    sent, routed = [], []
    monkeypatch.setattr(wa, "_download_media_bytes", AsyncMock(return_value=b"ogg"))
    monkeypatch.setattr(wa, "_send_reply", AsyncMock(side_effect=lambda p, m, *a, **k: sent.append(m) or True))
    monkeypatch.setattr(wa, "_handle_commerce_message",
                        AsyncMock(side_effect=lambda phone, text, *a, **k: routed.append(text)))
    return wa, sent, routed


@pytest.mark.asyncio
async def test_english_voice_note_is_handled_like_typed_text(voice, monkeypatch):
    wa, sent, routed = voice
    monkeypatch.setattr(wa, "transcribe_audio", AsyncMock(return_value=(
        "Make a quote for Sam, 3 days tiling at 2500 a day", "en")))
    await wa._handle_voice_note("27820000001", "m1", "audio/ogg", "w1", "commerce", TID)
    assert routed == ["Make a quote for Sam, 3 days tiling at 2500 a day"] and sent == []


@pytest.mark.asyncio
async def test_isizulu_voice_note_asks_to_type(voice, monkeypatch):
    wa, sent, routed = voice
    monkeypatch.setattr(wa, "transcribe_audio", AsyncMock(return_value=("garbled", "zu")))
    await wa._handle_voice_note("27820000001", "m1", "audio/ogg", "w1", "commerce", TID)
    assert routed == [] and "type" in sent[0].lower()


# ── 2. a quote is only ever a draft ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_quote_is_created_as_a_draft_and_not_sent(monkeypatch):
    from vula.commerce import service
    created = {}

    async def create(tid, data):
        created.update(data)
        return {"id": "q1", "invoice_number": "ACM-QUO-00004", "total_cents": 862500, "status": "draft"}

    monkeypatch.setattr(service, "create_invoice", create)
    monkeypatch.setattr(ca.settings, "readback_verify_enabled", False)
    send = AsyncMock()
    monkeypatch.setattr("vula.api.commerce.admin_send_invoice_whatsapp", send)
    out = await CommerceAdminSkill()._create_invoice(TID, {
        "doc_type": "quote", "customer_name": "Sam",
        "line_items": [{"description": "Tiling", "quantity": 3, "unit_price_rands": 2500}]})
    assert created["status"] == "draft" and created["doc_type"] == "quote"
    assert out["status"] == "draft" and "send it" in out["note"]
    send.assert_not_awaited()


# ── 3. no confirm without a preview ──────────────────────────────────────────

def _loop_calls(tool_args: str):
    from tests.test_known_bad_transcripts import _resp, _tool_call

    async def fake_completion(*a, **kw):
        return _resp(tool_calls=[_tool_call("c1", "send_invoice", tool_args)])
    return fake_completion


@pytest.mark.asyncio
async def test_confirm_before_any_preview_becomes_a_preview(monkeypatch):
    from tests.test_known_bad_transcripts import _fake_route
    seen = []

    async def dispatch(self, name, args, ctx):
        seen.append(dict(args))
        return ({"sent": True} if args.get("confirm") else
                {"preview": True, "invoice_number": "ACM-QUO-00004", "message": "Confirm to send"})

    monkeypatch.setattr(ca, "_preview_was_shown", lambda tid, phone, tool: False)
    with (patch.object(ca, "resolve_generation_route", new=_fake_route),
          patch.object(ca, "escalate_to_cloud", return_value=("openrouter/test", "k", None)),
          patch.object(CommerceAdminSkill, "_dispatch_tool", new=dispatch),
          patch("litellm.acompletion", new=_loop_calls('{"invoice_number": "ACM-QUO-00004", "confirm": true}'))):
        with pytest.raises(ConfirmationRequired):
            await CommerceAdminSkill()._agent_loop("system", "", "make the quote and send it to Sam",
                                                   {"tenant_id": TID, "phone": "27820000001"}, tools=None)
    assert seen == [{"invoice_number": "ACM-QUO-00004"}]        # confirm dropped, preview shown


@pytest.mark.asyncio
async def test_typed_yes_after_a_real_preview_still_sends(monkeypatch):
    from tests.test_known_bad_transcripts import _fake_route, _resp
    seen, calls = [], {"n": 0}

    async def dispatch(self, name, args, ctx):
        seen.append(dict(args))
        return {"sent": True, "invoice_number": "ACM-QUO-00004"}

    first = _loop_calls('{"invoice_number": "ACM-QUO-00004", "confirm": true}')

    async def completion(*a, **kw):
        calls["n"] += 1
        return await first() if calls["n"] == 1 else _resp(content="Sent ✅")

    monkeypatch.setattr(ca, "_preview_was_shown", lambda tid, phone, tool: True)
    with (patch.object(ca, "resolve_generation_route", new=_fake_route),
          patch.object(ca, "escalate_to_cloud", return_value=("openrouter/test", "k", None)),
          patch.object(CommerceAdminSkill, "_dispatch_tool", new=dispatch),
          patch("litellm.acompletion", new=completion)):
        await CommerceAdminSkill()._agent_loop("system", "", "yes", {"tenant_id": TID, "phone": "27820000001"},
                                               tools=None)
    assert seen == [{"invoice_number": "ACM-QUO-00004", "confirm": True}]


def test_a_shown_preview_is_used_up_once(monkeypatch):
    rows = [{"id": "p1"}]
    q = MagicMock()
    for m in ("update", "eq", "gt"):
        getattr(q, m).return_value = q
    q.execute.side_effect = lambda: MagicMock(data=[rows.pop()] if rows else [])
    db = MagicMock()
    db.table.return_value = q
    monkeypatch.setattr("vula.commerce.service._client", lambda: db)
    assert ca._preview_was_shown(TID, "27820000001", "send_invoice") is True
    assert ca._preview_was_shown(TID, "27820000001", "send_invoice") is False
    assert ca._preview_was_shown(TID, None, "send_invoice") is False
