"""Owner and coach HTTP endpoints for unpaid bills and reminders."""
import re
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from tests.tap_fakes import Clock, FakeGateway, FakeMessenger, MemoryRepo
from vula.api.server import app
from vula.api.tenant_auth import require_tenant_actor
from vula.tap import api as tap_api
from vula.tap import appapi
from vula.tap.core import reminders as rm
from vula.tap.service import TapConfig, TapService

T, CUST = "tenant-a", "+27 82 111 4482"


def at(h, mi=0, day=6):
    return datetime(2026, 10, day, h, mi, tzinfo=rm.SAST).astimezone(timezone.utc)


@pytest.fixture
def env(monkeypatch):
    clock = Clock(); clock.t = at(14)
    repo = MemoryRepo(clock)
    repo.members[T] = [{"id": "m1", "name": "Sipho", "whatsapp": "27800000001", "role": "staff", "active": True},
                       {"id": "m2", "name": "Anele", "whatsapp": "27800000002", "role": "staff", "active": True}]
    repo.settings[T] = {"tenant_id": T, "mode": "live", "reminders_max": 3}
    repo.rules[(T, "m1")] = {"staff_share_bp": 7000, "tip_rule": "direct"}
    msg, gw = FakeMessenger(), FakeGateway()
    svc = TapService(repo, msg, gw, TapConfig(pepper="p", public_base_url="https://api.test", encrypt=lambda x: "enc:" + x,
                     decrypt=lambda x: x[4:], clock=clock, enabled=tap_api.tenant_enabled))
    tag = repo.add_tag(T, "tag-m1", bound_id="m1"); repo.add_tag(T, "tag-m2", bound_id="m2")
    monkeypatch.setattr(tap_api, "get_service", lambda: svc)
    monkeypatch.setattr(tap_api, "_payfast_lookup", lambda t: {"connected": True, "mode": "test"})
    monkeypatch.setattr(tap_api.settings, "tap_to_pay_tenants", "")
    tap_api._mode_cache.clear()
    return svc, repo, msg, clock, tag


@pytest.fixture
def client(env):
    app.dependency_overrides[require_tenant_actor] = lambda: {"user_id": "u", "email": "o@x.co"}
    yield TestClient(app, follow_redirects=False)
    app.dependency_overrides.pop(require_tenant_actor, None)


async def make_unpaid(env):
    svc, repo, msg, clock, tag = env
    svc.create_bill(tenant_id=T, tag_id=tag["id"], amount_cents=50000, description="Beginner lesson", staff_id="m1", created_by="m1")
    tok = re.search(r"text=PAY%20(\S+)", svc.tap(tag["code"])).group(1)
    await svc.handle_text(T, CUST, f"PAY {tok}")
    sid = list(repo.sessions)[-1]
    await svc.handle_interactive(T, CUST, f"kb:tip:{sid}:10")
    clock.t = at(14, 11)
    await svc.sweep(T)
    return next(b for b in repo.bills.values() if b["status"] == "abandoned")


async def test_unpaid_list_and_reminder_setting(env, client):
    bill = await make_unpaid(env)
    d = client.get(f"/v1/tap/{T}/unpaid").json()
    assert d["reminders_max"] == 3 and [u["id"] for u in d["unpaid"]] == [bill["id"]]
    assert d["unpaid"][0]["customer"] == "ending 482" and "27821114482" not in str(d)
    assert client.put(f"/v1/tap/{T}/setup/reminders", json={"max": 1}).status_code == 200
    assert client.get(f"/v1/tap/{T}/unpaid").json()["reminders_max"] == 1
    assert client.put(f"/v1/tap/{T}/setup/reminders", json={"max": 4}).status_code == 422
    assert client.put(f"/v1/tap/{T}/setup/reminders", json={"max": -1}).status_code == 422
    assert client.get(f"/v1/tap/{T}/setup").json()["reminders_max"] == 1


async def test_owner_can_resend_once_a_day_inside_the_window(env, client):
    bill = await make_unpaid(env)
    svc, repo, msg, clock, tag = env
    assert client.post(f"/v1/tap/{T}/bills/{bill['id']}/remind").status_code == 200
    r = client.post(f"/v1/tap/{T}/bills/{bill['id']}/remind")
    assert r.status_code == 409 and "already went out" in r.json()["detail"]
    clock.t = at(21, 0, day=7)
    r = client.post(f"/v1/tap/{T}/bills/{bill['id']}/remind")
    assert r.status_code == 409 and "08:00 and 20:00" in r.json()["detail"]


async def test_owner_closes_unpaid_bills(env, client):
    bill = await make_unpaid(env)
    assert client.post(f"/v1/tap/{T}/bills/{bill['id']}/close", json={"action": "paid_other"}).status_code == 422     # needs a reason
    assert client.post(f"/v1/tap/{T}/bills/{bill['id']}/close", json={"action": "paid_other", "reason": "barter"}).status_code == 422
    assert client.post(f"/v1/tap/{T}/bills/{bill['id']}/close", json={"action": "bogus"}).status_code == 422
    assert client.post(f"/v1/tap/{T}/bills/{bill['id']}/close", json={"action": "paid_other", "reason": "cash"}).status_code == 200
    assert client.post(f"/v1/tap/{T}/bills/{bill['id']}/close", json={"action": "write_off"}).status_code == 409        # already closed
    assert client.get(f"/v1/tap/{T}/unpaid").json()["unpaid"] == []


async def test_other_tenants_bills_are_untouchable(env, client):
    bill = await make_unpaid(env)
    assert client.post(f"/v1/tap/tenant-b/bills/{bill['id']}/close", json={"action": "write_off"}).status_code in (404, 409)
    assert next(b for b in env[1].bills.values() if b["id"] == bill["id"])["status"] == "abandoned"


async def test_coach_sees_only_their_own_unpaid_bills_and_can_resend(env, client):
    bill = await make_unpaid(env)
    svc, repo, msg, clock, tag = env
    auth = appapi.get_auth()
    def signin(mid, phone, pin):
        code = auth.issue_enrol_code(T, mid, "o")["code"]
        r = client.post("/v1/tap/app/enrol", json={"tenant": T, "phone": phone, "code": code, "pin": pin})
        return {"Authorization": "Bearer " + r.json()["access_token"]}
    h1, h2 = signin("m1", "27800000001", "2468"), signin("m2", "27800000002", "1357")
    ids = lambda h: [b["id"] for b in client.get("/v1/tap/app/bills", headers=h).json()["bills"]]
    assert bill["id"] in ids(h1) and bill["id"] not in ids(h2)
    assert client.post(f"/v1/tap/app/bills/{bill['id']}/remind", headers=h2).status_code == 404       # not hers
    assert client.post(f"/v1/tap/app/bills/{bill['id']}/remind", headers=h1).status_code == 200
    assert client.post(f"/v1/tap/app/bills/{bill['id']}/remind", headers=h1).status_code == 409       # once a day
    assert client.post("/v1/tap/app/bills/x/remind").status_code == 401


async def test_sweeper_loop_imports_and_tenant_helpers():
    from vula.api import server
    assert callable(server._tap_sweep_loop)
    assert isinstance(tap_api._override_tenants(), set)
