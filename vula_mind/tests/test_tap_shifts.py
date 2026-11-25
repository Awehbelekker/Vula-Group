"""Shifts and availability (vula/tap/shifts.py, 24 Nov 2026): the manager plans and publishes; a
person confirms, can't make it or is sick — in the app or on WhatsApp — and the managers hear; staff
say when they can work; an open shift has one taker; nobody is ever on two shifts at once."""
from datetime import datetime, timedelta

import pytest

from tests.tap_fakes import Clock, FakeGateway, FakeMessenger, MemoryRepo
from vula.tap.service import TapConfig, TapService
from vula.tap.shifts import SAST, ShiftError

T = "the-yard"
THANDI, PIETER, BOSS = "w-thandi", "w-pieter", "m-boss"
T_PHONE, P_PHONE, B_PHONE = "27821110001", "27821110002", "27821110009"


class Pusher:
    def __init__(self):
        self.sent = []

    async def send(self, sub, payload):
        self.sent.append((sub["member_id"], payload["title"], payload["body"]))
        return "ok"


@pytest.fixture
def env():
    clock = Clock()                                              # Tue 6 Oct 2026, 10:00 UTC = 12:00 SAST
    repo = MemoryRepo(clock)
    msg, push = FakeMessenger(), Pusher()
    svc = TapService(repo, msg, FakeGateway(), TapConfig(pepper="pep", public_base_url="https://api.test",
                                                         encrypt=lambda s: "enc:" + s, decrypt=lambda s: s[4:], clock=clock))
    svc.pusher = push
    repo.members[T] = [{"id": THANDI, "name": "Thandi Mokoena", "role": "staff", "whatsapp": "+27 82 111 0001"},
                       {"id": PIETER, "name": "Pieter Botha", "role": "staff", "whatsapp": "+27 82 111 0002"},
                       {"id": BOSS, "name": "Aisha Davids", "role": "owner", "whatsapp": "+27 82 111 0009"}]
    for m in (THANDI, PIETER, BOSS):
        repo.subs[f"https://push/{m}"] = {"endpoint": f"https://push/{m}", "member_id": m, "tenant_id": T, "p256dh": "k", "auth": "a"}
    return svc, repo, msg, push, clock


def at(clock, days, hhmm):
    d = (clock.t.astimezone(SAST) + timedelta(days=days)).date()
    h, m = hhmm.split(":")
    return datetime(d.year, d.month, d.day, int(h), int(m), tzinfo=SAST).isoformat()


def shift(svc, clock, member, days=1, start="17:00", end="23:00", **kw):
    return svc.shifts.create(T, {"member_id": member, "starts_at": at(clock, days, start), "ends_at": at(clock, days, end), **kw}, "owner@x")


def said(msg, phone):
    return [m[3] for m in msg.to(phone, "text")]


# ── planning ──────────────────────────────────────────────────────────────────────────────────
def test_a_new_shift_is_a_draft_only_the_manager_sees(env):
    svc, repo, msg, push, clock = env
    s = shift(svc, clock, THANDI, section="Floor", note="Big table at 7")
    assert s["status"] == "draft" and s["name"] == "Thandi" and s["hours"] == 6.0 and s["section"] == "Floor"
    assert "17:00–23:00" in s["when"] and s["warning"] == ""
    assert svc.shifts.mine(T, THANDI)["shifts"] == [] and push.sent == [] and said(msg, T_PHONE) == []
    week = svc.shifts.week(T, at(clock, 1, "00:00")[:10])
    assert week["drafts"] == 1 and len(week["days"]) == 7 and week["shifts"][0]["id"] == s["id"]
    thandi = next(t for t in week["team"] if t["id"] == THANDI)
    assert thandi["hours"] == 6.0 and thandi["shifts"] == 1


def test_a_time_typed_with_no_zone_is_south_african_time(env):
    svc, repo, msg, push, clock = env
    d = (clock.t.astimezone(SAST) + timedelta(days=1)).date().isoformat()
    s = svc.shifts.create(T, {"member_id": THANDI, "starts_at": f"{d}T17:00:00", "ends_at": f"{d}T23:00:00"}, "owner@x")
    assert "17:00–23:00" in s["when"] and s["date"] == d
    assert s["starts_at"] == f"{d}T15:00:00+00:00"                                 # 17:00 SAST is 15:00 UTC


@pytest.mark.parametrize("data, says", [
    ({"starts_at": "x"}, "start and an end"), ({"start": "23:00", "end": "17:00"}, "ends after it starts"),
    ({"member_id": "nobody"}, "Unknown team member"), ({"start": "06:00", "end": "23:30"}, "no more than 16 hours"),
])
def test_refused_shifts(env, data, says):
    svc, repo, msg, push, clock = env
    body = {"member_id": THANDI, "starts_at": at(clock, 1, data.pop("start", "17:00")), "ends_at": at(clock, 1, data.pop("end", "23:00")), **data}
    with pytest.raises(ShiftError, match=says):
        svc.shifts.create(T, body, "owner@x")
    assert svc.shifts.week(T)["shifts"] == []


def test_nobody_is_on_two_shifts_at_once(env):
    svc, repo, msg, push, clock = env
    shift(svc, clock, THANDI)
    with pytest.raises(ShiftError, match="Thandi already has a shift then"):
        shift(svc, clock, THANDI, start="22:00", end="23:30")
    shift(svc, clock, THANDI, start="09:00", end="15:00")            # earlier the same day is fine
    shift(svc, clock, PIETER)                                         # and someone else at the same time


async def test_publishing_tells_each_person_once(env):
    svc, repo, msg, push, clock = env
    shift(svc, clock, THANDI, days=1, section="Floor")
    shift(svc, clock, THANDI, days=2)
    shift(svc, clock, PIETER, days=1)
    shift(svc, clock, None, days=3)                                   # an open shift
    out = await svc.shifts.publish(T, at(clock, 1, "00:00")[:10])
    assert out == {"published": 4, "people": 2, "open": 1}
    mine = [p for p in push.sent if p[0] == THANDI]
    assert len(mine) == 1 and mine[0][1] == "Your shifts" and "(Floor)" in mine[0][2] and mine[0][2].count("17:00–23:00") == 2
    assert len(said(msg, T_PHONE)) == 1 and "reply CONFIRM" in said(msg, T_PHONE)[0]
    assert (await svc.shifts.publish(T, at(clock, 1, "00:00")[:10]))["published"] == 0     # nothing left to publish
    me = svc.shifts.mine(T, THANDI)
    assert len(me["shifts"]) == 2 and me["to_confirm"] == 2 and len(me["open"]) == 1 and me["hours_this_week"] == 12.0


# ── what a person does ────────────────────────────────────────────────────────────────────────
async def test_confirm_cant_and_sick(env):
    svc, repo, msg, push, clock = env
    a, b = shift(svc, clock, THANDI, days=1), shift(svc, clock, THANDI, days=2)
    await svc.shifts.publish(T, at(clock, 1, "00:00")[:10])
    push.sent.clear()
    assert svc.shifts.confirm(T, a["id"], THANDI)["status"] == "confirmed"
    assert svc.shifts.confirm(T, a["id"], THANDI)["status"] == "confirmed"          # again is harmless
    with pytest.raises(ShiftError, match="isn't one of your shifts"):
        svc.shifts.confirm(T, a["id"], PIETER)
    out = await svc.shifts.cant(T, b["id"], THANDI, reason="  Exam\n that day ")
    assert out["status"] == "declined" and out["reason"] == "Exam that day"
    assert push.sent == [(BOSS, "Shift needs cover", push.sent[0][2])] and "Thandi can't work" in push.sent[0][2] and "(Exam that day)" in push.sent[0][2]
    with pytest.raises(ShiftError, match="already told us"):
        await svc.shifts.cant(T, b["id"], THANDI)
    sick = await svc.shifts.cant(T, a["id"], THANDI, sick=True)
    assert sick["status"] == "sick" and "Thandi is sick and can't work" in push.sent[-1][2]
    assert "Shift needs cover" in said(msg, B_PHONE)[-1]
    with pytest.raises(ShiftError):
        await svc.shifts.cant("someone-else", a["id"], THANDI)


async def test_an_open_shift_has_one_taker_who_isnt_already_working(env):
    svc, repo, msg, push, clock = env
    a = shift(svc, clock, THANDI)
    mine = shift(svc, clock, PIETER, start="18:00", end="22:00")
    await svc.shifts.publish(T, at(clock, 1, "00:00")[:10])
    await svc.shifts.cant(T, a["id"], THANDI, sick=True)
    offered = await svc.shifts.offer(T, a["id"])
    assert offered["open"] and offered["status"] == "published" and offered["name"] is None
    assert [s["id"] for s in svc.shifts.mine(T, BOSS)["open"]] == [a["id"]]
    with pytest.raises(ShiftError, match="Pieter already has a shift then"):
        await svc.shifts.take(T, a["id"], PIETER)
    got = await svc.shifts.take(T, a["id"], BOSS)
    assert got["status"] == "confirmed" and got["member_id"] == BOSS and "Aisha took" in push.sent[-1][2]
    await svc.shifts.cancel(T, mine["id"])
    with pytest.raises(ShiftError, match="Someone else has taken"):
        await svc.shifts.take(T, a["id"], PIETER)


async def test_changing_and_cancelling_a_published_shift_tells_the_people_it_touches(env):
    svc, repo, msg, push, clock = env
    a = shift(svc, clock, THANDI)
    draft = shift(svc, clock, PIETER, days=2)
    await svc.shifts.publish(T, at(clock, 1, "00:00")[:10])
    svc.shifts.confirm(T, a["id"], THANDI)
    push.sent.clear()
    moved = await svc.shifts.change(T, a["id"], {"member_id": PIETER})
    assert moved["name"] == "Pieter" and moved["status"] == "published"            # the new person confirms
    assert {(p[0], p[1]) for p in push.sent} == {(PIETER, "Your shift"), (THANDI, "Shift changed")}
    push.sent.clear()
    await svc.shifts.cancel(T, a["id"])
    assert push.sent == [(PIETER, "Shift cancelled", push.sent[0][2])]
    assert [s["id"] for s in svc.shifts.week(T, at(clock, 1, "00:00")[:10])["shifts"]] == [draft["id"]]
    with pytest.raises(ShiftError):
        await svc.shifts.cancel(T, a["id"])


# ── availability ──────────────────────────────────────────────────────────────────────────────
def test_staff_say_when_they_can_work_and_the_planner_flags_what_falls_outside(env):
    svc, repo, msg, push, clock = env
    tomorrow = (clock.t.astimezone(SAST) + timedelta(days=1))
    av = svc.shifts.add_weekly(T, THANDI, tomorrow.weekday(), "16:00", "23:30")
    assert av["weekly"] == [{"id": av["weekly"][0]["id"], "weekday": tomorrow.weekday(), "start": "16:00", "end": "23:30"}]
    for bad in ((7, "16:00", "23:00"), (1, "23:00", "16:00"), (1, "4pm", "23:00"), (tomorrow.weekday(), "22:00", "23:45")):
        with pytest.raises(ShiftError):
            svc.shifts.add_weekly(T, THANDI, *bad)
    assert shift(svc, clock, THANDI)["warning"] == ""                              # inside her times
    early = shift(svc, clock, THANDI, start="09:00", end="15:00")
    assert "hasn't said they can work then" in early["warning"]                      # allowed, but flagged
    assert shift(svc, clock, PIETER)["warning"] == ""                              # said nothing: nothing to go against
    day_after = (tomorrow + timedelta(days=1)).date().isoformat()
    off = svc.shifts.add_time_off(T, THANDI, day_after, day_after, "Wedding")
    assert off["off"][0]["note"] == "Wedding"
    assert "is away that day (Wedding)" in shift(svc, clock, THANDI, days=2)["warning"]
    for bad in (("2020-01-01", "2020-01-02"), (day_after, "2026-01-01"), ("soon", "")):
        with pytest.raises(ShiftError):
            svc.shifts.add_time_off(T, THANDI, *bad)
    with pytest.raises(ShiftError, match="isn't one of your times"):
        svc.shifts.remove_availability(T, PIETER, av["weekly"][0]["id"])            # someone else's
    assert svc.shifts.remove_availability(T, THANDI, av["weekly"][0]["id"])["weekly"] == []
    assert next(t for t in svc.shifts.week(T)["team"] if t["id"] == THANDI)["availability"]["off"][0]["from"] == day_after


# ── WhatsApp ──────────────────────────────────────────────────────────────────────────────────
async def test_the_team_on_whatsapp(env):
    svc, repo, msg, push, clock = env
    a = shift(svc, clock, THANDI, days=0, start="17:00", end="23:00")               # tonight
    shift(svc, clock, THANDI, days=3)
    await svc.shifts.publish(T, at(clock, 0, "00:00")[:10])
    assert await svc.handle_text(T, T_PHONE, "my shifts") is True
    assert "Your shifts:" in said(msg, T_PHONE)[-1] and "please confirm" in said(msg, T_PHONE)[-1]
    assert await svc.handle_text(T, T_PHONE, "Confirm") is True
    assert "2 shifts confirmed" in said(msg, T_PHONE)[-1] and svc.shifts.mine(T, THANDI)["to_confirm"] == 0
    push.sent.clear()
    assert await svc.handle_text(T, T_PHONE, "I'm sick today sorry") is True
    assert "Your manager has been told" in said(msg, T_PHONE)[-1] and "Get well soon" in said(msg, T_PHONE)[-1]
    assert repo.get_row("kb_shifts", T, a["id"])["status"] == "sick" and push.sent[0][0] == BOSS
    # the next one is three days off: not marked from a one-word message
    assert await svc.handle_text(T, T_PHONE, "sick") is True
    assert "more than a day away" in said(msg, T_PHONE)[-1]
    assert sum(1 for s in svc.shifts.mine(T, THANDI)["shifts"] if s["status"] == "sick") == 1


async def test_whatsapp_words_from_anyone_else_are_left_alone(env):
    svc, repo, msg, push, clock = env
    shift(svc, clock, THANDI, days=0)
    await svc.shifts.publish(T, at(clock, 0, "00:00")[:10])
    assert await svc.shifts.handle_text(T, "27839999999", "I'm sick") is False        # a customer
    assert await svc.shifts.handle_text(T, P_PHONE, "sick") is False                  # on the team, but no shift
    assert await svc.shifts.handle_text(T, T_PHONE, "is the fish sick-making?") is False
    assert await svc.shifts.handle_text(T, T_PHONE, "can I confirm my order") is False
    assert await svc.shifts.handle_text(T, P_PHONE, "shifts") is True and "no shifts coming up" in said(msg, P_PHONE)[-1]


async def test_a_reminder_two_hours_before_once(env):
    svc, repo, msg, push, clock = env
    shift(svc, clock, THANDI, days=0, start="13:30", end="20:00", section="Bar")     # 90 minutes from now
    shift(svc, clock, PIETER, days=0, start="19:00", end="23:00")                    # too far off
    assert await svc.shifts.sweep(T) == 0                                            # drafts are never reminded
    await svc.shifts.publish(T, at(clock, 0, "00:00")[:10])
    push.sent.clear()
    assert await svc.shifts.sweep(T) == 1 and push.sent == [(THANDI, "Shift soon", "You start at 13:30, Bar.")]
    assert await svc.shifts.sweep(T) == 0


# ── the tips switch ───────────────────────────────────────────────────────────────────────────
def test_tips_on_the_shared_board_only_when_the_owner_says(env):
    svc, repo, msg, push, clock = env
    assert svc.scores.tips_on_board(T) is False
    repo.upsert_settings(T, {"board_tips": True})
    assert svc.scores.tips_on_board(T) is True


# ── the roster outside WhatsApp's 24-hour window ──────────────────────────────────────────────
async def test_a_roster_whatsapp_refuses_goes_by_the_approved_template_on_one_line(env):
    svc, repo, msg, push, clock = env
    from vula.tap import shifts as sh
    shift(svc, clock, THANDI, section="Floor")
    shift(svc, clock, THANDI, days=2)

    async def refused(tenant_id, phone, body):                   # what Meta does outside the window
        msg.sent.append(("text", tenant_id, phone, body))
        return False
    msg.text = refused
    await svc.shifts.publish(T)
    sent = [m[3] for m in msg.to(T_PHONE, "template")]
    assert len(sent) == 1 and sent[0][0] == sh.SHIFT_TEMPLATE
    first, business, line = sent[0][1]
    assert first == "Thandi" and business and "\n" not in line and len(line) <= 200
    assert line.startswith("Your shifts: ") and "17:00" in line
    assert sh.SHIFT_TEMPLATE_BODY.count("{{") == len(sh.SHIFT_TEMPLATE_EXAMPLE) == 3


async def test_inside_the_window_the_roster_is_a_plain_message_and_no_template(env):
    svc, repo, msg, push, clock = env
    shift(svc, clock, THANDI)
    await svc.shifts.publish(T)
    assert said(msg, T_PHONE) and not msg.to(T_PHONE, "template")


def test_the_roster_template_is_in_the_standard_set_where_shifts_show():
    from vula.commerce import wa_templates
    from vula.tap import shifts as sh
    names = lambda mods: {t["name"] for t in wa_templates.standard_templates("the-yard", mods, "restaurant_lite")}  # noqa: E731
    assert sh.SHIFT_TEMPLATE in names({"tables", "products"}) and sh.SHIFT_TEMPLATE in names({"team"})
    assert sh.SHIFT_TEMPLATE not in names({"orders", "products"})


# ── who is working now ────────────────────────────────────────────────────────────────────────
async def test_on_now_is_only_people_on_a_published_shift_that_covers_this_minute(env):
    svc, repo, msg, push, clock = env
    shift(svc, clock, THANDI, days=0, start="11:00", end="15:00")        # now is 12:00
    shift(svc, clock, PIETER, days=0, start="17:00", end="23:00")        # later today
    assert svc.shifts.on_now(T) == []                                    # drafts count for nothing
    await svc.shifts.publish(T)
    assert svc.shifts.on_now(T) == [THANDI]
    clock.t += timedelta(hours=4)                                        # 16:00: hers is over, his not begun
    assert svc.shifts.on_now(T) == []


# ── copying last week ─────────────────────────────────────────────────────────────────────────
async def test_copy_last_week_makes_drafts_at_the_same_times_and_tells_nobody(env):
    svc, repo, msg, push, clock = env
    shift(svc, clock, THANDI, days=1, section="Floor", note="Open the bar")      # Wed this week
    shift(svc, clock, PIETER, days=2)
    svc.shifts.create(T, {"member_id": None, "starts_at": at(clock, 3, "10:00"), "ends_at": at(clock, 3, "15:00")}, "o@x")
    gone = shift(svc, clock, PIETER, days=4)
    await svc.shifts.publish(T)
    await svc.shifts.cancel(T, gone["id"])
    msg.sent.clear(); push.sent.clear()
    nxt = (clock.t.astimezone(SAST) + timedelta(days=7)).date().isoformat()
    out = svc.shifts.copy_week(T, nxt, "owner@x")
    assert out["copied"] == 3 and out["last_week"] == 3 and out["already_there"] == 0      # the cancelled one isn't last week's
    wk = svc.shifts.week(T, nxt)
    assert {s["status"] for s in wk["shifts"]} == {"draft"} and wk["drafts"] == 3
    mine = next(s for s in wk["shifts"] if s["member_id"] == THANDI)
    assert mine["section"] == "Floor" and mine["note"] == "Open the bar" and "17:00" in mine["when"]
    assert mine["date"] == (clock.t.astimezone(SAST) + timedelta(days=8)).date().isoformat()
    assert sum(1 for s in wk["shifts"] if s["open"]) == 1
    assert not msg.sent and not push.sent                                    # drafts: nobody hears
    assert svc.shifts.mine(T, THANDI)["to_confirm"] == 1                     # only last week's own


async def test_copying_twice_adds_nothing_and_a_shift_already_planned_is_kept(env):
    svc, repo, msg, push, clock = env
    shift(svc, clock, THANDI, days=1)
    shift(svc, clock, PIETER, days=1)
    nxt = (clock.t.astimezone(SAST) + timedelta(days=7)).date().isoformat()
    svc.shifts.create(T, {"member_id": THANDI, "starts_at": at(clock, 8, "18:00"), "ends_at": at(clock, 8, "22:00")}, "o@x")
    out = svc.shifts.copy_week(T, nxt, "owner@x")
    assert out["copied"] == 1 and out["already_there"] == 1                  # Thandi is already on then
    again = svc.shifts.copy_week(T, nxt, "owner@x")
    assert again["copied"] == 0 and again["already_there"] == 2
    assert len(svc.shifts.week(T, nxt)["shifts"]) == 2


def test_someone_who_left_the_team_is_not_copied(env):
    svc, repo, msg, push, clock = env
    shift(svc, clock, PIETER, days=1)
    repo.members[T] = [m for m in repo.members[T] if m["id"] != PIETER]
    nxt = (clock.t.astimezone(SAST) + timedelta(days=7)).date().isoformat()
    assert svc.shifts.copy_week(T, nxt, "o@x") == {"copied": 0, "already_there": 0, "left_the_team": 1, "last_week": 1,
                                                   "week_start": svc.shifts.week(T, nxt)["week_start"]}
