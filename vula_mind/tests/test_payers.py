"""Whose money a proof of payment came from (migration 196, vula/commerce/payers.py).

2026-10-06 (digg-demo): the STE Scaffolding POP was paid from "MR RICHARD D DOWNING" — the
owner said it "went through the DIGG account, just paid by me". Payer names seen on DIGG's POPs:
"AWEH BE LEKKER (PTY) LTD", "*AWEH BE LEKKER (PTY) LTD", "MR RICHARD D DOWNING",
"Richard D Downing", "MRRICHARDDDOWNING", "MRS J DOWNING".
"""
from unittest.mock import AsyncMock, patch

import pytest

from vula.commerce import payers
from vula.ingestion.payment_notice import parse

TID, PHONE = "digg-demo", "27645755210"


@pytest.mark.parametrize("raw", ["MR RICHARD D DOWNING", "Richard D Downing", "MRRICHARDDDOWNING",
                                 "*MR RICHARD D DOWNING"])
def test_the_ways_a_bank_prints_one_name_are_one_key(raw):
    assert payers.name_key(raw) == "richardddowning"


def test_another_person_is_another_key():
    assert payers.name_key("MRS J DOWNING") != payers.name_key("MR RICHARD D DOWNING")


class _Q:
    def __init__(self, db, t):
        self.db, self.t, self.f, self.ins = db, t, [], None

    def select(self, *_a, **_k):
        return self

    def insert(self, row):
        self.ins = row
        return self

    def eq(self, c, v):
        self.f.append(lambda r: r.get(c) == v)
        return self

    def gte(self, *_a):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        if self.ins is not None:
            self.db.setdefault(self.t, []).append(dict(self.ins, id=f"r{len(self.db.get(self.t, []))}"))
            return type("R", (), {"data": [self.ins]})()
        return type("R", (), {"data": [r for r in self.db.get(self.t, []) if all(f(r) for f in self.f)]})()


@pytest.fixture()
def db(monkeypatch):
    data = {"commerce_payer_accounts": [], "vula_open_questions": []}
    client = type("C", (), {"table": lambda self, t: _Q(data, t)})()
    monkeypatch.setattr(payers, "_client", lambda: client)
    monkeypatch.setattr("vula.open_questions._client", lambda: client)
    monkeypatch.setattr("vula.integrations.doc_filing._own_names",
                        lambda t: {"AWEH BE LEKKER", "AWEH", "DIGG"})
    return data


def test_the_business_own_name_is_its_money_without_asking(db):
    assert payers.classify(TID, "*AWEH BE LEKKER (PTY) LTD")["owner"] == "business"


def test_an_unknown_payer_is_asked_once_and_remembered(db):
    note = payers.pop_note(TID, PHONE, {"payer": "MR RICHARD D DOWNING"}, "DIGG")
    assert "*MR RICHARD D DOWNING*" in note and "*DIGG* account or *your own* money" in note
    [q] = db["vula_open_questions"]
    assert q["kind"] == "payer_account"
    reply = payers.answer(TID, q["ref_id"], "DIGG account", "DIGG")
    assert "pays from the DIGG account" in reply
    # next POP from the same account holder, however the bank spells it
    assert payers.pop_note(TID, PHONE, {"payer": "Richard D Downing"}, "DIGG").startswith(
        "🏦 Paid from DIGG's account")


def test_own_money_is_remembered_as_personal(db):
    payers.pop_note(TID, PHONE, {"payer": "MRS J DOWNING"}, "DIGG")
    q = db["vula_open_questions"][0]
    assert "personal money" in payers.answer(TID, q["ref_id"], "own money", "DIGG")
    assert "owed back" in payers.pop_note(TID, PHONE, {"payer": "MRS J DOWNING"}, "DIGG")


def test_an_account_number_wins_over_the_name(db):
    payers.remember(TID, "MR RICHARD D DOWNING", "personal", account="..62845", person="Richard")
    assert payers.classify(TID, "MR RICHARD D DOWNING", "..62845")["owner"] == "personal"
    assert payers.classify(TID, "MR RICHARD D DOWNING", "..11111") is None


def test_a_reply_that_isnt_an_answer_is_left_alone(db):
    assert payers.answer(TID, "payer:x|X|", "send me the STE invoice", "DIGG") is None


def test_fnb_notice_with_a_payer_account_line():
    text = ("NOTIFICATION OF PAYMENT\nDate Actioned\n: 2026/10/05\nPayer Details\nPayment From\n"
            "*MR RICHARD D DOWNING\nAccount\n: ..62845\nCur/Amount\nZAR2397.17\nPayee Details\n"
            "Recipient/Account no\n: ..478922\nName\n: Ste\nReference\n: Digg\nEND OF NOTIFICATION\n")
    f = parse(text)["fields"]
    assert f["payer_account"] == "..62845" and f["payee_account_number"] == "..478922"


def test_fnb_notice_without_one_has_none():
    from tests.test_payment_notice import FNB
    assert parse(FNB)["fields"]["payer_account"] is None


@pytest.mark.asyncio
async def test_the_answer_reaches_the_payer_question(db, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from vula.api import whatsapp as wa
    now = datetime.now(timezone.utc)
    db["vula_open_questions"].append({"id": "q1", "tenant_id": TID, "phone": PHONE, "kind": "payer_account",
                                      "ref_id": "payer:richardddowning|MR RICHARD D DOWNING|",
                                      "status": "open", "asked_at": now.isoformat(),
                                      "expires_at": (now + timedelta(days=3)).isoformat()})
    monkeypatch.setattr(wa, "_business_label", lambda t: "DIGG")
    with patch.object(wa, "_send_reply", AsyncMock()) as reply:
        assert await wa._answer_open_question(TID, PHONE, "DIGG")
    assert "pays from the DIGG account" in reply.await_args.args[1]
    assert db["commerce_payer_accounts"][0]["owner"] == "business"
