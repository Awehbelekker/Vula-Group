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


# ── weekly customer-view guard (2026-10-20) ───────────────────────────────────

from datetime import datetime as _dt, timedelta as _td, timezone as _tz


def _guard_env(monkeypatch, last, summary, retry_rows=None):
    from vula import kb_maintenance as km
    sent, ran = [], []
    monkeypatch.setattr(km, "_last_done", lambda job: last)

    async def fake_run_job(job, by="boot"):
        ran.append((job, by))
        return {"summary": dict(summary)}

    async def fake_alert(text):
        sent.append(text)
        return True

    async def no_sleep(_s):
        return None

    async def fake_retry(tenants):
        return {"rows": retry_rows or []}

    monkeypatch.setattr(km, "run_job", fake_run_job)
    monkeypatch.setattr("vula.integrations.platform_support.alert_operator", fake_alert)
    monkeypatch.setattr(km.asyncio, "sleep", no_sleep)
    monkeypatch.setattr("evals.retrieval.run_customer_view", fake_retry)
    return km, sent, ran


async def test_weekly_guard_not_due_within_a_week(monkeypatch):
    now = _dt(2026, 10, 20, tzinfo=_tz.utc)
    km, sent, ran = _guard_env(monkeypatch, now - _td(days=3), {})
    assert await km.weekly_guard(now) is None
    assert ran == [] and sent == []


async def test_weekly_guard_clean_run_sends_nothing(monkeypatch):
    now = _dt(2026, 10, 20, tzinfo=_tz.utc)
    clean = {"gerflor": {"ok": True, "violations": 0, "untagged_visible": 0}}
    km, sent, ran = _guard_env(monkeypatch, now - _td(days=8), clean)
    assert await km.weekly_guard(now) == []
    assert ran == [("customer_view", "weekly")] and sent == []


async def test_weekly_guard_alerts_on_violation(monkeypatch):
    now = _dt(2026, 10, 20, tzinfo=_tz.utc)
    bad = {"off-the-hook": {"ok": False, "violations": 2, "untagged_visible": 0},
           "gerflor": {"ok": True, "violations": 0, "untagged_visible": 0}}
    km, sent, _ = _guard_env(monkeypatch, None, bad)
    problems = await km.weekly_guard(now)
    assert problems == ["off-the-hook: 2 private and 0 untagged document chunks visible to customers"]
    assert len(sent) == 1 and "off-the-hook" in sent[0] and "\n" not in sent[0]


async def test_weekly_guard_retries_a_timed_out_tenant_before_alerting(monkeypatch):
    now = _dt(2026, 10, 20, tzinfo=_tz.utc)
    summary = {"digg-demo": "ConnectTimeout: "}
    retry = [{"tenant": "digg-demo", "ok": True, "violations": [], "legacy_untagged_visible": 0}]
    km, sent, _ = _guard_env(monkeypatch, None, summary, retry)
    assert await km.weekly_guard(now) == []
    assert sent == []
