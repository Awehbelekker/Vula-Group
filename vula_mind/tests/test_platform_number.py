"""Vula's own WhatsApp number connected like a client's (5 Nov 2026, Ian: "test it like a client will
onboard"): Settings › WhatsApp › Connect on Vula's own business saves the number with its token
encrypted in the database; PLATFORM_TENANT_ID names that business and Vula uses its number for
its own messages, without the token ever being copied anywhere. Made-up ids; no network.
"""
import secrets
from types import SimpleNamespace

import pytest

from config import settings
from vula import readiness
from vula.email_imap.credentials import encrypt_secret
from vula.integrations import platform_support as ps

VULA = "vula"


class _Q:
    def __init__(self, rows): self.rows, self.f = rows, []
    def select(self, *a, **k): return self
    def eq(self, k, v): self.f.append((k, v)); return self
    def execute(self):
        return SimpleNamespace(data=[dict(r) for r in self.rows if all(r.get(k) == v for k, v in self.f)])


class _DB:
    def __init__(self, rows): self.rows = rows
    def table(self, name): return _Q(self.rows if name == "vula_whatsapp_accounts" else [])


@pytest.fixture()
def world(monkeypatch):
    tok = "tok-" + secrets.token_hex(8)      # built at runtime: never a token-looking literal
    rows = [
        {"tenant_id": VULA, "phone_number_id": "VULA-MAIN", "waba_id": "VULA-WABA", "phone_number": "+27 82 000 0009",
         "status": "connected", "is_main": True, "access_token": encrypt_secret(tok)},
        {"tenant_id": "seaview", "phone_number_id": "SEAVIEW-1", "waba_id": "SEA-WABA", "phone_number": "+27215550000",
         "status": "connected", "is_main": True, "access_token": encrypt_secret("other-" + tok)},
    ]
    db = _DB(rows)
    monkeypatch.setattr("vula.commerce.service._client", lambda: db)
    for k, v in {"platform_tenant_id": VULA, "platform_wa_phone_id": "", "platform_wa_token": "",
                 "platform_waba_id": "", "platform_wa_number": "", "whatsapp_token": "server-wide"}.items():
        monkeypatch.setattr(settings, k, v)
    ps.forget_platform_number()
    yield SimpleNamespace(rows=rows, tok=tok)
    ps.forget_platform_number()


def test_vulas_number_comes_from_its_own_business_with_the_token_from_the_database(world):
    c = ps.platform_creds()
    assert (c["phone_id"], c["waba_id"], c["token"], c["number"]) == ("VULA-MAIN", "VULA-WABA", world.tok, "27820000009")
    assert ps.is_platform_number("VULA-MAIN") and not ps.is_platform_number("SEAVIEW-1")
    assert not ps.is_platform_number("")
    assert ps.platform_number() == "27820000009"


def test_with_several_numbers_on_vulas_business_the_named_one_is_used(world, monkeypatch):
    world.rows.append({"tenant_id": VULA, "phone_number_id": "VULA-SETUP", "waba_id": "VULA-WABA",
                       "phone_number": "+27820000010", "status": "connected", "is_main": False})
    monkeypatch.setattr(settings, "platform_wa_phone_id", "VULA-SETUP")
    ps.forget_platform_number()
    c = ps.platform_creds()
    assert c["phone_id"] == "VULA-SETUP" and c["token"] == "server-wide"   # no token of its own: Vula's WABA token


def test_another_business_number_is_never_vulas(world, monkeypatch):
    monkeypatch.setattr(settings, "platform_wa_phone_id", "SEAVIEW-1")   # a typo'd id pointing at a client's number
    ps.forget_platform_number()
    assert ps.platform_creds() is None


def test_disconnected_falls_back_to_the_railway_values_or_nothing(world, monkeypatch):
    world.rows[0]["status"] = "disconnected"
    ps.forget_platform_number()
    assert ps.platform_creds() is None
    gaps = [g["what"] for g in readiness.platform_gaps([])]
    assert any("not connected on business 'vula'" in g for g in gaps)
    monkeypatch.setattr(settings, "platform_wa_phone_id", "ENV-PNID")
    monkeypatch.setattr(settings, "platform_wa_token", "env-" + world.tok)
    ps.forget_platform_number()
    assert ps.platform_creds()["phone_id"] == "ENV-PNID"


def test_connecting_the_number_again_is_picked_up_at_once(world):
    from vula.api import whatsapp_connect
    assert ps.platform_creds()["phone_id"] == "VULA-MAIN"
    world.rows[0]["phone_number_id"] = "VULA-NEW"
    assert ps.platform_creds()["phone_id"] == "VULA-MAIN"          # cached
    whatsapp_connect._forget_health(VULA)
    assert ps.platform_creds()["phone_id"] == "VULA-NEW"


def test_a_read_failure_is_no_number_not_a_crash(world, monkeypatch):
    def boom():
        raise RuntimeError("db down")
    monkeypatch.setattr("vula.commerce.service._client", boom)
    ps.forget_platform_number()
    assert ps.platform_creds() is None and not ps.is_platform_number("VULA-MAIN")


def test_readiness_has_no_gap_once_it_is_connected(world):
    assert not any("Vula's own WhatsApp number" in g["what"] for g in readiness.platform_gaps([]))
