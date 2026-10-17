"""vula/kb_maintenance.py (2026-10-17): the server-side runner for the KB jobs that need
production's keys. Run here against the in-memory Qdrant with Supabase stubbed out."""
from unittest.mock import MagicMock, patch

import pytest

from evals.retrieval import offline
from vula import kb_maintenance as km
from vula.ingestion.pipeline import VulaIngestionPipeline


@pytest.fixture()
def db():
    fake = MagicMock()
    fake.table.return_value.insert.return_value.execute.return_value.data = [{"id": "r1"}]
    with patch("vula.commerce.service._client", return_value=fake), \
            patch("tools.kb_audience_backfill._categories", return_value={}), \
            patch("vula.api.tenants._client", side_effect=RuntimeError("no supabase in tests")):
        yield fake


async def _legacy_shop():
    p = VulaIngestionPipeline("shop")
    await p.ingest_text("Invoice from the co-op: yellowtail R 95 per kg.", "Supplier invoice co-op.pdf")
    await p.ingest_text("Is the fish fresh? Yes, line caught daily.", "Fresh catch FAQ.md",
                        source_type="website")
    return p


@pytest.mark.asyncio
async def test_stats_preview_apply_and_customer_view_end_to_end(db):
    with offline() as mem:
        p = await _legacy_shop()
        for _v, pl in mem.cols[p.store._collection_name("shop")]["points"].values():
            pl.pop("audience", None)                       # as stored before 2026-10-16
        stats = await km.run_job("stats")
        assert stats["summary"]["untagged_points"] == 2 and stats["summary"]["qdrant_version"] == "memory"

        before = await km.run_job("customer_view")
        assert before["summary"]["shop"]["untagged_visible"] > 0

        prev = await km.run_job("backfill_preview")
        assert prev["summary"]["by_audience"] == {"staff": 1, "public": 1} and prev["summary"]["written"] == 0
        done = await km.run_job("backfill_apply")
        assert done["summary"]["written"] == done["summary"]["verified"] == 2

        after = await km.run_job("customer_view")
        assert after["summary"]["shop"] == {"ok": True, "violations": 0, "untagged_visible": 0,
                                            "by_audience": after["summary"]["shop"]["by_audience"]}
        assert after["summary"]["shop"]["by_audience"]["staff"] == 0
    # every job wrote its report row and marked it done
    updates = [c.args[0] for c in db.table.return_value.update.call_args_list]
    assert updates and all(u["status"] == "done" for u in updates)


@pytest.mark.asyncio
async def test_boot_runs_only_known_jobs_named_in_the_setting(monkeypatch):
    ran = []

    async def _run_jobs(jobs, by="boot"):
        ran.extend(jobs)

    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(km, "run_jobs", _run_jobs)
    monkeypatch.setattr(km.asyncio, "sleep", _no_sleep)
    with patch("config.settings.kb_maintenance", "stats, nonsense,backfill_preview"):
        await km.on_boot()
    assert ran == ["stats", "backfill_preview"]
    ran.clear()
    with patch("config.settings.kb_maintenance", ""):
        await km.on_boot()
    assert ran == []
