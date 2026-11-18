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


def test_the_registered_rules():
    assert [r.name for r in retention.RULES] == [
        "takeoff_plans", "whatsapp_dedupe", "whatsapp_logins", "app_errors", "website_leads",
        "conversation_turns", "conversations", "chat_attachments",
        "whatsapp_sent", "escalations", "chat_messages", "email_followups", "email_contacts",
        "email_voice_samples", "routing_telemetry", "health_checks", "webhooks", "automation_log"]


def test_the_agreed_periods():
    """9 Nov 2026, the owner's decision (go-live audit): technical data briefly, conversations
    and unconverted leads for a year."""
    from config import Settings
    s = Settings()
    assert (s.wa_dedup_retention_days, s.wa_login_retention_days, s.app_error_retention_days,
            s.lead_retention_days, s.turn_retention_days) == (30, 30, 90, 365, 365)


class _Del:
    def __init__(self, log, table):
        self.log, self.table, self.calls = log, table, []

    def delete(self):
        return self

    def lt(self, col, cutoff):
        self.calls.append(("lt", col, cutoff))
        return self

    def neq(self, col, val):
        self.calls.append(("neq", col, val))
        return self

    def execute(self):
        self.log.append((self.table, self.calls))
        from types import SimpleNamespace
        return SimpleNamespace(data=[{"id": 1}, {"id": 2}])


def test_each_new_rule_deletes_by_its_own_date_and_keeps_won_leads(monkeypatch):
    from datetime import datetime, timedelta, timezone
    log = []
    monkeypatch.setattr("vula.commerce.service._client",
                        lambda: type("C", (), {"table": lambda self, t: _Del(log, t)})())
    by = {r.name: r for r in retention.RULES}
    assert by["whatsapp_dedupe"].purge(30) == {"rows": 2}
    assert by["whatsapp_logins"].purge(30) == {"rows": 2}
    assert by["app_errors"].purge(90) == {"rows": 2}
    assert by["website_leads"].purge(365) == {"rows": 2}
    assert by["conversation_turns"].purge(365) == {"turns": 2, "kb_turns": 2}
    cols = {(t, c[0][1]) for t, c in log}
    assert cols == {("vula_wa_msg_dedup", "seen_at"), ("vula_wa_login_requests", "created_at"),
                    ("vula_app_errors", "last_seen"), ("vula_website_leads", "created_at"),
                    ("vula_turns", "started_at"), ("vula_kb_turns", "created_at")}
    leads = next(c for t, c in log if t == "vula_website_leads")
    assert ("neq", "status", "won") in leads
    cutoff = datetime.fromisoformat(next(c for t, c in log if t == "vula_app_errors")[0][2])
    assert abs((datetime.now(timezone.utc) - cutoff) - timedelta(days=90)) < timedelta(minutes=1)


def test_the_server_runs_one_sweep_not_a_loop_per_feature():
    import inspect
    from vula.api import server
    src = inspect.getsource(server)
    assert "_retention_loop()" in src and "_takeoff_retention_loop" not in src


def test_the_18_nov_periods():
    """Pre-launch audit, 18 Nov 2026: what people said or were sent a year (two for chat history
    and mailbox contacts), technical records sooner."""
    from config import Settings
    s = Settings()
    assert (s.outbound_retention_days, s.escalation_retention_days, s.chat_message_retention_days,
            s.email_followup_retention_days, s.email_contact_retention_days,
            s.email_voice_sample_retention_days, s.telemetry_retention_days,
            s.health_event_retention_days, s.webhook_retention_days,
            s.automation_log_retention_days) == (365, 365, 730, 365, 730, 365, 365, 180, 90, 365)


def test_each_18_nov_rule_deletes_its_own_tables_by_their_own_date(monkeypatch):
    log = []
    monkeypatch.setattr("vula.commerce.service._client",
                        lambda: type("C", (), {"table": lambda self, t: _Del(log, t)})())
    by = {r.name: r for r in retention.RULES}
    for name in ("whatsapp_sent", "escalations", "chat_messages", "email_followups", "email_contacts",
                 "email_voice_samples", "routing_telemetry", "health_checks", "webhooks", "automation_log"):
        assert all(v == 2 for v in by[name].purge(30).values())
    cols = {(t, c[0][1]) for t, c in log}
    assert cols == {
        ("vula_wa_outbound", "created_at"), ("vula_escalations", "created_at"),
        ("vula_chat_messages", "created_at"), ("vula_email_followups", "created_at"),
        ("vula_email_contacts", "last_seen"), ("vula_email_voice_samples", "created_at"),
        ("vula_reasoning_telemetry", "created_at"), ("vula_health_events", "created_at"),
        ("vula_eval_reports", "created_at"), ("vula_webhook_failures", "created_at"),
        ("kb_webhook_events", "received_at"), ("commerce_automation_log", "fired_at")}


def test_business_records_are_never_swept():
    """Filed documents, invoices, consent proof, audit trails, AI usage for billing and emails sent
    to a project's client have no rule here."""
    import inspect
    src = inspect.getsource(retention)
    for table in ("vula_filed_documents", "commerce_invoices", "commerce_consent_log", "vula_admin_audit",
                  "vula_merchant_audit", "vula_ai_usage", "vula_project_client_emails", "journal_entries"):
        assert f'"{table}"' not in src
