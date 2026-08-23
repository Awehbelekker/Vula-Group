"""Executes benchmark scenarios against the real, live-configured model and scores them.

Not a pytest file on purpose (see scripts/benchmarks/__init__.py) — this hits whatever
core.llm_router actually routes to in the current environment (local Ollama or cloud, same as
production traffic would get), so results reflect real system behaviour, not a mock's opinion
of it.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional
from unittest.mock import patch

from core.skills.base import SkillInput
from core.skills.loader import get_skill
from scripts.benchmarks.scenarios import CheckResult, Scenario
from vula.commerce.service import format_history

# Skills whose tool calls we can capture precisely by wrapping their dispatch method — extend
# this as more skills get tool_selection-category scenarios. Skills not listed here still run
# fine; their scenarios just can't use tool-call-based checks (answer-text checks only).
_DISPATCH_METHOD = {
    "commerce_admin": "_dispatch_tool",
    "commerce_assistant": "_dispatch_tool",
    "clickup_admin": "_dispatch_tool",
}


@dataclass
class TurnRecord:
    message: str
    answer: str
    latency_ms: int
    confidence: float
    check: Optional[CheckResult]


@dataclass
class ScenarioRecord:
    scenario: Scenario
    turns: List[TurnRecord] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def passed(self) -> bool:
        if self.error:
            return False
        checks = [t.check for t in self.turns if t.check is not None]
        if not all(c.passed for c in checks):
            return False
        if self.scenario.max_latency_ms is not None:
            return max((t.latency_ms for t in self.turns), default=0) <= self.scenario.max_latency_ms
        return True

    @property
    def reason(self) -> str:
        if self.error:
            return f"error: {self.error}"
        for t in self.turns:
            if t.check is not None and not t.check.passed:
                return t.check.reason
        if self.scenario.max_latency_ms is not None:
            worst = max((t.latency_ms for t in self.turns), default=0)
            if worst > self.scenario.max_latency_ms:
                return f"took {worst}ms, over the {self.scenario.max_latency_ms}ms threshold"
        return "passed"


async def run_scenario(scenario: Scenario) -> ScenarioRecord:
    record = ScenarioRecord(scenario=scenario)
    skill = get_skill(scenario.skill_name)
    dispatch_attr = _DISPATCH_METHOD.get(scenario.skill_name)
    captured: List[tuple] = []
    history: List[dict] = []

    async def _capturing_dispatch(self, name, args, *a, **kw):
        captured.append((name, dict(args) if isinstance(args, dict) else args))
        return await orig_dispatch(self, name, args, *a, **kw)

    patcher = None
    if dispatch_attr:
        orig_dispatch = getattr(type(skill), dispatch_attr)
        patcher = patch.object(type(skill), dispatch_attr, _capturing_dispatch)

    try:
        if patcher:
            patcher.start()
        for turn in scenario.turns:
            inp = SkillInput(
                question=turn.message,
                tenant_id=scenario.tenant_id,
                conversation_history=format_history(history),
                metadata=dict(scenario.metadata),
            )
            started = time.monotonic()
            out = await skill(inp)
            latency_ms = int((time.monotonic() - started) * 1000)
            history.append({"role": "user", "content": turn.message})
            history.append({"role": "assistant", "content": out.answer})
            check = turn.check(out.answer, list(captured), latency_ms) if turn.check else None
            record.turns.append(TurnRecord(
                message=turn.message, answer=out.answer, latency_ms=latency_ms,
                confidence=out.confidence, check=check,
            ))
    except Exception as exc:  # noqa: BLE001 — a scenario blowing up is itself a finding, not a crash
        record.error = str(exc)
    finally:
        if patcher:
            patcher.stop()

    return record


async def run_all(scenarios: List[Scenario]) -> List[ScenarioRecord]:
    results = []
    for s in scenarios:
        results.append(await run_scenario(s))
    return results


def scorecard(results: List[ScenarioRecord]) -> str:
    by_category: dict[str, list[ScenarioRecord]] = {}
    for r in results:
        by_category.setdefault(r.scenario.category, []).append(r)

    lines = []
    total_passed = sum(1 for r in results if r.passed)
    lines.append(f"OVERALL: {total_passed}/{len(results)} passed")
    lines.append("")
    for category, records in sorted(by_category.items()):
        passed = sum(1 for r in records if r.passed)
        lines.append(f"[{category}] {passed}/{len(records)}")
        for r in records:
            mark = "PASS" if r.passed else "FAIL"
            worst_ms = max((t.latency_ms for t in r.turns), default=0)
            lines.append(f"  {mark}  {r.scenario.id}  ({worst_ms}ms)  — {r.reason}")
        lines.append("")
    return "\n".join(lines)
