"""Lessons and gear hire the business confirms by hand (vula/commerce/lessons.py, 24 Nov 2026):
the quote is the price list's, worked out in code; nothing is booked until a person confirms it;
a customer cancels only their own; the chat asks from exact taps; the conditions check reads the
forecast's own figures. The price list is a real surf school's published one (Big Bay, 2026)."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tests.fake_supabase import FakeSupabase
from vula.api import lessons as lessons_api, tenants
from vula.commerce import conditions, lesson_chat, lessons, service

TID = "big_bay_surf"

SURF = {"id": "surf", "name": "Surf lesson", "sport": "surf", "minutes": 90, "max_per_coach": 5, "package_sessions": 4,
        "group": [{"min": 1, "max": 1, "single_cents": 40000, "package_cents": 140000},
                  {"min": 2, "max": 3, "single_cents": 35000, "package_cents": 120000},
                  {"min": 4, "max": None, "single_cents": 30000, "package_cents": 100000}],
        "private": {"single_cents": 65000, "package_cents": 240000},
        "extras": [{"id": "wetsuit", "name": "Wetsuit", "cents": 5000}]}
KITE = {"name": "Kite lesson", "sport": "kite", "private": {"single_cents": 150000}}
HIRE = [{"name": "Surfboard", "prices": {"2h": 15000, "4h": 25000, "1d": 30000, "2d": 56000, "3d": 78000}, "extra_day_cents": 24000},
        {"name": "Wetsuit", "prices": {"2h": 7000, "1d": 10000}}]
SPOT = {"name": "Big Bay", "lat": -33.794, "lng": 18.459}


def run(c):
    return asyncio.run(c)


@pytest.fixture
def world(monkeypatch):
    db = FakeSupabase({"commerce_lesson_settings": [], "commerce_lesson_requests": [], "commerce_invoices": []})
    monkeypatch.setattr(service, "_client", lambda: db)
    monkeypatch.setattr(tenants, "is_active", lambda tid: tid != "paused")
    monkeypatch.setattr(tenants, "display_name", lambda tid: "Big Bay Surf")
    monkeypatch.setattr(lessons, "coaches", lambda tid: [{"id": "m1", "name": "Shane Daniels", "role": "staff"}])
    team, told = [], []

    async def notify_team(tenant_id, event, message, **kw):
        team.append((event, message))

    async def tell(tenant_id, r, text, news):
        told.append(text)
        return True

    monkeypatch.setattr("vula.integrations.notify.notify_team", notify_team)
    monkeypatch.setattr(lessons, "_tell_customer", tell)
    lessons.save_settings(TID, {"enabled": True, "lessons": [SURF, KITE], "hire": HIRE, "spot": SPOT,
                                "form_url": "https://bigbaysurf.example.co.za/waiver",
                                "pay_note": "Pay at the shop or by EFT."})
    return SimpleNamespace(db=db, team=team, told=told)


def _day(n=1):
    return (datetime.now(lessons.SAST) + timedelta(days=n)).date().isoformat()


def _at(n=1, hour=9):
    d = (datetime.now(lessons.SAST) + timedelta(days=n)).date()
    return datetime(d.year, d.month, d.day, hour, 0, tzinfo=lessons.SAST).isoformat()


def _ask(**kw):
    base = dict(kind="lesson", item_id="surf", wanted_date=_day(), name="Thandi Mokoena", phone="082 123 4567",
                people=2, fmt="group", level="first_timer")
    return run(lessons.request(TID, **{**base, **kw}))


# ── the price list ────────────────────────────────────────────────────────────────────────────
def test_settings_are_checked_and_read_back(world):
    st = lessons.settings(TID)
    assert [x["id"] for x in st["lessons"]] == ["surf", "kitelesson"] and [g["id"] for g in st["hire"]] == ["surfboard", "wetsuit"]
    assert st["lessons"][1]["minutes"] == 90 and st["lessons"][1]["group"] == []
    for bad in ({"lessons": [{"name": "Surf"}]},                                              # no price at all
                {"lessons": [{**SURF, "group": [{"min": 1, "max": 3, "single_cents": 1}, {"min": 2, "max": 4, "single_cents": 1}]}]},
                {"lessons": [{**SURF, "private": {"single_cents": 650.0}}]},                  # rand as a float, not cents
                {"lessons": [SURF, {**SURF}]},                                                # the same short name twice
                {"hire": [{"name": "Board", "prices": {}}]},
                {"spot": {"name": "Big Bay", "lat": 120, "lng": 18}},
                {"form_url": "http://not-secure.example/waiver"},
                {"enabled": "yes"}):
        with pytest.raises(lessons.LessonError):
            lessons.save_settings(TID, bad)
    assert lessons.settings(TID) == st                                                        # nothing changed
    with pytest.raises(lessons.LessonError, match="Add a lesson"):
        lessons.save_settings("empty", {"enabled": True})


def test_a_group_lesson_is_priced_per_person_by_how_many_come(world):
    st = lessons.settings(TID)
    assert lessons.quote_lesson(st, "surf", "group", 1, False)[0] == 40000
    assert lessons.quote_lesson(st, "surf", "group", 2, False)[0] == 70000
    assert lessons.quote_lesson(st, "surf", "group", 3, False)[0] == 105000
    assert lessons.quote_lesson(st, "surf", "group", 4, False)[0] == 120000
    assert lessons.quote_lesson(st, "surf", "group", 9, False)[0] == 270000
    cents, lines, sessions = lessons.quote_lesson(st, "surf", "group", 3, True, {"wetsuit": 2})
    assert sessions == 4 and cents == 3 * 120000 + 2 * 4 * 5000                               # a wetsuit per lesson
    assert lines == [{"description": "Surf lesson (group), 4-lesson package, per person", "quantity": 3, "unit_price_cents": 120000},
                     {"description": "Wetsuit per lesson", "quantity": 8, "unit_price_cents": 5000}]
    assert lessons.total(lines) == cents
    with pytest.raises(lessons.LessonError, match="at most one per person"):
        lessons.quote_lesson(st, "surf", "group", 2, False, {"wetsuit": 3})


def test_a_private_lesson_is_priced_for_one_and_left_to_the_business_for_more(world):
    st = lessons.settings(TID)
    assert lessons.quote_lesson(st, "surf", "private", 1, False)[0] == 65000
    assert lessons.quote_lesson(st, "surf", "private", 1, True)[0] == 240000
    assert lessons.quote_lesson(st, "surf", "private", 2, False)[0] is None                   # no listed price: not guessed
    assert lessons.quote_lesson(st, "kitelesson", "group", 2, False)[0] is None               # no group price listed
    assert lessons.quote_lesson(st, "kitelesson", "private", 1, True)[0] is None              # no package listed
    with pytest.raises(lessons.LessonError):
        lessons.quote_lesson(st, "wakeboard", "group", 1, False)


def test_gear_is_priced_by_how_long_it_is_out(world):
    st = lessons.settings(TID)
    assert lessons.quote_hire(st, "surfboard", "2h", 1)[0] == 15000
    assert lessons.quote_hire(st, "surfboard", "3d", 2)[0] == 156000
    assert lessons.quote_hire(st, "surfboard", "7d", 1)[0] == 7 * 24000                       # from 4 days: per day
    assert lessons.quote_hire(st, "wetsuit", "4h", 1)[0] is None and lessons.quote_hire(st, "wetsuit", "7d", 1)[0] is None
    assert lessons.hire_durations(st["hire"][0]) == ["2h", "4h", "1d", "2d", "3d", "7d"]
    assert lessons.hire_durations(st["hire"][1]) == ["2h", "1d"]
    text = lessons.price_list_text(TID)
    assert "• Group, 2–3 people: R350 each, 4 lessons R1,200 each" in text and "• Group, 4+ people: R300 each" in text
    assert "• Group, 1 person: R400, 4 lessons R1,400" in text and "• Private 1-on-1: R650, 4 lessons R2,400" in text
    assert "• Surfboard: 2 hours R150, 4 hours R250, 1 day R300" in text


# ── a request, and a person confirming it ─────────────────────────────────────────────────────
def test_a_request_is_kept_quoted_and_not_booked(world):
    r = _ask(extras={"wetsuit": 2})
    row = world.db.rows["commerce_lesson_requests"][0]
    assert row["status"] == "requested" and row.get("starts_at") is None and row["customer_phone"] == "27821234567"
    assert r["quote_cents"] == 80000 and r["amount"] == "R800" and r["ref"].startswith("L-")
    assert r["summary"] == "Surf lesson, group, 2 people" and r["level_text"] == "First timer"
    assert world.team[0][0] == "lesson_request" and "Quote R800" in world.team[0][1] and r["ref"] in world.team[0][1]
    for bad in (dict(wanted_date="2020-01-01"), dict(name="T"), dict(phone="12"), dict(item_id="nope"), dict(kind="tour")):
        with pytest.raises(lessons.LessonError):
            _ask(**bad)
    with pytest.raises(lessons.LessonError):
        run(lessons.request("paused", kind="lesson", item_id="surf", wanted_date=_day(), name="Thandi", phone="0821234567"))
    assert len(world.db.rows["commerce_lesson_requests"]) == 1


def test_confirming_sets_the_time_and_coach_and_tells_the_customer(world):
    r = _ask()
    with pytest.raises(lessons.LessonError, match="already passed"):
        run(lessons.confirm(TID, r["id"], starts_at=_at(-1)))
    with pytest.raises(lessons.LessonError, match="isn't on the team"):
        run(lessons.confirm(TID, r["id"], starts_at=_at(), coach_id="gone"))
    assert world.db.rows["commerce_lesson_requests"][0]["status"] == "requested" and world.told == []
    done = run(lessons.confirm(TID, r["id"], starts_at=_at(1, 9), coach_id="m1", by="27820000001"))
    assert done["status"] == "confirmed" and done["coach_name"] == "Shane Daniels" and "at 09:00" in done["when"]
    said = world.told[-1]
    assert "Confirmed: Surf lesson, group, 2 people" in said and "Your coach is Shane." in said and "Total R700." in said
    assert "Pay at the shop or by EFT." in said and "https://bigbaysurf.example.co.za/waiver" in said and done["ref"] in said
    again = run(lessons.confirm(TID, r["id"], starts_at=_at(2, 14)))                          # the time can be moved
    assert "at 14:00" in again["when"] and again["coach_name"] == "Shane Daniels"
    with pytest.raises(lessons.LessonError):
        run(lessons.confirm("other", r["id"], starts_at=_at()))                               # another business's request
    lessons.set_status(TID, r["id"], "completed")
    with pytest.raises(lessons.LessonError, match="already completed"):
        run(lessons.confirm(TID, r["id"], starts_at=_at()))


def test_a_request_with_no_listed_price_needs_one_at_confirmation(world):
    r = _ask(fmt="private", people=2)
    assert r["quote_cents"] is None and "No listed price" in world.team[-1][1]
    with pytest.raises(lessons.LessonError, match="Enter the price"):
        run(lessons.confirm(TID, r["id"], starts_at=_at()))
    with pytest.raises(lessons.LessonError):
        run(lessons.confirm(TID, r["id"], starts_at=_at(), price_cents=950.5))
    done = run(lessons.confirm(TID, r["id"], starts_at=_at(), price_cents=95000))
    assert done["amount"] == "R950" and "Total R950." in world.told[-1]


def test_a_draft_invoice_carries_the_quote_lines_or_the_agreed_price(world, monkeypatch):
    made = []

    async def create_invoice(tenant_id, data):
        made.append(data)
        return {"id": f"00000000-0000-0000-0000-00000000000{len(made)}"}

    monkeypatch.setattr(service, "create_invoice", create_invoice)
    a = _ask(extras={"wetsuit": 1})
    done = run(lessons.confirm(TID, a["id"], starts_at=_at(), invoice=True))
    assert done["invoice_id"] and made[0]["status"] == "draft" and made[0]["prices_include_vat"] is True
    assert lessons.total(made[0]["line_items"]) == 75000 and len(made[0]["line_items"]) == 2
    run(lessons.confirm(TID, a["id"], starts_at=_at(2), invoice=True))
    assert len(made) == 1                                                                     # never a second invoice
    b = _ask(phone="0835550000")
    run(lessons.confirm(TID, b["id"], starts_at=_at(), price_cents=60000, invoice=True))
    assert made[1]["line_items"] == [{"description": "Surf lesson, group, 2 people", "quantity": 1, "unit_price_cents": 60000}]


def test_declining_needs_a_reason_and_only_a_waiting_request(world):
    r = _ask()
    with pytest.raises(lessons.LessonError, match="Say why"):
        run(lessons.decline(TID, r["id"], " "))
    done = run(lessons.decline(TID, r["id"], "The swell is too big for first timers on Saturday."))
    assert done["status"] == "declined" and "too big for first timers" in world.told[-1]
    with pytest.raises(lessons.LessonError):
        run(lessons.decline(TID, r["id"], "again"))
    with pytest.raises(lessons.LessonError):
        lessons.set_status(TID, r["id"], "completed")                                         # never confirmed


def test_a_customer_sees_and_cancels_only_their_own(world):
    mine, theirs = _ask(), _ask(phone="083 555 0000", name="Sipho Dlamini")
    run(lessons.confirm(TID, mine["id"], starts_at=_at()))
    assert [r["id"] for r in lessons.upcoming_for(TID, "+27 82 123 4567")] == [mine["id"]]
    assert lessons.known_name(TID, "0821234567") == "Thandi Mokoena" and lessons.known_name(TID, "0711111111") == ""
    assert run(lessons.cancel_own(TID, "0821234567", theirs["id"])) is None
    world.team.clear()
    assert run(lessons.cancel_own(TID, "0821234567", mine["id"]))["status"] == "cancelled"
    assert "cancelled" in world.team[0][1]                                                    # it was confirmed: the team hears
    assert run(lessons.cancel_own(TID, "0835550000", theirs["id"]))["status"] == "cancelled" and len(world.team) == 1
    assert lessons.upcoming_for(TID, "0821234567") == []


def test_the_reminder_goes_once_and_only_in_the_daytime(world):
    r = _ask()
    run(lessons.confirm(TID, r["id"], starts_at=_at(1, 9)))
    world.told.clear()
    d = (datetime.now(lessons.SAST) + timedelta(days=1)).date()
    evening = datetime(d.year, d.month, d.day, 17, 0, tzinfo=lessons.SAST) - timedelta(days=1)
    night = evening.replace(hour=23)
    assert run(lessons.send_reminders(night.astimezone(timezone.utc))) == 0
    assert run(lessons.send_reminders(evening.astimezone(timezone.utc))) == 1 and "A reminder" in world.told[0]
    assert run(lessons.send_reminders(evening.astimezone(timezone.utc))) == 0


# ── the conditions check ──────────────────────────────────────────────────────────────────────
MARINE = {"hourly": {"time": ["2026-11-25T08:00", "2026-11-25T09:00"], "wave_height": [1.38, 2.9], "wave_period": [9.55, 14.0],
                     "wave_direction": [212, 225], "swell_wave_height": [1.38, None]}}
WEATHER = {"hourly": {"time": ["2026-11-25T08:00", "2026-11-25T09:00"], "wind_speed_10m": [4.6, 22.0],
                      "wind_gusts_10m": [11.3, 31.0], "wind_direction_10m": [155, 135], "precipitation_probability": [0, 40]}}


def test_the_forecast_is_read_as_given_and_rated_in_code():
    hours = conditions.merge(MARINE, WEATHER)
    assert hours[8]["wave_m"] == 1.38 and hours[8]["wind_kn"] == 4.6 and "swell_m" not in hours[9]
    assert conditions.describe(hours[8]) == "swell 1.4 m at 10 s from SSW, wind 5 kn SSE gusting 11"
    assert conditions.describe(hours[9]).endswith("40% chance of rain") and conditions.describe({}) == "no forecast for this hour"
    assert conditions.rate("surf", "first_timer", hours[8]) == {"rating": "good", "reasons": []}
    big = conditions.rate("surf", "beginner", hours[9])
    assert big["rating"] == "poor" and "a big swell for learners" in big["reasons"] and "very strong wind" in big["reasons"]
    assert conditions.rate("surf", "advanced", hours[9])["rating"] == "marginal"
    assert conditions.rate("kite", "", hours[8]) == {"rating": "poor", "reasons": ["not enough wind"]}
    assert conditions.rate("kite", "", hours[9])["rating"] == "marginal"
    assert conditions.rate("sup", "", {"wind_kn": 16})["rating"] == "poor"
    assert conditions.rate("surf", "", {}) == {"rating": "marginal", "reasons": ["no forecast for this hour"]}
    assert conditions.compass(0) == "N" and conditions.compass(225) == "SW" and conditions.compass(None) == ""
    assert conditions.merge({}, {"hourly": {"time": ["bad"], "wind_speed_10m": [3]}}) == {}


def test_the_team_hears_tomorrows_conditions_once(world, monkeypatch):
    seen = []

    async def forecast(lat, lng, day):
        seen.append((lat, lng, day.isoformat()))
        return {9: {"wave_m": 2.9, "period_s": 14.0, "wave_from": 225, "wind_kn": 8.0, "gust_kn": 12.0, "wind_from": 135}}

    monkeypatch.setattr(conditions, "forecast", forecast)
    r = _ask()
    run(lessons.confirm(TID, r["id"], starts_at=_at(1, 9), coach_id="m1"))
    world.team.clear()
    d = datetime.now(lessons.SAST).date()
    morning = datetime(d.year, d.month, d.day, 10, 0, tzinfo=lessons.SAST).astimezone(timezone.utc)
    evening = datetime(d.year, d.month, d.day, 17, 0, tzinfo=lessons.SAST).astimezone(timezone.utc)
    assert run(lessons.check_conditions(morning)) == 0 and seen == []
    assert run(lessons.check_conditions(evening)) == 1
    assert seen == [(-33.794, 18.459, _day(1))]                                               # only the place and the day
    event, message = world.team[0]
    assert event == "lesson_conditions" and "Big Bay" in message and "🔴 09:00 Thandi Mokoena" in message
    assert "swell 2.9 m at 14 s from SW" in message and "a big swell for learners" in message and "(Shane)" in message
    assert world.db.rows["commerce_lesson_requests"][0]["conditions"]["rating"] == "poor"
    assert run(lessons.check_conditions(evening)) == 0 and len(world.team) == 1


# ── the routes ────────────────────────────────────────────────────────────────────────────────
def test_the_routes_quote_list_and_refuse(world, monkeypatch):
    out = run(lessons_api.quote(TID, {"kind": "lesson", "item_id": "surf", "format": "group", "people": 4, "package": True}))
    assert out["quote_cents"] == 400000
    assert run(lessons_api.quote(TID, {"kind": "hire", "item_id": "wetsuit", "duration": "1d", "people": 3}))["quote_cents"] == 30000
    made = run(lessons_api.add_request(TID, {"kind": "hire", "item_id": "surfboard", "duration": "4h", "people": 2,
                                             "wanted_date": _day(), "name": "Walk-in customer"}))["request"]
    assert made["channel"] == "dashboard" and made["quote_cents"] == 50000 and world.team == []
    listed = run(lessons_api.list_requests(TID))
    assert listed["waiting"] == 1 and listed["coaches"][0]["name"] == "Shane Daniels" and listed["enabled"] is True
    with pytest.raises(HTTPException) as e:
        run(lessons_api.list_requests(TID, status="lost"))
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        run(lessons_api.set_status(TID, made["id"], {"status": "completed"}))
    assert e.value.status_code == 409
    assert run(lessons_api.conditions_for("empty")) == {"spot": None, "hours": []}

    async def down(lat, lng, day):
        raise RuntimeError("no forecast")

    monkeypatch.setattr(conditions, "forecast", down)
    assert run(lessons_api.conditions_for(TID))["unavailable"] is True


def test_the_business_type_switches_lessons_on():
    assert "lessons" in tenants.MODULES and "lessons" in tenants.BUSINESS_TYPES["watersports"]["modules"]
    assert all(m in tenants.MODULES for m in tenants.BUSINESS_TYPES["watersports"]["modules"])


# ── the chat ──────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def chat(world, monkeypatch):
    from vula.api import whatsapp as wa
    sent, asked = [], []

    async def resolve(tid):
        return {"phone_id": "1", "token": "t"}

    async def send_list(creds, number, header, body, footer, button, sections):
        sent.append(("list", body, [r for sec in sections for r in sec["rows"]]))
        return True

    async def buttons(creds, number, body, btns, image_url=None):
        sent.append(("buttons", body, btns))
        return True

    async def reply(phone, body, tenant_id=None, **k):
        sent.append(("text", body))

    async def profile(tid, phone):
        return None

    monkeypatch.setattr(wa, "_resolve_wa", resolve)
    monkeypatch.setattr(wa, "_send_wa_list", send_list)
    monkeypatch.setattr(wa, "_send_wa_buttons", buttons)
    monkeypatch.setattr(wa, "_send_reply", reply)
    monkeypatch.setattr(service, "get_customer_profile", profile)
    monkeypatch.setattr("vula.open_questions.ask", lambda t, p, kind, ref, prompt="": asked.append((kind, ref)) or "q1")
    monkeypatch.setattr("vula.open_questions.close", lambda qid, answer="", status="answered": None)
    return SimpleNamespace(sent=sent, asked=asked, world=world)


def test_what_the_chat_takes_as_a_lesson_or_hire_request():
    for yes in ("Hi, do you do surf lessons?", "I want to learn to surf", "can I hire a wetsuit", "board rental?",
                "cancel my lesson please", "how much is a lesson"):
        assert lesson_chat.is_request(yes), yes
    for no in ("is the red board in stock", "I need less wax than last time", "what time do you open", "lesson " * 40):
        assert not lesson_chat.is_request(no), no


def test_a_lesson_asked_for_in_the_chat_step_by_step(chat):
    phone, day = "27821234567", _day(2)
    assert run(lesson_chat.start(phone, TID, "Hi do you do surf lessons")) is True
    assert [r["id"] for r in chat.sent[-1][2]] == ["lsn_l_surf", "lsn_l_kitelesson", "lsn_h"]
    run(lesson_chat.handle_tap(phone, TID, "lsn_l_surf"))
    assert [r["id"] for r in chat.sent[-1][2]] == [f"lsn_v_surf_{c}" for c in ("ft", "bg", "im", "ad")]
    run(lesson_chat.handle_tap(phone, TID, "lsn_v_surf_ft"))
    assert "from R300 per person" in chat.sent[-1][1] and "one on one, R650" in chat.sent[-1][1]
    run(lesson_chat.handle_tap(phone, TID, "lsn_f_surf_ft_g"))
    assert len(chat.sent[-1][2]) == 8
    run(lesson_chat.handle_tap(phone, TID, "lsn_n_surf_ft_g_3"))
    assert "*One lesson*: R1,050 in total." in chat.sent[-1][1] and "*4 lessons*: R3,600 in total" in chat.sent[-1][1]
    run(lesson_chat.handle_tap(phone, TID, "lsn_s_surf_ft_g_3_1"))
    assert "wetsuit? R50 per person per lesson" in chat.sent[-1][1]
    assert [b["title"] for b in chat.sent[-1][2]] == ["Yes, for everyone", "No thanks"]
    assert all(len(b["title"]) <= 20 for b in chat.sent[-1][2])
    run(lesson_chat.handle_tap(phone, TID, "lsn_x_surf_ft_g_3_1_1"))
    rows = chat.sent[-1][2]
    assert len(rows) == 10 and rows[0]["title"] == "Today" and rows[2]["id"] == f"lsn_d_l_surf_ft_g_3_1_1_{day}"
    run(lesson_chat.handle_tap(phone, TID, rows[2]["id"]))
    assert [r["id"][-1] for r in chat.sent[-1][2]] == ["m", "d", "a", "x"]
    run(lesson_chat.handle_tap(phone, TID, f"lsn_p_l_surf_ft_g_3_1_1_{day}_m"))
    assert chat.asked == [("lesson_name", f"l_surf_ft_g_3_1_1_{day}_m")]                      # a new customer
    q = {"id": "q1", "ref_id": chat.asked[0][1]}
    assert run(lesson_chat.take_name(phone, TID, "how much is it?", q)) is False
    assert run(lesson_chat.take_name(phone, TID, "Thandi Mokoena", q)) is True
    row = chat.world.db.rows["commerce_lesson_requests"][0]
    assert (row["status"], row["party_size"], row["format"], row["level"], row["wanted_date"], row["wanted_part"]) == \
        ("requested", 3, "group", "first_timer", day, "morning")
    assert row["quote_cents"] == 3 * 35000 + 3 * 5000 and row["extras"][0]["quantity"] == 3
    said = chat.sent[-1][1]
    assert "*Total R1,200*" in said and "This isn't booked yet" in said and row["ref"] in said
    assert "3 × Surf lesson (group), per person: R1,050" in said and "3 × Wetsuit: R150" in said
    chat.asked.clear()
    run(lesson_chat.handle_tap(phone, TID, f"lsn_p_l_surf_bg_p_1_k_0_{day}_x"))                # next time: not asked again
    assert chat.asked == [] and chat.world.db.rows["commerce_lesson_requests"][1]["quote_cents"] == 240000


def test_gear_asked_for_in_the_chat(chat):
    phone, day = "27835550000", _day(1)
    assert run(lesson_chat.start(phone, TID, "can I hire a surfboard")) is True
    assert [r["id"] for r in chat.sent[-1][2]] == ["lsn_g_surfboard", "lsn_g_wetsuit"]
    run(lesson_chat.handle_tap(phone, TID, "lsn_g_wetsuit"))
    assert [(r["title"], r["description"]) for r in chat.sent[-1][2]] == [("2 hours", "R70 each"), ("1 day", "R100 each")]
    run(lesson_chat.handle_tap(phone, TID, "lsn_t_wetsuit_1d"))
    run(lesson_chat.handle_tap(phone, TID, "lsn_q_wetsuit_1d_2"))
    assert chat.sent[-1][2][1]["id"] == f"lsn_d_h_wetsuit_1d_2_{day}"
    run(lesson_chat.handle_tap(phone, TID, f"lsn_d_h_wetsuit_1d_2_{day}"))
    run(lesson_chat.handle_tap(phone, TID, f"lsn_p_h_wetsuit_1d_2_{day}_a"))
    run(lesson_chat.take_name(phone, TID, "Sipho Dlamini", {"id": "q1", "ref_id": chat.asked[0][1]}))
    row = chat.world.db.rows["commerce_lesson_requests"][0]
    assert (row["kind"], row["duration"], row["party_size"], row["quote_cents"]) == ("hire", "1d", 2, 20000)
    assert "2 × Wetsuit for 1 day" in chat.sent[-1][1]


def test_prices_own_requests_and_what_the_chat_leaves_alone(chat):
    phone = "27821234567"
    assert run(lesson_chat.start(phone, TID, "how much is a surf lesson?")) is True
    kind, body, btns = chat.sent[-1]
    assert kind == "buttons" and "Private 1-on-1: R650" in body and [b["id"] for b in btns] == ["lsn_start", "lsn_h"]
    assert run(lesson_chat.start(phone, TID, "my lesson")) is True and "nothing booked" in chat.sent[-1][1]
    r = _ask()
    run(lesson_chat.start(phone, TID, "cancel my lesson"))
    assert chat.sent[-1][2][0]["id"] == f"lsn_c_{r['id']}" and "waiting to be confirmed" in chat.sent[-1][1]
    run(lesson_chat.handle_tap("27835550000", TID, f"lsn_c_{r['id']}"))                        # someone else's number
    assert "couldn't find that" in chat.sent[-1][1]
    run(lesson_chat.handle_tap(phone, TID, f"lsn_c_{r['id']}"))
    assert chat.world.db.rows["commerce_lesson_requests"][0]["status"] == "cancelled"
    for stale in ("lsn_p_l_surf_ft_g_3_1_1_notadate_m", "lsn_n_surf_ft_g_99", "lsn_zz", "lsn_p_l_surf_zz_g_3_1_1_2026-12-01_m"):
        assert run(lesson_chat.handle_tap(phone, TID, stale)) is False, stale
    before = len(chat.world.db.rows["commerce_lesson_requests"])
    run(lesson_chat.handle_tap(phone, TID, f"lsn_p_l_gone_ft_g_2_1_0_{_day()}_m"))             # a lesson no longer offered
    assert len(chat.world.db.rows["commerce_lesson_requests"]) == before and "Reply *lesson*" in chat.sent[-1][1]
    assert run(lesson_chat.start(phone, "other", "surf lesson please")) is False               # requests off there
    assert run(lesson_chat.handle_tap(phone, "other", "lsn_start")) is False
    lessons.save_settings(TID, {"hire": []})
    assert run(lesson_chat.start(phone, TID, "do you rent bikes")) is False                    # no hire: the assistant answers


def test_a_partner_is_named_only_when_it_is_a_known_one():
    assert tenants.partner_of({"features": {"partner": "flowcrew", "trade_costing": True}}) == {"id": "flowcrew", "name": "FlowCrew"}
    for cfg in ({"features": {"partner": "someone"}}, {"features": {"partner": True}}, {"features": None}, {}, None):
        assert tenants.partner_of(cfg) is None
    assert tenants._public({"tenant_id": None, "features": {"partner": "flowcrew"}})["partner"]["name"] == "FlowCrew"


def test_a_partners_business_wears_its_colours_until_it_sets_its_own(monkeypatch):
    from vula.api import commerce
    kit = {}

    async def invoice_settings(tid):
        return kit

    monkeypatch.setattr(service, "get_invoice_settings", invoice_settings)
    monkeypatch.setattr(tenants, "get_config", lambda tid, **k: {"tenant_id": tid, "display_name": "Big Bay Surf",
                                                                 "features": {"partner": "flowcrew"} if tid == TID else {}})
    b = run(commerce.public_brand(TID))
    assert (b["accent_color"], b["secondary_color"], b["ink_color"]) == ("#2196F3", "#7ED321", "#1E2A39")
    assert b["partner"] == {"id": "flowcrew", "name": "FlowCrew"} and b["name"] == "Big Bay Surf" and b["logo_url"] is None
    kit.update(accent_color="#AA3300", trading_as="Big Bay Surf School")                       # its own always wins
    b = run(commerce.public_brand(TID))
    assert b["accent_color"] == "#AA3300" and b["secondary_color"] == "#7ED321" and b["name"] == "Big Bay Surf School"
    kit.clear()
    plain = run(commerce.public_brand("other"))
    assert plain["accent_color"] is None and plain["partner"] is None                          # anyone else: Vula's defaults
    assert tenants.partner_brand(None) == {} and tenants.partner_brand("other") == {}
