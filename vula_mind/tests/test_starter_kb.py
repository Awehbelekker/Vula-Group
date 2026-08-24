"""Tests for vula/commerce/starter_kb.py — the structured starter KB seeded per business_type
at signup. Mirrors page_copy.py's established test discipline: never invent specific facts,
malformed/empty LLM output degrades gracefully, everything is best-effort (never raises).
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.commerce import starter_kb

TID = "test-tenant"


def _resp(content):
    msg = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=msg)
    return SimpleNamespace(choices=[choice])


async def _fake_route(*a, **kw):
    return ("openrouter/test", "k", None)


@pytest.mark.asyncio
async def test_generate_starter_kb_returns_one_doc_per_slot():
    async def fake_completion(*a, **kw):
        return _resp("Some drafted content about [delivery areas].")

    with (
        patch("core.llm_router.resolve_generation_route", new=_fake_route),
        patch("litellm.acompletion", new=fake_completion),
    ):
        docs = await starter_kb.generate_starter_kb(TID, "food")

    expected_slots = starter_kb.STARTER_SLOTS["food"]
    assert len(docs) == len(expected_slots)
    for doc in docs:
        assert doc["content"]
        assert doc["category"] in {s["category"] for s in expected_slots}
        assert doc["filename"].startswith("starter_")


@pytest.mark.asyncio
async def test_generate_starter_kb_falls_back_to_other_for_unknown_business_type():
    async def fake_completion(*a, **kw):
        return _resp("content")

    with (
        patch("core.llm_router.resolve_generation_route", new=_fake_route),
        patch("litellm.acompletion", new=fake_completion),
    ):
        docs = await starter_kb.generate_starter_kb(TID, "not_a_real_business_type")

    assert len(docs) == len(starter_kb.STARTER_SLOTS["other"])


@pytest.mark.asyncio
async def test_generate_starter_kb_skips_empty_llm_response_for_that_slot():
    call_count = {"n": 0}

    async def fake_completion(*a, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _resp("")  # empty — should be skipped
        return _resp("real content")

    with (
        patch("core.llm_router.resolve_generation_route", new=_fake_route),
        patch("litellm.acompletion", new=fake_completion),
    ):
        docs = await starter_kb.generate_starter_kb(TID, "food")

    # One slot's response was empty and skipped — fewer docs than slots.
    assert len(docs) == len(starter_kb.STARTER_SLOTS["food"]) - 1


@pytest.mark.asyncio
async def test_generate_starter_kb_never_raises_on_llm_failure():
    async def fake_completion(*a, **kw):
        raise RuntimeError("model unavailable")

    with (
        patch("core.llm_router.resolve_generation_route", new=_fake_route),
        patch("litellm.acompletion", new=fake_completion),
    ):
        docs = await starter_kb.generate_starter_kb(TID, "food")

    assert docs == []


@pytest.mark.asyncio
async def test_generate_starter_kb_never_raises_when_route_resolution_fails():
    with patch("core.llm_router.resolve_generation_route", side_effect=RuntimeError("no route")):
        docs = await starter_kb.generate_starter_kb(TID, "food")

    assert docs == []


@pytest.mark.asyncio
async def test_system_prompt_forbids_inventing_specific_facts():
    """Static check that the guardrail instruction is actually present — the real 'never
    invent contact facts' enforcement in page_copy.py works via a post-hoc override; this
    module has no such override (starter content isn't merged against a template with known
    real-data fields), so the prompt instruction IS the only guardrail — must be present."""
    assert "never invent specific facts" in starter_kb._SYSTEM_PROMPT.lower()
    assert "[square brackets]" in starter_kb._SYSTEM_PROMPT or "placeholder" in starter_kb._SYSTEM_PROMPT.lower()


# ── seed_starter_kb: generation + ingestion ───────────────────────────────────────

@pytest.mark.asyncio
async def test_seed_starter_kb_ingests_each_generated_doc_with_category_and_starter_source_type():
    fake_docs = [
        {"category": "Booking Policy", "topic": "t1", "filename": "starter_booking_policy.md",
         "content": "content 1"},
        {"category": "General Document", "topic": "t2", "filename": "starter_general_document.md",
         "content": "content 2"},
    ]
    ingested = []

    class _FakePipeline:
        def __init__(self, tenant_id):
            self.tenant_id = tenant_id

        async def ingest_text(self, content, filename, doc_id=None, source_type="document", category=None):
            ingested.append({"content": content, "filename": filename,
                             "source_type": source_type, "category": category})
            return SimpleNamespace(status="success")

    with (
        patch("vula.commerce.starter_kb.generate_starter_kb", new=AsyncMock(return_value=fake_docs)),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline", new=_FakePipeline),
    ):
        stored = await starter_kb.seed_starter_kb(TID, "food")

    assert stored == 2
    assert all(i["source_type"] == "starter" for i in ingested)
    assert {i["category"] for i in ingested} == {"Booking Policy", "General Document"}


@pytest.mark.asyncio
async def test_seed_starter_kb_returns_zero_when_generation_produces_nothing():
    with patch("vula.commerce.starter_kb.generate_starter_kb", new=AsyncMock(return_value=[])):
        stored = await starter_kb.seed_starter_kb(TID, "food")
    assert stored == 0


@pytest.mark.asyncio
async def test_seed_starter_kb_never_raises_on_ingestion_failure():
    with (
        patch("vula.commerce.starter_kb.generate_starter_kb",
              new=AsyncMock(return_value=[{"category": "General Document", "topic": "t",
                                            "filename": "f.md", "content": "c"}])),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline", side_effect=RuntimeError("qdrant down")),
    ):
        stored = await starter_kb.seed_starter_kb(TID, "food")
    assert stored == 0
