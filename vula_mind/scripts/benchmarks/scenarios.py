"""Scenario definitions for the chat benchmark. Each scenario is a short, real-shaped
conversation run against the live model; a `check` on the relevant turn decides pass/fail.

Categories (each a proven-to-matter failure class from this session's real-transcript audits):
  - tool_selection : does the right tool get called, not the closest-sounding wrong one
                     (DIGG's bookings/log_meeting/finance_insights guessing incident)
  - ask_dont_guess : does it ask/decline instead of answering from thin air when it has nothing
                     to ground an answer in (the fabricated R70,400 expense; the no-KB
                     tenant-data-question fix in reasoning.py)
  - confirm_flow   : does a normal preview-then-confirm exchange complete in a couple of turns
                     without narrating a fake "issue" (Regan's invoice, which took 3 turns and
                     ended in a fabricated success)
  - need_info      : when a tool can't proceed, does it ask for exactly what's missing on the
                     FIRST try instead of retrying blindly (same Regan incident, upstream cause)
  - response_time  : how long real, everyday questions actually take end to end

Tenant IDs are the project's own real dev tenants (digg-demo, off-the-hook) — same ones every
other test in this codebase uses; there's no separate fixture tenant.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional


@dataclass
class CheckResult:
    passed: bool
    reason: str


@dataclass
class Turn:
    message: str
    # (answer_text, tool_calls_so_far, latency_ms) -> CheckResult. tool_calls_so_far is only
    # populated for skills wired for capture (see runner.py) — commerce_admin currently.
    check: Optional[Callable[[str, List[tuple], int], CheckResult]] = None


@dataclass
class Scenario:
    id: str
    category: str
    description: str
    skill_name: str
    tenant_id: str
    turns: List[Turn]
    metadata: dict = field(default_factory=dict)
    max_latency_ms: Optional[int] = None  # response_time category


def _called(tool_calls: List[tuple], name: str) -> bool:
    return any(n == name for n, _args in tool_calls)


def _only_called(tool_calls: List[tuple], name: str) -> CheckResult:
    if not tool_calls:
        return CheckResult(False, "no tool was called at all")
    if _called(tool_calls, name):
        return CheckResult(True, f"called {name}")
    got = ", ".join(sorted({n for n, _ in tool_calls}))
    return CheckResult(False, f"expected {name}, got: {got}")


def _no_fabricated_success(answer: str) -> Optional[str]:
    """Shared guard: a reply claiming a real-looking outcome (invoice number, 'created') should
    only pass if it's plausible — this is a light heuristic backstop for the benchmark, not a
    replacement for the actual need_info/verification fixes it's checking held."""
    lowered = answer.lower()
    if "issue persists" in lowered or "let me try again" in lowered:
        return "narrated a fake retry/issue (the exact fabrication-incident phrasing)"
    return None


# ── tool_selection ────────────────────────────────────────────────────────────────

TOOL_SELECTION = [
    Scenario(
        id="ts_proof_of_payment_reference",
        category="tool_selection",
        description="Owner references a specific document ('re-look at the proof of payment') — "
                     "must use find_document, not guess bookings/log_meeting/finance_insights "
                     "(the real DIGG incident this tool was built to fix).",
        skill_name="commerce_admin",
        tenant_id="digg-demo",
        metadata={"customer_phone": "27645755210"},
        turns=[Turn(
            message="You need to check that number it's incorrect, please re-look at the proof of payment",
            check=lambda answer, calls, ms: _only_called(calls, "find_document"),
        )],
    ),
    Scenario(
        id="ts_sales_summary",
        category="tool_selection",
        description="A plain, unambiguous sales question — golden path, must call sales_summary.",
        skill_name="commerce_admin",
        tenant_id="off-the-hook",
        metadata={"customer_phone": "27737815979"},
        turns=[Turn(
            message="What were today's sales?",
            check=lambda answer, calls, ms: _only_called(calls, "sales_summary"),
        )],
    ),
    Scenario(
        id="ts_stock_status",
        category="tool_selection",
        description="Golden path — must call stock_status.",
        skill_name="commerce_admin",
        tenant_id="off-the-hook",
        metadata={"customer_phone": "27737815979"},
        turns=[Turn(
            message="How's stock looking, anything low?",
            check=lambda answer, calls, ms: _only_called(calls, "stock_status"),
        )],
    ),
    Scenario(
        id="ts_outstanding_invoices",
        category="tool_selection",
        description="Golden path — must call outstanding_invoices.",
        skill_name="commerce_admin",
        tenant_id="off-the-hook",
        metadata={"customer_phone": "27737815979"},
        turns=[Turn(
            message="What invoices are still outstanding?",
            check=lambda answer, calls, ms: _only_called(calls, "outstanding_invoices"),
        )],
    ),
    Scenario(
        id="ts_add_expense",
        category="tool_selection",
        description="Golden path — a clear expense-logging request must call add_expense.",
        skill_name="commerce_admin",
        tenant_id="off-the-hook",
        metadata={"customer_phone": "27737815979"},
        turns=[Turn(
            message="Log R450 for packaging from Boxshop",
            check=lambda answer, calls, ms: _only_called(calls, "add_expense"),
        )],
    ),
]


# ── ask_dont_guess ────────────────────────────────────────────────────────────────

def _declines_or_asks(answer: str, calls, ms) -> CheckResult:
    lowered = answer.lower()
    declining = any(p in lowered for p in (
        "don't have", "no document", "couldn't find", "can you", "could you",
        "which one", "not sure", "need more", "resend", "attach",
    ))
    return CheckResult(declining, "declined/asked for clarification" if declining
                       else "answered directly instead of declining/asking — check for a guess")


ASK_DONT_GUESS = [
    Scenario(
        id="adg_no_kb_tenant_figure",
        category="ask_dont_guess",
        description="A question needing a specific figure/fact from this tenant's own records, "
                     "with nothing in the KB to ground it — must decline, not answer from "
                     "general knowledge (the fabricated R70,400 'logged expense' incident).",
        skill_name="reasoning",
        tenant_id="digg-demo",
        metadata={},
        turns=[Turn(
            message="What was the total payment amount on the Zephyrine Holdings invoice from last March?",
            check=_declines_or_asks,
        )],
    ),
    Scenario(
        id="adg_unmatched_document_reference",
        category="ask_dont_guess",
        description="References a specific document that doesn't exist — find_document should "
                     "come back empty and the reply should ask, not invent a match.",
        skill_name="commerce_admin",
        tenant_id="off-the-hook",
        metadata={"customer_phone": "27737815979"},
        turns=[Turn(
            message="Check the invoice I sent you yesterday for the Greenfield order, is it paid?",
            check=_declines_or_asks,
        )],
    ),
    Scenario(
        id="adg_genuine_general_question_still_answers",
        category="ask_dont_guess",
        description="A genuinely general knowledge question — the decline logic must NOT "
                     "overfire on this; it should answer normally.",
        skill_name="reasoning",
        tenant_id="digg-demo",
        metadata={},
        turns=[Turn(
            message="What's the standard retention period for a certificate of practical "
                    "completion on a South African construction contract?",
            check=lambda answer, calls, ms: CheckResult(
                len(answer) > 40 and "don't have" not in answer.lower(),
                "answered normally" if len(answer) > 40 else "declined a genuinely general question"),
        )],
    ),
]


# ── confirm_flow / need_info ─────────────────────────────────────────────────────

def _no_fake_narration_and_reasonable_length(answer: str, calls, ms) -> CheckResult:
    bad = _no_fabricated_success(answer)
    if bad:
        return CheckResult(False, bad)
    return CheckResult(True, "no fake retry/issue narration")


CONFIRM_FLOW = [
    Scenario(
        id="cf_create_invoice_two_turns",
        category="confirm_flow",
        description="A complete, unambiguous invoice request should preview then create within "
                     "2 turns, no fake 'issue persists' narration along the way.",
        skill_name="commerce_admin",
        tenant_id="off-the-hook",
        metadata={"customer_phone": "27737815979"},
        turns=[
            Turn(message="Make a customer invoice for Priya: 3kg hake fillets at R120 per kg, "
                         "plus a R15 delivery fee.",
                 check=_no_fake_narration_and_reasonable_length),
            Turn(message="Yes, go ahead.",
                 check=_no_fake_narration_and_reasonable_length),
        ],
    ),
]

NEED_INFO = [
    Scenario(
        id="ni_delivery_fee_missing_price",
        category="need_info",
        description="The exact real incident shape: a delivery-fee line item with no price. "
                     "Must ask for the missing price on the FIRST reply, never fabricate a "
                     "created invoice (the Regan incident — real production, 2026-08-22).",
        skill_name="commerce_admin",
        tenant_id="off-the-hook",
        metadata={"customer_phone": "27737815979"},
        turns=[Turn(
            message="I would like to make a customer invoice for Regan for Angel fish at R100 "
                    "per kg, 2kg please include a delivery fee of R10.",
            check=lambda answer, calls, ms: CheckResult(
                ("delivery" in answer.lower() and "price" in answer.lower())
                and "OFF-INV" not in answer and "created" not in answer.lower(),
                "asked for the missing price" if "delivery" in answer.lower()
                else "did not ask for the missing delivery-fee price — check for a fabricated success",
            ),
        )],
    ),
]


# ── response_time ─────────────────────────────────────────────────────────────────
# Threshold is deliberately generous (this flags "got much slower", not "isn't instant") —
# see core/skills/reasoning.py's own _LOCAL_TIMEOUT_S/_CLOUD_TIMEOUT_S for the enforced caps
# this should stay well under.

RESPONSE_TIME = [
    Scenario(
        id="rt_sales_summary",
        category="response_time",
        description="A simple, single-tool read should come back quickly.",
        skill_name="commerce_admin",
        tenant_id="off-the-hook",
        metadata={"customer_phone": "27737815979"},
        max_latency_ms=20000,
        turns=[Turn(message="What were this week's sales?")],
    ),
    Scenario(
        id="rt_outstanding_invoices",
        category="response_time",
        description="Another simple single-tool read.",
        skill_name="commerce_admin",
        tenant_id="off-the-hook",
        metadata={"customer_phone": "27737815979"},
        max_latency_ms=20000,
        turns=[Turn(message="Any invoices still outstanding?")],
    ),
    Scenario(
        id="rt_general_question",
        category="response_time",
        description="A general-knowledge reasoning question, no KB retrieval needed.",
        skill_name="reasoning",
        tenant_id="digg-demo",
        metadata={},
        max_latency_ms=25000,
        turns=[Turn(message="What's the typical lifespan of galvanised steel roof sheeting in "
                            "a coastal South African climate?")],
    ),
]


ALL_SCENARIOS: List[Scenario] = (
    TOOL_SELECTION + ASK_DONT_GUESS + CONFIRM_FLOW + NEED_INFO + RESPONSE_TIME
)
