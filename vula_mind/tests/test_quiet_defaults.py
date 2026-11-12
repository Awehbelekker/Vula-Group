"""Team messages start off for a new business; existing ones keep what they had (12 Nov 2026)."""
import pytest

from vula.commerce import job_config as jc
from vula import onboard_wa


def _tenant(monkeypatch, created, modules=("orders",), btype="retail", projects=False):
    import vula.api.tenants as tenants
    monkeypatch.setattr(tenants, "get_config", lambda tid, fresh=False: {"created_at": created, "business_type": btype})
    monkeypatch.setattr(tenants, "enabled_modules", lambda tid: list(modules))
    monkeypatch.setattr(tenants, "uses_projects", lambda tid: projects)


@pytest.mark.parametrize("job", ["delivery_briefing", "low_stock_alert", "sales_summary", "stale_escalation_nudge"])
def test_a_new_business_starts_with_team_messages_off(monkeypatch, job):
    _tenant(monkeypatch, "2026-11-20T09:00:00+00:00")
    assert jc.effective_config("t", job, None)["enabled"] is False


@pytest.mark.parametrize("job", ["delivery_briefing", "low_stock_alert", "sales_summary", "stale_escalation_nudge"])
def test_an_existing_business_keeps_what_it_had(monkeypatch, job):
    _tenant(monkeypatch, "2026-03-01T09:00:00+00:00")
    assert jc.effective_config("t", job, None)["enabled"] is True


def test_customer_facing_jobs_are_not_affected(monkeypatch):
    _tenant(monkeypatch, "2026-11-20T09:00:00+00:00")
    assert jc.effective_config("t", "unpaid_order_chase", None)["enabled"] is True


def test_a_stored_choice_always_wins(monkeypatch):
    _tenant(monkeypatch, "2026-11-20T09:00:00+00:00")
    assert jc.effective_config("t", "sales_summary", {"enabled": True})["enabled"] is True


def test_unknown_creation_date_is_not_quiet(monkeypatch):
    _tenant(monkeypatch, None)
    assert jc.quiet_by_default("t") is False


def test_choices_fit_the_business(monkeypatch):
    _tenant(monkeypatch, "2026-11-20", modules=("orders",), btype="food")
    assert jc.team_message_choices("t") == ["delivery_briefing", "low_stock_alert", "sales_summary",
                                            "friday_catch_reminder", "stale_escalation_nudge"]
    _tenant(monkeypatch, "2026-11-20", modules=(), btype="professional", projects=True)
    assert jc.team_message_choices("t") == ["pending_project_nudge", "stale_escalation_nudge"]


@pytest.mark.parametrize("text,want", [("1 3", [0, 2]), ("1,2", [0, 1]), ("all", [0, 1, 2]),
                                       ("none", []), ("no thanks", []), ("4", None), ("whatever", None)])
def test_parse_the_owner_s_pick(text, want):
    assert onboard_wa.parse_alert_choice(text, 3) == want


# ── by asking on WhatsApp ─────────────────────────────────────────────────────────────────

def _store(monkeypatch):
    rows = {}

    def upsert(tid, job, patch):
        rows[job] = {**rows.get(job, {}), **patch}
        return jc.effective_config(tid, job, rows[job])
    monkeypatch.setattr(jc, "upsert_config", upsert)
    monkeypatch.setattr(jc, "get_configs", lambda tid: {j: jc.effective_config(tid, j, rows.get(j)) for j in jc.JOB_TYPES})
    return rows


def _skill():
    from core.skills.commerce_admin import CommerceAdminSkill
    return CommerceAdminSkill()


def test_owner_switches_one_on_at_their_time(monkeypatch):
    _tenant(monkeypatch, "2026-11-20")
    rows = _store(monkeypatch)
    out = _skill()._team_messages("t", {"action": "on", "message": "sales_summary", "when": "6:30pm"})
    assert rows["sales_summary"] == {"enabled": True, "hour": 18, "minute": 30}
    assert "End-of-day sales* is on — daily at 18:30" in out["reply_verbatim"]


def test_owner_switches_one_off(monkeypatch):
    _tenant(monkeypatch, "2026-03-01")
    rows = _store(monkeypatch)
    out = _skill()._team_messages("t", {"action": "off", "message": "low_stock_alert"})
    assert rows["low_stock_alert"] == {"enabled": False} and "is off" in out["reply_verbatim"]


def test_status_lists_only_what_fits(monkeypatch):
    _tenant(monkeypatch, "2026-11-20")
    _store(monkeypatch)
    text = _skill()._team_messages("t", {"action": "status"})["reply_verbatim"]
    assert "End-of-day sales — off" in text and "specials" not in text


def test_a_change_that_doesnt_stick_is_not_reported(monkeypatch):
    _tenant(monkeypatch, "2026-11-20")
    monkeypatch.setattr(jc, "upsert_config", lambda tid, job, patch: {"job_type": job})
    monkeypatch.setattr(jc, "get_configs", lambda tid: {j: jc.effective_config(tid, j, None) for j in jc.JOB_TYPES})
    out = _skill()._team_messages("t", {"action": "on", "message": "sales_summary"})
    assert "error" in out


def test_only_owners_and_managers_get_it():
    from core.skills import commerce_admin as ca
    assert ca._TOOL_SCOPE["team_messages"] == "owner_only"
