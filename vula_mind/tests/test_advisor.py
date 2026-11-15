"""The AI advisor (employees/advisor): a read-only thinking partner for the business's own people,
off until a business switches it on, with facts and ideas kept apart."""
from unittest.mock import AsyncMock, patch

import pytest

from config import settings
from core.skills import commerce_admin as ca
from core.skills import employees as em
from core.skills.base import SkillInput, tool_source

T = "deli"
ADVISOR = em.BY_ID["advisor"]
WRITES = {"add_expense", "create_invoice", "send_invoice", "record_payment", "update_stock", "send_broadcast",
          "create_manual_order", "update_order_status", "create_product", "staff_notice", "payment_link",
          "create_purchase_order", "send_payment_reminders", "credit_note", "create_booking"}


def _no_saved_settings():
    return patch.object(em, "_client", side_effect=RuntimeError("no table"))


def test_it_is_off_until_a_business_or_the_platform_switches_it_on():
    assert settings.advisor_mode_enabled is False
    with _no_saved_settings():
        s = em.settings_for(T)
        assert s["advisor"]["enabled"] is False and s["bookkeeper"]["enabled"] is True
        with patch.object(settings, "advisor_mode_enabled", True):
            assert em.settings_for(T)["advisor"]["enabled"] is True


def test_an_owner_can_switch_it_on_for_their_own_business():
    class Q:
        def select(self, *_a): return self
        def eq(self, *_a): return self
        def execute(self):
            return type("R", (), {"data": [{"employee": "advisor", "enabled": True, "instructions": "Be blunt."}]})()
    db = type("D", (), {"table": lambda self, n: Q()})()
    with patch.object(em, "_client", return_value=db):
        assert em.settings_for(T)["advisor"] == {"enabled": True, "instructions": "Be blunt."}


def test_it_can_look_but_never_change_anything():
    assert not (ADVISOR.tools & WRITES)
    for t in ADVISOR.tasks:
        assert not (t.tools & WRITES), t.id


def test_the_job_keeps_facts_and_ideas_under_separate_headings():
    for heading in ("*From your records*", "*Ideas (not facts)*", "*To check*"):
        assert heading in ADVISOR.job
    assert "never change anything" in ADVISOR.job and "Don't record a decision or a rule unless they ask" in ADVISOR.job


def test_thinking_aloud_goes_to_the_advisor_only_when_it_is_on():
    on = {e.id for e in em.TEAM}
    off = on - {"advisor"}
    q = "Give me the pros and cons of hiring a driver versus using a courier"
    assert em.is_advisor(em.pick(q, on)) and em.pick(q, off) is None          # off: the whole team, as before
    assert em.pick("Who owes us money?", on).id == "bookkeeper"               # a lookup is not a brainstorm
    assert em.pick("What should I reorder this week?", on).id == "operations"


def test_a_reply_that_looked_nothing_up_says_it_is_ideas():
    ideas = "You could run a midweek special, or partner with the gym next door."
    marked = em.mark_ideas(ideas, [])
    assert marked.startswith(em.IDEAS_NOT_RECORDS) and marked.endswith(ideas)
    assert em.mark_ideas(marked, []) == marked                                               # only once
    used = [tool_source("sales_summary", {"total": "R1,842.50"})]
    assert em.mark_ideas(ideas, used) == ideas                                               # it did look
    labelled = "*Ideas (not facts)*\n- a midweek special"
    assert em.mark_ideas(labelled, []) == labelled                                           # already labelled
    assert em.mark_ideas("", []) == ""


@pytest.mark.asyncio
async def test_the_advisors_reply_is_labelled_and_not_second_guessed_as_if_it_were_a_fact():
    skill = ca.CommerceAdminSkill()
    inp = SkillInput(question="Any ideas for a slow winter?", tenant_id=T, metadata={"caller_role": "owner"})
    with patch.object(ca, "_tools_for", return_value=[]), \
         patch.object(ca, "_pick_employee", return_value=([], ADVISOR, "\n\nYOUR JOB — AI advisor: …")), \
         patch.object(skill, "_system_prompt", return_value="sys"), \
         patch.object(skill, "_decisions_block", return_value=""), \
         patch.object(skill, "_agent_loop", AsyncMock(return_value="Run a soup-and-bread special on Tuesdays.")), \
         patch("core.verification.adversarial_check", AsyncMock()) as checker:
        out = await skill(inp)
    assert out.answer.startswith(em.IDEAS_NOT_RECORDS)
    assert out.verification["verifier"] == "advisor.ideas_labelled"
    checker.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_ordinary_admin_reply_is_unchanged():
    skill = ca.CommerceAdminSkill()
    inp = SkillInput(question="What were today's sales?", tenant_id=T, metadata={"caller_role": "owner"})
    with patch.object(ca, "_tools_for", return_value=[]), \
         patch.object(ca, "_pick_employee", return_value=([], em.BY_ID["bookkeeper"], "")), \
         patch.object(skill, "_system_prompt", return_value="sys"), \
         patch.object(skill, "_decisions_block", return_value=""), \
         patch.object(skill, "_agent_loop", AsyncMock(return_value="Which day did you mean?")):
        out = await skill.run(inp)
    assert out.answer == "Which day did you mean?" and out.verification is None


def test_a_knowledge_line_sends_thinking_aloud_to_the_advisor_only_when_it_is_on():
    import vula.api.whatsapp as wa
    q = "Help me think through our strategy for next year"
    with _no_saved_settings():
        assert wa._wants_advisor(T, q) is False
        with patch.object(settings, "advisor_mode_enabled", True):
            assert wa._wants_advisor(T, q) is True
            assert wa._wants_advisor(T, "what is the fire rating of the Affinity range?") is False
