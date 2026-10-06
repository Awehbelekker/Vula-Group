"""Chat rework step 3 (6 Oct 2026): replies built from checked results, and a supervisor check.

The supervisor keeps, per skill run, everything the model could legitimately take a figure from
(the question, the conversation, every model call's messages, every tool result) and checks the
model's answer: claimed actions need a tool that did them, every rand figure in an owner reply
must be in that evidence (or the sum/difference of two figures that are), and shown arithmetic
must add up. Only the faulty line or sentence is dropped; code-built replies are left alone.
"""
from unittest.mock import AsyncMock, patch

import pytest

from core import supervisor
from core.skills import commerce_admin as ca
from core.skills.base import BaseSkill, DB_ANSWER_SOURCE, SkillInput, SkillOutput, tool_source

TID = "digg-demo"

# The Jack Hammer account as filed (6 Oct): 34 documents, R42,949.00 total, R5,602.03 VAT.
JH_RESULT = {"supplier": "GARDENS HANDIMAN CENTRE", "total_amount_cents": 4294900,
             "vat_cents": 560203, "count": 34, "refunds_cents": -261000}


# ── figure matching ───────────────────────────────────────────────────────────

def test_figures_match_rands_cents_and_spaced_thousands():
    ev = ["{\"total_amount_cents\": 4294900, \"vat_cents\": 560203}", "Paint R1 800.00"]
    assert supervisor.unmatched_figures("Total R42,949.00 incl. R5,602.03 VAT", ev) == []
    assert supervisor.unmatched_figures("The paint was R1 800.00", ev) == []
    assert supervisor.unmatched_figures("You spent R43,000 there", ev) == ["R43,000"]


def test_a_rounded_figure_within_two_percent_is_fine():
    ev = ["{\"total_amount_cents\": 4294900}"]
    assert supervisor.unmatched_figures("About R43k so far", ev) == []
    assert supervisor.unmatched_figures("About R50k so far", ev) == ["R50k"]


def test_a_sum_or_difference_of_two_known_figures_is_fine():
    ev = ["owed 300000 paid 100000"]
    assert supervisor.unmatched_figures("R2,000 still owed after paying R1,000", ev) == []
    assert supervisor.unmatched_figures("R4,000 in total", ev) == []          # 3000 + 1000
    assert supervisor.unmatched_figures("R5,500 in total", ev) == ["R5,500"]


# ── the check ─────────────────────────────────────────────────────────────────

def _run_with(*texts, sources=()):
    token = supervisor.start()
    for t in texts:
        supervisor.add(t)
    for s in sources:
        supervisor.add_source(s)
    return token


def test_only_the_line_with_an_unmatched_figure_is_dropped():
    token = _run_with(sources=[tool_source("supplier_history", JH_RESULT)])
    try:
        out, found = supervisor.check(
            "Jack Hammer (Belladonna): 34 documents.\nTotal R42,949.00.\nLast month R12,000.00.",
            skill="commerce_admin", tenant_id=TID)
    finally:
        supervisor.stop(token)
    assert found == ["unverified_figure"]
    assert "R42,949.00" in out and "34 documents" in out
    assert "R12,000.00" not in out and out.endswith(supervisor.FIGURE_NOTE)


def test_no_tool_this_run_means_no_figure_check():
    token = _run_with("what were today's sales?")
    try:
        out, found = supervisor.check("Today's sales: R4,500.", skill="commerce_admin")
    finally:
        supervisor.stop(token)
    assert found == [] and out == "Today's sales: R4,500."


def test_customer_replies_are_not_figure_checked_here():
    # commerce_assistant has its own catalogue price check (unverified_prices)
    token = _run_with(sources=[tool_source("list_products", {"items": [{"price": "R220.00"}]})])
    try:
        out, found = supervisor.check("Hake is R199.", skill="commerce_assistant", customer=True)
    finally:
        supervisor.stop(token)
    assert found == [] and out == "Hake is R199."


def test_trusted_code_built_reply_is_left_alone():
    token = _run_with(sources=[tool_source("record_payment", {"recorded": "R1,000.00"})])
    try:
        text = supervisor.trust("✅ Recorded *R1,000.00*. Still owed: *R9,999.99*.")
        out, found = supervisor.check(text, skill="commerce_admin")
    finally:
        supervisor.stop(token)
    assert found == [] and out == text


def test_wrong_arithmetic_is_dropped():
    # 2026-08-31 Gerflor: 11.8 × 18.2 is 214.76, not 215.56
    out, found = supervisor.check("Area: 11.8 × 18.2 = 215.56 m².\nI'll price it next.",
                                  skill="commerce_admin")
    assert "wrong_arithmetic" in found
    assert "215.56" not in out and "I'll price it next." in out


def test_claimed_filing_with_no_tool_is_replaced():
    # 2026-09-28 Gerflor: "I can see a receipt … I have filed the receipt." — nothing was filed
    out, found = supervisor.check("I can see a fuel slip for R871.50. I have filed the receipt.",
                                  skill="commerce_admin")
    assert "unbacked_claim" in found and "I have filed" not in out


def test_claim_backed_by_a_successful_tool_stands():
    src = tool_source("add_expense", {"logged": "R871.50", "category": "fuel"})
    out, found = supervisor.check("I've logged the R871.50 fuel slip.", skill="commerce_admin",
                                  extra_sources=[src], evidence=[src["text"]])
    assert found == [] and out == "I've logged the R871.50 fuel slip."


# ── through a real skill call ─────────────────────────────────────────────────

class _Skill(BaseSkill):
    name = "commerce_admin"

    def __init__(self, answer, result=None, prompt=None, sources=None):
        self._answer, self._result, self._prompt, self._sources = answer, result, prompt, sources

    async def run(self, inp):
        if self._prompt is not None:      # what a model call in run() would have been given
            supervisor.saw_messages([{"role": "system", "content": self._prompt}])
        srcs = list(self._sources or [])
        if self._result is not None:
            srcs.append(tool_source("supplier_history", self._result))
        return SkillOutput(answer=self._answer, skill_name=self.name, sources=srcs)


@pytest.mark.asyncio
async def test_skill_answer_with_an_invented_figure_is_corrected():
    out = await _Skill("Jack Hammer total: R42,949.00.\nAverage per invoice: R1,500.00.",
                       result=JH_RESULT)(SkillInput(question="jack hammer total?", tenant_id=TID))
    assert "R42,949.00" in out.answer and "R1,500.00" not in out.answer


@pytest.mark.asyncio
async def test_figure_from_the_prompt_counts_as_evidence():
    out = await _Skill("Your fee is R2,500.00 per month.", result={"ok": True},
                       prompt="Business profile: monthly fee R2,500.00")(
        SkillInput(question="what's my fee?", tenant_id=TID))
    assert out.answer == "Your fee is R2,500.00 per month."


@pytest.mark.asyncio
async def test_code_built_database_answer_is_not_checked():
    out = await _Skill("Total: R99,999.00", sources=[DB_ANSWER_SOURCE], result=JH_RESULT)(
        SkillInput(question="total?", tenant_id=TID))
    assert out.answer == "Total: R99,999.00"


@pytest.mark.asyncio
async def test_every_model_call_is_recorded_as_evidence():
    import litellm
    seen = {}

    async def fake(*a, **kw):
        seen["called"] = True
        return "resp"
    with patch.object(litellm, "acompletion", new=fake):
        supervisor.install()                         # wraps whatever acompletion is current
        token = supervisor.start()
        try:
            assert await litellm.acompletion(model="m", messages=[
                {"role": "user", "content": "Paid R777.00 yesterday"}]) == "resp"
            assert seen["called"]
            assert supervisor.unmatched_figures("R777.00", supervisor._run()["texts"]) == []
        finally:
            supervisor.stop(token)


# ── replies built from checked results (step 3a) ─────────────────────────────

def test_checked_reply_prefers_reply_verbatim():
    assert ca.checked_reply({"reply_verbatim": "✅ a", "reply": "b"}) == "✅ a"
    assert ca.checked_reply({"reply": "✅ setup done"}) == "✅ setup done"
    assert ca.checked_reply({"updated": "x"}) is None


@pytest.fixture()
def skill():
    return ca.CommerceAdminSkill()


@pytest.mark.asyncio
async def test_record_payment_reply_states_paid_and_owed_from_the_read_back(skill, monkeypatch):
    monkeypatch.setattr(ca.settings, "readback_verify_enabled", True)
    monkeypatch.setattr(skill, "_find_invoice_by_number", AsyncMock(return_value={
        "id": "i1", "invoice_number": "DIG-INV-00042", "total_cents": 115000, "total_paid_cents": 0}))
    monkeypatch.setattr(ca.service, "record_invoice_payment", AsyncMock(return_value={
        "status": "part_paid", "balance_due_cents": 65000}), raising=False)
    monkeypatch.setattr(ca.service, "list_invoice_payments",
                        AsyncMock(return_value=[{"amount_cents": 50000}]))
    res = await skill._record_payment(TID, {"invoice_number": "DIG-INV-00042", "amount_rands": 500,
                                            "confirm": True})
    assert res["verified"] is True
    assert res["reply_verbatim"] == ("✅ Recorded *R500.00* against DIG-INV-00042.\n"
                                     "Paid so far: R500.00 · still owed: *R650.00* · status: part_paid.")


@pytest.mark.asyncio
async def test_record_payment_that_does_not_read_back_is_reported_not_softened(skill, monkeypatch):
    monkeypatch.setattr(ca.settings, "readback_verify_enabled", True)
    monkeypatch.setattr(skill, "_find_invoice_by_number", AsyncMock(return_value={
        "id": "i1", "invoice_number": "DIG-INV-00042", "total_cents": 115000, "total_paid_cents": 0}))
    monkeypatch.setattr(ca.service, "record_invoice_payment", AsyncMock(return_value={}), raising=False)
    monkeypatch.setattr(ca.service, "list_invoice_payments", AsyncMock(return_value=[]))
    res = await skill._record_payment(TID, {"invoice_number": "DIG-INV-00042", "amount_rands": 500,
                                            "confirm": True})
    assert "error" in res and res["reply_verbatim"].startswith("⚠️ Payment for DIG-INV-00042 did not persist")


def test_a_write_that_does_not_read_back_says_so_plainly():
    r = ca._not_confirmed("Payment for X did not persist. Not confirmed.")
    assert r["error"] and r["reply_verbatim"].startswith("⚠️ Payment for X did not persist")
