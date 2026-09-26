"""Tests for email_admin.py's fabrication backstops, added 2026-09-26.

email_admin.py dispatches the exact same find_document/email_thread_summary tools as
commerce_admin.py, for the same class of money-shaped tenant data, but never had
commerce_admin.py's deterministic backstops (verification_policy, SkillOutput.sources,
wrong_arithmetic, unverified_prices) — see tests/test_unverified_prices.py and
tests/test_wrong_arithmetic.py for the same mechanism's original commerce_admin.py coverage,
which this file mirrors for email_admin.py. A knowledge-mode tenant's money question that
doesn't land the clean answer_supplier_history shortcut (see test_chat_followups_0923.py) falls
into the general tool loop, which is what these tests cover.
"""
from unittest.mock import AsyncMock, patch

import pytest

from core.skills.base import SkillInput
from core.skills.email_admin import EmailAdminSkill

TID = "digg-demo"

# A question that: (a) doesn't match looks_like_supplier_history_question (no named supplier/
# "invoices"/"materials" wording), so run() doesn't take the upfront shortcut, and (b) doesn't
# match CONTINUATION_INTENT_RE either, so answer_supplier_history_continuation returns None
# immediately with no DB call — falls straight through to the tool-calling loop.
QUESTION = "what did we pay for the site visit"


def _skill_with_creds():
    skill = EmailAdminSkill()
    return skill


@pytest.mark.asyncio
async def test_verification_policy_is_adversarial():
    assert EmailAdminSkill.verification_policy == "adversarial"


@pytest.mark.asyncio
async def test_run_replaces_answer_when_price_is_unverified():
    skill = _skill_with_creds()

    async def fake_loop(history, question, tenant_id, creds, phone="", sources=None):
        if sources is not None:
            sources.append({"type": "tool", "name": "find_document",
                            "text": "POS Account Sale — R942.00"})
        return "That invoice came to R1,234.56."

    with (
        patch("core.skills.email_admin.get_email_creds", return_value={"send_mode": "draft"}),
        patch.object(skill, "_loop", new=fake_loop),
    ):
        out = await skill.run(SkillInput(question=QUESTION, tenant_id=TID))

    assert "1,234.56" not in out.answer
    assert "couldn't confirm" in out.answer.lower()


@pytest.mark.asyncio
async def test_run_keeps_answer_when_price_is_verified():
    skill = _skill_with_creds()

    async def fake_loop(history, question, tenant_id, creds, phone="", sources=None):
        if sources is not None:
            sources.append({"type": "tool", "name": "find_document",
                            "text": "POS Account Sale — R942.00"})
        return "That invoice came to R942.00."

    with (
        patch("core.skills.email_admin.get_email_creds", return_value={"send_mode": "draft"}),
        patch.object(skill, "_loop", new=fake_loop),
    ):
        out = await skill.run(SkillInput(question=QUESTION, tenant_id=TID))

    assert "R942.00" in out.answer
    assert "couldn't confirm" not in out.answer.lower()


@pytest.mark.asyncio
async def test_run_corrects_wrong_arithmetic():
    skill = _skill_with_creds()

    async def fake_loop(history, question, tenant_id, creds, phone="", sources=None):
        return "11.8 x 18.2 = 215.56"

    with (
        patch("core.skills.email_admin.get_email_creds", return_value={"send_mode": "draft"}),
        patch.object(skill, "_loop", new=fake_loop),
    ):
        out = await skill.run(SkillInput(question=QUESTION, tenant_id=TID))

    assert "correct my own maths" in out.answer
    assert out.confidence == 0.3


@pytest.mark.asyncio
async def test_run_leaves_correct_arithmetic_untouched():
    skill = _skill_with_creds()

    async def fake_loop(history, question, tenant_id, creds, phone="", sources=None):
        return "11.8 x 18.2 = 214.76"

    with (
        patch("core.skills.email_admin.get_email_creds", return_value={"send_mode": "draft"}),
        patch.object(skill, "_loop", new=fake_loop),
    ):
        out = await skill.run(SkillInput(question=QUESTION, tenant_id=TID))

    assert "correct my own maths" not in out.answer
    assert out.confidence == 0.8


@pytest.mark.asyncio
async def test_sources_are_populated_and_passed_through():
    skill = _skill_with_creds()

    async def fake_loop(history, question, tenant_id, creds, phone="", sources=None):
        if sources is not None:
            sources.append({"type": "tool", "name": "find_document", "text": "R100.00"})
        return "That's R100.00."

    with (
        patch("core.skills.email_admin.get_email_creds", return_value={"send_mode": "draft"}),
        patch.object(skill, "_loop", new=fake_loop),
    ):
        out = await skill.run(SkillInput(question=QUESTION, tenant_id=TID))

    assert out.sources == [{"type": "tool", "name": "find_document", "text": "R100.00"}]


@pytest.mark.asyncio
async def test_loop_appends_tool_source_on_dispatch():
    """_loop() itself appends a tool_source() entry after a real _dispatch call, not just when
    the caller happens to populate it manually (the above tests stub _loop entirely) — this
    confirms the actual wiring inside _loop, mirroring commerce_admin.py's _agent_loop."""
    skill = EmailAdminSkill()
    sources: list = []

    class _Msg:
        content = ""
        tool_calls = None

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    async def fake_complete_local_first(route, **kwargs):
        return _Resp(), route

    with (
        patch("core.skills.email_admin.resolve_generation_route", new=AsyncMock(return_value="r")),
        patch("core.skills.email_admin.complete_local_first", new=fake_complete_local_first),
        patch.object(skill, "_inline",
                     side_effect=[("find_document", {"query": "x"}), None, None, None]),
        patch.object(skill, "_dispatch", new=AsyncMock(return_value={"status": "not_found_filed"})),
    ):
        await skill._loop("", "find the x invoice", TID, {"send_mode": "draft"}, sources=sources)

    assert sources == [{"type": "tool", "name": "find_document",
                        "text": '{"status": "not_found_filed"}'[:900]}]
