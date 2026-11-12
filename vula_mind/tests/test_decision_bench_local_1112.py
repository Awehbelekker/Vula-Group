"""The decision benchmark can measure models on the local box (12 Nov 2026).

Choosing the 11 GB card's text model (qwen3:8b against llama3.1:8b) on Vula's own labelled cases:
each local model gets today's prompt for the decision with no cloud fallback, one model at a time
(the box holds one text model; interleaving would time reloads), first answer as an uncounted
warm-up, and is "ready" on the same bar as Jev: within 2 points of today, under 20% failures.
"""
import asyncio
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from vula import decision_bench as bench


def _reply(text):
    msg = types.SimpleNamespace(content=text)
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])


@pytest.mark.asyncio
async def test_local_models_run_one_after_another_with_an_uncounted_warm_up(monkeypatch):
    from core import reasoning_telemetry
    monkeypatch.setattr(reasoning_telemetry, "emit", lambda **k: None)
    calls = []

    async def fake_local(model, decision, case):
        calls.append(model)
        await asyncio.sleep(0)
        return {"answers": bench.expected(decision, case), "ok": True, "error": None,
                "confidence": None, "cost_usd": 0.0, "latency_ms": 100}

    async def current(decision, case):
        return {"answers": bench.expected(decision, case), "ok": True, "error": None,
                "confidence": None, "cost_usd": None, "latency_ms": 900}
    monkeypatch.setattr(bench, "_local", fake_local)
    monkeypatch.setattr(bench, "SIDES", {"jev": current, "current": current})
    out = await bench.run("bench-local", decisions=["escalation"], limit=5,
                          providers=["current", "local:qwen3:8b", "local:llama3.1:8b"])
    # 1 warm-up + 5 cases per model, and never interleaved
    assert calls == ["qwen3:8b"] * 6 + ["llama3.1:8b"] * 6
    res = out["results"]["escalation"]
    assert res["local:qwen3:8b"]["n"] == 5 and res["local:llama3.1:8b"]["n"] == 5
    assert res["local_verdicts"] == {"local:qwen3:8b": "ready", "local:llama3.1:8b": "ready"}
    assert out["providers"] == ["current", "local:qwen3:8b", "local:llama3.1:8b"]


def test_a_local_model_behind_today_is_not_ready():
    def rows(provider, outcomes):
        return [{"decision": "skill", "provider": provider, "case_id": f"s-{i}", "lang": "en",
                 "correct": c, "ok": ok, "error": None if ok else "Timeout", "latency_ms": 500,
                 "confidence": None, "cost_usd": None} for i, (c, ok) in enumerate(outcomes)]
    cur = rows("current", [(True, True)] * 9 + [(False, True)])
    worse = rows("local:llama3.1:8b", [(True, True)] * 8 + [(False, True)] * 2)
    flaky = rows("local:qwen3:8b", [(True, True)] * 7 + [(False, False)] * 3)   # 30% no answer
    v = bench.summarise(cur + worse + flaky)["skill"]["local_verdicts"]
    assert v == {"local:llama3.1:8b": "keep current", "local:qwen3:8b": "keep current"}


def test_only_ollama_model_names_are_accepted():
    assert bench.valid_provider("local:qwen3:8b") and bench.valid_provider("local:glm-ocr")
    assert bench.valid_provider("current") and bench.valid_provider("jev")
    assert not bench.valid_provider("local:../../etc") and not bench.valid_provider("local:")
    assert not bench.valid_provider("local:a b") and not bench.valid_provider("cloud")


@pytest.mark.asyncio
async def test_local_skill_has_no_cloud_fallback():
    from core.hrm import orchestrator
    resp = MagicMock()
    resp.raise_for_status = MagicMock(side_effect=RuntimeError("box down"))
    case = {"text": "Put 'order tiles' on my to-do board", "expect": "clickup_admin", "id": "s1"}
    with (patch.object(orchestrator.httpx, "post", return_value=resp),
          patch.object(orchestrator.HRMOrchestrator, "_cloud_classify_skill") as cloud):
        got = await bench._local("qwen3:8b", "skill", case)
    cloud.assert_not_called()
    assert got["ok"] is False and got["error"] == "RuntimeError"


@pytest.mark.asyncio
async def test_local_escalation_and_expense_use_todays_prompts():
    from vula.escalation import escalation_prompt
    from vula.commerce.expenses import purpose_prompt
    acomp = AsyncMock(side_effect=[_reply('{"wants_person": true, "upset": false}'),
                                   _reply('{"category": "clients"}')])
    with patch("litellm.acompletion", acomp):
        esc = await bench._local("qwen3:8b", "escalation",
                                 {"text": "Can I speak to the manager please", "id": "e1"})
        exp = await bench._local("qwen3:8b", "expense_purpose",
                                 {"vendor": "Zqx Trading 77", "amount_cents": 18500, "id": "x1"})
    assert esc["answers"] == {"wants_person": "yes", "upset": "no"} and esc["ok"]
    assert exp["answers"] == {"category": "clients"}
    first = acomp.await_args_list[0].kwargs
    assert first["messages"][0]["content"] == escalation_prompt("Can I speak to the manager please")
    assert first["model"].endswith("/qwen3:8b") and first["temperature"] == 0
    assert acomp.await_args_list[1].kwargs["messages"][0]["content"] == purpose_prompt("Zqx Trading 77", 18500)


@pytest.mark.asyncio
async def test_the_endpoint_takes_up_to_three_local_models(monkeypatch):
    from vula.api import master
    seen = {}

    async def fake_run(run_id, **k):
        seen.update(k)
        return {}
    monkeypatch.setattr(bench, "run", fake_run)
    monkeypatch.setattr(bench, "run_in_progress", lambda *a, **k: None)
    master._BENCH_TASKS.clear()
    out = await master.decision_bench_start({"local_models": ["qwen3:8b", "llama3.1:8b"]})
    await asyncio.sleep(0)
    assert out["providers"] == ["current", "local:qwen3:8b", "local:llama3.1:8b"]
    assert seen["providers"] == out["providers"]
    master._BENCH_TASKS.clear()
    with pytest.raises(HTTPException) as e:
        await master.decision_bench_start({"local_models": ["a", "b", "c", "d"]})
    assert e.value.status_code == 422
    with pytest.raises(HTTPException) as e:
        await master.decision_bench_start({"local_models": ["qwen3:8b; rm -rf"]})
    assert e.value.status_code == 422
    # No local models: today's run, unchanged.
    out = await master.decision_bench_start({})
    assert out["providers"] == list(bench.PROVIDERS)
    master._BENCH_TASKS.clear()
