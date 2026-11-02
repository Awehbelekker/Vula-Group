"""One retention sweep for every kind of expiring data (vula/retention.py)."""
from __future__ import annotations

from vula import retention


def test_each_rule_gets_its_own_period(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "takeoff_retention_days", 30)
    seen = {}
    rules = [retention.Rule("a", "takeoff_retention_days", lambda d: seen.setdefault("a", d) and {"files": 2})]
    assert retention.run_all(rules) == {"a": {"files": 2}}
    assert seen == {"a": 30}


def test_a_period_of_zero_keeps_the_data(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "takeoff_retention_days", 0)
    called = []
    rules = [retention.Rule("a", "takeoff_retention_days", lambda d: called.append(d) or {})]
    assert retention.run_all(rules) == {} and called == []


def test_one_failing_rule_never_stops_the_others(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "takeoff_retention_days", 10)

    def boom(_):
        raise RuntimeError("db down")
    rules = [retention.Rule("bad", "takeoff_retention_days", boom),
             retention.Rule("good", "takeoff_retention_days", lambda d: {"jobs": 1})]
    assert retention.run_all(rules) == {"bad": {"error": 1}, "good": {"jobs": 1}}


def test_takeoff_plans_are_the_registered_rule():
    assert [r.name for r in retention.RULES] == ["takeoff_plans"]


def test_the_server_runs_one_sweep_not_a_loop_per_feature():
    import inspect
    from vula.api import server
    src = inspect.getsource(server)
    assert "_retention_loop()" in src and "_takeoff_retention_loop" not in src
