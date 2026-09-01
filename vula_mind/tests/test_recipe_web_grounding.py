"""suggest_recipe's web step must read real pages, not just search-result titles.

2026-09-01: the "live web inspiration" step passed only titles and URLs to the model. With no
page text to work from, the recipe body came entirely out of the model's own general knowledge
and the grounding was decorative — the exact failure mode this platform keeps having to design
against. It now fetches the actual page text (same _fetch_text the real web_search skill uses).
"""
from unittest.mock import AsyncMock, patch

import pytest

from core.skills.commerce_assistant import CommerceAssistantSkill

TENANT = "off-the-hook"


def _captured_prompt(mock_completion):
    return mock_completion.call_args.kwargs["messages"][0]["content"]


@pytest.mark.asyncio
async def test_recipe_prompt_carries_real_page_text_not_just_titles():
    skill = CommerceAssistantSkill()
    hits = [{"title": "Best Hake Recipe", "url": "https://example.com/hake"}]
    page = "Pan-fry the hake skin-side down in brown butter for four minutes, then rest."

    completion = AsyncMock(return_value=type("R", (), {
        "choices": [type("C", (), {"message": type("M", (), {"content": "Recipe!"})()})()]})())

    with patch("core.skills.web_search._ddg_search", AsyncMock(return_value=hits)), \
         patch("core.skills.web_search._fetch_text", AsyncMock(return_value=page)), \
         patch("vula.commerce.service.list_products", AsyncMock(return_value=[])), \
         patch("vula.ingestion.pipeline.VulaIngestionPipeline") as pipe, \
         patch("litellm.acompletion", completion):
        pipe.return_value.query = AsyncMock(return_value=[])
        await skill._exec_suggest_recipe(TENANT, {"dish": "hake"})

    prompt = _captured_prompt(completion)
    assert "brown butter" in prompt, "actual page text should reach the model"
    assert "https://example.com/hake" in prompt


@pytest.mark.asyncio
async def test_unfetchable_page_still_contributes_its_title():
    """A blocked/dead page shouldn't drop the result entirely — degrade to the old behaviour."""
    skill = CommerceAssistantSkill()
    hits = [{"title": "Best Hake Recipe", "url": "https://example.com/hake"}]

    completion = AsyncMock(return_value=type("R", (), {
        "choices": [type("C", (), {"message": type("M", (), {"content": "Recipe!"})()})()]})())

    with patch("core.skills.web_search._ddg_search", AsyncMock(return_value=hits)), \
         patch("core.skills.web_search._fetch_text", AsyncMock(return_value="")), \
         patch("vula.commerce.service.list_products", AsyncMock(return_value=[])), \
         patch("vula.ingestion.pipeline.VulaIngestionPipeline") as pipe, \
         patch("litellm.acompletion", completion):
        pipe.return_value.query = AsyncMock(return_value=[])
        await skill._exec_suggest_recipe(TENANT, {"dish": "hake"})

    assert "Best Hake Recipe" in _captured_prompt(completion)


@pytest.mark.asyncio
async def test_web_step_failing_entirely_does_not_break_the_recipe():
    skill = CommerceAssistantSkill()
    completion = AsyncMock(return_value=type("R", (), {
        "choices": [type("C", (), {"message": type("M", (), {"content": "Recipe!"})()})()]})())

    with patch("core.skills.web_search._ddg_search", AsyncMock(side_effect=RuntimeError("no net"))), \
         patch("vula.commerce.service.list_products", AsyncMock(return_value=[])), \
         patch("vula.ingestion.pipeline.VulaIngestionPipeline") as pipe, \
         patch("litellm.acompletion", completion):
        pipe.return_value.query = AsyncMock(return_value=[])
        out = await skill._exec_suggest_recipe(TENANT, {"dish": "hake"})

    assert out and "INSPIRATION ONLY" not in _captured_prompt(completion)


@pytest.mark.asyncio
async def test_tenant_own_recipe_kb_still_takes_priority():
    """The business's own recipe stays the thing being adapted — web text is inspiration only."""
    skill = CommerceAssistantSkill()
    completion = AsyncMock(return_value=type("R", (), {
        "choices": [type("C", (), {"message": type("M", (), {"content": "Recipe!"})()})()]})())

    with patch("core.skills.web_search._ddg_search", AsyncMock(return_value=[])), \
         patch("vula.commerce.service.list_products", AsyncMock(return_value=[])), \
         patch("vula.ingestion.pipeline.VulaIngestionPipeline") as pipe, \
         patch("litellm.acompletion", completion):
        pipe.return_value.query = AsyncMock(
            return_value=[{"text": "# Grilled Hake with Lemon Butter\nOur house recipe."}])
        await skill._exec_suggest_recipe(TENANT, {"dish": "hake"})

    prompt = _captured_prompt(completion)
    assert "Our house recipe." in prompt
    assert "keep it true to ours" in prompt
