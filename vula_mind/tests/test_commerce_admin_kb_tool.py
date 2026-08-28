"""Tests for lookup_business_info (2026-08-27) — the real fix for a confirmed live incident:
commerce_admin.py had NO path to the tenant's own knowledge base at all, for either owner/staff
or sales_rep. A gerflor sales rep asked "what colours do we have" and, with no KB tool
available, the model reached for competitor_check (a generic, unscoped web search) and answered
with unrelated paint-brand results. This tool gives both roles a real path to the tenant's own
KB instead, mirroring commerce_assistant.py's proven _retrieve_kb exactly.

Also covers the accompanying competitor_check query-template hardening (tenant business_type/
display_name injected so a generic phrase drifts less easily into the wrong industry)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.skills.commerce_admin as ca
from core.skills.commerce_admin import CommerceAdminSkill

TID = "gerflor"


@pytest.fixture
def skill():
    return CommerceAdminSkill()


# ── tool availability ───────────────────────────────────────────────────────────

def test_lookup_business_info_available_to_owner(monkeypatch):
    monkeypatch.setattr("vula.api.tenants.enabled_modules", lambda tid: ["orders"])
    tools = ca._tools_for(TID, role=None)
    names = {t["function"]["name"] for t in tools}
    assert "lookup_business_info" in names


def test_lookup_business_info_available_to_sales_rep():
    tools = ca._tools_for(TID, role="sales_rep")
    names = {t["function"]["name"] for t in tools}
    assert "lookup_business_info" in names


def test_lookup_business_info_in_all_tool_specs():
    names = {t["function"]["name"] for t in ca._ALL_TOOL_SPECS}
    assert "lookup_business_info" in names


def test_lookup_business_info_not_module_gated(monkeypatch):
    # Present regardless of which modules are configured — read-only/low-risk, never gated.
    monkeypatch.setattr("vula.api.tenants.enabled_modules", lambda tid: ["invoices"])
    tools = ca._tools_for(TID, role=None)
    names = {t["function"]["name"] for t in tools}
    assert "lookup_business_info" in names


# ── dispatch ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_lookup_business_info_returns_found_results(skill):
    chunks = [{"filename": "products.html", "text": "Gerflor Mipolam comes in 40 stock colours."}]
    with patch("vula.ingestion.pipeline.VulaIngestionPipeline") as mock_pipeline_cls:
        mock_pipeline_cls.return_value.query = AsyncMock(return_value=chunks)
        result = await skill._lookup_business_info(TID, {"query": "what colours do we have"})
    assert result["found"] is True
    assert result["source_kb"] == "tenant"
    assert result["results"][0]["source"] == "products.html"
    assert "40 stock colours" in result["results"][0]["text"]


@pytest.mark.asyncio
async def test_lookup_business_info_empty_kb_returns_found_false(skill):
    with patch("vula.ingestion.pipeline.VulaIngestionPipeline") as mock_pipeline_cls:
        mock_pipeline_cls.return_value.query = AsyncMock(return_value=[])
        result = await skill._lookup_business_info(TID, {"query": "what colours do we have"})
    assert result == {"found": False, "message": "Nothing in the knowledge base matches that yet."}


# 2026-08-28: real incident, gerflor — a commerce-mode owner asking a generic SA small-business
# "how do I..." question (VAT, HR, etc.) previously got answered from raw ungrounded LLM
# parametric knowledge. lookup_business_info now falls back to the shared business_basics KB
# (vula/training/business_content.py) when the tenant's own KB has nothing — mirrors
# architecture_planning.py's already-proven "tenant KB + shared training KB" pattern.

@pytest.mark.asyncio
async def test_lookup_business_info_falls_back_to_shared_kb_when_tenant_kb_empty(skill):
    shared_chunks = [{"filename": "vat_basics.md", "text": "VAT registration is compulsory once turnover exceeds R1 million."}]
    call_tenant_ids = []

    def _pipeline_factory(tenant_id):
        call_tenant_ids.append(tenant_id)
        m = MagicMock()
        m.query = AsyncMock(return_value=[] if tenant_id == TID else shared_chunks)
        return m

    with patch("vula.ingestion.pipeline.VulaIngestionPipeline", side_effect=_pipeline_factory):
        result = await skill._lookup_business_info(TID, {"query": "how do I register for VAT"})

    assert result["found"] is True
    assert result["source_kb"] == "general_sa_business"
    assert result["results"][0]["source"] == "vat_basics.md"
    assert "R1 million" in result["results"][0]["text"]
    assert call_tenant_ids == [TID, "business_basics"]  # tenant tried first, shared second


@pytest.mark.asyncio
async def test_lookup_business_info_prefers_tenant_kb_over_shared(skill):
    tenant_chunks = [{"filename": "products.html", "text": "Real tenant content."}]
    shared_pipeline_called = {"n": 0}

    def _pipeline_factory(tenant_id):
        m = MagicMock()
        if tenant_id == "business_basics":
            shared_pipeline_called["n"] += 1
        m.query = AsyncMock(return_value=tenant_chunks)
        return m

    with patch("vula.ingestion.pipeline.VulaIngestionPipeline", side_effect=_pipeline_factory):
        result = await skill._lookup_business_info(TID, {"query": "what do we sell"})

    assert result["source_kb"] == "tenant"
    assert shared_pipeline_called["n"] == 0  # shared KB never even instantiated


@pytest.mark.asyncio
async def test_lookup_business_info_both_kbs_empty_returns_found_false(skill):
    with patch("vula.ingestion.pipeline.VulaIngestionPipeline") as mock_pipeline_cls:
        mock_pipeline_cls.return_value.query = AsyncMock(return_value=[])
        result = await skill._lookup_business_info(TID, {"query": "something obscure"})
    assert result == {"found": False, "message": "Nothing in the knowledge base matches that yet."}


@pytest.mark.asyncio
async def test_lookup_business_info_shared_kb_failure_degrades_to_found_false(skill):
    def _pipeline_factory(tenant_id):
        m = MagicMock()
        if tenant_id == "business_basics":
            m.query = AsyncMock(side_effect=RuntimeError("qdrant down"))
        else:
            m.query = AsyncMock(return_value=[])
        return m

    with patch("vula.ingestion.pipeline.VulaIngestionPipeline", side_effect=_pipeline_factory):
        result = await skill._lookup_business_info(TID, {"query": "how do I register for VAT"})

    assert result == {"found": False, "message": "Nothing in the knowledge base matches that yet."}


@pytest.mark.asyncio
async def test_lookup_business_info_retrieval_failure_degrades_gracefully(skill):
    with patch("vula.ingestion.pipeline.VulaIngestionPipeline") as mock_pipeline_cls:
        mock_pipeline_cls.return_value.query = AsyncMock(side_effect=RuntimeError("qdrant down"))
        result = await skill._lookup_business_info(TID, {"query": "what colours do we have"})
    assert "error" in result


@pytest.mark.asyncio
async def test_lookup_business_info_empty_query_is_an_error(skill):
    result = await skill._lookup_business_info(TID, {"query": ""})
    assert "error" in result


@pytest.mark.asyncio
async def test_dispatch_routes_lookup_business_info(skill):
    with patch.object(skill, "_lookup_business_info", new=AsyncMock(return_value={"found": True})) as mock_impl:
        result = await skill._dispatch_tool("lookup_business_info", {"query": "colours"}, {"tenant_id": TID})
    mock_impl.assert_called_once()
    assert result == {"found": True}


# ── competitor_check query-template hardening ────────────────────────────────────

@pytest.mark.asyncio
async def test_competitor_check_injects_tenant_business_context(skill):
    with (
        patch("vula.api.tenants.get_config",
              return_value={"business_type": "flooring", "display_name": "Gerflor"}),
        patch("core.skills.web_search._ddg_search", new=AsyncMock(return_value=[])) as mock_search,
    ):
        await skill._competitor_check(TID, {"query": "colour range"}, {})
    called_query = mock_search.call_args.args[0]
    assert "flooring" in called_query
    assert "Gerflor" in called_query
    assert "colour range" in called_query


@pytest.mark.asyncio
async def test_competitor_check_falls_back_cleanly_with_no_tenant_context(skill):
    with (
        patch("vula.api.tenants.get_config", return_value={}),
        patch("core.skills.web_search._ddg_search", new=AsyncMock(return_value=[])) as mock_search,
    ):
        await skill._competitor_check(TID, {"query": "colour range"}, {})
    called_query = mock_search.call_args.args[0]
    assert called_query == "colour range price buy South Africa"


@pytest.mark.asyncio
async def test_competitor_check_get_config_failure_never_blocks_search(skill):
    with (
        patch("vula.api.tenants.get_config", side_effect=RuntimeError("no config")),
        patch("core.skills.web_search._ddg_search", new=AsyncMock(return_value=[])) as mock_search,
    ):
        result = await skill._competitor_check(TID, {"query": "colour range"}, {})
    mock_search.assert_called_once()
    assert "error" in result  # no results — but it ran, didn't crash
