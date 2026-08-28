"""Tests for the shared general SA small-business knowledge base (vula/training/business_content.py)
— mirrors tests/test_training.py's structure exactly, for the separate business_basics corpus."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from vula.api.server import app

client = TestClient(app)


# ── Content module ────────────────────────────────────────────────────────────

def test_business_documents_exist():
    from vula.training.business_content import BUSINESS_TRAINING_DOCUMENTS
    assert len(BUSINESS_TRAINING_DOCUMENTS) >= 10


def test_business_documents_have_content():
    from vula.training.business_content import BUSINESS_TRAINING_DOCUMENTS
    for doc in BUSINESS_TRAINING_DOCUMENTS:
        assert doc.filename.endswith(".md"), f"{doc.filename} should be .md"
        assert len(doc.content) > 500, f"{doc.filename} has too little content"
        assert doc.topic, f"{doc.filename} missing topic"


def test_business_documents_filenames_unique():
    from vula.training.business_content import BUSINESS_TRAINING_DOCUMENTS
    filenames = [d.filename for d in BUSINESS_TRAINING_DOCUMENTS]
    assert len(filenames) == len(set(filenames))


def test_business_training_tenant_id():
    from vula.training.business_content import BUSINESS_TRAINING_TENANT_ID
    assert BUSINESS_TRAINING_TENANT_ID == "business_basics"


def test_business_kb_is_a_separate_collection_from_construction():
    # 2026-08-28: the whole point of a separate pseudo-tenant is keeping architecture_planning's/
    # standards_lookup's construction-only retrieval undiluted, and this corpus free of
    # construction noise. Must never collide with the construction corpus's tenant_id.
    from vula.training.business_content import BUSINESS_TRAINING_TENANT_ID
    from vula.training.content import TRAINING_TENANT_ID
    assert BUSINESS_TRAINING_TENANT_ID != TRAINING_TENANT_ID


def test_vat_basics_covers_key_facts():
    from vula.training.business_content import BUSINESS_TRAINING_DOCUMENTS
    doc = next((d for d in BUSINESS_TRAINING_DOCUMENTS if d.filename == "vat_basics.md"), None)
    assert doc is not None
    content = doc.content.lower()
    for term in ["r1 million", "15%", "output vat", "input vat"]:
        assert term in content, f"vat_basics.md missing: {term}"


def test_bcea_basics_covers_key_facts():
    from vula.training.business_content import BUSINESS_TRAINING_DOCUMENTS
    doc = next((d for d in BUSINESS_TRAINING_DOCUMENTS if d.filename == "bcea_basics.md"), None)
    assert doc is not None
    content = doc.content.lower()
    for term in ["45 hours", "annual leave", "sick leave", "notice"]:
        assert term in content, f"bcea_basics.md missing: {term}"


def test_no_construction_content_leaked_into_business_docs():
    # Sanity guard against scope creep — this corpus is explicitly NOT construction/QS.
    from vula.training.business_content import BUSINESS_TRAINING_DOCUMENTS
    joined = " ".join(d.content.lower() for d in BUSINESS_TRAINING_DOCUMENTS)
    for term in ["jbcc", "sacap", "cidb", "nhbrc", "sans 10400"]:
        assert term not in joined, f"unexpected construction-specific term leaked in: {term}"


# ── Seeder ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_seed_business_kb_calls_ingest_text():
    from vula.training.business_content import BUSINESS_TRAINING_DOCUMENTS

    mock_result = MagicMock()
    mock_result.status = "success"
    mock_result.chunks_stored = 8

    mock_pipeline = AsyncMock()
    mock_pipeline.ingest_text = AsyncMock(return_value=mock_result)

    with patch("vula.training.seeder.VulaIngestionPipeline", return_value=mock_pipeline):
        from vula.training.seeder import seed_business_kb
        result = await seed_business_kb()

    assert result.total_documents == len(BUSINESS_TRAINING_DOCUMENTS)
    assert mock_pipeline.ingest_text.call_count == len(BUSINESS_TRAINING_DOCUMENTS)
    assert result.total_chunks == 8 * len(BUSINESS_TRAINING_DOCUMENTS)
    assert result.failed == []


@pytest.mark.asyncio
async def test_seed_business_kb_never_touches_construction_pipeline():
    from vula.training.business_content import BUSINESS_TRAINING_TENANT_ID

    mock_result = MagicMock()
    mock_result.status = "success"
    mock_result.chunks_stored = 3

    captured_tenant_ids = []

    def _pipeline_factory(tenant_id):
        captured_tenant_ids.append(tenant_id)
        m = AsyncMock()
        m.ingest_text = AsyncMock(return_value=mock_result)
        return m

    with patch("vula.training.seeder.VulaIngestionPipeline", side_effect=_pipeline_factory):
        from vula.training.seeder import seed_business_kb
        await seed_business_kb()

    assert all(tid == BUSINESS_TRAINING_TENANT_ID for tid in captured_tenant_ids)


@pytest.mark.asyncio
async def test_seed_business_kb_records_failures():
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
        from vula.training.seeder import seed_business_kb
        result = await seed_business_kb()

    assert len(result.failed) == 1


@pytest.mark.asyncio
async def test_business_kb_status_not_seeded():
    mock_resp = MagicMock()
    mock_resp.status_code = 404

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient", return_value=mock_client):
        from vula.training.seeder import business_kb_status
        status = await business_kb_status()

    assert status["seeded"] is False
    assert status["chunks"] == 0


@pytest.mark.asyncio
async def test_business_kb_status_seeded():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"result": {"points_count": 90}}

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient", return_value=mock_client):
        from vula.training.seeder import business_kb_status
        status = await business_kb_status()

    assert status["seeded"] is True
    assert status["chunks"] == 90


# ── Business training API endpoints ───────────────────────────────────────────

def test_business_training_topics_endpoint():
    resp = client.get("/v1/training/business/topics")
    assert resp.status_code == 200
    data = resp.json()
    assert "topics" in data
    assert data["total"] >= 10
    assert all("filename" in t and "topic" in t for t in data["topics"])


def test_business_training_seed_endpoint():
    resp = client.post("/v1/training/business/seed")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "started"
    assert "background" in data["message"].lower() or "seeding" in data["message"].lower()


def test_business_training_status_endpoint():
    with patch("vula.training.seeder.business_kb_status", new=AsyncMock(return_value={"seeded": False, "chunks": 0})):
        resp = client.get("/v1/training/business/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "seeded" in data
    assert "expected_documents" in data
    assert "topics" in data
