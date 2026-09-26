"""Master-panel model bake-off: queue a tool-choice eval per model, store each report
(migration 178), list them with headline numbers. The live model call is stubbed here."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from vula.api import master

MASTER = {"user_id": "master-1", "email": "ian@vula.ai"}


def _db(inserted_id="job-1"):
    db = MagicMock()
    db.table.return_value.insert.return_value.execute.return_value.data = [{"id": inserted_id}]
    return db


def test_eval_routes_are_master_only():
    paths = {r.path for r in master.router.routes}
    assert {"/evals/tools", "/evals/reports", "/evals/candidates"} <= paths
    assert any(getattr(d.dependency, "__name__", "") == "require_master" for d in master.router.dependencies)


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"models": []},
    {"models": ["gpt-4o"]},                                   # no provider prefix
    {"models": [f"openrouter/v/m{i}" for i in range(7)]},  # too many
    {"models": ["openrouter/a/b"], "skill": "finance_everything"},
])
async def test_run_rejects_bad_requests(body):
    with patch("vula.api.master.audit"), \
         patch("vula.api.master._client", return_value=_db()), \
         patch("config.settings.openrouter_api_key", "k"):
        with pytest.raises(HTTPException) as e:
            await master.master_run_tool_evals(body, identity=MASTER)
    assert e.value.status_code == 422


@pytest.mark.asyncio
async def test_run_needs_openrouter_key():
    with patch("vula.api.master._client", return_value=_db()), \
         patch("config.settings.openrouter_api_key", ""):
        with pytest.raises(HTTPException) as e:
            await master.master_run_tool_evals({"models": ["openrouter/anthropic/claude-haiku-4.5"]},
                                               identity=MASTER)
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_run_queues_and_backgrounds():
    db = _db()
    with patch("vula.api.master._client", return_value=db), \
         patch("vula.api.master.audit") as aud, \
         patch("config.settings.openrouter_api_key", "k"), \
         patch("vula.commerce.background_tasks.run_background") as bg:
        out = await master.master_run_tool_evals(
            {"models": ["openrouter/anthropic/claude-haiku-4.5"], "skill": "commerce_admin"}, identity=MASTER)
    assert out == {"queued": ["job-1"]}
    row = db.table.return_value.insert.call_args[0][0]
    assert row["model"] == "openrouter/anthropic/claude-haiku-4.5" and row["status"] == "running"
    aud.assert_called_once()
    bg.assert_called_once()
    bg.call_args[0][2].close()   # the queued coroutine — not run in this test


@pytest.mark.asyncio
async def test_batch_saves_report_and_flags_unknown_model():
    db = MagicMock()
    report = {"passed": 20, "total": 25, "p50_secs": 1.2, "rows": []}
    with patch("vula.api.master._client", return_value=db), \
         patch("vula.api.master._openrouter_model_ids", new=AsyncMock(return_value={"anthropic/claude-haiku-4.5"})), \
         patch("evals.harness.run_tools", new=AsyncMock(return_value=report)) as run:
        await master._run_eval_batch([("j1", "openrouter/anthropic/claude-haiku-4.5"),
                                      ("j2", "openrouter/made-up/model")], None)
    run.assert_awaited_once_with("openrouter/anthropic/claude-haiku-4.5", None)
    updates = [c[0][0] for c in db.table.return_value.update.call_args_list]
    assert updates[0]["status"] == "done" and updates[0]["passed"] == 20 and updates[0]["report"] == report
    assert updates[1]["status"] == "failed" and "Not an OpenRouter model id" in updates[1]["error"]


@pytest.mark.asyncio
async def test_batch_records_a_crash_as_failed():
    db = MagicMock()
    with patch("vula.api.master._client", return_value=db), \
         patch("vula.api.master._openrouter_model_ids", new=AsyncMock(return_value=None)), \
         patch("evals.harness.run_tools", new=AsyncMock(side_effect=RuntimeError("boom"))):
        await master._run_eval_batch([("j1", "openrouter/a/b")], None)
    upd = db.table.return_value.update.call_args[0][0]
    assert upd["status"] == "failed" and "boom" in upd["error"]


@pytest.mark.asyncio
async def test_reports_pull_out_headline_numbers_and_failures():
    db = MagicMock()
    db.table.return_value.select.return_value.order.return_value.limit.return_value.execute.return_value.data = [{
        "id": "j1", "model": "openrouter/a/b", "status": "done", "passed": 1, "total": 2,
        "report": {"p50_secs": 1.0, "p95_secs": 2.0, "cost_per_100_usd": 0.5, "errors": 0,
                   "rows": [{"prompt": "hi", "expect": "x", "got": "x", "ok": True},
                            {"prompt": "bye", "expect": "y", "got": None, "ok": False}]},
    }]
    with patch("vula.api.master._client", return_value=db):
        out = await master.master_eval_reports()
    r = out["reports"][0]
    assert r["p50_secs"] == 1.0 and r["cost_per_100_usd"] == 0.5
    assert r["failures"] == [{"prompt": "bye", "expect": "y", "got": None, "error": None}]


@pytest.mark.asyncio
async def test_feedback_cases_are_redacted_and_prefilled_with_todays_route():
    db = MagicMock()
    q = db.table.return_value
    for m in ("select", "eq", "order", "limit"):
        getattr(q, m).return_value = q
    q.execute.return_value.data = [{
        "id": "f1", "tenant_id": "digg-demo", "created_at": "2026-09-25T10:00:00Z",
        "question": "Email jan@site.co.za the Jack Hammer invoices, call me on 082 555 1234",
        "correction": "Send them to jan@site.co.za"}]
    with patch("vula.api.master._client", return_value=db), \
         patch("evals.harness.route", return_value=("email_admin", "keyword")):
        out = await master.master_feedback_cases()
    case = out["cases"][0]
    assert "jan@site" not in case["yaml"] and "555" not in case["yaml"]
    assert "<email>" in case["question"] and "<phone>" in case["question"]
    import yaml
    parsed = yaml.safe_load(case["yaml"])[0]
    assert parsed["expect"] == "email_admin" and parsed["tenant"] == "digg-demo"
