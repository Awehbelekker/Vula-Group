"""Tests for the centralized agentic-tool-calling guardrails (2026-08-08).

The `_GUARDRAILS` block added to commerce_admin.py earlier the same day (ask instead of
guessing, never leak internal tool names, never claim an action succeeded without a real tool
call, handle need_info properly) turned out to be a systemic gap — no other tool-calling skill
had it, and commerce_admin.py/commerce_assistant.py didn't even call the platform's shared
behaviour_preamble() at all. This closes that gap centrally: AGENTIC_RULES lives in
core/skills/base.py and is opt-in via behaviour_preamble(agentic=True).
"""
from unittest.mock import AsyncMock, patch

import pytest

from core.skills.base import behaviour_preamble, AGENTIC_RULES
from core.skills.email_admin import EmailAdminSkill

TENANT = "off-the-hook"


def test_agentic_true_includes_agentic_rules():
    prompt = behaviour_preamble(agentic=True)
    assert "how-to/procedural question" in prompt
    assert "internal tool/function names" in prompt
    assert "unless a tool" in prompt
    assert "need_info" in prompt


def test_agentic_false_by_default_excludes_agentic_rules():
    prompt = behaviour_preamble()
    assert "how-to/procedural question" not in prompt
    assert AGENTIC_RULES not in prompt


def test_persona_still_prepended_with_agentic():
    prompt = behaviour_preamble(persona="Sound warm and casual.", agentic=True)
    assert prompt.startswith("Sound warm and casual.")
    assert "how-to/procedural question" in prompt


# ── Spot-check each migrated skill actually renders the shared rules ────────────

def test_commerce_assistant_prompt_has_agentic_rules():
    from core.skills.commerce_assistant import CommerceAssistantSkill
    skill = CommerceAssistantSkill()
    prompt = skill._system_prompt("off-the-hook", kb_context="")
    assert "how-to/procedural question" in prompt
    assert "internal tool/function names" in prompt


def test_commerce_assistant_booking_focused_prompt_has_agentic_rules():
    from core.skills.commerce_assistant import CommerceAssistantSkill
    skill = CommerceAssistantSkill()
    prompt = skill._system_prompt("off-the-hook", kb_context="", booking_focused=True)
    assert "how-to/procedural question" in prompt


def test_email_admin_prompt_has_agentic_rules():
    skill = EmailAdminSkill()
    prompt = skill._system("draft")
    assert "how-to/procedural question" in prompt


def test_clickup_admin_prompt_has_agentic_rules():
    from core.skills.clickup_admin import ClickUpAdminSkill
    skill = ClickUpAdminSkill()
    prompt = skill._system_prompt()
    assert "how-to/procedural question" in prompt


def test_email_admin_send_mode_prompt_confirms_before_sending():
    skill = EmailAdminSkill()
    prompt = skill._system("send")
    assert "confirm the exact" in prompt
    assert "wait for a clear 'yes'" in prompt


# ── email_admin's real-send completeness gate ────────────────────────────────────

@pytest.mark.asyncio
async def test_email_draft_rejects_malformed_address_when_sending():
    skill = EmailAdminSkill()
    creds = {"send_mode": "send"}
    with patch("core.skills.email_admin.service.send", new=AsyncMock()) as mock_send:
        result = await skill._dispatch(
            "email_draft", {"to": "not an email", "subject": "Hi", "body": "Body"}, TENANT, creds)
    assert result["status"] == "need_info"
    mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_email_draft_sends_with_a_valid_address():
    skill = EmailAdminSkill()
    creds = {"send_mode": "send"}
    with patch("core.skills.email_admin.service.send", new=AsyncMock(return_value={"sent": True})) as mock_send:
        result = await skill._dispatch(
            "email_draft", {"to": "client@example.com", "subject": "Hi", "body": "Body"}, TENANT, creds)
    assert result == {"sent": True}
    mock_send.assert_called_once()


@pytest.mark.asyncio
async def test_email_draft_in_draft_mode_ignores_address_validity():
    """Draft-mode is never a real send, so a malformed address there just makes an unsendable
    draft — the gate only applies to send_mode='send'."""
    skill = EmailAdminSkill()
    creds = {"send_mode": "draft"}
    with patch("core.skills.email_admin.service.save_draft",
               new=AsyncMock(return_value={"draft": True})) as mock_draft:
        result = await skill._dispatch(
            "email_draft", {"to": "not an email", "subject": "Hi", "body": "Body"}, TENANT, creds)
    assert result == {"draft": True}
    mock_draft.assert_called_once()
