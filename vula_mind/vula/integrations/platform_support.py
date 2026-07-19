"""
vula/integrations/platform_support.py — a tenant owner/team message about the VULA PLATFORM
itself (not their business) reaches Ian directly, through that tenant's own WhatsApp number.

Detection is natural-language keywords (+ an explicit "vula support" trigger as a guaranteed
fallback), gated to team/owner only — the same is_team check already used for bank-review and
escalation answers, so a customer ordering fish can never trigger this.

Delivery is three-layered: durable (always logs to vula_platform_feedback — Ian can never miss
one), then an approved WhatsApp TEMPLATE (works regardless of the 24h window — see
vula/commerce/wa_templates.py), then a free-form fallback (works only if Ian already has an
open 24h window with that specific tenant's number).

ensure_template() provisions the template for a tenant's WABA — called once for every existing
tenant, and automatically every time a NEW tenant finishes connecting WhatsApp
(vula/api/whatsapp_connect.py). Meta templates are WABA-wide, not tenant-scoped, so sibling
tenants sharing one Meta Business Account (confirmed: digg-demo and off-the-hook share a WABA)
only need one real Meta submission between them — ensure_template still gives each tenant its
own commerce_wa_templates row for the dashboard's per-tenant grouping either way.
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


TEMPLATE_NAME = "vula_platform_feedback"   # one param: "From {sender} ({phone}) on {tenant}: {message}"
_TEMPLATE_BODY = (
    "\U0001F4E3 New Vula platform feedback:\n\n{{1}}\n\n"
    "Please follow up with the sender directly."
)
_TEMPLATE_EXAMPLE = ("From Staci Brits (27821234567) on off-the-hook: "
                     "The bank tab is a bit confusing, could you check it?")


async def ensure_template(tenant_id: str) -> dict:
    """Make sure this tenant's WABA has TEMPLATE_NAME; submit it if not. Safe to call
    repeatedly — a tenant reconnecting WhatsApp, or two sibling tenants sharing one WABA,
    just find it already there. Never raises."""
    try:
        from vula.commerce import wa_templates
        creds = await wa_templates._waba_creds(tenant_id)
        if not creds:
            return {"skipped": "no WABA connected"}

        import httpx
        live = []
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(
                    f"{wa_templates.GRAPH_BASE}/{creds['waba_id']}/message_templates",
                    headers={"Authorization": f"Bearer {creds['token']}"},
                    params={"fields": "name,status", "name": TEMPLATE_NAME},
                )
                if resp.is_success:
                    live = resp.json().get("data", [])
        except Exception as exc:
            logger.debug("template existence check failed for %s: %s", tenant_id, exc)

        if live:
            # Already on this WABA (possibly via a sibling tenant) — just make sure THIS
            # tenant has its own local row too, for the dashboard's per-tenant grouping.
            try:
                wa_templates._client().table("commerce_wa_templates").upsert({
                    "tenant_id": tenant_id, "name": TEMPLATE_NAME, "category": "UTILITY",
                    "language": "en", "body_text": _TEMPLATE_BODY, "param_count": 1,
                    "example": [_TEMPLATE_EXAMPLE], "created_by": "system:platform_support",
                }, on_conflict="tenant_id,name").execute()
            except Exception as exc:
                logger.debug("template local row upsert skipped for %s: %s", tenant_id, exc)
            return {"already_exists": True, "status": live[0].get("status")}

        return await wa_templates.create_template(
            tenant_id, TEMPLATE_NAME, _TEMPLATE_BODY, category="UTILITY", language="en",
            example_params=[_TEMPLATE_EXAMPLE], created_by="system:platform_support",
        )
    except Exception as exc:
        logger.warning("ensure_template failed for %s: %s", tenant_id, exc)
        return {"error": str(exc)}


async def forward(tenant_id: str, phone: str, sender_name: str, text: str) -> None:
    """Log durably, then WhatsApp Ian — approved template first (works regardless of the 24h
    window), free-form as a fallback. Never raises — this must never block or break the
    tenant's normal conversation."""
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
    who = f"{sender_name} ({phone})" if sender_name else phone
    try:
        from vula.api.whatsapp import _send_wa_template
        detail = f"From {who} on {tenant_id}: {text}"
        if await _send_wa_template(tenant_id, settings.platform_support_phone, TEMPLATE_NAME, detail):
            return
    except Exception as exc:
        logger.info("platform feedback template send failed (%s): %s", tenant_id, exc)
    try:
        from vula.api.whatsapp import _send_reply
        msg = f"📣 Platform feedback from {who} on {tenant_id}:\n\n{text}"
        await _send_reply(settings.platform_support_phone, msg, tenant_id=tenant_id)
    except Exception as exc:
        # Expected to fail with Meta's re-engagement error if the template above isn't
        # approved yet (or doesn't exist for this tenant) AND Ian has no open 24h window with
        # this tenant's number — the DB log above already guarantees the message isn't lost.
        logger.info("platform feedback WhatsApp forward skipped (%s): %s", tenant_id, exc)
