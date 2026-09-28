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


@pytest.mark.asyncio
async def test_a_model_with_no_tool_endpoint_stops_after_one_case():
    """2026-09-28: qwen3-235b produced 35 identical NotFoundErrors; one is enough."""
    calls = []

    async def not_found(**kw):
        calls.append(1)
        raise type("NotFoundError", (Exception,), {})("OpenrouterException - No endpoints found")
    with patch("litellm.acompletion", not_found):
        with pytest.raises(RuntimeError, match="no tool-calling endpoint"):
            await harness.run_tools("openrouter/fake")
    assert len(calls) == 1


def test_the_customer_assistant_knows_todays_date():
    """2026-09-28 bake-off: every model failed "What times are free on Thursday?" — the booking
    tools need a YYYY-MM-DD date and the customer prompt never said what today is."""
    from datetime import datetime, timedelta, timezone
    from core.skills.commerce_assistant import CommerceAssistantSkill
    today = datetime.now(timezone(timedelta(hours=2))).strftime("%A, %d %B %Y")
    for booking_focused in (False, True):
        prompt = CommerceAssistantSkill()._system_prompt("eval-sandbox", "", booking_focused=booking_focused)
        assert f"Today is {today}" in prompt


def test_owner_and_rep_prompts_say_a_preview_is_the_confirmation():
    """2026-09-28 bake-off: "Book Sarah in for a consult on Tuesday at 2pm" — Gemini Flash (and
    Haiku, GPT-5 mini) asked in text instead of calling create_booking, following "show the
    details and wait for a clear yes". A confirm-flag tool's preview IS that step (Confirm/Cancel
    buttons), so both prompts now say to call it without confirm."""
    from core.skills.commerce_admin import CommerceAdminSkill
    for role in ("owner", "sales_rep"):
        prompt = CommerceAdminSkill()._system_prompt("eval-sandbox", role=role, name="Eval")
        assert "create_booking" in prompt and "Confirm/Cancel" in prompt, role
