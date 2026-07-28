"""Tests for the stale_escalation_nudge job type (vula/commerce/job_config.py) — the one
JOB_TYPES entry that isn't commerce-specific, added so proactive re-engagement can run for
every tenant (server.py's _stale_escalation_scheduler_loop), not just ones with the "orders"
module. Covers only the generic config-resolution machinery already exercised for the other
five job types; the scheduler loop itself isn't unit-tested, matching this codebase's existing
convention for the other background loops in vula/api/server.py."""
from vula.commerce import job_config


def test_stale_escalation_nudge_registered():
    assert "stale_escalation_nudge" in job_config.JOB_TYPES
    assert job_config.JOB_TYPES["stale_escalation_nudge"]["kind"] == "interval"


def test_effective_config_defaults():
    cfg = job_config.effective_config("t1", "stale_escalation_nudge", row=None)
    assert cfg["enabled"] is True
    assert cfg["kind"] == "interval"
    assert cfg["interval_minutes"] == 60


def test_effective_config_respects_tenant_override():
    cfg = job_config.effective_config(
        "t1", "stale_escalation_nudge", row={"enabled": False, "interval_minutes": 15})
    assert cfg["enabled"] is False
    assert cfg["interval_minutes"] == 15


def test_get_configs_includes_stale_escalation_nudge(monkeypatch):
    monkeypatch.setattr(job_config, "_client", lambda: _EmptyDB())
    cfgs = job_config.get_configs("t1")
    assert "stale_escalation_nudge" in cfgs


class _EmptyDB:
    def table(self, name):
        return self

    def select(self, *a, **kw):
        return self

    def eq(self, *a, **kw):
        return self

    def execute(self):
        class _R:
            data = []
        return _R()
