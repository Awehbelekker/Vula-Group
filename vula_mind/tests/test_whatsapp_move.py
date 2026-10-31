"""Moving a number from Vula's own WhatsApp account to the business's own (25 Oct, Ian: "Can Vula
transfer the current numbers to the tenants, keep them active").

Meta moves the number when the owner enters it in Embedded Signup; Vula then registers it on the
business's account (which completes the move), keeps its purpose and main-number place, and
retires the Vula-account row if Meta gave it a new id.
"""
import pytest
from fastapi import HTTPException

from config import settings
from vula.api import whatsapp_connect
from tests.test_whatsapp_purposes import T, _DB, _row


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.is_success = 200 <= status < 300
        self.text = str(body)
        self.content = b"x"

    def json(self):
        return self._body


@pytest.fixture()
def world(monkeypatch):
    d = _DB([_row("P0001", "shop", main=True, phone_number="+27 82 000 0001"),
             _row("P0002", "team", phone_number="+27 82 000 0002")])
    calls, alerts, meta = [], [], {"number": "+27 82 000 0001", "register_ok": True}

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        async def get(self, url, params=None, headers=None):
            calls.append(("GET", url))
            if url.endswith("/oauth/access_token"):
                return _Resp(200, {"access_token": "their-token"})
            return _Resp(200, {"display_phone_number": meta["number"], "verified_name": "Harbour & Vine"})

        async def post(self, url, headers=None, json=None):
            calls.append(("POST", url))
            if url.endswith("/register"):
                return _Resp(200 if meta["register_ok"] else 400, {"success": meta["register_ok"]})
            return _Resp(200, {"success": True})

    async def _alert(text):
        alerts.append(text)
        return True

    monkeypatch.setattr(whatsapp_connect.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(whatsapp_connect, "_supabase", lambda: d)
    monkeypatch.setattr("vula.commerce.service._client", lambda: d)
    monkeypatch.setattr(settings, "vula_fb_app_id", "app")
    monkeypatch.setattr(settings, "vula_fb_app_secret", "secret")
    monkeypatch.setattr(settings, "whatsapp_register_on_connect", False)    # a move registers anyway
    monkeypatch.setattr(settings, "whatsapp_registration_pin", "123456")
    monkeypatch.setattr("vula.email_imap.credentials.encrypt_secret", lambda s: f"enc:{s}")
    monkeypatch.setattr("vula.integrations.platform_support.alert_operator", _alert)
    return d, calls, alerts, meta


def _by(d, pnid):
    return next(r for r in d.tables["vula_whatsapp_accounts"] if r["phone_number_id"] == pnid)


async def _move(new_pnid="P0001", move="P0001"):
    return await whatsapp_connect.connect_whatsapp(whatsapp_connect.ConnectRequest(
        tenant_id=T, code="c", waba_id="THEIR-WABA", phone_number_id=new_pnid, move_phone_number_id=move))


@pytest.mark.asyncio
async def test_a_move_registers_on_their_account_and_keeps_its_place(world):
    d, calls, alerts, _ = world
    out = await _move()
    assert out.moved is True and "nothing changed for your customers" in out.move_note
    assert ("POST", "https://graph.facebook.com/v25.0/P0001/register") in calls
    row = _by(d, "P0001")
    assert row["waba_id"] == "THEIR-WABA" and row["access_token"] == "enc:their-token"   # theirs now
    assert row["purpose"] == "shop" and row["is_primary"] is True and row["status"] == "connected"
    assert not alerts


@pytest.mark.asyncio
async def test_a_new_id_from_meta_retires_the_vula_account_row_and_hands_main_over(world):
    d, *_ = world
    out = await _move(new_pnid="P0009")
    assert out.moved is True
    assert _by(d, "P0001")["status"] == "disconnected" and _by(d, "P0001")["access_token"] is None
    new = _by(d, "P0009")
    assert new["purpose"] == "shop" and new["is_primary"] is True and new["status"] == "connected"
    assert _by(d, "P0002")["status"] == "connected"                     # the other number untouched


@pytest.mark.asyncio
async def test_the_wrong_number_entered_moves_nothing(world):
    d, calls, _, meta = world
    meta["number"] = "+27 83 999 0000"
    with pytest.raises(HTTPException) as e:
        await _move()
    assert e.value.status_code == 409 and "+27 82 000 0001" in e.value.detail
    assert _by(d, "P0001")["access_token"] is None and not any(u.endswith("/register") for _, u in calls)


@pytest.mark.asyncio
async def test_only_a_number_on_vulas_account_can_be_moved(world):
    d, *_ = world
    _by(d, "P0002")["access_token"] = "enc:already-theirs"
    with pytest.raises(HTTPException) as e:
        await _move(new_pnid="P0002", move="P0002")
    assert e.value.status_code == 400
    with pytest.raises(HTTPException):
        await _move(move="SOMEONE-ELSES")


@pytest.mark.asyncio
async def test_a_move_meta_hasnt_finished_tells_vula_and_says_so(world):
    d, _, alerts, meta = world
    meta["register_ok"] = False
    out = await _move()
    assert out.moved is False and "Vula has been told" in out.move_note
    assert alerts and "+27 82 000 0001" in alerts[0] and T in alerts[0]
    assert _by(d, "P0001")["status"] == "connected"                     # it keeps working as before


@pytest.mark.asyncio
async def test_without_a_registration_pin_the_move_is_flagged_for_vula(world, monkeypatch):
    _, calls, alerts, _ = world
    monkeypatch.setattr(settings, "whatsapp_registration_pin", "")
    out = await _move()
    assert out.moved is False and not any(u.endswith("/register") for _, u in calls)
    assert "WHATSAPP_REGISTRATION_PIN" in alerts[0]
