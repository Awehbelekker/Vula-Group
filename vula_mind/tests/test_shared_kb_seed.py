"""2026-09-30: the shared business KB only existed if someone pressed Seed. It's now kept seeded
automatically (with the sector packs), and re-seeded only when the content changes."""
import asyncio
from types import SimpleNamespace

from vula.training import seeder
from vula.training.sector_content import SECTOR_DOCUMENTS, sector_tenant_id


def _patch(monkeypatch, *, seeded, marker):
    calls = []

    async def fake_seed(tid, docs):
        calls.append(tid)
        return seeder.SeedResult(total_documents=len(docs), total_chunks=len(docs), failed=[], duration_s=0)

    async def fake_status():
        return {"seeded": seeded}

    monkeypatch.setattr(seeder, "seed_documents", fake_seed)
    monkeypatch.setattr(seeder, "business_kb_status", fake_status)
    monkeypatch.setattr(seeder, "_seeded_marker", lambda fp: marker)
    written = []

    class _T:
        def insert(self, row): written.append(row); return self
        def execute(self): return SimpleNamespace(data=[])

    monkeypatch.setattr("vula.commerce.service._client", lambda: SimpleNamespace(table=lambda n: _T()))
    return calls, written


def test_empty_kb_is_seeded_with_every_sector_pack(monkeypatch):
    calls, written = _patch(monkeypatch, seeded=False, marker=False)
    res = asyncio.run(seeder.ensure_shared_kbs())
    assert res["seeded"] and "business_basics" in calls
    for sector, docs in SECTOR_DOCUMENTS.items():
        if docs:
            assert sector_tenant_id(sector) in calls
    assert written and written[0]["detail"]["fingerprint"] == seeder.shared_kb_fingerprint()


def test_up_to_date_kb_is_left_alone(monkeypatch):  # marker + collection has points
    calls, _ = _patch(monkeypatch, seeded=True, marker=True)
    assert asyncio.run(seeder.ensure_shared_kbs())["seeded"] is False and calls == []


def test_changed_content_reseeds(monkeypatch):
    calls, _ = _patch(monkeypatch, seeded=True, marker=False)   # no marker for this content
    assert asyncio.run(seeder.ensure_shared_kbs())["seeded"] and calls


def test_sector_packs_never_state_a_tenant_figure():
    """Packs explain terms and law; a product's figure comes from its own data sheet."""
    text = " ".join(d.content for docs in SECTOR_DOCUMENTS.values() for d in docs)
    for brand in ("Affinity", "Taraflex", "Mipolam", "Off the Hook", "DIGG"):
        assert brand not in text


def test_a_failed_status_check_does_not_reseed(monkeypatch):
    """2026-09-30: the status check sent no Qdrant api-key, was refused, and every deploy
    re-seeded everything. With the marker present, an unreadable status means "leave it"."""
    calls, _ = _patch(monkeypatch, seeded=False, marker=True)

    async def refused():
        return {"seeded": False, "chunks": 0, "error": "HTTP 403"}
    monkeypatch.setattr(seeder, "business_kb_status", refused)
    assert asyncio.run(seeder.ensure_shared_kbs())["seeded"] is False and calls == []


def test_status_check_sends_the_qdrant_key(monkeypatch):
    import httpx
    seen = {}

    class _C:
        def __init__(self, **kw): seen.update(kw.get("headers") or {})
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url):
            class R:
                status_code = 200
                def json(self): return {"result": {"points_count": 13}}
            return R()

    monkeypatch.setattr(httpx, "AsyncClient", _C)
    monkeypatch.setattr("config.settings.qdrant_api_key", "k")
    st = asyncio.run(seeder.collection_status("business_basics"))
    assert seen.get("api-key") == "k" and st["seeded"] and st["chunks"] == 13


def test_marker_but_empty_collection_reseeds(monkeypatch):
    """Qdrant reset: the marker exists but the collection clearly has no points."""
    calls, _ = _patch(monkeypatch, seeded=False, marker=True)
    assert asyncio.run(seeder.ensure_shared_kbs())["seeded"] and calls
