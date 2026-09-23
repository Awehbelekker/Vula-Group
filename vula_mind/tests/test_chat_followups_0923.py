"""Fixes from reviewing the real digg-demo WhatsApp chats, 2026-09-23.

1. "Try again" was routed as a brand-new question (to `reasoning`) and answered from an
   unrelated health-and-safety document. It now re-runs the previous request.
2. "Can you make a note the jack hammer is a alias to the gardens account" went to ClickUp
   ("make a note") and failed. It now saves a supplier alias in commerce_suppliers.aliases
   (read back), which find_filed_document already resolves nicknames from.
3. "See if you can find invoices gardening gardens area" / "For gardens handiman full list of
   spend and material" fell through to `reasoning`; they're now supplier-history questions.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.skills.base import looks_like_supplier_history_question
from vula.api import whatsapp as wa
from vula.commerce.service import learn_supplier_alias, parse_alias_statement

TID = "digg-demo"


# ── 1. retry ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("Try again", True), ("try again please", True), ("Please try again.", True),
    ("again", True), ("Retry", True), ("OK try again", True), ("one more time", True),
    ("Yes please", False), ("Try again with Gardens", False), ("Okay try Jack Hammer", False),
])
def test_retry_detection(text, expected):
    assert bool(wa._RETRY_RE.match(text)) is expected


def _db(messages):
    db = MagicMock()
    db.get.return_value = [SimpleNamespace(role=r, text=t) for r, t in messages]
    return db


def test_retry_reroutes_to_the_previous_real_request():
    db = _db([("user", "Need all jack hammer invoices and a summary of what was spent"),
              ("assistant", "Sorry, I couldn't work that out"),
              ("user", "Try again"),
              ("assistant", "Sorry, I couldn't work that out")])
    assert wa._resolve_retry(db, TID, "t", "Try again") == (
        "Need all jack hammer invoices and a summary of what was spent")


def test_non_retry_text_is_unchanged_and_needs_no_lookup():
    db = _db([])
    assert wa._resolve_retry(db, TID, "t", "Yes please") == "Yes please"
    db.get.assert_not_called()


def test_retry_with_no_previous_request_stays_as_is():
    assert wa._resolve_retry(_db([]), TID, "t", "Try again") == "Try again"


# ── 2. supplier aliases ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("Send me a full list on all documents... Can you make a note the jack hammer is a alias "
     "to the gardens account", ("jack hammer", "gardens")),
    ("Jack Hammer is an alias for Gardens Handiman Centre", ("Jack Hammer", "Gardens Handiman Centre")),
    ("Remember that JH is the same as Gardens Handiman", ("JH", "Gardens Handiman")),
    ("Coastal is short for Coastal Hire.", ("Coastal", "Coastal Hire")),
    ("Please note: Gerflor is a supplier", None),
    ("The invoice is for Gardens", None),
    ("What is Jack Hammer?", None),
])
def test_parse_alias_statement(text, expected):
    assert parse_alias_statement(text) == expected


_GARDENS = [{"id": "s1", "name": "GARDENS HANDIMAN CENTRE", "aliases": []},
            {"id": "s2", "name": "Gardens Handiman Centre", "aliases": []},
            {"id": "s3", "name": "Solid Cape", "aliases": []}]


def _client_readback(rows):
    m = MagicMock()
    t = m.table.return_value
    t.select.return_value.eq.return_value.in_.return_value.execute.return_value = MagicMock(data=rows)
    return m


@pytest.mark.asyncio
async def test_alias_is_added_to_every_row_of_the_same_supplier_and_read_back():
    client = _client_readback([{"id": "s1", "aliases": ["jack hammer"]},
                               {"id": "s2", "aliases": ["jack hammer"]}])
    with patch("vula.commerce.service.list_suppliers", AsyncMock(return_value=_GARDENS)), \
         patch("vula.commerce.service._client", return_value=client):
        res = await learn_supplier_alias(TID, "jack hammer", "gardens")
    assert res == {"status": "added", "supplier": "GARDENS HANDIMAN CENTRE", "alias": "jack hammer"}
    updates = client.table.return_value.update.call_args_list
    assert [u[0][0] for u in updates] == [{"aliases": ["jack hammer"]}] * 2


@pytest.mark.asyncio
async def test_failed_read_back_is_reported_as_an_error():
    client = _client_readback([{"id": "s1", "aliases": []}, {"id": "s2", "aliases": []}])
    with patch("vula.commerce.service.list_suppliers", AsyncMock(return_value=_GARDENS)), \
         patch("vula.commerce.service._client", return_value=client):
        res = await learn_supplier_alias(TID, "jack hammer", "gardens")
    assert res["status"] == "error"


@pytest.mark.asyncio
async def test_existing_alias_is_not_written_again():
    rows = [dict(r, aliases=["Jack Hammer"]) for r in _GARDENS[:2]]
    client = MagicMock()
    with patch("vula.commerce.service.list_suppliers", AsyncMock(return_value=rows)), \
         patch("vula.commerce.service._client", return_value=client):
        res = await learn_supplier_alias(TID, "jack hammer", "gardens")
    assert res["status"] == "exists"
    client.table.assert_not_called()


@pytest.mark.asyncio
async def test_ambiguous_target_asks_instead_of_guessing():
    rows = [{"id": "a", "name": "Coastal Hire", "aliases": []},
            {"id": "b", "name": "Coastal Hardware", "aliases": []}]
    with patch("vula.commerce.service.list_suppliers", AsyncMock(return_value=rows)):
        res = await learn_supplier_alias(TID, "CH", "coastal")
    assert res["status"] == "ambiguous"
    assert set(res["candidates"]) == {"Coastal Hire", "Coastal Hardware"}


@pytest.mark.asyncio
async def test_unknown_target_is_not_found():
    with patch("vula.commerce.service.list_suppliers", AsyncMock(return_value=_GARDENS)):
        res = await learn_supplier_alias(TID, "JH", "Makro")
    assert res["status"] == "not_found"


@pytest.mark.asyncio
async def test_rag_reply_handles_alias_statements_for_insiders_only():
    added = {"status": "added", "supplier": "GARDENS HANDIMAN CENTRE", "alias": "jack hammer"}
    msg = "make a note the jack hammer is a alias to the gardens account"
    with patch("vula.commerce.service.learn_supplier_alias", AsyncMock(return_value=added)) as learn, \
         patch("core.agent_runner.get_agent_runner") as runner:
        out = await wa._rag_reply(TID, msg, caller_role="admin")
        assert "saved as another name for GARDENS HANDIMAN CENTRE" in out
        runner.assert_not_called()
        learn.reset_mock()
        runner.return_value.run = AsyncMock(side_effect=RuntimeError("stop here"))
        try:
            await wa._rag_reply(TID, msg, caller_role=None)
        except Exception:
            pass
        learn.assert_not_called()


# ── 3. wider supplier-history phrasing ──────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("See if you can find invoices gardening gardens area", True),
    ("For gardens handiman full list of spend and material", True),
    ("No please do a document with all gardens handiman invoice with summary of material", True),
    ("Show me unpaid invoices", False),
    ("Create an invoice for Regan", False),
    ("Which invoices are overdue?", False),
])
def test_supplier_history_phrasing(text, expected):
    assert looks_like_supplier_history_question(text) is expected
