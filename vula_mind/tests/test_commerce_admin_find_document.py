"""Tests for commerce_admin's find_document tool, added 2026-08-21 after a real transcript
(digg-demo, owner admin session, 2026-08-20) showed the admin tool-calling loop guessing among
unrelated tools (bookings, log_meeting, finance_insights) three times in a row rather than
looking up the specific invoice/proof-of-payment the owner referenced — despite AGENTIC_RULES
already saying to ask instead of guess. Root cause: there was nothing correct to reach for. See
core/skills/commerce_admin.py's _find_document and the system-prompt guidance in _system_prompt.

2026-09-18: the actual search (SQL filename/summary match + semantic-KB fallback) moved into
vula.commerce.service.find_filed_document, shared with email_admin.py's identical tool — see
test_service_find_filed_document.py for those behavior tests. This file now only checks that
the handler here delegates to it correctly.
"""
from unittest.mock import AsyncMock, patch

import pytest

from core.skills.commerce_admin import (
    TOOL_SPECS, _REP_TOOL_SPECS, _tools_for, CommerceAdminSkill, _is_pure_create_invoice_request,
)

TID = "test-tenant"


@pytest.fixture
def skill():
    return CommerceAdminSkill()


# ── tool registration ────────────────────────────────────────────────────────────

def test_find_document_is_a_registered_tool_spec():
    names = [t["function"]["name"] for t in TOOL_SPECS]
    assert "find_document" in names


def test_find_document_requires_query_arg():
    spec = next(t for t in TOOL_SPECS if t["function"]["name"] == "find_document")
    assert "query" in spec["function"]["parameters"]["required"]


# ── deterministic create-request filter (2026-08-24) ─────────────────────────────
# The benchmark's own re-runs showed the prompt-only "don't use find_document for a create
# request" guidance wasn't reliably followed: the model kept calling find_document first for
# "make a customer invoice for Regan...", then — after a no-match — wandered to add_expense or
# create_manual_order instead of create_invoice, the tool that actually matched. This excludes
# find_document from the offered tools outright for a pure creation request, deterministically.

@pytest.mark.parametrize("message", [
    "I would like to make a customer invoice for Regan for Angel fish at R100 per kg, 2kg "
    "please include a delivery fee of R10.",
    "Make a customer invoice for Priya: 3kg hake fillets at R120 per kg, plus a R15 delivery fee.",
    "Create a quote for Acme Builders for 3 hours of consulting",
    "Can you draft an invoice for the new client",
])
def test_pure_create_requests_are_detected(message):
    assert _is_pure_create_invoice_request(message) is True


@pytest.mark.parametrize("message", [
    "You need to check that number it's incorrect, please re-look at the proof of payment",
    "The invoice you just got, on the bank statement, might be the wrong invoice number",
    "What invoices are outstanding?",
    "Can you check the invoice I sent you yesterday",
    "How's stock looking?",
])
def test_non_create_messages_are_not_flagged(message):
    assert _is_pure_create_invoice_request(message) is False


def test_find_document_excluded_from_tools_for_a_create_request():
    tools = _tools_for(TID, role=None,
                       message="Make a customer invoice for Regan for Angel fish at R100/kg")
    names = [t["function"]["name"] for t in tools]
    assert "find_document" not in names


def test_find_document_still_offered_for_a_document_reference():
    tools = _tools_for(TID, role=None, message="please re-look at the proof of payment")
    names = [t["function"]["name"] for t in tools]
    assert "find_document" in names


def test_find_document_is_always_on_for_owner_role():
    tools = _tools_for(TID, role=None)
    assert any(t["function"]["name"] == "find_document" for t in tools)


def test_find_document_not_offered_to_sales_rep():
    # Filed documents (invoices/proof-of-payment/BOQ) are shop-wide financial records — a rep
    # gets personal-scope tools only (contacts, meetings, bookings), same boundary as invoices.
    names = [t["function"]["name"] for t in _REP_TOOL_SPECS]
    assert "find_document" not in names


# ── system-prompt guidance ───────────────────────────────────────────────────────

def test_owner_prompt_tells_model_to_use_find_document_before_guessing(skill):
    prompt = skill._system_prompt(TID, role=None, name="Test")
    assert "find_document" in prompt
    assert "BEFORE acting or answering" in prompt


def test_owner_prompt_excludes_create_requests_and_forbids_wrong_tool_fallback(skill):
    # 2026-08-23 benchmark finding: "make a customer invoice for Regan for Angel fish..." made
    # the model call find_document (nothing to look up — this is a create request), and when
    # that came back empty, it fell back to add_expense — logging a real R210 "stock purchase
    # from supplier Regan" for what should have been a sales invoice to a customer. Both
    # guardrails below are the fix.
    prompt = skill._system_prompt(TID, role=None, name="Test")
    assert "does NOT apply to a request to CREATE something" in prompt
    assert "logging an expense" in prompt


def test_find_document_tool_spec_excludes_create_requests():
    spec = next(t for t in TOOL_SPECS if t["function"]["name"] == "find_document")
    desc = spec["function"]["description"]
    assert "Do NOT use this for a request to CREATE something new" in desc
    assert "do not fall back to a different, unrelated tool" in desc


# ── live-mailbox fallback (2026-09-22) ───────────────────────────────────────────────

def test_find_document_spec_tells_model_to_try_email_thread_summary_on_a_miss():
    spec = next(t for t in TOOL_SPECS if t["function"]["name"] == "find_document")
    assert "not_found_filed" in spec["function"]["description"]
    assert "email_thread_summary" in spec["function"]["description"]


def test_owner_prompt_tells_model_to_try_email_thread_summary_before_giving_up(skill):
    prompt = skill._system_prompt(TID, role=None, name="Test")
    assert "not_found_filed" in prompt
    assert "email_thread_summary next" in prompt


def test_owner_prompt_tells_model_not_to_re_ask_a_narrowing_question(skill):
    prompt = skill._system_prompt(TID, role=None, name="Test")
    assert "never ask the same question twice" in prompt.lower()


# ── _find_document handler ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_find_document_requires_query(skill):
    res = await skill._find_document(TID, {})
    assert "error" in res


@pytest.mark.asyncio
async def test_find_document_delegates_to_shared_service_function(skill):
    """The actual search logic (SQL match + semantic-KB fallback) lives in
    service.find_filed_document, shared with email_admin.py — see
    test_service_find_filed_document.py for its behavior tests."""
    import core.skills.commerce_admin as ca

    expected = {"matches": [{"filename": "solid-cape-invoice.pdf"}], "match_type": "filed_document"}
    with patch.object(ca, "service") as mock_service:
        mock_service.find_filed_document = AsyncMock(return_value=expected)
        res = await skill._find_document(TID, {"query": "Solid Cape invoice", "category": "Invoice"})

    mock_service.find_filed_document.assert_awaited_once_with(
        TID, "Solid Cape invoice", category="Invoice")
    assert res is expected


@pytest.mark.asyncio
async def test_find_document_passes_through_no_match_message(skill):
    import core.skills.commerce_admin as ca

    with patch.object(ca, "service") as mock_service:
        mock_service.find_filed_document = AsyncMock(
            return_value={"message": "No filed document matches 'nonexistent thing'."})
        res = await skill._find_document(TID, {"query": "nonexistent thing"})

    assert "matches" not in res
    assert "message" in res


# ── find_document vs lookup_business_info: supplier-spend-history disambiguation ──
# Real incident, digg-demo, 2026-09-22: "Need all jack hammer invoice and summary of what was
# spent" — telemetry (vula_reasoning_telemetry) confirmed the agent called ONLY
# lookup_business_info, never find_document or email_thread_summary, and stated a price that
# unverified_prices() correctly flagged as unfounded. Root cause: find_document's own tool
# description and the matching system-prompt rule both framed it as being for a single ALREADY
# REFERENCED document ("that invoice", "the proof of payment I sent you") — a broad "all
# invoices from X" / "what have we spent with X" request doesn't read as that, and
# lookup_business_info's description explicitly invites "a supplier or distributor name is
# mentioned" queries, so the model reached for the wrong tool. Both tool descriptions and the
# system prompt now say explicitly that a named-supplier spend/invoice question is a
# find_document (then email_thread_summary) query, never lookup_business_info.

def test_find_document_description_covers_broad_supplier_spend_requests():
    spec = next(t for t in TOOL_SPECS if t["function"]["name"] == "find_document")
    desc = spec["function"]["description"]
    assert "all invoices from" in desc.lower()
    assert "spent" in desc.lower()
    assert "never lookup_business_info" in desc.lower()


def test_lookup_business_info_description_excludes_actual_spend_history():
    import core.skills.commerce_admin as ca

    spec = next(t for t in ca.KNOWLEDGE_TOOLS if t["function"]["name"] == "lookup_business_info")
    desc = spec["function"]["description"]
    assert "transaction history" in desc.lower()
    assert "find_document" in desc


def test_system_prompt_routes_supplier_spend_requests_to_find_document(skill):
    prompt = skill._system_prompt(TID, role=None, name="Test").lower()
    assert "what have we spent/paid with x" in prompt or "all invoices from x" in prompt
    assert "never lookup_business_info" in prompt


# ── deterministic backstop for #62 (2026-09-23) ──────────────────────────────────
# #62's wording fix is prompt-only; this excludes lookup_business_info from the offered tools
# outright for a spend/invoice-history question, the same way find_document is excluded for a
# pure create request above.

@pytest.mark.parametrize("message", [
    "Need all jack hammer invoice and summary of what was spent",
    "What have we spent with Makro this year?",
    "How much did we pay Builders Warehouse last month",
    "Send me all the invoices from Gerflor",
    "What's our total spend on cement?",
    "Give me a summary of our payments to Coastal Hire",
    "What materials did we buy from Jack Hammer?",
    "Summary of materials from Gardens Handiman this month",
    "Which items have we ordered from Builders Warehouse",
])
def test_spend_history_requests_are_detected(message):
    from core.skills.commerce_admin import _is_spend_history_request
    assert _is_spend_history_request(message) is True


@pytest.mark.parametrize("message", [
    "What does Makro charge for hake fillets?",
    "What have we spent with Makro and what do they charge now?",
    "Make a customer invoice for Regan for Angel fish at R100/kg",
    "How's stock looking?",
    "What's the price of the 3m scaffolding?",
    "Has Regan paid?",
    "What materials do you sell?",
    "Do we have materials in stock for the Smith job?",
])
def test_non_spend_history_messages_are_not_flagged(message):
    from core.skills.commerce_admin import _is_spend_history_request
    assert _is_spend_history_request(message) is False


def test_lookup_business_info_excluded_for_a_spend_history_request():
    names = [t["function"]["name"] for t in _tools_for(
        TID, role=None, message="Need all jack hammer invoice and summary of what was spent")]
    assert "lookup_business_info" not in names
    assert "find_document" in names
    assert "email_thread_summary" in names


def test_lookup_business_info_still_offered_for_a_pricing_question():
    names = [t["function"]["name"] for t in _tools_for(
        TID, role=None, message="What does Makro charge for hake fillets?")]
    assert "lookup_business_info" in names


def test_find_document_description_covers_materials_summaries():
    spec = next(t for t in TOOL_SPECS if t["function"]["name"] == "find_document")
    desc = spec["function"]["description"].lower()
    assert "what materials/items did we buy" in desc
    assert "materials" in desc
