"""
vula/retention.py — one place that deletes personal information Vula no longer needs (POPIA s14).

2026-10-31. Takeoff plans were the first data with a retention period (vula/takeoff/api.py,
purge_expired). Rather than each feature adding its own daily loop (vula/api/server.py already runs
~40), every kind of data that expires is one RULE here, and one daily sweep runs them all
(server.py::_retention_loop).

A rule is (name, the setting that holds its period in days, the function that deletes what is
older). A period of 0 keeps that data. One rule failing never stops the others. Each rule reports
counts only, never content.

Add a rule only for data with no legal minimum. Invoices, bank lines, payroll and other
accounting records must be kept for at least 5 years (Tax Administration Act s29), so they are
never swept here. See docs/security-model.md, "Retention".
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Dict, List

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Rule:
    name: str
    setting: str                                  # attribute on config.settings, in days
    purge: Callable[[int], Dict[str, int]]        # (days) → counts deleted


def _takeoff(days: int) -> Dict[str, int]:
    from vula.takeoff.api import purge_expired
    return purge_expired(days=days)


def _older_than(table: str, column: str, days: int, **eq_not) -> int:
    """Delete rows of `table` whose `column` is older than `days`; `eq_not` keeps rows whose
    column equals the value (e.g. status="won"). Returns how many went."""
    from datetime import datetime, timedelta, timezone
    from vula.commerce.service import _client
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    q = _client().table(table).delete().lt(column, cutoff)
    for col, keep in eq_not.items():
        q = q.neq(col, keep)
    return len(q.execute().data or [])


def _dedup(days: int) -> Dict[str, int]:
    return {"rows": _older_than("vula_wa_msg_dedup", "seen_at", days)}


def _wa_logins(days: int) -> Dict[str, int]:
    return {"rows": _older_than("vula_wa_login_requests", "created_at", days)}


def _app_errors(days: int) -> Dict[str, int]:
    return {"rows": _older_than("vula_app_errors", "last_seen", days)}


def _leads(days: int) -> Dict[str, int]:
    # A won lead became a business; the rest (new, contacted, lost) go after the period.
    return {"rows": _older_than("vula_website_leads", "created_at", days, status="won")}


def _turns(days: int) -> Dict[str, int]:
    out = {"turns": _older_than("vula_turns", "started_at", days)}
    out["kb_turns"] = _older_than("vula_kb_turns", "created_at", days)
    return out


def _conversations(days: int) -> Dict[str, int]:
    """Conversations whose LAST message is older than the period, with their messages, ratings
    and the client's note. Counted from the last contact, so an active client keeps their
    history; orders and invoices are accounting records and are never touched here."""
    from datetime import datetime, timedelta, timezone
    from vula.commerce.service import _client
    db = _client()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    out = {"sessions": 0, "messages": 0, "client_notes": 0}
    while True:
        rows = (db.table("commerce_conversation_sessions").select("id,tenant_id,session_key,customer_phone")
                .lt("updated_at", cutoff).limit(200).execute().data or [])
        if not rows:
            break
        ids = [r["id"] for r in rows]
        out["messages"] += len(db.table("commerce_conversation_messages").delete().in_("session_id", ids).execute().data or [])
        db.table("vula_reply_feedback").delete().in_("session_id", ids).execute()
        from vula import client_memory
        for r in rows:
            key = client_memory.client_key(r.get("customer_phone") or r.get("session_key"))
            if key:
                out["client_notes"] += len(db.table(client_memory.TABLE).delete()
                                           .eq("tenant_id", r["tenant_id"]).eq("client_key", key).execute().data or [])
        out["sessions"] += len(db.table("commerce_conversation_sessions").delete().in_("id", ids).execute().data or [])
        if len(rows) < 200:
            break
    return out


def _chat_attachments(days: int) -> Dict[str, int]:
    from vula.chat_attachments import prune_all
    return prune_all(days)


def _table_rule(*pairs):
    """A rule over one or more (table, date column) pairs, each counted on its own."""
    def purge(days: int) -> Dict[str, int]:
        return {table: _older_than(table, column, days) for table, column in pairs}
    return purge


RULES: List[Rule] = [
    Rule("takeoff_plans", "takeoff_retention_days", _takeoff),
    # 9 Nov 2026 (owner's decision, golive audit): the candidates docs/security-model.md listed.
    Rule("whatsapp_dedupe", "wa_dedup_retention_days", _dedup),
    Rule("whatsapp_logins", "wa_login_retention_days", _wa_logins),
    Rule("app_errors", "app_error_retention_days", _app_errors),
    Rule("website_leads", "lead_retention_days", _leads),
    Rule("conversation_turns", "turn_retention_days", _turns),
    # 12 Nov 2026 (owner's decision): 24 months after a client's last message.
    Rule("conversations", "conversation_retention_days", _conversations),
    # 17 Nov 2026 audit: files attached to the dashboard chat ("read, not filed") — a day
    Rule("chat_attachments", "chat_attachment_retention_days", _chat_attachments),
    # 18 Nov 2026 (pre-launch audit): the rest of what holds people's words or details, and the
    # technical records. Never: filed documents, invoices, consent proof, audit trails, AI usage,
    # emails sent to a project's client (kept like invoices).
    Rule("whatsapp_sent", "outbound_retention_days", _table_rule(("vula_wa_outbound", "created_at"))),
    Rule("escalations", "escalation_retention_days", _table_rule(("vula_escalations", "created_at"))),
    Rule("chat_messages", "chat_message_retention_days", _table_rule(("vula_chat_messages", "created_at"))),
    Rule("email_followups", "email_followup_retention_days", _table_rule(("vula_email_followups", "created_at"))),
    Rule("email_contacts", "email_contact_retention_days", _table_rule(("vula_email_contacts", "last_seen"))),
    Rule("email_voice_samples", "email_voice_sample_retention_days",
         _table_rule(("vula_email_voice_samples", "created_at"))),
    Rule("routing_telemetry", "telemetry_retention_days", _table_rule(("vula_reasoning_telemetry", "created_at"))),
    Rule("health_checks", "health_event_retention_days",
         _table_rule(("vula_health_events", "created_at"), ("vula_eval_reports", "created_at"))),
    Rule("webhooks", "webhook_retention_days",
         _table_rule(("vula_webhook_failures", "created_at"), ("kb_webhook_events", "received_at"))),
    Rule("automation_log", "automation_log_retention_days", _table_rule(("commerce_automation_log", "fired_at"))),
]


def run_all(rules: List[Rule] = None) -> Dict[str, Dict[str, int]]:
    """Run every rule whose period is set. {rule name: counts, or {"error": 1}}."""
    from config import settings
    out: Dict[str, Dict[str, int]] = {}
    for rule in (RULES if rules is None else rules):
        days = int(getattr(settings, rule.setting, 0) or 0)
        if days <= 0:
            continue
        try:
            out[rule.name] = rule.purge(days)
        except Exception as exc:  # noqa: BLE001 — one rule never stops the rest
            log.warning("Retention rule %s failed: %s", rule.name, exc)
            out[rule.name] = {"error": 1}
    return out
