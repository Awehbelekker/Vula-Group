"""core/mass_mind/health.py — Mass Mind Phase 1 systemic health watch (migration 160).

vula/api/server.py's _stale_escalation_scheduler_loop and _stale_handoff_scheduler_loop each
recover ONE tenant's stuck conversation independently. Checked before building this: neither
left any durable trace that a recovery had fired — mark_stale_notified/mark_customer_notified
stamp the SAME vula_escalations row, set_session_paused just flips a bool. There was nothing to
roll up. This module + migration 160 give both loops a shared event log, and the rollup that
turns "N isolated recoveries" into "M distinct tenants hit the same thing at once" — the real
test for a platform-wide incident versus ordinary background noise.

The scheduler loops themselves (including the new _mass_mind_health_watch_loop) are NOT
unit-tested here — matching this codebase's existing, explicit convention for its background
loops (see tests/test_stale_handoff.py's own docstring: "the scheduler LOOP itself isn't
unit-tested (infinite loop, real sleeps)"). Everything the loop actually decides lives in this
testable module instead.
"""
import pytest

from core.mass_mind import health


# ── fake Supabase client (same minimal pattern as tests/test_reflection_tenant_scoping.py) ──

class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, rows):
        self._filtered = list(rows)
        self._limit_n = None

    def select(self, *_a, **_kw):
        return self

    def gte(self, key, val):
        self._filtered = [r for r in self._filtered if r.get(key, "") >= val]
        return self

    def limit(self, n):
        self._limit_n = n
        return self

    def execute(self):
        rows = self._filtered[: self._limit_n] if self._limit_n is not None else self._filtered
        return _FakeResult(rows)


class _FakeInsert:
    def __init__(self, store, row):
        self._store = store
        self._row = dict(row)

    def execute(self):
        self._row.setdefault("created_at", _now_iso())
        self._store.append(self._row)
        return _FakeResult([self._row])


class _FakeTable:
    def __init__(self, store):
        self._store = store

    def select(self, *a, **kw):
        return _FakeQuery(self._store).select(*a, **kw)

    def insert(self, row):
        return _FakeInsert(self._store, row)


class _FakeClient:
    def __init__(self):
        self._tables: dict[str, list] = {}

    def table(self, name):
        return _FakeTable(self._tables.setdefault(name, []))


class _BrokenClient:
    def table(self, name):
        raise Exception('relation "vula_health_events" does not exist')


def _now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _hours_ago_iso(hours):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


@pytest.fixture(autouse=True)
def _clean_alert_state():
    """_last_alerted is module-level, shared across tests — isolate each test."""
    health._last_alerted.clear()
    yield
    health._last_alerted.clear()


# ── record_event ──────────────────────────────────────────────────────────────────────

def test_record_event_writes_a_row(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(health, "_client", lambda: fake)
    health.record_event("off-the-hook", "stale_handoff_auto_resumed", {"session_id": "s1"})

    rows = fake._tables["vula_health_events"]
    assert len(rows) == 1
    assert rows[0]["tenant_id"] == "off-the-hook"
    assert rows[0]["kind"] == "stale_handoff_auto_resumed"
    assert rows[0]["detail"] == {"session_id": "s1"}


def test_record_event_rejects_an_unknown_kind(monkeypatch):
    """A typo'd kind must not silently create a permanent blind spot in the rollup — it's
    dropped, not written under a name systemic_incidents() will never recognise."""
    fake = _FakeClient()
    monkeypatch.setattr(health, "_client", lambda: fake)
    health.record_event("off-the-hook", "totally_made_up_kind")
    assert fake._tables.get("vula_health_events", []) == []


def test_record_event_fails_open_when_the_table_is_missing(monkeypatch):
    monkeypatch.setattr(health, "_client", lambda: _BrokenClient())
    health.record_event("off-the-hook", "stale_handoff_auto_resumed")  # must not raise


# ── systemic_incidents ────────────────────────────────────────────────────────────────

def _seed(fake, tenant_id, kind, hours_ago=0.1):
    fake._tables.setdefault("vula_health_events", []).append({
        "tenant_id": tenant_id, "kind": kind, "created_at": _hours_ago_iso(hours_ago),
    })


def test_below_threshold_is_not_an_incident(monkeypatch):
    """Some background rate of isolated recoveries is normal — 2 tenants (below the default
    min_tenants=3) must not trigger."""
    fake = _FakeClient()
    monkeypatch.setattr(health, "_client", lambda: fake)
    _seed(fake, "off-the-hook", "stale_handoff_auto_resumed")
    _seed(fake, "digg", "stale_handoff_auto_resumed")
    assert health.systemic_incidents() == []


def test_detects_a_real_cross_tenant_incident(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(health, "_client", lambda: fake)
    for t in ("off-the-hook", "digg", "gerflor"):
        _seed(fake, t, "stale_handoff_auto_resumed")

    incidents = health.systemic_incidents()
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc["kind"] == "stale_handoff_auto_resumed"
    assert inc["tenant_count"] == 3
    assert inc["event_count"] == 3
    assert set(inc["tenant_ids"]) == {"off-the-hook", "digg", "gerflor"}


def test_one_tenant_firing_repeatedly_is_not_an_incident(monkeypatch):
    """The whole point: count DISTINCT tenants, not total events — one tenant's bad day must
    never look like a platform-wide problem."""
    fake = _FakeClient()
    monkeypatch.setattr(health, "_client", lambda: fake)
    for _ in range(10):
        _seed(fake, "off-the-hook", "stale_handoff_auto_resumed")
    assert health.systemic_incidents() == []


def test_events_outside_the_window_are_excluded(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(health, "_client", lambda: fake)
    for t in ("off-the-hook", "digg", "gerflor"):
        _seed(fake, t, "stale_handoff_auto_resumed", hours_ago=48)  # older than the 24h default
    assert health.systemic_incidents(hours=24.0) == []


def test_kinds_are_evaluated_independently(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(health, "_client", lambda: fake)
    for t in ("off-the-hook", "digg", "gerflor"):
        _seed(fake, t, "stale_handoff_auto_resumed")
    for t in ("off-the-hook", "digg"):  # below threshold for this kind
        _seed(fake, t, "stale_escalation_abandoned")

    incidents = health.systemic_incidents()
    assert len(incidents) == 1
    assert incidents[0]["kind"] == "stale_handoff_auto_resumed"


def test_min_tenants_threshold_is_configurable(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(health, "_client", lambda: fake)
    for t in ("off-the-hook", "digg"):
        _seed(fake, t, "stale_handoff_auto_resumed")
    assert health.systemic_incidents(min_tenants=2) != []
    assert health.systemic_incidents(min_tenants=3) == []


def test_systemic_incidents_fails_open_when_the_table_is_missing(monkeypatch):
    monkeypatch.setattr(health, "_client", lambda: _BrokenClient())
    assert health.systemic_incidents() == []


# ── should_alert (cooldown) ──────────────────────────────────────────────────────────────

def test_should_alert_true_once_then_false_within_the_cooldown():
    assert health.should_alert("stale_handoff_auto_resumed", now=1000.0) is True
    assert health.should_alert("stale_handoff_auto_resumed", now=1000.0 + 60) is False


def test_should_alert_true_again_after_the_cooldown_elapses():
    assert health.should_alert("stale_handoff_auto_resumed", now=1000.0) is True
    later = 1000.0 + health._ALERT_COOLDOWN_SECONDS + 1
    assert health.should_alert("stale_handoff_auto_resumed", now=later) is True


def test_should_alert_cooldown_is_independent_per_kind():
    assert health.should_alert("stale_handoff_auto_resumed", now=1000.0) is True
    assert health.should_alert("stale_escalation_abandoned", now=1000.0) is True


# ── describe ─────────────────────────────────────────────────────────────────────────────

def test_describe_includes_the_key_figures():
    msg = health.describe({
        "kind": "stale_handoff_auto_resumed", "tenant_ids": ["digg", "off-the-hook"],
        "tenant_count": 2, "event_count": 3, "window_hours": 24.0,
    })
    assert "2" in msg and "3" in msg and "24" in msg
    assert "digg" in msg and "off-the-hook" in msg
