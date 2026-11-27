"""Each coach's week and what a new lesson would run into (vula/commerce/lesson_calendar.py), and
the coach's own profile and hours (their own entry only)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tests.fake_supabase import FakeSupabase
from tests.test_lessons import HIRE, KITE, SPOT, SURF, TID
from vula.api import lessons as lessons_api, tenants
from vula.commerce import lesson_calendar as cal, lesson_directory as ld, lessons, service
from vula.tap.shifts import ShiftDesk

TEAM = [{"id": "m1", "name": "Shane Daniels", "role": "staff", "whatsapp": "27820000001", "email": "shane@example.com"},
        {"id": "m2", "name": "Zoe Peters", "role": "owner", "whatsapp": "27820000002", "email": "zoe@example.com"}]


def run(c):
    return asyncio.run(c)


class Desk:
    def _fits(self, rows, member_id, start, end):
        return ShiftDesk._fits(None, rows, member_id, start, end)


@pytest.fixture
def world(monkeypatch):
    db = FakeSupabase({"commerce_lesson_settings": [], "commerce_lesson_requests": [], "commerce_invoices": []})
    avail = []
    monkeypatch.setattr(service, "_client", lambda: db)
    monkeypatch.setattr(tenants, "is_active", lambda tid: True)
    monkeypatch.setattr(tenants, "display_name", lambda tid: "Big Bay Surf")
    monkeypatch.setattr("vula.team_index.active_members", lambda tid: TEAM)
    monkeypatch.setattr(cal, "_availability", lambda tid: (Desk(), avail))

    async def quiet(*a, **k):
        return True
    monkeypatch.setattr("vula.integrations.notify.notify_team", quiet)
    monkeypatch.setattr(lessons, "_tell_customer", quiet)
    lessons.save_settings(TID, {"enabled": True, "lessons": [SURF, KITE], "hire": HIRE, "spot": SPOT})
    return SimpleNamespace(db=db, avail=avail)


def _next(weekday, hour=9, minute=0):
    """The next such weekday, a week or more ahead so nothing is in the past."""
    today = datetime.now(lessons.SAST).date()
    d = today + timedelta(days=(weekday - today.weekday()) % 7 + 7)
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=lessons.SAST)


def _ask(day, people=2, item="surf", name="Thandi Mokoena", phone="082 123 4567"):
    return run(lessons.request(TID, kind="lesson", item_id=item, wanted_date=day.date().isoformat(), name=name, phone=phone,
                               people=people, fmt="group", level="first_timer"))


def test_bookings_for_the_same_lesson_and_time_are_one_group_session(world):
    at = _next(1, 9)
    a, b = _ask(at, 2), _ask(at, 2, name="Sipho Dube", phone="083 111 2222")
    run(lessons.confirm(TID, a["id"], starts_at=at.isoformat(), coach_id="m1"))
    run(lessons.confirm(TID, b["id"], starts_at=at.isoformat(), coach_id="m1"))
    w = cal.week(TID, at.date().isoformat())
    shane = next(c for c in w["coaches"] if c["coach_id"] == "m1")
    day = shane["days"][at.date().isoformat()]
    assert len(day) == 1 and day[0]["people"] == 4 and day[0]["max"] == 5 and day[0]["time"] == "09:00–10:30"
    assert [x["customer_name"] for x in day[0]["bookings"]] == ["Thandi Mokoena", "Sipho Dube"]
    assert shane["lessons"] == 1 and shane["people"] == 4 and len(w["days"]) == 7 and w["week"] == (at.date() - timedelta(days=1)).isoformat()
    assert next(c for c in w["coaches"] if c["coach_id"] == "m2")["lessons"] == 0
    only = cal.week(TID, at.date().isoformat(), only_coach="m2")
    assert [c["coach_id"] for c in only["coaches"]] == ["m2"] and "Thandi" not in str(only)      # a coach's own view


def test_a_coach_isnt_given_two_lessons_at_once_or_too_many_people(world):
    at = _next(2, 9)
    first, third = _ask(at, 4), _ask(at, 2, name="Lebo", phone="084 000 1111")
    kite = _ask(at, 1, item="kitelesson", name="Anna", phone="085 000 2222")
    run(lessons.confirm(TID, first["id"], starts_at=at.isoformat(), coach_id="m1"))
    with pytest.raises(lessons.LessonError, match="That would make 6 people in Shane's 09:00 surf lesson; the limit is 5 per coach") as e:
        run(lessons.confirm(TID, third["id"], starts_at=at.isoformat(), coach_id="m1"))
    assert e.value.status == 409 and world.db.rows["commerce_lesson_requests"][1]["status"] == "requested"
    with pytest.raises(lessons.LessonError, match="Shane is already teaching surf lesson 09:00–10:30"):
        run(lessons.confirm(TID, kite["id"], starts_at=(at + timedelta(minutes=30)).isoformat(), coach_id="m1", price_cents=150000))
    after = run(lessons.confirm(TID, kite["id"], starts_at=(at + timedelta(minutes=90)).isoformat(), coach_id="m1", price_cents=150000))
    assert after["status"] == "confirmed" and after["warning"] == ""           # straight after the first one ends: no clash
    other = run(lessons.confirm(TID, third["id"], starts_at=at.isoformat(), coach_id="m2"))        # another coach is free
    assert other["coach_name"] == "Zoe Peters"
    moved = run(lessons.confirm(TID, first["id"], starts_at=at.isoformat(), coach_id="m1"))        # re-confirming doesn't clash with itself
    assert moved["status"] == "confirmed"


def test_the_school_can_insist_and_a_coach_who_is_away_is_a_warning_not_a_refusal(world):
    at = _next(3, 9)
    a, b = _ask(at, 5), _ask(at, 3, name="Lebo", phone="084 000 1111")
    run(lessons.confirm(TID, a["id"], starts_at=at.isoformat(), coach_id="m1"))
    forced = run(lessons.confirm(TID, b["id"], starts_at=at.isoformat(), coach_id="m1", allow_clash=True))
    assert forced["status"] == "confirmed"
    world.avail.append({"member_id": "m2", "kind": "off", "from_date": at.date().isoformat(), "to_date": at.date().isoformat(), "note": "Leave"})
    world.avail.append({"member_id": "m1", "kind": "weekly", "weekday": at.weekday(), "start_time": "12:00", "end_time": "17:00"})
    c = _ask(at, 1, name="Anna", phone="085 000 2222")
    away = run(lessons.confirm(TID, c["id"], starts_at=at.isoformat(), coach_id="m2"))
    assert away["status"] == "confirmed" and away["warning"] == "Zoe is away that day (Leave)."
    hit = cal.check(TID, "m1", at + timedelta(days=7), "surf", 1)
    assert hit["clash"] == "" and "hasn't said they can work then" in hit["outside"]
    w = cal.week(TID, at.date().isoformat())
    assert next(x for x in w["coaches"] if x["coach_id"] == "m2")["away"] == {at.date().isoformat(): "Leave"}
    assert next(x for x in w["coaches"] if x["coach_id"] == "m1")["hours"] == [{"weekday": at.weekday(), "start": "12:00", "end": "17:00"}]


def test_the_calendar_still_reads_when_there_is_no_roster(world, monkeypatch):
    monkeypatch.setattr(cal, "_availability", lambda tid: (None, []))
    at = _next(4, 9)
    a = _ask(at, 1)
    assert run(lessons.confirm(TID, a["id"], starts_at=at.isoformat(), coach_id="m1"))["warning"] == ""
    assert cal.week(TID, at.date().isoformat())["coaches"][0]["hours"] == []
    with pytest.raises(lessons.LessonError, match="Choose a week"):
        cal.week(TID, "soon")


def test_a_coach_edits_only_their_own_profile_and_never_whether_it_shows(world):
    lessons.save_settings(TID, {"coach_profiles": [{"member_id": "m2", "bio": "Owner and coach", "shown": True}]})
    mine = ld.save_own(TID, "m1", {"bio": "  Surfing Big Bay since 2009. ", "levels": ["first_timer"], "sports": ["surf", "kite"],
                                    "spots": [{"name": "Big Bay", "lat": -33.7941, "lng": 18.4594}], "shown": True, "member_id": "m2"})
    assert mine == {"bio": "Surfing Big Bay since 2009.", "photo_url": None, "levels": ["first_timer"], "sports": ["surf", "kite"],
                    "spots": [{"name": "Big Bay", "lat": -33.794, "lng": 18.459, "photo_url": None}], "shown": False}
    kept = {p["member_id"]: p for p in lessons.settings(TID)["coach_profiles"]}
    assert kept["m2"]["bio"] == "Owner and coach" and kept["m2"]["shown"] is True and kept["m1"]["shown"] is False
    assert ld.save_own(TID, "m1", {"bio": "New words"})["sports"] == ["surf", "kite"]              # only what is sent changes
    with pytest.raises(lessons.LessonError, match="https://"):
        ld.save_own(TID, "m1", {"photo_url": "http://plain.example/me.jpg"})
    with pytest.raises(lessons.LessonError, match="aren't on this school's team"):
        ld.save_own(TID, "gone", {"bio": "x"})


def test_the_own_routes_take_who_you_are_from_the_login(world, monkeypatch):
    who = {"v": "shane@example.com"}

    async def acting(auth, tenant_id):
        return who["v"]
    monkeypatch.setattr("vula.acting.from_dashboard", acting)

    async def not_manager(request, tenant_id):
        return False
    monkeypatch.setattr("vula.api.commerce._may_apply_stock", not_manager)
    req = SimpleNamespace(headers={"authorization": "Bearer x"})
    got = run(lessons_api.save_my_coach_profile(TID, {"bio": "Mine", "member_id": "m2"}, req))
    assert got["me"]["id"] == "m1" and got["profile"]["bio"] == "Mine"
    assert run(lessons_api.my_coach_profile(TID, req))["profile"]["bio"] == "Mine"
    week = run(lessons_api.lesson_calendar_week(TID, req))
    assert week["everyone"] is False and [c["coach_id"] for c in week["coaches"]] == ["m1"]
    who["v"] = "stranger@example.com"
    for call in (lessons_api.my_coach_profile(TID, req), lessons_api.save_my_coach_profile(TID, {}, req), lessons_api.lesson_calendar_week(TID, req)):
        with pytest.raises(HTTPException) as e:
            run(call)
        assert e.value.status_code == 403
