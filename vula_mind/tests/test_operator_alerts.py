"""Operator alerts come from Vula's own WhatsApp number, never a business's (31 Oct 2026, Ian).

They used to go out on Off the Hook's number (settings.local_llm_alert_tenant / the env default
number), so a client's line carried Vula's alerts and other clients' names."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import settings
from vula.integrations import platform_support as ps


class _Resp:
    def __init__(self, ok=True, data=None):
        self.is_success = ok
        self._data = data or {}
        self.text = "" if ok else "error"

    def json(self):
        return self._data


def _httpx(calls, responses):
    """httpx.AsyncClient stand-in: records (method, url, json) and answers from `responses`."""
    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            calls.append(("POST", url, json, headers))
            return responses.pop(0) if responses else _Resp()

        async def get(self, url, headers=None, params=None):
            calls.append(("GET", url, params, headers))
            return responses.pop(0) if responses else _Resp(data={"data": []})
    return _Client


@pytest.fixture
def vula_number(monkeypatch):
    for k, v in {"platform_support_phone": "0645755210", "platform_wa_phone_id": "VULA_PNID",
                 "platform_waba_id": "VULA_WABA", "platform_wa_token": "vula-token",
                 "whatsapp_phone_id": "ENV_DEFAULT_PNID", "whatsapp_token": "env-token",
                 "local_llm_alert_tenant": "off-the-hook"}.items():
        monkeypatch.setattr(settings, k, v)


@pytest.mark.asyncio
async def test_alert_goes_from_vulas_number_to_the_operator_as_the_alert_template(vula_number, monkeypatch):
    calls = []
    monkeypatch.setattr("httpx.AsyncClient", _httpx(calls, [_Resp()]))
    with patch("vula.api.whatsapp._get_tenant_wa_creds", new=AsyncMock()) as tenant_creds:
        assert await ps.alert_operator("🔧 Vula setup: 1 thing(s) still to do.")
    tenant_creds.assert_not_awaited()
    method, url, body, headers = calls[0]
    assert url.endswith("/VULA_PNID/messages")
    assert headers["Authorization"] == "Bearer vula-token"
    assert body["to"] == "27645755210"
    assert body["template"]["name"] == ps.OPERATOR_TEMPLATE
    assert body["template"]["components"][0]["parameters"][0]["text"].startswith("🔧 Vula setup")


@pytest.mark.asyncio
async def test_free_text_fallback_also_from_vulas_number(vula_number, monkeypatch):
    calls = []
    monkeypatch.setattr("httpx.AsyncClient", _httpx(calls, [_Resp(ok=False), _Resp()]))
    assert await ps.alert_operator("the local AI computer is down")
    assert [c[1] for c in calls] == [c[1] for c in calls if c[1].endswith("/VULA_PNID/messages")]
    assert calls[1][2]["type"] == "text"


@pytest.mark.asyncio
async def test_without_vulas_number_nothing_is_sent_not_even_from_a_tenant(vula_number, monkeypatch):
    monkeypatch.setattr(settings, "platform_wa_phone_id", "")
    calls = []
    monkeypatch.setattr("httpx.AsyncClient", _httpx(calls, []))
    with (
        patch("vula.api.whatsapp._send_wa_template", new=AsyncMock(return_value=True)) as tenant_template,
        patch("vula.api.whatsapp._send_reply", new=AsyncMock(return_value=True)) as tenant_send,
    ):
        assert await ps.alert_operator("🔧 Vula setup: kelp-boardbags: no way to take payment") is False
    assert calls == []
    tenant_template.assert_not_awaited()
    tenant_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_without_a_support_phone_nothing_is_sent(vula_number, monkeypatch):
    monkeypatch.setattr(settings, "platform_support_phone", "")
    calls = []
    monkeypatch.setattr("httpx.AsyncClient", _httpx(calls, []))
    assert await ps.alert_operator("anything") is False
    assert calls == []


@pytest.mark.asyncio
async def test_alert_never_raises(vula_number, monkeypatch):
    class _Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("network down")
    monkeypatch.setattr("httpx.AsyncClient", _Boom)
    assert await ps.alert_operator("x") is False


@pytest.mark.asyncio
async def test_feedback_reaches_the_operator_from_vulas_number(vula_number, monkeypatch):
    calls = []
    monkeypatch.setattr("httpx.AsyncClient", _httpx(calls, [_Resp()]))
    with patch("vula.integrations.platform_support._client", return_value=MagicMock()):
        await ps.forward("off-the-hook", "27821234567", "Staci", "the vula app is broken")
    assert calls and calls[0][1].endswith("/VULA_PNID/messages")
    text = calls[0][2]["template"]["components"][0]["parameters"][0]["text"]
    assert "Staci" in text and "off-the-hook" in text and "the vula app is broken" in text


@pytest.mark.asyncio
async def test_feedback_text_stays_out_of_the_log_when_not_sent(vula_number, monkeypatch, caplog):
    monkeypatch.setattr(settings, "platform_wa_token", "")
    with patch("vula.integrations.platform_support._client", return_value=MagicMock()):
        await ps.forward("off-the-hook", "27821234567", "Staci", "a private complaint")
    assert "a private complaint" not in caplog.text


@pytest.mark.asyncio
async def test_operator_template_submitted_to_vulas_waba_once(vula_number, monkeypatch):
    calls = []
    monkeypatch.setattr("httpx.AsyncClient", _httpx(calls, [_Resp(data={"data": []}),
                                                            _Resp(data={"id": "1", "status": "PENDING"})]))
    out = await ps.ensure_operator_template()
    assert out["status"] == "PENDING"
    get, post = calls
    assert get[1].endswith("/VULA_WABA/message_templates") and post[1].endswith("/VULA_WABA/message_templates")
    body = post[2]["components"][0]["text"]
    # Meta refuses a body that starts or ends with a variable.
    assert not body.startswith("{{") and not body.rstrip().endswith("}}")

    calls.clear()
    monkeypatch.setattr("httpx.AsyncClient", _httpx(calls, [_Resp(data={"data": [{"status": "APPROVED"}]})]))
    assert (await ps.ensure_operator_template())["already_exists"]
    assert [c[0] for c in calls] == ["GET"]


@pytest.mark.asyncio
async def test_operator_template_skipped_without_vulas_number(vula_number, monkeypatch):
    monkeypatch.setattr(settings, "platform_waba_id", "")
    assert "skipped" in await ps.ensure_operator_template()


def test_connecting_a_business_no_longer_submits_a_template_to_its_account():
    import inspect
    from vula.api import whatsapp_connect
    assert "ensure_template" not in inspect.getsource(whatsapp_connect)
    assert not hasattr(ps, "ensure_template")


@pytest.mark.asyncio
async def test_template_parameter_is_one_line_free_text_keeps_the_layout(vula_number, monkeypatch):
    """Meta refuses a template parameter with newlines/tabs/5+ spaces (the stalled-server alert
    carries a stack trace)."""
    calls = []
    monkeypatch.setattr("httpx.AsyncClient", _httpx(calls, [_Resp(ok=False), _Resp()]))
    text = "event loop stalled for 69s:\n  File \"x.py\", line 1\n\tin send"
    assert await ps.alert_operator(text)
    param = calls[0][2]["template"]["components"][0]["parameters"][0]["text"]
    assert "\n" not in param and "\t" not in param and "     " not in param
    assert calls[1][2]["text"]["body"] == text
