"""Fixed step sequences per document, one question at a time (step 2b of the chat rework).

A receipt goes: booked (confirm mode) → which project? → company card or own money? → what
was it for? → odometer (petrol). The next step is read off the claim itself; each answer asks
the next; a second question about the same receipt is never stacked on an open one. A POP asks
"which bill?" before "whose account?" — the payer question waits on the queue until the bill
answer is in.
"""
from unittest.mock import AsyncMock, patch

import pytest

from tests.test_open_questions import NOW, PHONE, TID, _asked, db  # noqa: F401  (fixture)
from vula import doc_steps, open_questions as oq
from vula.api import whatsapp as wa

BASE = {"id": "fuel", "tenant_id": TID, "amount_cents": 87150, "status": "submitted",
        "project": None, "paid_with": None, "purpose_category": None, "odometer_km": None,
        "needs_project": True}


@pytest.fixture
def cards(monkeypatch):
    monkeypatch.setattr("vula.commerce.expenses.list_cards", lambda t: [{"last4": "5723"}])
    monkeypatch.setattr("vula.commerce.expenses.known_projects", lambda t: ["Atlantis Paarden Eiland"])


@pytest.mark.parametrize("over,step", [
    ({"status": "unconfirmed"}, "expense_confirm"),
    ({}, "expense_project"),
    ({"project": "Atlantis Paarden Eiland"}, "expense_paid_with"),
    ({"project": "", "paid_with": "personal"}, "expense_purpose"),
    ({"project": "", "paid_with": "company_card", "purpose_category": "petrol"}, "expense_odometer"),
    ({"project": "", "paid_with": "cash", "purpose_category": "petrol", "odometer_km": 45280}, None),
    ({"project": "", "paid_with": "cash", "purpose_category": "clients"}, None),
])
def test_the_receipt_steps_come_in_order(cards, over, step):
    assert doc_steps.expense_step(TID, {**BASE, **over}) == step


def test_one_question_is_asked_and_recorded(db, cards):
    q = doc_steps.next_question(TID, PHONE, "fuel", claim=dict(BASE))
    assert q.startswith("📍 Which project")
    [rec] = oq.open_for(TID, PHONE)
    assert (rec["kind"], rec["ref_id"]) == ("expense_project", "fuel")
    # asking again while that one is open adds nothing
    assert doc_steps.next_question(TID, PHONE, "fuel", claim={**BASE, "project": "x"}) is None
    assert len(oq.open_for(TID, PHONE)) == 1


def test_nothing_is_asked_while_the_receipt_waits_for_its_confirm_tap(db, cards):
    assert doc_steps.next_question(TID, PHONE, "fuel", claim={**BASE, "status": "unconfirmed"}) is None
    assert oq.open_for(TID, PHONE) == []


@pytest.mark.asyncio
async def test_answering_the_project_asks_the_card_question_next(db, cards, monkeypatch):
    db["commerce_expenses"] = [dict(BASE)]
    _asked(db, "expense_project", "fuel", 1)

    def assign(t, cid, project):
        db["commerce_expenses"][0]["project"] = project
    monkeypatch.setattr("vula.commerce.expenses.match_project", lambda t, x: "Atlantis Paarden Eiland")
    monkeypatch.setattr("vula.commerce.expenses.assign", assign)
    with patch.object(wa, "_send_reply", AsyncMock()) as reply:
        assert await wa._answer_open_question(TID, PHONE, "Atlantis")
    text = reply.await_args.args[1]
    assert "Atlantis Paarden Eiland" in text and "*company card* or *your own money*" in text
    assert "What was this for" not in text                         # one at a time
    assert [q["kind"] for q in oq.open_for(TID, PHONE)] == ["expense_paid_with"]


@pytest.mark.asyncio
async def test_purpose_then_odometer_land_on_the_asked_receipt(db, cards, monkeypatch):
    db["commerce_expenses"] = [{**BASE, "project": "", "paid_with": "company_card"}]
    _asked(db, "expense_purpose", "fuel", 1)
    monkeypatch.setattr("vula.commerce.expenses.set_purpose_category",
                        lambda t, cid, cat, detail=None: db["commerce_expenses"][0].update(purpose_category=cat))
    monkeypatch.setattr("vula.commerce.expenses.set_odometer",
                        lambda t, cid, km: db["commerce_expenses"][0].update(odometer_km=km))
    with patch.object(wa, "_send_reply", AsyncMock()) as reply:
        assert await wa._answer_open_question(TID, PHONE, "petrol")
        assert "Petrol" in reply.await_args.args[1] and "odometer reading" in reply.await_args.args[1]
        assert await wa._answer_open_question(TID, PHONE, "45 280 km")
    assert db["commerce_expenses"][0]["odometer_km"] == 45280
    assert "45,280 km" in reply.await_args.args[1]
    assert oq.open_for(TID, PHONE) == []                            # receipt complete


@pytest.mark.asyncio
async def test_a_bare_number_is_not_a_purpose(db, cards):
    db["commerce_expenses"] = [{**BASE, "project": "", "paid_with": "cash"}]
    _asked(db, "expense_purpose", "fuel", 1)
    assert wa._answer_expense_question(TID, "expense_purpose", "fuel", "45280") is None


@pytest.mark.asyncio
async def test_a_pop_asks_which_bill_before_whose_account(db, monkeypatch):
    _asked(db, "pop_match", "txn-1", 0)                            # just asked "does this pay STE00866?"
    monkeypatch.setattr(wa, "_business_label", lambda t: "DIGG")
    monkeypatch.setattr("vula.commerce.payers.classify", lambda *a: None)
    with patch.object(wa, "_send_reply", AsyncMock()) as send:
        line = await wa._payer_line(TID, PHONE, {"payer": "MR RICHARD D DOWNING"})
    assert line == "" and send.await_count == 0                    # not in this message
    [queued] = [q for q in db["vula_open_questions"] if q["status"] == "queued"]
    assert queued["kind"] == "payer_account" and "DIGG* account" in queued["message"]


@pytest.mark.asyncio
async def test_with_no_bill_question_the_payer_question_comes_straight_away(db, monkeypatch):
    monkeypatch.setattr(wa, "_business_label", lambda t: "DIGG")
    monkeypatch.setattr("vula.commerce.payers.classify", lambda *a: None)
    line = await wa._payer_line(TID, PHONE, {"payer": "MR RICHARD D DOWNING"})
    assert "*DIGG* account or *your own* money" in line
    assert [q["kind"] for q in oq.open_for(TID, PHONE)] == ["payer_account"]


def test_every_sequence_is_declared():
    assert doc_steps.SEQUENCES["expense"][0] == "expense_confirm"
    assert doc_steps.SEQUENCES["pop"] == ("pop_match", "payer_account")
