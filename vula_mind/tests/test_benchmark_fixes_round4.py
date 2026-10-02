"""Fixes from the benchmark run of 1 Oct, 12:57 (125/141), in the benchmark's own wording."""
from unittest.mock import MagicMock, patch

import pytest


def _async_return(value):
    async def f(*a, **k):
        return value
    return f


# ── 2. "SOLID CAPE (PTY) LTD" matches its own invoices ─────────────────────────

def test_a_supplier_name_with_brackets_matches_words_in_order():
    from vula.commerce import service
    chain = MagicMock()
    for m in ("table", "select", "eq", "order", "or_", "limit"):
        getattr(chain, m).return_value = chain
    chain.execute.return_value.data = []
    with patch.object(service, "_client", return_value=chain):
        service._filed_rows_query("digg-demo", [service._pg_term("SOLID CAPE (PTY) LTD")], "Invoice", True)
    flt = chain.or_.call_args[0][0]
    assert "fields->>supplier.ilike.%SOLID%CAPE%PTY%LTD%" in flt   # matched 40 real invoices; old form matched 0


# ── 3. one document → find_document ─────────────────────────────────────────────

def test_a_specific_invoice_routes_to_find_document_not_the_ledger():
    from core.hrm.orchestrator import HRMOrchestrator
    with patch.object(HRMOrchestrator, "_llm_classify_skill", return_value="finance_admin") as clf:
        skill, why = HRMOrchestrator()._route_with_reason("Find the Solid Cape invoice for R7,571.44", "digg-demo")
    assert (skill, why) == ("email_admin", "document_lookup")
    clf.assert_not_called()


@pytest.mark.parametrize("text,skill", [
    ("What is 15% VAT on R48,300.00?", "calculations"),
    ("How much have we spent with Solid Cape?", "email_admin"),
])
def test_amounts_without_a_document_are_not_lookups(text, skill):
    from core.hrm.orchestrator import HRMOrchestrator
    assert HRMOrchestrator()._route_with_reason(text, "digg-demo")[0] == skill


# ── 4. self-correction preamble ─────────────────────────────────────────────────

def test_a_revised_response_preamble_is_cut():
    from core.skills.base import strip_narration
    raw = ("This means that I should not claim that the letter was sent, but rather explain what "
           "would happen if the action were to be taken. Here is the revised response:\n\nDear Client,\n\n"
           "I have drafted a letter to the City of Cape Town.")
    assert strip_narration(raw).startswith("Dear Client,")


def test_a_reply_that_merely_mentions_a_revised_quote_is_untouched():
    from core.skills.base import strip_narration
    text = "Here is the revised quote you asked for: 3 items."
    assert strip_narration(text) == text


# ── 5. broadcast preview says what goes out ─────────────────────────────────────

@pytest.mark.asyncio
async def test_the_broadcast_preview_names_the_template_and_audience():
    from core.skills.commerce_admin import CommerceAdminSkill, _preview_summary
    skill = CommerceAdminSkill()
    with patch.object(CommerceAdminSkill, "_preview_broadcast", side_effect=_async_return({"would_reach": 738})):
        out = await skill._send_broadcast("off-the-hook", {"template_name": "fresh_in", "audience": "all"})
    text = _preview_summary(out)
    assert "fresh_in" in text and "All customers" in text and "738 customers" in text


# ── 6 & 8. the judge sees what tools do and what documents were read ──────────

@pytest.mark.asyncio
async def test_the_judge_prompt_carries_tool_descriptions_and_searched_documents():
    from evals import benchmark
    seen = {}

    async def fake_completion(**kw):
        seen["user"] = kw["messages"][1]["content"]
        msg = MagicMock(content='{"score": 5, "reason": "ok"}')
        return MagicMock(choices=[MagicMock(message=msg)])
    with patch("litellm.acompletion", side_effect=fake_completion), \
         patch("litellm.completion_cost", return_value=0.0):
        await benchmark.judge(
            {"prompt": "Log the Buildmax meeting"}, {}, "Logged, and reminders set.",
            [{"tool": "log_meeting", "args": {}, "executed": False}], "openrouter/x",
            tool_docs={"log_meeting": "creates a real reminder for each action item"},
            kb=[{"filename": "Affinity test report.pdf", "text": "Slip resistance R10 EN 16165"}])
    assert "creates a real reminder" in seen["user"]
    assert "Affinity test report.pdf" in seen["user"] and "R10" in seen["user"]


def test_tool_docs_come_from_the_skill_module():
    from core.skills.commerce_admin import CommerceAdminSkill
    from evals.benchmark import _tool_docs
    docs = _tool_docs(CommerceAdminSkill())
    assert "reminder" in docs["log_meeting"]
