"""vula/kb_context.py — each stored piece re-embedded with its document's label; text untouched,
resumable, revertible."""
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from vula import kb_context as kc

FILED = {"doc_id": "d1", "filename": "Invoice - Solid Cape 20261019-0947.pdf", "category": "Invoice",
         "fields": {"supplier": "SOLID CAPE (PTY) LTD", "date": "2026-10-19", "total_cents": 120461},
         "created_at": "2026-10-19T09:47:00+00:00"}


def test_a_filed_document_is_labelled_from_its_record():
    label = kc.label_for({"doc_id": "d1", "filename": "00097750.pdf"}, FILED)
    assert label == "Invoice from SOLID CAPE (PTY) LTD, 19 Oct 2026 — 00097750"


def test_a_report_is_labelled_with_its_content_title():
    filed = {"doc_id": "r", "filename": "Mipolam Affinity slip test 20260827-1001.pdf", "category": "Report",
             "fields": {"supplier": "Gerflor"}, "created_at": "2026-08-27T10:01:00+00:00"}
    assert kc.label_for({"filename": "Report 20260827-1001.pdf"}, filed) == \
        "Report from Gerflor, 27 Aug 2026 — Mipolam Affinity slip test"


def test_an_unfiled_document_is_labelled_by_its_name():
    assert kc.label_for({"filename": "my_taralay_impression_-_guide.pdf"}, None) == \
        "my taralay impression - guide"
    assert kc.embed_text("X", "body") == "Document: X\n\nbody"


class _FakeQdrant:
    def __init__(self, points):
        self.points = points          # id -> payload
        self.vectors, self.payload_calls, self.deleted = {}, [], []

    def handler(self, req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content or b"{}")
        if req.url.path.endswith("/points/scroll"):
            pts = [{"id": i, "payload": dict(p)} for i, p in self.points.items()]
            return httpx.Response(200, json={"result": {"points": pts, "next_page_offset": None}})
        if req.url.path.endswith("/points/vectors"):
            for p in body["points"]:
                self.vectors[p["id"]] = p["vector"]
            return httpx.Response(200, json={"result": {}})
        if req.url.path.endswith("/points/payload/delete"):
            self.deleted.append(body)
            for i in body["points"]:
                for k in body["keys"]:
                    self.points[i].pop(k, None)
            return httpx.Response(200, json={"result": {}})
        if req.url.path.endswith("/points/payload"):
            self.payload_calls.append(body)
            for i in body["points"]:
                self.points[i].update(body["payload"])
            return httpx.Response(200, json={"result": {}})
        return httpx.Response(404)


@pytest.fixture
def qdrant(monkeypatch):
    fake = _FakeQdrant({
        1: {"doc_id": "d1", "filename": "00097750.pdf", "text": "Qty 4 Hake R1 280"},
        2: {"doc_id": "d1", "filename": "00097750.pdf", "text": "Total R1 204.61"},
        3: {"doc_id": "d2", "filename": "taralay_guide.pdf", "text": "Taralay colours", "ctx_v": 1},
    })
    real = httpx.AsyncClient
    monkeypatch.setattr(kc.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(fake.handler),
                                          base_url="http://q", **kw))
    monkeypatch.setattr(kc, "_filed_rows", lambda t, ids: {"d1": FILED})
    from vula.ingestion import pipeline
    monkeypatch.setattr(pipeline.QdrantStore, "base", "http://q", raising=False)
    return fake


@pytest.mark.asyncio
async def test_unlabelled_pieces_are_re_embedded_with_the_label_and_marked(qdrant):
    seen = []

    async def embed_batch(self, texts):
        seen.extend(texts)
        return [[0.1, 0.2] for _ in texts]
    with patch("vula.ingestion.pipeline.EmbeddingEngine.embed_batch", new=embed_batch):
        out = await kc.contextualize("digg-demo", pause_s=0)
    assert (out["points"], out["todo"], out["done"]) == (3, 2, 2)      # 3 was already done
    assert seen[0].startswith("Document: Invoice from SOLID CAPE") and seen[0].endswith("Qty 4 Hake R1 280")
    assert set(qdrant.vectors) == {1, 2}
    assert qdrant.points[1]["ctx_v"] == 1 and qdrant.points[1]["text"] == "Qty 4 Hake R1 280"
    assert len(qdrant.payload_calls) == 1          # one call per label, not per piece


@pytest.mark.asyncio
async def test_a_second_run_has_nothing_to_do(qdrant):
    with patch("vula.ingestion.pipeline.EmbeddingEngine.embed_batch",
               new=AsyncMock(return_value=[[0.1], [0.1]])):
        await kc.contextualize("digg-demo", pause_s=0)
        out = await kc.contextualize("digg-demo", pause_s=0)
    assert out["todo"] == 0 and out["done"] == 0


@pytest.mark.asyncio
async def test_revert_re_embeds_plain_text_and_clears_the_marker(qdrant):
    seen = []

    async def embed_batch(self, texts):
        seen.extend(texts)
        return [[0.3] for _ in texts]
    with patch("vula.ingestion.pipeline.EmbeddingEngine.embed_batch", new=embed_batch):
        out = await kc.contextualize("digg-demo", revert=True, pause_s=0)
    assert out["done"] == 1 and seen == ["Taralay colours"]
    assert "ctx_v" not in qdrant.points[3]


@pytest.mark.asyncio
async def test_dry_run_changes_nothing_and_shows_labels(qdrant):
    out = await kc.contextualize("digg-demo", dry_run=True)
    assert out["todo"] == 2 and qdrant.vectors == {} and out["examples"][0].startswith("Invoice")


@pytest.mark.asyncio
async def test_the_embedding_box_dropping_out_stops_the_run_resumably(qdrant, monkeypatch):
    monkeypatch.setattr(kc, "BATCH", 1)
    monkeypatch.setattr(kc.asyncio, "sleep", AsyncMock())
    with patch("vula.ingestion.pipeline.EmbeddingEngine.embed_batch",
               new=AsyncMock(side_effect=httpx.ConnectError("down"))):
        out = await kc.contextualize("digg-demo", pause_s=0)
    assert out["done"] == 0 and out["failed"] == 2 and qdrant.vectors == {}


# ── Scheduling ───────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_nightly_runs_once_in_the_window_and_measures_when_something_changed(monkeypatch):
    from config import settings
    from vula import kb_maintenance as km
    monkeypatch.setattr(settings, "kb_context_nightly", True)
    monkeypatch.setattr(settings, "kb_ctx_baseline", "base-report")
    jobs = []

    async def run_job(job, by="boot"):
        jobs.append(job)
        return {"by_tenant": {"digg-demo": {"done": 5}}}
    monkeypatch.setattr(km, "run_job", run_job)
    monkeypatch.setattr(km, "_last_done", lambda job: None)
    assert await km.context_nightly(datetime(2026, 10, 25, 14, tzinfo=timezone.utc)) is None   # daytime
    await km.context_nightly(datetime(2026, 10, 25, 23, 30, tzinfo=timezone.utc))
    assert jobs == ["enrich_documents", "supplier_details", "contextualize", "retrieval_diag_ctx"]
    monkeypatch.setattr(km, "_last_done", lambda job: datetime(2026, 10, 25, 23, 31, tzinfo=timezone.utc))
    assert await km.context_nightly(datetime(2026, 10, 26, 0, 30, tzinfo=timezone.utc)) is None


@pytest.mark.asyncio
async def test_nightly_is_off_unless_switched_on(monkeypatch):
    from config import settings
    from vula import kb_maintenance as km
    monkeypatch.setattr(settings, "kb_context_nightly", False)
    assert await km.context_nightly(datetime(2026, 10, 25, 23, tzinfo=timezone.utc)) is None
