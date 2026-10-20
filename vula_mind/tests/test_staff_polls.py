"""Staff notices part 2 (migration 219): Vula asks for birthdays, ID number suggestions, special
days and work anniversaries, and polls to the team — optionally anonymous (vula/staff_polls.py)."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from vula import sa_holidays as h
from vula import staff_notices as sn
from vula import staff_polls as sp
from tests.test_staff_notices import digg  # noqa: F401  (the shared in-memory DIGG team)


# ── birthdays: Vula asks, and reads the answer ────────────────────────────────

@pytest.mark.parametrize("text,md", [("16 Dec", "12-16"), ("16 December", "12-16"), ("Dec 16", "12-16"),
                                     ("16/12", "12-16"), ("16-12", "12-16"), ("16 Desember", "12-16"),
                                     ("5 June", "06-05"), ("05-06", "06-05"), ("29 Feb", "02-29")])
def test_day_and_month_as_people_type_them(text, md):
    assert sn.parse_day_month(text) == md


def test_not_a_date():
    assert sn.parse_day_month("31 Feb") is None and sn.parse_day_month("hello there") is None
    assert sn.parse_day_month("05-06", stored=True) == "05-06"           # the dashboard's own format
    assert sn.parse_start("March 2021") == "2021-03-01" and sn.parse_start("1 Mar 2021") == "2021-03-01"
    assert sn.parse_start("next year") is None


@pytest.mark.asyncio
async def test_asks_only_those_without_a_birthday_once(digg):
    res = await sn.ask_birthdays("digg-demo")
    assert res["names"] == ["Ian Downing"]            # Judy has one; Nelitho opted out; Sipho has no WhatsApp
    assert digg.sends[0][0] == "27645755210" and "when's your birthday" in digg.sends[0][1]
    assert ("27645755210", "staff_birthday", "m1") in digg.asked
    assert digg.db["vula_team_members"][0]["birthday_asked_at"]
    assert (await sn.ask_birthdays("digg-demo"))["asked"] == 0


@pytest.mark.asyncio
async def test_the_answer_is_saved_and_skip_is_respected(digg):
    assert await sn.answer("digg-demo", "staff_birthday", "m1", "16 Dec") == "🎂 Saved — 16 Dec. Thank you!"
    assert digg.db["vula_team_members"][0]["birthday"] == "12-16"
    assert await sn.answer("digg-demo", "staff_birthday", "m1", "skip") == "👍 No problem — I won't ask again."
    assert (await sn.answer("digg-demo", "staff_birthday", "m1", "Got it ✅")).startswith("Please send")
    assert await sn.answer("digg-demo", "staff_birthday", "m1", "Can you send me the HPC invoices from August?") is None


@pytest.mark.asyncio
async def test_start_date_is_asked_after_the_birthday_when_anniversaries_are_on(digg):
    digg.db["vula_tenant_config"][0]["staff_notices"] = {"anniversaries": True}
    await sn.answer("digg-demo", "staff_birthday", "m1", "16 Dec")
    assert ("27645755210", "staff_start", "m1") in digg.asked
    assert await sn.answer("digg-demo", "staff_start", "m1", "March 2021") == "👍 Saved — you started March 2021."
    assert digg.db["vula_team_members"][0]["start_date"] == "2021-03-01"


# ── ID numbers: a suggestion, never saved on its own ──────────────────────────

def test_birthday_from_an_sa_id_number():
    assert sn.id_birthday("8001015009087") == "01-01"
    assert sn.id_birthday("8001015009088") is None        # wrong check digit
    assert sn.id_birthday("8013015009084") is None        # month 13
    assert sn.id_birthday("123") is None


def test_suggestion_comes_from_the_same_persons_worker_record(digg):
    digg.db["commerce_workers"] = [{"tenant_id": "digg-demo", "phone": "0645755210", "id_number": "8001015009087"}]
    ian = digg.db["vula_team_members"][0]
    assert sn.suggest_birthday("digg-demo", ian) == "01-01"
    assert ian.get("birthday") is None                    # nothing saved
    assert sn.suggest_birthday("digg-demo", digg.db["vula_team_members"][1]) is None   # Judy has one


# ── special days, the business's own days, work anniversaries ─────────────────

def test_mothers_and_fathers_day():
    obs = h.observances(2026)
    assert obs[date(2026, 5, 10)][0] == "mothers_day" and obs[date(2026, 6, 21)][0] == "fathers_day"
    assert h.observances(2027)[date(2027, 5, 9)][0] == "mothers_day"


@pytest.mark.asyncio
async def test_switched_on_special_days_and_own_days_go_once(digg):
    digg.db["vula_tenant_config"][0]["staff_notices"] = {
        "observances": ["spring_day"], "observance_lines": {"spring_day": "happy Spring Day — braai at 1!"},
        "company_days": [{"name": "DIGG's birthday", "date": "09-01", "line": "DIGG turns 5 today! 🎉"}]}
    made = await sn.daily_pass("digg-demo", date(2026, 9, 1))
    assert made == ["Spring Day 2026-09-01", "DIGG's birthday 2026-09-01"]
    texts = [s[1] for s in digg.sends] + [t[2][1] for t in digg.templates]
    assert any("braai at 1" in t for t in texts) and any("DIGG turns 5" in t for t in texts)
    assert not [t for t in digg.templates if t[0] == "27831111111"]     # Nelitho opted out
    assert await sn.daily_pass("digg-demo", date(2026, 9, 1)) == []
    assert await sn.daily_pass("digg-demo", date(2026, 12, 24)) == []   # Christmas Eve not switched on


@pytest.mark.asyncio
async def test_work_anniversary(digg):
    digg.db["vula_tenant_config"][0]["staff_notices"] = {"anniversaries": True}
    digg.db["vula_team_members"][1]["start_date"] = "2023-10-21"
    made = await sn.daily_pass("digg-demo", date(2026, 10, 21))
    assert made == ["Anniversary: Judy Smith 2026-10-21"]
    judy = [t for t in digg.templates if t[0] == "27827000000"][0]
    assert judy[2][1] == "3 years with DIGG today — thank you! 🎉"
    assert any("Judy has been with us 3 years today" in s[1] for s in digg.sends)   # team note to Ian
    assert sn._years_today("2026-10-21", date(2026, 10, 21)) == 0      # not on day one


def test_company_days_are_checked_when_saved():
    assert sn._clean_company_day({"name": "Year-end", "date": "12-05"}) == {"name": "Year-end", "date": "12-05", "line": ""}
    assert sn._clean_company_day({"name": "", "date": "12-05"}) is None
    assert sn._clean_company_day({"name": "x", "date": "31 Feb"}) is None


# ── polls ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def wa_ui(monkeypatch):
    from vula.api import whatsapp as wa
    shown = []

    async def creds(t):
        return {"token": "t", "phone_id": "p"}

    async def buttons(c, number, body, btns, image_url=None):
        shown.append(("buttons", number, [b["id"] for b in btns]))
        return True

    async def lst(c, number, header, body, footer, button, sections):
        shown.append(("list", number, [r["id"] for s in sections for r in s["rows"]]))
        return True

    monkeypatch.setattr(wa, "_get_tenant_wa_creds", creds)
    monkeypatch.setattr(wa, "_send_wa_buttons", buttons)
    monkeypatch.setattr(wa, "_send_wa_list", lst)
    return shown


@pytest.mark.asyncio
async def test_a_poll_reaches_everyone_the_right_way(digg, wa_ui):
    poll = sp.create("digg-demo", "Which day for the year-end function?", ["Fri 5 Dec", "Sat 6 Dec", "Fri 12 Dec"])
    res = await sn.send("digg-demo", poll["notice_id"])
    assert res["sent"] == 3
    assert wa_ui == [("buttons", "27645755210", [f"poll:{poll['id']}:{i}" for i in range(3)])]   # Ian, in his 24 hours
    invites = [t for t in digg.templates if t[1] == "digg_demo_staff_poll_invite"]
    assert len(invites) == 2 and invites[0][2] == ["Judy", "DIGG", "Which day for the year-end function?"]
    assert sum(1 for a in digg.asked if a[1] == "staff_poll") == 3


@pytest.mark.asyncio
async def test_five_choices_come_as_a_list_and_vote_opens_them(digg, wa_ui):
    poll = sp.create("digg-demo", "Lunch?", ["Pizza", "Sushi", "Braai", "Curry", "Wraps"], audience_spec={"ids": ["m1", "m2"]})
    await sn.send("digg-demo", poll["notice_id"])
    assert wa_ui[0][0] == "list" and len(wa_ui[0][2]) == 5
    reply = await sp.vote_text("digg-demo", "27827000000", poll["id"], "Vote")   # Judy taps Vote on the invite
    assert reply == "👆 Tap your choice above." and wa_ui[-1][1] == "27827000000"


@pytest.mark.asyncio
async def test_vote_change_and_results(digg, wa_ui):
    poll = sp.create("digg-demo", "Year-end?", ["Fri 5 Dec", "Sat 6 Dec"])
    await sn.send("digg-demo", poll["notice_id"])
    assert (await sp.vote_tap("digg-demo", "27645755210", f"poll:{poll['id']}:0")).startswith("🗳️ Got your vote: *Fri 5 Dec*")
    assert await sp.vote_text("digg-demo", "0827000000", poll["id"], "2")
    assert await sp.vote_text("digg-demo", "0827000000", poll["id"], "fri")     # Judy changes her mind
    assert await sp.vote_text("digg-demo", "0827000000", poll["id"], "Can you send the invoices?") is None
    res = sp.results("digg-demo", poll["id"])
    assert [c["votes"] for c in res["counts"]] == [2, 0]
    assert res["voted"] == 2 and res["reached"] == 3 and res["not_voted"] == ["Nelitho Mbeki"]
    assert {b["name"] for b in res["by_person"]} == {"Ian Downing", "Judy Smith"}


@pytest.mark.asyncio
async def test_anonymous_poll_never_shows_who_chose_what(digg, wa_ui):
    poll = sp.create("digg-demo", "Happy with the new shifts?", ["Yes", "No"], anonymous=True)
    await sn.send("digg-demo", poll["notice_id"])
    reply = await sp.vote_text("digg-demo", "27827000000", poll["id"], "No")
    assert "isn't shown to anyone at DIGG" in reply
    assert all(v["member_id"] is None for v in digg.db["vula_staff_poll_votes"])
    res = sp.results("digg-demo", poll["id"])
    assert "by_person" not in res and res["voted"] == 1 and res["counts"][1]["votes"] == 1
    assert "Judy Smith" not in res["not_voted"]                            # who voted is still known
    await sp.vote_text("digg-demo", "27827000000", poll["id"], "Yes")       # changing it keeps one vote
    assert [c["votes"] for c in sp.results("digg-demo", poll["id"])["counts"]] == [1, 0]


@pytest.mark.asyncio
async def test_closing_refuses_late_votes_and_tells_the_owner(digg, wa_ui):
    soon = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
    poll = sp.create("digg-demo", "Year-end?", ["Fri", "Sat"], closes_at=soon)
    await sn.send("digg-demo", poll["notice_id"])
    await sp.vote_text("digg-demo", "27827000000", poll["id"], "Fri")
    digg.sends.clear()
    out = await sp.run_due(datetime.now(timezone.utc) + timedelta(minutes=2))
    assert out["poll_closed"] == 1
    summary = [s for s in digg.sends if s[2] and s[2].startswith("poll_summary")]
    assert summary and "Fri 1 · Sat 0" in summary[0][1] and "1 of 3 voted" in summary[0][1]
    assert await sp.vote_text("digg-demo", "27827000000", poll["id"], "2") == "That poll has closed — thanks anyway!"


@pytest.mark.asyncio
async def test_non_voters_get_one_reminder(digg, wa_ui):
    poll = sp.create("digg-demo", "Year-end?", ["Fri", "Sat"])
    await sn.send("digg-demo", poll["notice_id"])
    await sp.vote_text("digg-demo", "27645755210", poll["id"], "1")
    digg.templates.clear()
    out = await sp.run_due(datetime.now(timezone.utc) + timedelta(hours=25))
    assert out["poll_reminded"] == 2                                       # Judy and Nelitho, not Ian
    again = await sp.run_due(datetime.now(timezone.utc) + timedelta(hours=26))
    assert again["poll_reminded"] == 0


def test_a_poll_needs_a_question_and_two_choices(digg):
    with pytest.raises(ValueError):
        sp.create("digg-demo", "Lunch?", ["Pizza", "pizza"])
    with pytest.raises(ValueError):
        sp.create("digg-demo", "", ["A", "B"])


@pytest.mark.asyncio
async def test_owner_starts_an_anonymous_poll_on_whatsapp(digg, wa_ui):
    from core.skills.base import looks_like_owner_admin_question
    from core.skills.commerce_admin import CommerceAdminSkill
    assert looks_like_owner_admin_question("Anonymous poll for the staff: happy with the new shifts?")
    skill = CommerceAdminSkill()
    ctx = {"tenant_id": "digg-demo", "phone": "27645755210"}
    args = {"question": "Happy with the new shifts?", "options": ["Yes", "No"], "anonymous": True}
    prev = await skill._staff_poll("digg-demo", args, ctx)
    assert prev["preview"] and prev["count"] == 2 and "Anonymous" in prev["message"]
    done = await skill._staff_poll("digg-demo", {**args, "confirm": True}, ctx)
    assert done["reached"] == 2 and done["reply_verbatim"].startswith("✅ Poll sent to 2 of 2 staff")


def test_poll_tool_is_confirm_first_and_template_has_vote_button():
    from core import dry_run
    from vula.commerce import wa_templates as wt
    assert "staff_poll" in dry_run.PREVIEW_UNLESS_CONFIRMED
    t = {x["name"]: x for x in wt.staff_templates("digg-demo")}["digg_demo_staff_poll_invite"]
    assert t["buttons"] == [{"type": "QUICK_REPLY", "text": "Vote"}] and t["category"] == "UTILITY"


@pytest.mark.asyncio
async def test_birthday_and_poll_replies_route_through_open_questions(monkeypatch):
    from unittest.mock import AsyncMock
    from vula.api import whatsapp as wa
    from vula import open_questions as oq
    monkeypatch.setattr(sn, "answer", AsyncMock(return_value="🎂 Saved — 16 Dec. Thank you!"))
    monkeypatch.setattr(sp, "vote_text", AsyncMock(return_value="🗳️ Got your vote: *Fri*."))
    monkeypatch.setattr(oq, "close", lambda *a, **k: None)
    monkeypatch.setattr(wa, "_send_reply", AsyncMock(return_value=True))
    assert await wa._try_open_question("digg-demo", "27827000000", "16 Dec",
                                       {"id": "q", "kind": "staff_birthday", "ref_id": "m2"})
    assert await wa._try_open_question("digg-demo", "27827000000", "1",
                                       {"id": "q", "kind": "staff_poll", "ref_id": "p1"})
    sn.answer.return_value = None
    assert not await wa._try_open_question("digg-demo", "27827000000", "Send me the invoices",
                                           {"id": "q", "kind": "staff_birthday", "ref_id": "m2"})

