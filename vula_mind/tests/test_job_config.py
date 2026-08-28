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


# ── pending_project_nudge (2026-08-28, unassigned-document backlog reminder) ────────────

def test_pending_project_nudge_registered():
    assert "pending_project_nudge" in job_config.JOB_TYPES
    assert job_config.JOB_TYPES["pending_project_nudge"]["kind"] == "weekly"


def test_pending_project_nudge_effective_config_defaults():
    cfg = job_config.effective_config("t1", "pending_project_nudge", row=None)
    assert cfg["enabled"] is True
    assert cfg["kind"] == "weekly"
    assert cfg["hour"] == 8
    assert cfg["day_of_week"] == 0


def test_pending_project_nudge_respects_tenant_override():
    cfg = job_config.effective_config(
        "t1", "pending_project_nudge", row={"enabled": False, "day_of_week": 2})
    assert cfg["enabled"] is False
    assert cfg["day_of_week"] == 2


def test_get_configs_includes_pending_project_nudge(monkeypatch):
    monkeypatch.setattr(job_config, "_client", lambda: _EmptyDB())
    cfgs = job_config.get_configs("t1")
    assert "pending_project_nudge" in cfgs


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
