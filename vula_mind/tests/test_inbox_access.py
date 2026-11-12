"""Inbox: chats assigned to real team members; someone without Inbox access sees only theirs."""
import asyncio

import pytest
from fastapi import HTTPException

from vula.api import commerce as api

OWNER = {"full": True, "access": [], "member": {"id": "o", "name": "Ian", "whatsapp": "27820000001"}}
INBOX = {"full": False, "access": ["inbox"], "member": {"id": "i", "name": "Desk", "whatsapp": "27820000005"}}
REP = {"full": False, "access": ["rep-chats", "rep-contacts"], "member": {"id": "t", "name": "Thabo", "whatsapp": "27820000002"}}
TEAM = [{"id": "o", "name": "Ian", "whatsapp": "27820000001", "notify": []},
        {"id": "t", "name": "Thabo", "whatsapp": "27820000002", "notify": ["chat_assigned"]},
        {"id": "l", "name": "Lindi", "whatsapp": "27820000003", "notify": []}]


class _Req:
    def __init__(self, token="Bearer x"):
        self.headers = {"authorization": token}


class _Q:
    def __init__(self, store, name):
        self.store, self.name, self.f, self.op, self.payload = store, name, [], "select", None

    def select(self, *_): return self
    def order(self, *a, **k): return self
    def limit(self, *_): return self
    def update(self, row): self.op, self.payload = "update", row; return self
    def eq(self, k, v): self.f.append(lambda r: r.get(k) == v); return self

    def execute(self):
        rows = [r for r in self.store.get(self.name, []) if all(f(r) for f in self.f)]
        if self.op == "update":
            for r in rows:
                r.update(self.payload)
        return type("R", (), {"data": rows})()


@pytest.fixture
def world(monkeypatch):
    store = {"commerce_conversation_sessions": [
        {"id": "s1", "tenant_id": "t1", "customer_name": "Acme", "customer_phone": "2783", "assigned_member_id": "t"},
        {"id": "s2", "tenant_id": "t1", "customer_name": "Bongi", "customer_phone": "2784", "assigned_member_id": None},
    ]}
    client = type("C", (), {"table": lambda s, n: _Q(store, n)})()
    monkeypatch.setattr(api.service, "_client", lambda: client)
    who = {"scope": OWNER}

    async def member_scope(auth, tid):
        return who["scope"]
    import vula.api.tenant_auth as ta
    monkeypatch.setattr(ta, "member_scope", member_scope)
    import vula.team_index as team_index
    monkeypatch.setattr(team_index, "active_members", lambda tid: TEAM)
    sent = []

    async def send(phone, text, tid, idem_key=None):
        sent.append((phone, text))
        return True
    import vula.api.whatsapp as wa
    monkeypatch.setattr(wa, "_send_reply", send)
    return store, who, sent


def _list(world_who, scope):
    world_who["scope"] = scope
    return asyncio.run(api.admin_list_conversations("t1", _Req(), limit=50))


def test_owner_and_inbox_users_see_every_chat(world):
    store, who, _ = world
    assert {c["id"] for c in _list(who, OWNER)["conversations"]} == {"s1", "s2"}
    assert _list(who, INBOX)["mine_only"] is False


def test_a_rep_sees_only_chats_assigned_to_them(world):
    store, who, _ = world
    out = _list(who, REP)
    assert out["mine_only"] is True and [c["id"] for c in out["conversations"]] == ["s1"]


def test_a_rep_cannot_open_someone_elses_chat(world):
    store, who, _ = world
    who["scope"] = REP
    asyncio.run(api._require_chat(_Req(), "t1", "s1"))               # theirs: fine
    with pytest.raises(HTTPException) as e:
        asyncio.run(api._require_chat(_Req(), "t1", "s2"))
    assert e.value.status_code == 403


def test_internal_callers_without_a_login_are_unchanged(world):
    assert asyncio.run(api._inbox_access(_Req(""), "t1")) == (True, None)


def test_assigning_to_a_team_member_sets_the_name_and_tells_them_if_they_asked(world):
    store, who, sent = world
    who["scope"] = OWNER
    out = asyncio.run(api.admin_conv_meta("t1", "s2", api.ConvMetaRequest(assigned_member_id="t"), _Req()))
    row = store["commerce_conversation_sessions"][1]
    assert (row["assigned_member_id"], row["assigned_to"]) == ("t", "Thabo") and out["assigned_to"] == "Thabo"
    assert sent and sent[0][0] == "27820000002" and "assigned you the chat with Bongi" in sent[0][1]


def test_no_notice_unless_they_ticked_it(world):
    store, who, sent = world
    asyncio.run(api.admin_conv_meta("t1", "s2", api.ConvMetaRequest(assigned_member_id="l"), _Req()))
    assert store["commerce_conversation_sessions"][1]["assigned_to"] == "Lindi" and sent == []


def test_unassign_and_bad_member(world):
    store, who, _ = world
    asyncio.run(api.admin_conv_meta("t1", "s1", api.ConvMetaRequest(assigned_member_id=""), _Req()))
    assert store["commerce_conversation_sessions"][0]["assigned_member_id"] is None
    with pytest.raises(HTTPException) as e:
        asyncio.run(api.admin_conv_meta("t1", "s1", api.ConvMetaRequest(assigned_member_id="nobody"), _Req()))
    assert e.value.status_code == 400


def test_a_rep_cannot_reassign(world):
    store, who, _ = world
    who["scope"] = REP
    with pytest.raises(HTTPException) as e:
        asyncio.run(api.admin_conv_meta("t1", "s1", api.ConvMetaRequest(assigned_member_id="l"), _Req()))
    assert e.value.status_code == 403
    out = asyncio.run(api.admin_conv_meta("t1", "s1", api.ConvMetaRequest(agent_note="called back"), _Req()))
    assert out["agent_note"] == "called back"                       # a note on their own chat is fine
