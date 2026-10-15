"""The sign-up form on vula-ai.com (vula/api/website_leads.py): a lead is stored and the Vula team
is told on WhatsApp; bots (honeypot), bad numbers and floods are turned away."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from vula.api import website_leads as wl


class _Q:
    def __init__(self, db):
        self.db, self.op, self.vals, self.filters = db, "select", None, []

    def select(self, *_a): return self
    def order(self, *_a, **_k): return self
    def limit(self, *_a): return self

    def insert(self, row):
        self.op, self.vals = "insert", row
        return self

    def eq(self, c, v):
        self.filters.append(lambda r, c=c, v=v: r.get(c) == v)
        return self

    def gte(self, *_a):
        return self

    def execute(self):
        if self.op == "insert":
            self.db.rows.append(dict(self.vals))
            return SimpleNamespace(data=[self.vals])
        return SimpleNamespace(data=[r for r in self.db.rows if all(f(r) for f in self.filters)])


@pytest.fixture
def env(monkeypatch):
    db = SimpleNamespace(rows=[])
    db.table = lambda name: _Q(db)
    sent = []
    monkeypatch.setattr(wl, "_db", lambda: db)
    monkeypatch.setattr(wl, "_recipients", lambda: ["27820001111"])

    async def send(to, text, tid="", **k):
        sent.append((to, text))
        return True
    monkeypatch.setattr("vula.api.whatsapp._send_reply", send)
    monkeypatch.setattr(wl.settings, "team_email", "")
    return SimpleNamespace(db=db, sent=sent)


def _req(ip="1.2.3.4"):
    return SimpleNamespace(headers={"x-forwarded-for": ip}, client=None)


@pytest.mark.asyncio
async def test_a_lead_is_stored_and_the_team_is_told(env):
    r = await wl.create_lead(wl.LeadIn(name=" Thandi  Mokoena ", business="Bay Bakery", whatsapp="082 123 4567",
                                       kind="Shop / food"), _req())
    assert r == {"ok": True}
    assert env.db.rows[0]["whatsapp"] == "27821234567" and env.db.rows[0]["name"] == "Thandi Mokoena"
    to, text = env.sent[0]
    assert to == "27820001111" and "Bay Bakery" in text and "https://wa.me/27821234567" in text


@pytest.mark.asyncio
async def test_bots_bad_numbers_and_floods(env):
    assert await wl.create_lead(wl.LeadIn(name="x", business="y", whatsapp="0821234567", website="spam.biz"),
                                _req()) == {"ok": True}
    assert not env.db.rows and not env.sent                     # honeypot: quietly dropped
    with pytest.raises(HTTPException) as bad:
        await wl.create_lead(wl.LeadIn(name="A", business="B", whatsapp="123"), _req())
    assert bad.value.status_code == 422
    for _ in range(wl.MAX_PER_IP):
        await wl.create_lead(wl.LeadIn(name="A", business="B", whatsapp="0821234567"), _req("9.9.9.9"))
    with pytest.raises(HTTPException) as many:
        await wl.create_lead(wl.LeadIn(name="A", business="B", whatsapp="0821234567"), _req("9.9.9.9"))
    assert many.value.status_code == 429
