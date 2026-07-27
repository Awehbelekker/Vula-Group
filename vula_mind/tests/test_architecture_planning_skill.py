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
