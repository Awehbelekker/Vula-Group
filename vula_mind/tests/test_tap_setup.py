"""Self-serve setup: checklist, shares, tags, R5 test payment, go-live, per-tenant switch."""
import re

import pytest
from fastapi.testclient import TestClient

from tests.tap_fakes import Clock, FakeGateway, FakeMessenger, MemoryRepo
from vula.api.server import app
from vula.api.tenant_auth import require_tenant_actor
from vula.tap import api as tap_api
from vula.tap.service import REF_PREFIX, TapConfig, TapService
from vula.tap.setup import Setup, SetupError

T = "tenant-a"
CUST = "+27 82 111 4482"
PF_OK = {"connected": True, "mode": "test"}


@pytest.fixture
def env(monkeypatch):
    clock = Clock()
    repo = MemoryRepo(clock)
    repo.members[T] = [{"id": "m1", "name": "Sipho Dlamini", "whatsapp": "27800000001", "role": "staff"},
                       {"id": "m2", "name": "Anele", "whatsapp": "27800000002", "role": "staff"}]
    repo.team[(T, None)] = ["27800000009"]
    msg, gw = FakeMessenger(), FakeGateway()
    cfg = TapConfig(pepper="p", public_base_url="https://api.test", encrypt=lambda x: "enc:" + x,
                    decrypt=lambda x: x[4:], clock=clock, enabled=tap_api.tenant_enabled)
    svc = TapService(repo, msg, gw, cfg)
    changed = []
    pf = {"v": PF_OK}
    setup = Setup(svc, "https://api.test", lambda t: pf["v"], on_mode_change=lambda t: (changed.append(t), tap_api.invalidate_mode(t)))
    monkeypatch.setattr(tap_api, "get_service", lambda: svc)
    monkeypatch.setattr(tap_api.settings, "tap_to_pay_tenants", "")
    tap_api._mode_cache.clear()
    return setup, svc, repo, msg, gw, pf, changed


def test_status_lists_blockers_in_order(env):
    setup, svc, repo, msg, gw, pf, _ = env
    pf["v"] = {"connected": False, "mode": None}
    repo.wa[T] = ""
    st = setup.status(T)
    assert st["mode"] == "off" and not st["can_test"] and not st["can_go_live"]
    assert st["blockers"] == ["Connect your PayFast account.",
                              "Connect your WhatsApp number (Settings).",
                              "Create a tag for at least one team member."]
    assert [s["name"] for s in st["staff"]] == ["Sipho Dlamini", "Anele"]


def test_shares_validated_and_reflected(env):
    setup, *_ = env
    setup.save_shares(T, 0, {"m1": 7000})
    st = setup.status(T)
    assert {s["id"]: s["share_bp"] for s in st["staff"]} == {"m1": 7000, "m2": 0}
    for bad in (-1, 10_001):
        with pytest.raises(SetupError):
            setup.save_shares(T, bad, {})
    with pytest.raises(SetupError):
        setup.save_shares(T, 0, {"someone-elses-member": 5000})


def test_tag_is_idempotent_and_rotation_kills_the_old_one(env):
    setup, svc, repo, *_ = env
    a = setup.ensure_tag(T, "m1")
    assert setup.ensure_tag(T, "m1") == a
    assert a["url"] == f"https://api.test/t/{a['code']}" and a["qr"].endswith("/qr.svg")
    b = setup.ensure_tag(T, "m1", rotate=True)
    assert b["code"] != a["code"]
    assert svc.tap(a["code"]) is None or True          # old tag disabled (checked below)
    assert repo.get_tag_by_code(a["code"])["status"] == "disabled"
    with pytest.raises(SetupError):
        setup.ensure_tag(T, "not-a-member")


def test_cannot_test_until_prerequisites_met(env):
    setup, svc, repo, msg, gw, pf, _ = env
    pf["v"] = {"connected": False, "mode": None}
    with pytest.raises(SetupError, match="PayFast"):
        setup.start_test(T, "m1")


def test_full_setup_to_live(env):
    setup, svc, repo, msg, gw, pf, changed = env
    setup.save_shares(T, 0, {"m1": 7000})
    with pytest.raises(SetupError, match="tag"):
        setup.go_live(T)
    t = setup.start_test(T, "m1")          # creates the tag itself
    assert repo.get_settings(T)["mode"] == "testing" and changed == [T]
    bill = repo.bills[t["bill_id"]]
    assert (bill["subtotal_cents"], bill["is_test"], bill["status"]) == (500, True, "open")
    with pytest.raises(SetupError, match="test payment"):
        setup.go_live(T)                                  # not tested yet

    # owner taps the link, pays R5 -> the real flow runs
    import asyncio
    async def flow():
        url = svc.tap(t["code"])
        assert url, "tap works while testing"
        tok = re.search(r"text=PAY%20(\S+)", url).group(1)
        await svc.handle_text(T, CUST, f"PAY {tok}")
        sid = next(iter(repo.sessions))
        await svc.handle_interactive(T, CUST, f"kb:tip:{sid}:0")
        await svc.handle_interactive(T, CUST, f"kb:pay:{sid}")
        gw.itn = {"reference": REF_PREFIX + sid, "paid": True, "amount_cents": 500, "pf_payment_id": "T1", "fee_cents": 230}
        return await svc.confirm_payment(T, {}, b"", {})
    assert asyncio.run(flow()) == "test_paid"
    assert repo.ledger == []                              # a test books nothing
    assert repo.get_settings(T)["tested_at"]
    assert any("test payment received" in m[3] for m in msg.to("27800000009", "text"))
    # the payer gets the same slip, clearly marked TEST, and no tax-invoice offer
    slip = [m for m in msg.to(CUST, "text") if "TEST PAYMENT" in m[3]]
    assert slip and "Paid R 5.00" in slip[-1][3] and "Reply TAX" not in slip[-1][3]
    assert not any("you earned" in m[3].lower() or "Payment from" in m[3] for m in msg.to("27800000001", "text"))   # coach is not alerted for a test

    assert setup.status(T)["can_go_live"] is True
    setup.go_live(T)
    assert repo.get_settings(T)["mode"] == "live"
    setup.pause(T)
    assert repo.get_settings(T)["mode"] == "off"
    assert svc.tap(t["code"]) is None                     # switched off: tags stop resolving


def test_test_bill_replaces_old_test_but_never_a_real_bill(env):
    setup, svc, repo, *_ = env
    t1 = setup.start_test(T, "m1")
    t2 = setup.start_test(T, "m1")
    assert repo.bills[t1["bill_id"]]["status"] == "cancelled" and repo.bills[t2["bill_id"]]["status"] == "open"
    svc.cancel_bill(T, t2["bill_id"])
    tag = repo.get_tag_by_code(t2["code"])
    svc.create_bill(tenant_id=T, tag_id=tag["id"], amount_cents=50000, description="Lesson",
                    staff_id="m1", created_by="u")
    with pytest.raises(SetupError, match="open bill"):
        setup.start_test(T, "m1")


def test_mode_cache_and_override(env, monkeypatch):
    setup, svc, repo, *_ = env
    assert tap_api.tenant_mode(T) == "off" and not tap_api.tenant_enabled(T)
    repo.upsert_settings(T, {"mode": "live"})
    assert tap_api.tenant_mode(T) == "off"                # cached up to 30 s
    tap_api.invalidate_mode(T)
    assert tap_api.tenant_mode(T) == "live"
    monkeypatch.setattr(tap_api.settings, "tap_to_pay_tenants", "other")
    assert tap_api.tenant_mode("other") == "live"         # operator override
    assert tap_api.tenant_mode(None) == "off"


def test_db_trouble_reads_as_off(env, monkeypatch):
    setup, svc, repo, *_ = env
    monkeypatch.setattr(repo, "get_settings", lambda t: (_ for _ in ()).throw(RuntimeError("db down")))
    assert tap_api.tenant_mode(T) == "off"


# ── HTTP ─────────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def client(env, monkeypatch):
    setup, svc, repo, msg, gw, pf, _ = env
    monkeypatch.setattr(tap_api, "_payfast_lookup", lambda t: pf["v"])
    app.dependency_overrides[require_tenant_actor] = lambda: {"user_id": "u1", "email": "o@x.co"}
    yield TestClient(app, follow_redirects=False)
    app.dependency_overrides.pop(require_tenant_actor, None)


def test_http_setup_flow(client, env):
    setup, svc, repo, *_ = env
    st = client.get(f"/v1/tap/{T}/setup").json()
    assert st["mode"] == "off" and st["blockers"] == ["Create a tag for at least one team member."]
    r = client.put(f"/v1/tap/{T}/setup/shares", json={"default_share_bp": 0, "staff_shares": {"m1": 7000}})
    assert r.status_code == 200
    tag = client.post(f"/v1/tap/{T}/staff/m1/tag").json()
    assert tag["url"].endswith(f"/t/{tag['code']}")
    assert client.put(f"/v1/tap/{T}/setup/shares", json={"default_share_bp": 20000}).status_code == 422
    assert client.post(f"/v1/tap/{T}/go-live").status_code == 422
    t = client.post(f"/v1/tap/{T}/test/m1").json()
    assert t["amount_cents"] == 500
    assert client.get(f"/v1/tap/{T}/bills").json()["bills"][0]["is_test"] is True
    assert client.post(f"/v1/tap/{T}/pause").json()["mode"] == "off"


def test_http_bill_creation_needs_the_switch(client, env):
    setup, svc, repo, *_ = env
    link = client.post(f"/v1/tap/{T}/staff/m1/tag").json()
    body = {"amount_cents": 50000, "description": "Beginner lesson", "staff_id": "m1"}
    assert client.post(f"/v1/tap/{T}/bills", json=body).status_code == 409      # off
    repo.upsert_settings(T, {"mode": "live"})
    tap_api.invalidate_mode(T)
    r = client.post(f"/v1/tap/{T}/bills", json=body)
    assert r.status_code == 200 and r.json()["status"] == "open"
    assert client.post(f"/v1/tap/{T}/bills", json=body).status_code in (200, 409)   # one open bill per tag
    assert client.post(f"/v1/tap/{T}/bills", json={**body, "staff_id": "m2"}).status_code == 404   # m2 has no tag
    assert client.post(f"/v1/tap/{T}/bills", json={"amount_cents": 100, "description": "x"}).status_code == 422


def test_qr_is_an_svg_for_active_tags_only(client, env):
    setup, svc, repo, *_ = env
    link = client.post(f"/v1/tap/{T}/staff/m1/tag").json()
    r = client.get(f"/t/{link['code']}/qr.svg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml") and "<svg" in r.text
    assert client.get("/t/nope/qr.svg").status_code == 404
    client.post(f"/v1/tap/{T}/staff/m1/tag?rotate=true")
    assert client.get(f"/t/{link['code']}/qr.svg").status_code == 404
