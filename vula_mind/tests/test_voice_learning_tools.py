"""Tests for the WhatsApp-triggerable voice-learning tools (2026-08-08) and the two "sound more
human" CONVERSATION_RULES additions.

vula/commerce/voice_profile.py (migration 119/120) already gathers real owner-authored text and
suggests a tenant-specific tone, but was only reachable via a dashboard REST endpoint nobody had
triggered for any real tenant — persona_prompt was confirmed NULL for every real tenant.
learn_my_voice/apply_voice_persona in core/skills/commerce_admin.py give the owner a WhatsApp
path to the same feature, reusing voice_profile.analyze_voice() and the existing accept
semantics (vula/api/commerce.py's admin_set_persona) as-is.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.skills.base import CONVERSATION_RULES, behaviour_preamble
from core.skills.commerce_admin import CommerceAdminSkill

TID = "digg-demo"


@pytest.fixture
def skill():
    return CommerceAdminSkill()


# ── CONVERSATION_RULES additions ──────────────────────────────────────────────

def test_conversation_rules_discourages_generic_filler():
    assert "is there anything else i can help you with" in CONVERSATION_RULES.lower()


def test_conversation_rules_discourages_raw_list_dumps():
    assert "lead with a short summary" in CONVERSATION_RULES.lower()


def test_behaviour_preamble_always_includes_conversation_rules():
    # Not gated behind agentic=True — applies to every skill, tool-calling or not.
    assert "is there anything else i can help you with" in behaviour_preamble().lower()


# ── learn_my_voice ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_learn_my_voice_returns_suggestion(skill):
    with patch("vula.commerce.voice_profile.analyze_voice",
              new=AsyncMock(return_value={"suggested": "Warm and casual, short replies.",
                                          "sample_count": 23})):
        res = await skill._learn_my_voice(TID)
    assert res["suggested_persona"] == "Warm and casual, short replies."
    assert res["sample_count"] == 23
    assert "note" in res


@pytest.mark.asyncio
async def test_learn_my_voice_passes_through_not_enough_data_error(skill):
    with patch("vula.commerce.voice_profile.analyze_voice",
              new=AsyncMock(return_value={"error": "Not enough data yet — Vula found 6 of your "
                                          "own messages/notes/emails (needs 15)."})):
        res = await skill._learn_my_voice(TID)
    assert "error" in res
    assert "Not enough data" in res["error"]


# ── apply_voice_persona ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_apply_voice_persona_updates_tenant_config(skill):
    import core.skills.commerce_admin as ca

    mock_db = MagicMock()
    mock_db.table.return_value.update.return_value.eq.return_value.execute.return_value = None

    with (
        patch.object(ca.service, "_client", return_value=mock_db),
        patch("vula.api.tenants.invalidate") as mock_invalidate,
    ):
        res = await skill._apply_voice_persona(TID, "Warm and casual, short replies.")

    assert res == {"applied": True, "persona_prompt": "Warm and casual, short replies."}
    mock_db.table.assert_called_with("vula_tenant_config")
    update_call = mock_db.table.return_value.update.call_args[0][0]
    assert update_call["persona_prompt"] == "Warm and casual, short replies."
    assert update_call["persona_prompt_suggested"] is None
    mock_invalidate.assert_called_once_with(TID)


@pytest.mark.asyncio
async def test_apply_voice_persona_requires_text(skill):
    res = await skill._apply_voice_persona(TID, "")
    assert "error" in res


# ── system prompt wiring ──────────────────────────────────────────────────────

def test_system_prompt_confirms_before_applying_persona(skill):
    prompt = skill._system_prompt(TID, role=None, name="Test")
    assert "apply_voice_persona" in prompt
    assert "learn_my_voice" in prompt
