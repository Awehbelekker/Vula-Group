"""PR E (2026-09-29): Master › Tenants shows a health light, the reasons and the last message, so
a dormant tenant (kelp-boardbags, medusa, awake-sa) or a disconnected WhatsApp number stands out."""
from datetime import datetime, timedelta, timezone

from vula.api import master


def _iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def test_health_light_and_reasons():
    ok = master._health({"active": True, "whatsapp": "connected", "last_activity": _iso(1)})
    assert ok == {"health": "green", "health_reasons": [], "dormant": False}

    down = master._health({"active": True, "whatsapp": "disconnected", "open_escalations": 2,
                           "last_activity": _iso(0)})
    assert down["health"] == "red"
    assert down["health_reasons"][0] == "WhatsApp disconnected"
    assert "2 unanswered question(s) for the team" in down["health_reasons"]

    quiet = master._health({"active": True, "whatsapp": "connected", "last_activity": _iso(45)})
    assert quiet["health"] == "amber" and quiet["dormant"]
    never = master._health({"active": True, "whatsapp": "connected", "last_activity": None})
    assert never["dormant"] and never["health_reasons"] == ["No messages yet"]

    assert master._health({"active": False, "whatsapp": "disconnected"})["health"] == "off"


def test_a_missing_signal_is_not_reported_as_a_problem():
    # a table that couldn't be read leaves the key out → no false alarm about activity
    assert master._health({"active": True, "whatsapp": "connected"})["health"] == "green"


class _Q:
    def __init__(self, rows):
        self.rows, self.filters = rows, {}

    def select(self, *_a):
        return self

    def eq(self, k, v):
        self.filters[k] = v
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        rows = [r for r in self.rows if all(r.get(k) == v for k, v in self.filters.items())]
        return type("R", (), {"data": rows})()


class _DB:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        if name not in self.tables:
            raise RuntimeError(f"relation {name} does not exist")
        return _Q(self.tables[name])


def test_signals_are_gathered_per_tenant_and_survive_a_missing_table():
    db = _DB({
        "vula_whatsapp_accounts": [{"tenant_id": "digg-demo", "status": "connected"},
                                   {"tenant_id": "medusa", "status": "disconnected"}],
        "commerce_conversation_messages": [
            {"tenant_id": "digg-demo", "role": "user", "created_at": "2026-09-28T10:00:00+00:00"}],
        # vula_escalations missing (migration not applied) → skipped, not an error
    })
    sig = master._tenant_signals(db, ["digg-demo", "medusa"])
    assert sig["digg-demo"] == {"whatsapp": "connected", "last_activity": "2026-09-28T10:00:00+00:00"}
    assert sig["medusa"] == {"whatsapp": "disconnected", "last_activity": None}
