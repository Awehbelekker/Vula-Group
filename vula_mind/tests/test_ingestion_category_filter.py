"""Tests for the 2026-08-24 structured-starter-KB additions to vula/ingestion/pipeline.py:
a `category` tag on ingested chunks (QdrantStore.upsert_chunks already spread `metadata` into
the payload, so no store-layer change was needed there) and a `category` filter on search/query.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.ingestion.pipeline import QdrantStore, VulaIngestionPipeline


@pytest.mark.asyncio
async def test_ingest_text_tags_chunks_with_category_when_given():
    pipeline = VulaIngestionPipeline(tenant_id="test-tenant")
    pipeline.chunker = MagicMock()
    pipeline.chunker.chunk.return_value = ["chunk one text"]
    pipeline.embedder = MagicMock()
    pipeline.embedder.embed_batch = AsyncMock(return_value=[[0.1, 0.2]])
    pipeline.embedder.dimension = 2
    pipeline.store = MagicMock()
    pipeline.store.ensure_collection = AsyncMock()

    captured = {}
    async def fake_upsert(tenant_id, chunks):
        captured["chunks"] = chunks
        return len(chunks)
    pipeline.store.upsert_chunks = fake_upsert

    await pipeline.ingest_text("some content", "starter_booking_policy.md",
                               source_type="starter", category="Booking Policy")

    assert captured["chunks"][0].metadata["category"] == "Booking Policy"
    assert captured["chunks"][0].metadata["source_type"] == "starter"


@pytest.mark.asyncio
async def test_ingest_text_omits_category_key_when_not_given():
    """Backward compatibility: every existing call site (agent-teach, page reference docs,
    etc.) doesn't pass category — the payload must look exactly as it did before this existed."""
    pipeline = VulaIngestionPipeline(tenant_id="test-tenant")
    pipeline.chunker = MagicMock()
    pipeline.chunker.chunk.return_value = ["chunk one text"]
    pipeline.embedder = MagicMock()
    pipeline.embedder.embed_batch = AsyncMock(return_value=[[0.1, 0.2]])
    pipeline.embedder.dimension = 2
    pipeline.store = MagicMock()
    pipeline.store.ensure_collection = AsyncMock()

    captured = {}
    async def fake_upsert(tenant_id, chunks):
        captured["chunks"] = chunks
        return len(chunks)
    pipeline.store.upsert_chunks = fake_upsert

    await pipeline.ingest_text("some content", "reference.md", source_type="reference")

    assert "category" not in captured["chunks"][0].metadata


@pytest.mark.asyncio
async def test_search_adds_category_filter_when_given():
    store = QdrantStore()
    captured = {}

    class _FakeResp:
        status_code = 200
        def json(self):
            return {"result": []}
        def raise_for_status(self):
            pass

    class _FakeClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, url, json):
            captured["body"] = json
            return _FakeResp()

    with patch("httpx.AsyncClient", return_value=_FakeClient()):
        await store.search("test-tenant", [0.1, 0.2], category="Booking Policy")

    assert captured["body"]["filter"]["must"] == [{"key": "category", "match": {"value": "Booking Policy"}}]


@pytest.mark.asyncio
async def test_search_combines_category_and_exclude_source_types_filters():
    store = QdrantStore()
    captured = {}

    class _FakeResp:
        status_code = 200
        def json(self):
            return {"result": []}
        def raise_for_status(self):
            pass

    class _FakeClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, url, json):
            captured["body"] = json
            return _FakeResp()

    with patch("httpx.AsyncClient", return_value=_FakeClient()):
        await store.search("test-tenant", [0.1, 0.2], exclude_source_types=["learned"],
                           category="Booking Policy")

    f = captured["body"]["filter"]
    assert f["must"] == [{"key": "category", "match": {"value": "Booking Policy"}}]
    assert f["must_not"] == [{"key": "source_type", "match": {"value": "learned"}}]


@pytest.mark.asyncio
async def test_search_no_filter_key_when_neither_given():
    """Regression: a plain query (no category, no exclude) must behave exactly as before —
    no 'filter' key in the request body at all."""
    store = QdrantStore()
    captured = {}

    class _FakeResp:
        status_code = 200
        def json(self):
            return {"result": []}
        def raise_for_status(self):
            pass

    class _FakeClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, url, json):
            captured["body"] = json
            return _FakeResp()

    with patch("httpx.AsyncClient", return_value=_FakeClient()):
        await store.search("test-tenant", [0.1, 0.2])

    assert "filter" not in captured["body"]


@pytest.mark.asyncio
async def test_query_passes_category_through_to_store_search():
    pipeline = VulaIngestionPipeline(tenant_id="test-tenant")
    pipeline.embedder = MagicMock()
    pipeline.embedder.embed = AsyncMock(return_value=[0.1, 0.2])
    pipeline.store = MagicMock()
    captured = {}
    async def fake_search(*a, **kw):
        captured.update(kw)
        return []
    pipeline.store.search = fake_search

    await pipeline.query("what's on the menu", category="Menu / Price List")

    assert captured["category"] == "Menu / Price List"
