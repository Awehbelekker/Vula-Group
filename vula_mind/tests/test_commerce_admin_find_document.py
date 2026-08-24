"""Tests for commerce_admin's find_document tool, added 2026-08-21 after a real transcript
(digg-demo, owner admin session, 2026-08-20) showed the admin tool-calling loop guessing among
unrelated tools (bookings, log_meeting, finance_insights) three times in a row rather than
looking up the specific invoice/proof-of-payment the owner referenced — despite AGENTIC_RULES
already saying to ask instead of guess. Root cause: there was nothing correct to reach for. See
core/skills/commerce_admin.py's _find_document and the system-prompt guidance in _system_prompt.
"""
from unittest.mock import MagicMock, patch

import pytest

from core.skills.commerce_admin import (
    TOOL_SPECS, _REP_TOOL_SPECS, _tools_for, CommerceAdminSkill,
)

TID = "test-tenant"


@pytest.fixture
def skill():
    return CommerceAdminSkill()


def _mock_filed_documents(rows):
    """Chainable mock matching vula_filed_documents' select().eq().order()[.eq()][.or_()]
    .limit().execute() shape — category/or_ filters are optional so both must return self."""
    m = MagicMock()
    chain = m.table.return_value.select.return_value.eq.return_value.order.return_value
    chain.eq.return_value = chain
    chain.or_.return_value = chain
    chain.limit.return_value.execute.return_value = MagicMock(data=rows)
    return m


# ── tool registration ────────────────────────────────────────────────────────────

def test_find_document_is_a_registered_tool_spec():
    names = [t["function"]["name"] for t in TOOL_SPECS]
    assert "find_document" in names


def test_find_document_requires_query_arg():
    spec = next(t for t in TOOL_SPECS if t["function"]["name"] == "find_document")
    assert "query" in spec["function"]["parameters"]["required"]


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


# ── _find_document handler ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_find_document_requires_query(skill):
    res = await skill._find_document(TID, {})
    assert "error" in res


@pytest.mark.asyncio
async def test_find_document_returns_matches(skill):
    import core.skills.commerce_admin as ca

    rows = [{
        "id": "d1", "filename": "solid-cape-invoice.pdf", "category": "Invoice",
        "summary": "Invoice from Solid Cape for performer costs, R7,500.00",
        "fields": {"supplier": "Solid Cape", "amount": "R7,500.00"},
        "status": "filed", "created_at": "2026-08-19T10:00:00Z", "customer_phone": None,
    }]
    with patch.object(ca, "service") as mock_service:
        mock_service._client.return_value = _mock_filed_documents(rows)
        res = await skill._find_document(TID, {"query": "Solid Cape performer invoice"})

    assert "matches" in res
    assert len(res["matches"]) == 1
    match = res["matches"][0]
    assert match["filename"] == "solid-cape-invoice.pdf"
    assert match["party"] == "Solid Cape"
    assert match["amount"] == "R7,500.00"


@pytest.mark.asyncio
async def test_find_document_no_matches_gives_actionable_message_not_a_guess(skill):
    import core.skills.commerce_admin as ca

    with patch.object(ca, "service") as mock_service:
        mock_service._client.return_value = _mock_filed_documents([])
        res = await skill._find_document(TID, {"query": "nonexistent thing"})

    assert "matches" not in res
    assert "message" in res
    assert "invoice/document number" in res["message"] or "resend" in res["message"]


@pytest.mark.asyncio
async def test_find_document_sanitizes_filter_breaking_characters(skill):
    import core.skills.commerce_admin as ca

    mock_client = _mock_filed_documents([])
    with patch.object(ca, "service") as mock_service:
        mock_service._client.return_value = mock_client
        await skill._find_document(TID, {"query": "Solid Cape, (urgent)"})

    chain = mock_client.table.return_value.select.return_value.eq.return_value.order.return_value
    called_with = chain.or_.call_args[0][0]
    assert "," not in called_with.split("ilike.%")[1].split("%")[0]
    assert "(" not in called_with and ")" not in called_with


@pytest.mark.asyncio
async def test_find_document_query_failure_returns_error_not_raise(skill):
    import core.skills.commerce_admin as ca

    with patch.object(ca, "service") as mock_service:
        mock_service._client.side_effect = RuntimeError("db down")
        res = await skill._find_document(TID, {"query": "anything"})

    assert "error" in res
