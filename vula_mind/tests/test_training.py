"""Tests for the Vula training knowledge base."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from vula.api.server import app

client = TestClient(app)


# ── Content module ────────────────────────────────────────────────────────────

def test_training_documents_exist():
    from vula.training.content import TRAINING_DOCUMENTS
    assert len(TRAINING_DOCUMENTS) >= 7


def test_training_documents_have_content():
    from vula.training.content import TRAINING_DOCUMENTS
    for doc in TRAINING_DOCUMENTS:
        assert doc.filename.endswith(".md"), f"{doc.filename} should be .md"
        assert len(doc.content) > 500, f"{doc.filename} has too little content"
        assert doc.topic, f"{doc.filename} missing topic"


def test_training_tenant_id():
    from vula.training.content import TRAINING_TENANT_ID
    assert TRAINING_TENANT_ID == "vula_training"


def test_qs_terms_covers_key_concepts():
    from vula.training.content import TRAINING_DOCUMENTS
    qs_doc = next((d for d in TRAINING_DOCUMENTS if d.filename == "qs_terms.md"), None)
    assert qs_doc is not None
    content = qs_doc.content.lower()
    for term in ["prime cost", "provisional sum", "preliminaries", "retention", "variation"]:
        assert term in content, f"QS terms doc missing: {term}"


def test_materials_covers_concrete():
    from vula.training.content import TRAINING_DOCUMENTS
    doc = next((d for d in TRAINING_DOCUMENTS if d.filename == "materials.md"), None)
    assert doc is not None
    assert "c25" in doc.content.lower()
    assert "reinforcing" in doc.content.lower() or "reinforcement" in doc.content.lower()


def test_measurement_doc_has_rates():
    from vula.training.content import TRAINING_DOCUMENTS
    doc = next((d for d in TRAINING_DOCUMENTS if d.filename == "measurement.md"), None)
    assert doc is not None
    assert "m²" in doc.content
    assert "brickwork" in doc.content.lower()


# ── Seeder ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_seed_training_kb_calls_ingest_text():
    from vula.training.content import TRAINING_DOCUMENTS

    mock_result = MagicMock()
    mock_result.status = "success"
    mock_result.chunks_stored = 12

    mock_pipeline = AsyncMock()
    mock_pipeline.ingest_text = AsyncMock(return_value=mock_result)

    with patch("vula.training.seeder.VulaIngestionPipeline", return_value=mock_pipeline):
        from vula.training.seeder import seed_training_kb
        result = await seed_training_kb()

    assert result.total_documents == len(TRAINING_DOCUMENTS)
    assert mock_pipeline.ingest_text.call_count == len(TRAINING_DOCUMENTS)
    assert result.total_chunks == 12 * len(TRAINING_DOCUMENTS)
    assert result.failed == []


@pytest.mark.asyncio
async def test_seed_training_kb_records_failures():
    mock_result_ok = MagicMock()
    mock_result_ok.status = "success"
    mock_result_ok.chunks_stored = 5

    mock_result_fail = MagicMock()
    mock_result_fail.status = "failed"
    mock_result_fail.error = "Qdrant unavailable"
    mock_result_fail.chunks_stored = 0

    call_count = {"n": 0}

    async def _fake_ingest(content, filename, doc_id=None):
        call_count["n"] += 1
        return mock_result_fail if call_count["n"] == 1 else mock_result_ok

    mock_pipeline = AsyncMock()
    mock_pipeline.ingest_text = _fake_ingest

    with patch("vula.training.seeder.VulaIngestionPipeline", return_value=mock_pipeline):
        from vula.training.seeder import seed_training_kb
        result = await seed_training_kb()

    assert len(result.failed) == 1


@pytest.mark.asyncio
async def test_training_kb_status_not_seeded():
    mock_resp = MagicMock()
    mock_resp.status_code = 404

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient", return_value=mock_client):
        from vula.training.seeder import training_kb_status
        status = await training_kb_status()

    assert status["seeded"] is False
    assert status["chunks"] == 0


@pytest.mark.asyncio
async def test_training_kb_status_seeded():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"result": {"points_count": 340}}

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient", return_value=mock_client):
        from vula.training.seeder import training_kb_status
        status = await training_kb_status()

    assert status["seeded"] is True
    assert status["chunks"] == 340


# ── _rag_reply fallback ───────────────────────────────────────────────────────
# _rag_reply tries the full multi-agent runner FIRST (a real, non-deterministic LLM
# call) and only falls through to the VulaIngestionPipeline-based logic these tests
# exercise if that runner errors — so every test here must also force the runner to
# fail, or it never reaches (and never deterministically tests) the fallback at all.

@pytest.mark.asyncio
async def test_rag_reply_uses_training_fallback_when_no_tenant_sources():
    mock_tenant_pipeline = AsyncMock()
    mock_tenant_pipeline.query = AsyncMock(return_value=[])  # no tenant sources

    mock_training_pipeline = AsyncMock()
    mock_training_pipeline.query = AsyncMock(return_value=[{"text": "PC Sum is a prime cost allowance"}])
    mock_training_pipeline.answer = AsyncMock(return_value="A PC Sum is a Prime Cost allowance for work whose cost is unknown at tender stage.")

    call_count = {"n": 0}

    def _pipeline_factory(tenant_id):
        call_count["n"] += 1
        if tenant_id == "vula_training":
            return mock_training_pipeline
        return mock_tenant_pipeline

    with patch("vula.ingestion.pipeline.VulaIngestionPipeline", side_effect=_pipeline_factory), \
         patch("core.agent_runner.get_agent_runner", side_effect=RuntimeError("agent runner unavailable in test")):
        from vula.api.whatsapp import _rag_reply
        reply = await _rag_reply("tenant-abc", "What is a PC Sum?")

    assert "PC Sum" in reply or "prime cost" in reply.lower()
    mock_training_pipeline.answer.assert_called_once()


@pytest.mark.asyncio
async def test_rag_reply_prefers_tenant_sources_over_training():
    mock_tenant_pipeline = AsyncMock()
    mock_tenant_pipeline.query = AsyncMock(return_value=[{"text": "Our PC sum policy is R50,000"}])
    mock_tenant_pipeline.answer = AsyncMock(return_value="Your PC sum budget is R50,000.")

    mock_training_pipeline = AsyncMock()

    def _pipeline_factory(tenant_id):
        if tenant_id == "vula_training":
            return mock_training_pipeline
        return mock_tenant_pipeline

    with patch("vula.ingestion.pipeline.VulaIngestionPipeline", side_effect=_pipeline_factory), \
         patch("core.agent_runner.get_agent_runner", side_effect=RuntimeError("agent runner unavailable in test")):
        from vula.api.whatsapp import _rag_reply
        reply = await _rag_reply("tenant-abc", "What is our PC sum budget?")

    assert reply == "Your PC sum budget is R50,000."
    mock_training_pipeline.query.assert_not_called()


@pytest.mark.asyncio
async def test_rag_reply_fallback_message_when_both_empty():
    mock_pipeline = AsyncMock()
    mock_pipeline.query = AsyncMock(return_value=[])

    with patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=mock_pipeline), \
         patch("core.agent_runner.get_agent_runner", side_effect=RuntimeError("agent runner unavailable in test")):
        from vula.api.whatsapp import _rag_reply
        reply = await _rag_reply("tenant-abc", "Random question with no match")

    assert "don't have enough information" in reply


# ── Training API endpoints ────────────────────────────────────────────────────

def test_training_topics_endpoint():
    resp = client.get("/v1/training/topics")
    assert resp.status_code == 200
    data = resp.json()
    assert "topics" in data
    assert data["total"] >= 7
    assert all("filename" in t and "topic" in t for t in data["topics"])


def test_training_seed_endpoint():
    resp = client.post("/v1/training/seed")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "started"
    assert "background" in data["message"].lower() or "seeding" in data["message"].lower()


def test_training_seed_force_param():
    resp = client.post("/v1/training/seed?force=true")
    assert resp.status_code == 200
    assert resp.json()["force"] is True


def test_training_status_endpoint():
    with patch("vula.training.seeder.training_kb_status", new=AsyncMock(return_value={"seeded": False, "chunks": 0})):
        resp = client.get("/v1/training/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "seeded" in data
    assert "expected_documents" in data
    assert "topics" in data


# ── ingest_text on pipeline ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pipeline_ingest_text_returns_result():
    from vula.ingestion.pipeline import VulaIngestionPipeline

    mock_embedder = AsyncMock()
    mock_embedder.embed_batch = AsyncMock(return_value=[[0.1] * 10])
    mock_embedder.dimension = 10

    mock_store = AsyncMock()
    mock_store.ensure_collection = AsyncMock()
    mock_store.upsert_chunks = AsyncMock(return_value=1)

    pipeline = VulaIngestionPipeline(tenant_id="test_training")
    pipeline.embedder = mock_embedder
    pipeline.store = mock_store

    result = await pipeline.ingest_text(
        content="This is a test construction knowledge document about concrete grades.",
        filename="test_doc.md",
    )

    assert result.status == "success"
    assert result.filename == "test_doc.md"
    assert result.chunks_stored == 1
    mock_store.upsert_chunks.assert_called_once()
