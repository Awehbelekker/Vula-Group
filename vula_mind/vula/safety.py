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
killing me"). Messages in the other South African languages go through the escalation AI check,
which carries a crisis flag. Abuse (GBV Command Centre 0800 428 428) and substance abuse (SADAG
0800 12 13 14) get their own lines; the business is told only about a self-harm risk.
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

_ABUSE = re.compile(
    r"\b("
    r"(he|she|my (husband|wife|boyfriend|girlfriend|partner|father|stepfather|uncle|ex)) "
    r"(hits|beats|beat|hit|abuses|abused|rapes|raped|chokes|choked|threatens to kill) me|"
    r"i (was|got|have been|am being) (raped|abused|beaten)|being abused|domestic (violence|abuse)|"
    r"gender[- ]based violence|"
    r"(hy|sy|my (man|vrou|kêrel|meisie)) (slaan|mishandel|verkrag) my|ek (is|word) (verkrag|mishandel)"
    r")\b",
    re.IGNORECASE)

_SUBSTANCE = re.compile(
    r"\b("
    r"(i'?m|i am) addicted to (drugs|alcohol|tik|heroin|nyaope|whoonga|cocaine|meth|pills|crack)|"
    r"(drug|drinking|alcohol) (problem|addiction)|can'?t stop (drinking|using)|i'?m an alcoholic|"
    r"verslaaf aan (dwelms|drank|tik)"
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

ABUSE_REPLY = (
    "I'm so sorry this is happening to you. You deserve to be safe. Free help is available any "
    "time, in confidence:\n\n"
    "• GBV Command Centre: 0800 428 428 (24 hours) — or SMS \"HELP\" to 31531\n"
    "• In immediate danger: 10111, or 112 from a cell phone"
)

SUBSTANCE_REPLY = (
    "Thank you for saying it — that takes courage. Free, confidential help is available:\n\n"
    "• SADAG Substance Abuse Helpline: 0800 12 13 14 (24 hours)\n"
    "• If you or someone is in danger right now: 10111, or 112 from a cell phone"
)


def _enabled() -> bool:
    from config import settings
    return bool(getattr(settings, "crisis_support_enabled", True))


def kind_of(text: str) -> Optional[str]:
    """"self_harm" | "abuse" | "substance" from the phrase lists, else None. Self-harm first."""
    if not _enabled():
        return None
    for kind, pat in (("self_harm", _CRISIS), ("abuse", _ABUSE), ("substance", _SUBSTANCE)):
        if pat.search(text or ""):
            return kind
    return None


def crisis_signal(text: str) -> bool:
    """A self-harm signal in the phrase lists."""
    return kind_of(text) == "self_harm"


def _ref(phone: str) -> str:
    return hashlib.sha256((phone or "").encode()).hexdigest()[:10]


async def _detect(text: str) -> Optional[str]:
    kind = kind_of(text)
    if kind or not _enabled():
        return kind
    # Other SA languages (isiZulu, isiXhosa, Sesotho, …): the escalation AI check already reads these,
    # and carries a crisis flag (cached, so the customer path doesn't classify the message twice).
    try:
        from vula import escalation as esc
        if esc.needs_classifier(text):
            verdict = await esc.classify_customer_message(text)
            if verdict and verdict.get("crisis"):
                return "self_harm"
    except Exception:  # noqa: BLE001 — the phrase lists already ran
        pass
    return None


async def handle(phone: str, text: str, tenant_id: Optional[str]) -> bool:
    """True when the message was a crisis signal and has been handled (the caller stops)."""
    kind = await _detect(text)
    if not kind:
        return False
    from vula.api import whatsapp as wa
    log.warning("crisis support: %s signal on tenant=%s sender=%s (content not logged)",
                kind, tenant_id, _ref(phone))
    if kind == "abuse":
        # The business is NOT told: the person causing harm may be connected to it.
        await wa._send_reply(phone, ABUSE_REPLY, tenant_id)
        return True
    if kind == "substance":
        await wa._send_reply(phone, SUBSTANCE_REPLY, tenant_id)
        return True
    business = "the business"
    try:
        from vula.api.tenants import display_name
        business = display_name(tenant_id) if tenant_id else "the business"
    except Exception:  # noqa: BLE001
        pass
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
