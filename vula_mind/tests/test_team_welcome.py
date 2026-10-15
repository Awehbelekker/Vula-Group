"""The first-message welcome for a business's owner or team (2026-10-13, SeedleMe). It sent the
owner to "admin.<the shop's own domain>" — admin.seedleme.com doesn't exist — and never said
that the business profile, which customers are answered from, was 2 of 13 done."""
from types import SimpleNamespace

import pytest

from config import settings
from vula.api import whatsapp


class _Q:
    def __init__(self, db):
        self.db, self.op = db, "select"

    def select(self, *_a): return self
    def eq(self, *_a): return self
    def is_(self, *_a): return self
    def limit(self, *_a): return self

    def update(self, payload):
        self.op = "update"
        return self

    def execute(self):
        if self.op == "update":
            claimed, self.db.claimed = not self.db.claimed, True
            return SimpleNamespace(data=[{"id": "m1"}] if claimed else [])
        return SimpleNamespace(data=[self.db.member])


class _DB:
    def __init__(self, member):
        self.member, self.claimed = member, False

    def table(self, _name):
        return _Q(self)


@pytest.fixture()
def welcome(monkeypatch):
    sent = []

    def run(tenant_id, role="owner", answered=2, total=13, trading_as="SeedleMe"):
        db = _DB({"id": "m1", "name": "Matt Wilkinson", "role": role, "welcomed_at": None})

        async def invoice_settings(tid):
            return {"trading_as": trading_as}

        async def send(phone, text, tid=None, **k):
            sent.append(text)

        async def menu(*_a, **_k):
            return True

        monkeypatch.setattr("vula.commerce.service._client", lambda: db)
        monkeypatch.setattr("vula.commerce.service.get_invoice_settings", invoice_settings)
        monkeypatch.setattr("vula.api.tenants.tenant_profile", lambda tid: {"sells_products": True})
        monkeypatch.setattr("vula.commerce.business_profile.status",
                            lambda tid: {"answered": answered, "total": total})
        monkeypatch.setattr(whatsapp, "_send_reply", send)
        monkeypatch.setattr(whatsapp, "_send_staff_capability_menu", menu)
        monkeypatch.setattr(settings, "dashboard_url", "https://vuladashboard.vercel.app")
        sent.clear()
        return sent
    return run


@pytest.mark.asyncio
async def test_a_new_shop_owner_gets_the_real_dashboard_and_is_offered_the_profile(welcome):
    sent = welcome("seedleme")
    assert await whatsapp._maybe_welcome_new_owner("seedleme", "0662545068") is True
    msg = sent[0]
    assert "Hey Matt!" in msg and "*SeedleMe*" in msg
    assert "https://vuladashboard.vercel.app" in msg and "admin.seedleme.com" not in msg
    assert "2 of 13 done" in msg and "*set up my profile*" in msg


@pytest.mark.asyncio
async def test_businesses_with_their_own_dashboard_address_keep_it(welcome):
    sent = welcome("off-the-hook", trading_as="Off the Hook")
    await whatsapp._maybe_welcome_new_owner("off-the-hook", "0820000000")
    assert "https://admin.offthehook.co.za" in sent[0]
    sent = welcome("digg-demo", trading_as="DIGG")
    await whatsapp._maybe_welcome_new_owner("digg-demo", "0820000000")
    assert "https://digg.vula-ai.com" in sent[0] and "admin.digg" not in sent[0]


@pytest.mark.asyncio
async def test_no_profile_nudge_when_it_is_done_or_for_staff(welcome):
    sent = welcome("seedleme", answered=13)
    await whatsapp._maybe_welcome_new_owner("seedleme", "0662545068")
    assert "set up my profile" not in sent[0]
    sent = welcome("seedleme", role="staff")
    await whatsapp._maybe_welcome_new_owner("seedleme", "0660000000")
    assert "set up my profile" not in sent[0]


@pytest.mark.asyncio
async def test_the_welcome_is_sent_once(welcome):
    sent = welcome("seedleme")
    first = await whatsapp._maybe_welcome_new_owner("seedleme", "0662545068")
    again = await whatsapp._maybe_welcome_new_owner("seedleme", "0662545068")   # the other worker / a retry
    assert (first, again) == (True, False) and len(sent) == 1


# ── 15 Oct: each business's own <name>.vula-ai.com, once it's live ──────────────────────

def _cfg(monkeypatch, domains):
    monkeypatch.setattr("vula.api.tenants.get_config", lambda tid, fresh=False: {"domains": domains})


def test_a_business_with_its_own_vula_address_is_sent_there(monkeypatch):
    from vula.api.tenants import dashboard_url_for
    monkeypatch.setattr(settings, "dashboard_url", "https://vuladashboard.vercel.app")
    _cfg(monkeypatch, ["seedleme.vula-ai.com"])
    assert dashboard_url_for("seedleme") == "https://seedleme.vula-ai.com"
    _cfg(monkeypatch, ["shop.seedleme.com", "admin.seedleme.co.za"])     # a shop host is never the dashboard
    assert dashboard_url_for("seedleme") == "https://admin.seedleme.co.za"
    _cfg(monkeypatch, ["shop.seedleme.com"])
    assert dashboard_url_for("seedleme") == "https://vuladashboard.vercel.app"
    _cfg(monkeypatch, [])
    assert dashboard_url_for("seedleme") == "https://vuladashboard.vercel.app"
    assert dashboard_url_for("off-the-hook") == "https://admin.offthehook.co.za"
    assert dashboard_url_for("digg-demo") == "https://digg.vula-ai.com"


@pytest.mark.asyncio
async def test_the_welcome_carries_the_business_own_address(welcome, monkeypatch):
    sent = welcome("seedleme")
    _cfg(monkeypatch, ["seedleme.vula-ai.com"])
    await whatsapp._maybe_welcome_new_owner("seedleme", "27662545068")
    assert "https://seedleme.vula-ai.com" in sent[0]
