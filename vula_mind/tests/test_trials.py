"""Gap audit 5.1: trials ending are never silent. The owner email may not go out (Resend off),
so the operator gets one line a day; and the email no longer threatens a suspension that
nothing performs. Made-up businesses."""
from datetime import date

import pytest

from vula import trials

TODAY = date(2026, 10, 27)


def _t(name, ends, paid=False, status="active", plan="starter"):
    return {"tenant_id": name.lower(), "company_name": name, "trial_ends": ends, "paid": paid,
            "status": status, "plan": plan}


def test_emails_go_on_the_7_3_1_0_day_marks_only():
    rows = [_t("A", "2026-11-03"), _t("B", "2026-10-30"), _t("C", "2026-10-29"),
            _t("D", "2026-10-27"), _t("E", "2026-10-31"), _t("F", "2026-10-30", paid=True)]
    assert [(r["company_name"], d) for r, d in trials.due_emails(rows, TODAY)] == [
        ("A", 7), ("B", 3), ("D", 0)]


def test_operator_line_lists_this_weeks_trials_soonest_first():
    rows = [_t("Seaside Cafe", "2026-10-30"), _t("Hilltop", "2026-10-25", plan="growth"),
            _t("Later Co", "2026-11-20"), _t("Old Co", "2026-06-27"), _t("No Trial", None),
            _t("Paid Co", "2026-10-29", paid=True), _t("Today Co", "2026-10-27")]
    assert trials.operator_line(rows, TODAY) == (
        "🧾 Trials to follow up (unpaid): Hilltop (growth) ended 2 days ago; "
        "Today Co (starter) ends today; Seaside Cafe (starter) ends in 3 days.")


def test_nothing_to_say_is_none():
    assert trials.operator_line([_t("Later Co", "2026-12-01")], TODAY) is None


@pytest.mark.asyncio
async def test_email_does_not_threaten_a_suspension(monkeypatch):
    from vula.api import email
    sent = {}

    async def fake_send(to, subject, html, text):
        sent.update(html=html, text=text)
        return False                       # Resend off

    monkeypatch.setattr(email, "_send", fake_send)
    assert await email.send_trial_expiry_email("x@y.test", "Sam", "Seaside Cafe", 3) is False
    assert "suspended" not in sent["html"].lower() and "Subscribe" in sent["html"]


def test_the_daily_loop_counts_real_sends_and_tells_the_operator():
    import inspect
    import vula.api.server as server
    src = inspect.getsource(server._daily_trial_expiry_loop)
    assert "trials.due_emails(" in src and "trials.operator_line(" in src
    assert "if await send_trial_expiry_email(" in src and "alert_operator(line)" in src


# ── 14 days, from when the WhatsApp number goes live (27 Oct) ─────────────────

class _DB:
    def __init__(self, row):
        self.row, self.updates = row, []

    def table(self, _n):
        return self

    def select(self, _c):
        return self

    def eq(self, _k, _v):
        return self

    def limit(self, _n):
        return self

    def update(self, patch):
        self.updates.append(patch)
        return self

    def execute(self):
        return type("R", (), {"data": [self.row] if self.row else []})()


NOW = __import__("datetime").datetime(2026, 10, 27, 12, 0, tzinfo=__import__("datetime").timezone.utc)


def test_trial_is_14_days_by_default():
    from config import settings
    assert settings.trial_days == 14


def test_going_live_restarts_the_14_days(monkeypatch):
    db = _DB({"trial_ends": "2026-10-30T09:00:00+00:00", "paid": False})   # signed up 16 Oct
    assert trials.start_on_go_live("seaside", db, now=NOW) == "2026-11-10"
    assert db.updates[0]["trial_ends"].startswith("2026-11-10")


def test_going_live_never_shortens_a_trial():
    db = _DB({"trial_ends": "2026-11-20T00:00:00+00:00", "paid": False})   # an older 30-day trial
    assert trials.start_on_go_live("old", db, now=NOW) is None and db.updates == []


@pytest.mark.parametrize("row", [None, {"trial_ends": None, "paid": False},
                                 {"trial_ends": "2026-10-30", "paid": True}])
def test_paid_or_no_trial_is_left_alone(row):
    db = _DB(row)
    assert trials.start_on_go_live("x", db, now=NOW) is None and db.updates == []


def test_a_db_error_never_breaks_connecting_a_number():
    class Broken:
        def table(self, _n):
            raise RuntimeError("db down")
    assert trials.start_on_go_live("x", Broken(), now=NOW) is None


def test_both_connect_flows_start_the_trial_and_signup_uses_the_setting():
    import inspect
    from vula.api import signup, whatsapp_connect
    src = inspect.getsource(whatsapp_connect)
    assert src.count("trials.start_on_go_live(") == 2
    assert "trial_days=settings.trial_days" in inspect.getsource(signup)
