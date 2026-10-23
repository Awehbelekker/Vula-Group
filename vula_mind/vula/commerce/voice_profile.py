"""
vula/commerce/voice_profile.py — learn a tenant's actual writing tone from their own text.

persona_prompt (migration 116) is a static field someone types once. This module gathers real
owner/staff-authored text from every channel Vula already has stored (never AI-generated text)
and asks the LLM to *describe* the tone/formality/phrasing it sees, in a few sentences:

  - WhatsApp: commerce_conversation_messages.role='agent' — replies sent via the shared-inbox
    handoff (admin_reply, vula/api/commerce.py).
  - WhatsApp: the owner/staff's own messages TO Vula when running the shop via WhatsApp
    (role='user' under an "admin:{phone}" session — vula/api/whatsapp.py::_run_commerce_admin).
  - Meeting notes: raw dictated/typed notes from log_meeting (vula_filed_documents,
    category='meeting_notes').
  - Email: Sent-folder excerpts captured by vula/email_imap/sync.py — genuinely human-composed
    only, AI-composed/auto-sent mail is tagged and excluded there (see vula_sent flag).

The result is stored as a SUGGESTION only (vula_tenant_config.persona_prompt_suggested) — never
auto-applied. The owner accepts, edits, or dismisses it via the dashboard.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List

log = logging.getLogger(__name__)

MIN_SAMPLE = 15
SAMPLE_LIMIT = 60
_PER_SOURCE_LIMIT = 40

# Tenant Mind Phase 2 (2026-09-15): how many NEW real samples must accumulate since the last
# analysis before a scheduled re-check is worth an LLM call — see due_for_recheck().
MIN_GROWTH_FOR_RECHECK = 10

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"(?<!\w)(\+?\d[\d\s\-()]{6,}\d)(?!\w)")


def _client():
    from vula.commerce import service
    return service._client()


def _clean(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()


def _redact(text: str) -> str:
    """Strip embedded emails/phone numbers before this ever reaches an LLM call or gets
    reused — this is the owner's own writing, but it may reference a customer's contact
    details inline (e.g. a meeting note or an email reply)."""
    text = _EMAIL_RE.sub("[email]", text)
    return _PHONE_RE.sub("[phone]", text)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _whatsapp_agent_replies(tenant_id: str) -> List[str]:
    try:
        rows = (_client().table("commerce_conversation_messages")
                .select("content").eq("tenant_id", tenant_id).eq("role", "agent")
                .order("created_at", desc=True).limit(_PER_SOURCE_LIMIT).execute().data or [])
    except Exception as exc:
        log.debug("voice_profile: whatsapp agent-reply source skipped (run migration 048?): %s", exc)
        return []
    return [r["content"].strip() for r in rows if (r.get("content") or "").strip()]


async def _whatsapp_self_chat(tenant_id: str) -> List[str]:
    """The owner/staff's own typed messages TO Vula (running the shop by WhatsApp) —
    stored as role='user' under an "admin:{phone}" session, distinct from a customer's
    role='user' messages in a normal storefront session."""
    try:
        sessions = (_client().table("commerce_conversation_sessions")
                    .select("id").eq("tenant_id", tenant_id)
                    .like("session_key", "admin:%").execute().data or [])
        session_ids = [s["id"] for s in sessions]
        if not session_ids:
            return []
        rows = (_client().table("commerce_conversation_messages")
                .select("content").eq("tenant_id", tenant_id).eq("role", "user")
                .in_("session_id", session_ids)
                .order("created_at", desc=True).limit(_PER_SOURCE_LIMIT).execute().data or [])
    except Exception as exc:
        log.debug("voice_profile: whatsapp self-chat source skipped: %s", exc)
        return []
    return [r["content"].strip() for r in rows if (r.get("content") or "").strip()]


async def _meeting_notes(tenant_id: str) -> List[str]:
    try:
        rows = (_client().table("vula_filed_documents")
                .select("fields").eq("tenant_id", tenant_id).eq("category", "meeting_notes")
                .order("created_at", desc=True).limit(_PER_SOURCE_LIMIT).execute().data or [])
    except Exception as exc:
        log.debug("voice_profile: meeting-notes source skipped: %s", exc)
        return []
    out = []
    for r in rows:
        notes = (r.get("fields") or {}).get("raw_notes") or ""
        if notes.strip():
            out.append(notes.strip()[:600])
    return out


async def _email_excerpts(tenant_id: str) -> List[str]:
    try:
        rows = (_client().table("vula_email_voice_samples")
                .select("excerpt").eq("tenant_id", tenant_id)
                .order("created_at", desc=True).limit(_PER_SOURCE_LIMIT).execute().data or [])
    except Exception as exc:
        log.debug("voice_profile: email source skipped (run migration 120?): %s", exc)
        return []
    return [r["excerpt"].strip() for r in rows if (r.get("excerpt") or "").strip()]


async def _promotions(tenant_id: str) -> List[str]:
    """The business's own promotions, forwarded into its WhatsApp line (migration 220) — the
    clearest sample of how it talks to customers. Prices are masked: they're not tone."""
    try:
        from vula.commerce import promotions
        return [t[:600] for t in promotions.examples(tenant_id, n=_PER_SOURCE_LIMIT)]
    except Exception as exc:
        log.debug("voice_profile: promotions source skipped: %s", exc)
        return []


def audience(tenant_id: str) -> str:
    """"customer" for a shop: its voice is learned only from what it writes to customers.
    2026-10-23 (Off the Hook): the owner's own commands to Vula ("stock hake 5") made up most of
    the samples, so customers got "concise, 👍, no full stops" instead of OTH's warm,
    emoji-rich voice. Every other business keeps all sources ("all")."""
    try:
        from vula.api.tenants import tenant_profile
        prof = tenant_profile(tenant_id)
        return "customer" if prof.get("known") and prof.get("sells_products") else "all"
    except Exception:
        return "all"


async def _gather_samples(tenant_id: str, audience_: str = "all") -> List[str]:
    sources = ((_promotions, _whatsapp_agent_replies, _email_excerpts) if audience_ == "customer"
               else (_whatsapp_agent_replies, _whatsapp_self_chat, _meeting_notes, _email_excerpts))
    texts: List[str] = []
    for fn in sources:
        texts.extend(await fn(tenant_id))
    texts = [_redact(t) for t in texts[:SAMPLE_LIMIT]]
    if audience_ == "customer":
        from vula.commerce.promotions import mask_prices
        texts = [mask_prices(t) for t in texts]
    return texts


async def sample_count(tenant_id: str) -> int:
    """How many owner-authored samples exist across every source (for the "not enough data
    yet" guard)."""
    return len(await _gather_samples(tenant_id, audience(tenant_id)))


# A customer voice is stored as two headed parts: how to sound in chat, and how promotions look.
_CHAT_HEAD, _PROMO_HEAD = "Chat replies:", "Promotions:"
_PART_RE = re.compile(r"^\s*(chat replies|promotions|example)\s*:\s*", re.I | re.M)


def _parts(text: str) -> Dict[str, str]:
    marks = list(_PART_RE.finditer(text or ""))
    out: Dict[str, str] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out[m.group(1).lower()] = text[m.end():end].strip()
    return out


def persona_part(persona: str, part: str) -> str:
    """The "chat" or "promotions" part of a stored voice. A voice without parts (written by
    hand, or learned before parts existed) is used whole for chat and not at all for promotions."""
    parts = _parts(persona or "")
    if not parts:
        return (persona or "").strip() if part == "chat" else ""
    return parts.get("chat replies" if part == "chat" else "promotions", "")


_CUSTOMER_PROMPT = (
    "Below are real messages a small business wrote to its customers — WhatsApp promotions it "
    "posted, replies its staff sent, and emails. Describe how this business talks to its "
    "customers. Answer in exactly three labelled parts:\n"
    "Chat replies: 2-3 sentences of direct instructions for an AI assistant answering a "
    "customer's WhatsApp message in this voice — warmth, greetings, sign-offs, emoji they really "
    "use, sentence length, recurring phrases. Chat replies stay short; no capital-letter "
    "headlines.\n"
    "Promotions: 2-3 sentences on how their promotions look — headline style, emoji, line "
    "layout, call to action.\n"
    "Example: a reply in that chat voice to a customer who wrote \"Thanks, I'll place my order "
    "tomorrow\" — no products, prices, dates or opening hours.\n"
    "Do not invent traits you don't see evidence for. No preamble.\n\nMessages:\n"
)


def has_pending_suggestion(cfg: Dict[str, Any]) -> bool:
    """A suggestion the owner hasn't reviewed yet. One identical to the voice already in use
    isn't pending (Off the Hook had one sitting there, which stopped any re-learning)."""
    sug = (cfg.get("persona_prompt_suggested") or "").strip()
    return bool(sug) and sug != (cfg.get("persona_prompt") or "").strip()


async def due_for_recheck(tenant_id: str, min_growth: int = MIN_GROWTH_FOR_RECHECK) -> bool:
    """Should the scheduled loop re-analyse this tenant's voice right now?

    True only when: there's at least MIN_SAMPLE data (same bar analyze_voice() itself uses),
    genuinely new growth since the last analysis (not just re-running on the same data for no
    reason), and no suggestion is already sitting there unreviewed — a fresh suggestion must
    never silently replace one the owner hasn't looked at yet. Fails open to False: an error
    here should skip a tenant this tick, never crash the loop that calls it for everyone."""
    try:
        rows = (_client().table("vula_tenant_config")
                .select("persona_prompt,persona_prompt_suggested,voice_last_checked_sample_count")
                .eq("tenant_id", tenant_id).limit(1).execute().data or [])
        cfg = rows[0] if rows else {}
        if has_pending_suggestion(cfg):
            return False  # an unreviewed suggestion is already pending — don't pile on
        current = await sample_count(tenant_id)
        if current < MIN_SAMPLE:
            return False
        last_checked = cfg.get("voice_last_checked_sample_count") or 0
        return (current - last_checked) >= min_growth
    except Exception as exc:
        log.debug("voice_profile: due_for_recheck skipped for %s: %s", tenant_id, exc)
        return False


async def analyze_voice(tenant_id: str) -> Dict[str, Any]:
    """Analyse the tenant's own authored text (WhatsApp, meeting notes, sent email) and store
    a suggested persona_prompt.

    Returns {"suggested": str, "sample_count": int} or {"error": str}. A shop's customer voice
    (see audience) also returns "example": a sample reply written in it, shown to the owner."""
    aud = audience(tenant_id)
    texts = await _gather_samples(tenant_id, aud)
    if len(texts) < MIN_SAMPLE:
        return {"error": (
            f"Not enough data yet — Vula found {len(texts)} of your own messages/notes/emails "
            f"(needs {MIN_SAMPLE}). Take over a few more WhatsApp conversations, log a meeting, "
            f"or send a few emails first, then try again."
        )}

    import litellm
    from core.llm_router import resolve_generation_route

    sample_block = "\n".join(f"- {t}" for t in texts)
    prompt = _CUSTOMER_PROMPT + sample_block if aud == "customer" else (
        "Below are real messages, notes, and emails a small-business owner personally wrote — "
        "some to customers on WhatsApp, some to their own AI assistant, some meeting notes, "
        "some sent emails. Describe how they write — tone, formality, emoji use, typical "
        "greetings or sign-offs, sentence length, any recurring phrases. Write 2-4 sentences as "
        "direct instructions for an AI assistant to sound like them (e.g. 'Warm and casual, "
        "short replies, uses \"howzit\" and a thumbs-up emoji, never uses full stops on short "
        "replies.'). Do not invent traits you don't see evidence for. Start directly with the "
        "instructions — no preamble like 'Here are...'.\n\nMessages:\n" + sample_block
    )

    litellm.drop_params = True
    model, api_key, api_base = await resolve_generation_route(task_type="voice_profile")
    try:
        resp = await litellm.acompletion(
            # Local models here are reasoning models (e.g. deepseek-r1) that spend tokens on an
            # internal think-pass before the real answer — 250 left nothing for the answer itself
            # and silently produced empty content; 900 gives it room for both.
            model=model, messages=[{"role": "user", "content": prompt}],
            temperature=0.4, max_tokens=900, api_key=api_key, api_base=api_base,
        )
        suggested = _clean(resp.choices[0].message.content or "")
    except Exception as exc:
        log.warning("voice_profile: analysis failed: %s", exc)
        return {"error": "Could not analyse your voice right now — please try again."}

    if not suggested:
        return {"error": "Empty response — please try again."}
    example = ""
    if aud == "customer":
        parts = _parts(suggested)
        if not parts.get("chat replies"):
            return {"error": "Could not describe your voice clearly — please try again."}
        example = parts.get("example", "")
        suggested = f"{_CHAT_HEAD} {parts['chat replies']}"
        if parts.get("promotions"):
            suggested += f"\n{_PROMO_HEAD} {parts['promotions']}"
        suggested = suggested[:2500]

    try:
        _client().table("vula_tenant_config").update({
            "persona_prompt_suggested": suggested,
            "persona_prompt_suggested_at": _now(),
            # Marks "we analysed at this much data" so due_for_recheck() (migration 163) can
            # tell real new growth from noise — set on every successful analysis, whichever
            # path triggered it (dashboard button, WhatsApp admin tool, or the scheduled
            # recheck loop), so none of them fights the others into re-suggesting too often.
            "voice_last_checked_sample_count": len(texts),
        }).eq("tenant_id", tenant_id).execute()
    except Exception as exc:
        return {"error": f"{exc} (run migration 119?)"}

    try:
        from vula.api import tenants as _tenants
        _tenants.invalidate(tenant_id)
    except Exception:
        pass

    return {"suggested": suggested, "sample_count": len(texts), "example": example}
