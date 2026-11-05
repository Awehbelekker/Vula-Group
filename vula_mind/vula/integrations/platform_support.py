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
from vula.meta_graph import graph_url  # noqa: E402  (the Graph API version lives in config)


_TENANT_CREDS: dict = {}       # {"at": monotonic, "creds": dict|None}
_TENANT_TTL, _TENANT_MISS_TTL = 300, 60


def _tenant_number_creds(tenant_id: str) -> Optional[dict]:
    """Vula's own number connected like a client's (5 Nov 2026, Ian: "test it like a client will
    onboard"): the number Settings › WhatsApp › Connect saved on Vula's own business
    (settings.platform_tenant_id), token encrypted in the database, never copied to Railway or
    chat. With several numbers on that business, PLATFORM_WA_PHONE_ID picks one; else its main
    number. Cached 5 minutes (a miss 1 minute), so the per-message check stays cheap."""
    import time as _t
    from config import settings
    hit = _TENANT_CREDS.get(tenant_id)
    if hit and _t.monotonic() - hit["at"] < (_TENANT_TTL if hit["creds"] else _TENANT_MISS_TTL):
        return hit["creds"]
    creds = None
    try:
        from vula import whatsapp_numbers
        rows = [r for r in whatsapp_numbers.rows_for(tenant_id) if r.get("status") != "disconnected"]
        want = settings.platform_wa_phone_id
        row = (next((r for r in rows if r.get("phone_number_id") == want), None) if want
               else whatsapp_numbers.main_row(rows))
        if row and row.get("phone_number_id"):
            token = ""
            if row.get("access_token"):
                from vula.email_imap.credentials import decrypt_secret
                token = decrypt_secret(row["access_token"]) or ""
            token = token or settings.whatsapp_token   # a number added on Vula's own WABA
            if token:
                creds = {"phone_id": row["phone_number_id"], "token": token,
                         "waba_id": row.get("waba_id") or settings.platform_waba_id,
                         "number": re.sub(r"\D", "", row.get("phone_number") or "")}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Vula's own number (business %s) could not be read: %s", tenant_id, type(exc).__name__)
    _TENANT_CREDS[tenant_id] = {"at": _t.monotonic(), "creds": creds}
    return creds


def forget_platform_number() -> None:
    """After Vula's own number is connected, changed or disconnected."""
    _TENANT_CREDS.clear()


def _platform_creds() -> Optional[dict]:
    """Vula's own number, or None. Never a business's number: a client's line only ever carries
    that client's own conversations (31 Oct 2026, Ian). Either connected like a client's on Vula's
    own business (PLATFORM_TENANT_ID), or entered in Railway (PLATFORM_WA_PHONE_ID + _TOKEN)."""
    from config import settings
    if settings.platform_tenant_id:
        creds = _tenant_number_creds(settings.platform_tenant_id)
        if creds:
            return creds
    if settings.platform_wa_phone_id and settings.platform_wa_token:
        return {"phone_id": settings.platform_wa_phone_id, "token": settings.platform_wa_token,
                "waba_id": settings.platform_waba_id,
                "number": re.sub(r"\D", "", settings.platform_wa_number or "")}
    return None


def platform_number() -> str:
    """Vula's own number as people dial it (digits), or "" — for "message Vula on WhatsApp"."""
    from config import settings
    creds = _platform_creds()
    return re.sub(r"\D", "", settings.platform_wa_number or "") or ((creds or {}).get("number") or "")


def _number(to: str) -> str:
    n = re.sub(r"\D", "", to or "")
    return "27" + n[1:] if n.startswith("0") else n


async def _post(creds: dict, body: dict) -> bool:
    import httpx
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(graph_url(f"{creds['phone_id']}/messages"),
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


async def _ensure_template(name: str, body: str, example: list, buttons: Optional[list] = None) -> dict:
    """Make sure Vula's own WABA has template `name`; submit it for review if not. Safe to call at
    every boot. Never raises."""
    creds = _platform_creds()
    if not creds or not creds.get("waba_id"):
        return {"skipped": "Vula's own number is not set"}
    import httpx
    url = graph_url(f"{creds['waba_id']}/message_templates")
    headers = {"Authorization": f"Bearer {creds['token']}"}
    components: list = [{"type": "BODY", "text": body, "example": {"body_text": [example]}}]
    if buttons:
        components.append({"type": "BUTTONS", "buttons": [{"type": "QUICK_REPLY", "text": b} for b in buttons]})
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, headers=headers, params={"fields": "name,status", "name": name})
            live = resp.json().get("data", []) if resp.is_success else []
            if live:
                return {"already_exists": True, "status": live[0].get("status")}
            resp = await client.post(url, headers=headers, json={
                "name": name, "language": "en", "category": "UTILITY", "components": components})
            data = resp.json()
        if not resp.is_success:
            return {"error": (data.get("error") or {}).get("message") or "template submit failed"}
        return {"name": name, "status": data.get("status", "PENDING")}
    except Exception as exc:  # noqa: BLE001
        logger.warning("template %s check failed: %s", name, exc)
        return {"error": type(exc).__name__}


async def ensure_operator_template() -> dict:
    """Vula's own WABA has OPERATOR_TEMPLATE (submitted for review if not)."""
    return await _ensure_template(OPERATOR_TEMPLATE, _OPERATOR_BODY, [_OPERATOR_EXAMPLE])


# ── Talking to a new business from Vula's own number (vula/onboard_wa.py, 5 Nov 2026) ──────────
# A business set up from Master has no WhatsApp number of its own yet, so its owner works with
# Vula through Vula's number. While a message from Vula's number is being handled,
# REPLY_VIA_PLATFORM holds that business's id: every reply for that business goes out from
# Vula's number (whatsapp._get_tenant_wa_creds) and the message's media is fetched with Vula's
# token. Empty = the normal rules.
import contextvars as _contextvars  # noqa: E402

REPLY_VIA_PLATFORM: "_contextvars.ContextVar[str]" = _contextvars.ContextVar("vula_reply_via_platform",
                                                                              default="")
# Set for a message to Vula's number answered by onboard_wa itself (media fetched with Vula's token).
FROM_PLATFORM: "_contextvars.ContextVar[bool]" = _contextvars.ContextVar("vula_from_platform", default=False)

SETUP_TEMPLATE = "vula_business_setup"   # params: owner's first name, business name
_SETUP_BODY = ("Hi {{1}}, this is Vula. {{2}} is ready on Vula. Reply START and I'll set it up "
               "with you on WhatsApp, one question at a time.")
_SETUP_EXAMPLE = ["Sam", "Seaview Tiles"]


def platform_creds() -> Optional[dict]:
    return _platform_creds()


def is_platform_number(phone_number_id: str) -> bool:
    creds = _platform_creds() if phone_number_id else None
    return bool(creds) and phone_number_id == creds["phone_id"]


async def ensure_setup_template() -> dict:
    """Vula's own WABA has SETUP_TEMPLATE, the welcome a new business's owner gets."""
    return await _ensure_template(SETUP_TEMPLATE, _SETUP_BODY, _SETUP_EXAMPLE, buttons=["Start"])


async def send_text(to: str, text: str) -> bool:
    """Free text from Vula's own number (only arrives within 24 hours of their last message)."""
    creds = _platform_creds()
    if not creds:
        return False
    from core import dry_run as _dry
    if _dry.record_send("whatsapp", to, "platform_text"):
        return True
    try:
        return await _post(creds, {"messaging_product": "whatsapp", "to": _number(to), "type": "text",
                                   "text": {"body": text[:4000]}})
    except Exception as exc:  # noqa: BLE001
        logger.info("platform text not sent: %s", exc)
        return False


async def send_template(to: str, name: str, *params: str) -> bool:
    """An approved template from Vula's own number (arrives any time)."""
    creds = _platform_creds()
    if not creds:
        return False
    from core import dry_run as _dry
    if _dry.record_send("whatsapp", to, name):
        return True
    clean = [re.sub(r"\s+", " ", str(p)).strip()[:200] or "-" for p in params]
    body: dict = {"messaging_product": "whatsapp", "to": _number(to), "type": "template",
                  "template": {"name": name, "language": {"code": "en"}}}
    if clean:
        body["template"]["components"] = [{"type": "body", "parameters": [
            {"type": "text", "text": p} for p in clean]}]
    try:
        return await _post(creds, body)
    except Exception as exc:  # noqa: BLE001
        logger.info("platform template %s not sent: %s", name, exc)
        return False


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
