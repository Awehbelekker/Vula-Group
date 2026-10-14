"""The first message to imported contacts is the business's own intro, never Off the Hook's
(2026-10-14: every business fell back to "oth_intro_optin" — "This is Vula, Off the Hook's new
WhatsApp ordering assistant… fish & chicken specials")."""
import httpx
import pytest

from vula.commerce import onboarding


class _DB:
    def __init__(self, counts):
        self.counts = counts

    def table(self, name):
        db = self

        class Q:
            def __init__(self):
                self.f = {}

            def select(self, *a, **k):
                return self

            def eq(self, k, v):
                self.f[k] = v
                return self

            def limit(self, *a):
                return self

            def update(self, *a, **k):
                return self

            def execute(self):
                n = db.counts.get(self.f.get("name"))
                return type("R", (), {"data": [{"param_count": n}] if n is not None else []})()
        return Q()


def test_each_business_has_its_own_intro():
    assert onboarding.intro_template("seedleme") == "seedleme_intro_optin"
    assert onboarding.intro_template("off-the-hook") == "oth_intro_optin"     # its approved one, kept


def test_params_follow_the_template(monkeypatch):
    monkeypatch.setattr(onboarding, "_client", lambda: _DB({"oth_intro_optin": 1}))
    monkeypatch.setattr(onboarding, "_business_name", lambda tid: "SeedleMe")
    contact = {"name": "Thandi Mokoena"}
    assert [p["text"] for p in onboarding._intro_params("seedleme", "seedleme_intro_optin", contact)] == \
        ["Thandi", "SeedleMe"]
    assert [p["text"] for p in onboarding._intro_params("off-the-hook", "oth_intro_optin", contact)] == ["Thandi"]


@pytest.mark.asyncio
async def test_dashboard_batch_defaults_to_the_businesss_own_template(monkeypatch):
    from vula.api import commerce as api
    seen = {}

    async def send_batch(tid, *, template, language="en", limit=50, test_phone=None):
        seen["template"] = template
        return {"sent": 1}

    monkeypatch.setattr(onboarding, "send_batch", send_batch)
    await api.admin_onboarding_send_batch("seedleme", {"test_phone": "0821234567"})
    assert seen["template"] == "seedleme_intro_optin"


@pytest.mark.asyncio
async def test_send_batch_sends_name_and_business(monkeypatch):
    import vula.api.whatsapp as wa
    posted = []

    async def creds(tid):
        return {"phone_id": "1", "token": "t"}

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            posted.append(json)
            return httpx.Response(200, json={}, request=httpx.Request("POST", url))

    monkeypatch.setattr(wa, "_get_tenant_wa_creds", creds)
    monkeypatch.setattr(onboarding.httpx, "AsyncClient", Client)
    monkeypatch.setattr(onboarding, "_client", lambda: _DB({}))
    monkeypatch.setattr(onboarding, "_business_name", lambda tid: "SeedleMe")
    res = await onboarding.send_batch("seedleme", template="seedleme_intro_optin", test_phone="0821234567")
    assert res["sent"] == 1
    params = posted[0]["template"]["components"][0]["parameters"]
    assert [p["text"] for p in params] == ["there", "SeedleMe"]
