"""A customer's own progress and their ratings (vula/commerce/lesson_progress.py, lesson_ratings.py,
migration 308): counted from finished lessons, a lesson rated once by the customer who had it."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta

import pytest
from fastapi import HTTPException

from tests.fake_supabase import FakeSupabase
from tests.test_lessons import HIRE, KITE, SPOT, SURF, TID
from vula.api import lessons as lessons_api, tenants
from vula.commerce import conditions, lesson_progress as lp, lesson_ratings as lr, lessons, service


def run(c):
    return asyncio.run(c)


@pytest.fixture
def db(monkeypatch):
    db = FakeSupabase({"commerce_lesson_settings": [], "commerce_lesson_requests": [], "commerce_lesson_ratings": []})
    monkeypatch.setattr(service, "_client", lambda: db)
    monkeypatch.setattr(tenants, "is_active", lambda tid: True)
    monkeypatch.setattr(tenants, "display_name", lambda tid: "Big Bay Surf")
    monkeypatch.setattr(tenants, "get_config", lambda tid, **k: {"tenant_id": tid})
    monkeypatch.setattr(lessons, "coaches", lambda tid: [{"id": "m1", "name": "Shane Daniels", "role": "staff"},
                                                           {"id": "m2", "name": "Zoe Peters", "role": "owner"}])
    lessons.save_settings(TID, {"enabled": True, "lessons": [SURF, KITE], "hire": HIRE, "spot": SPOT})
    lr._CACHE.clear()
    lessons_api._RECENT.clear()
    return db


def _row(db, n, *, start, status="completed", item="surf", level="first_timer", coach="m1", kind="lesson", user=None):
    r = {"id": f"00000000-0000-0000-0000-{n:012d}", "tenant_id": TID, "ref": f"L-{n:05d}", "kind": kind, "item_id": item,
         "item_name": "Surf lesson", "level": level, "format": "group", "party_size": 1, "sessions": 1, "extras": [],
         "wanted_date": start[:10], "wanted_part": "morning", "status": status, "starts_at": start,
         "coach_id": coach, "coach_name": "Shane Daniels" if coach else None, "customer_name": "Thandi Mokoena",
         "customer_phone": "27821234567", "customer_user_id": user, "quote_cents": 40000, "price_cents": None,
         "quote_lines": [], "created_at": start}
    db.rows["commerce_lesson_requests"].append(r)
    return r


def _at(day, hour=9):
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=lessons.SAST).isoformat()


MON = date(2026, 11, 2)


def test_progress_is_counted_from_lessons_marked_done_and_nothing_else(db):
    rows = [_row(db, 1, start=_at(MON, 7)), _row(db, 2, start=_at(MON + timedelta(days=7)), level="beginner"),
            _row(db, 3, start=_at(MON + timedelta(days=14)), level="beginner"),
            _row(db, 4, start=_at(MON + timedelta(days=30)), status="confirmed"),
            _row(db, 5, start=_at(MON + timedelta(days=3)), status="no_show"),
            _row(db, 6, start=_at(MON), kind="hire", item="surfboard")]
    p = lp.progress(rows, lessons._sport_of({}))
    assert p["lessons"] == 3 and p["level"] == "Beginner" and p["next_level"] == "Intermediate" and p["weeks_running"] == 3
    earned = {b["id"] for b in p["badges"] if b["earned"]}
    assert earned == {"first", "dawn", "weeks"} and [b["id"] for b in p["badges"]][:2] == ["first", "five"]
    assert all(b["how"] for b in p["badges"])                         # every badge says how it is earned
    none = lp.progress([], lessons._sport_of({}))
    assert none["lessons"] == 0 and none["level"] is None and not any(b["earned"] for b in none["badges"])


def test_weeks_in_a_row_cross_a_new_year_and_a_second_sport_earns_its_badge(db):
    assert lp._weeks_running([(2026, 52), (2026, 53), (2027, 1), (2027, 3)]) == 3
    rows = [_row(db, 1, start=_at(MON)), _row(db, 2, start=_at(MON + timedelta(days=1)), item="kitelesson")]
    assert next(b for b in lp.progress(rows, lessons._sport_of({}))["badges"] if b["id"] == "sports")["earned"] is True
    top = lp.progress([_row(db, 3, start=_at(MON), level="advanced")], lessons._sport_of({}))
    assert top["level"] == "Advanced" and top["next_level"] is None


def test_a_finished_lesson_is_rated_once_by_the_customer_who_had_it(db):
    done, soon, gear = _row(db, 1, start=_at(MON)), _row(db, 2, start=_at(MON), status="confirmed"), \
        _row(db, 3, start=_at(MON), kind="hire", item="surfboard", coach=None)
    tok = lessons.token_for
    with pytest.raises(lr.RatingError, match="once it is done"):
        lr.rate(TID, tok(soon["id"]), 5)
    with pytest.raises(lr.RatingError, match="once it is done"):
        lr.rate(TID, tok(gear["id"]), 5)
    with pytest.raises(lr.RatingError, match="couldn't be found"):
        lr.rate(TID, "not-a-token", 5)
    with pytest.raises(lr.RatingError, match="couldn't be found"):
        lr.rate("another_school", tok(done["id"]), 5)                 # a token opens its own school's lesson only
    for bad in (0, 6, "5", True, None):
        with pytest.raises(lr.RatingError, match="1 to 5"):
            lr.rate(TID, tok(done["id"]), bad)
    assert db.rows["commerce_lesson_ratings"] == []
    got = lr.rate(TID, tok(done["id"]), 4, 5, "  Shane was   patient. ")
    assert got == {"stars": 4, "coach_stars": 5}
    kept = db.rows["commerce_lesson_ratings"][0]
    assert kept["coach_id"] == "m1" and kept["comment"] == "Shane was patient." and kept["tenant_id"] == TID
    with pytest.raises(lr.RatingError, match="already rated"):
        lr.rate(TID, tok(done["id"]), 1)
    assert len(db.rows["commerce_lesson_ratings"]) == 1


def test_the_averages_are_public_and_the_comment_is_the_schools_alone(db):
    assert lr.public(TID) is None and lr.summary(TID) == {"stars": None, "count": 0, "coaches": {}}
    for n, (stars, coach) in enumerate([(5, 5), (4, 4), (4, None)], 1):
        r = _row(db, n, start=_at(MON + timedelta(days=n)), coach="m1" if coach else None)
        lr.rate(TID, lessons.token_for(r["id"]), stars, coach, "Lovely morning" if n == 1 else "")
    assert lr.public(TID) == {"stars": 4.3, "count": 3}
    assert lr.summary(TID)["coaches"] == {"m1": {"stars": 4.5, "count": 2}}
    mine = lessons.by_tokens(TID, [lessons.token_for(r["id"]) for r in db.rows["commerce_lesson_requests"]])
    assert all(m["rated"] and not m["can_rate"] for m in mine) and "comment" not in str(mine)
    school = lr.for_school(TID)
    assert school["stars"] == 4.3 and school["coaches"] == [{"name": "Shane Daniels", "stars": 4.5, "count": 2}]
    assert [x["comment"] for x in school["latest"]].count("Lovely morning") == 1 and school["latest"][0]["customer"] == "Thandi"


def test_ratings_that_cant_be_read_stop_nothing(db, monkeypatch):
    r = _row(db, 1, start=_at(MON))
    del db.rows["commerce_lesson_ratings"]
    monkeypatch.setattr(lr, "_rows", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no table")))
    assert lr.public(TID) is None and lr.rated_ids([r["id"]]) == set()
    mine = lessons.by_tokens(TID, [lessons.token_for(r["id"])])
    assert mine[0]["status"] == "completed"


def test_what_a_phone_and_a_signed_in_customer_see(db):
    a = _row(db, 1, start=_at(MON), user="u-1")
    _row(db, 2, start=_at(MON + timedelta(days=7)), user="u-1")
    _row(db, 3, start=_at(MON), user="u-2")
    assert lessons.progress_by_tokens(TID, [lessons.token_for(a["id"])])["lessons"] == 1     # this phone's own
    assert lessons.progress_by_tokens(TID, ["junk"])["lessons"] == 0
    acct = lessons.for_account("u-1")
    assert acct["progress"]["lessons"] == 2 and len(acct["requests"]) == 2 and acct["requests"][0]["can_rate"] is True


def test_the_next_good_day_is_the_first_well_rated_part_from_tomorrow(db, monkeypatch):
    today = date(2026, 11, 25)

    async def forecast(lat, lng, day):
        windy = day < today + timedelta(days=3)
        return {h: {"wave_m": 1.0, "wind_kn": 30.0 if windy or h >= 10 else 6.0, "gust_kn": 40.0 if windy or h >= 10 else 9.0,
                    "wind_from": 135} for h in range(6, 18)}
    monkeypatch.setattr(conditions, "forecast", forecast)
    got = run(lp.next_good_day(SPOT, "surf", "first_timer", today=today))
    assert got["date"] == "2026-11-28" and got["part"] == "morning" and got["day"].startswith("Sat 28")

    async def always_bad(lat, lng, day):
        return {h: {"wave_m": 4.0, "wind_kn": 30.0, "gust_kn": 45.0} for h in range(6, 18)}
    monkeypatch.setattr(conditions, "forecast", always_bad)
    assert run(lp.next_good_day(SPOT, "surf", today=today)) is None

    async def down(lat, lng, day):
        raise RuntimeError("no forecast")
    monkeypatch.setattr(conditions, "forecast", down)
    assert run(lp.next_good_day(SPOT, "surf", today=today)) is None


def test_the_rate_route_refuses_in_words(db):
    r = _row(db, 1, start=_at(MON), status="confirmed")

    class Req:
        client = type("C", (), {"host": "1.2.3.4"})()
        headers = {}
    with pytest.raises(HTTPException) as e:
        run(lessons_api.flow_rate(TID.replace("_", "-"), {"token": lessons.token_for(r["id"]), "stars": 5}, Req()))
    assert e.value.status_code == 409 and "once it is done" in e.value.detail


def test_erasing_a_person_removes_their_words_and_keeps_the_stars(db, monkeypatch):
    from vula import privacy_data
    monkeypatch.setattr(privacy_data, "_client", lambda: db)
    r = _row(db, 1, start=_at(MON))
    other = _row(db, 2, start=_at(MON))
    other["customer_phone"] = "27830000000"
    lr.rate(TID, lessons.token_for(r["id"]), 5, 5, "Shane was great")
    lr.rate(TID, lessons.token_for(other["id"]), 3, None, "Too cold")
    step = dict(privacy_data.ERASE_STEPS)["lesson_rating_words"]
    assert step(TID, ["27821234567", "0821234567"]) == 1 and step(TID, ["27821234567"]) == 0
    kept = {x["request_id"]: x for x in db.rows["commerce_lesson_ratings"]}
    assert kept[r["id"]]["comment"] is None and kept[r["id"]]["stars"] == 5 and kept[other["id"]]["comment"] == "Too cold"
