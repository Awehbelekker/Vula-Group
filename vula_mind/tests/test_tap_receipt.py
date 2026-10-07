"""Receipt links and the public receipt endpoint."""
import re

import pytest
from fastapi.testclient import TestClient

from tests.tap_fakes import Clock, FakeGateway, FakeMessenger, MemoryRepo
from vula.api.server import app
from vula.api.tenant_auth import require_tenant_actor
from vula.tap import api as tap_api
from vula.tap import receipt as rc
from vula.tap.service import REF_PREFIX, TapConfig, TapService

T, CUST = "tenant-a", "+27 82 111 4482"


def test_token_roundtrip_and_tamper():
    t = rc.make_token("s", "pay-123", 2)
    assert rc.read_token("s", t) == ("pay-123", 2)
    assert rc.read_token("other", t) is None
    body, mac = t.split(".")
    assert rc.read_token("s", body + "." + mac[:-2] + "AA") is None
    forged = rc.make_token("s", "pay-999", 2).split(".")[0] + "." + mac        # swap payload, keep mac
    assert rc.read_token("s", forged) is None
    for junk in ("", "x", "a.b", "....", "\x00.\x00"):
        assert rc.read_token("s", junk) is None


def test_tokens_differ_per_payment_and_nonce():
    assert len({rc.make_token("s", f"p{i}", 0) for i in range(50)}) == 50
    assert rc.make_token("s", "p", 0) != rc.make_token("s", "p", 1)


@pytest.mark.parametrize("bill,vat", [(11500, 1500), (50000, 6522), (115, 15), (0, 0), (1, 0)])
def test_vat_included_is_15_percent_of_inclusive_amount(bill, vat):
    assert rc.vat_included(bill) == vat


@pytest.fixture
def env(monkeypatch):
    clock = Clock()
    repo = MemoryRepo(clock)
    repo.members[T] = [{"id": "m1", "name": "Sipho Dlamini", "whatsapp": "27800000001", "role": "staff"}]
    repo.settings[T] = {"tenant_id": T, "mode": "live"}
    repo.rules[(T, "m1")] = {"staff_share_bp": 7000, "tip_rule": "direct"}
    repo.vat[T] = {"vat_number": "4123456789", "vat_registered": True}
    msg, gw = FakeMessenger(), FakeGateway()
    svc = TapService(repo, msg, gw, TapConfig(
        pepper="p", public_base_url="https://api.test", encrypt=lambda x: "enc:" + x, decrypt=lambda x: x[4:],
        clock=clock, enabled=tap_api.tenant_enabled, receipt_secret="rs", receipt_base_url="https://dash.test"))
    tag = repo.add_tag(T, "tag-m1", bound_id="m1")
    monkeypatch.setattr(tap_api, "get_service", lambda: svc)
    monkeypatch.setattr(tap_api.settings, "tap_to_pay_tenants", "")
    tap_api._mode_cache.clear()
    return svc, repo, msg, gw, tag


async def pay(svc, repo, msg, gw, tag, tip="10"):
    svc.create_bill(tenant_id=T, tag_id=tag["id"], amount_cents=50000, description="Beginner lesson",
                    staff_id="m1", created_by="m1")
    tok = re.search(r"text=PAY%20(\S+)", svc.tap(tag["code"])).group(1)
    await svc.handle_text(T, CUST, f"PAY {tok}")
    sid = next(iter(repo.sessions))
    await svc.handle_interactive(T, CUST, f"kb:tip:{sid}:{tip}")
    await svc.handle_interactive(T, CUST, f"kb:pay:{sid}")
    gw.itn = {"reference": REF_PREFIX + sid, "paid": True, "amount_cents": 55000, "pf_payment_id": "P", "fee_cents": 0}
    assert await svc.confirm_payment(T, {}, b"", {}) == "paid"
    return list(repo.payments.values())[0]


async def test_slip_carries_a_view_receipt_button_with_a_working_link(env):
    svc, repo, msg, gw, tag = env
    p = await pay(svc, repo, msg, gw, tag)
    (tenant, phone, body, label, url), = [l for l in msg.links if l[3] == "View receipt"]
    assert "Paid R 550.00" in body and "http" not in body              # the address is not shown in the message
    assert label == "View receipt" and url.startswith("https://dash.test/r/") and " " not in url
    assert rc.read_token("rs", url.rsplit("/", 1)[1]) == (p["id"], 0)


async def test_pay_link_is_sent_as_a_button(env):
    svc, repo, msg, gw, tag = env
    await pay(svc, repo, msg, gw, tag)
    pay_btn = [l for l in msg.links if l[3].startswith("Pay R")]
    assert pay_btn and "http" not in pay_btn[0][2] and "/v1/tap/pay/" in pay_btn[0][4]
    assert pay_btn[0][3] == "Pay R 550.00"[:20] or len(pay_btn[0][3]) <= 20


async def test_no_link_when_receipts_not_configured(env):
    svc, repo, msg, gw, tag = env
    svc.cfg.receipt_secret = ""
    await pay(svc, repo, msg, gw, tag)
    assert not any("receipt" in m[3].lower() for m in msg.to(CUST, "text"))


@pytest.fixture
def client(env):
    app.dependency_overrides[require_tenant_actor] = lambda: {"user_id": "u", "email": "o@x.co"}
    yield TestClient(app, follow_redirects=False)
    app.dependency_overrides.pop(require_tenant_actor, None)


async def test_public_receipt_is_customer_safe(env):
    svc, repo, msg, gw, tag = env
    p = await pay(svc, repo, msg, gw, tag)
    token = rc.make_token("rs", p["id"], 0)
    r = TestClient(app).get(f"/v1/tap/receipt/{token}")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store" and "noindex" in r.headers["x-robots-tag"]
    d = r.json()
    assert (d["bill_cents"], d["tip_cents"], d["total_cents"], d["currency"]) == (50000, 5000, 55000, "ZAR")
    assert d["merchant"] == "Bean and Brew Coffee" and d["description"] == "Beginner lesson" and d["served_by"] == "Sipho"
    assert d["vat_number"] == "4123456789" and d["vat_cents"] == 6522
    assert len(d["ref"]) == 8
    blob = r.text
    for secret in ("4482", "enc:", "share", "payer", "27800000001", CUST):
        assert secret not in blob


async def test_vat_hidden_when_not_registered(env):
    svc, repo, msg, gw, tag = env
    repo.vat[T] = {"vat_number": "4123456789", "vat_registered": False}
    p = await pay(svc, repo, msg, gw, tag)
    d = TestClient(app).get(f"/v1/tap/receipt/{rc.make_token('rs', p['id'], 0)}").json()
    assert d["vat_number"] is None and d["vat_cents"] is None


async def test_bad_wrong_nonce_and_revoked_links_all_look_the_same(env, client):
    svc, repo, msg, gw, tag = env
    p = await pay(svc, repo, msg, gw, tag)
    good = rc.make_token("rs", p["id"], 0)
    c = TestClient(app)
    assert c.get(f"/v1/tap/receipt/{good}").status_code == 200
    bodies = set()
    for bad in ("junk", rc.make_token("wrong-secret", p["id"], 0), rc.make_token("rs", p["id"], 5),
                rc.make_token("rs", "no-such-payment", 0)):
        r = c.get(f"/v1/tap/receipt/{bad}")
        assert r.status_code == 404
        bodies.add(r.text)
    assert len(bodies) == 1                                  # no oracle: every failure is identical
    assert client.post(f"/v1/tap/{T}/payments/{p['id']}/receipt/revoke").status_code == 200
    assert c.get(f"/v1/tap/receipt/{good}").status_code == 404


async def test_cannot_revoke_another_tenants_receipt(env, client):
    svc, repo, msg, gw, tag = env
    p = await pay(svc, repo, msg, gw, tag)
    assert client.post(f"/v1/tap/tenant-b/payments/{p['id']}/receipt/revoke").status_code == 404
    assert client.get(f"/v1/tap/receipt/{rc.make_token('rs', p['id'], 0)}").status_code == 200


async def test_paid_detail_gives_coach_app_the_date_and_reference(env):
    svc, repo, msg, gw, tag = env
    p = await pay(svc, repo, msg, gw, tag)
    d = repo.paid_detail(T, next(iter(repo.bills)), "m1")
    assert d["ref"] == p["id"][:8].upper() and d["paid_at"] and d["share_cents"] == 40000
