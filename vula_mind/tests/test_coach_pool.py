"""A school asks a coach from another school (vula/commerce/coach_pool.py, migration 310): the
coach's own choice, who is offered, first to accept wins, what each side may see, and the fee."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from tests.fake_supabase import FakeSupabase
from tests.test_lessons import HIRE, KITE, SPOT, SURF, TID
from vula.api import tenants
from vula.commerce import coach_pool as pool, lesson_calendar as cal, lessons, service
from vula.tap.shifts import ShiftDesk

HOME, FAR, OUTSIDE = "kite_lab", "durban_surf", "corner_cafe"
TEAMS = {TID: [{"id": "m1", "name": "Shane Daniels", "role": "owner", "whatsapp": "27820000001"}],
         HOME: [{"id": "k1", "name": "Ayanda Zulu", "role": "staff", "whatsapp": "27820000011"},
                {"id": "k2", "name": "Ben Fourie", "role": "staff", "whatsapp": "27820000012"}],
         FAR: [{"id": "d1", "name": "Dee Naidoo", "role": "staff", "whatsapp": "27820000021"}],
         OUTSIDE: [{"id": "c1", "name": "Cara Smith", "role": "staff", "whatsapp": "27820000031"}]}


def run(c):
    return asyncio.run(c)


class Desk:
    def _fits(self, rows, member_id, start, end):
        return ShiftDesk._fits(None, rows, member_id, start, end)


@pytest.fixture
def world(monkeypatch):
    db = FakeSupabase({"commerce_lesson_settings": [], "commerce_lesson_requests": [], "commerce_invoices": [],
                       "flow_pool_coaches": [], "flow_coach_offers": [], "commerce_lesson_ratings": []})
    avail, told = [], []
    monkeypatch.setattr(service, "_client", lambda: db)
    monkeypatch.setattr(tenants, "is_active", lambda tid: True)
    monkeypatch.setattr(tenants, "display_name", lambda tid: tid.replace("_", " ").title())
    monkeypatch.setattr("vula.team_index.active_members", lambda tid: TEAMS.get(tid, []))
    monkeypatch.setattr(cal, "_availability", lambda tid: (Desk(), [r for r in avail if r["tenant"] == tid]))
    monkeypatch.setattr(pool, "partner_id", lambda tid: None if tid == OUTSIDE else "flowcrew")

    async def note(tid, event, text, **k):
        told.append((tid, text))
        return True

    async def quiet(*a, **k):
        return True
    monkeypatch.setattr("vula.integrations.notify.notify_team", note)
    monkeypatch.setattr(lessons, "_tell_customer", quiet)
    wa = SimpleNamespace(texts=[], templates=[], window=True, keys=set())

    async def send_text(phone, message, tenant_id="", idem_key=None):
        if not wa.window:
            return False                                # outside WhatsApp's 24 hours
        if idem_key not in wa.keys:
            wa.keys.add(idem_key)
            wa.texts.append((tenant_id, phone, message))
        return True

    async def send_tpl(tenant_id, phone, name, params, lang=None):
        wa.templates.append((tenant_id, phone, name, params))
        return True
    monkeypatch.setattr("vula.api.whatsapp._send_reply", send_text)
    monkeypatch.setattr("vula.api.whatsapp.send_template", send_tpl)
    monkeypatch.setattr(pool, "_dashboard", lambda tid: "https://app.flowcrew.co.za")
    near = {"name": "Dolphin Beach", "lat": SPOT["lat"] + 0.05, "lng": SPOT["lng"]}
    lessons.save_settings(TID, {"enabled": True, "lessons": [SURF, KITE], "hire": HIRE, "spot": SPOT, "coach_share_bp": 7000})
    lessons.save_settings(HOME, {"enabled": True, "lessons": [SURF], "spot": near, "coach_share_bp": 5000})
    lessons.save_settings(FAR, {"enabled": True, "lessons": [SURF], "spot": {"name": "North Beach", "lat": -29.85, "lng": 31.04}})
    return SimpleNamespace(db=db, avail=avail, told=told, wa=wa)


def _next(weekday, hour=9):
    today = datetime.now(lessons.SAST).date()
    d = today + timedelta(days=(weekday - today.weekday()) % 7 + 7)
    return datetime(d.year, d.month, d.day, hour, 0, tzinfo=lessons.SAST)


def _ask(day, people=2, name="Thandi Mokoena", phone="082 123 4567", tid=TID):
    return run(lessons.request(tid, kind="lesson", item_id="surf", wanted_date=day.date().isoformat(), name=name, phone=phone,
                               people=people, fmt="group", level="first_timer"))


def test_only_coaches_who_chose_it_are_offered_and_only_within_reach_and_free(world):
    at = _next(1)
    r = _ask(at)
    assert pool.candidates(TID, r["id"], at.isoformat())["coaches"] == []          # nobody said yes yet
    assert pool.set_open(HOME, "k1", True) is True and pool.set_open(HOME, "k2", True) is True
    assert pool.set_open(FAR, "d1", True) is True                                 # Durban: too far from Cape Town
    with pytest.raises(lessons.LessonError, match="isn't available"):
        pool.set_open(OUTSIDE, "c1", True)                                        # not a business of the partner
    with pytest.raises(lessons.LessonError, match="aren't on this school's team"):
        pool.set_open(HOME, "m1", True)
    found = pool.candidates(TID, r["id"], at.isoformat())
    assert [c["name"] for c in found["coaches"]] == ["Ayanda", "Ben"] and found["coaches"][0]["school"] == "Kite Lab"
    assert found["coaches"][0]["km"] == pytest.approx(5.6, abs=0.2)
    assert found["share_bp"] == 7000 and found["fee_bp"] == 1000                  # the asking school's share, the partner's fee
    assert found["coach_cents"] == lessons.share_of(found["price_cents"], 7000) and found["fee_cents"] == lessons.share_of(found["price_cents"], 1000)
    own = _ask(at, name="Home customer", phone="083 000 0000", tid=HOME)          # Ayanda is teaching at home then
    run(lessons.confirm(HOME, own["id"], starts_at=at.isoformat(), coach_id="k1"))
    world.avail.append({"tenant": HOME, "member_id": "k2", "kind": "off", "from_date": at.date().isoformat(),
                        "to_date": at.date().isoformat(), "note": "Leave"})    # and Ben is away
    assert pool.candidates(TID, r["id"], at.isoformat())["coaches"] == []
    assert [c["name"] for c in pool.candidates(TID, r["id"], (at + timedelta(days=1)).isoformat())["coaches"]] == ["Ayanda", "Ben"]
    pool.set_open(HOME, "k1", False)                                              # the coach changes their mind
    assert [c["name"] for c in pool.candidates(TID, r["id"], (at + timedelta(days=1)).isoformat())["coaches"]] == ["Ben"]


def test_the_school_needs_a_share_set_and_an_unconfirmed_lesson(world):
    at = _next(2)
    r = _ask(at)
    lessons.save_settings(TID, {"coach_share_bp": None})
    with pytest.raises(lessons.LessonError, match="Set the coach's share first"):
        pool.candidates(TID, r["id"], at.isoformat())
    lessons.save_settings(TID, {"coach_share_bp": 7000})
    with pytest.raises(lessons.LessonError, match="half an hour"):
        pool.candidates(TID, r["id"], (datetime.now(lessons.SAST) - timedelta(days=1)).isoformat())
    run(lessons.confirm(TID, r["id"], starts_at=at.isoformat(), coach_id="m1"))
    with pytest.raises(lessons.LessonError, match="before the lesson is confirmed"):
        pool.candidates(TID, r["id"], at.isoformat())
    with pytest.raises(lessons.LessonError, match="isn't available"):
        pool.candidates(OUTSIDE, r["id"], at.isoformat())


def test_the_first_coach_to_accept_gets_it_and_sees_no_customer_details(world):
    at = _next(3)
    r = _ask(at, 3)
    pool.set_open(HOME, "k1", True), pool.set_open(HOME, "k2", True)
    with pytest.raises(lessons.LessonError, match="no longer free"):
        run(pool.ask(TID, r["id"], at.isoformat(), [f"{FAR}:d1"]))
    asked = run(pool.ask(TID, r["id"], at.isoformat(), [f"{HOME}:k1", f"{HOME}:k2"], by="shane"))
    assert [(o["name"], o["status"]) for o in asked["offers"]] == [("Ayanda", "offered"), ("Ben", "offered")]
    run(pool.ask(TID, r["id"], at.isoformat(), [f"{HOME}:k1"]))                    # asking again adds nobody twice
    assert len(world.db.rows["flow_coach_offers"]) == 2
    mine = pool.mine(HOME, "k1")
    offer = mine["asked"][0]
    assert mine["open"] is True and offer["school"] == "Atlantic Surf Demo".replace("Atlantic Surf Demo", tenants.display_name(TID))
    assert offer["people"] == 3 and offer["level"] == "First timer" and offer["share_bp"] == 7000
    assert offer["pay_cents"] == lessons.share_of(r["amount_cents"], 7000) and offer["spot"]["name"] == SPOT["name"]
    assert "Thandi" not in str(mine) and "082" not in str(mine) and "27821234567" not in str(mine)
    with pytest.raises(lessons.LessonError, match="wasn't found"):
        run(pool.answer(HOME, "k2", offer["id"], True))                           # another coach can't answer it
    with pytest.raises(lessons.LessonError, match="wasn't found"):
        run(pool.answer(FAR, "d1", offer["id"], True))
    after = run(pool.answer(HOME, "k1", offer["id"], True))
    assert [t["lesson"] for t in after["taken"]] == ["Surf lesson"] and after["asked"] == []
    row = world.db.rows["commerce_lesson_requests"][0]
    assert (row["guest_coach_tenant"], row["guest_coach_id"], row["coach_name"], row["pool_fee_bp"]) == (HOME, "k1", "Ayanda Zulu", 1000)
    assert row["status"] == "requested"                                           # the school still confirms it
    assert world.told[-1][0] == TID and "Ayanda from Kite Lab will take surf lesson" in world.told[-1][1]
    bens = next(o for o in world.db.rows["flow_coach_offers"] if o["coach_member_id"] == "k2")
    assert bens["status"] == "taken" and pool.mine(HOME, "k2")["asked"] == []
    with pytest.raises(lessons.LessonError, match="Another coach has taken this lesson"):
        run(pool.answer(HOME, "k2", bens["id"], True))
    with pytest.raises(lessons.LessonError, match="already accepted"):
        run(pool.ask(TID, r["id"], at.isoformat(), [f"{HOME}:k2"]))
    other = _ask(at, 1, name="Lebo", phone="084 000 1111")                         # a taken coach isn't free for another lesson then
    assert [c["name"] for c in pool.candidates(TID, other["id"], at.isoformat())["coaches"]] == ["Ben"]


def test_confirming_keeps_the_accepted_time_and_names_the_guest_coach(world, monkeypatch):
    at = _next(4)
    r = _ask(at)
    pool.set_open(HOME, "k1", True)
    run(pool.ask(TID, r["id"], at.isoformat(), [f"{HOME}:k1"]))
    run(pool.answer(HOME, "k1", pool.mine(HOME, "k1")["asked"][0]["id"], True))
    said = []

    async def tell(tid, req, text, news):
        said.append(text)
        return True
    monkeypatch.setattr(lessons, "_tell_customer", tell)
    with pytest.raises(lessons.LessonError, match="Ayanda accepted for .* Keep that time, or release them first") as e:
        run(lessons.confirm(TID, r["id"], starts_at=(at + timedelta(hours=2)).isoformat()))
    assert e.value.status == 409
    done = run(lessons.confirm(TID, r["id"], starts_at=at.isoformat()))
    assert done["status"] == "confirmed" and done["guest_coach"] is True and "Your coach is Ayanda." in said[0]
    w = cal.week(TID, at.date().isoformat())
    assert [(g["guest"], g["people"]) for g in w["guests"]] == [("Ayanda Zulu", 2)] and w["no_coach"] == []
    own = cal.week(HOME, at.date().isoformat(), only_coach="k1")                   # in the coach's own week, at home
    assert [(x["school"], x["lesson"], x["confirmed"]) for x in own["elsewhere"]] == [(tenants.display_name(TID), "Surf lesson", True)]
    assert "Thandi" not in str(own)
    lessons.set_status(TID, r["id"], "completed")
    month = at.strftime("%Y-%m")
    figures = lessons.coach_month(TID, month)
    guest = next(c for c in figures["coaches"] if c["guest"])
    owed = done["amount_cents"]
    assert guest["name"] == "Ayanda Zulu" and guest["coach_id"] is None and guest["done"] == 1
    assert guest["share_cents"] == lessons.share_of(owed, 7000) and guest["fee_cents"] == lessons.share_of(owed, 1000) == figures["pool_fee_cents"]
    assert lessons.coach_month(HOME, month)["coaches"] == []                      # not the home school's lesson
    assert pool.mine(HOME, "k1", month)["month"] == {"month": month, "done": 1, "share_cents": lessons.share_of(owed, 7000)}
    fees = pool.partner_month("flowcrew", month)
    assert fees["schools"] == [{"tenant_id": TID, "school": tenants.display_name(TID), "lessons": 1, "value_cents": owed,
                                "coach_cents": lessons.share_of(owed, 7000), "fee_cents": lessons.share_of(owed, 1000)}]
    assert fees["total"]["fee_cents"] == lessons.share_of(owed, 1000)


def test_a_coach_can_pull_out_and_the_school_can_release_or_use_its_own_coach(world):
    at = _next(5)
    r = _ask(at)
    pool.set_open(HOME, "k1", True), pool.set_open(HOME, "k2", True)
    run(pool.ask(TID, r["id"], at.isoformat(), [f"{HOME}:k1", f"{HOME}:k2"]))
    k2 = pool.mine(HOME, "k2")["asked"][0]["id"]
    assert run(pool.answer(HOME, "k2", k2, False))["asked"] == []                  # Ben declines
    k1 = pool.mine(HOME, "k1")["asked"][0]["id"]
    run(pool.answer(HOME, "k1", k1, True))
    run(pool.answer(HOME, "k1", k1, False))                                        # Ayanda can't make it after all
    row = world.db.rows["commerce_lesson_requests"][0]
    assert row["guest_coach_id"] is None and row["coach_name"] is None and "needs a coach again" in world.told[-1][1]
    with pytest.raises(lessons.LessonError, match="no longer open"):
        run(pool.answer(HOME, "k1", k1, True))
    run(pool.ask(TID, r["id"], at.isoformat(), [f"{HOME}:k2"]))                    # asked afresh
    k2 = pool.mine(HOME, "k2")["asked"][0]["id"]
    run(pool.answer(HOME, "k2", k2, True))
    back = pool.release(TID, r["id"])                                              # the school lets him go
    assert back["request"]["guest_coach"] is False and [o["status"] for o in back["offers"]] == ["declined", "declined"]
    assert pool.mine(HOME, "k2")["taken"] == []
    run(pool.ask(TID, r["id"], at.isoformat(), [f"{HOME}:k2"]))
    run(pool.answer(HOME, "k2", pool.mine(HOME, "k2")["asked"][0]["id"], True))
    own = run(lessons.confirm(TID, r["id"], starts_at=at.isoformat(), coach_id="m1"))   # its own coach takes it after all
    row = world.db.rows["commerce_lesson_requests"][0]
    assert own["coach_name"] == "Shane Daniels" and row["guest_coach_id"] is None and own["guest_coach"] is False
    assert pool.mine(HOME, "k2")["taken"] == []


def test_asking_at_a_new_time_withdraws_the_old_ask(world):
    at = _next(6)
    r = _ask(at)
    pool.set_open(HOME, "k1", True), pool.set_open(HOME, "k2", True)
    run(pool.ask(TID, r["id"], at.isoformat(), [f"{HOME}:k1"]))
    later = run(pool.ask(TID, r["id"], (at + timedelta(hours=3)).isoformat(), [f"{HOME}:k2"]))
    assert [(o["name"], o["status"]) for o in later["offers"]] == [("Ben", "offered")]
    assert pool.mine(HOME, "k1")["asked"] == [] and len(pool.mine(HOME, "k2")["asked"]) == 1


def test_the_coach_hears_on_whatsapp_from_their_own_schools_number_and_never_the_customer(world):
    at = _next(1)
    r = _ask(at, 3)
    pool.set_open(HOME, "k1", True), pool.set_open(HOME, "k2", True)
    out = run(pool.ask(TID, r["id"], at.isoformat(), [f"{HOME}:k1", f"{HOME}:k2"]))
    assert (out["asked"], out["told"]) == (2, 2)
    tenant, phone, text = world.wa.texts[0]
    assert (tenant, phone) == (HOME, "27820000011")                               # the coach's own school's line
    assert text.startswith(f"{tenants.display_name(TID)}: Can you teach a surf lesson on ") and f"at {SPOT['name']}, 3 people (first timer)?" in text
    assert f"You'd earn {lessons.rand(lessons.share_of(r['amount_cents'], 7000))}." in text and "https://app.flowcrew.co.za" in text
    assert "Thandi" not in str(world.wa.texts) and "0821234567" not in str(world.wa.texts)
    again = run(pool.ask(TID, r["id"], at.isoformat(), [f"{HOME}:k1"]))           # already asked: nobody is told twice
    assert (again["asked"], again["told"]) == (0, 0) and len(world.wa.texts) == 2
    run(pool.answer(HOME, "k1", pool.mine(HOME, "k1")["asked"][0]["id"], True))
    done = run(lessons.confirm(TID, r["id"], starts_at=at.isoformat()))
    assert done["coach_told"] is True and "Your surf lesson on" in world.wa.texts[-1][2] and "is confirmed" in world.wa.texts[-1][2]
    assert run(lessons.cancel_own(TID, "27821234567", r["id"])) is not None       # the customer cancels
    assert world.wa.texts[-1][1] == "27820000011" and "has been cancelled. It is off your calendar." in world.wa.texts[-1][2]


def test_outside_the_24_hours_the_approved_template_carries_it_and_a_release_is_told(world):
    at = _next(2)
    r = _ask(at)
    pool.set_open(HOME, "k1", True)
    world.wa.window = False
    out = run(pool.ask(TID, r["id"], at.isoformat(), [f"{HOME}:k1"]))
    assert out["told"] == 1 and world.wa.texts == []
    tenant, phone, name, params = world.wa.templates[0]
    assert (tenant, phone, name) == (HOME, "27820000011", pool.COACH_TEMPLATE)
    assert params[:2] == ["Ayanda", tenants.display_name(TID)] and params[2].startswith("Can you teach a surf lesson on ")
    assert len(params[2]) <= 190 and "\n" not in params[2]
    run(pool.answer(HOME, "k1", pool.mine(HOME, "k1")["asked"][0]["id"], True))
    back = run(pool.release_and_tell(TID, r["id"]))
    assert back["coach_told"] is True and "dropped" not in back
    assert "You are no longer needed for the surf lesson" in world.wa.templates[-1][3][2]
    TEAMS[HOME][0]["whatsapp"] = ""                                               # no number on record: said, not sent
    try:
        run(pool.ask(TID, r["id"], at.isoformat(), [f"{HOME}:k1"]))
        assert world.db.rows["flow_coach_offers"][-1]["status"] == "offered"
        assert pool.for_request(TID, r["id"])[-1]["status"] == "offered" and len(world.wa.templates) == 2
    finally:
        TEAMS[HOME][0]["whatsapp"] = "27820000011"


def test_the_template_is_in_the_standard_set_for_a_business_with_lessons():
    from vula.commerce import wa_templates
    names = [t["name"] for t in wa_templates.standard_templates(TID, {"lessons"}, "watersports")]
    assert pool.COACH_TEMPLATE in names
    assert pool.COACH_TEMPLATE not in [t["name"] for t in wa_templates.standard_templates(TID, {"orders"}, "retail")]
    assert pool.COACH_TEMPLATE_BODY.count("{{") == 3 == len(pool.COACH_TEMPLATE_EXAMPLE)
