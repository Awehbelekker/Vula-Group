"""25 Nov 2026 (Ian: DIGG's books review hadn't moved for five days). A weekly note to the owners of
what's left in the books review, until the check is agreed (vula/books_review_nudge.py); and the
re-file list offering to keep a payee where all its lines already are, in one tap. Made-up business."""
import asyncio
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from vula import books_review_nudge as nb, features, team_index
from vula.commerce import books_review, finance_folders
from tests.fake_supabase import FakeSupabase

T = "t1"
SAST = nb.SAST
TEAM = [{"id": "m1", "name": "Sam Owner", "role": "owner", "whatsapp": "27820000001", "email": "sam@example.com"},
        {"id": "m2", "name": "Lee Owner", "role": "owner", "whatsapp": None, "email": "lee@example.com"},
        {"id": "m3", "name": "Max Manager", "role": "manager", "whatsapp": "27820000003", "email": None}]
AUDIT = {"id": "a1", "tenant_id": T, "agreed_by": None}


@pytest.fixture
def db(monkeypatch):
    d = FakeSupabase({"vula_statement_checks": [
        {"id": f"c{i}", "tenant_id": T, "status": "proposed"} for i in range(3)]
        + [{"id": "x", "tenant_id": "other", "status": "proposed"}],
        "vula_merchant_audit": [], "vula_tenant_config": [{"tenant_id": T, "features": {}}]})
    monkeypatch.setattr(nb, "_client", lambda: d)
    monkeypatch.setattr("vula.api.merchant_audit._client", lambda: d)
    monkeypatch.setattr(books_review, "latest", lambda tid: dict(AUDIT))
    monkeypatch.setattr(books_review, "review", lambda tid: {"open": {"refile": 554, "in_transit": 120, "drafts": 0,
                                                                       "odd_dates": 4}})
    monkeypatch.setattr(team_index, "active_members", lambda tid: [dict(m) for m in TEAM] if tid == T else [])
    monkeypatch.setattr(finance_folders, "_business", lambda tid: "Mandla Designs")
    return d


@pytest.fixture
def send(monkeypatch):
    from vula.api import whatsapp
    from vula.commerce import mail_router
    calls = {"wa": AsyncMock(return_value=True), "mail": AsyncMock(return_value=True)}
    monkeypatch.setattr(whatsapp, "_send_reply", calls["wa"])
    monkeypatch.setattr(mail_router, "send_tenant_email", calls["mail"])
    return calls


@pytest.mark.parametrize("when,week", [(datetime(2026, 11, 30, 7, 59, tzinfo=SAST), None),
                                       (datetime(2026, 11, 30, 8, 0, tzinfo=SAST), "2026-W49"),
                                       (datetime(2026, 12, 2, 15, 0, tzinfo=SAST), "2026-W49")])
def test_when_it_is_due(when, week):
    assert nb.due_week(when) == week


def test_what_is_left_largest_first_with_where_to_tap(db):
    its = nb.items(T)
    assert [(i["key"], i["count"]) for i in its] == [("statements", 3), ("refile", 554), ("in_transit", 120),
                                                     ("odd_dates", 4)]
    note = nb.text("Mandla Designs", its)
    assert note.startswith("*Mandla Designs — your books review this week*")
    assert "• Bank statements compared and waiting to be applied: 3 — Statements › Check again and apply all" in note
    assert "• Bank lines filed by an old rule: 554 — Bank lines › Filed by an old rule, one tap per payee" in note
    assert "Quickest win" in note and "press *Agree*" in note


def test_owners_only_once_a_week_whatsapp_else_email(db, send):
    out = asyncio.run(nb.nudge(T, "2026-W49"))
    assert [(s["name"], s["via"]) for s in out["sent"]] == [("Sam Owner", "WhatsApp"), ("Lee Owner", "email")]
    assert send["wa"].await_count == 1 and send["wa"].await_args.args[0] == "27820000001"   # never the manager
    assert send["wa"].await_args.kwargs["idem_key"] == "booksnudge:t1:2026-W49:27820000001"
    again = asyncio.run(nb.nudge(T, "2026-W49"))
    assert again["sent"] == [] and send["wa"].await_count == 1
    assert len(asyncio.run(nb.nudge(T, "2026-W50"))["sent"]) == 2                   # next week, again


def test_whatsapp_refused_goes_by_email(db, send):
    send["wa"].return_value = False
    out = asyncio.run(nb.nudge(T, "2026-W49"))
    assert [s["via"] for s in out["sent"]] == ["email", "email"]


def test_nothing_once_the_check_is_agreed(db, send, monkeypatch):
    monkeypatch.setattr(books_review, "latest", lambda tid: {**AUDIT, "agreed_by": "owner@example.com"})
    out = asyncio.run(nb.nudge(T, "2026-W49"))
    assert out["sent"] == [] and not send["wa"].await_count


def test_the_switch_decides(db, send, monkeypatch):
    from vula.api import tenants
    monkeypatch.setattr(tenants, "is_active", lambda tid: True)
    when = datetime(2026, 11, 30, 9, 0, tzinfo=SAST)
    monkeypatch.setattr(features, "on", lambda tid, name: False)
    assert asyncio.run(nb.tick(when)) == 0
    monkeypatch.setattr(features, "on", lambda tid, name: name == nb.SWITCH and tid == T)
    assert asyncio.run(nb.tick(when)) == 2


# ── the re-file list: keep a payee where all its lines already are, in one tap ──

def _line(i, desc, code, cents=10000):
    return {"id": i, "tenant_id": T, "description": desc, "direction": "out", "amount_cents": cents,
            "account_code": code, "categorized_by": "learned", "txn_date": "2026-07-01", "match_status": "unmatched"}


def test_a_payee_on_one_account_is_offered_to_keep_it_there():
    lines = [_line("a1", "FNB App Payment To Sipho Ndlovu Wages", "casual_labour"),
             _line("a2", "FNB App Payment To Sipho Ndlovu Wages", "casual_labour"),
             _line("b1", "FNB App Payment To Cape Timber", "casual_labour"),
             _line("b2", "FNB App Payment To Cape Timber", "cost_of_sales"),
             _line("c1", "FNB bank charges (3)", "casual_labour", 3500)]
    groups = {g["label"]: g for g in books_review.refile_groups(lines, set(), set())}
    sipho = groups["FNB App Payment To Sipho Ndlovu Wages"]
    assert sipho["keep"] is True and sipho["current"] == "casual_labour"
    assert sipho["suggested"] is None                                              # not Vula's pick: the person says
    timber = groups["FNB App Payment To Cape Timber"]
    assert timber["keep"] is False and timber["current"] is None                   # split: a person picks
    charges = next(g for g in groups.values() if g["why"] == "bank_charge")
    assert charges["suggested"] == "bank_charges" and charges["keep"] is False
