"""Coach profiles and the directory of schools (vula/commerce/lesson_directory.py, migration 305)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tests.fake_supabase import FakeSupabase
from vula.api import commerce as commerce_api, lessons as lessons_api, tenants
from vula.commerce import conditions, lesson_directory as ld, lessons, service

A, B, C = "big_bay_surf", "muizenberg_surf", "quiet_school"
TEAM = {A: [{"id": "m1", "name": "Shane Daniels", "role": "staff"}, {"id": "m2", "name": "Zoe Peters", "role": "owner"},
            {"id": "m9", "name": "Pat Books", "role": "accountant"}], B: [], C: []}
LESSON = {"name": "Surf lesson", "sport": "surf", "group": [{"min": 1, "max": 1, "single_cents": 40000},
                                                             {"min": 2, "max": None, "single_cents": 35000}],
          "private": {"single_cents": 65000}}


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def world(monkeypatch):
    db = FakeSupabase({"commerce_lesson_settings": []})
    active = {A: True, B: True, C: True}
    monkeypatch.setattr(service, "_client", lambda: db)
    monkeypatch.setattr(tenants, "is_active", lambda tid: active.get(tid, False))
    monkeypatch.setattr(tenants, "get_config", lambda tid, **k: {"tenant_id": tid} if tid in active else {})
    monkeypatch.setattr("vula.team_index.active_members", lambda tid: TEAM.get(tid, []))

    async def brand(tid):
        return {"name": {A: "Big Bay Surf", B: "Muizenberg Surf", C: "Quiet School"}[tid], "logo_url": "https://cdn.example/logo.png" if tid == A else "http://plain.example/l.png",
                "accent_color": "#2196F3", "partner": None, "whatsapp": "0820000000"}

    async def forecast(lat, lng, day):
        return {h: {"wave_m": 1.0, "wave_period_s": 10, "wind_kn": 5.0, "gust_kn": 8.0} for h in range(6, 20)}

    monkeypatch.setattr(commerce_api, "public_brand", brand)
    monkeypatch.setattr(conditions, "forecast", forecast)
    lessons.save_settings(A, {"enabled": True, "lessons": [LESSON], "spot": {"name": "Big Bay", "lat": -33.7941, "lng": 18.4594}})
    lessons.save_settings(B, {"enabled": True, "lessons": [LESSON], "spot": {"name": "Surfers Corner", "lat": -34.1087, "lng": 18.4702}})
    lessons.save_settings(C, {"enabled": True, "lessons": [LESSON], "spot": {"name": "Somewhere", "lat": -33.9, "lng": 18.4}})
    ld.changed()
    lessons_api._RECENT.clear()
    return SimpleNamespace(db=db, active=active)


def test_a_profile_is_for_someone_on_the_team_and_the_name_is_theirs(world):
    st = lessons.save_settings(A, {"coach_profiles": [
        {"member_id": "m1", "name": "Kelly Slater", "bio": "  Surfing   Big Bay since 2009. ", "photo_url": "https://cdn.example/shane.jpg",
         "levels": ["first_timer", "expert"], "sports": ["surf", "golf"], "shown": True},
        {"member_id": "m2", "bio": "Owner", "shown": False}]})
    assert st["coach_profiles"][0] == {"member_id": "m1", "name": "Shane", "bio": "Surfing Big Bay since 2009.",
                                       "photo_url": "https://cdn.example/shane.jpg", "levels": ["first_timer"], "sports": ["surf"], "shown": True}
    assert ld.public_coaches(A, st) == [{"name": "Shane", "bio": "Surfing Big Bay since 2009.", "photo_url": "https://cdn.example/shane.jpg",
                                         "levels": ["First timer"], "sports": ["surf"]}]       # Zoe isn't shown; no ids
    for bad in ([{"member_id": "nobody"}], [{"member_id": "m9"}],                              # not on the team; the accountant
                [{"member_id": "m1"}, {"member_id": "m1"}], [{"member_id": "m1", "photo_url": "http://plain.example/a.jpg"}],
                [{"member_id": "m1", "bio": "Book at www.other.co.za"}], "nope"):
        with pytest.raises(lessons.LessonError):
            lessons.save_settings(A, {"coach_profiles": bad})
    assert lessons.settings(A)["coach_profiles"] == st["coach_profiles"]                       # a refused save changed nothing
    TEAM[A] = [m for m in TEAM[A] if m["id"] != "m1"]                                          # Shane leaves the team
    try:
        assert ld.public_coaches(A, lessons.settings(A)) == []
    finally:
        TEAM[A].insert(0, {"id": "m1", "name": "Shane Daniels", "role": "staff"})


def test_only_schools_that_asked_are_listed(world):
    assert run(ld.entries()) == []                                                             # nobody is listed by default
    assert ld.status(A, lessons.settings(A))["why_not"] == "Your school isn't listed."
    lessons.save_settings(A, {"listing": {"listed": True, "about": "Lessons for all ages at Big Bay", "town": "Table View"},
                              "coach_profiles": [{"member_id": "m1", "shown": True}]})
    lessons.save_settings(B, {"listing": {"listed": True, "about": "", "town": "Muizenberg"}})
    got = run(ld.entries())
    assert [e["shop"] for e in got] == ["big-bay-surf", "muizenberg-surf"]                     # C never asked
    a = got[0]
    assert a["spot"] == {"name": "Big Bay", "lat": -33.794, "lng": 18.459}                     # to about 100 m
    assert a["from_cents"] == 35000 and a["sports"] == ["surf"] and a["coach_count"] == 1 and a["coaches"] == [{"name": "Shane", "photo_url": None}]
    assert a["logo_url"] == "https://cdn.example/logo.png" and got[1]["logo_url"] is None      # only an https logo
    assert ld.status(A, lessons.settings(A))["showing"] is True
    with pytest.raises(lessons.LessonError):
        lessons.save_settings(A, {"listing": {"listed": True, "about": "See https://elsewhere.example"}})
    with pytest.raises(lessons.LessonError):
        lessons.save_settings(A, {"listing": {"listed": "yes"}})
    # paused, requests turned off, or the spot removed: off the list, and the school is told why
    world.active[B] = False
    lessons.save_settings(A, {"spot": None})
    assert run(ld.entries()) == []
    assert "spot" in ld.status(A, lessons.settings(A))["why_not"] and "paused" in ld.status(B, lessons.settings(B))["why_not"]


def test_nearest_first_and_the_public_route(world):
    for t in (A, B):
        lessons.save_settings(t, {"listing": {"listed": True, "about": "", "town": ""}})
    rows = run(ld.entries())
    assert ld.distance_km(-33.794, 18.459, -33.794, 18.459) == 0.0
    assert 34.5 < ld.distance_km(-33.794, 18.459, -34.109, 18.470) < 35.5
    assert [r["shop"] for r in ld.near(rows, -34.1, 18.47)] == ["muizenberg-surf", "big-bay-surf"]
    assert "distance_km" not in ld.near(rows, None, None)[0] and "distance_km" not in rows[0]

    class Req:
        headers = {"x-forwarded-for": "41.0.0.7"}
        client = None

    out = run(lessons_api.flow_directory(Req(), lat="-34.1", lng="18.47"))
    assert out["sorted_by"] == "distance" and out["schools"][0]["shop"] == "muizenberg-surf" and out["schools"][0]["distance_km"] < 2
    assert out["schools"][0]["now"]["rating"] in ("good", "marginal", "poor") and out["schools"][0]["now"]["text"]
    for lat, lng in (("abc", "18"), ("-34.1", None), ("-200", "18")):                          # no usable position: by name
        out = run(lessons_api.flow_directory(Req(), lat=lat, lng=lng))
        assert out["sorted_by"] == "name" and [s["shop"] for s in out["schools"]] == ["big-bay-surf", "muizenberg-surf"]
    assert set(out["schools"][0]) == {"shop", "name", "logo_url", "accent_color", "about", "town", "spot", "sports", "main_sport", "lessons",
                                      "from_cents", "hire", "coaches", "coach_count", "now"}  # no tenant id, no phone
    shop = run(lessons_api.flow_shop("big-bay-surf", Req()))
    assert shop["coaches"] == [] and shop["spot"] == {"name": "Big Bay"}                       # a school's own page never gives the position
    with pytest.raises(HTTPException):
        for _ in range(40):
            run(lessons_api.flow_directory(Req()))


def test_a_database_without_the_new_columns_still_saves_the_rest(world, monkeypatch):
    seen = []
    real = world.db.table

    def table(name):
        t = real(name)
        up = t.upsert

        def upsert(row, **k):
            seen.append(set(row))
            return up(row, **k)
        t.upsert = upsert
        return t
    monkeypatch.setattr(world.db, "table", table)
    lessons.save_settings(A, {"note": "Bring a towel"})
    assert not ({"coach_profiles", "listing"} & seen[-1])                                      # untouched columns aren't written
    lessons.save_settings(A, {"listing": {"listed": False}})
    assert "listing" in seen[-1] and "coach_profiles" not in seen[-1]


def test_a_partners_own_address_wears_the_partners_look():
    for host in ("flowcrew.co.za", "app.flowcrew.co.za", "APP.FlowCrew.co.za:443"):
        got = tenants.partner_for_host(host)
        assert got["id"] == "flowcrew" and got["name"] == "FlowCrew" and got["brand"]["accent_color"] == "#2196F3"
    for host in ("notflowcrew.co.za", "flowcrew.co.za.evil.example", "digg.vula-ai.com", ""):
        assert tenants.partner_for_host(host) is None
    out = run(tenants.get_tenant_by_domain("app.flowcrew.co.za"))
    assert out["tenant_id"] is None and out["partner"]["name"] == "FlowCrew"
    import re
    from vula.api import server
    rx = next(m.kwargs["allow_origin_regex"] for m in server.app.user_middleware if "allow_origin_regex" in m.kwargs)
    assert re.fullmatch(rx, "https://app.flowcrew.co.za") and re.fullmatch(rx, "https://flowcrew.co.za")
    assert not re.fullmatch(rx, "https://flowcrew.co.za.evil.example") and not re.fullmatch(rx, "https://evilflowcrew.co.za")


def test_a_partners_business_keeps_its_own_look_apart_from_the_partners(monkeypatch):
    from vula.commerce import service as svc
    kit = {"accent_color": "#2C5545", "logo_url": ""}

    async def settings(tid):
        return dict(kit)
    monkeypatch.setattr(svc, "get_invoice_settings", settings)
    monkeypatch.setattr(tenants, "get_config", lambda tid, **k: {"tenant_id": tid, "features": {"partner": "flowcrew"}})
    b = run(commerce_api.public_brand("x"))
    assert b["partner_look"]["accent_color"] == "#2196F3" and b["accent_color"] == "#2196F3"       # Vula's default green = none chosen
    assert b["own_look"] == {"accent_color": None, "secondary_color": None, "ink_color": None, "logo_url": None}
    kit.update(accent_color="#0B3D5C", logo_url="https://cdn.example/a.png")
    b = run(commerce_api.public_brand("x"))
    assert b["own_look"]["accent_color"] == "#0B3D5C" and b["own_look"]["logo_url"] == "https://cdn.example/a.png" and b["partner_look"]["accent_color"] == "#2196F3"
    monkeypatch.setattr(tenants, "get_config", lambda tid, **k: {"tenant_id": tid})                # no partner: unchanged
    kit.update(accent_color="#2C5545")
    b = run(commerce_api.public_brand("x"))
    assert b["partner_look"] is None and b["own_look"] is None and b["accent_color"] == "#2C5545"
