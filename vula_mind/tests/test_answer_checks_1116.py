"""16 Nov 2026, Ian: "we need to be accurate". evals/answer_checks.py re-asks real questions whose
right answer is in the business's own records (read live), after each deploy and weekly, checks
the figure in code, and says loudly when an answer that was right goes wrong."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from evals import answer_checks as ac
from evals import benchmark
from tests.fake_supabase import FakeSupabase


def test_every_case_is_well_formed_and_its_figure_kind_exists():
    cases = benchmark.load_cases(ac.CASES)
    assert len(cases) >= 6 and len({c["id"] for c in cases}) == len(cases)
    # every business with records to check (16 Nov: the other 8 tenants have no products, prices
    # or documents yet)
    assert {c["tenant"] for c in cases} >= {"digg-demo", "off-the-hook", "seedleme", "gerflor"}
    for t in ("digg-demo", "off-the-hook", "seedleme", "gerflor"):
        assert sum(1 for c in cases if c["tenant"] == t) >= 3, t
    for c in cases:
        for spec in c.get("amounts") or []:
            assert spec["kind"] in ac.AMOUNTS, c["id"]
        for spec in c.get("truth") or []:
            assert spec["kind"] in benchmark.TRUTH, c["id"]


def test_the_rate_is_read_from_the_businesss_own_rates(monkeypatch):
    monkeypatch.setattr("vula.api.qs.search_rates", lambda t, q, limit=25, include_learned=True: [
        {"description": "One brick walls circular on plan", "unit": "m2", "rate": 677.79},
        {"description": "One brick walls", "unit": "m2", "rate": 554.56}])
    assert asyncio.run(ac._amount_qs_rate("digg-demo", "one brick walls | m2")) == 55456
    assert asyncio.run(ac._amount_qs_rate("digg-demo", "one brick walls | each")) is None


def _row(answer):
    return {"id": "x", "component": "c", "tenant": "digg-demo", "prompt": "q", "answer": answer,
            "checks": {"answered": True}, "ok": True, "why": []}


@pytest.mark.parametrize("answer,ok", [
    ("Your rate for one brick walls is R554.56/m² (from your HPC Bokaap BOQ).", True),
    ("About R550 per m² is typical for a one-brick wall.", False),      # a general-knowledge guess
])
def test_the_figure_must_be_stated(monkeypatch, answer, ok):
    monkeypatch.setattr(benchmark, "run_agent_case", AsyncMock(return_value=_row(answer)))
    monkeypatch.setitem(ac.AMOUNTS, "qs_rate", AsyncMock(return_value=55456))
    r = asyncio.run(ac.run_case({"id": "x", "component": "c", "tenant": "digg-demo", "prompt": "q",
                                 "amounts": [{"kind": "qs_rate", "arg": "one brick walls | m2"}]}))
    assert r["ok"] is ok
    if not ok:
        assert r["why"] == ["didn't state R554.56"]


def test_a_figure_that_isnt_on_file_fails_with_why(monkeypatch):
    monkeypatch.setattr(benchmark, "run_agent_case", AsyncMock(return_value=_row("R150.00")))
    monkeypatch.setitem(ac.AMOUNTS, "product_price", AsyncMock(return_value=None))
    r = asyncio.run(ac.run_case({"id": "x", "component": "c", "tenant": "off-the-hook", "prompt": "q",
                                 "amounts": [{"kind": "product_price", "arg": "Gone product"}]}))
    assert not r["ok"] and r["why"] == ["expected_figure_on_file"]


def test_the_check_runs_read_only():
    """run_agent_case runs inside core.dry_run — no write tool runs, nothing is sent."""
    import inspect
    assert "dry_run.session()" in inspect.getsource(benchmark.run_agent_case)


def _db(rows):
    return FakeSupabase({"vula_eval_reports": rows})


def test_due_after_a_new_deploy_or_a_week_and_not_twice(monkeypatch):
    now = datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "dep-2")
    done = {"model": ac.MODEL, "status": "done", "created_at": (now - timedelta(days=1)).isoformat(),
            "report": {"deploy_id": "dep-1"}}
    monkeypatch.setattr(ac, "_client", lambda: _db([done]))
    assert ac.due(now) == "deploy"
    monkeypatch.setattr(ac, "_client", lambda: _db([{**done, "report": {"deploy_id": "dep-2"}}]))
    assert ac.due(now) is None
    old = {**done, "report": {"deploy_id": "dep-2"}, "created_at": (now - timedelta(days=8)).isoformat()}
    monkeypatch.setattr(ac, "_client", lambda: _db([old]))
    assert ac.due(now) == "weekly"
    running = {"model": ac.MODEL, "status": "running", "created_at": (now - timedelta(minutes=5)).isoformat()}
    monkeypatch.setattr(ac, "_client", lambda: _db([done, running]))
    assert ac.due(now) is None


def test_only_a_newly_wrong_answer_alerts(monkeypatch):
    prev = {"model": ac.MODEL, "status": "done", "created_at": "2026-11-15T09:00:00+00:00",
            "report": {"failing": ["oth-price-prawns"]}}
    db = _db([prev])
    monkeypatch.setattr(ac, "_client", lambda: db)
    report = {"kind": ac.MODEL, "passed": 6, "total": 8, "deploy_id": "dep-2",
              "failing": ["oth-price-prawns", "digg-rate-one-brick-wall"],
              "rows": [{"id": "oth-price-prawns", "tenant": "off-the-hook", "ok": False, "why": ["x"]},
                       {"id": "digg-rate-one-brick-wall", "tenant": "digg-demo", "ok": False,
                        "why": ["didn't state R554.56"]}]}
    monkeypatch.setattr(ac, "run", AsyncMock(return_value=report))
    alerts = []

    async def fake_alert(text):
        alerts.append(text)
        return {}

    monkeypatch.setattr("vula.ai_health._alert", fake_alert)
    asyncio.run(ac.run_and_store("deploy"))
    assert len(alerts) == 1 and "digg-rate-one-brick-wall" in alerts[0] and "oth-price-prawns" not in alerts[0]
    saved = [r for r in db.rows["vula_eval_reports"] if r.get("status") == "done" and r.get("passed") == 6]
    assert saved and saved[0]["report"]["deploy_id"] == "dep-2"
