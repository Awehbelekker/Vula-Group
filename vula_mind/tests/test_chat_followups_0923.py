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


# ── 4. supplier-history answers are written from the numbers, not by the model ──
# Live retest after #68: find_filed_document returned all 16 invoices and R21,256.00, and the
# local 8B replied "The total amount spent is R942.00" with invented quantities.

from vula.commerce.service import format_supplier_history_reply  # noqa: E402

_RESULT = {
    "status": "found", "match_type": "resolved_via_knowledge_base",
    "resolved_supplier": "GARDENS HANDIMAN CENTRE", "total_matches": 3,
    "total_amount": "R1,084.00", "total_amount_cents": 108400, "matches_with_amount": 3,
    "materials": [{"description": "SAND PER BAG ACC", "quantity": 40, "spend": "R1,240.00",
                   "spend_cents": 124000, "documents": 2, "unit_price_varies": True},
                  {"description": "CEMENT 50KG", "quantity": -3, "spend": "-R477.00",
                   "spend_cents": -47700, "documents": 1}],
    "materials_distinct": 2,
    "matches": [
        {"filename": "POS Account Sale 24-225537.pdf", "amount": 942.0, "filed_at": "2026-09-22T11:40:03"},
        {"filename": "POS Account Refund 21-366230.pdf", "amount": 954.0, "is_refund": True,
         "filed_at": "2026-09-22T10:36:10"},
        {"filename": "POS Account Sale 23-244976.pdf", "amount": 1252.0, "filed_at": "2026-09-12T09:10:52"},
    ],
}


def test_reply_states_the_server_total_and_every_invoice():
    out = format_supplier_history_reply(_RESULT, query="jack hammer")
    assert "*GARDENS HANDIMAN CENTRE*: 3 documents, total spend *R1,084.00*" in out
    assert "after 1 refund of R954.00" in out
    assert "I took \"jack hammer\" to mean GARDENS HANDIMAN CENTRE" in out
    assert "• 2026-09-22 — POS Account Refund 21-366230 — R954.00 (refund)" in out
    assert "• SAND PER BAG ACC × 40 — R1,240.00 — ⚠️ quantity to check" in out
    assert "R942.00" in out and "total spend *R942" not in out


def test_reply_is_none_when_there_is_nothing_complete_to_state():
    assert format_supplier_history_reply({"status": "not_found_filed"}) is None
    assert format_supplier_history_reply({"status": "found", "match_type": "knowledge_base",
                                          "matches": [{"excerpt": "x"}]}) is None


@pytest.mark.asyncio
async def test_email_admin_answers_supplier_history_without_the_model_reading_numbers():
    from core.skills.base import SkillInput
    from core.skills.email_admin import EmailAdminSkill
    calls = {"n": 0}

    async def _fake(**kw):
        calls["n"] += 1
        tc = SimpleNamespace(id="c1", function=SimpleNamespace(
            name="find_document", arguments='{"query": "jack hammer", "category": "Invoice"}'))
        r = MagicMock()
        r.choices = [SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[tc]))]
        return r

    with (
        patch("core.skills.email_admin.get_email_creds", return_value={"email": "a@b.c"}),
        patch("core.skills.email_admin.resolve_generation_route",
              new=AsyncMock(return_value=("ollama_chat/llama3.1:8b", None, "http://x"))),
        patch("vula.commerce.service.find_filed_document", new=AsyncMock(return_value=_RESULT)),
        patch("litellm.acompletion", new=_fake),
    ):
        out = await EmailAdminSkill().run(SkillInput(
            question="Need all jack hammer invoices and a summary of what was spent", tenant_id=TID))
    assert calls["n"] == 1  # the model chose the tool; it never wrote the answer
    assert "total spend *R1,084.00*" in out.answer


@pytest.mark.asyncio
async def test_non_supplier_questions_still_go_back_to_the_model():
    from core.skills.email_admin import _direct_supplier_answer
    assert _direct_supplier_answer("find the proof of payment I sent", "find_document", {}, _RESULT) is None
    assert _direct_supplier_answer("Need all jack hammer invoices", "email_search", {}, _RESULT) is None
