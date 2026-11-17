"""How Vula sounds: the company's voice, the person's own preferences, and the channel — composed
in ONE place and read by every skill.

Before this the company voice (vula_tenant_config.persona_prompt) reached two skills only, each
with its own copy of the wording; the knowledge, finance, email and calendar skills never saw it.
Nothing was per person, and every prompt assumed WhatsApp even when the reply was shown in the
dashboard.

The entry point (a WhatsApp turn, the dashboard chat) says who it is talking to once, with
`voice.use(...)`. `core.skills.base.behaviour_preamble` then appends `current_block()` to the
shared rules, so every skill gets the same voice without being edited.

Three layers, narrowest last:
  company   the business's voice: the team voice for its own people, the customer voice for
            customers. A voice learned from customer messages never reaches staff.
  person    a staff member's own tweak, within the company voice: style, reply length, language,
            what to call them. FIXED CHOICES, not free text — a preference can't carry an
            instruction or a "fact".
  channel   WhatsApp, or the dashboard.

Style changes how something is said, never what is true. The block says so, and it is placed
after the honesty and integrity rules, which win any conflict.
"""
from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

AUDIENCES = ("team", "customer")
CHANNELS = ("whatsapp", "dashboard")

# What a staff member can choose. The wording is ours, so nothing they pick becomes an instruction.
STYLES: Dict[str, str] = {
    "friendly": "warm and conversational, like a colleague they get on with",
    "fun": "light and upbeat, with a bit of humour; an emoji now and then is welcome",
    "formal": "professional and polished: full sentences, no slang, no emoji",
    "direct": "straight to the point: the answer first, no pleasantries or padding",
}
LENGTHS: Dict[str, str] = {
    "short": "Keep replies as short as the answer allows: a line or two when that covers it.",
    "medium": "",                       # the default — nothing to add
    "detailed": "Give the fuller picture: the answer, then the reasoning or breakdown behind it.",
}
_NICKNAME_RE = re.compile(r"[^\w .'\-]", re.UNICODE)
_MAX_NICKNAME = 30

# 14 Nov 2026 (DIGG): a voice learned mostly from sent emails ("open with Hi <name>, sign off Kind
# Regards with a full signature block") was applied whole to chat, so some replies read as letters
# and others as messages. Chat is always message-shaped; the email voice stays for drafted emails.
CHAT_SHAPE = (
    "In this chat, reply as a message, not a letter: no 'Hi <name>' opener, no sign-off, no "
    "signature block. Those belong only inside an email or document you are drafting for them."
)
# One length rule for WhatsApp instead of each skill's own; a person's chosen length wins.
WHATSAPP_LENGTH = (
    "On WhatsApp: the answer first, in a few short lines (about 80 words at most); if there's "
    "more, give the headline and offer the rest."
)

STYLE_ONLY = (
    "The lines above are STYLE ONLY. They change how you say things, never what is true: "
    "figures, names, dates, and whether something was done stay exactly as your tools and "
    "records give them. Warnings, caveats, 'I don't have that' and requests to confirm are "
    "never shortened away or softened to fit a style. If a style line conflicts with any other "
    "rule you have been given, the other rule wins."
)


def clean_prefs(prefs: Any) -> Dict[str, str]:
    """Only the known preference keys, with known values. Anything else is dropped — this is the
    one gate between what a person typed into a settings form and a system prompt."""
    if not isinstance(prefs, dict):
        return {}
    out: Dict[str, str] = {}
    style = str(prefs.get("style") or "").strip().lower()
    if style in STYLES:
        out["style"] = style
    length = str(prefs.get("reply_length") or "").strip().lower()
    if length in LENGTHS and length != "medium":
        out["reply_length"] = length
    lang = str(prefs.get("language") or "").strip().lower()
    if lang:
        try:
            from core.lang import language_name, normalize_lang
            code = normalize_lang(lang)
            if code and language_name(code):
                out["language"] = code
        except Exception:  # noqa: BLE001
            pass
    nick = _NICKNAME_RE.sub("", str(prefs.get("nickname") or "")).strip()[:_MAX_NICKNAME].strip()
    if nick:
        out["nickname"] = nick
    return out


@dataclass(frozen=True)
class Voice:
    tenant_id: str
    audience: str = "customer"
    channel: str = "whatsapp"
    prefs: Dict[str, str] = field(default_factory=dict)


_CURRENT: ContextVar[Optional[Voice]] = ContextVar("vula_voice", default=None)


def enabled(tenant_id: Optional[str] = None) -> bool:
    """On for everyone (VOICE_COMPOSE_ENABLED), or for this business (vula/features.py)."""
    try:
        from vula import features
        return features.on(tenant_id, "voice_compose")
    except Exception:  # noqa: BLE001
        return False


@contextmanager
def use(tenant_id: str, audience: str = "customer", channel: str = "whatsapp",
        prefs: Optional[Dict[str, Any]] = None):
    """Set who this turn is for. Everything a skill builds inside the block gets this voice."""
    voice = Voice(tenant_id=tenant_id or "",
                  audience=audience if audience in AUDIENCES else "customer",
                  channel=channel if channel in CHANNELS else "whatsapp",
                  prefs=clean_prefs(prefs) if audience == "team" else {})
    token = _CURRENT.set(voice)
    try:
        yield voice
    finally:
        _CURRENT.reset(token)


def current() -> Optional[Voice]:
    v = _CURRENT.get()
    return v if v is not None and enabled(v.tenant_id) else None


def active() -> bool:
    """True when the composed voice is in force — a skill that used to add its own copy of the
    company voice leaves it out, so it isn't said twice."""
    return current() is not None


def preferred_language() -> str:
    """The language the person chose for themselves, or "". It outranks a detected language."""
    v = current()
    return (v.prefs.get("language") or "") if v else ""


def company_voice(tenant_id: str, audience: str) -> str:
    """The business's voice for this audience, or ""."""
    try:
        from vula.api.tenants import get_config
        from vula.commerce.voice_profile import _parts, persona_part
        cfg = get_config(tenant_id) or {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("company voice skipped: %s", exc)
        return ""
    persona = (cfg.get("persona_prompt") or "").strip()
    if audience == "customer":
        return persona_part(persona, "chat")
    team = (cfg.get("team_voice_prompt") or "").strip()
    if team:
        return team
    # No team voice set: a voice the owner wrote by hand describes the business as a whole and is
    # used. One LEARNED from customer messages ("Chat replies: … Promotions: …") is how the shop
    # talks to its customers — not how Vula should talk to the people who run it.
    return "" if _parts(persona) else persona


def chat_voice(tenant_id: str, audience: str) -> str:
    """The company voice for a chat reply, with the message-not-a-letter line, or "". For a
    caller that adds the voice itself (the composer off)."""
    company = company_voice(tenant_id, audience)
    return f"{company}\n{CHAT_SHAPE}" if company else ""


def compose(tenant_id: str, audience: str = "customer", channel: str = "whatsapp",
            prefs: Optional[Dict[str, Any]] = None) -> str:
    """The style block for one turn, or "" when there is nothing to say."""
    prefs = clean_prefs(prefs) if audience == "team" else {}
    lines = []
    company = company_voice(tenant_id, audience)
    if company:
        lines.append(f"How this business sounds: {company}")
        lines.append(CHAT_SHAPE)
    if prefs.get("style"):
        lines.append(f"This person prefers you to be {STYLES[prefs['style']]}.")
    if LENGTHS.get(prefs.get("reply_length", ""), ""):
        lines.append(LENGTHS[prefs["reply_length"]])
    if prefs.get("nickname"):
        lines.append(f"They like to be called {prefs['nickname']}.")
    if channel == "dashboard":
        lines.append("This conversation is in the web dashboard, not on WhatsApp: don't call it "
                     "WhatsApp or say you'll message them there, and a longer reply is fine "
                     "where the detail helps.")
    if not lines:
        return ""
    if channel == "whatsapp" and not LENGTHS.get(prefs.get("reply_length", ""), ""):
        lines.append(WHATSAPP_LENGTH)
    return "\n".join(lines) + "\n" + STYLE_ONLY + "\n"



def current_block() -> str:
    """The style block for the turn in progress, or "" (no turn set, or the feature is off)."""
    v = current()
    if not v:
        return ""
    try:
        return compose(v.tenant_id, v.audience, v.channel, v.prefs)
    except Exception as exc:  # noqa: BLE001 — a voice must never cost a reply
        logger.debug("voice block skipped: %s", exc)
        return ""
