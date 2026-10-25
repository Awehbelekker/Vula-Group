"""A business's WhatsApp Business app number shared with Vula — Meta's coexistence (25 Oct 2026).

Ian: tenants should keep the WhatsApp number they already use and barely touch the Meta
dashboard. The owner links the app's number through Embedded Signup and keeps the app on their
phone; Vula answers on the same number. So:
- connecting never re-registers the number (that would take it off the owner's phone), and asks
  Meta for the app's contacts and shared history;
- the owner's own replies from the phone go into the chat, and Vula goes quiet there;
- shared history lands in each chat's thread; contacts name chats that have no name;
- a knowledge line also stays quiet in a chat someone took over.
"""
from types import SimpleNamespace

import pytest

from config import settings
from vula.api import whatsapp, whatsapp_coexist as cx, whatsapp_connect

SHOP = "off-the-hook"
CUSTOMER = "27820001111"


class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.f, self.op, self.payload = db, table, [], "select", None

    def select(self, *_a, **_k): return self
    def limit(self, *_a): return self
    def order(self, *_a, **_k): return self

    def eq(self, k, v):
        self.f.append((k, v))
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def upsert(self, payload, on_conflict=None):
        self.op, self.payload = "upsert", payload
        return self

    def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        if self.op in ("insert", "upsert"):
            if self.table in self.db.refuse:
                raise RuntimeError(self.db.refuse[self.table])
            new = self.payload if isinstance(self.payload, list) else [self.payload]
            rows.extend(dict(r) for r in new)
            return SimpleNamespace(data=new)
        hit = [r for r in rows if all(r.get(k) == v for k, v in self.f)]
        if self.op == "update":
            for r in hit:
                r.update(self.payload)
        return SimpleNamespace(data=hit)


class _DB:
    def __init__(self):
        self.tables, self.refuse = {}, {}

    def table(self, name):
        return _Q(self, name)


@pytest.fixture()
def db(monkeypatch):
    d = _DB()
    monkeypatch.setattr("vula.commerce.service._client", lambda: d)
    monkeypatch.setattr(whatsapp_connect, "_supabase", lambda: d)
    seen = set()

    def claim(key):                         # the durable dedup: a key is taken once
        if key in seen:
            return False
        seen.add(key)
        return True
    monkeypatch.setattr(whatsapp, "_durable_dedup_claim", claim)
    sessions = {}

    async def get_or_create_session(tenant_id, session_key, channel="whatsapp", customer_phone=None):
        key = (tenant_id, session_key)
        if key not in sessions:
            row = {"id": f"s-{session_key}", "tenant_id": tenant_id, "session_key": session_key,
                   "paused": False, "customer_name": None}
            sessions[key] = row
            d.tables.setdefault("commerce_conversation_sessions", []).append(row)
        return sessions[key]

    async def append_message(tenant_id, session_id, role, content):
        d.tables.setdefault("commerce_conversation_messages", []).append(
            {"tenant_id": tenant_id, "session_id": session_id, "role": role, "content": content})

    async def set_session_paused(tenant_id, session_id, paused, paused_by=None):
        for r in d.tables["commerce_conversation_sessions"]:
            if r["id"] == session_id:
                r.update(paused=paused, paused_by=paused_by)
        return {}
    monkeypatch.setattr("vula.commerce.service.get_or_create_session", get_or_create_session)
    monkeypatch.setattr("vula.commerce.service.append_message", append_message)
    monkeypatch.setattr("vula.commerce.service.set_session_paused", set_session_paused)
    return d


def _msgs(db):
    return db.tables.get("commerce_conversation_messages", [])


# ── the owner replies from the app ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_owners_reply_from_the_phone_goes_in_the_chat_and_vula_steps_back(db):
    echo = {"from": "27713128891", "to": CUSTOMER, "id": "wamid.E1", "timestamp": "1761370000",
            "type": "text", "text": {"body": "Hi Thandi, I'll bring the hake myself at 5."}}
    took = await cx.handle("smb_message_echoes", SHOP, {"message_echoes": [echo]})
    assert took == 1
    assert _msgs(db) == [{"tenant_id": SHOP, "session_id": f"s-{CUSTOMER}", "role": "agent",
                          "content": "Hi Thandi, I'll bring the hake myself at 5."}]
    session = db.tables["commerce_conversation_sessions"][0]
    assert session["paused"] is True and session["paused_by"] == cx.OWNER_ON_PHONE
    # Meta resends the same echo: nothing doubles
    assert await cx.handle("smb_message_echoes", SHOP, {"message_echoes": [echo]}) == 0
    assert len(_msgs(db)) == 1


@pytest.mark.asyncio
async def test_a_photo_from_the_phone_is_noted_with_its_caption(db):
    echo = {"to": CUSTOMER, "id": "wamid.E2", "type": "image", "image": {"caption": "Today's catch"}}
    await cx.handle("smb_message_echoes", SHOP, {"message_echoes": [echo]})
    assert _msgs(db)[0]["content"] == "[photo] Today's catch"


@pytest.mark.asyncio
async def test_an_unknown_number_is_ignored(db):
    assert await cx.handle("smb_message_echoes", None, {"message_echoes": [{"to": CUSTOMER, "id": "x"}]}) == 0
    assert _msgs(db) == []


# ── shared history and contacts ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_shared_history_lands_in_each_chat_oldest_first_and_capped(db, monkeypatch):
    monkeypatch.setattr(cx, "HISTORY_PER_CHAT", 3)
    msgs = [{"from": CUSTOMER if i % 2 else "27713128891", "id": f"h{i}", "timestamp": str(1761300000 + i),
             "type": "text", "text": {"body": f"line {i}"}} for i in range(5)]
    msgs.append({"from": CUSTOMER, "id": "h-sticker", "timestamp": "1761300009", "type": "unsupported"})
    value = {"history": [{"metadata": {"phase": 0}, "threads": [{"id": CUSTOMER, "messages": msgs}]}]}
    took = await cx.handle("history", SHOP, value)
    rows = _msgs(db)
    assert took == len(rows) == 3
    assert [r["content"] for r in rows][:3] == ["line 3", "line 4", "[unsupported]"]
    assert [r["role"] for r in rows][:2] == ["user", "agent"]
    assert rows[0]["created_at"].startswith("2025-10-24")
    assert db.tables["commerce_conversation_sessions"][0]["paused"] is False   # history never pauses


@pytest.mark.asyncio
async def test_history_the_owner_declined_to_share_is_skipped(db):
    value = {"history": [{"errors": [{"code": 2593109, "title": "History sharing declined"}]}]}
    assert await cx.handle("history", SHOP, value) == 0 and _msgs(db) == []


@pytest.mark.asyncio
async def test_contacts_name_only_chats_without_a_name(db):
    db.tables["commerce_conversation_sessions"] = [
        {"id": "a", "tenant_id": SHOP, "session_key": CUSTOMER, "customer_name": None},
        {"id": "b", "tenant_id": SHOP, "session_key": "27820002222", "customer_name": "Sipho N."}]
    items = [{"type": "contact", "action": "add", "contact": {"full_name": "Thandi Mokoena", "phone_number": "+27 82 000 1111"}},
             {"type": "contact", "action": "add", "contact": {"full_name": "Someone Else", "phone_number": "27820002222"}},
             {"type": "contact", "action": "remove", "contact": {"full_name": "Gone", "phone_number": "27820003333"}}]
    assert await cx.handle("smb_app_state_sync", SHOP, {"state_sync": items}) == 1
    names = {r["id"]: r["customer_name"] for r in db.tables["commerce_conversation_sessions"]}
    assert names == {"a": "Thandi Mokoena", "b": "Sipho N."}


# ── a knowledge line stays quiet in a chat someone took over ─────────────────

@pytest.mark.asyncio
async def test_a_taken_over_chat_on_a_knowledge_line_stays_quiet(db):
    db.tables["commerce_conversation_sessions"] = [
        {"id": "s1", "tenant_id": "digg-demo", "session_key": CUSTOMER, "paused": True}]
    assert await cx.owner_is_handling("digg-demo", "+27 82 000 1111", "Is the plan ready?") is True
    assert _msgs(db)[-1] == {"tenant_id": "digg-demo", "session_id": "s1", "role": "user", "content": "Is the plan ready?"}
    db.tables["commerce_conversation_sessions"][0]["paused"] = False
    assert await cx.owner_is_handling("digg-demo", CUSTOMER, "hello") is False


# ── connecting ───────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, status, data):
        self.status_code, self._data = status, data
        self.is_success = 200 <= status < 300
        self.text = str(data)

    def json(self):
        return self._data


@pytest.fixture()
def meta(monkeypatch):
    calls = []

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        async def get(self, url, params=None, headers=None):
            calls.append(("GET", url, params))
            if url.endswith("/oauth/access_token"):
                return _Resp(200, {"access_token": "tenant-token"})
            return _Resp(200, {"display_phone_number": "+27 71 312 8891", "verified_name": "Off the Hook"})

        async def post(self, url, headers=None, json=None):
            calls.append(("POST", url, json))
            return _Resp(200, {"success": True})
    monkeypatch.setattr(whatsapp_connect.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(cx.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(settings, "vula_fb_app_id", "app")
    monkeypatch.setattr(settings, "vula_fb_app_secret", "secret")
    monkeypatch.setattr(settings, "whatsapp_register_on_connect", True)
    monkeypatch.setattr(settings, "whatsapp_registration_pin", "123456")
    monkeypatch.setattr("vula.email_imap.credentials.encrypt_secret", lambda s: f"enc:{s}")
    monkeypatch.setattr("vula.integrations.platform_support.ensure_template", lambda tid: _noop())
    return calls


async def _noop():
    return None


@pytest.mark.asyncio
async def test_connecting_the_app_number_never_registers_it_and_asks_for_contacts_and_history(db, meta):
    out = await whatsapp_connect.connect_whatsapp(whatsapp_connect.ConnectRequest(
        tenant_id=SHOP, code="c", waba_id="W1", phone_number_id="P1", coexistence=True))
    posts = [c for c in meta if c[0] == "POST"]
    assert not any(u.endswith("/register") for _, u, _ in posts)               # stays on the owner's phone
    assert [j["sync_type"] for _, u, j in posts if u.endswith("/smb_app_data")] == ["smb_app_state_sync", "history"]
    assert any(u.endswith("/W1/subscribed_apps") for _, u, _ in posts)
    row = db.tables["vula_whatsapp_accounts"][-1]
    assert row["coexistence"] is True and row["history_sync"] == {"contacts": "requested", "history": "requested"}
    assert out.coexistence is True and out.phone_number_id == "P1"


@pytest.mark.asyncio
async def test_a_new_number_is_registered_as_before_and_nothing_is_synced(db, meta):
    await whatsapp_connect.connect_whatsapp(whatsapp_connect.ConnectRequest(
        tenant_id=SHOP, code="c", waba_id="W1", phone_number_id="P1"))
    urls = [u for m, u, _ in meta if m == "POST"]
    assert any(u.endswith("/P1/register") for u in urls) and not any("smb_app_data" in u for u in urls)
    assert db.tables["vula_whatsapp_accounts"][-1]["coexistence"] is False


@pytest.mark.asyncio
async def test_connect_still_works_before_migration_223(db, meta):
    real = _Q.execute
    new_cols = ("coexistence", "history_sync", "purpose", "is_primary")

    def old_table(self):                     # a write naming a column the table lacks is refused
        if self.table == "vula_whatsapp_accounts" and self.op in ("upsert", "update") \
                and any(c in (self.payload or {}) for c in new_cols):
            raise RuntimeError('column "coexistence" does not exist')
        return real(self)
    _Q.execute = old_table
    try:
        out = await whatsapp_connect.connect_whatsapp(whatsapp_connect.ConnectRequest(
            tenant_id=SHOP, code="c", waba_id="W1", phone_number_id="P1", coexistence=True))
    finally:
        _Q.execute = real
    assert out.status == "connected" and "coexistence" not in db.tables["vula_whatsapp_accounts"][-1]


def test_the_webhook_hands_coexistence_fields_to_this_module():
    import inspect
    src = inspect.getsource(whatsapp.receive_message)
    assert "COEXIST_FIELDS" in src and "_coexist.handle" in src
    assert set(cx.COEXIST_FIELDS) == {"smb_message_echoes", "history", "smb_app_state_sync"}
