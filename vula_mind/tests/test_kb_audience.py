"""KB retrieval C5 (2026-10-16, docs/kb-retrieval-audit.md): a customer's question must never be
grounded on a staff or owner document — supplier invoices with cost prices, bank statements,
wage rosters all live in the same tenant collection as the shop's FAQ."""
from unittest.mock import patch

import pytest

from evals.retrieval import offline
from vula.ingestion import pipeline as pl
from vula.ingestion.pipeline import VulaIngestionPipeline, audience_filter, default_audience

INVOICE = "Invoice from the fishermen co-op: 40 kg yellowtail at R 95 per kg. Settlement in 7 days."
FAQ = "Is your yellowtail fresh? Yes, our yellowtail is line caught and on the counter within a day."


def test_default_audience_is_conservative():
    assert default_audience("document") == "staff"
    assert default_audience(None) == "staff"
    assert default_audience("website") == "public"
    assert default_audience("starter") == "public"
    assert default_audience("learned_answer_approved") == "public"
    assert default_audience("document", "owner") == "owner"
    assert default_audience("document", "everyone") == "staff"   # unknown value → the default


def test_only_a_customer_is_filtered():
    assert audience_filter(None) == {} and audience_filter("staff") == {}
    assert audience_filter("public") == {
        "must_not": [{"key": "audience", "match": {"any": ["staff", "owner"]}}]}
    with patch.object(pl.settings, "kb_customer_audience_strict", True):
        assert audience_filter("public") == {"must": [{"key": "audience", "match": {"value": "public"}}]}


async def _seed(mem, tenant="shop"):
    p = VulaIngestionPipeline(tenant)
    await p.ingest_text(INVOICE, "Supplier invoice co-op.pdf")                  # staff by default
    await p.ingest_text(FAQ, "Fresh catch FAQ.md", source_type="website")       # public
    return p


@pytest.mark.asyncio
async def test_customer_query_never_gets_a_staff_document_by_vector_or_keyword():
    with offline() as mem:
        p = await _seed(mem)
        q = "yellowtail per kg price"           # terse → the keyword pass runs as well
        owner = {c["filename"] for c in await p.query(q, top_k=5)}
        customer = {c["filename"] for c in await p.query(q, top_k=5, audience="public")}
    assert "Supplier invoice co-op.pdf" in owner
    assert "Supplier invoice co-op.pdf" not in customer
    assert "Fresh catch FAQ.md" in customer


@pytest.mark.asyncio
async def test_untagged_legacy_points_stay_visible_until_strict_mode():
    with offline() as mem:
        p = await _seed(mem)
        col = mem.cols[p.store._collection_name("shop")]
        for _pid, (_v, payload) in col["points"].items():   # as if stored before this change
            payload.pop("audience", None)
        q = "yellowtail per kg price"
        lenient = {c["filename"] for c in await p.query(q, audience="public")}
        with patch.object(pl.settings, "kb_customer_audience_strict", True):
            strict = await p.query(q, audience="public")
    assert "Supplier invoice co-op.pdf" in lenient       # the known gap the backfill closes
    assert strict == []


@pytest.mark.asyncio
async def test_a_refused_audience_filter_fails_closed():
    """The old 400 fallback retried WITHOUT the filter — for a customer that would be the leak."""
    import httpx

    async def handler(req):
        if req.url.path.endswith("/points/search"):
            return httpx.Response(400, json={"status": {"error": "bad filter"}})
        return httpx.Response(200, json={"result": True})

    real = httpx.AsyncClient
    with patch("httpx.AsyncClient", lambda *a, **kw: real(*a, **{**kw, "transport": httpx.MockTransport(handler)})):
        got = await pl.QdrantStore().search("shop", [0.1], audience="public")
    assert got == []


@pytest.mark.asyncio
async def test_the_customer_assistant_asks_as_public():
    from core.skills.base import SkillInput
    from core.skills.commerce_assistant import CommerceAssistantSkill
    with offline() as mem:
        await _seed(mem)
        block, sources = await CommerceAssistantSkill()._retrieve_kb(
            SkillInput(question="what do you pay for yellowtail per kg", tenant_id="shop"))
    assert "co-op" not in block and all("invoice" not in s["filename"].lower() for s in sources)
