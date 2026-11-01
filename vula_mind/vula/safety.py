"""
vula/safety.py — someone who may be at risk of harming themselves gets help, not a sales reply.

2026-11-01. A business's WhatsApp line is open to the public, and nothing in Vula recognised a
message like "I want to end my life": it went to the shop assistant like any other. Now, on every
inbound text (customer or team, any line), a high-precision phrase check runs first. On a match:

- the person gets a fixed reply — never model-written — with South Africa's crisis lines
  (SADAG Suicide Crisis Helpline 0800 567 567, 24 hours, free; Lifeline 0861 322 322; 10111 / 112
  for immediate danger), and nothing else is sent in reply to that message;
- one person at the business is told someone on the line may need them, with the number but
  never the message;
- the log carries no content (POPIA: this is special personal information).

Phrases are English and Afrikaans, chosen to avoid everyday figures of speech ("this price is
killing me"). Other South African languages are not covered yet — see docs/security-model.md.
Switch off with CRISIS_SUPPORT_ENABLED=false.
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Optional

log = logging.getLogger(__name__)

_CRISIS = re.compile(
    r"\b("
    r"kill(ing)? my ?self|end(ing)? (it all|my (own )?life)|take my (own )?life|"
    r"(want|wanna|going) to die|wish i (was|were) dead|better off dead|"
    r"(don'?t|do not) want to (live|be alive)|no (reason|point) (to|in) (living|live)|"
    r"suicid(e|al)|self[- ]?harm|hurt(ing)? my ?self|cut(ting)? my ?self|overdose on|"
    r"selfmoord|my(self)? (dood ?maak|om die lewe bring)|wil nie meer lewe nie|my lewe neem|"
    r"wil dood ?gaan"
    r")\b",
    re.IGNORECASE)

REPLY = (
    "I'm really sorry you're going through this. You don't have to face it alone. Please talk to "
    "someone who can help right now:\n\n"
    "• SADAG Suicide Crisis Helpline: 0800 567 567 (free, 24 hours)\n"
    "• Lifeline: 0861 322 322\n"
    "• In immediate danger: 10111, or 112 from a cell phone\n\n"
    "I've also asked someone at {business} to check in with you."
)


def crisis_signal(text: str) -> bool:
    from config import settings
    if not getattr(settings, "crisis_support_enabled", True):
        return False
    return bool(_CRISIS.search(text or ""))


def _ref(phone: str) -> str:
    return hashlib.sha256((phone or "").encode()).hexdigest()[:10]


async def handle(phone: str, text: str, tenant_id: Optional[str]) -> bool:
    """True when the message was a crisis signal and has been handled (the caller stops)."""
    if not crisis_signal(text):
        return False
    from vula.api import whatsapp as wa
    business = "the business"
    try:
        from vula.api.tenants import display_name
        business = display_name(tenant_id) if tenant_id else "the business"
    except Exception:  # noqa: BLE001
        pass
    log.warning("crisis support: signal on tenant=%s sender=%s (content not logged)", tenant_id, _ref(phone))
    await wa._send_reply(phone, REPLY.format(business=business), tenant_id)
    if tenant_id:
        try:
            from vula.escalation import _pick_helper
            helper = _pick_helper(tenant_id, exclude_phone=phone)
            to = (helper or {}).get("phone") or (helper or {}).get("whatsapp")
            if to:
                await wa._send_reply(to, (
                    f"⚠️ Someone messaging your WhatsApp line (+{phone.lstrip('+')}) may be at risk of "
                    "harming themselves. Vula has sent them the SADAG and Lifeline crisis numbers. "
                    "Please check in with them personally as soon as you can."), tenant_id)
        except Exception as exc:  # noqa: BLE001 — the person already has the helplines
            log.warning("crisis support: team alert failed for %s: %s", tenant_id, type(exc).__name__)
    return True
