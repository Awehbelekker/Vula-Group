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


def test_up_to_date_kb_is_left_alone(monkeypatch):
    calls, _ = _patch(monkeypatch, seeded=True, marker=True)
    assert asyncio.run(seeder.ensure_shared_kbs())["seeded"] is False and calls == []


def test_changed_content_reseeds(monkeypatch):
    calls, _ = _patch(monkeypatch, seeded=True, marker=False)
    assert asyncio.run(seeder.ensure_shared_kbs())["seeded"] and calls


def test_sector_packs_never_state_a_tenant_figure():
    """Packs explain terms and law; a product's figure comes from its own data sheet."""
    text = " ".join(d.content for docs in SECTOR_DOCUMENTS.values() for d in docs)
    for brand in ("Affinity", "Taraflex", "Mipolam", "Off the Hook", "DIGG"):
        assert brand not in text
