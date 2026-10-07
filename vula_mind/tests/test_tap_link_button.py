"""URL-button messages: the CTA payload, and the plain-text fallback when WhatsApp refuses it."""
import pytest

from vula.api import whatsapp as wa
from vula.tap.adapters import WhatsAppMessenger


class _Resp:
    def __init__(self, ok=True):
        self.ok = ok

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError("400")


def _client_factory(sent, ok=True):
    class _C:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, json=None):
            sent.append((url, json))
            return _Resp(ok)
    return _C


async def test_cta_payload_shape(monkeypatch):
    sent = []
    monkeypatch.setattr(wa.httpx, "AsyncClient", _client_factory(sent))
    ok = await wa._send_wa_cta_url({"phone_id": "123", "token": "t"}, "27645755210",
                                   "Pay R 6.00 to Gerflor.", "Pay R 6.00 now please", "https://x.test/p/1")
    assert ok
    url, body = sent[0]
    assert url.endswith("/123/messages") and body["type"] == "interactive"
    inter = body["interactive"]
    assert inter["type"] == "cta_url" and inter["action"]["name"] == "cta_url"
    assert inter["action"]["parameters"]["url"] == "https://x.test/p/1"
    assert len(inter["action"]["parameters"]["display_text"]) <= 20          # WhatsApp's button limit
    assert "https://" not in inter["body"]["text"]


async def test_cta_failure_returns_false(monkeypatch):
    monkeypatch.setattr(wa.httpx, "AsyncClient", _client_factory([], ok=False))
    assert await wa._send_wa_cta_url({"phone_id": "1", "token": "t"}, "27", "b", "Pay", "https://x") is False


async def test_adapter_falls_back_to_plain_link(monkeypatch):
    texts = []

    async def creds(_t): return {"phone_id": "1", "token": "t"}
    async def no_cta(*a, **k): return False
    async def reply(phone, body, tenant_id): texts.append(body); return True

    monkeypatch.setattr(wa, "_get_tenant_wa_creds", creds)
    monkeypatch.setattr(wa, "_send_wa_cta_url", no_cta)
    monkeypatch.setattr(wa, "_send_reply", reply)
    assert await WhatsAppMessenger().link_button("t1", "+27 64 575 5210", "Pay R5.", "Pay", "https://x.test/p/1")
    assert texts == ["Pay R5.\nhttps://x.test/p/1"]


async def test_adapter_uses_button_when_it_works(monkeypatch):
    seen = []

    async def creds(_t): return {"phone_id": "1", "token": "t"}
    async def cta(c, number, body, label, url): seen.append((number, label, url)); return True
    async def reply(*a, **k): raise AssertionError("must not send the plain link when the button worked")

    monkeypatch.setattr(wa, "_get_tenant_wa_creds", creds)
    monkeypatch.setattr(wa, "_send_wa_cta_url", cta)
    monkeypatch.setattr(wa, "_send_reply", reply)
    assert await WhatsAppMessenger().link_button("t1", "+27 64 575 5210", "Pay R5.", "Pay", "https://x.test/p/1")
    assert seen == [("27645755210", "Pay", "https://x.test/p/1")]


def test_merchant_name_prefers_the_tenant_display_name(monkeypatch):
    from vula.api import tenants
    from vula.tap.repo import SupabaseRepo
    monkeypatch.setattr(tenants, "get_config", lambda t: {"display_name": "Gerflor Cape Town"})
    assert SupabaseRepo.__new__(SupabaseRepo).merchant_name("gerflor") == "Gerflor Cape Town"


def test_merchant_name_falls_back_when_config_is_missing(monkeypatch):
    from vula.api import tenants
    from vula.tap.repo import SupabaseRepo

    class _Q:
        def select(self, *a): return self
        def eq(self, *a): return self
        def limit(self, *a): return self
        def execute(self):
            class R: data = [{"company_name": "Gerflor — Western Cape Sales"}]
            return R()

    class _DB:
        def table(self, *_): return _Q()

    monkeypatch.setattr(tenants, "get_config", lambda t: {})
    repo = SupabaseRepo(client=_DB())
    assert repo.merchant_name("gerflor") == "Gerflor — Western Cape Sales"
