"""Gap audit 1.4: a chat the team took over can't leave the customer waiting.

- 30 min with the customer waiting: the person handling it is reminded once;
- 2 h of silence: they're told, the bot resumes, and a waiting customer gets an apology;
- the resume never depends on any message going out."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from vula import handoff_watch

NOW = datetime(2026, 10, 27, 14, 0, tzinfo=timezone.utc)
HELPER = {"name": "Judy", "whatsapp": "27820000001"}


def _session(sid, minutes_idle, last_role="user", **kw):
    return {"id": sid, "customer_phone": "27820000009", "customer_name": "Rudi",
            "last_role": last_role, "last_at": (NOW - timedelta(minutes=minutes_idle)).isoformat(), **kw}


@pytest.fixture
def world(monkeypatch):
    from vula.api import whatsapp as wa
    from vula.commerce import service
    state = {"sessions": [], "sent": [], "resumed": [], "thread": [], "deliver": True}

    async def find(tenant_id, hours=2.0):
        assert hours == 0.5
        return list(state["sessions"])

    async def send(to, text, tenant_id=""):
        state["sent"].append((to, text))
        return state["deliver"]

    async def paused(tenant_id, sid, flag, paused_by=None):
        state["resumed"].append((sid, flag))
        return {}

    async def append(tenant_id, sid, role, content):
        state["thread"].append((sid, role, content))

    monkeypatch.setattr(service, "find_stale_paused_sessions", find)
    monkeypatch.setattr(service, "set_session_paused", paused)
    monkeypatch.setattr(service, "append_message", append)
    monkeypatch.setattr(wa, "_send_reply", send)
    handoff_watch._reminded.clear()
    return state


def _sweep(helper=HELPER):
    return asyncio.run(handoff_watch.sweep("digg-demo", helper_for=lambda s: helper, now=NOW))


def test_reminder_once_after_30_minutes_when_the_customer_is_waiting(world):
    world["sessions"] = [_session("s1", 35)]
    assert _sweep()["reminded"] == 1
    to, text = world["sent"][0]
    assert to == HELPER["whatsapp"] and "Rudi has been waiting 35 min" in text
    assert "17:25" in text                 # 13:25 UTC + 2 h = 15:25 UTC = 17:25 SAST
    assert world["resumed"] == []
    assert _sweep()["reminded"] == 0       # once per waiting message
    assert len(world["sent"]) == 1


def test_no_reminder_when_the_team_spoke_last(world):
    world["sessions"] = [_session("s1", 45, last_role="agent")]
    assert _sweep() == {"reminded": 0, "resumed": 0, "apologised": 0}
    assert world["sent"] == []


def test_after_2_hours_resume_and_apologise_to_a_waiting_customer(world):
    world["sessions"] = [_session("s1", 130)]
    assert _sweep() == {"reminded": 0, "resumed": 1, "apologised": 1}
    assert world["resumed"] == [("s1", False)]
    assert world["sent"][0][0] == HELPER["whatsapp"] and "resumed answering" in world["sent"][0][1]
    assert world["sent"][1] == ("27820000009", handoff_watch.CUSTOMER_APOLOGY)
    assert world["thread"] == [("s1", "assistant", handoff_watch.CUSTOMER_APOLOGY)]


def test_no_apology_when_the_team_answered_last(world):
    world["sessions"] = [_session("s1", 180, last_role="agent")]
    assert _sweep()["apologised"] == 0
    assert world["resumed"] == [("s1", False)]
    assert all(to != "27820000009" for to, _ in world["sent"])


def test_resume_happens_even_if_nothing_can_be_sent(world):
    world["sessions"] = [_session("s1", 300)]
    world["deliver"] = False                # outside WhatsApp's 24-hour window
    out = _sweep(helper=None)
    assert out["resumed"] == 1 and out["apologised"] == 0
    assert world["resumed"] == [("s1", False)] and world["thread"] == []


def test_the_scheduler_uses_the_sweep():
    import inspect
    import vula.api.server as server
    src = inspect.getsource(server._stale_handoff_scheduler_loop)
    assert "handoff_watch.sweep(" in src
