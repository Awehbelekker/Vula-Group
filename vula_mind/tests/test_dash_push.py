"""The dashboard's notifications on a phone (2026-10-27, PWA pass 3): a device gets the alerts its
person already gets on WhatsApp — same people, same choices — and nothing for anyone else."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from config import settings
from vula import dash_push
from vula.api import dash_push as api

T = "harbour"


class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.f, self.op, self.payload, self.conflict = db, table, [], "select", None, None

    def select(self, *_a, **_k): return self
    def limit(self, *_a): return self

    def eq(self, k, v):
        self.f.append((k, v))
        return self

    def update(self, p):
        self.op, self.payload = "update", p
        return self

    def delete(self):
        self.op = "delete"
        return self

    def upsert(self, p, on_conflict=None):
        self.op, self.payload, self.conflict = "upsert", p, on_conflict
        return self

    def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        if self.op == "upsert":
            hit = next((r for r in rows if r.get(self.conflict) == self.payload.get(self.conflict)), None)
            (hit.update(self.payload) if hit else rows.append(dict(self.payload, id=f"s{len(rows) + 1}")))
            return SimpleNamespace(data=[self.payload])
        hits = [r for r in rows if all(r.get(k) == v for k, v in self.f)]
        if self.op == "update":
            for r in hits:
                r.update(self.payload)
        if self.op == "delete":
            self.db.tables[self.table] = [r for r in rows if r not in hits]
        return SimpleNamespace(data=[dict(r) for r in hits])


class _DB:
    def __init__(self, rows):
        self.tables = {dash_push.TABLE: rows}

    def table(self, name):
        return _Q(self, name)


class _Pusher:
    def __init__(self):
        self.sent, self.outcome = [], {}

    async def send(self, sub, payload):
        self.sent.append((sub["endpoint"], payload))
        return self.outcome.get(sub["endpoint"], "ok")


def _dev(endpoint, phone=None, role="staff"):
    return {"id": endpoint, "tenant_id": T, "endpoint": f"https://push.example/{endpoint}", "p256dh": "k",
            "auth": "a", "member_phone": phone, "role": role, "failures": 0}


@pytest.fixture()
def world(monkeypatch):
    db = _DB([_dev("owner-phone", "27820000001", "owner"), _dev("rep-phone", "27820000002"),
              _dev("owner-no-member", None, "owner"), _dev("staff-no-member", None, "staff")])
    pusher = _Pusher()
    monkeypatch.setattr("vula.commerce.service._client", lambda: db)
    monkeypatch.setattr(dash_push, "_pusher", lambda: pusher)
    return db, pusher


def _to(pusher):
    return sorted(e.rsplit("/", 1)[1] for e, _ in pusher.sent)


@pytest.mark.asyncio
async def test_an_alert_reaches_the_devices_of_the_people_it_went_to(world):
    _, pusher = world
    n = await dash_push.push_event(T, "new_order", "*Order* OTH-12 — R318.40\nfrom Thandi", ["0820000002"])
    assert n == 1 and _to(pusher) == ["rep-phone"]                    # 0820… matches 27820…
    payload = pusher.sent[0][1]
    assert payload == {"title": "New order", "body": "Order OTH-12 — R318.40 from Thandi",
                       "tag": "new_order", "url": "/#/go/orders"}


@pytest.mark.asyncio
async def test_urgent_alerts_also_reach_an_owner_login_with_no_team_row(world):
    _, pusher = world
    await dash_push.push_event(T, "customer_unanswered", "Thandi asked about delivery", ["27820000001"],
                               owner_fallback=True)
    assert _to(pusher) == ["owner-no-member", "owner-phone"]           # never the staff login


@pytest.mark.asyncio
async def test_a_device_the_push_service_dropped_is_forgotten(world):
    db, pusher = world
    pusher.outcome["https://push.example/rep-phone"] = "gone"
    await dash_push.push_to_phones(T, ["27820000002"], "x", "y")
    assert all(r["id"] != "rep-phone" for r in db.tables[dash_push.TABLE])


@pytest.mark.asyncio
async def test_a_device_failing_again_and_again_is_dropped(world):
    db, pusher = world
    pusher.outcome["https://push.example/rep-phone"] = "error"
    for _ in range(dash_push.MAX_FAILURES):
        await dash_push.push_to_phones(T, ["27820000002"], "x", "y")
    assert all(r["id"] != "rep-phone" for r in db.tables[dash_push.TABLE])


@pytest.mark.asyncio
async def test_no_keys_no_push_and_never_an_error(world, monkeypatch):
    monkeypatch.setattr(dash_push, "_pusher", lambda: None)
    assert await dash_push.push_event(T, "new_order", "x", ["27820000002"]) == 0
    monkeypatch.setattr(dash_push, "subscriptions", lambda t: 1 / 0)
    monkeypatch.setattr(dash_push, "_pusher", lambda: _Pusher())
    assert await dash_push.push_to_phones(T, ["27820000002"], "x", "y") == 0


@pytest.mark.asyncio
async def test_a_rerun_scheduled_alert_is_pushed_once(world, monkeypatch):
    _, pusher = world
    seen = set()
    monkeypatch.setattr("vula.api.whatsapp._durable_dedup_claim", lambda k: not (k in seen or seen.add(k)))
    for _ in range(2):
        await dash_push.push_event(T, "followup_digest", "3 emails", ["27820000002"], idem_key="digest:2026-10-27")
    assert len(pusher.sent) == 1


@pytest.mark.asyncio
async def test_notify_team_pushes_to_the_same_people(monkeypatch):
    from vula.integrations import notify
    monkeypatch.setattr(notify, "recipients_for", lambda t, e: ["27820000002"])

    async def reply(*a, **k):
        return True
    monkeypatch.setattr("vula.api.whatsapp._send_reply", reply)
    got = {}

    async def push_event(tenant, event, message, phones, idem_key=None, owner_fallback=False):
        got.update(tenant=tenant, event=event, phones=phones, owner_fallback=owner_fallback)
        return 1
    monkeypatch.setattr(dash_push, "push_event", push_event)
    assert await notify.notify_team(T, "customer_unanswered", "Thandi is waiting") == 1
    assert got == {"tenant": T, "event": "customer_unanswered", "phones": ["27820000002"], "owner_fallback": True}


@pytest.mark.asyncio
async def test_a_paid_order_is_pushed_to_the_order_team(monkeypatch):
    from vula.api import yoco
    got = {}

    async def push_to_phones(tenant, phones, title, body, page=None, tag=None, owner_fallback=False):
        got.update(phones=phones, title=title, body=body, page=page)
        return 1
    monkeypatch.setattr(dash_push, "push_to_phones", push_to_phones)
    await yoco._push_paid_order(T, [("Staci", "27820000001", "owner")], "OTH-12", "R318.40", "Thandi")
    assert got == {"phones": ["27820000001"], "title": "New paid order", "body": "OTH-12 · R318.40 · Thandi",
                   "page": "orders"}


# ── turning it on and off ────────────────────────────────────────────────────

def _scope(role="staff", phone="+27 82 000 0002", notify=("new_order", "low_stock")):
    return {"role": role, "email": "rep@harbour.test",
            "member": {"id": "m2", "whatsapp": phone, "notify": list(notify), "role": role}}


@pytest.mark.asyncio
async def test_turning_it_on_ties_the_device_to_the_signed_in_person(world, monkeypatch):
    db, _ = world
    async def scope(auth, tenant):
        return _scope()
    monkeypatch.setattr("vula.api.tenant_auth.member_scope", scope)
    req = SimpleNamespace(headers={"user-agent": "Pixel 7"})
    body = api.Subscription(endpoint="https://push.example/new", keys=api.Keys(p256dh="p", auth="a"))
    assert (await api.subscribe(T, body, req, authorization="Bearer x")) == {"ok": True}
    row = next(r for r in db.tables[dash_push.TABLE] if r["endpoint"] == "https://push.example/new")
    assert row["member_phone"] == "27820000002" and row["member_id"] == "m2" and row["user_agent"] == "Pixel 7"
    await api.unsubscribe(T, api.Subscription(endpoint="https://push.example/new"), authorization="Bearer x")
    assert all(r["endpoint"] != "https://push.example/new" for r in db.tables[dash_push.TABLE])


@pytest.mark.asyncio
async def test_not_a_browser_subscription_is_refused(world, monkeypatch):
    async def scope(auth, tenant):
        return _scope()
    monkeypatch.setattr("vula.api.tenant_auth.member_scope", scope)
    with pytest.raises(HTTPException) as e:
        await api.subscribe(T, api.Subscription(endpoint="http://evil.test/x", keys=api.Keys(p256dh="p", auth="a")),
                            SimpleNamespace(headers={}), authorization="Bearer x")
    assert e.value.status_code == 422


@pytest.mark.asyncio
async def test_the_key_says_which_alerts_this_person_will_get(monkeypatch):
    monkeypatch.setattr(settings, "vapid_public_key", "PUB")
    monkeypatch.setattr(settings, "vapid_private_key", "PRIV")

    async def scope(auth, tenant):
        return _scope(role="owner", notify=("low_stock", "customer_unanswered"))
    monkeypatch.setattr("vula.api.tenant_auth.member_scope", scope)
    out = await api.push_key(T, authorization="Bearer x")
    assert out["public_key"] == "PUB" and out["enabled"] is True
    assert out["alerts"] == ["New paid orders", "Stock running low", "A customer waiting for a person",
                             "Approvals waiting for you"]


@pytest.mark.asyncio
async def test_someone_from_another_business_is_refused(monkeypatch):
    async def scope(auth, tenant):
        return None
    monkeypatch.setattr("vula.api.tenant_auth.member_scope", scope)
    with pytest.raises(HTTPException) as e:
        await api.push_key(T, authorization="Bearer someone-else")
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_an_approval_is_pushed_to_its_approvers(monkeypatch):
    from vula.commerce import approvals

    class _Ins:
        def __init__(self, data): self.data = data
        def insert(self, row): return _Ins([dict(row, id="ap1")] if isinstance(row, dict) else row)
        def execute(self): return self
    monkeypatch.setattr(approvals, "_client", lambda: SimpleNamespace(table=lambda name: _Ins(None)))

    async def ask_or_queue(*a, **k):
        return None
    monkeypatch.setattr("vula.open_questions.ask_or_queue", ask_or_queue)
    got = {}

    async def push_to_phones(tenant, phones, title, body, page=None, tag=None, owner_fallback=False):
        got.update(phones=phones, title=title, body=body, page=page)
        return 1
    monkeypatch.setattr(dash_push, "push_to_phones", push_to_phones)
    await approvals.create_approval(T, "order", "o1", "OTH-12 — R318.40",
                                    [{"phone": "+27 82 000 0001", "name": "Staci", "role": "owner"}])
    assert got == {"phones": ["27820000001"], "title": "Approval needed", "body": "OTH-12 — R318.40", "page": "orders"}
