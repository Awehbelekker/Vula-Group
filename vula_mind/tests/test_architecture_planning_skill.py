"""Tests for core/skills/architecture_planning.py's prompt assembly — same fix as
reasoning.py (2026-07-27 DIGG bug): retrieved context must precede and be labelled
authoritative over raw conversation history, not just concatenated with no precedence rule."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.skills.architecture_planning import ArchitecturePlanningSkill
from core.skills.base import SkillInput


class _Msg:
    def __init__(self, content):
        self.content = content


class _Resp:
    def __init__(self, content):
        self.choices = [type("C", (), {"message": _Msg(content)})()]


def _pipeline_mock(chunks):
    mock_pipeline = MagicMock()
    mock_pipeline.query = AsyncMock(return_value=chunks)
    return mock_pipeline


@pytest.mark.asyncio
async def test_context_precedes_history_and_is_labelled_authoritative():
    captured = {}

    async def _fake_completion(*a, **kw):
        captured["messages"] = kw["messages"]
        return _Resp("the answer")

    chunks = [{"filename": "boq.pdf", "text": "White and Pastel colour groups", "score": 0.5}]

    with (
        patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=_pipeline_mock(chunks)),
        patch("litellm.acompletion", new=_fake_completion),
        patch("core.skills.architecture_planning.resolve_generation_route",
              new=AsyncMock(return_value=("ollama/test", None, "http://localhost:11434"))),
    ):
        inp = SkillInput(question="paint colours", tenant_id="digg-demo",
                         conversation_history="Vula AI: unrelated bathroom bizarre thread")
        await ArchitecturePlanningSkill().run(inp)

    user_msg = captured["messages"][1]["content"]
    assert user_msg.index("White and Pastel colour groups") < user_msg.index("bathroom bizarre thread")
    assert "authoritative" in user_msg.lower()
    assert "may be stale" in user_msg.lower()


@pytest.mark.asyncio
async def test_sources_carry_retrieved_text_for_verification_reuse():
    """Both tenant_kb and training_kb sources must carry chunk text — verification.py's
    context builder matches on "kb" as a substring of the source type, so tenant_kb/
    training_kb need real text just like reasoning.py's plain "kb" sources."""
    async def _fake_completion(*a, **kw):
        return _Resp("the answer")

    with (
        patch("vula.ingestion.pipeline.VulaIngestionPipeline",
              return_value=_pipeline_mock([{"filename": "boq.pdf", "text": "Bathroom Bizarre R1,234", "score": 0.5}])),
        patch("litellm.acompletion", new=_fake_completion),
        patch("core.skills.architecture_planning.resolve_generation_route",
              new=AsyncMock(return_value=("ollama/test", None, "http://localhost:11434"))),
    ):
        out = await ArchitecturePlanningSkill().run(SkillInput(question="q", tenant_id="digg-demo"))

    kb_sources = [s for s in out.sources if "kb" in s["type"]]
    assert kb_sources, "expected at least one kb-ish source"
    assert all(s["text"] == "Bathroom Bizarre R1,234" for s in kb_sources)


@pytest.mark.asyncio
async def test_no_context_omits_context_block():
    captured = {}

    async def _fake_completion(*a, **kw):
        captured["messages"] = kw["messages"]
        return _Resp("the answer")

    with (
        patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=_pipeline_mock([])),
        patch("litellm.acompletion", new=_fake_completion),
        patch("core.skills.architecture_planning.resolve_generation_route",
              new=AsyncMock(return_value=("ollama/test", None, "http://localhost:11434"))),
    ):
        inp = SkillInput(question="q", tenant_id="digg-demo", conversation_history="Vula AI: hi")
        await ArchitecturePlanningSkill().run(inp)

    user_msg = captured["messages"][1]["content"]
    assert "Context (authoritative" not in user_msg
    assert "Vula AI: hi" in user_msg


@pytest.mark.asyncio
async def test_no_context_appends_accuracy_caveat():
    """2026-08 accuracy audit: this skill is a domain-expert advisor (SACAP/JBCC/SANS
    knowledge), not a document-lookup skill, so it can't refuse outright when nothing is
    retrieved — confidence already quietly dropped (0.85->0.6), but that never reached the
    WhatsApp user. Fix: a visible caveat instead."""
    async def _fake_completion(*a, **kw):
        return _Resp("Typical retention on a JBCC contract is 5-10%.")

    with (
        patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=_pipeline_mock([])),
        patch("litellm.acompletion", new=_fake_completion),
        patch("core.skills.architecture_planning.resolve_generation_route",
              new=AsyncMock(return_value=("ollama/test", None, "http://localhost:11434"))),
    ):
        out = await ArchitecturePlanningSkill().run(
            SkillInput(question="typical retention on JBCC?", tenant_id="digg-demo"))

    assert "⚠️" in out.answer
    assert out.confidence == 0.6


@pytest.mark.asyncio
async def test_context_present_has_no_caveat():
    async def _fake_completion(*a, **kw):
        return _Resp("Per the filed BoQ, retention is 5%.")

    chunks = [{"filename": "boq.pdf", "text": "Retention: 5%", "score": 0.9}]
    with (
        patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=_pipeline_mock(chunks)),
        patch("litellm.acompletion", new=_fake_completion),
        patch("core.skills.architecture_planning.resolve_generation_route",
              new=AsyncMock(return_value=("ollama/test", None, "http://localhost:11434"))),
    ):
        out = await ArchitecturePlanningSkill().run(
            SkillInput(question="what's the retention?", tenant_id="digg-demo"))

    assert "⚠️" not in out.answer
    assert out.confidence == 0.85


# ── 2026-08-24 chat-accuracy audit: hard-decline guard for tenant-specific questions ──

def test_verification_policy_is_adversarial():
    assert ArchitecturePlanningSkill.verification_policy == "adversarial"


@pytest.mark.asyncio
async def test_declines_a_possessive_tenant_specific_question_with_no_tenant_docs():
    """"what's the retention on OUR Riverside contract" with no matching tenant document is
    asking about this practice's own real records — same hallucination class the R70,400
    fabrication guard exists to stop. Must decline BEFORE calling the LLM."""
    mock_completion = AsyncMock()
    with (
        patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=_pipeline_mock([])),
        patch("litellm.acompletion", new=mock_completion),
        patch("core.skills.architecture_planning.resolve_generation_route",
              new=AsyncMock(return_value=("ollama/test", None, "http://localhost:11434"))),
    ):
        out = await ArchitecturePlanningSkill().run(
            SkillInput(question="what's the retention on our Riverside contract?", tenant_id="digg-demo"))

    mock_completion.assert_not_awaited()  # no LLM call spent on a question we're declining
    assert "don't have a document on file" in out.answer
    assert out.confidence == 0.3


@pytest.mark.asyncio
async def test_general_domain_question_still_answers_from_training_kb_when_no_tenant_docs():
    """A GENERAL "what's a typical retention percentage" question must still be answered from
    this skill's real domain expertise, not declined just because 'retention' matches the
    marker regex — only a possessive, tenant-specific phrasing should decline."""
    async def _fake_completion(*a, **kw):
        return _Resp("Typical retention on a JBCC contract is 5-10%.")

    with (
        patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=_pipeline_mock([])),
        patch("litellm.acompletion", new=_fake_completion),
        patch("core.skills.architecture_planning.resolve_generation_route",
              new=AsyncMock(return_value=("ollama/test", None, "http://localhost:11434"))),
    ):
        out = await ArchitecturePlanningSkill().run(
            SkillInput(question="what is a typical retention percentage on a JBCC contract?",
                      tenant_id="digg-demo"))

    assert "don't have a document on file" not in out.answer
    assert "5-10%" in out.answer


@pytest.mark.asyncio
async def test_decline_guard_does_not_fire_when_tenant_docs_were_actually_retrieved():
    """A possessive tenant-specific question with real matching tenant docs must answer
    normally, not decline just because the question happens to be possessive."""
    async def _fake_completion(*a, **kw):
        return _Resp("Per the filed BoQ, retention on our Riverside contract is 5%.")

    chunks = [{"filename": "riverside_boq.pdf", "text": "Retention: 5%", "score": 0.9}]
    with (
        patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=_pipeline_mock(chunks)),
        patch("litellm.acompletion", new=_fake_completion),
        patch("core.skills.architecture_planning.resolve_generation_route",
              new=AsyncMock(return_value=("ollama/test", None, "http://localhost:11434"))),
    ):
        out = await ArchitecturePlanningSkill().run(
            SkillInput(question="what's the retention on our Riverside contract?", tenant_id="digg-demo"))

    assert "don't have a document on file" not in out.answer
    assert "5%" in out.answer
