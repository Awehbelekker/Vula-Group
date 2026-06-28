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
    try:
        row = (_client().table("vula_email_accounts").select("notify_phone")
               .eq("tenant_id", tenant_id).limit(1).execute().data or [{}])[0]
        return row.get("notify_phone")
    except Exception:
        return None


async def notify_team(tenant_id: str, event_type: str, message: str) -> int:
    """Send `message` to every active member subscribed to `event_type`. Returns count sent.
    Falls back to vula_email_accounts.notify_phone if no member is subscribed."""
    from vula.api.whatsapp import _send_reply
    recipients = []
    for m in _members(tenant_id):
        notify = m.get("notify") or []
        if event_type in notify and m.get("whatsapp"):
            recipients.append(_digits(m["whatsapp"]))
    recipients = list(dict.fromkeys(r for r in recipients if r))   # dedupe, drop blanks

    if not recipients:
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
