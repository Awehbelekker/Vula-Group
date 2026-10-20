"""Staff notices (vula/staff_notices.py, migration 218): a business's announcements, birthdays and
public holiday reminders to its own team on WhatsApp, with "Got it" on must-read notices."""
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from vula import sa_holidays as h
from vula import staff_notices as sn


# ── South African public holidays ─────────────────────────────────────────────

def test_easter_and_the_fixed_days():
    assert h.easter_sunday(2026) == date(2026, 4, 5)
    assert h.easter_sunday(2027) == date(2027, 3, 28)
    hol = h.holidays(2026)
    assert hol[date(2026, 4, 3)] == "Good Friday" and hol[date(2026, 4, 6)] == "Family Day"
    assert hol[date(2026, 12, 16)] == "Day of Reconciliation"
    assert h.holidays(2027)[date(2027, 6, 16)] == "Youth Day"            # a Wednesday
    assert len([d for d in hol if "observed" not in hol[d]]) == 12


def test_a_sunday_holiday_is_also_kept_on_the_monday():
    assert h.holiday_name(date(2026, 8, 9)) == "National Women's Day"    # Sunday
    assert h.holiday_name(date(2026, 8, 10)) == "National Women's Day (observed)"
    assert not h.is_business_day(date(2026, 8, 10))
    assert h.is_business_day(date(2026, 8, 11))


def test_tax_due_dates_skip_public_holidays():
    from vula.commerce.tax import last_business_day
    assert last_business_day(date(2026, 4, 27)) == date(2026, 4, 24)     # Freedom Day, a Monday
    assert last_business_day(date(2026, 4, 6)) == date(2026, 4, 2)      # Family Day ← Good Friday
    assert last_business_day(date(2026, 10, 30)) == date(2026, 10, 30)


# ── a small in-memory stand-in for the Supabase tables ───────────────────────

class _Q:
    def __init__(self, db, name):
        self.db, self.name, self.filters, self.op, self.payload, self._limit = db, name, [], "select", None, None

    def select(self, *a): return self
    def order(self, *a, **k): return self
    def limit(self, n): self._limit = n; return self
    def eq(self, k, v): self.filters.append(lambda r, k=k, v=v: str(r.get(k)) == str(v)); return self
    def in_(self, k, vs): self.filters.append(lambda r, k=k, vs=vs: r.get(k) in vs); return self
    def lte(self, k, v): self.filters.append(lambda r, k=k, v=v: (r.get(k) or "") <= v); return self
    def gte(self, k, v): self.filters.append(lambda r, k=k, v=v: (r.get(k) or "") >= v); return self

    def is_(self, k, v):
        self.filters.append(lambda r, k=k: r.get(k) is None)
        return self

    @property
    def not_(self):
        outer = self

        class _Not:
            def is_(self, k, v):
                outer.filters.append(lambda r, k=k: r.get(k) is not None)
                return outer
        return _Not()

    def insert(self, row):
        self.op, self.payload = "insert", row
        return self

    def update(self, patch):
        self.op, self.payload = "update", patch
        return self

    def execute(self):
        rows = self.db.setdefault(self.name, [])
        if self.op == "insert":
            row = {"id": str(uuid.uuid4()), "created_at": datetime.now(timezone.utc).isoformat(), **self.payload}
            if self.name == "vula_staff_notice_recipients" and any(
                    r["notice_id"] == row["notice_id"] and r["member_id"] == row["member_id"] for r in rows):
                raise Exception("duplicate key value violates unique constraint")
            if self.name == "vula_staff_poll_voters" and any(
                    r["poll_id"] == row["poll_id"] and r["member_id"] == row["member_id"] for r in rows):
                raise Exception("duplicate key value violates unique constraint")
            if self.name == "vula_staff_polls":
                row.setdefault("status", "open")       # the table's default
            for k in ("sent_at", "failed", "wamid", "delivered_at", "read_at", "acknowledged_at",
                      "reminded_at", "summary_sent_at", "send_at"):
                row.setdefault(k, None)
            rows.append(row)
            return SimpleNamespace(data=[row])
        hit = [r for r in rows if all(f(r) for f in self.filters)]
        if self.op == "update":
            for r in hit:
                r.update(self.payload)
            return SimpleNamespace(data=hit)
        return SimpleNamespace(data=hit[: self._limit] if self._limit else hit)


OLD = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
NOW = datetime.now(timezone.utc).isoformat()


@pytest.fixture
def digg(monkeypatch):
    db = {
        "vula_team_members": [
            {"id": "m1", "tenant_id": "digg-demo", "name": "Ian Downing", "whatsapp": "27645755210",
             "role": "owner", "active": True, "last_message_at": NOW},
            {"id": "m2", "tenant_id": "digg-demo", "name": "Judy Smith", "whatsapp": "27827000000",
             "role": "manager", "active": True, "birthday": "10-21", "last_message_at": OLD},
            {"id": "m3", "tenant_id": "digg-demo", "name": "Nelitho Mbeki", "whatsapp": "27831111111",
             "role": "staff", "active": True, "birthday": "02-29", "notices_opt_out": True},
            {"id": "m4", "tenant_id": "digg-demo", "name": "Sipho", "whatsapp": "", "role": "staff",
             "active": True},
        ],
        "vula_tenant_config": [{"tenant_id": "digg-demo", "staff_notices": None}],
    }
    sends, templates, asked = [], [], []
    from vula.api import whatsapp as wa
    from vula import open_questions
    monkeypatch.setattr(sn, "_client", lambda: SimpleNamespace(table=lambda n: _Q(db, n)))
    monkeypatch.setattr(sn, "_business", lambda t: "DIGG")
    monkeypatch.setattr(sn, "_PACE_S", 0)

    async def reply(to, msg, tenant_id="", idem_key=None):
        sends.append((to, msg, idem_key))
        return True

    async def template(tenant_id, to, name, params, header_image_url=None, lang=None):
        templates.append((to, name, params, header_image_url))
        return f"wamid.{len(templates)}"

    monkeypatch.setattr(wa, "_send_reply", reply)
    monkeypatch.setattr(wa, "send_template_message", template)
    monkeypatch.setattr(open_questions, "ask", lambda t, p, k, ref, prompt="": asked.append((p, k, ref)))
    return SimpleNamespace(db=db, sends=sends, templates=templates, asked=asked)


# ── who gets it ───────────────────────────────────────────────────────────────

def test_audience_by_roles_and_ids_and_opt_out(digg):
    everyone = sn.audience("digg-demo", None)
    assert [m["id"] for m in everyone] == ["m1", "m2", "m3"]              # Sipho has no WhatsApp
    assert [m["id"] for m in sn.audience("digg-demo", {"roles": ["staff"]})] == ["m3"]
    assert [m["id"] for m in sn.audience("digg-demo", {"ids": ["m2"]})] == ["m2"]
    assert [m["id"] for m in sn.audience("digg-demo", None, kind="holiday")] == ["m1", "m2"]


# ── sending ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_notice_reaches_everyone_once(digg):
    n = sn.create("digg-demo", "Office closed Friday", "The office is closed on Friday.\nSite carries on.",
                  must_read=True)
    res = await sn.send("digg-demo", n["id"])
    assert res == {"sent": 3, "failed": 0, "total": 3, "names_failed": []}
    # Ian messaged today → plain text; Judy and Nelitho haven't → the approved template
    assert digg.sends[0][0] == "27645755210" and "Reply *got it*" in digg.sends[0][1]
    assert [t[1] for t in digg.templates] == ["digg_demo_staff_notice", "digg_demo_staff_notice"]
    assert digg.templates[0][2] == ["DIGG", "Office closed Friday — The office is closed on Friday. Site carries on."]
    assert {a[1] for a in digg.asked} == {"staff_notice"} and len(digg.asked) == 3
    # a second send (two schedulers, a double tap) finds nothing to do
    again = await sn.send("digg-demo", n["id"])
    assert "already" in again["error"] and len(digg.templates) == 2 and len(digg.sends) == 1
    assert sn.get("digg-demo", n["id"])["status"] == "sent"


@pytest.mark.asyncio
async def test_a_picture_notice_always_uses_the_picture_template(digg):
    n = sn.create("digg-demo", "October newsletter", "Our month on site.", image_url="https://x/news.jpg",
                  audience_spec={"ids": ["m1"]})
    await sn.send("digg-demo", n["id"])
    assert digg.templates == [("27645755210", "digg_demo_staff_notice_image",
                               ["DIGG", "October newsletter — Our month on site."], "https://x/news.jpg")]


# ── read ticks, "Got it", the reminder and the owner's summary ───────────────

@pytest.mark.asyncio
async def test_got_it_and_the_next_day_summary(digg):
    n = sn.create("digg-demo", "Site safety", "Hard hats on site from Monday.", must_read=True)
    await sn.send("digg-demo", n["id"])
    sn.record_status("wamid.1", "read")
    assert sn.is_ack("Got it ✅") and sn.is_ack("ok thanks") and sn.is_ack("👍")
    assert not sn.is_ack("ok but which site?") and not sn.is_ack("Belladonna")
    assert sn.acknowledge("digg-demo", "0827000000", n["id"])            # Judy, typed "got it"
    t = sn.tally(sn.recipients("digg-demo", n["id"]))
    assert t["acknowledged"] == 1 and t["read"] >= 1
    assert set(t["not_acknowledged"]) == {"Ian Downing", "Nelitho Mbeki"}
    digg.sends.clear(); digg.templates.clear()
    res = await sn.remind_and_summarise("digg-demo", sn.get("digg-demo", n["id"]))
    assert res["reminded"] == 2                                          # not Judy
    summary = [s for s in digg.sends if s[2] and s[2].startswith("notice_summary")]
    assert summary and "1 of 3" in summary[0][1] and "Nelitho Mbeki" in summary[0][1]
    assert (await sn.remind_and_summarise("digg-demo", sn.get("digg-demo", n["id"])))["skipped"]


@pytest.mark.asyncio
async def test_got_it_on_whatsapp_closes_the_question(monkeypatch):
    from vula.api import whatsapp as wa
    from vula import open_questions as oq
    acked, closed = [], []
    monkeypatch.setattr(sn, "acknowledge", lambda t, p, ref: acked.append(ref) or True)
    monkeypatch.setattr(oq, "close", lambda qid, answer="", status="answered": closed.append(qid))
    monkeypatch.setattr(wa, "_send_reply", AsyncMock(return_value=True))
    q = {"id": "q1", "kind": "staff_notice", "ref_id": "n1"}
    assert await wa._try_open_question("digg-demo", "27827000000", "Got it ✅", q)
    assert acked == ["n1"] and closed == ["q1"]
    assert not await wa._try_open_question("digg-demo", "27827000000", "Which site is this for?", q)


# ── birthdays and public holidays ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_birthday_wish_and_a_team_note_once(digg):
    digg.db["vula_tenant_config"][0]["staff_notices"] = {"birthdays": True}
    made = await sn.daily_pass("digg-demo", date(2026, 10, 21))
    assert made == ["Birthday: Judy Smith 2026-10-21"]
    wish = [t for t in digg.templates if t[0] == "27827000000"]
    assert wish and wish[0][1] == "digg_demo_staff_day" and wish[0][2][0] == "Judy"
    team = [s for s in digg.sends if s[0] == "27645755210"]               # Ian, in his 24 hours
    assert team and "it's Judy's birthday today" in team[0][1]
    assert await sn.daily_pass("digg-demo", date(2026, 10, 21)) == []   # not twice


@pytest.mark.asyncio
async def test_no_team_note_when_they_would_rather_not_and_opt_out_skips(digg):
    digg.db["vula_tenant_config"][0]["staff_notices"] = {"birthdays": True}
    digg.db["vula_team_members"][1]["birthday_share"] = False
    await sn.daily_pass("digg-demo", date(2026, 10, 21))
    assert not [s for s in digg.sends if s[0] == "27645755210"]
    digg.templates.clear()
    # Nelitho (29 Feb, opted out) gets nothing on 28 Feb of a non-leap year
    assert await sn.daily_pass("digg-demo", date(2027, 2, 28)) == []
    assert sn._birthday_today({"birthday": "02-29"}, date(2027, 2, 28))


@pytest.mark.asyncio
async def test_the_day_before_a_public_holiday(digg):
    digg.db["vula_tenant_config"][0]["staff_notices"] = {"holidays": True, "holiday_note": "The office is closed"}
    made = await sn.daily_pass("digg-demo", date(2026, 12, 15))
    assert made == ["Public holiday 2026-12-16"]
    judy = [t for t in digg.templates if t[0] == "27827000000"][0]
    assert judy[2][1] == ("a reminder that tomorrow (Wed 16 Dec) is Day of Reconciliation, a public "
                          "holiday. The office is closed.")
    assert not [t for t in digg.templates if t[0] == "27831111111"]     # Nelitho opted out
    assert await sn.daily_pass("digg-demo", date(2026, 12, 15)) == []
    assert await sn.daily_pass("digg-demo", date(2026, 12, 25)) == []    # 26 Dec is a Saturday


@pytest.mark.asyncio
async def test_scheduled_notices_go_out_when_due(digg):
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    later = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    sn.create("digg-demo", "Braai Friday", "Bring a chair.", send_at=past, audience_spec={"ids": ["m1"]})
    sn.create("digg-demo", "Later", "Not yet.", send_at=later, audience_spec={"ids": ["m1"]})
    out = await sn.run_due()
    assert out["sent"] == 1 and len(digg.sends) == 1 and "Braai Friday" in digg.sends[0][1]


# ── on WhatsApp: "tell all staff …" → preview → Confirm → sent, read back ─────

@pytest.mark.asyncio
async def test_owner_tells_all_staff_from_whatsapp(digg):
    from core.skills.commerce_admin import CommerceAdminSkill
    from core.skills.base import looks_like_owner_admin_question
    assert looks_like_owner_admin_question("Tell all staff the office is closed on Friday")
    skill = CommerceAdminSkill()
    ctx = {"tenant_id": "digg-demo", "phone": "27645755210"}
    args = {"message": "The office is closed on Friday for the year-end function."}
    prev = await skill._staff_notice("digg-demo", args, ctx)
    assert prev["preview"] and prev["count"] == 2                        # not Ian himself
    assert "Judy, Nelitho" in prev["message"] and "year-end function" in prev["message"]
    assert not digg.templates and not digg.sends
    done = await skill._staff_notice("digg-demo", {**args, "confirm": True}, ctx)
    assert done["sent"] == 2 and done["reply_verbatim"].startswith("✅ Sent to 2 of 2 staff")


def test_staff_notice_is_a_confirm_first_tool():
    from core import dry_run
    from core.skills.commerce_admin import TOOL_SPECS
    assert "staff_notice" in dry_run.PREVIEW_UNLESS_CONFIRMED
    assert any(t["function"]["name"] == "staff_notice" for t in TOOL_SPECS)


def test_staff_templates_carry_a_got_it_button():
    from vula.commerce import wa_templates as wt
    names = {t["name"]: t for t in wt.staff_templates("digg-demo")}
    assert set(names) == {"digg_demo_staff_notice", "digg_demo_staff_notice_image", "digg_demo_staff_day",
                          "digg_demo_staff_poll_invite"}
    assert names["digg_demo_staff_notice"]["buttons"] == [{"type": "QUICK_REPLY", "text": "Got it ✅"}]
    assert names["digg_demo_staff_notice"]["category"] == "UTILITY"
    assert names["digg_demo_staff_notice_image"]["header_type"] == "IMAGE"


def test_a_bookkeeper_reply_reaches_the_admin_agent_not_the_shop():
    from vula.api import whatsapp as wa
    assert "bookkeeper" in wa._ADMIN_AGENT_ROLES


def test_stop_from_a_team_member_turns_off_birthdays_and_holidays(digg, monkeypatch):
    from vula import team_index
    monkeypatch.setattr(team_index, "member_for_phone",
                        lambda t, p: next((m for m in digg.db["vula_team_members"]
                                           if m["whatsapp"] and m["whatsapp"][-9:] == p[-9:]), None))
    assert sn.set_opt_out("digg-demo", "0827000000", True) == "Judy"
    assert digg.db["vula_team_members"][1]["notices_opt_out"] is True
    assert sn.set_opt_out("digg-demo", "0820000009", True) is None       # not on the team
