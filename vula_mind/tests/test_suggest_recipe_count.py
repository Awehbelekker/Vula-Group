"""Tests for suggest_recipe's count support (2026-09-01) — real incident, off-the-hook: "Do you
have a 3 hake recipes" got back exactly one recipe, with no acknowledgement it fell short of
what was actually asked. See core/skills/commerce_assistant.py's _exec_suggest_recipe.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.skills.commerce_assistant import CommerceAssistantSkill, TOOL_SPECS

TID = "off-the-hook"


@pytest.fixture
def skill():
    return CommerceAssistantSkill()


def test_suggest_recipe_tool_spec_has_count_param():
    spec = next(t for t in TOOL_SPECS if t["function"]["name"] == "suggest_recipe")
    assert "count" in spec["function"]["parameters"]["properties"]


@pytest.mark.asyncio
async def test_count_defaults_to_one():
    skill = CommerceAssistantSkill()
    captured = {}

    async def fake_completion(*a, **kw):
        captured["prompt"] = kw["messages"][0]["content"]
        captured["max_tokens"] = kw["max_tokens"]
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content="Recipe 1: Braaied Hake"))]
        return resp

    with (
        patch("core.skills.commerce_assistant.service.list_products", new=AsyncMock(return_value=[])),
        patch("core.skills.commerce_assistant.resolve_generation_route",
              new=AsyncMock(return_value=("openrouter/test", "k", None))),
        patch("litellm.acompletion", new=fake_completion),
    ):
        await skill._exec_suggest_recipe(TID, {"dish": "hake"})

    assert "SHORT, practical South African recipe" in captured["prompt"]
    assert "3 DIFFERENT recipes" not in captured["prompt"]
    assert captured["max_tokens"] == 500


@pytest.mark.asyncio
async def test_count_three_asks_for_three_distinct_recipes():
    skill = CommerceAssistantSkill()
    captured = {}

    async def fake_completion(*a, **kw):
        captured["prompt"] = kw["messages"][0]["content"]
        captured["max_tokens"] = kw["max_tokens"]
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(
            content="1. Braaied Hake\n2. Hake Curry\n3. Hake Fish Cakes"))]
        return resp

    with (
        patch("core.skills.commerce_assistant.service.list_products", new=AsyncMock(return_value=[])),
        patch("core.skills.commerce_assistant.resolve_generation_route",
              new=AsyncMock(return_value=("openrouter/test", "k", None))),
        patch("litellm.acompletion", new=fake_completion),
    ):
        result = await skill._exec_suggest_recipe(TID, {"dish": "hake", "count": 3})

    assert "3 DIFFERENT recipes" in captured["prompt"]
    assert "numbered 1-3" in captured["prompt"]
    assert captured["max_tokens"] == 1500  # 500 * count, so 3 real recipes aren't truncated
    assert "Braaied Hake" in result["recipe"]
    assert "Hake Curry" in result["recipe"]
    assert "Hake Fish Cakes" in result["recipe"]


@pytest.mark.asyncio
async def test_count_is_capped_at_three():
    skill = CommerceAssistantSkill()
    captured = {}

    async def fake_completion(*a, **kw):
        captured["prompt"] = kw["messages"][0]["content"]
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content="recipes"))]
        return resp

    with (
        patch("core.skills.commerce_assistant.service.list_products", new=AsyncMock(return_value=[])),
        patch("core.skills.commerce_assistant.resolve_generation_route",
              new=AsyncMock(return_value=("openrouter/test", "k", None))),
        patch("litellm.acompletion", new=fake_completion),
    ):
        await skill._exec_suggest_recipe(TID, {"dish": "hake", "count": 10})

    assert "3 DIFFERENT recipes" in captured["prompt"]
    assert "numbered 1-3" in captured["prompt"]


@pytest.mark.asyncio
async def test_count_zero_or_negative_falls_back_to_one():
    skill = CommerceAssistantSkill()
    captured = {}

    async def fake_completion(*a, **kw):
        captured["prompt"] = kw["messages"][0]["content"]
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content="one recipe"))]
        return resp

    with (
        patch("core.skills.commerce_assistant.service.list_products", new=AsyncMock(return_value=[])),
        patch("core.skills.commerce_assistant.resolve_generation_route",
              new=AsyncMock(return_value=("openrouter/test", "k", None))),
        patch("litellm.acompletion", new=fake_completion),
    ):
        await skill._exec_suggest_recipe(TID, {"dish": "hake", "count": 0})

    assert "SHORT, practical South African recipe" in captured["prompt"]
