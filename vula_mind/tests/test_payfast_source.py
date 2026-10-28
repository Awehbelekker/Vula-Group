"""PayFast ITN source check (vula/payfast_source.py): log by default, enforce only when told."""
import pytest
from fastapi.testclient import TestClient

from vula import payfast_source as ps

RANGES = "197.97.145.144/28"


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    monkeypatch.setattr(ps, "_resolve", lambda hosts: frozenset({"41.0.0.9"}))
    ps._cache.update(ips=frozenset(), at=0.0)
    yield
    ps._cache.update(ips=frozenset(), at=0.0)


def test_caller_is_the_last_forwarded_hop_not_whatever_the_caller_sent():
    assert ps.caller_ip({"x-forwarded-for": "197.97.145.150, 8.8.8.8"}, "10.0.0.1") == "8.8.8.8"
    assert ps.caller_ip({}, "10.0.0.1") == "10.0.0.1"


async def test_modes():
    fwd = lambda ip: {"x-forwarded-for": ip}
    assert await ps.allowed(fwd("41.0.0.9"), "", mode="enforce", ranges="")            # resolved host
    assert await ps.allowed(fwd("197.97.145.150"), "", mode="enforce", ranges=RANGES)   # published range
    assert not await ps.allowed(fwd("8.8.8.8"), "", mode="enforce", ranges=RANGES)
    assert await ps.allowed(fwd("8.8.8.8"), "", mode="log", ranges=RANGES)              # logged, processed
    assert await ps.allowed(fwd("8.8.8.8"), "", mode="off", ranges=RANGES)


async def test_unresolvable_and_no_ranges_fails_open(monkeypatch):
    monkeypatch.setattr(ps, "_resolve", lambda hosts: frozenset())
    assert await ps.allowed({"x-forwarded-for": "8.8.8.8"}, "", mode="enforce", ranges="")


def test_webhook_ignores_a_forged_itn_in_enforce_mode(monkeypatch):
    from vula.api.server import app
    from vula.tap import api as tap_api
    from config import settings
    seen = []

    async def handled(tenant_id, headers, body, form):
        seen.append(form.get("m_payment_id"))
        return "ok"
    monkeypatch.setattr(tap_api, "try_handle_itn", handled)
    monkeypatch.setattr(settings, "payfast_itn_source_check", "enforce")
    monkeypatch.setattr(settings, "payfast_itn_ip_ranges", RANGES)
    c = TestClient(app)
    form = {"m_payment_id": "kb-s1", "payment_status": "COMPLETE"}
    assert c.post("/v1/payments/webhook/t1/payfast", data=form, headers={"x-forwarded-for": "8.8.8.8"}).json() == {"received": True}
    assert seen == []
    c.post("/v1/payments/webhook/t1/payfast", data=form, headers={"x-forwarded-for": "197.97.145.150"})
    assert seen == ["kb-s1"]
