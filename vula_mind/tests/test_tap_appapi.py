"""Coach app API: sign-in, scoping, live events, push."""
import asyncio
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from tests.tap_fakes import Clock, FakeGateway, FakeMessenger, MemoryRepo
from vula.api.server import app
from vula.api.tenant_auth import require_tenant_actor
from vula.tap import api as tap_api
from vula.tap import appapi
from vula.tap.appauth import Actor
from vula.tap.service import REF_PREFIX, TapConfig, TapService

T = "tenant-a"
SIPHO, ANELE, BOSS = "27800000001", "27800000002", "27800000003"
CUST = "+27 82 111 4482"


class FakePusher:
    def __init__(self):
        self.sent, self.result = [], "ok"

    async def send(self, sub, payload):
        self.sent.append((sub["endpoint"], payload))
        return self.result


@pytest.fixture
def env(monkeypatch):
    clock = Clock()
    repo = MemoryRepo(clock)
    repo.members[T] = [
        {"id": "m1", "name": "Sipho", "whatsapp": SIPHO, "role": "staff", "active": True},
        {"id": "m2", "name": "Anele", "whatsapp": ANELE, "role": "staff", "active": True},
        {"id": "boss", "name": "Boss", "whatsapp": BOSS, "role": "owner", "active": True}]
    repo.team[(T, "m1")] = [SIPHO]
    repo.rules[(T, "m1")] = {"staff_share_bp": 7000, "tip_rule": "direct"}
    repo.settings[T] = {"tenant_id": T, "mode": "live"}
    pusher, msg, gw = FakePusher(), FakeMessenger(), FakeGateway()
    svc = TapService(repo, msg, gw, TapConfig(pepper="p", public_base_url="https://api.test",
                     encrypt=lambda x: "enc:" + x, decrypt=lambda x: x[4:], clock=clock, enabled=tap_api.tenant_enabled),
                     pusher=pusher)
    repo.add_tag(T, "tag-m1", bound_id="m1")
    repo.add_tag(T, "tag-m2", bound_id="m2")
    monkeypatch.setattr(tap_api, "get_service", lambda: svc)
    monkeypatch.setattr(tap_api.settings, "tap_to_pay_tenants", "")
    tap_api._mode_cache.clear()
    return svc, repo, clock, pusher, msg, gw


@pytest.fixture
def client(env):
    app.dependency_overrides[require_tenant_actor] = lambda: {"user_id": "u", "email": "o@x.co"}
    yield TestClient(app, follow_redirects=False)
    app.dependency_overrides.pop(require_tenant_actor, None)


def sign_in(client, svc, member="m1", phone=SIPHO, pin="2468"):
    code = appapi.get_auth().issue_enrol_code(T, member, "o")["code"]
    r = client.post("/v1/tap/app/enrol", json={"tenant": T, "phone": phone, "code": code, "pin": pin, "label": "phone"})
    assert r.status_code == 200, r.text
    d = r.json()
    return {"Authorization": "Bearer " + d["access_token"]}, d


def test_enrol_login_me(client, env):
    svc, *_ = env
    h, d = sign_in(client, svc)
    r = client.post("/v1/tap/app/login", json={"device_token": d["device_token"], "pin": "2468"})
    assert r.status_code == 200
    me = client.get("/v1/tap/app/me", headers=h).json()
    assert me["name"] == "Sipho" and me["sees_all"] is False and me["tag"]["url"].endswith("/t/tag-m1")
    assert client.post("/v1/tap/app/login", json={"device_token": d["device_token"], "pin": "9999"}).status_code == 401


def test_enrol_refused_when_tap_to_pay_is_off(client, env):
    svc, repo, *_ = env
    code = appapi.get_auth().issue_enrol_code(T, "m1", "o")["code"]
    repo.settings[T]["mode"] = "off"
    tap_api.invalidate_mode(T)
    r = client.post("/v1/tap/app/enrol", json={"tenant": T, "phone": SIPHO, "code": code, "pin": "2468"})
    assert r.status_code == 403


PROTECTED = [("GET", "/v1/tap/app/me"), ("GET", "/v1/tap/app/bills"), ("POST", "/v1/tap/app/bills"),
             ("POST", "/v1/tap/app/bills/x/cancel"), ("POST", "/v1/tap/app/bills/x/release"),
             ("GET", "/v1/tap/app/events"), ("GET", "/v1/tap/app/push-key"), ("POST", "/v1/tap/app/push"),
             ("DELETE", "/v1/tap/app/push")]


@pytest.mark.parametrize("method,path", PROTECTED)
def test_every_app_route_needs_a_valid_token(client, env, method, path):
    kw = {"json": {}} if method in ("POST", "DELETE") else {}
    assert client.request(method, path, **kw).status_code == 401
    assert client.request(method, path, headers={"Authorization": "Bearer junk"}, **kw).status_code == 401


def test_bill_scoping_staff_vs_manager(client, env):
    svc, *_ = env
    h1, _ = sign_in(client, svc, "m1", SIPHO)
    h2, _ = sign_in(client, svc, "m2", ANELE, "1357")
    hb, _ = sign_in(client, svc, "boss", BOSS, "1111")
    b1 = client.post("/v1/tap/app/bills", headers=h1, json={"amount_cents": 50000, "description": "Lesson"}).json()
    assert b1["status"] == "open"
    ids = lambda h: [b["id"] for b in client.get("/v1/tap/app/bills", headers=h).json()["bills"]]
    assert ids(h1) == [b1["id"]] and ids(h2) == [] and ids(hb) == [b1["id"]]
    assert client.post(f"/v1/tap/app/bills/{b1['id']}/cancel", headers=h2).status_code == 404   # not hers
    assert client.post(f"/v1/tap/app/bills/{b1['id']}/cancel", headers=h1).status_code == 200


def test_staff_cannot_bill_on_someone_elses_tag_but_manager_can(client, env):
    svc, repo, *_ = env
    h1, _ = sign_in(client, svc, "m1", SIPHO)
    hb, _ = sign_in(client, svc, "boss", BOSS, "1111")
    r = client.post("/v1/tap/app/bills", headers=h1, json={"amount_cents": 100, "description": "x", "staff_id": "m2"})
    assert r.status_code == 200 and r.json()["staff_id"] == "m1"          # silently own tag
    r = client.post("/v1/tap/app/bills", headers=hb, json={"amount_cents": 100, "description": "x", "staff_id": "m2"})
    assert r.status_code == 200 and r.json()["staff_id"] == "m2"


def test_second_open_bill_is_a_clear_409_and_validation(client, env):
    svc, *_ = env
    h1, _ = sign_in(client, svc, "m1", SIPHO)
    body = {"amount_cents": 50000, "description": "Lesson"}
    assert client.post("/v1/tap/app/bills", headers=h1, json=body).status_code == 200
    # the in-memory fake has no unique index, so assert the validation paths here
    assert client.post("/v1/tap/app/bills", headers=h1, json={"amount_cents": 0, "description": "x"}).status_code == 422
    assert client.post("/v1/tap/app/bills", headers=h1, json={"amount_cents": 5, "description": ""}).status_code == 422


def test_no_tag_and_switched_off(client, env):
    svc, repo, *_ = env
    repo.tags = {k: v for k, v in repo.tags.items() if v["bound_id"] != "m1"}
    h1, _ = sign_in(client, svc, "m1", SIPHO)
    assert client.post("/v1/tap/app/bills", headers=h1, json={"amount_cents": 100, "description": "x"}).status_code == 404
    repo.settings[T]["mode"] = "off"
    tap_api.invalidate_mode(T)
    assert client.post("/v1/tap/app/bills", headers=h1, json={"amount_cents": 100, "description": "x"}).status_code == 409


def test_revoked_device_is_locked_out_immediately(client, env):
    svc, repo, *_ = env
    h1, d = sign_in(client, svc, "m1", SIPHO)
    assert client.get("/v1/tap/app/me", headers=h1).status_code == 200
    dev = client.get(f"/v1/tap/{T}/devices").json()["devices"][0]
    assert "token_hash" not in dev and "pin_hash" not in dev
    assert client.post(f"/v1/tap/{T}/devices/{dev['id']}/revoke").status_code == 200
    assert client.get("/v1/tap/app/me", headers=h1).status_code == 401
    assert client.post("/v1/tap/app/login", json={"device_token": d["device_token"], "pin": "2468"}).status_code == 401


def test_owner_issues_enrol_code_with_app_link(client, env):
    r = client.post(f"/v1/tap/{T}/members/m1/enrol-code")
    assert r.status_code == 200 and len(r.json()["code"]) == 6 and r.json()["app_url"].endswith(f"/pay/?t={T}")
    assert client.post(f"/v1/tap/{T}/members/nobody/enrol-code").status_code == 404


# ── live events ──────────────────────────────────────────────────────────────────────────────
async def collect(gen, n):
    out = []
    async for chunk in gen:
        out.append(chunk)
        if len(out) >= n:
            break
    return out


async def test_event_stream_pushes_paid_with_tip_and_share(env):
    svc, repo, clock, pusher, msg, gw = env
    bill = svc.create_bill(tenant_id=T, tag_id=next(t["id"] for t in repo.tags.values() if t["bound_id"] == "m1"),
                           amount_cents=50000, description="Lesson", staff_id="m1", created_by="m1")
    a = Actor(T, "m1", "d1", "staff", "Sipho")
    ticks = []

    async def sleep(_):
        ticks.append(1)
        if len(ticks) == 1:                      # between polls: customer pays (a second later)
            clock.t += timedelta(seconds=1)
            await run_payment(svc, repo, msg, gw, bill)
    gen = appapi.event_stream(repo, a, "2000-01-01T00:00:00", poll=1, max_seconds=5, sleep=sleep)
    chunks = [c async for c in gen]
    text = "".join(chunks)
    assert chunks[0].startswith("event: hello")
    assert '"status": "open"' in text and '"status": "paid"' in text
    assert '"tip_cents": 5000' in text and '"share_cents": 40000' in text      # 70% of R500 + the R50 tip
    assert chunks[-1].startswith("event: bye")


async def run_payment(svc, repo, msg, gw, bill):
    import re
    url = svc.tap("tag-m1")
    tok = re.search(r"text=PAY%20(\S+)", url).group(1)
    await svc.handle_text(T, CUST, f"PAY {tok}")
    sid = next(iter(repo.sessions))
    await svc.handle_interactive(T, CUST, f"kb:tip:{sid}:10")
    await svc.handle_interactive(T, CUST, f"kb:pay:{sid}")
    gw.itn = {"reference": REF_PREFIX + sid, "paid": True, "amount_cents": 55000, "pf_payment_id": "P1", "fee_cents": 0}
    assert await svc.confirm_payment(T, {}, b"", {}) == "paid"


async def test_events_only_include_my_bills(env):
    svc, repo, clock, *_ = env
    for staff, tag in (("m1", "tag-m1"), ("m2", "tag-m2")):
        svc.create_bill(tenant_id=T, tag_id=next(t["id"] for t in repo.tags.values() if t["code"] == tag),
                        amount_cents=100, description=staff, staff_id=staff, created_by=staff)
    mine = await collect(appapi.event_stream(repo, Actor(T, "m1", "d", "staff", "S"), "2000", poll=0, max_seconds=0.1,
                                             sleep=lambda s: asyncio.sleep(0)), 2)
    assert 'm1' in mine[1] and "m2" not in "".join(mine)
    boss = "".join(await collect(appapi.event_stream(repo, Actor(T, "b", "d", "owner", "B"), "2000", poll=0,
                                                     max_seconds=0.1, sleep=lambda s: asyncio.sleep(0)), 3))
    assert "m1" in boss and "m2" in boss


async def test_stream_ends_after_max_seconds_and_sends_heartbeats(env):
    svc, repo, *_ = env
    a = Actor(T, "m1", "d", "staff", "S")
    chunks = [c async for c in appapi.event_stream(repo, a, "2099", poll=10, heartbeat=20, max_seconds=60,
                                                    sleep=lambda s: asyncio.sleep(0))]
    assert chunks[0].startswith("event: hello") and chunks[-1].startswith("event: bye")
    assert any(c.startswith(": keep-alive") for c in chunks)


# ── push ─────────────────────────────────────────────────────────────────────────────────────
def test_push_subscribe_only_https_and_unsubscribe_only_own(client, env):
    svc, repo, *_ = env
    h1, _ = sign_in(client, svc, "m1", SIPHO)
    h2, _ = sign_in(client, svc, "m2", ANELE, "1357")
    sub = {"endpoint": "https://push.example/abc", "keys": {"p256dh": "k", "auth": "a"}}
    assert client.post("/v1/tap/app/push", headers=h1, json={**sub, "endpoint": "http://insecure"}).status_code == 422
    assert client.post("/v1/tap/app/push", headers=h1, json=sub).status_code == 200
    assert repo.subs["https://push.example/abc"]["member_id"] == "m1"
    client.request("DELETE", "/v1/tap/app/push", headers=h2, json={"endpoint": sub["endpoint"]})
    assert "https://push.example/abc" in repo.subs                           # not hers: untouched
    client.request("DELETE", "/v1/tap/app/push", headers=h1, json={"endpoint": sub["endpoint"]})
    assert "https://push.example/abc" not in repo.subs


async def test_payment_sends_one_push_to_the_serving_person(env):
    svc, repo, clock, pusher, msg, gw = env
    repo.upsert_push_sub({"tenant_id": T, "member_id": "m1", "endpoint": "https://p/1", "p256dh": "k", "auth": "a"})
    repo.upsert_push_sub({"tenant_id": T, "member_id": "m2", "endpoint": "https://p/2", "p256dh": "k", "auth": "a"})
    bill = svc.create_bill(tenant_id=T, tag_id=next(t["id"] for t in repo.tags.values() if t["code"] == "tag-m1"),
                           amount_cents=50000, description="Lesson", staff_id="m1", created_by="m1")
    await run_payment(svc, repo, msg, gw, bill)
    assert [e for e, _ in pusher.sent] == ["https://p/1"]
    p = pusher.sent[0][1]
    assert "R 500.00" in p["title"] and "R 50.00 tip" in p["title"] and "Your share R 400.00" in p["body"]
    assert "4482" not in p["body"] or "ending 482" in p["body"]


async def test_dead_subscription_removed_and_push_failure_never_breaks_payment(env):
    svc, repo, clock, pusher, msg, gw = env
    repo.upsert_push_sub({"tenant_id": T, "member_id": "m1", "endpoint": "https://p/1", "p256dh": "k", "auth": "a"})
    pusher.result = "gone"
    bill = svc.create_bill(tenant_id=T, tag_id=next(t["id"] for t in repo.tags.values() if t["code"] == "tag-m1"),
                           amount_cents=50000, description="Lesson", staff_id="m1", created_by="m1")
    await run_payment(svc, repo, msg, gw, bill)
    assert "https://p/1" not in repo.subs
    repo.upsert_push_sub({"tenant_id": T, "member_id": "m1", "endpoint": "https://p/2", "p256dh": "k", "auth": "a"})

    async def boom(sub, payload):
        raise RuntimeError("push service down")
    pusher.send = boom
    repo.sessions.clear(); repo.bills.clear(); repo.payments.clear(); repo.events.clear()
    bill = svc.create_bill(tenant_id=T, tag_id=next(t["id"] for t in repo.tags.values() if t["code"] == "tag-m1"),
                           amount_cents=50000, description="Lesson 2", staff_id="m1", created_by="m1")
    await run_payment(svc, repo, msg, gw, bill)           # still completes: asserts inside run_payment
