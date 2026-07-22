"""
vula/integrations/notify.py — route notifications to the right team members by WhatsApp.

Each tenant has a team directory (vula_team_members); members subscribe to event types.
notify_team() fans a message out to every active member subscribed to that event, falling
back to the legacy single vula_email_accounts.notify_phone when no team is configured.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Notification event types members can subscribe to.
EVENTS = (
    "which_project",      # a filed doc couldn't be matched to a project
    "followup_digest",    # daily summary of emails awaiting a reply
    "payment_received",   # a payment/invoice was filed to the ledger
    "new_invoice",
    "new_order",
    "low_stock",
    "procurement_done",   # a procurement/stock-ordering ClickUp task was marked complete
)


def _client():
    from vula.commerce import service as commerce_service
    return commerce_service._client()


def _digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def _members(tenant_id: str) -> list:
    try:
        return (_client().table("vula_team_members").select("*")
                .eq("tenant_id", tenant_id).eq("active", True).execute().data or [])
    except Exception as exc:
        logger.debug("team members lookup skipped (run migration 029?): %s", exc)
        return []


def _fallback_phone(tenant_id: str) -> Optional[str]:
    """A tenant can have several connected mailboxes (migration 093) — fall back to the
    primary account's notify_phone, so this stays well-defined instead of picking an
    arbitrary connected account."""
    try:
        row = (_client().table("vula_email_accounts").select("notify_phone")
               .eq("tenant_id", tenant_id).eq("is_primary", True).limit(1).execute().data or [{}])[0]
        return row.get("notify_phone")
    except Exception:
        return None


async def notify_team(tenant_id: str, event_type: str, message: str) -> int:
    """Send `message` to every active member subscribed to `event_type`. Returns count sent.
    Falls back to vula_email_accounts.notify_phone only when the tenant has NO team members
    configured at all — a real bug otherwise: a team member who deliberately unsubscribed
    from an event (via the dashboard or the WhatsApp preference command) would still get
    pinged through this fallback whenever they're the tenant's only contact, silently
    overriding their own opt-out."""
    from vula.api.whatsapp import _send_reply
    members = _members(tenant_id)
    recipients = []
    for m in members:
        notify = m.get("notify") or []
        if event_type in notify and m.get("whatsapp"):
            recipients.append(_digits(m["whatsapp"]))
    recipients = list(dict.fromkeys(r for r in recipients if r))   # dedupe, drop blanks

    if not recipients and not members:
        fb = _fallback_phone(tenant_id)
        if fb:
            recipients = [_digits(fb)]

    sent = 0
    for to in recipients:
        try:
            if await _send_reply(to, message, tenant_id=tenant_id):
                sent += 1
        except Exception as exc:
            logger.debug("notify_team send failed (%s): %s", to, exc)
    return sent


def team_member_for_phone(tenant_id: str, phone: str) -> Optional[dict]:
    """Resolve an inbound WhatsApp sender to a team member (name/role/access)."""
    p = _digits(phone)
    if not p:
        return None
    for m in _members(tenant_id):
        if _digits(m.get("whatsapp")) == p:
            return m
    return None


# ── Self-service preference commands ("stop sending me follow-up emails") ─────────

_EVENT_KEYWORDS = {
    "followup_digest": ("follow up", "follow-up", "followup", "email digest",
                        "outstanding email", "email reminder"),
    "which_project": ("which project", "which-project", "project question"),
    "payment_received": ("payment received", "payments"),
    "new_invoice": ("new invoice", "invoices"),
    "new_order": ("new order", "orders"),
    "low_stock": ("low stock", "stock alert"),
    "help_request": ("help request", "escalation"),
    "procurement_done": ("procurement", "stock ordering", "stock order"),
}
# Deliberately requires more than a bare trigger word so this never shadows the exact-match
# POPIA opt-out phrases ("stop", "unsubscribe" alone) handled earlier in the WhatsApp pipeline.
_OFF_RE = re.compile(r"\b(stop|turn off|don'?t send|no more|unsubscribe)\b", re.IGNORECASE)
_ON_RE = re.compile(r"\b(turn on|send me|start sending|resubscribe|subscribe)\b", re.IGNORECASE)


def handle_preference_command(tenant_id: str, phone: str, text: str) -> Optional[str]:
    """A team member managing their own WhatsApp notification preferences by message —
    e.g. 'stop sending me follow-up emails' or 'turn on low stock alerts'. Only ever acts
    on the sender's own subscriptions (never a customer's). Returns a confirmation reply
    if this message was a preference command, else None so the caller falls through to
    normal handling."""
    member = team_member_for_phone(tenant_id, phone)
    if not member:
        return None
    t = (text or "").lower().strip()
    off = bool(_OFF_RE.search(t))
    on = bool(_ON_RE.search(t)) if not off else False
    if not off and not on:
        return None
    matched = [key for key, kws in _EVENT_KEYWORDS.items() if any(kw in t for kw in kws)]
    if not matched:
        return None

    notify = list(member.get("notify") or [])
    if off:
        notify = [n for n in notify if n not in matched]
    else:
        notify = list(dict.fromkeys(notify + matched))
    try:
        _client().table("vula_team_members").update({"notify": notify}).eq("id", member["id"]).execute()
    except Exception as exc:
        logger.debug("preference update failed: %s", exc)
        return None

    labels = ", ".join(k.replace("_", " ") for k in matched)
    verb = "off" if off else "on"
    return (f"Got it — turned {verb} *{labels}* notifications for you. "
            "Reply anytime to change this, or manage it in the dashboard under Team.")
