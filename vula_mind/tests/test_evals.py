"""The eval harness (evals/): routing cases gate CI; the live tool-choice layer is checked
here with a fake model so its plumbing (real prompts, real toolsets, scoring) can't rot."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from evals import harness

ROUTING = harness.load("routing.yaml")


@pytest.mark.parametrize("case", ROUTING, ids=[c["prompt"][:40] for c in ROUTING])
def test_routing_case(case):
    got, how = harness.route(case["prompt"], case.get("tenant"))
    assert got == case["expect"], f"{case['prompt']!r} -> {got} ({how}); {case.get('note', '')}"


def _fake_completion(pick):
    async def _acompletion(**kw):
        user = kw["messages"][-1]["content"]
        tool = pick(user, {t["function"]["name"] for t in kw["tools"]})
        calls = [SimpleNamespace(function=SimpleNamespace(name=tool, arguments="{}"))] if tool else []
        msg = SimpleNamespace(tool_calls=calls, content="" if tool else "Sure.")
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None)
    return _acompletion


@pytest.mark.asyncio
async def test_tool_layer_scores_a_perfect_model_as_perfect():
    expected = {c["prompt"]: c.get("expect") for c in harness.load("tool_choice.yaml")}
    with patch("litellm.acompletion", _fake_completion(lambda p, offered: expected[p])), \
         patch("litellm.completion_cost", return_value=0.0):
        report = await harness.run_tools("openrouter/fake")
    failed = [r for r in report["rows"] if not r["ok"]]
    assert not failed, failed
    assert report["passed"] == report["total"] > 0


@pytest.mark.asyncio
async def test_tool_layer_fails_forbidden_and_unoffered_tools():
    # Always answer with update_stock: right for nothing here, forbidden for the rep case, and
    # not even offered to a rep — every case must fail.
    with patch("litellm.acompletion", _fake_completion(lambda p, offered: "update_stock")), \
         patch("litellm.completion_cost", return_value=0.0):
        report = await harness.run_tools("openrouter/fake", skill="commerce_admin")
    assert report["passed"] == 0
