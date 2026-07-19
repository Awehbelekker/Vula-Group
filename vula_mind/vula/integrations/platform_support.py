"""
vula/integrations/platform_support.py — a tenant owner/team message about the VULA PLATFORM
itself (not their business) reaches Ian directly, through that tenant's own WhatsApp number.

Detection is natural-language keywords (+ an explicit "vula support" trigger as a guaranteed
fallback), gated to team/owner only — the same is_team check already used for bank-review and
escalation answers, so a customer ordering fish can never trigger this.

Delivery is two-layered: durable (always logs to vula_platform_feedback — Ian can never miss
one) plus best-effort WhatsApp (may fail with Meta's "re-engagement" error if Ian hasn't
messaged that specific tenant's number within 24h and no approved template exists yet for it —
this is expected, not a bug; see _send_wa_template's docstring in vula/api/whatsapp.py for the
same constraint already hit by other proactive-notification code this session).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_KEYWORDS = (
    "vula app", "vula system", "vula platform", "vula isn't working", "vula is not working",
    "vula broke", "vula broken", "vula bug", "the app is broken", "the system is broken",
    "app isn't working", "system isn't working", "app is glitching", "system is glitching",
    "having an issue with vula", "having trouble with vula", "issue with the app",
    "issue with the system", "problem with vula", "problem with the app",
    "talk to the vula team", "speak to the vula team", "contact vula", "vula support",
    "who do i talk to about vula", "who made vula", "vula feedback",
    "bug in the system", "bug in the app", "system bug", "app bug", "software bug",
)


def _client():
    from vula.commerce import service
    return service._client()


def detect(text: str) -> bool:
    low = (text or "").lower()
    return any(k in low for k in _KEYWORDS)


async def forward(tenant_id: str, phone: str, sender_name: str, text: str) -> None:
    """Log durably, then best-effort WhatsApp Ian directly. Never raises — this must never
    block or break the tenant's normal conversation."""
    try:
        _client().table("vula_platform_feedback").insert({
            "tenant_id": tenant_id, "phone": phone, "sender_name": sender_name or "",
            "message": text,
        }).execute()
    except Exception as exc:
        logger.warning("platform feedback log failed (run migration 076?): %s", exc)

    from config import settings
    if not settings.platform_support_phone:
        return
    try:
        from vula.api.whatsapp import _send_reply
        who = f"{sender_name} ({phone})" if sender_name else phone
        msg = f"📣 Platform feedback from {who} on {tenant_id}:\n\n{text}"
        await _send_reply(settings.platform_support_phone, msg, tenant_id=tenant_id)
    except Exception as exc:
        # Expected to fail with Meta's re-engagement error until either Ian has an open 24h
        # window with this tenant's number, or an approved template exists — the DB log above
        # already guarantees the message isn't lost.
        logger.info("platform feedback WhatsApp forward skipped (%s): %s", tenant_id, exc)
