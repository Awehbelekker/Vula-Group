"""Tests for the shared hard-decline guard and tool-source helper added to core/skills/base.py
during the 2026-08-24 chat-accuracy audit — centralizing what was previously a reasoning.py-only
mechanism so architecture_planning.py (and others) can reuse it without drifting.
"""
from core.skills.base import looks_like_tenant_data_question, tool_source


# ── looks_like_tenant_data_question ───────────────────────────────────────────────

def test_english_tenant_data_markers():
    assert looks_like_tenant_data_question("how much do we owe on the invoice for Stage 3")
    assert looks_like_tenant_data_question("what's our outstanding balance")
    assert looks_like_tenant_data_question("what's the total on that quote")
    assert looks_like_tenant_data_question("Logg as expense")


def test_afrikaans_tenant_data_marker_previously_a_real_gap():
    """Regression: the original English-only regex silently let an Afrikaans tenant-data
    question bypass the guard entirely — the exact hallucination class it exists to stop."""
    assert looks_like_tenant_data_question("Hoeveel skuld ons nog op die faktuur vir Stage 3?")


def test_construction_specific_markers_for_architecture_planning():
    assert looks_like_tenant_data_question("what's the retention on this contract")
    assert looks_like_tenant_data_question("what's the fee for the BOQ")
    assert looks_like_tenant_data_question("who is the subcontractor on this")


def test_generic_greeting_and_howto_do_not_match():
    assert not looks_like_tenant_data_question("hello how are you")
    assert not looks_like_tenant_data_question("how do I add a product")


def test_require_possessive_distinguishes_general_from_tenant_specific():
    """architecture_planning.py's narrower use: a general knowledge question about retention
    percentages should still be answerable from training_kb even with an empty tenant_kb —
    only a possessive ('our'/'this project's') tenant-specific question should decline."""
    general = "what is a typical retention percentage on a JBCC contract"
    specific = "what is the retention on our Riverside contract"
    assert looks_like_tenant_data_question(general, require_possessive=False) is True
    assert looks_like_tenant_data_question(general, require_possessive=True) is False
    assert looks_like_tenant_data_question(specific, require_possessive=True) is True


def test_empty_question_does_not_match():
    assert not looks_like_tenant_data_question("")
    assert not looks_like_tenant_data_question(None)


def test_breakdown_typo_split_across_two_words_still_matches():
    """Real digg-demo incident, 2026-09-25: "Please show all and do a full.break down in
    excel" — a stray typo period split "breakdown" into two separate words, missing the
    literal \\bbreakdown\\b marker entirely. HRM's mailbox-fallback routing (which gates on
    this function) never fired, and the message fell all the way through to `reasoning` (no
    find_document tool at all), which just paraphrased the prior WhatsApp reply from
    conversation history into a markdown table instead of answering properly."""
    assert looks_like_tenant_data_question("Please show all and do a full.break down in excel")
    assert looks_like_tenant_data_question("give me a break-down of expenses")
    assert looks_like_tenant_data_question("break  down please")  # double space, still tolerated


# ── tool_source ────────────────────────────────────────────────────────────────────

def test_tool_source_shape():
    src = tool_source("update_stock", {"product": "Hake", "stock_quantity": 20})
    assert src["type"] == "tool"
    assert src["name"] == "update_stock"
    assert "Hake" in src["text"]


def test_tool_source_truncates_to_900_chars():
    big_result = {"data": "x" * 5000}
    src = tool_source("some_tool", big_result)
    assert len(src["text"]) <= 900
