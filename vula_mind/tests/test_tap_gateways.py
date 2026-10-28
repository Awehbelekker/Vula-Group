"""Tap to Pay through the business's own gateway (vula/tap/gateways.py), 28 Oct.

Groundwork for Stitch: checkout goes to the gateway the business chose, a payment notification is
verified by the gateway it came from, and the payment is booked under that gateway's name. Every
business keeps PayFast until another gateway is built and connected. A second fake gateway stands
in for the next one here; nothing about Stitch's own API is assumed.
"""
from unittest.mock import patch

import pytest

from tests.tap_fakes import Clock, FakeGateway, FakeMessenger, MemoryRepo
from vula.tap.gateways import GatewayRouter, tenant_choice
from vula.tap.service import REF_PREFIX, TapConfig, TapService

T = "tenant-a"


class OtherGateway(FakeGateway):
    label = "Other Pay"

    async def create_checkout(self, *, tenant_id, session, description):
        self.created.append(session["id"])
        return f"https://other.example/pay/{session['id']}"


@pytest.fixture
def env():
    clock = Clock()
    repo = MemoryRepo(clock)
    pf, other = FakeGateway(), OtherGateway()
    pf.label = "PayFast"
    choice = {"t": None}
    router = GatewayRouter({"payfast": pf, "other": other}, choose=lambda t: choice["t"])
    svc = TapService(repo, FakeMessenger(), router,
                     TapConfig(pepper="pep", public_base_url="https://api.test",
                               encrypt=lambda s: "enc:" + s, decrypt=lambda s: s[4:], clock=clock))
    tag = repo.add_tag(T, "coach-sipho", bound_id="coach")
    repo.rules[(T, "coach")] = {"staff_share_bp": 7000, "tip_rule": "direct"}
    repo.team[(T, "coach")] = ["27800000001"]
    repo.team[(T, None)] = ["27800000001"]
    return svc, repo, pf, other, choice, tag


async def _checkout(svc, tag):
    svc.create_bill(tenant_id=T, tag_id=tag["id"], amount_cents=50000, description="2 beers",
                    staff_id="coach", created_by="u1")
    v = svc.express.view("coach-sipho", "browser-1")
    return v, await svc.express.checkout("coach-sipho", v["token"], "browser-1", tip="0")


async def test_payfast_stays_the_default(env):
    svc, repo, pf, other, choice, tag = env
    v, url = await _checkout(svc, tag)
    assert url.startswith("https://pay.example/") and pf.created and not other.created
    assert v["pay_with"] == "PayFast"
    choice["t"] = "not-built-yet"                          # an unknown choice never blocks a payment
    assert svc.gateway.name_for(T) == "payfast"


async def test_checkout_and_confirmation_go_through_the_chosen_gateway(env):
    svc, repo, pf, other, choice, tag = env
    choice["t"] = "other"
    v, url = await _checkout(svc, tag)
    assert url.startswith("https://other.example/pay/") and v["pay_with"] == "Other Pay"
    s = next(iter(repo.sessions.values()))
    other.itn = {"reference": REF_PREFIX + s["id"], "paid": True, "amount_cents": 50000,
                 "provider_payment_id": "OP-77", "fee_cents": 0}
    # PayFast's webhook can't confirm a payment the other gateway took
    assert await svc.confirm_payment(T, {}, b"", {}, provider="payfast") == "rejected"
    assert await svc.confirm_payment(T, {}, b"", {}, provider="other") == "paid"
    pay = next(iter(repo.payments.values()))
    assert (pay["provider"], pay["pf_payment_id"]) == ("other", "OP-77")
    assert await svc.confirm_payment(T, {}, b"", {}, provider="other") == "duplicate"
    assert await svc.confirm_payment(T, {}, b"", {}, provider="nobody") == "rejected"


def test_a_business_uses_its_default_provider_when_it_can_do_tap_to_pay():
    rows = [{"provider": "yoco", "is_default": True}, {"provider": "payfast"}, {"provider": "other"}]
    with patch("vula.payments.list_providers", return_value=rows):
        assert tenant_choice(T, {"payfast", "other"}) == "payfast"     # Yoco can't do Tap to Pay
        rows[2]["is_default"] = True
        assert tenant_choice(T, {"payfast", "other"}) == "other"
    with patch("vula.payments.list_providers", side_effect=RuntimeError("db down")):
        assert tenant_choice(T, {"payfast"}) is None                   # → the router's default


async def test_payfast_refuses_another_gateways_notification():
    from vula.tap.adapters import PayFastGateway
    assert await PayFastGateway("https://api.test").verify_itn(
        tenant_id=T, headers={}, body=b"", form={}, provider="other") is None


async def test_a_notification_that_isnt_a_tap_payment_carries_on_to_invoices(monkeypatch):
    from vula.tap import api as tap_api

    class Svc:
        async def confirm_payment(self, *a, **k):
            return "not_tap"
    monkeypatch.setattr(tap_api, "get_service", lambda: Svc())
    monkeypatch.setattr(tap_api, "tenant_enabled", lambda t: True)
    assert await tap_api.try_handle_itn(T, {}, b"", {"m_payment_id": "kb-x"}, "payfast") is None
    assert await tap_api.try_handle_itn(T, {}, b"", {}, "not-a-tap-gateway") is None


def test_the_choice_is_looked_up_once_a_minute():
    calls, now = [], [0.0]
    r = GatewayRouter({"payfast": FakeGateway(), "other": OtherGateway()},
                      choose=lambda t: calls.append(t) or "other", ttl_s=60, clock=lambda: now[0])
    assert [r.name_for(T), r.name_for(T)] == ["other", "other"] and len(calls) == 1
    now[0] = 61
    r.name_for(T)
    assert len(calls) == 2
