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
