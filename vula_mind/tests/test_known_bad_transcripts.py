"""A durable catalog of confirmed real production failures, one test per incident, in one place.

Started 2026-08-22 after the user pushed back on how reliability work was going: every fix this
session came from someone noticing a bad transcript, then got patched into whichever one skill
it happened in. This file is the answer to "how do we know it's actually getting better" — every
new real bug found from here on gets a permanent regression test added here, not just a fix
buried in the skill that happened to break. Growing this file over time (and its pass rate
staying at 100%) is the actual measurable signal, not anecdote.

Where a bug's fix already has direct test coverage elsewhere, this file cross-references rather
than duplicating — its job is to be the index, not to re-implement every test.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

TID = "test-tenant"


# ── 1. Leaked internal merge-failure string (DIGG, digg-demo) ───────────────────────────────
# "Hi what info do you have on solid Cape" got the literal string "[No successful branches —
# all failed]" sent verbatim to a real customer. Fixed in core/thinkmesh/merger.py; covered by
# tests/test_thinkmesh_merger.py.


# ── 2. Fabricated "logged as expense" claim with zero backing (DIGG, digg-demo) ─────────────
# "Logg as expense" -> Vula claimed R70,400.00 was logged; commerce_expenses had no such row.
# Root cause: reasoning.py's verification_policy was never activated despite the checking
# machinery already existing. Covered by tests/test_reasoning_skill.py (adversarial policy +
# tenant-data-question decline tests).


# ── 3. Tool-misselection: three wrong tools in a row instead of asking (DIGG, digg-demo) ────
# "please re-look at the proof of payment" -> bookings, then log_meeting, then finance_insights,
# never asking which document was meant. Fixed via commerce_admin.py's find_document tool.
# Covered by tests/test_commerce_admin_find_document.py.


# ── 4. AP/AR mixing inflated "outstanding invoices" (Staci, off-the-hook) ───────────────────
# Real transcript, 2026-08-22: "outstanding invoices" grew from a real 25/R37,938.69 to a
# nonsense 79/R109,743.11 because inbound supplier bills (direction="inbound", money OWED, not
# owed-to) were being summed into the same total as her real outbound sales invoices. Fixed by
# filtering direction="outbound" in commerce_admin.py's _outstanding_invoices.

@pytest.mark.asyncio
async def test_outstanding_invoices_excludes_inbound_supplier_bills():
    from core.skills.commerce_admin import CommerceAdminSkill
    import core.skills.commerce_admin as ca

    real_outbound = [
        {"invoice_number": "OFF-INV-00002", "status": "sent", "total_cents": 30000,
         "customer_name": "Paola"},
    ]
    # A real inbound supplier bill, shaped exactly like commerce/service.py's
    # commit_inbound_document writes it: customer_name is the tenant's own name, not a real
    # customer, since there's no "customer" for a bill the business owes.
    inbound_supplier_bills = [
        {"invoice_number": "OFF-INV-00078", "status": "draft", "total_cents": 144900,
         "customer_name": "Off the Hook"},
        {"invoice_number": "OFF-INV-00077", "status": "draft", "total_cents": 305193,
         "customer_name": "Off the Hook"},
    ]

    async def list_invoices(tid, status=None, direction=None, limit=100):
        if direction == "outbound":
            return [inv for inv in real_outbound if inv["status"] == status]
        # Old (buggy) call shape used no direction filter — assert nothing calls it that way.
        assert direction == "outbound", "must always pass direction='outbound'"
        return []

    with patch.object(ca, "service") as mock_service:
        mock_service.list_invoices = list_invoices
        skill = CommerceAdminSkill()
        res = await skill._outstanding_invoices(TID)

    assert res["count"] == 1
    assert res["outstanding_total"] == "R300.00"
    assert all(inv["customer"] != "Off the Hook" for inv in res["invoices"])
    # sanity: the fixture's inbound bills would have summed to R4,500.93 if direction leaked
    # through unfiltered — confirms the mock itself models the real bug shape correctly.
    assert sum(b["total_cents"] for b in inbound_supplier_bills) == 450093


@pytest.mark.asyncio
async def test_outstanding_invoices_drops_drafts_from_the_total():
    from core.skills.commerce_admin import CommerceAdminSkill
    import core.skills.commerce_admin as ca

    async def list_invoices(tid, status=None, direction=None, limit=100):
        assert direction == "outbound"
        if status == "sent":
            return [{"invoice_number": "OFF-INV-1", "status": "sent", "total_cents": 10000,
                     "customer_name": "Real Customer"}]
        return []

    with patch.object(ca, "service") as mock_service:
        mock_service.list_invoices = list_invoices
        skill = CommerceAdminSkill()
        res = await skill._outstanding_invoices(TID)

    assert res["count"] == 1
    assert res["outstanding_total"] == "R100.00"


# ── 5. A garbled wall of repeated characters sent live (Staci, off-the-hook) ────────────────
# 2026-08-22T04:50:42: "Ok great, where do I view it?" got back ~1000 literal '!' characters,
# nothing else. Fixed via core.llm_router.looks_degenerate, wired into every agentic skill's
# run(). Direct looks_degenerate unit tests live in tests/test_llm_router.py — this test
# confirms the wiring actually replaces the answer at the skill level, not just that the
# detector function works in isolation.

@pytest.mark.asyncio
async def test_commerce_admin_run_replaces_a_degenerate_reply_with_a_safe_fallback():
    from core.skills.commerce_admin import CommerceAdminSkill
    from core.skills.base import SkillInput
    from core.llm_router import DEGENERATE_OUTPUT_FALLBACK
    import core.skills.commerce_admin as ca

    async def fake_agent_loop(*args, **kwargs):
        return "!" * 1000

    with patch.object(ca.CommerceAdminSkill, "_agent_loop", fake_agent_loop):
        skill = CommerceAdminSkill()
        inp = SkillInput(question="Ok great, where do I view it?", tenant_id=TID,
                         conversation_history="", metadata={"customer_phone": "27737815979"})
        out = await skill.run(inp)

    assert out.answer == DEGENERATE_OUTPUT_FALLBACK
    assert "!!!" not in out.answer


# ── 6. A fully fabricated "invoice created" success, invoice number and all (Staci, off-the-hook) ──
# Real transcript, 2026-08-22T04:49:30: create_invoice was called 3 times in one turn with the
# SAME broken args (a "Delivery fee" line item missing unit_price_rands — confirmed live via
# direct reproduction: it deterministically returns status:"need_info" for that exact shape).
# Nothing in the loop noticed the tool kept saying the same thing; it burned all 3 attempts, hit
# the "final pass" fallback, and that pass invented a complete success story with a real-looking
# invoice number that was never actually generated (confirmed: zero rows in commerce_invoices for
# off-the-hook that day). Root cause was two-fold: (a) AGENTIC_RULES' "don't retry need_info
# blindly" was a prompt-only instruction and got ignored, (b) the adversarial verifier that
# should have caught the fabrication was silently failing open on every call that whole session
# (checker task_type="verification" was local-first-routed and hit its 8s timeout 5/5 times —
# fixed separately by adding "verification" to _FRONTIER_TASK_TYPES). Fixed here with a
# deterministic short-circuit (core.skills.base.need_info_message) that returns the tool's own
# "ask the user" message the FIRST time need_info comes back, rather than ever giving the model
# a chance to retry blindly or fabricate.

def _resp(content="", tool_calls=None):
    resp = MagicMock()
    resp.choices = [SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))]
    return resp


def _tool_call(call_id, name, args_json):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=args_json))


async def _fake_route(*a, **kw):
    return ("openrouter/test", "k", None)


@pytest.mark.asyncio
async def test_create_invoice_need_info_short_circuits_instead_of_fabricating_success():
    from core.skills.commerce_admin import CommerceAdminSkill
    import core.skills.commerce_admin as ca

    broken_args = (
        '{"doc_type": "invoice", "customer_name": "Regan", "line_items": '
        '[{"description": "Angel fish", "quantity": 2, "unit": "kg", "unit_price_rands": 100}, '
        '{"description": "Delivery fee", "quantity": 1}]}'
    )
    call_count = {"n": 0}

    async def fake_completion(*a, **kw):
        call_count["n"] += 1
        # A real create_invoice tool call every time — mirrors the real transcript exactly
        # (the model never adjusted the broken payload between attempts).
        return _resp(tool_calls=[_tool_call(f"c{call_count['n']}", "create_invoice", broken_args)])

    with (
        patch.object(ca, "resolve_generation_route", new=_fake_route),
        patch.object(ca, "escalate_to_cloud", return_value=("openrouter/test", "k", None)),
        patch("litellm.acompletion", new=fake_completion),
    ):
        skill = CommerceAdminSkill()
        answer = await skill._agent_loop(
            "system", "", "invoice for Regan, angel fish R100/kg 2kg, delivery R10",
            {"tenant_id": TID}, tools=None)

    # Must stop on the FIRST need_info — never a second identical retry, never the fabricating
    # final pass.
    assert call_count["n"] == 1
    assert "Delivery fee" in answer
    assert "created" not in answer.lower()
    assert "OFF-INV" not in answer


def test_need_info_message_helper():
    from core.skills.base import need_info_message

    assert need_info_message({"status": "need_info", "message": "which one?"}) == "which one?"
    assert need_info_message({"status": "need_info"}) is None  # no message to show — don't fire
    assert need_info_message({"created": True}) is None
    assert need_info_message({"error": "boom"}) is None
    assert need_info_message("not a dict") is None
    assert need_info_message(None) is None


@pytest.mark.asyncio
async def test_draft_admin_also_short_circuits_on_need_info():
    # Same fix, same shared need_info_message() — draft_admin's draft_letter uses the identical
    # {"status": "need_info", ...} shape (_fee_proposal_gaps).
    from core.skills.draft_admin import DraftAdminSkill
    import core.skills.draft_admin as da

    call_count = {"n": 0}

    async def fake_completion(*a, **kw):
        call_count["n"] += 1
        return _resp(tool_calls=[_tool_call(f"c{call_count['n']}", "draft_letter", "{}")])

    with (
        patch.object(da, "resolve_generation_route", new=_fake_route),
        patch.object(DraftAdminSkill, "_dispatch",
                     return_value={"status": "need_info", "missing": ["project value"],
                                   "message": "Before I draft this, I still need: project value."}),
        patch("litellm.acompletion", new=fake_completion),
    ):
        skill = DraftAdminSkill()
        answer = await skill._loop("", "draft a fee proposal", TID, "27737815979")

    assert call_count["n"] == 1
    assert "project value" in answer
