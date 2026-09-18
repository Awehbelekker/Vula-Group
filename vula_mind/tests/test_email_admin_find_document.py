"""Tests for email_admin's find_document tool, added 2026-09-17 after a real transcript
(digg-demo, owner admin session) showed a request to "group all Jack hammer invoices" get
answered "I was unable to find any emails that match the query 'Jack hammer'" — wrong, because
email_search only does a literal text match against the raw mailbox, while the 10 matching
invoices were already extracted and filed under vula_filed_documents (the search term was an
account/party name, not text that appears verbatim in the inbox). Professional/knowledge tenants
like digg-demo route admin questions through email_admin rather than commerce_admin (which
already had this same tool — see test_commerce_admin_find_document.py), so email_admin needed
its own copy to answer "invoices we have on file" questions correctly.
"""
from unittest.mock import MagicMock, patch

import pytest

from core.skills.email_admin import TOOL_SPECS, EmailAdminSkill

TID = "digg-demo"


@pytest.fixture
def skill():
    return EmailAdminSkill()


def _mock_filed_documents(rows):
    """Chainable mock matching vula_filed_documents' select().eq().order()[.eq()][.or_()]
    .limit().execute() shape — category/or_ filters are optional so both must return self."""
    m = MagicMock()
    chain = m.table.return_value.select.return_value.eq.return_value.order.return_value
    chain.eq.return_value = chain
    chain.or_.return_value = chain
    chain.limit.return_value.execute.return_value = MagicMock(data=rows)
    return m


def test_find_document_is_a_registered_tool_spec():
    names = [t["function"]["name"] for t in TOOL_SPECS]
    assert "find_document" in names


def test_find_document_requires_query_arg():
    spec = next(t for t in TOOL_SPECS if t["function"]["name"] == "find_document")
    assert "query" in spec["function"]["parameters"]["required"]


@pytest.mark.asyncio
async def test_find_document_requires_query(skill):
    res = await skill._find_document(TID, {})
    assert "error" in res


@pytest.mark.asyncio
async def test_find_document_returns_matches(skill):
    rows = [{
        "id": "d1", "filename": "POS Account Sale 23-245492.pdf", "category": "Invoice",
        "summary": "Tax invoice from Gardens Handiman Centre for a diamond blade.",
        "fields": {"supplier": "Gardens Handiman Centre", "amount": "R4,565.50"},
        "status": "filed", "created_at": "2026-09-17T08:49:02Z", "customer_phone": None,
    }]
    with patch("vula.commerce.service._client", return_value=_mock_filed_documents(rows)):
        res = await skill._find_document(TID, {"query": "Jack hammer"})

    assert "matches" in res
    assert len(res["matches"]) == 1
    match = res["matches"][0]
    assert match["filename"] == "POS Account Sale 23-245492.pdf"
    assert match["party"] == "Gardens Handiman Centre"


@pytest.mark.asyncio
async def test_find_document_no_matches_gives_actionable_message_not_a_guess(skill):
    with patch("vula.commerce.service._client", return_value=_mock_filed_documents([])):
        res = await skill._find_document(TID, {"query": "nonexistent thing"})

    assert "matches" not in res
    assert "message" in res


@pytest.mark.asyncio
async def test_find_document_sanitizes_filter_breaking_characters(skill):
    mock_client = _mock_filed_documents([])
    with patch("vula.commerce.service._client", return_value=mock_client):
        await skill._find_document(TID, {"query": "Jack hammer, (urgent)"})

    chain = mock_client.table.return_value.select.return_value.eq.return_value.order.return_value
    called_with = chain.or_.call_args[0][0]
    assert "," not in called_with.split("ilike.%")[1].split("%")[0]
    assert "(" not in called_with and ")" not in called_with


@pytest.mark.asyncio
async def test_find_document_query_failure_returns_error_not_raise(skill):
    with patch("vula.commerce.service._client", side_effect=RuntimeError("db down")):
        res = await skill._find_document(TID, {"query": "anything"})

    assert "error" in res


def test_system_prompt_tells_model_to_try_find_document_before_email_search():
    skill = EmailAdminSkill()
    prompt = skill._system("draft")
    assert "call find_document, not email_thread_summary or email_search" in prompt
