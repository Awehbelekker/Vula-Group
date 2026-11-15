"""The dashboard "Chat to Vula" knows who is signed in: it uses that member's own thread, tells
the agent their name and role, and gives the agent only the tools their access allows. It used to
run anonymous, with every tool, on a session id the browser made up on each page load."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from core.skills import commerce_admin as ca
from core.skills.base import SkillOutput
from tests.fake_supabase import FakeSupabase
from vula import identity
from vula.api import commerce
from vula.commerce import service

T = "deli"
REP = identity.Person(T, member_id="m-rep", name="Thandi", role="staff", access=["orders"], phone="27821112222")
OWNER = identity.Person(T, member_id="m-own", name="Ian", role="owner", phone="27835550000")


def _req():
    r = MagicMock()
    r.headers = {"authorization": "Bearer tok"}
    return r


class _Skill:
    def __init__(self):
        self.seen = None

    async def __call__(self, inp):
        self.seen = inp
        return SkillOutput(answer="Done.", skill_name="commerce_admin")


async def _chat(person, db, message="what were today's sales?", session_id="browser-made-this-up"):
    skill = _Skill()
    with patch.object(service, "_client", return_value=db), \
         patch("vula.identity.person_for_login", AsyncMock(return_value=person)), \
         patch("core.skills.loader.get_skill", return_value=skill):
        out = await commerce.admin_assistant(T, commerce.AssistantRequest(message=message, session_id=session_id), _req())
    return out, skill.seen


@pytest.mark.asyncio
async def test_signed_in_member_gets_their_own_thread_name_role_and_access():
    db = FakeSupabase()
    out, seen = await _chat(REP, db)
    assert out["ok"] is True
    md = seen.metadata
    assert md["session_id"] == "webadmin:m:m-rep"          # the browser's id is ignored
    assert (md["caller_name"], md["caller_role"], md["access"], md["channel"]) == ("Thandi", "staff", ["orders"], "dashboard")
    assert "customer_phone" not in md
    assert [s["session_key"] for s in db.rows["commerce_conversation_sessions"]] == ["webadmin:m:m-rep"]


@pytest.mark.asyncio
async def test_the_thread_is_remembered_and_labelled_with_their_name():
    db = FakeSupabase()
    await _chat(REP, db, message="remember I do the Tuesday stock count")
    _, seen = await _chat(REP, db, message="what did I just say?", session_id="a-different-page-load")
    assert "Thandi" in seen.conversation_history and "Tuesday stock count" in seen.conversation_history
    assert "Customer" not in seen.conversation_history
    assert len(db.rows["commerce_conversation_sessions"]) == 1


@pytest.mark.asyncio
async def test_owner_has_full_access_and_a_different_thread():
    db = FakeSupabase()
    _, seen = await _chat(OWNER, db)
    assert seen.metadata["access"] is None and seen.metadata["session_id"] == "webadmin:m:m-own"


@pytest.mark.asyncio
async def test_a_rep_keeps_their_own_scoped_tools():
    rep = identity.Person(T, member_id="m-rep", name="Thandi", role="sales_rep", access=["crm"], phone="27821112222")
    _, seen = await _chat(rep, FakeSupabase())
    assert seen.metadata["caller_role"] == "sales_rep" and seen.metadata["customer_phone"] == "27821112222"


@pytest.mark.asyncio
async def test_a_member_with_no_access_at_all_is_refused():
    inactive = identity.Person(T, member_id="m-x", name="Gone", role="staff", access=[])
    with pytest.raises(HTTPException) as e:
        await _chat(inactive, FakeSupabase())
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_a_caller_with_no_login_keeps_the_old_browser_session():
    _, seen = await _chat(None, FakeSupabase(), session_id="abc")
    assert seen.metadata["session_id"] == "webadmin:abc" and "access" not in seen.metadata


@pytest.mark.asyncio
async def test_history_endpoint_returns_only_the_callers_own_thread():
    db = FakeSupabase()
    await _chat(REP, db, message="mine")
    await _chat(OWNER, db, message="the owner's")
    with patch.object(service, "_client", return_value=db), \
         patch("vula.identity.person_for_login", AsyncMock(return_value=REP)):
        out = await commerce.admin_assistant_history(T, _req(), limit=50)
    assert [m["content"] for m in out["messages"]] == ["mine", "Done."]
    with patch.object(service, "_client", return_value=db), \
         patch("vula.identity.person_for_login", AsyncMock(return_value=None)):
        # no login: no messages — only whether "Ask anything" is on for the business (13 Nov)
        assert (await commerce.admin_assistant_history(T, _req(), limit=50)) == {"messages": [], "open_chat": False}


# ── the agent honours that access ───────────────────────────────────────────────────────────

def test_access_passed_in_the_context_wins_over_no_phone_means_full():
    assert ca._ctx_access(T, {"phone": None, "access": ["orders"]}) == ["orders"]
    assert ca._ctx_access(T, {"phone": None, "access": None}) is None          # full member
    assert ca._ctx_access(T, {"phone": None}) is None                          # no login info: unchanged
    with patch.object(ca, "_member_access", return_value=["crm"]) as by_phone:
        assert ca._ctx_access(T, {"phone": "27821112222"}) == ["crm"]          # WhatsApp: unchanged
    by_phone.assert_called_once_with(T, "27821112222")


def test_a_restricted_dashboard_member_is_not_offered_money_tools():
    tools = ca._tools_for(T, role="staff", message="send invoice 104")
    money = {n for n, scope in ca._TOOL_SCOPE.items() if scope not in (None, "orders")}
    offered = {t["function"]["name"] for t in ca._restrict_to_access(tools, ca._ctx_access(T, {"access": ["orders"]}))}
    assert offered and not (offered & money)
    everything = {t["function"]["name"] for t in ca._restrict_to_access(tools, ca._ctx_access(T, {"access": None}))}
    assert offered < everything


# ── continuity beyond the 24-hour window ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_earlier_topics_are_their_own_older_messages_cut_short_and_marked_old():
    from datetime import datetime, timedelta, timezone
    from config import settings

    def ago(**kw):
        return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat()

    db = FakeSupabase({"commerce_conversation_messages": [
        {"tenant_id": T, "session_id": "s1", "role": "user", "content": "help me plan the December roster " + "x" * 200, "created_at": ago(days=3)},
        {"tenant_id": T, "session_id": "s1", "role": "assistant", "content": "Sales were R9,999.00", "created_at": ago(days=3)},
        {"tenant_id": T, "session_id": "s1", "role": "user", "content": "today's question", "created_at": ago(hours=2)},
        {"tenant_id": T, "session_id": "s1", "role": "user", "content": "too old to matter", "created_at": ago(days=20)},
        {"tenant_id": T, "session_id": "s2", "role": "user", "content": "someone else's thread", "created_at": ago(days=3)},
    ]})
    with patch.object(service, "_client", return_value=db):
        assert await service.earlier_topics(T, "s1") == ""                 # off by default
        with patch.object(settings, "history_earlier_topics_enabled", True):
            block = await service.earlier_topics(T, "s1")
            assert await service.earlier_topics(T, "nothing-here") == ""
    assert "help me plan the December roster" in block and block.count("\n- ") == 1
    assert "NOT a request to act on now" in block and "…" in block
    for absent in ("R9,999.00", "today's question", "too old", "someone else"):
        assert absent not in block
