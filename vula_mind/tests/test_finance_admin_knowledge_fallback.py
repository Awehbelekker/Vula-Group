"""Tests for finance_admin.py's lookup_finance_knowledge tool (2026-08-28): before this,
finance_admin had zero fallback for a GENERAL how-does-this-work finance question (e.g. "what's
a typical professional fee percentage") — every tool being a ledger lookup meant it declined
with "no financial records" even when a real answer existed in the shared training KB.
"""
from unittest.mock import AsyncMock, patch

import pytest

from core.skills.finance_admin import FinanceAdminSkill

TENANT = "digg-demo"


@pytest.mark.asyncio
async def test_lookup_finance_knowledge_queries_both_shared_kbs_and_merges_results():
    skill = FinanceAdminSkill()

    async def _fake_query(query, top_k=3, authoritative_only=True):
        return [{"filename": "professional_fees.md", "text": "Typical QS fees are 3-5%.",
                  "score": 0.8}]

    with patch("vula.ingestion.pipeline.VulaIngestionPipeline") as MockPipeline:
        MockPipeline.return_value.query = AsyncMock(side_effect=_fake_query)
        result = await skill._dispatch_finance_knowledge("professional fee percentage")

    assert result["found"] is True
    assert len(result["results"]) == 2  # one hit per shared KB (construction + business_basics)
    assert result["results"][0]["source"] == "construction_kb"
    assert result["results"][1]["source"] == "general_sa_business"


@pytest.mark.asyncio
async def test_lookup_finance_knowledge_returns_not_found_when_both_kbs_empty():
    skill = FinanceAdminSkill()
    with patch("vula.ingestion.pipeline.VulaIngestionPipeline") as MockPipeline:
        MockPipeline.return_value.query = AsyncMock(return_value=[])
        result = await skill._dispatch_finance_knowledge("something obscure")

    assert result["found"] is False


@pytest.mark.asyncio
async def test_lookup_finance_knowledge_empty_query_short_circuits_with_no_kb_call():
    skill = FinanceAdminSkill()
    with patch("vula.ingestion.pipeline.VulaIngestionPipeline") as MockPipeline:
        result = await skill._dispatch_finance_knowledge("")
    assert result["found"] is False
    MockPipeline.assert_not_called()


@pytest.mark.asyncio
async def test_lookup_finance_knowledge_degrades_cleanly_on_kb_error():
    skill = FinanceAdminSkill()
    with patch("vula.ingestion.pipeline.VulaIngestionPipeline") as MockPipeline:
        MockPipeline.return_value.query = AsyncMock(side_effect=Exception("qdrant down"))
        result = await skill._dispatch_finance_knowledge("what's a healthy profit margin")
    assert result["found"] is False


def test_lookup_finance_knowledge_tool_spec_present():
    from core.skills.finance_admin import TOOL_SPECS
    names = {t["function"]["name"] for t in TOOL_SPECS}
    assert "lookup_finance_knowledge" in names


@pytest.mark.asyncio
async def test_dispatch_routes_lookup_finance_knowledge_by_name():
    skill = FinanceAdminSkill()
    with patch.object(FinanceAdminSkill, "_dispatch_finance_knowledge",
                       new=AsyncMock(return_value={"found": True, "results": []})) as mocked:
        result = await skill._dispatch("lookup_finance_knowledge", {"query": "VAT basics"}, TENANT)
    mocked.assert_awaited_once_with("VAT basics")
    assert result["found"] is True
