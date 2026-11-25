"""A customer's own profile (vula/commerce/lesson_customers.py, migration 309): theirs across
schools, read by a school only through their booking with it, a level a coach can confirm."""
from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest
from fastapi import HTTPException

from tests.fake_supabase import FakeSupabase
from tests.test_lesson_progress import MON, _at, _row
from tests.test_lessons import HIRE, KITE, SPOT, SURF, TID
from vula import secret_keys
from vula.api import lessons as lessons_api, tenants
from vula.commerce import conditions, lesson_customers as lc, lesson_ratings as lr, lessons, service

U = "11111111-1111-1111-1111-111111111111"


def run(c):
    return asyncio.run(c)


@pytest.fixture
def db(monkeypatch):
    db = FakeSupabase({"commerce_lesson_settings": [], "commerce_lesson_requests": [], "commerce_lesson_ratings": [],
                       "flow_customer_profiles": []})
    monkeypatch.setattr(service, "_client", lambda: db)
    monkeypatch.setattr(tenants, "is_active", lambda tid: True)
    monkeypatch.setattr(tenants, "display_name", lambda tid: "Big Bay Surf")
    monkeypatch.setattr(tenants, "get_config", lambda tid, **k: {"tenant_id": tid})
    monkeypatch.setattr(lessons, "coaches", lambda tid: [{"id": "m1", "name": "Shane Daniels", "role": "staff"}])
    monkeypatch.setattr(secret_keys, "encrypt", lambda t: "sealed:" + t[::-1])
    monkeypatch.setattr(secret_keys, "decrypt", lambda s: s[len("sealed:"):][::-1])
    lessons.save_settings(TID, {"enabled": True, "lessons": [SURF, KITE], "hire": HIRE, "spot": SPOT,
                                "coach_profiles": [{"member_id": "m1", "shown": True, "photo_url": "https://cdn.example/shane.jpg"}]})
    lr._CACHE.clear()
    lessons_api._RECENT.clear()
    return db


def test_the_catalog_covers_every_sport_with_its_own_skills():
    cat = lc.catalog()
    ids = [s["id"] for s in cat["sports"]]
    assert {"surf", "kite", "windsurf", "wing", "foil", "bodyboard", "kayak", "sup"} <= set(ids) and "other" not in ids
    assert all(s["name"] and s["skills"] for s in cat["sports"]) and cat["levels"]["beginner"] == "Beginner"
    assert set(lc.SKILLS) <= set(lessons.SPORTS)


def test_a_customer_fills_in_their_profile_and_it_reads_back(db):
    assert lc.get(U)["saved"] is False
    p = lc.save(U, {"name": "  Thandi   Mokoena ", "phone": "082 123 4567", "birth_year": 1994, "can_swim": True,
                    "gear": {"height_cm": 168, "weight_kg": 62, "wetsuit": "M"},
                    "emergency": {"name": "Sipho Mokoena", "phone": "0831112222"}, "medical": "Asthma, carries an inhaler",
                    "sports": {"surf": {"level": "beginner", "skills": ["stand", "paddle", "fly"]},
                               "kite": {"level": "first_timer", "skills": []}, "golf": {"level": "advanced"}},
                    "photo": True}, "https://lh3.example/thandi.jpg")
    assert p["name"] == "Thandi Mokoena" and p["phone"] == "27821234567" and p["gear"] == {"height_cm": 168, "weight_kg": 62, "wetsuit": "M"}
    assert p["sports"] == {"surf": {"level": "beginner", "skills": ["paddle", "stand"]}, "kite": {"level": "first_timer", "skills": []}}
    assert p["emergency"] == {"name": "Sipho Mokoena", "phone": "27831112222"} and p["medical"] == "Asthma, carries an inhaler"
    assert p["photo_url"] == "https://lh3.example/thandi.jpg" and p["saved"] is True
    kept = db.rows["flow_customer_profiles"][0]
    assert "Asthma" not in str(kept) and "Sipho" not in str(kept)            # the note and the contact are stored sealed
    assert lc.save(U, {"photo": False})["photo_url"] is None and lc.get(U)["name"] == "Thandi Mokoena"   # only what is sent changes
    assert lc.save(U, {"photo": True}, "http://plain.example/x.jpg")["photo_url"] is None


@pytest.mark.parametrize("bad, why", [({"birth_year": 1800}, "year of birth"), ({"can_swim": "yes"}, "yes or no"),
                                      ({"gear": {"wetsuit": "Huge"}}, "wetsuit size"), ({"gear": {"height_cm": 20}}, "height"),
                                      ({"sports": {"surf": {"level": "pro"}}}, "choose a level"), ({"phone": "12"}, "cellphone"),
                                      ({"emergency": {"name": "X", "phone": "1"}}, "emergency contact")])
def test_what_doesnt_make_sense_is_refused_in_words(db, bad, why):
    with pytest.raises(lc.ProfileError, match=why):
        lc.save(U, bad)
    assert db.rows["flow_customer_profiles"] == []


def test_a_coach_confirms_the_level_once_the_lesson_is_done_and_the_customer_cant_undo_it(db):
    done = _row(db, 1, start=_at(MON), user=U)
    soon = _row(db, 2, start=_at(MON), status="confirmed", user=U)
    anon = _row(db, 3, start=_at(MON))
    lc.save(U, {"sports": {"surf": {"level": "first_timer", "skills": ["paddle"]}}})
    with pytest.raises(lc.ProfileError, match="once the lesson is done"):
        lc.confirm_level(TID, soon["id"], "beginner")
    with pytest.raises(lc.ProfileError, match="didn't sign in"):
        lc.confirm_level(TID, anon["id"], "beginner")
    with pytest.raises(lc.ProfileError, match="Choose the level"):
        lc.confirm_level(TID, done["id"], "pro")
    with pytest.raises(lc.ProfileError, match="couldn't be found"):
        lc.confirm_level("another_school", done["id"], "beginner")
    got = lc.confirm_level(TID, done["id"], "beginner", "Shane Daniels")
    assert got["level"] == "Beginner" and got["confirmed"]["school"] == "Big Bay Surf" and got["confirmed"]["level"] == "beginner"
    mine = lc.get(U)["sports"]["surf"]
    assert mine["level"] == "beginner" and mine["skills"] == ["paddle"]                      # their own skills are kept
    after = lc.save(U, {"sports": {"surf": {"level": "advanced", "skills": ["paddle", "stand"], "confirmed": {"level": "advanced"}}}})
    assert after["sports"]["surf"]["level"] == "advanced" and after["sports"]["surf"]["confirmed"]["level"] == "beginner"
    assert lc.save(U, {"sports": {}})["sports"]["surf"]["confirmed"]["level"] == "beginner"      # clearing keeps what a coach confirmed


def test_a_school_reads_the_profile_only_through_that_customers_live_booking(db):
    lc.save(U, {"name": "Thandi Mokoena", "can_swim": False, "gear": {"wetsuit": "M"}, "medical": "Asthma",
                "emergency": {"name": "Sipho", "phone": "0831112222"},
                "sports": {"surf": {"level": "beginner", "skills": ["stand"]}, "kite": {"level": "advanced", "skills": ["upwind"]}}})
    live = _row(db, 1, start=_at(MON), status="confirmed", user=U)
    gone = _row(db, 2, start=_at(MON), status="cancelled", user=U)
    seen = lc.for_school(TID, live["id"])
    assert seen["sport"] == "surf" and seen["level"] == "Beginner" and seen["skills"] == ["Stand up in the white water"]
    assert seen["can_swim"] is False and seen["gear"] == {"wetsuit": "M"} and seen["medical"] == "Asthma" and seen["can_confirm"] is False
    assert "upwind" not in str(seen) and "Advanced" not in str(seen)             # their other sports aren't this lesson's business
    with pytest.raises(lc.ProfileError, match="waiting, confirmed or done"):
        lc.for_school(TID, gone["id"])
    with pytest.raises(lc.ProfileError, match="couldn't be found"):
        lc.for_school("another_school", live["id"])
    with pytest.raises(HTTPException) as e:
        run(lessons_api.lesson_customer("another_school", live["id"]))
    assert e.value.status_code == 404


def test_my_coaches_come_from_finished_lessons_with_my_own_stars(db):
    a = _row(db, 1, start=_at(MON), user=U)
    _row(db, 2, start=_at(MON + timedelta(days=7)), user=U)
    _row(db, 3, start=_at(MON + timedelta(days=14)), status="confirmed", user=U)
    _row(db, 4, start=_at(MON), user="22222222-2222-2222-2222-222222222222")
    lr.rate(TID, lessons.token_for(a["id"]), 5, 4)
    got = lc.my_coaches(U)
    assert got == [{"name": "Shane", "school": "Big Bay Surf", "shop": TID.replace("_", "-"), "photo_url": "https://cdn.example/shane.jpg",
                    "lessons": 2, "last": lessons.when_text(_at(MON + timedelta(days=7))), "lesson_id": "surf", "my_stars": 4.0}]
    assert lc.my_coaches("33333333-3333-3333-3333-333333333333") == []


def test_the_customer_deletes_their_profile_and_the_routes_need_their_sign_in(db, monkeypatch):
    lc.save(U, {"name": "Thandi"})
    assert lc.erase(U) is True and lc.get(U)["saved"] is False

    class Req:
        client = type("C", (), {"host": "1.2.3.4"})()
        def __init__(self, token=""):
            self.headers = {"authorization": f"Bearer {token}"} if token else {}

    async def verify(token):
        return {"id": U, "user_metadata": {"avatar_url": "https://lh3.example/t.jpg"}} if token == "good" else None
    monkeypatch.setattr("vula.api.master_auth._verify_jwt", verify)
    for call in (lessons_api.flow_profile(Req()), lessons_api.flow_profile(Req("forged")), lessons_api.flow_profile_save({}, Req()),
                 lessons_api.flow_profile_delete(Req())):
        with pytest.raises(HTTPException) as e:
            run(call)
        assert e.value.status_code == 401
    got = run(lessons_api.flow_profile(Req("good")))
    assert got["account_photo"] is True and got["profile"]["saved"] is False and got["coaches"] == [] and got["catalog"]["sports"]
    saved = run(lessons_api.flow_profile_save({"name": "Thandi", "photo": True}, Req("good")))
    assert saved["profile"]["photo_url"] == "https://lh3.example/t.jpg"
    with pytest.raises(HTTPException) as e:
        run(lessons_api.flow_profile_save({"birth_year": 1}, Req("good")))
    assert e.value.status_code == 422


def test_a_profile_that_cant_be_read_stops_nothing(db):
    del db.rows["flow_customer_profiles"]
    assert lc.get(U) == {**lc.get(U), "saved": False} and lc.get(U)["sports"] == {}


def test_the_newer_sports_are_rated_by_the_one_they_are_most_like():
    calm, windy = {"wind_kn": 4.0, "gust_kn": 6.0, "wave_m": 0.4}, {"wind_kn": 18.0, "gust_kn": 22.0, "wave_m": 0.8}
    assert conditions.rate("windsurf", "", calm)["rating"] == conditions.rate("kite", "", calm)["rating"] == "poor"
    assert conditions.rate("wing", "", windy)["rating"] == "good" and conditions.rate("kayak", "", windy)["rating"] == "poor"
    assert conditions.rate("bodyboard", "", windy) == conditions.rate("surf", "", windy)
    out = conditions.brief({h: {**windy, "wind_from": 135} for h in range(6, 18)}, {}, ["windsurf"])
    assert "windsurfing" in out["headline"]
