"""Tests for commerce_assistant's research_product tool (2026-09-01) — lets a customer ask a
factual question about a product/fish type ("is snoek local", "is kingklip sustainable") and get
a grounded answer instead of nothing or a guess. Mirrors suggest_recipe's proven KB-first +
web-fallback grounding pattern: the business's own product records first (most trusted), then
the tenant KB, then a full web research pass — never invents facts, says plainly when nothing
reliable is found.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.skills.commerce_assistant import CommerceAssistantSkill
from core.skills.base import SkillOutput

TID = "off-the-hook"
CTX = {"tenant_id": TID, "session_id": "+27821234567", "customer_phone": "+27821234567"}


@pytest.fixture
def skill():
    return CommerceAssistantSkill()


def test_research_product_is_a_registered_tool():
    from core.skills.commerce_assistant import TOOL_SPECS
    names = [t["function"]["name"] for t in TOOL_SPECS]
    assert "research_product" in names


def test_research_product_requires_topic_arg():
    from core.skills.commerce_assistant import TOOL_SPECS
    spec = next(t for t in TOOL_SPECS if t["function"]["name"] == "research_product")
    assert "topic" in spec["function"]["parameters"]["required"]


@pytest.mark.asyncio
async def test_dispatch_routes_research_product(skill):
    with patch.object(skill, "_exec_research_product", new=AsyncMock(return_value={"found": True})) as mock_impl:
        result = await skill._dispatch_tool("research_product", {"topic": "is snoek local"}, CTX)
    mock_impl.assert_called_once()
    assert result == {"found": True}


@pytest.mark.asyncio
async def test_empty_topic_is_an_error(skill):
    result = await skill._exec_research_product(TID, {"topic": ""})
    assert "error" in result


# ── grounding priority: own product data first ────────────────────────────────────

@pytest.mark.asyncio
async def test_uses_own_product_data_when_available(skill):
    products = [{"name": "Snoek", "catch_source": "Line-caught, Cape West Coast",
                "fisherman_name": "Local skiboat fleet", "cooking_tips": None, "notes": None}]
    with (
        patch("core.skills.commerce_assistant.service.list_products", new=AsyncMock(return_value=products)),
        patch.object(skill, "_synthesize_research_answer",
                     new=AsyncMock(return_value={"found": True, "source": "our own records",
                                                 "answer": "Yes, our snoek is local — line-caught off the Cape West Coast."})) as mock_synth,
    ):
        result = await skill._exec_research_product(TID, {"topic": "is snoek local"})

    mock_synth.assert_called_once()
    grounding_arg = mock_synth.call_args[0][1]
    assert "Line-caught, Cape West Coast" in grounding_arg
    assert result["source"] == "our own records"


@pytest.mark.asyncio
async def test_own_data_match_is_case_and_substring_tolerant(skill):
    products = [{"name": "Hake Fillets", "catch_source": "Wild-caught, South Africa",
                "fisherman_name": None, "cooking_tips": None, "notes": None}]
    with (
        patch("core.skills.commerce_assistant.service.list_products", new=AsyncMock(return_value=products)),
        patch.object(skill, "_synthesize_research_answer", new=AsyncMock(return_value={"found": True})),
    ):
        await skill._exec_research_product(TID, {"topic": "where does your hake come from"})
        skill._synthesize_research_answer.assert_called_once()


# ── falls back to tenant KB when no own-product match ─────────────────────────────

@pytest.mark.asyncio
async def test_falls_back_to_tenant_kb_when_no_product_match(skill):
    with (
        patch("core.skills.commerce_assistant.service.list_products", new=AsyncMock(return_value=[])),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline") as mock_pipeline_cls,
        patch.object(skill, "_synthesize_research_answer",
                     new=AsyncMock(return_value={"found": True, "source": "our own records"})) as mock_synth,
    ):
        mock_pipeline_cls.return_value.query = AsyncMock(
            return_value=[{"filename": "sustainability.pdf", "text": "Kingklip is MSC-certified sustainable."}])
        await skill._exec_research_product(TID, {"topic": "is kingklip sustainable"})

    grounding_arg = mock_synth.call_args[0][1]
    assert "MSC-certified" in grounding_arg


# ── falls back to real web research when nothing of our own exists ────────────────

@pytest.mark.asyncio
async def test_falls_back_to_web_search_when_nothing_of_our_own(skill):
    with (
        patch("core.skills.commerce_assistant.service.list_products", new=AsyncMock(return_value=[])),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline") as mock_pipeline_cls,
        patch("core.skills.loader.get_skill") as mock_get_skill,
    ):
        mock_pipeline_cls.return_value.query = AsyncMock(return_value=[])
        mock_web_skill = AsyncMock(return_value=SkillOutput(
            answer="Yellowtail is a popular South African line fish, generally sustainably managed.",
            skill_name="web_search", confidence=0.6))
        mock_get_skill.return_value = mock_web_skill
        result = await skill._exec_research_product(TID, {"topic": "is yellowtail sustainable"})

    assert result["found"] is True
    assert result["source"] == "web"
    assert "line fish" in result["answer"]


@pytest.mark.asyncio
async def test_low_confidence_web_result_is_not_treated_as_found(skill):
    with (
        patch("core.skills.commerce_assistant.service.list_products", new=AsyncMock(return_value=[])),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline") as mock_pipeline_cls,
        patch("core.skills.loader.get_skill") as mock_get_skill,
    ):
        mock_pipeline_cls.return_value.query = AsyncMock(return_value=[])
        mock_get_skill.return_value = AsyncMock(return_value=SkillOutput(
            answer="I couldn't find live web results for that right now.",
            skill_name="web_search", confidence=0.2))
        result = await skill._exec_research_product(TID, {"topic": "something obscure"})

    assert result["found"] is False


@pytest.mark.asyncio
async def test_nothing_found_anywhere_declines_honestly_not_a_guess(skill):
    with (
        patch("core.skills.commerce_assistant.service.list_products", new=AsyncMock(return_value=[])),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline") as mock_pipeline_cls,
        patch("core.skills.loader.get_skill", side_effect=RuntimeError("web search unavailable")),
    ):
        mock_pipeline_cls.return_value.query = AsyncMock(return_value=[])
        result = await skill._exec_research_product(TID, {"topic": "something totally obscure"})

    assert result["found"] is False
    assert "team" in result["message"].lower()


# ── synthesis grounding discipline ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_synthesize_only_uses_given_grounding_text(skill):
    captured = {}

    async def fake_completion(*a, **kw):
        captured["messages"] = kw["messages"]
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content="Yes, line-caught locally."))]
        return resp

    with (
        patch("core.skills.commerce_assistant.resolve_generation_route",
              new=AsyncMock(return_value=("openrouter/test", "k", None))),
        patch("litellm.acompletion", new=fake_completion),
    ):
        result = await skill._synthesize_research_answer(
            "is snoek local", "Snoek: Line-caught, Cape West Coast", "our own records")

    assert result["found"] is True
    assert result["answer"] == "Yes, line-caught locally."
    user_msg = captured["messages"][-1]["content"]
    assert "Line-caught, Cape West Coast" in user_msg


@pytest.mark.asyncio
async def test_synthesize_falls_back_to_raw_grounding_on_llm_failure(skill):
    with (
        patch("core.skills.commerce_assistant.resolve_generation_route",
              new=AsyncMock(return_value=("openrouter/test", "k", None))),
        patch("litellm.acompletion", new=AsyncMock(side_effect=RuntimeError("no cloud"))),
    ):
        result = await skill._synthesize_research_answer(
            "is snoek local", "Snoek: Line-caught, Cape West Coast", "our own records")

    assert result["found"] is True
    assert "Line-caught" in result["answer"]  # deterministic fallback, never silence
