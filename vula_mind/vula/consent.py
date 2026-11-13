"""Has this person opted out? One check for every direct-marketing send (13 Nov 2026 audit).

The broadcast, automations, bring-back, overdue reminders, booking reminders and Tap to Pay already
skipped numbers that sent STOP (commerce_consent, status opted_out). Listing shares, wish-list
matches and the enquiry acknowledgement didn't. Direct marketing (POPIA s69) asks here first.
Messages about something the person bought or booked (order updates, deposits) are transactional
and are not gated here.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def norm(phone: str) -> str:
    n = "".join(ch for ch in (phone or "") if ch.isdigit())
    return "27" + n[1:] if n.startswith("0") else n


def opted_out(tenant_id: str, phone: str) -> bool:
    """True when this business's records say the person opted out. Fails closed: when the consent
    table can't be read, a marketing message isn't sent (a transactional one never asks)."""
    if not phone:
        return False
    try:
        from vula.commerce import service
        rows = (service._client().table("commerce_consent").select("phone,status")
                .eq("tenant_id", tenant_id).eq("status", "opted_out").execute().data or [])
    except Exception as exc:  # noqa: BLE001
        log.warning("consent check failed for %s — not sending marketing: %s", tenant_id, type(exc).__name__)
        return True
    target = norm(phone)
    return any(norm(r.get("phone") or "") == target for r in rows)
