"""KB retrieval phase 1 (2026-10-16): 128-bit point ids (C2), the collision detector, and the
retrieval thresholds now living in settings (C4). docs/kb-retrieval-audit.md."""
import uuid
from unittest.mock import patch

import pytest

from evals.retrieval import MemoryQdrant, offline
from vula.ingestion import pipeline as pl
from vula.ingestion.pipeline import VulaIngestionPipeline, legacy_point_id, point_id

TEXT = "\n\n".join(f"Paragraph {i}. " + "word " * 300 for i in range(4))


def test_point_ids_are_uuid5_per_tenant_and_deterministic():
    a = point_id("gerflor", "abc_0")
    assert a == point_id("gerflor", "abc_0")
    assert a != point_id("digg-demo", "abc_0")
    assert uuid.UUID(a).version == 5
    with patch.object(pl.settings, "qdrant_uuid_point_ids", False):
        assert point_id("gerflor", "abc_0") == legacy_point_id("abc_0") < 2 ** 32


async def _points(mem: MemoryQdrant, tenant: str):
    col = mem.cols[VulaIngestionPipeline(tenant).store._collection_name(tenant)]
    return col["points"]


@pytest.mark.asyncio
async def test_reingesting_a_legacy_document_replaces_it_instead_of_duplicating():
    with offline() as mem:
        p = VulaIngestionPipeline("t1")
        with patch.object(pl.settings, "qdrant_uuid_point_ids", False):
            first = await p.ingest_text(TEXT, "sheet.pdf", doc_id="doc1")
        pts = await _points(mem, "t1")
        assert first.chunks_stored > 1 and all(isinstance(i, int) for i in pts)
        again = await p.ingest_text(TEXT, "sheet.pdf", doc_id="doc1")
        pts = await _points(mem, "t1")
        assert len(pts) == again.chunks_stored == first.chunks_stored
        assert all(isinstance(i, str) for i in pts)
        # and a third, identical ingest is a pure overwrite
        await p.ingest_text(TEXT, "sheet.pdf", doc_id="doc1")
        assert len(await _points(mem, "t1")) == first.chunks_stored


@pytest.mark.asyncio
async def test_a_legacy_id_held_by_another_chunk_is_not_deleted():
    """A collision gave chunk doc1_0's legacy id to some other chunk: the clean-up must leave it."""
    with offline() as mem:
        p = VulaIngestionPipeline("t2")
        await p.store.ensure_collection("t2", 16)
        name = p.store._collection_name("t2")
        mem.cols[name]["points"][legacy_point_id("doc1_0")] = (
            [0.1] * 16, {"chunk_id": "other_7", "doc_id": "other", "chunk_index": 7, "text": "kept"})
        await p.ingest_text(TEXT, "sheet.pdf", doc_id="doc1")
        assert legacy_point_id("doc1_0") in mem.cols[name]["points"]


def test_kb_stats_finds_gaps_and_confirms_a_collision():
    from tools.kb_stats import analyse, expected_collisions
    pts = [{"id": legacy_point_id(f"d1_{i}"), "payload": {"chunk_id": f"d1_{i}", "doc_id": "d1",
                                                           "chunk_index": i, "text": "x" * 1000}}
           for i in (0, 2)]
    # d1_1 is missing and its legacy id is held by a chunk of another document
    pts.append({"id": legacy_point_id("d1_1"), "payload": {"chunk_id": "d9_4", "doc_id": "d9",
                                                           "chunk_index": 0, "text": "y" * 100}})
    rep = analyse(pts)
    assert rep["missing_chunks"] == 1 and rep["confirmed_collisions"] == 1
    assert rep["documents_with_gaps"] == 1
    assert rep["over_900_chars_pct"] == pytest.approx(66.7)
    assert rep["audience"] == {"(none)": 3}
    assert 0.009 < expected_collisions(9300) / 1 < 0.011


def test_thresholds_default_to_the_old_literals_and_can_be_overridden_per_model():
    assert pl.kb_score_threshold() == 0.30
    assert pl.kb_score_threshold(authoritative=True) == 0.35
    with patch.object(pl.settings, "kb_score_thresholds_by_model",
                      '{"bge-m3": {"default": 0.25}}'), patch.object(pl.settings, "model_embed", "bge-m3"):
        assert pl.kb_score_threshold() == 0.25
        assert pl.kb_score_threshold(authoritative=True) == 0.35
    with patch.object(pl.settings, "kb_score_thresholds_by_model", "not json"):
        assert pl.kb_score_threshold() == 0.30


@pytest.mark.asyncio
async def test_query_passes_the_configured_threshold_to_qdrant():
    seen = []

    async def _search(self, tenant_id, emb, limit=5, score_threshold=None, **kw):
        seen.append(score_threshold)
        return []

    p = VulaIngestionPipeline("t3")

    async def _embed(_q):
        return [0.0]

    with patch.object(pl.QdrantStore, "search", _search), patch.object(p.embedder, "embed", _embed), \
            patch.object(pl.settings, "kb_score_threshold_authoritative", 0.41), \
            patch.object(pl.QdrantStore, "keyword_search", new=lambda *a, **k: _none()):
        await p.query("anything", authoritative_only=True, expand=False)
    assert seen == [0.41]


async def _none():
    return []
