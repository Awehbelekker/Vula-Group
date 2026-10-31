"""
vula/integrations/platform_support.py — messages for Ian about the VULA PLATFORM itself: a tenant
owner/team member's feedback about Vula (forward), and the platform's own health alerts
(alert_operator: setup gaps, the local AI computer down, a stalled server, cloud AI down,
trials ending, a stuck WhatsApp move, knowledge-base checks).

Detection is natural-language keywords (+ an explicit "vula support" trigger as a guaranteed
fallback), gated to team/owner only — the same is_team check already used for bank-review and
escalation answers, so a customer ordering fish can never trigger this.

Feedback is logged durably first (vula_platform_feedback — Ian can never miss one). Then both
kinds go to platform_support_phone from VULA'S OWN WhatsApp number (settings.platform_wa_*, on
Vula's own Meta Business account): the approved vula_operator_alert template, which arrives
outside Meta's 24-hour window, with free text as the fallback. 31 Oct 2026 (Ian): they used to go
out on a tenant's number (Off the Hook's), so a client's line carried Vula's alerts and other
clients' names. Now a business's number is never used; until Vula's number is set, alerts are
logged only. ensure_operator_template() submits the template to Vula's WABA at boot.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

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


OPERATOR_TEMPLATE = "vula_operator_alert"   # one param: the alert line
_OPERATOR_BODY = "\U0001F527 Vula alert:\n\n{{1}}\n\nSent by Vula's own monitoring."
_OPERATOR_EXAMPLE = "The local AI computer has been unreachable for 15 minutes."
GRAPH_BASE = "https://graph.facebook.com/v19.0"


def _platform_creds() -> Optional[dict]:
    """Vula's own number (settings.platform_wa_*), or None. Never a business's number: a client's
    line only ever carries that client's own conversations (31 Oct 2026, Ian)."""
    from config import settings
    if settings.platform_wa_phone_id and settings.platform_wa_token:
        return {"phone_id": settings.platform_wa_phone_id, "token": settings.platform_wa_token,
                "waba_id": settings.platform_waba_id}
    return None


def _number(to: str) -> str:
    n = re.sub(r"\D", "", to or "")
    return "27" + n[1:] if n.startswith("0") else n


async def _post(creds: dict, body: dict) -> bool:
    import httpx
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(f"{GRAPH_BASE}/{creds['phone_id']}/messages",
                                 headers={"Authorization": f"Bearer {creds['token']}"}, json=body)
    if not resp.is_success:
        logger.info("operator alert send failed: %s", resp.text[:200])
    return resp.is_success


async def _send_platform(to: str, text: str) -> bool:
    """The approved operator template first (arrives outside Meta's 24-hour window), free text as
    the fallback, both from Vula's own number."""
    creds = _platform_creds()
    if not creds:
        return False
    from core import dry_run as _dry
    if _dry.record_send("whatsapp", to, OPERATOR_TEMPLATE):   # capability benchmark: never sent
        return True
    number = _number(to)
    # Meta refuses a template parameter with a newline, a tab or more than 4 spaces in a row
    # (the stalled-server alert carries a stack trace), so the template gets it on one line.
    param = re.sub(r"\s+", " ", text).strip()[:1000]
    try:
        if await _post(creds, {"messaging_product": "whatsapp", "to": number, "type": "template",
                               "template": {"name": OPERATOR_TEMPLATE, "language": {"code": "en"},
                                            "components": [{"type": "body", "parameters": [
                                                {"type": "text", "text": param}]}]}}):
            return True
    except Exception as exc:  # noqa: BLE001
        logger.info("operator alert template send failed: %s", exc)
    try:
        return await _post(creds, {"messaging_product": "whatsapp", "to": number, "type": "text",
                                   "text": {"body": text[:4000]}})
    except Exception as exc:  # noqa: BLE001
        logger.info("operator alert WhatsApp send failed: %s", exc)
        return False


async def ensure_operator_template() -> dict:
    """Make sure Vula's own WABA has OPERATOR_TEMPLATE; submit it for review if not. Safe to call
    at every boot. Never raises."""
    creds = _platform_creds()
    if not creds or not creds.get("waba_id"):
        return {"skipped": "Vula's own number is not set"}
    import httpx
    url = f"{GRAPH_BASE}/{creds['waba_id']}/message_templates"
    headers = {"Authorization": f"Bearer {creds['token']}"}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, headers=headers,
                                    params={"fields": "name,status", "name": OPERATOR_TEMPLATE})
            live = resp.json().get("data", []) if resp.is_success else []
            if live:
                return {"already_exists": True, "status": live[0].get("status")}
            resp = await client.post(url, headers=headers, json={
                "name": OPERATOR_TEMPLATE, "language": "en", "category": "UTILITY",
                "components": [{"type": "BODY", "text": _OPERATOR_BODY,
                                "example": {"body_text": [[_OPERATOR_EXAMPLE]]}}]})
            data = resp.json()
        if not resp.is_success:
            return {"error": (data.get("error") or {}).get("message") or "template submit failed"}
        return {"name": OPERATOR_TEMPLATE, "status": data.get("status", "PENDING")}
    except Exception as exc:  # noqa: BLE001
        logger.warning("operator template check failed: %s", exc)
        return {"error": type(exc).__name__}


async def forward(tenant_id: str, phone: str, sender_name: str, text: str) -> None:
    """Log durably, then WhatsApp Ian from Vula's own number (alert_operator). Never raises —
    this must never block or break the tenant's normal conversation."""
    try:
        _client().table("vula_platform_feedback").insert({
            "tenant_id": tenant_id, "phone": phone, "sender_name": sender_name or "",
            "message": text,
        }).execute()
    except Exception as exc:
        logger.warning("platform feedback log failed (run migration 076?): %s", exc)

    who = f"{sender_name} ({phone})" if sender_name else phone
    # The message itself is in vula_platform_feedback; keep it out of the server log.
    await alert_operator(f"Feedback from {who} on {tenant_id}: {text}", log_text=False)


async def alert_operator(text: str, log_text: bool = True) -> bool:
    """A platform alert (one line) to platform_support_phone, from Vula's own WhatsApp number
    only. Without that number it is logged, not sent: never from a business's number, never
    from the env default number. Never raises; False when nothing was delivered."""
    from config import settings
    to = settings.platform_support_phone
    if not to or not _platform_creds():
        logger.warning("operator alert (not sent: %s): %s",
                       "PLATFORM_SUPPORT_PHONE is not set" if not to else "Vula's own number is not set",
                       text[:500] if log_text else "(feedback, kept in vula_platform_feedback)")
        return False
    try:
        return await _send_platform(to, text)
    except Exception as exc:  # noqa: BLE001
        logger.info("operator alert not sent: %s", exc)
        return False
