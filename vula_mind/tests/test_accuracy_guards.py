"""Accuracy guards (12 Nov 2026): a business's own facts are never answered from general
knowledge, a short flat statement with documents behind it gets checked, a follow-up is routed
with the previous message in view, and every answer records what it rested on."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import settings
from core import verification
from core.hrm import orchestrator as orch
from core.skills import base
from core.skills.base import SkillInput
from tests.fake_supabase import FakeSupabase
from vula import route_memory
from vula.commerce import service

T = "deli"


# ── a business's own facts ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("q", [
    "what time do you open on Saturday?",
    "Do you deliver to Paarl?",
    "what are your opening hours",
    "where are you located?",
    "what's your returns policy?",
    "do you accept card?",
    "what is our delivery fee to Stellenbosch",
    "is the shop open tomorrow",
    "lewer julle af in Paarl?",
    "what's your phone number",
])
def test_questions_only_the_business_can_answer_are_recognised(q):
    assert base.looks_like_business_specific_question(q), q


@pytest.mark.parametrize("q", [
    "what does a typical returns policy say in South Africa?",      # general, no "you/our"
    "how long does concrete take to cure?",
    "what is the VAT rate?",
    "explain the difference between a quote and an estimate",
    "can I colour a cast iron fireplace?",
    "write a thank-you message for a customer",
    "",
])
def test_general_questions_are_not_declined(q):
    assert not base.looks_like_business_specific_question(q), q


def _reasoning(question, role=None, kb_chunks=None):
    from core.skills.reasoning import ReasoningSkill
    pipeline = MagicMock()
    pipeline.query = AsyncMock(return_value=kb_chunks or [])
    web = AsyncMock()
    return ReasoningSkill(), pipeline, web, SkillInput(
        question=question, tenant_id=T, metadata={"caller_role": role, "customer_phone": "27820000001"})


@pytest.mark.asyncio
async def test_a_business_fact_with_nothing_on_file_is_declined_without_a_model_or_web_call():
    skill, pipeline, web, inp = _reasoning("Do you deliver to Paarl?")
    with patch.object(settings, "strict_tenant_grounding", True), \
         patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=pipeline), \
         patch("core.skills.loader.get_skill", return_value=web), \
         patch("litellm.acompletion", new=AsyncMock()) as llm, patch("vula.kb_learning.record_turn"):
        out = await skill.run(inp)
    assert out.answer == base.BUSINESS_FACT_NOT_ON_FILE_CUSTOMER
    assert out.confidence < 0.4                      # below the hand-to-a-person floor
    llm.assert_not_awaited()
    web.assert_not_awaited()
    assert pipeline.query.await_count == 1           # only the business's own KB was searched


@pytest.mark.asyncio
async def test_the_team_is_told_how_to_give_vula_the_answer():
    skill, pipeline, web, inp = _reasoning("what are our opening hours?", role="owner")
    with patch.object(settings, "strict_tenant_grounding", True), \
         patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=pipeline), \
         patch("vula.kb_learning.record_turn"):
        out = await skill.run(inp)
    assert out.answer == base.BUSINESS_FACT_NOT_ON_FILE_TEAM and "remember that we" in out.answer


@pytest.mark.asyncio
async def test_off_by_default_the_old_path_runs():
    assert settings.strict_tenant_grounding is False
    skill, pipeline, web, inp = _reasoning("Do you deliver to Paarl?")
    web.return_value = MagicMock(success=False, confidence=0.0, answer="")
    with patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=pipeline), \
         patch("core.skills.loader.get_skill", return_value=web), \
         patch("vula.kb_learning.record_turn"), \
         patch("core.llm_router.resolve_generation_route", AsyncMock(side_effect=RuntimeError("stop here"))):
        out = await skill(inp)
    assert out.answer != base.BUSINESS_FACT_NOT_ON_FILE_CUSTOMER
    assert pipeline.query.await_count > 1            # it went on to the shared knowledge bases


# ── short answers ───────────────────────────────────────────────────────────────────────────

KB = [{"type": "kb", "filename": "delivery.pdf", "text": "We deliver to Cape Town CBD only."}]


def test_a_short_flat_statement_with_documents_behind_it_is_checked():
    answer = "Yes, we deliver to Paarl every day."
    assert verification.nothing_to_check(answer, KB) == "no_figures_short"          # off by default
    with patch.object(settings, "verify_short_assertions", True):
        assert verification.nothing_to_check(answer, KB) == ""                       # → run the checker
        assert verification.nothing_to_check(answer, []) == "no_figures_short"       # nothing to check against
        assert verification.nothing_to_check("Thanks!", KB) == "no_figures_short"    # an acknowledgement
        assert verification.nothing_to_check("Which branch do you mean?", KB) == "no_figures_short"
        tool = [{"type": "tool", "name": "sales_summary", "text": "…"}]
        assert verification.nothing_to_check(answer, tool) == "no_figures_short"     # tools: unchanged


# ── what an answer rested on ────────────────────────────────────────────────────────────────

def test_grounding_label_and_source_names():
    assert base.grounding_of([]) == "none" and base.grounding_of(None) == "none"
    assert base.grounding_of(KB) == "kb"
    assert base.grounding_of(KB + [{"type": "tool", "name": "customer_lookup", "text": "x"}]) == "records"
    assert base.grounding_of([{"type": "training_kb", "filename": "sans.pdf"}]) == "shared"
    assert base.grounding_of([{"type": "web", "text": "x"}]) == "web"
    names = base.source_names(KB + [{"type": "tool", "name": "customer_lookup", "text": "secret words"}])
    assert names == "delivery.pdf; customer_lookup" and "secret" not in names


@pytest.mark.asyncio
async def test_every_answer_records_its_grounding_and_sources_on_the_turn():
    from core.skills.base import BaseSkill, SkillOutput
    from vula import turns

    class Answers(BaseSkill):
        name = "reasoning"

        async def run(self, inp):
            return SkillOutput(answer="We deliver to the CBD.", skill_name=self.name, sources=list(KB))

    saved = {}
    db = MagicMock(**{"table.return_value.insert.side_effect": lambda row: saved.update(row) or MagicMock()})
    with patch.object(turns, "_client", return_value=db), patch("core.verification.apply", AsyncMock()):
        await turns.run(Answers()(SkillInput(question="where do you deliver?", tenant_id=T)),
                        tenant_id=T, phone="27820000001", kind="text", text="where do you deliver?")
    step = next(s for s in saved["steps"] if s["step"] == "skill")
    assert step["grounding"] == "kb" and step["source_names"] == "delivery.pdf"
    assert "We deliver to Cape Town CBD only." not in str(saved["steps"])          # names, never the text


# ── follow-ups ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _fresh_route_memory():
    route_memory.clear()
    yield
    route_memory.clear()


def test_the_classifier_is_told_what_was_asked_last_and_who_answered():
    o = orch.HRMOrchestrator.__new__(orch.HRMOrchestrator)
    bare = o._classifier_prompt("and for next week?")
    assert "Context:" not in bare
    prev = {"skill": "finance_admin", "question": "what did we spend on fuel this week?"}
    with orch.route_context(prev):
        assert "Context:" not in o._classifier_prompt("and for next week?")           # off by default
    with patch.object(settings, "follow_up_routing_enabled", True):
        with orch.route_context(prev):
            told = o._classifier_prompt("and for next week?")
        assert "what did we spend on fuel this week?" in told and "pick 'finance_admin'" in told
        assert "If it is a new subject, ignore the context" in told
        with orch.route_context({"skill": "not_a_skill", "question": "x"}):
            assert "Context:" not in o._classifier_prompt("hello")                    # unknown skill: ignored
    assert "Context:" not in o._classifier_prompt("and for next week?")               # nothing left behind


def test_the_last_skill_is_remembered_for_the_person_who_asked():
    route_memory.remember(T, "27821112222", "finance_admin", "what did we spend on fuel?", sticky=True)
    route_memory.remember(T, "27821112222", "reasoning", "thanks, and what is VAT?")
    assert route_memory.sticky(T, "27821112222") == "finance_admin"       # a follow-up still returns there
    assert route_memory.latest(T, "27821112222") == {"skill": "reasoning", "question": "thanks, and what is VAT?"}
    assert route_memory.sticky(T, "27820000009") is None and route_memory.latest("other", "27821112222") is None


def test_it_survives_a_restart_when_switched_on():
    db = FakeSupabase({"commerce_conversation_sessions": [
        {"id": "s1", "tenant_id": T, "session_key": "admin:27821112222"},
        {"id": "s2", "tenant_id": "other", "session_key": "admin:27821112222"}]})
    with patch.object(service, "_client", return_value=db), patch.object(settings, "follow_up_routing_enabled", True):
        route_memory._write(T, "27821112222", "finance_admin")
        assert db.rows["commerce_conversation_sessions"][0]["last_skill"] == "finance_admin"
        assert "last_skill" not in db.rows["commerce_conversation_sessions"][1]       # another business untouched
        route_memory.clear()                                                          # "the restart"
        assert route_memory.sticky(T, "27821112222", allowed={"finance_admin"}) == "finance_admin"
        route_memory.clear()
        assert route_memory.sticky(T, "27821112222", allowed={"email_admin"}) is None  # not on the caller's list
        route_memory.clear()
        db.rows["commerce_conversation_sessions"][0]["last_skill_at"] = "2026-01-01T00:00:00+00:00"
        assert route_memory.sticky(T, "27821112222", allowed={"finance_admin"}) is None  # too long ago


def test_without_the_switch_nothing_is_read_from_or_written_to_the_database():
    db = MagicMock()
    with patch.object(service, "_client", return_value=db):
        route_memory.remember(T, "27821112222", "finance_admin", "q", sticky=True)
        route_memory.clear()
        assert route_memory.sticky(T, "27821112222") is None
    db.table.assert_not_called()


# ── the quality numbers ─────────────────────────────────────────────────────────────────────

def _turn(grounding=None, confidence=0.8, reply="Here you go.", error=None):
    step = {"step": "skill", "name": "reasoning", "confidence": confidence}
    if grounding:
        step["grounding"] = grounding
    if error:
        step["error"] = error
    return {"steps": [{"step": "handler", "name": "x"}, step], "replies": [{"kind": "text", "text": reply}]}


def test_quality_counts_what_answers_rested_on_and_what_vula_was_unsure_of():
    from vula import quality
    turns = [
        _turn("records"), _turn("kb"), _turn("none"), _turn("web"),
        _turn(None),                                           # recorded before grounding existed
        _turn("none", confidence=0.3),                         # low confidence
        _turn("kb", reply="I couldn't find that in your documents."),   # said so
        {"steps": [{"step": "handler", "name": "button"}], "replies": []},     # not an answer
        _turn("kb", error="Timeout"),                          # a failed skill isn't an answer
    ]
    assert quality.summarise(turns) == {"answers": 7, "with_grounding_known": 6, "backed": 3, "unsure": 2}


def test_weekly_quality_is_counts_only_and_scoped_to_the_business():
    from datetime import datetime, timezone
    from vula import quality
    now = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
    recent, old = "2026-11-10T08:00:00+00:00", "2026-10-01T08:00:00+00:00"
    db = FakeSupabase({
        "vula_turns": [{"tenant_id": T, "started_at": recent, "text": "private words", **_turn("records")},
                       {"tenant_id": T, "started_at": recent, "text": "x", **_turn("none")},
                       {"tenant_id": T, "started_at": old, "text": "x", **_turn("records")},
                       {"tenant_id": "other", "started_at": recent, "text": "x", **_turn("records")}],
        "vula_reply_feedback": [{"id": 1, "tenant_id": T, "rating": "down", "created_at": recent},
                                {"id": 2, "tenant_id": T, "rating": "up", "created_at": recent}],
        "vula_kb_turns": [{"id": 3, "tenant_id": T, "feedback": "wrong", "feedback_at": recent}],
        "vula_escalations": [{"id": 4, "tenant_id": T, "created_at": recent},
                             {"id": 5, "tenant_id": "other", "created_at": recent}],
    })
    with patch.object(service, "_client", return_value=db):
        out = quality.weekly(T, days=7, now=now)
    assert out["answers"] == 2
    assert out["backed"] == {"count": 1, "of": 2, "pct": 50}
    assert out["corrected"] == {"count": 2, "pct": 100} and out["handed_on"] == {"count": 1}
    assert "private words" not in str(out)


def test_a_part_that_cannot_be_read_is_unknown_not_zero():
    from vula import quality
    db = FakeSupabase({"vula_turns": []})
    db.fail |= {"vula_turns", "vula_escalations"}
    with patch.object(service, "_client", return_value=db):
        out = quality.weekly(T)
    assert out["answers"] is None and out["backed"] is None and out["handed_on"] is None
    assert out["corrected"] == {"count": 0, "pct": None}
