"""A staff member's own thread with Vula is private to them: not in the shared inbox, not
openable by a colleague or the owner, not shown to the platform operator, and never quoted in the
daily conversation check. Customer conversations are unaffected."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from tests.fake_supabase import FakeSupabase
from vula import conversation_check, identity
from vula.api import commerce, master
from vula.commerce import service

T = "deli"
REP = identity.Person(T, member_id="m-rep", name="Thandi", role="sales_rep", access=["crm"], phone="27821112222")
OWNER = identity.Person(T, member_id="m-own", name="Ian", role="owner", phone="27835550000")


def _db():
    return FakeSupabase({
        "commerce_conversation_sessions": [
            {"id": "s-cust", "tenant_id": T, "session_key": "27820000001", "customer_phone": "27820000001",
             "updated_at": "2026-11-12T10:00:00Z"},
            {"id": "s-rep-wa", "tenant_id": T, "session_key": "admin:27821112222",
             "customer_phone": "27821112222", "updated_at": "2026-11-12T11:00:00Z"},
            {"id": "s-rep-web", "tenant_id": T, "session_key": "webadmin:m:m-rep", "updated_at": "2026-11-12T12:00:00Z"},
            {"id": "s-other-tenant", "tenant_id": "other", "session_key": "27820000002",
             "updated_at": "2026-11-12T12:00:00Z"},
        ],
        "commerce_conversation_messages": [
            {"id": "m1", "tenant_id": T, "session_id": "s-cust", "role": "user", "content": "do you deliver?",
             "created_at": "2026-11-12T10:00:00Z"},
            {"id": "m2", "tenant_id": T, "session_id": "s-rep-wa", "role": "user",
             "content": "I'm thinking of leaving, help me plan", "created_at": "2026-11-12T11:00:00Z"},
        ],
    })


def _req(token: str = "tok"):
    r = MagicMock()
    r.headers = {"authorization": f"Bearer {token}"} if token else {}
    return r


def _as(person):
    return patch("vula.identity.person_for_login", AsyncMock(return_value=person))


@pytest.mark.asyncio
async def test_inbox_lists_customers_only():
    with patch.object(service, "_client", return_value=_db()):
        out = await commerce.admin_list_conversations(T, limit=50)
    assert [c["session_key"] for c in out["conversations"]] == ["27820000001"]


@pytest.mark.asyncio
async def test_inbox_list_is_not_shortened_by_the_hidden_threads():
    db = _db()
    db.rows["commerce_conversation_sessions"] += [
        {"id": f"c{i}", "tenant_id": T, "session_key": f"2782000{i:04d}", "updated_at": f"2026-11-11T{i:02d}:00:00Z"}
        for i in range(10)]
    with patch.object(service, "_client", return_value=db):
        out = await commerce.admin_list_conversations(T, limit=5)
    assert len(out["conversations"]) == 5
    assert not any(identity.is_staff_session_key(c["session_key"]) for c in out["conversations"])


@pytest.mark.asyncio
@pytest.mark.parametrize("sid", ["s-rep-wa", "s-rep-web"])
async def test_a_colleague_and_the_owner_cannot_open_a_staff_thread(sid):
    with patch.object(service, "_client", return_value=_db()), \
         patch("vula.api.command_centre.audit_conversation_view", AsyncMock()):
        for someone_else in (OWNER, None):
            with _as(someone_else), pytest.raises(HTTPException) as e:
                await commerce.admin_get_thread(T, sid, _req())
            assert e.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("sid", ["s-rep-wa", "s-rep-web"])
async def test_the_member_can_open_their_own_thread(sid):
    with patch.object(service, "_client", return_value=_db()), _as(REP), \
         patch("vula.api.command_centre.audit_conversation_view", AsyncMock()):
        thread = await commerce.admin_get_thread(T, sid, _req())
    assert thread["session_id"] == sid


@pytest.mark.asyncio
async def test_customer_threads_still_open_for_everyone():
    with patch.object(service, "_client", return_value=_db()), _as(OWNER), \
         patch("vula.api.command_centre.audit_conversation_view", AsyncMock()):
        thread = await commerce.admin_get_thread(T, "s-cust", _req())
    assert thread["session_id"] == "s-cust"


@pytest.mark.asyncio
async def test_a_colleague_cannot_pause_reply_tag_or_rate_a_staff_thread():
    db = _db()
    send = AsyncMock(return_value=True)
    with patch.object(service, "_client", return_value=db), _as(OWNER), \
         patch("vula.api.whatsapp._send_reply", send):
        for call in (
            lambda: commerce.admin_handoff(T, "s-rep-wa", commerce.HandoffRequest(paused=True), _req()),
            lambda: commerce.admin_reply(T, "s-rep-wa", commerce.AgentReplyRequest(message="hi"), _req()),
            lambda: commerce.admin_conv_meta(T, "s-rep-wa", commerce.ConvMetaRequest(agent_note="x"), _req()),
            lambda: commerce.admin_reply_feedback(T, "s-rep-wa", commerce.ReplyFeedback(
                message_id="m2", rating="down"), _req()),
        ):
            with pytest.raises(HTTPException) as e:
                await call()
            assert e.value.status_code == 404
    send.assert_not_awaited()
    assert db.count("vula_reply_feedback") == 0
    assert not db.rows["commerce_conversation_sessions"][1].get("agent_note")


# ── the platform operator ───────────────────────────────────────────────────────────────────

def _chat_threads():
    return [{"phone": "27820000001", "last_message": "hi"}, {"phone": "27821112222", "last_message": "private"},
            {"phone": "0821112222:proj-1", "last_message": "private too"}]


@pytest.mark.asyncio
async def test_operator_sees_customer_threads_only():
    chat = MagicMock()
    chat.list_threads.return_value = _chat_threads()
    with patch("vula.chat.history.get_db", return_value=chat), \
         patch("vula.identity.staff_phone_digits", return_value={"27821112222"}):
        out = await master.master_tenant_conversations(T, limit=30)
        assert [t["phone"] for t in out["threads"]] == ["27820000001"]
        for phone in ("27821112222", "0821112222:proj-1"):
            with pytest.raises(HTTPException) as e:
                await master.master_tenant_conversation_messages(T, phone)
            assert e.value.status_code == 404
    chat.get.assert_not_called()


@pytest.mark.asyncio
async def test_operator_view_refuses_rather_than_show_everything_when_the_team_cant_be_read():
    chat = MagicMock()
    chat.list_threads.return_value = _chat_threads()
    with patch("vula.chat.history.get_db", return_value=chat), \
         patch("vula.identity.staff_phone_digits", side_effect=RuntimeError("db down")):
        with pytest.raises(HTTPException) as e:
            await master.master_tenant_conversations(T, limit=30)
    assert e.value.status_code == 503


def test_staff_numbers_come_from_the_team_and_registered_staff_phones():
    db = FakeSupabase({
        "vula_team_members": [{"tenant_id": T, "whatsapp": "082 111 2222"}, {"tenant_id": "other", "whatsapp": "0830000000"}],
        "vula_tenant_phones": [{"tenant_id": T, "phone": "27835550000", "role": "admin"},
                               {"tenant_id": T, "phone": "27820000001", "role": "viewer"}],
    })
    with patch.object(service, "_client", return_value=db):
        assert identity.staff_phone_digits(T) == {"27821112222", "27835550000"}


# ── the daily conversation check ────────────────────────────────────────────────────────────

def _check_msgs():
    day = "2026-11-11T08:00:0"
    return [
        {"tenant_id": T, "session_id": "s-cust", "role": "user", "content": "do you deliver to Paarl?", "created_at": day + "0Z"},
        {"tenant_id": T, "session_id": "s-cust", "role": "assistant", "content": "I couldn't find that.", "created_at": day + "1Z"},
        {"tenant_id": T, "session_id": "s-rep-wa", "role": "user", "content": "my private worry", "created_at": day + "2Z"},
        {"tenant_id": T, "session_id": "s-rep-wa", "role": "assistant", "content": "I couldn't find that.", "created_at": day + "3Z"},
    ]


def test_daily_check_counts_staff_threads_but_never_quotes_them():
    with patch.object(conversation_check, "_messages", return_value=_check_msgs()), \
         patch.object(conversation_check, "_escalations", return_value={}), \
         patch.object(conversation_check, "_client", return_value=_db()):
        d = conversation_check.build()
    t = d["tenants"][0]
    assert t["counts"]["cant_answer"] == 2                       # both are counted
    quoted = [e["question"] for e in t["examples"]["cant_answer"]]
    assert quoted == ["do you deliver to Paarl?"]                 # only the customer's is quoted
    assert "my private worry" not in conversation_check.render_text(d)


def test_daily_check_quotes_nothing_when_it_cant_tell_whose_thread_it_is():
    db = _db()
    db.fail.add("commerce_conversation_sessions")
    with patch.object(conversation_check, "_messages", return_value=_check_msgs()), \
         patch.object(conversation_check, "_escalations", return_value={}), \
         patch.object(conversation_check, "_client", return_value=db):
        d = conversation_check.build()
    assert d["tenants"][0]["counts"]["cant_answer"] == 2
    assert d["tenants"][0]["examples"] == {"cant_answer": []}
