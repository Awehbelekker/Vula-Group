"""A partner's fee, adjustable (vula/commerce/partner_fees.py, migration 314): the usual rate, a
school's own rate, a lesson keeping the rate it was asked at, and the monthly statement."""
from __future__ import annotations

from datetime import timedelta

import pytest

from tests.test_coach_pool import HOME, OUTSIDE, _ask, _next, run, world  # noqa: F401 — the fixture
from tests.test_lessons import TID
from vula.commerce import coach_pool as pool, lessons, partner_fees as fees


@pytest.fixture
def money(world):
    world.db.rows.setdefault("flow_partner_settings", [])
    world.db.rows.setdefault("flow_partner_fee_months", [])
    return world


def _taught(at, people=2):
    """A lesson at TID taught by Ayanda from HOME, marked done. Returns (request, amount)."""
    r = _ask(at, people, phone=f"082 000 {at.day:02d}{at.hour:02d}")
    pool.set_open(HOME, "k1", True)
    run(pool.ask(TID, r["id"], at.isoformat(), [f"{HOME}:k1"]))
    run(pool.answer(HOME, "k1", pool.mine(HOME, "k1")["asked"][0]["id"], True))
    done = run(lessons.confirm(TID, r["id"], starts_at=at.isoformat()))
    lessons.set_status(TID, r["id"], "completed")
    return r, done["amount_cents"]


def test_the_rate_is_the_one_in_code_until_the_operator_sets_it_and_a_school_can_have_its_own(money):
    assert fees.rate_for(TID) == 1000 and fees.rate_for(OUTSIDE) is None
    s = fees.settings("flowcrew")
    assert (s["pool_fee_bp"], s["set"], s["overrides"], s["pay_note"]) == (1000, False, [], "")
    s = fees.save_settings("flowcrew", {"pool_fee_bp": 1250, "pay_note": "  EFT to FlowCrew,   ref your school.  "}, by="master")
    assert (s["pool_fee_bp"], s["set"], s["pay_note"]) == (1250, True, "EFT to FlowCrew, ref your school.")
    assert fees.rate_for(TID) == 1250 and fees.rate_for(HOME) == 1250 and pool.fee_bp(TID) == 1250
    s = fees.save_settings("flowcrew", {"overrides": {TID: 500}})
    assert [(o["tenant_id"], o["fee_bp"]) for o in s["overrides"]] == [(TID, 500)] and s["pool_fee_bp"] == 1250   # only what was sent
    assert fees.rate_for(TID) == 500 and fees.rate_for(HOME) == 1250
    fees.save_settings("flowcrew", {"overrides": {TID: 0}})
    assert fees.rate_for(TID) == 0                                                # a school on no fee at all
    fees.save_settings("flowcrew", {"overrides": {TID: None}, "pool_fee_bp": None})
    assert fees.rate_for(TID) == 1000 and fees.settings("flowcrew")["set"] is False   # back to the one in code
    for bad, why in (({"pool_fee_bp": 3001}, "between 0% and 30%"), ({"pool_fee_bp": -1}, "between"), ({"pool_fee_bp": 12.5}, "between"),
                     ({"pool_fee_bp": True}, "between"), ({"overrides": {OUTSIDE: 500}}, "isn't one of this partner's schools"),
                     ({"overrides": [1]}, "by its id"), ({"pay_note": "x" * 401}, "under 400")):
        with pytest.raises(lessons.LessonError, match=why):
            fees.save_settings("flowcrew", bad)
    with pytest.raises(lessons.LessonError, match="isn't known"):
        fees.settings("nobody")
    assert len(money.db.rows["flow_partner_settings"]) == 1


def test_a_lesson_keeps_the_rate_it_was_asked_at(money):
    at = _next(1)
    fees.save_settings("flowcrew", {"pool_fee_bp": 2000})
    r = _ask(at)
    pool.set_open(HOME, "k1", True)
    found = pool.candidates(TID, r["id"], at.isoformat())
    assert found["fee_bp"] == 2000 and found["fee_cents"] == lessons.share_of(found["price_cents"], 2000)
    run(pool.ask(TID, r["id"], at.isoformat(), [f"{HOME}:k1"]))
    fees.save_settings("flowcrew", {"pool_fee_bp": 500})                              # changed after the coach was asked
    run(pool.answer(HOME, "k1", pool.mine(HOME, "k1")["asked"][0]["id"], True))
    assert money.db.rows["commerce_lesson_requests"][0]["pool_fee_bp"] == 2000
    done = run(lessons.confirm(TID, r["id"], starts_at=at.isoformat()))
    lessons.set_status(TID, r["id"], "completed")
    month = at.strftime("%Y-%m")
    assert lessons.coach_month(TID, month)["pool_fee_cents"] == lessons.share_of(done["amount_cents"], 2000)
    assert fees.month("flowcrew", month)["total"]["fee_cents"] == lessons.share_of(done["amount_cents"], 2000)


def test_a_finished_month_is_closed_once_and_marked_paid(money, monkeypatch):
    at = _next(2)
    _r, owed = _taught(at)
    month = at.strftime("%Y-%m")
    fee = lessons.share_of(owed, 1000)
    live = fees.month("flowcrew", month)
    assert [(s["tenant_id"], s["status"], s["fee_cents"], s["lessons"]) for s in live["schools"]] == [(TID, "open", fee, 1)]
    assert live["total"]["open_cents"] == fee and live["over"] is False
    with pytest.raises(lessons.LessonError, match="once it is over") as e:
        run(fees.close("flowcrew", month))
    assert e.value.status == 409 and money.db.rows["flow_partner_fee_months"] == []
    after = at + timedelta(days=40)                                                   # the month has ended
    monkeypatch.setattr(lessons, "_now", lambda: after)
    fees.save_settings("flowcrew", {"pay_note": "EFT to FlowCrew."})
    out = run(fees.close("flowcrew", month, by="master"))
    assert out["closed"] == 1 and out["schools"][0]["status"] == "due" and out["total"]["due_cents"] == fee
    assert money.told[-1][0] == TID and f"FlowCrew's fee for {month}: {lessons.rand(fee)} on 1 lesson" in money.told[-1][1]
    assert "EFT to FlowCrew." in money.told[-1][1]
    assert run(fees.close("flowcrew", month))["closed"] == 0 and len(money.db.rows["flow_partner_fee_months"]) == 1   # never twice
    money.db.rows["commerce_lesson_requests"][0]["price_cents"] = owed * 3             # the lesson is changed afterwards
    frozen = fees.month("flowcrew", month)["schools"][0]
    assert frozen["fee_cents"] == fee and frozen["live_fee_cents"] == lessons.share_of(owed * 3, 1000)   # the statement stands
    with pytest.raises(lessons.LessonError, match="Say why it is waived"):
        fees.settle("flowcrew", TID, month, "waived")
    with pytest.raises(lessons.LessonError, match="Choose paid, waived or due"):
        fees.settle("flowcrew", TID, month, "gone")
    with pytest.raises(lessons.LessonError, match="isn't closed for this school"):
        fees.settle("flowcrew", HOME, month, "paid")
    paid = fees.settle("flowcrew", TID, month, "paid", "EFT 3 Dec", by="master")
    assert paid["schools"][0]["status"] == "paid" and paid["total"]["paid_cents"] == fee and paid["total"]["due_cents"] == 0
    row = money.db.rows["flow_partner_fee_months"][0]
    assert row["settled_by"] == "master" and row["settled_at"] and row["note"] == "EFT 3 Dec"
    again = fees.settle("flowcrew", TID, month, "due")
    assert again["schools"][0]["status"] == "due" and money.db.rows["flow_partner_fee_months"][0]["settled_at"] is None


def test_a_school_sees_only_its_own_fee(money, monkeypatch):
    at = _next(3)
    _r, owed = _taught(at)
    month = at.strftime("%Y-%m")
    fee = lessons.share_of(owed, 1000)
    monkeypatch.setattr(lessons, "_now", lambda: at + timedelta(hours=3))             # later the same day
    fees.save_settings("flowcrew", {"pay_note": "EFT to FlowCrew.", "overrides": {HOME: 700}})
    mine = fees.for_school(TID)
    assert (mine["partner"], mine["fee_bp"], mine["month"], mine["so_far_cents"], mine["statements"]) == ("FlowCrew", 1000, month, fee, [])
    assert mine["pay_note"] == "EFT to FlowCrew." and HOME not in str(mine) and "overrides" not in mine
    monkeypatch.setattr(lessons, "_now", lambda: at + timedelta(days=40))
    run(fees.close("flowcrew", month))
    mine = fees.for_school(TID)
    assert [(s["month"], s["fee_cents"], s["status"]) for s in mine["statements"]] == [(month, fee, "due")] and mine["due_cents"] == fee
    other = fees.for_school(HOME)                                                     # the coach's school owes nothing
    assert other["statements"] == [] and other["due_cents"] == 0 and other["fee_bp"] == 700
    assert fees.for_school(OUTSIDE) == {"partner": None}
