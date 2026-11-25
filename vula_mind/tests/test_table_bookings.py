"""Table bookings and specials (vula/commerce/tables.py, 24 Nov 2026): only times the restaurant
offers and has room for; the room's limit is kept by the database call; a customer cancels only
their own; specials show only on the days they run; the chat books from exact taps."""
import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tests.fake_supabase import FakeSupabase
from vula.api import tables as tables_api, tenants
from vula.commerce import service, table_chat, tables, web_basket

TID = "the_yard"


class DB(FakeSupabase):
    def _rpc_book_table(self, p_tenant_id, p_ref, p_starts_at, p_ends_at, p_party, p_max_covers, p_name, p_phone,
                        p_notes, p_channel, p_booked_by):
        s, e = datetime.fromisoformat(p_starts_at), datetime.fromisoformat(p_ends_at)
        taken = sum(b["party_size"] for b in self.rows["commerce_table_bookings"]
                    if b["tenant_id"] == p_tenant_id and b["status"] in ("confirmed", "seated")
                    and datetime.fromisoformat(b["starts_at"]) < e and datetime.fromisoformat(b["ends_at"]) > s)
        if taken + p_party > p_max_covers:
            return None
        row = {"id": str(uuid.uuid4()), "tenant_id": p_tenant_id, "ref": p_ref, "starts_at": p_starts_at, "ends_at": p_ends_at,
               "party_size": p_party, "customer_name": p_name, "customer_phone": p_phone, "notes": p_notes,
               "channel": p_channel, "booked_by": p_booked_by, "status": "confirmed", "reminded_at": None,
               "created_at": datetime.now(timezone.utc).isoformat()}
        self.rows["commerce_table_bookings"].append(row)
        return row["id"]


def run(c):
    return asyncio.run(c)


EVERY_DAY = list(range(7))
SITTINGS = [{"name": "Lunch", "days": EVERY_DAY, "start": "12:00", "end": "14:00"},
            {"name": "Dinner", "days": EVERY_DAY, "start": "18:00", "end": "21:00"}]


@pytest.fixture
def world(monkeypatch):
    db = DB({"commerce_table_settings": [], "commerce_table_bookings": [], "commerce_specials": []})
    monkeypatch.setattr(service, "_client", lambda: db)
    monkeypatch.setattr(tenants, "is_active", lambda tid: tid != "paused")
    monkeypatch.setattr(tenants, "get_config", lambda tid, **k: {"tenant_id": tid} if tid in (TID, "other") else {})
    monkeypatch.setattr(web_basket, "public_base", lambda tid: f"https://{tid.replace('_', '-')}.shop.vula-ai.com")
    team = []

    async def notify_team(tenant_id, event, message, **kw):
        team.append((event, message))

    monkeypatch.setattr("vula.integrations.notify.notify_team", notify_team)
    tables.save_settings(TID, {"enabled": True, "sittings": SITTINGS, "max_covers": 10, "max_party": 8,
                               "seating_minutes": 120, "slot_minutes": 60, "lead_minutes": 0})
    return SimpleNamespace(db=db, team=team)


def _tomorrow():
    return (datetime.now(tables.SAST) + timedelta(days=1)).date()


def _slot(hhmm, day=None):
    return tables._at(day or _tomorrow(), hhmm).astimezone(timezone.utc).isoformat()


def _book(hhmm="18:00", party=4, phone="082 123 4567", name="Thandi Mokoena", **kw):
    return run(tables.book(TID, starts_at=_slot(hhmm), party=party, name=name, phone=phone, **kw))


# ── settings ──────────────────────────────────────────────────────────────────────────────────
def test_bookings_are_off_until_the_restaurant_sets_its_sittings(world):
    assert tables.settings("other")["enabled"] is False and tables.open_days("other") == []
    with pytest.raises(tables.TableError, match="at least one sitting"):
        tables.save_settings("other", {"enabled": True})
    for bad in ({"sittings": [{"name": "Dinner", "days": [0], "start": "22:00", "end": "18:00"}]},
                {"sittings": [{"name": "", "days": [0], "start": "18:00", "end": "22:00"}]},
                {"sittings": [{"name": "Dinner", "days": [9], "start": "18:00", "end": "22:00"}]},
                {"sittings": [{"name": "Dinner", "days": [], "start": "18:00", "end": "22:00"}]},
                {"max_covers": 0}, {"slot_minutes": 20}, {"enabled": "yes"}, {"seating_minutes": 5}):
        with pytest.raises(tables.TableError):
            tables.save_settings(TID, bad)
    assert tables.settings(TID)["max_covers"] == 10                              # nothing changed


def test_the_times_offered_are_the_sittings_own_with_room_left(world):
    got = tables.slots(TID, _tomorrow(), 4)
    assert [(s["sitting"], s["time"]) for s in got] == [("Lunch", "12:00"), ("Lunch", "13:00"),
                                                        ("Dinner", "18:00"), ("Dinner", "19:00"), ("Dinner", "20:00")]
    assert all(s["free"] == 10 for s in got)
    _book("18:00", 8)                                                           # sits 18:00–20:00
    after = {s["time"]: s["free"] for s in tables.slots(TID, _tomorrow(), 2)}
    assert after == {"12:00": 10, "13:00": 10, "18:00": 2, "19:00": 2, "20:00": 10}
    assert [s["time"] for s in tables.slots(TID, _tomorrow(), 4)] == ["12:00", "13:00", "20:00"]
    with pytest.raises(tables.TableError) as e:
        tables.slots(TID, _tomorrow(), 9)                                        # bigger than books online
    assert e.value.status == 409 and "message us" in str(e.value)
    assert tables.slots(TID, date.today() - timedelta(days=1), 2) == []
    assert tables.slots(TID, date.today() + timedelta(days=400), 2) == []


def test_closed_days_and_times_too_soon_are_not_offered(world):
    tomorrow = _tomorrow()
    tables.save_settings(TID, {"sittings": [{"name": "Dinner", "days": [(tomorrow.weekday() + 1) % 7], "start": "18:00", "end": "21:00"}]})
    assert tables.slots(TID, tomorrow, 2) == []
    assert tables.open_days(TID, 3)[0] == tomorrow + timedelta(days=1)
    tables.save_settings(TID, {"sittings": SITTINGS, "lead_minutes": 60 * 24 * 3})
    assert tables.slots(TID, tomorrow, 2) == []                                  # must book three days ahead


# ── booking ───────────────────────────────────────────────────────────────────────────────────
def test_a_booking_holds_its_seats_and_the_team_hears(world):
    b = _book("19:00", 4, notes="  Birthday,\n high chair ")
    assert b["ref"].startswith("T-") and len(b["ref"]) == 7 and b["party_size"] == 4 and b["status"] == "confirmed"
    assert b["customer_phone"] == "27821234567" and b["notes"] == "Birthday, high chair" and " at 19:00" in b["when"]
    row = world.db.rows["commerce_table_bookings"][0]
    assert datetime.fromisoformat(row["ends_at"]) - datetime.fromisoformat(row["starts_at"]) == timedelta(hours=2)
    assert world.team[0][0] == "table_booking" and "4 people" in world.team[0][1] and b["ref"] in world.team[0][1]


def test_the_room_never_takes_more_than_it_seats(world):
    _book("18:00", 6)
    _book("19:00", 4, phone="083 000 1111")                                      # 10 seated from 19:00
    with pytest.raises(tables.TableError) as e:
        _book("19:00", 1, phone="084 000 2222")
    assert e.value.status == 409
    # the last check is the database's own: a time that looked free a moment ago is still refused
    orig = tables.slots
    try:
        tables.slots = lambda *a, **k: [{"starts_at": _slot("19:00"), "time": "19:00", "sitting": "Dinner", "free": 9}]
        with pytest.raises(tables.TableError, match="just filled up"):
            _book("19:00", 1, phone="084 000 2222")
    finally:
        tables.slots = orig
    assert len(world.db.rows["commerce_table_bookings"]) == 2
    tables.set_status(TID, world.db.rows["commerce_table_bookings"][1]["id"], "cancelled")
    assert _book("19:00", 4, phone="084 000 2222")["party_size"] == 4            # a cancelled table is free again


@pytest.mark.parametrize("kw, says", [
    ({"hhmm": "18:30"}, "isn't available"), ({"hhmm": "03:00"}, "isn't available"), ({"party": 0}, "whole number"),
    ({"party": 2.5}, "whole number"), ({"name": "T"}, "name"), ({"phone": "12"}, "cellphone"),
])
def test_refused_bookings(world, kw, says):
    with pytest.raises(tables.TableError, match=says):
        _book(**kw)
    assert world.db.rows["commerce_table_bookings"] == []


def test_not_taking_bookings(world):
    with pytest.raises(tables.TableError) as e:
        run(tables.book("other", starts_at=_slot("18:00"), party=2, name="Thandi Mokoena", phone="0821234567"))
    assert e.value.status == 409
    tables.save_settings(TID, {"enabled": False})
    with pytest.raises(tables.TableError):
        _book()


def test_the_team_books_by_hand_into_a_time_that_is_too_soon_but_not_past_the_rooms_limit(world):
    tables.save_settings(TID, {"lead_minutes": 60 * 24 * 3})
    b = run(tables.book(TID, starts_at=_slot("18:00"), party=6, name="Walk in", phone="", channel="dashboard",
                        booked_by="owner@x.co.za", tell_team=False))
    assert b["customer_phone"] == "" and world.team == []
    with pytest.raises(tables.TableError, match="filled up"):
        run(tables.book(TID, starts_at=_slot("18:00"), party=6, name="Another", phone="", channel="dashboard"))
    sheet = tables.day_sheet(TID, _tomorrow().isoformat())
    assert sheet["covers"] == 6 and sheet["max_covers"] == 10 and len(sheet["bookings"]) == 1
    assert [t["time"] for t in sheet["times"]] == ["12:00", "13:00", "18:00", "19:00", "20:00"]
    done = tables.set_status(TID, b["id"], "seated")
    assert done["status"] == "seated"
    with pytest.raises(tables.TableError):
        tables.set_status("other", b["id"], "cancelled")                          # not their booking
    with pytest.raises(tables.TableError):
        tables.set_status(TID, b["id"], "eaten")


def test_a_customer_sees_and_cancels_only_their_own(world):
    mine = _book("18:00", 2)
    theirs = _book("19:00", 2, phone="083 000 1111", name="Pieter Botha")
    assert [b["ref"] for b in tables.upcoming_for(TID, "+27 82 123 4567")] == [mine["ref"]]
    assert tables.cancel_own(TID, "0821234567", theirs["id"]) is None
    assert tables.cancel_own(TID, "0821234567", mine["id"])["status"] == "cancelled"
    assert tables.upcoming_for(TID, "0821234567") == [] and tables.upcoming_for(TID, "12") == []


# ── specials ──────────────────────────────────────────────────────────────────────────────────
def test_specials_show_only_on_the_days_they_run(world):
    today = datetime.now(tables.SAST).date()
    every = tables.save_special(TID, {"title": "Soup of the day", "price_cents": 6500, "description": "With bread."})
    tables.save_special(TID, {"title": "Other day", "days": [(today.weekday() + 1) % 7], "price_cents": 9900})
    tables.save_special(TID, {"title": "Ended", "ends_on": (today - timedelta(days=1)).isoformat()})
    tables.save_special(TID, {"title": "Not yet", "starts_on": (today + timedelta(days=2)).isoformat()})
    tables.save_special(TID, {"title": "Switched off", "active": False})
    tables.save_special(TID, {"title": "Market price catch"})
    assert [s["title"] for s in tables.todays_specials(TID)] == ["Soup of the day", "Market price catch"]
    text = tables.specials_text(TID, tables.todays_specials(TID))
    assert "*Soup of the day* — R65" in text and "With bread." in text and "*Market price catch*" in text
    changed = tables.save_special(TID, {"id": every["id"], "title": "Soup of the day", "price_cents": 7050})
    assert changed["price_cents"] == 7050 and tables.rand(7050) == "R70.50"
    for bad in ({"title": ""}, {"title": "x", "price_cents": 65.5}, {"title": "x", "image_url": "http://plain/x.jpg"},
                {"title": "x", "days": [8]}, {"title": "x", "starts_on": "2026-12-10", "ends_on": "2026-12-01"}):
        with pytest.raises(tables.TableError):
            tables.save_special(TID, bad)
    with pytest.raises(tables.TableError):
        tables.save_special("other", {"id": every["id"], "title": "Hijacked"})       # not their special
    tables.delete_special(TID, every["id"])
    assert [s["title"] for s in tables.todays_specials(TID)] == ["Market price catch"]


# ── the public page ───────────────────────────────────────────────────────────────────────────
def test_the_booking_page(world, monkeypatch):
    async def inv(tid):
        return {"trading_as": "The Yard <b>", "accent_color": "8a3b12"}
    monkeypatch.setattr(service, "get_invoice_settings", inv)
    tables.save_special(TID, {"title": "Soup <script>x</script>", "price_cents": 6500})
    page = run(tables_api.booking_page(TID)).body.decode()
    assert "The Yard &lt;b&gt;" in page and "<script>x</script>" not in page and "Soup &lt;script&gt;" in page
    assert 'id="bk"' in page and "Today’s specials" in page and "R65" in page and "#8a3b12" in page and "{{" not in page
    for nobody in ("nosuch", "paused", "other"):                                   # unknown, paused, nothing to show
        with pytest.raises(HTTPException) as e:
            run(tables_api.booking_page(nobody))
        assert e.value.status_code == 404
    req = SimpleNamespace(headers={"x-forwarded-for": "1.2.3.4"}, client=None)
    tables_api._RECENT.clear()
    out = run(tables_api.booking_slots(TID, _tomorrow().isoformat(), 2, req))
    assert set(out["slots"][0]) == {"sitting", "time", "starts_at"}                 # how full it is isn't published
    made = run(tables_api.booking_make(TID, {"starts_at": _slot("18:00"), "party": 2, "name": "Thandi Mokoena",
                                             "phone": "082 123 4567"}, req))
    assert set(made) == {"ref", "when", "party_size"}
    for hhmm in ("19:00", "20:00"):
        run(tables_api.booking_make(TID, {"starts_at": _slot(hhmm), "party": 1, "name": "Thandi Mokoena", "phone": "0821234567"}, req))
    with pytest.raises(HTTPException) as e:
        run(tables_api.booking_make(TID, {"starts_at": _slot("12:00"), "party": 1, "name": "Thandi Mokoena", "phone": "0821234567"}, req))
    assert e.value.status_code == 409 and "three bookings" in e.value.detail


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


def test_what_the_chat_takes_as_a_booking_or_specials():
    for yes in ("book a table", "Can I book a table for 4 tomorrow", "reservation for dinner please", "table for 2", "Book", "bespreek 'n tafel"):
        assert table_chat.is_booking_request(yes), yes
    for no in ("what time do you open", "do you have a kids menu", "I booked a courier", ""):
        assert not table_chat.is_booking_request(no), no
    assert table_chat.is_specials_request("any specials today?") and not table_chat.is_specials_request("what's special about it " * 9)
    assert table_chat.is_own_booking_request("please cancel my booking") and table_chat.is_own_booking_request("my reservation")


def test_booking_in_the_chat_step_by_step(chat):
    phone = "27821234567"
    assert run(table_chat.start(phone, TID, "Hi can I book a table")) is True
    rows = chat.sent[-1][2]
    assert rows[0]["title"] == "Today" and rows[1]["title"] == "Tomorrow" and rows[1]["id"] == f"tbl_d_{_tomorrow().isoformat()}"
    day = _tomorrow().isoformat()
    run(table_chat.handle_tap(phone, TID, f"tbl_d_{day}"))
    assert [r["id"] for r in chat.sent[-1][2]] == [f"tbl_p_{day}_{n}" for n in range(1, 9)]       # up to 8 book online
    run(table_chat.handle_tap(phone, TID, f"tbl_p_{day}_4"))
    assert [r["title"] for r in chat.sent[-1][2]] == ["12:00", "13:00", "18:00", "19:00", "20:00"]
    run(table_chat.handle_tap(phone, TID, f"tbl_t_{day}_4_1900"))
    assert chat.asked == [("table_name", f"{tables._at(_tomorrow(), '19:00').isoformat()}|4")]     # a new customer
    q = {"id": "q1", "ref_id": chat.asked[0][1]}
    assert run(table_chat.take_name(phone, TID, "what's on the menu?", q)) is False
    assert run(table_chat.take_name(phone, TID, "Thandi Mokoena", q)) is True
    b = chat.world.db.rows["commerce_table_bookings"][0]
    assert b["party_size"] == 4 and b["customer_name"] == "Thandi Mokoena" and b["channel"] == "whatsapp"
    assert "Booked: a table for 4" in chat.sent[-1][1] and b["ref"] in chat.sent[-1][1] and "at 19:00" in chat.sent[-1][1]
    chat.asked.clear()
    run(table_chat.handle_tap(phone, TID, f"tbl_t_{day}_2_1200"))                 # next time: not asked again
    assert chat.asked == [] and len(chat.world.db.rows["commerce_table_bookings"]) == 2


def test_the_chat_never_books_a_time_that_is_not_offered(chat):
    day = _tomorrow().isoformat()
    _book("19:00", 8, phone="083 000 1111")
    run(table_chat.handle_tap("27821234567", TID, f"tbl_p_{day}_4"))
    assert "19:00" not in [r["title"] for r in chat.sent[-1][2]]
    chat.world.db.rows["commerce_table_bookings"].append({**chat.world.db.rows["commerce_table_bookings"][0], "id": "x",
                                                          "customer_phone": "27821234567", "status": "completed"})
    run(table_chat.handle_tap("27821234567", TID, f"tbl_t_{day}_4_1900"))          # a stale tap on a full time
    assert len([b for b in chat.world.db.rows["commerce_table_bookings"] if b["status"] == "confirmed"]) == 1
    assert "pick another time" in chat.sent[-1][1]
    assert run(table_chat.handle_tap("27821234567", TID, f"tbl_t_{day}_4_99")) is False
    assert run(table_chat.handle_tap("27821234567", "other", f"tbl_d_{day}")) is False       # bookings off there


def test_specials_and_own_bookings_in_the_chat(chat):
    assert run(table_chat.start("27821234567", TID, "any specials?")) is False     # none today: left to the assistant
    tables.save_special(TID, {"title": "Soup of the day", "price_cents": 6500})
    assert run(table_chat.start("27821234567", TID, "any specials?")) is True
    kind, body, btns = chat.sent[-1]
    assert kind == "buttons" and "*Soup of the day* — R65" in body and btns == [{"id": "tbl_start", "title": "Book a table"}]
    assert run(table_chat.start("27821234567", "other", "book a table")) is False
    mine = _book("18:00", 2)
    run(table_chat.start("27821234567", TID, "cancel my booking"))
    assert chat.sent[-1][2][0]["id"] == f"tbl_c_{mine['id']}"
    run(table_chat.handle_tap("27839999999", TID, f"tbl_c_{mine['id']}"))           # someone else's number
    assert "couldn't find" in chat.sent[-1][1] and tables.upcoming_for(TID, "0821234567")
    run(table_chat.handle_tap("27821234567", TID, f"tbl_c_{mine['id']}"))
    assert "is cancelled" in chat.sent[-1][1] and tables.upcoming_for(TID, "0821234567") == []


def test_the_reminder_goes_once(world, monkeypatch):
    sent = []

    async def send_template(tenant_id, phone, name, params, **k):
        sent.append((phone, params))
        return True

    monkeypatch.setattr("vula.api.whatsapp.send_template", send_template)
    monkeypatch.setattr("vula.commerce.job_config.template_name", lambda tid, key: "order_update")
    monkeypatch.setattr(tenants, "display_name", lambda tid: "The Yard")
    b = _book("19:00", 4)
    start = datetime.fromisoformat(world.db.rows["commerce_table_bookings"][0]["starts_at"])
    assert run(tables.send_reminders(start - timedelta(hours=10))) == 0             # too early
    assert run(tables.send_reminders(start - timedelta(hours=3))) == 1
    assert sent[0][0] == "27821234567" and sent[0][1][1] == b["ref"] and "table for 4 at The Yard" in sent[0][1][2]
    assert run(tables.send_reminders(start - timedelta(hours=3))) == 0 and len(sent) == 1
