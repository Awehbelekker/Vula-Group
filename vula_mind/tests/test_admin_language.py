"""Tests for the 2026-08-17 language fix: the owner/staff admin chat path
(core/skills/commerce_admin.py, vula/api/whatsapp.py::_run_commerce_admin) never detected,
persisted, or injected a language signal at all — only commerce_assistant.py (the customer
path) did, backwards from what actually mattered since the owner is who uses this path for
real business chat. Also covers the knowledge-mode entry point (_rag_reply) that a dedicated-
line tenant like DIGG actually routes through, and the new centralized `behaviour_preamble`
language parameter every skill can now opt into with one line instead of re-inventing it.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.skills.base import behaviour_preamble
from core.skills.commerce_admin import CommerceAdminSkill


# ── behaviour_preamble (core/skills/base.py) ────────────────────────────────

def test_behaviour_preamble_includes_afrikaans_block():
    text = behaviour_preamble(preferred_language="af")
    assert "Afrikaans" in text


def test_behaviour_preamble_no_lang_block_for_english():
    text = behaviour_preamble(preferred_language="en")
    assert "usually writes in" not in text


def test_behaviour_preamble_no_lang_block_when_not_given():
    text = behaviour_preamble()
    assert "usually writes in" not in text


def test_behaviour_preamble_lang_block_survives_agentic_and_persona():
    text = behaviour_preamble(persona="Be warm.", agentic=True, preferred_language="zu")
    assert "isiZulu" in text and "Be warm." in text


# ── CommerceAdminSkill._system_prompt ────────────────────────────────────────

def test_admin_system_prompt_includes_language_for_owner():
    """2026-08-24: this assertion (bare "Afrikaans" in prompt) was a false-positive all
    along — CONVERSATION_RULES' generic multi-language line always mentions "Afrikaans"
    regardless of whether preferred_language is actually threaded through, so this test never
    caught the real bug (confirmed live: the owner/staff branch computed `lang` but never
    passed it to behaviour_preamble — only the sales_rep branch did). Now asserts the specific
    per-language instruction block that only appears when preferred_language is genuinely used."""
    skill = CommerceAdminSkill()
    prompt = skill._system_prompt("digg-demo", role=None, name="Judy", lang="af")
    assert "Reply in Afrikaans by default" in prompt


def test_admin_system_prompt_omits_language_block_for_owner_when_no_lang_given():
    skill = CommerceAdminSkill()
    prompt = skill._system_prompt("digg-demo", role=None, name="Judy", lang="")
    assert "Reply in" not in prompt or "by default" not in prompt


def test_admin_system_prompt_includes_language_for_sales_rep():
    skill = CommerceAdminSkill()
    prompt = skill._system_prompt("digg-demo", role="sales_rep", name="Thabo", lang="zu")
    assert "Reply in isiZulu by default" in prompt


def test_admin_system_prompt_no_language_block_when_unset():
    skill = CommerceAdminSkill()
    prompt = skill._system_prompt("digg-demo", role=None, name="Judy")
    assert "usually writes in" not in prompt


# ── _run_commerce_admin (vula/api/whatsapp.py) ──────────────────────────────

@pytest.mark.asyncio
async def test_run_commerce_admin_persists_detected_language():
    """Mirrors the existing regression test for _run_commerce_assistant
    (test_run_commerce_assistant_persists_detected_language) — same bug, same fix, admin side."""
    from vula.api.whatsapp import _run_commerce_admin

    mock_session = {"id": "sess-1", "preferred_language": None}
    mock_service = MagicMock()
    mock_service.get_or_create_session = AsyncMock(return_value=mock_session)
    mock_service.get_recent_messages = AsyncMock(return_value=[])
    mock_service.format_history = MagicMock(return_value="")
    mock_service.set_session_language = AsyncMock()
    mock_service.append_message = AsyncMock()
    mock_service._client.return_value.table.return_value.select.return_value.eq.return_value \
        .eq.return_value.execute.return_value.data = []

    mock_output = MagicMock(success=True, answer="Goeie dag! Hoe kan ek help?", error=None)
    mock_skill = AsyncMock(return_value=mock_output)

    with (
        patch("vula.commerce.service", mock_service),
        patch("core.skills.loader.get_skill", return_value=mock_skill),
        patch("vula.api.whatsapp._send_reply", new=AsyncMock(return_value=True)),
    ):
        handled = await _run_commerce_admin(
            "27821234567", "wat is vandag se verkope", "digg-demo", detected_lang="af"
        )

    assert handled is True
    mock_service.set_session_language.assert_awaited_once_with(
        "digg-demo", "admin:27821234567", "af")
    sent_input = mock_skill.call_args[0][0]
    assert sent_input.metadata["preferred_language"] == "af"


@pytest.mark.asyncio
async def test_run_commerce_admin_no_detected_lang_falls_back_to_text_heuristic():
    mock_session = {"id": "sess-1", "preferred_language": None}
    mock_service = MagicMock()
    mock_service.get_or_create_session = AsyncMock(return_value=mock_session)
    mock_service.get_recent_messages = AsyncMock(return_value=[])
    mock_service.format_history = MagicMock(return_value="")
    mock_service.set_session_language = AsyncMock()
    mock_service.append_message = AsyncMock()
    mock_service._client.return_value.table.return_value.select.return_value.eq.return_value \
        .eq.return_value.execute.return_value.data = []

    mock_output = MagicMock(success=True, answer="ok", error=None)
    mock_skill = AsyncMock(return_value=mock_output)

    with (
        patch("vula.commerce.service", mock_service),
        patch("core.skills.loader.get_skill", return_value=mock_skill),
        patch("vula.api.whatsapp._send_reply", new=AsyncMock(return_value=True)),
    ):
        from vula.api.whatsapp import _run_commerce_admin
        await _run_commerce_admin("27821234567", "ek wil graag die verkope sien asseblief", "digg-demo")

    mock_service.set_session_language.assert_awaited_once_with(
        "digg-demo", "admin:27821234567", "af")


# ── _rag_reply (vula/api/whatsapp.py) — the actual DIGG dispatch path ───────

@pytest.mark.asyncio
async def test_rag_reply_detects_afrikaans_and_passes_to_agent_runner():
    from types import SimpleNamespace
    captured = {}

    class _FakeRunner:
        async def run(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(final_answer="Goeie dag!", skill_used="reasoning", confidence=0.8)

    with patch("core.agent_runner.get_agent_runner", return_value=_FakeRunner()):
        from vula.api.whatsapp import _rag_reply
        await _rag_reply("digg-demo", "ek wil graag die verkope sien asseblief", phone="27821234567")

    assert captured["metadata"]["preferred_language"] == "af"


@pytest.mark.asyncio
async def test_rag_reply_no_lang_key_when_undetected():
    from types import SimpleNamespace
    captured = {}

    class _FakeRunner:
        async def run(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(final_answer="ok", skill_used="reasoning", confidence=0.8)

    with patch("core.agent_runner.get_agent_runner", return_value=_FakeRunner()):
        from vula.api.whatsapp import _rag_reply
        await _rag_reply("digg-demo", "xyzxyz qwqw", phone="27821234567")

    assert "preferred_language" not in captured["metadata"]
