"""finance_admin must say "nothing on file" once, not narrate its own tool calls.

Reproduced on off-the-hook, 2026-09-02, across three real questions. Its project-finance ledger
is genuinely empty (OTH's money lives in the commerce tables), so every tool returned
{"period": "all", "money_in": 0, "money_out": 0, "net": 0, "transactions": 0} — a SUCCESSFUL
call over an empty ledger. The not-found guard only recognised `error` or `found: False`, so it
never fired and the model was left to improvise:

    "Sawubona! I couldn't find the amount spent at suppliers this month. The tool calls returned
     the following results: * Tool call 1: {"period": "all", "money_in": 0, "money_out": 0,
     "net": 0, "transactions": 0} * Tool call 2: {"peri..."

Three defects in one reply: raw tool JSON pasted to the owner, internal tool machinery narrated,
and a claim of not finding something immediately followed by stating it.

This also explains the 22-of-22 verification failures on that tenant: finance_admin was being
asked money questions against a ledger with nothing in it, every time.
"""
import pytest

from core.skills.finance_admin import FinanceAdminSkill, _is_empty_ledger_result


# ── recognising an empty ledger ─────────────────────────────────────────────────

@pytest.mark.parametrize("result", [
    {"period": "all", "money_in": 0, "money_out": 0, "net": 0, "transactions": 0},
    {"money_in": 0.0, "money_out": 0.0, "transactions": 0},
    {"net": 0, "total": 0, "transactions": 0},
])
def test_an_all_zero_result_with_no_transactions_is_nothing_on_file(result):
    assert _is_empty_ledger_result(result) is True


def test_a_real_zero_with_transactions_behind_it_is_a_genuine_answer():
    """"You're owed R0" because everything is paid must NOT read as missing data."""
    assert _is_empty_ledger_result(
        {"money_in": 0, "money_out": 0, "net": 0, "transactions": 7}) is False


def test_a_result_with_real_figures_is_not_empty():
    assert _is_empty_ledger_result(
        {"money_in": 18000, "money_out": 0, "net": 18000, "transactions": 4}) is False


@pytest.mark.parametrize("result", [
    {"projects": [{"project": "HPC", "spend": 0}]},   # no transactions key at all
    {"error": "nope"},
    {},
    "a string",
    None,
])
def test_anything_else_is_left_alone(result):
    assert _is_empty_ledger_result(result) is False


# ── the effect on a real turn ───────────────────────────────────────────────────

def test_an_empty_ledger_result_marks_the_turn_not_found():
    skill = FinanceAdminSkill()
    skill._verified, skill._sources = [], []
    skill._any_tool_dispatched, skill._all_not_found = False, True
    skill._record_dispatch("money_in_out",
                              {"period": "all", "money_in": 0, "money_out": 0,
                               "net": 0, "transactions": 0})
    assert skill._all_not_found is True, "an empty ledger must count as nothing found"
    assert skill._any_tool_dispatched is True


def test_a_real_figure_clears_the_not_found_flag():
    skill = FinanceAdminSkill()
    skill._verified, skill._sources = [], []
    skill._any_tool_dispatched, skill._all_not_found = False, True
    skill._record_dispatch("money_in_out",
                              {"money_in": 18000, "money_out": 0, "net": 18000,
                               "transactions": 4})
    assert skill._all_not_found is False


# ── the prompt must forbid what the real replies did ────────────────────────────

def test_the_prompt_forbids_narrating_tool_calls():
    p = FinanceAdminSkill()._system()
    assert "NEVER mention your tool calls" in p
    assert "quote raw tool output" in p


def test_the_prompt_forbids_the_self_contradiction():
    p = FinanceAdminSkill()._system()
    assert "never say you couldn't find something and then state it anyway" in p.lower()
