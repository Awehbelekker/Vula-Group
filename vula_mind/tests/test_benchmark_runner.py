"""Tests for the benchmark harness's own plumbing (scripts/benchmarks/) — deterministic, mocked,
so this runs in CI like everything else. The benchmark scenarios THEMSELVES are not run here on
purpose (they hit the real model — see scripts/benchmarks/__init__.py); this only checks that
the runner correctly drives a skill, captures tool calls, scores turns, and reports latency.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import core.skills.commerce_admin as ca
from scripts.benchmarks.runner import run_scenario, scorecard
from scripts.benchmarks.scenarios import CheckResult, Scenario, Turn

TID = "off-the-hook"


def _resp(content="", tool_calls=None):
    resp = MagicMock()
    resp.choices = [SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))]
    return resp


def _tool_call(call_id, name, args_json):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=args_json))


async def _fake_route(*a, **kw):
    return ("openrouter/test", "k", None)


@pytest.mark.asyncio
async def test_run_scenario_captures_the_tool_that_was_actually_called():
    scenario = Scenario(
        id="t1", category="tool_selection", description="d", skill_name="commerce_admin",
        tenant_id=TID, metadata={"customer_phone": "27737815979"},
        turns=[Turn(message="what were sales today?",
                    check=lambda answer, calls, ms: CheckResult(
                        any(n == "sales_summary" for n, _ in calls), "checked"))],
    )
    call_n = {"n": 0}

    async def fake_completion(*a, **kw):
        call_n["n"] += 1
        if call_n["n"] == 1:
            return _resp(tool_calls=[_tool_call("c1", "sales_summary", "{}")])
        return _resp(content="R450 today.")

    with (
        patch.object(ca, "resolve_generation_route", new=_fake_route),
        patch.object(ca, "escalate_to_cloud", return_value=("openrouter/test", "k", None)),
        patch.object(ca, "service") as mock_service,
        patch("litellm.acompletion", new=fake_completion),
    ):
        mock_service._client.return_value.table.return_value.select.return_value \
            .eq.return_value.gte.return_value.execute.return_value = MagicMock(data=[])
        record = await run_scenario(scenario)

    assert record.error is None
    assert record.passed is True
    assert record.turns[0].answer == "R450 today."
    assert record.turns[0].latency_ms >= 0


@pytest.mark.asyncio
async def test_run_scenario_fails_when_check_fails():
    scenario = Scenario(
        id="t2", category="tool_selection", description="d", skill_name="commerce_admin",
        tenant_id=TID, metadata={"customer_phone": "27737815979"},
        turns=[Turn(message="anything",
                    check=lambda answer, calls, ms: CheckResult(False, "wrong tool"))],
    )

    async def fake_completion(*a, **kw):
        return _resp(content="some answer")

    with (
        patch.object(ca, "resolve_generation_route", new=_fake_route),
        patch.object(ca, "escalate_to_cloud", return_value=("openrouter/test", "k", None)),
        patch("litellm.acompletion", new=fake_completion),
    ):
        record = await run_scenario(scenario)

    assert record.passed is False
    assert record.reason == "wrong tool"


def test_max_latency_ms_fails_the_scenario_even_with_a_passing_check():
    from scripts.benchmarks.runner import ScenarioRecord, TurnRecord

    scenario = Scenario(id="t3", category="response_time", description="d",
                        skill_name="commerce_admin", tenant_id=TID, turns=[Turn(message="x")],
                        max_latency_ms=1000)
    record = ScenarioRecord(scenario=scenario, turns=[
        TurnRecord(message="x", answer="ok", latency_ms=5000, confidence=0.8, check=None)])

    assert record.passed is False
    assert "5000ms" in record.reason


def test_scenario_error_fails_regardless_of_checks():
    from scripts.benchmarks.runner import ScenarioRecord

    scenario = Scenario(id="t4", category="tool_selection", description="d",
                        skill_name="commerce_admin", tenant_id=TID, turns=[])
    record = ScenarioRecord(scenario=scenario, error="boom")

    assert record.passed is False
    assert "boom" in record.reason


def test_scorecard_formats_pass_fail_counts():
    from scripts.benchmarks.runner import ScenarioRecord, TurnRecord

    passing = ScenarioRecord(scenario=Scenario(
        id="p1", category="tool_selection", description="d", skill_name="commerce_admin",
        tenant_id=TID, turns=[]), turns=[TurnRecord("x", "y", 100, 0.8, CheckResult(True, "ok"))])
    failing = ScenarioRecord(scenario=Scenario(
        id="f1", category="tool_selection", description="d", skill_name="commerce_admin",
        tenant_id=TID, turns=[]), turns=[TurnRecord("x", "y", 100, 0.8, CheckResult(False, "nope"))])

    out = scorecard([passing, failing])
    assert "OVERALL: 1/2 passed" in out
    assert "PASS  p1" in out
    assert "FAIL  f1  " in out and "nope" in out
