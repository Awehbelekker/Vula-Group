"""Tap-to-pay HTTP routes, WhatsApp/ITN hooks and the tenant guard, over the in-memory fakes."""
import re
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from tests.tap_fakes import Clock, FakeGateway, FakeMessenger, MemoryRepo
from vula.api import server
from vula.api.server import app
from vula.tap import api as tap_api
from vula.tap.service import REF_PREFIX, TapConfig, TapService

T = "tenant-a"
client = TestClient(app, follow_redirects=False)


@pytest.fixture
def svc(monkeypatch):
    clock = Clock()
    repo = MemoryRepo(clock)
    s = TapService(repo, FakeMessenger(), FakeGateway(),
                   TapConfig(pepper="p", public_base_url="https://api.test",
                             encrypt=lambda x: "enc:" + x, decrypt=lambda x: x[4:], clock=clock))
    repo.add_tag(T, "coach-sipho", bound_id="coach")
    monkeypatch.setattr(tap_api.settings, "tap_to_pay_tenants", T)
    monkeypatch.setattr(tap_api, "get_service", lambda: s)
    return s


def test_tap_redirects_to_whatsapp(svc):
    r = client.get("/t/coach-sipho")
    assert r.status_code == 302
    assert re.match(r"https://wa\.me/27737815979\?text=PAY%20[\w-]{16,}$", r.headers["location"])


def test_unknown_tag_and_feature_off_show_not_verified(svc, monkeypatch):
    assert client.get("/t/nope").status_code == 404
    monkeypatch.setattr(tap_api.settings, "tap_to_pay_tenants", "")
    r = client.get("/t/coach-sipho")
    assert r.status_code == 404 and "couldn't verify" in r.text


def test_pay_link_expired_page(svc):
    r = client.get("/v1/tap/pay/nosuchsession/badnonce")
    assert r.status_code == 410 and "expired" in r.text


def test_return_pages_are_informational_only(svc):
    assert client.get("/v1/tap/done/abc").status_code == 200
    assert "Thank you" in client.get("/v1/tap/done/abc").text
    assert client.get("/v1/tap/cancelled/abc").status_code == 200
    assert not svc.repo.sessions      # nothing was marked or created


async def test_text_and_interactive_hooks_are_off_unless_enabled(svc, monkeypatch):
    assert await tap_api.try_handle_text(T, "27821114482", "what time do you open?") is False
    monkeypatch.setattr(tap_api.settings, "tap_to_pay_tenants", "someone-else")
    assert await tap_api.try_handle_text(T, "27821114482", "PAY aaaaaaaaaaaaaaaa") is False
    assert await tap_api.try_handle_interactive(T, "27821114482", "kb:pay:x") is False


async def test_itn_hook_only_takes_tap_references(svc, monkeypatch):
    assert await tap_api.try_handle_itn(T, {}, b"", {"m_payment_id": "invoice-uuid"}) is None
    monkeypatch.setattr(tap_api.settings, "tap_to_pay_tenants", "")
    assert await tap_api.try_handle_itn(T, {}, b"", {"m_payment_id": REF_PREFIX + "x"}) is None
    monkeypatch.setattr(tap_api.settings, "tap_to_pay_tenants", T)
    svc.gateway.itn = None            # bad signature
    assert await tap_api.try_handle_itn(T, {}, b"", {"m_payment_id": REF_PREFIX + "x"}) == "rejected"


async def test_a_handler_crash_never_takes_the_assistant_down(svc):
    with patch.object(svc, "handle_text", AsyncMock(side_effect=RuntimeError("boom"))):
        assert await tap_api.try_handle_text(T, "27821114482", "PAY aaaaaaaaaaaaaaaa") is False


def test_payfast_webhook_route_hands_tap_references_to_tap_service(svc):
    svc.gateway.itn = {"reference": "kb-unknown", "paid": True, "amount_cents": 100, "pf_payment_id": "9"}
    r = client.post("/v1/payments/webhook/tenant-a/payfast", data={"m_payment_id": "kb-unknown", "x": "1"})
    assert r.status_code == 200 and r.json() == {"received": True}


MERCHANT = [
    ("POST", "/v1/tap/off-the-hook/bills"),
    ("POST", "/v1/tap/off-the-hook/bills/b1/cancel"),
    ("POST", "/v1/tap/off-the-hook/bills/b1/release"),
]
PUBLIC = [
    ("GET", "/v1/tap/pay/s1/nonce"),
    ("GET", "/v1/tap/done/s1"),
    ("GET", "/v1/tap/cancelled/s1"),
    ("GET", "/t/coach-sipho"),
]


@pytest.mark.parametrize("method,path", MERCHANT)
async def test_merchant_routes_need_membership(method, path):
    assert (await server._guard_check(method, path, "")).status_code == 401
    with patch("vula.api.tenant_auth.is_tenant_member", AsyncMock(return_value=False)):
        assert (await server._guard_check(method, path, "Bearer t")).status_code == 403
    with patch("vula.api.tenant_auth.is_tenant_member", AsyncMock(return_value=True)):
        assert await server._guard_check(method, path, "Bearer t") is None


@pytest.mark.parametrize("method,path", PUBLIC)
async def test_customer_routes_stay_public(method, path):
    assert await server._guard_check(method, path, "") is None
