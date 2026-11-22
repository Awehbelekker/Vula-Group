"""Conversation intelligence, step 1 (22 Nov 2026, docs/plans/conversation-intelligence.md).

- Each business sets the handoff watch's times (remind after, give back after) in the Scheduling
  tab (job settings, migration 294), checked and kept in range.
- The reminder quotes the customer and carries three buttons: Send holding reply, Give back to
  Vula, Pass to someone. Only an active team member of that business can use them, each use is
  audited, and a chat that is no longer paused or waiting is never acted on twice.
- A mixed-language message (English with isiXhosa or isiZulu words) gets the escalation check.
Made-up names and numbers."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from vula import handoff_watch
from vula.commerce import job_config

T = "digg-demo"
NOW = datetime(2026, 11, 22, 10, 0, tzinfo=timezone.utc)
JUDY = {"id": "aaaaaaaa-0000-0000-0000-000000000001", "name": "Judy", "whatsapp": "27820000001",
        "email": "judy@example.com"}
SIPHO = {"id": "aaaaaaaa-0000-0000-0000-000000000002", "name": "Sipho", "whatsapp": "27820000002"}
SID = "bbbbbbbb-0000-0000-0000-000000000001"


def _session(minutes_idle=15, last_role="user", paused=True, **kw):
    return {"id": SID, "tenant_id": T, "customer_phone": "27820000009", "customer_name": "Megan",
            "last_role": last_role, "paused": paused, "last_message": "Is the 2-seater board in stock?",
            "last_at": (NOW - timedelta(minutes=minutes_idle)).isoformat(), **kw}


@pytest.fixture
def world(monkeypatch):
    from vula import team_index
    from vula.api import merchant_audit
    from vula.api import whatsapp as wa
    from vula.commerce import service
    state = {"sessions": [], "sent": [], "buttons": [], "lists": [], "paused": [], "thread": [],
             "audit": [], "updates": [], "deliver": True, "hours": []}

    async def find(tenant_id, hours=2.0):
        state["hours"].append(hours)
        return list(state["sessions"])

    async def send(to, text, tenant_id=""):
        state["sent"].append((to, text))
        return state["deliver"]

    async def creds(tenant_id, *a, **k):
        return {"phone_id": "1", "token": "t"}

    async def buttons(c, number, body, btns, image_url=None):
        state["buttons"].append((number, body, btns))
        return True

    async def lst(c, number, header, body, footer, button, sections):
        state["lists"].append((number, body, sections))
        return True

    async def paused(tenant_id, sid, flag, paused_by=None):
        state["paused"].append((sid, flag))
        return {}

    async def append(tenant_id, sid, role, content):
        state["thread"].append((sid, role, content))

    class _Q:
        def __init__(self, table):
            self.table, self.patch = table, None

        def select(self, *_):
            return self

        def update(self, patch):
            self.patch = patch
            return self

        def eq(self, *_):
            return self

        def limit(self, *_):
            return self

        def execute(self):
            from types import SimpleNamespace
            if self.patch is not None:
                state["updates"].append(self.patch)
                return SimpleNamespace(data=[])
            return SimpleNamespace(data=list(state["sessions"]))

    monkeypatch.setattr(service, "find_stale_paused_sessions", find)
    monkeypatch.setattr(service, "set_session_paused", paused)
    monkeypatch.setattr(service, "append_message", append)
    monkeypatch.setattr(service, "_client", lambda: type("C", (), {"table": lambda self, t: _Q(t)})())
    monkeypatch.setattr(wa, "_send_reply", send)
    monkeypatch.setattr(wa, "_get_tenant_wa_creds", creds)
    monkeypatch.setattr(wa, "_send_wa_buttons", buttons)
    monkeypatch.setattr(wa, "_send_wa_list", lst)
    monkeypatch.setattr(team_index, "active_members", lambda t: [dict(JUDY), dict(SIPHO)])
    monkeypatch.setattr(team_index, "member_for_phone",
                        lambda t, p: next((dict(m) for m in (JUDY, SIPHO) if m["whatsapp"] == p), None))
    monkeypatch.setattr(merchant_audit, "audit", lambda t, actor, action, **d: state["audit"].append((action, d)))
    handoff_watch._reminded.clear()
    return state


def _press(reply_id, phone=JUDY["whatsapp"]):
    return asyncio.run(handoff_watch.handle_button(T, phone, reply_id))


# ── the business's own times ─────────────────────────────────────────────────────────────────

def test_the_business_sets_its_own_times(world):
    world["sessions"] = [_session(minutes_idle=12)]
    r = asyncio.run(handoff_watch.sweep(T, helper_for=lambda s: JUDY, now=NOW,
                                        remind_after=timedelta(minutes=10), resume_after=timedelta(minutes=40)))
    assert r["reminded"] == 1 and world["hours"] == [10 / 60]
    assert "12:28" in world["buttons"][0][1]          # 09:48 UTC + 40 min = 10:28 UTC = 12:28 SAST
    world["sessions"] = [_session(minutes_idle=45)]
    r = asyncio.run(handoff_watch.sweep(T, helper_for=lambda s: JUDY, now=NOW,
                                        remind_after=timedelta(minutes=10), resume_after=timedelta(minutes=40)))
    assert r["resumed"] == 1 and world["paused"] == [(SID, False)]


def test_the_reminder_quotes_the_customer_and_carries_the_three_buttons(world):
    world["sessions"] = [_session(minutes_idle=35)]
    asyncio.run(handoff_watch.sweep(T, helper_for=lambda s: JUDY, now=NOW))
    number, body, btns = world["buttons"][0]
    assert number == JUDY["whatsapp"]
    assert body == handoff_watch.reminder_text("Megan", 35, "13:25", "Is the 2-seater board in stock?")
    assert "\"Is the 2-seater board in stock?\"" in body
    assert [b["title"] for b in btns] == ["Send holding reply", "Give back to Vula", "Pass to someone"]
    assert all(len(b["title"]) <= 20 for b in btns)        # WhatsApp's button limit
    assert all(b["id"].endswith(SID) for b in btns)


def test_the_settings_are_checked(monkeypatch):
    assert job_config.settings_for("stale_handoff_resume", None) == {
        "remind_after_minutes": 30, "resume_after_minutes": 120}
    assert job_config.settings_for("stale_handoff_resume", {"remind_after_minutes": 2})["remind_after_minutes"] == 30
    assert job_config.check_settings("stale_handoff_resume", {"remind_after_minutes": 10}) == {
        "remind_after_minutes": 10, "resume_after_minutes": 120}
    for bad in ({"remind_after_minutes": 1}, {"remind_after_minutes": "10"}, {"nope": 5},
                {"resume_after_minutes": 20}, {"remind_after_minutes": True}):
        with pytest.raises(ValueError):
            job_config.check_settings("stale_handoff_resume", bad)
    with pytest.raises(ValueError):
        job_config.check_settings("sales_summary", {"remind_after_minutes": 10})
    cfg = job_config.effective_config(T, "stale_handoff_resume", {"settings": {"resume_after_minutes": 60}})
    assert cfg["settings"]["resume_after_minutes"] == 60
    assert {f["name"] for f in cfg["settings_fields"]} == {"remind_after_minutes", "resume_after_minutes"}


def test_the_scheduling_route_saves_checked_settings(monkeypatch):
    from vula.api import commerce
    saved = {}
    monkeypatch.setattr(job_config, "get_configs", lambda t: {
        "stale_handoff_resume": {"settings": {"remind_after_minutes": 30, "resume_after_minutes": 120}}})
    monkeypatch.setattr(job_config, "upsert_config", lambda t, j, p: saved.update(p) or {"ok": True})
    asyncio.run(commerce.admin_upsert_scheduled_job(
        T, {"job_type": "stale_handoff_resume", "settings": {"remind_after_minutes": 10}}))
    assert saved["settings"] == {"remind_after_minutes": 10, "resume_after_minutes": 120}
    with pytest.raises(HTTPException) as e:
        asyncio.run(commerce.admin_upsert_scheduled_job(
            T, {"job_type": "stale_handoff_resume", "settings": {"resume_after_minutes": 20}}))
    assert e.value.status_code == 400 and "later than the reminder" in e.value.detail


# ── the buttons ──────────────────────────────────────────────────────────────────────────────

def test_only_the_team_can_use_the_buttons(world):
    world["sessions"] = [_session()]
    assert _press(f"handoff:hold:{SID}", phone="27829999999") == "Only someone on the team can do that."
    assert world["sent"] == [] and world["audit"] == []
    assert _press("handoff:hold:not a uuid; drop table") is None


def test_send_holding_reply_once(world):
    world["sessions"] = [_session()]
    msg = _press(f"handoff:hold:{SID}")
    assert msg.startswith("Sent Megan a holding reply")
    assert world["sent"] == [("27820000009", handoff_watch.HOLDING_REPLY)]
    assert world["thread"] == [(SID, "assistant", handoff_watch.HOLDING_REPLY)]
    assert world["audit"] == [("handoff.holding_reply", {"session_id": SID})]
    world["sessions"] = [_session(last_role="assistant")]          # the holding reply went last
    assert "isn't waiting" in _press(f"handoff:hold:{SID}")
    assert len(world["sent"]) == 1


def test_a_holding_reply_outside_the_24_hours_says_so(world):
    world["sessions"] = [_session()]
    world["deliver"] = False
    assert "24 hours" in _press(f"handoff:hold:{SID}")
    assert world["thread"] == [] and world["audit"] == []


def test_give_back_to_vula_resumes_and_apologises_to_a_waiting_customer(world):
    world["sessions"] = [_session()]
    assert _press(f"handoff:back:{SID}") == "Vula is answering Megan again."
    assert world["paused"] == [(SID, False)]
    assert world["sent"] == [("27820000009", handoff_watch.CUSTOMER_APOLOGY)]
    assert world["audit"][0][0] == "handoff.give_back"


def test_a_chat_no_longer_paused_is_left_alone(world):
    world["sessions"] = [_session(paused=False)]
    for action in ("hold", "back", "pass"):
        assert "already answering" in _press(f"handoff:{action}:{SID}")
    assert world["sent"] == [] and world["paused"] == [] and world["lists"] == []


def test_pass_to_someone_lists_the_rest_of_the_team_then_assigns(world):
    world["sessions"] = [_session()]
    assert _press(f"handoff:pass:{SID}") is None
    number, body, sections = world["lists"][0]
    rows = sections[0]["rows"]
    assert number == JUDY["whatsapp"] and [r["title"] for r in rows] == ["Sipho"]      # not herself
    assert _press(rows[0]["id"]) == "Passed to Sipho."
    assert world["updates"][0]["assigned_to"] == "Sipho"
    to, text = world["sent"][-1]
    assert to == SIPHO["whatsapp"] and "Judy passed you the chat with Megan" in text
    assert world["audit"][-1] == ("handoff.pass", {"session_id": SID, "to_member": SIPHO["id"]})


def test_pass_to_someone_who_left_the_team_is_refused(world):
    world["sessions"] = [_session()]
    assert "isn't on the team" in _press(f"handoff:to:{SID}:aaaaaaaa-0000-0000-0000-00000000dead")
    assert world["updates"] == []


def test_whatsapp_routes_the_buttons_here():
    import inspect
    from vula.api import whatsapp
    src = inspect.getsource(whatsapp)
    assert 'reply_id.startswith("handoff:")' in src and "_hw.handle_button(route_tenant, phone, reply_id)" in src


# ── mixed-language messages ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "I've been waiting all day for the board, uxolo but this is not okay",   # English + isiXhosa
    "Hi I want to speak to the manager, ngicela uMphathi",                    # English + isiZulu
    "Molo, the order hasn't come yet and it's late",                         # isiXhosa greeting
    "Sawubona, my delivery is wrong",                                         # isiZulu greeting
])
def test_a_code_switched_message_gets_the_check(monkeypatch, text):
    from config import settings
    from vula import escalation
    monkeypatch.setattr(settings, "escalation_classifier_enabled", True)
    assert escalation.needs_classifier(text)


@pytest.mark.parametrize("text", [
    "Is the 2-seater board in stock?", "Ha ha thanks, the order is fine", "Ek wil asseblief bestel",
])
def test_english_and_afrikaans_alone_stay_on_the_word_lists(monkeypatch, text):
    from config import settings
    from vula import escalation
    monkeypatch.setattr(settings, "escalation_classifier_enabled", True)
    assert not escalation.needs_classifier(text)
