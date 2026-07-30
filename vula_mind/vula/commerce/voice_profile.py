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


async def _gather_samples(tenant_id: str) -> List[str]:
    texts: List[str] = []
    for fn in (_whatsapp_agent_replies, _whatsapp_self_chat, _meeting_notes, _email_excerpts):
        texts.extend(await fn(tenant_id))
    return [_redact(t) for t in texts[:SAMPLE_LIMIT]]


async def sample_count(tenant_id: str) -> int:
    """How many owner-authored samples exist across every source (for the "not enough data
    yet" guard)."""
    return len(await _gather_samples(tenant_id))


async def analyze_voice(tenant_id: str) -> Dict[str, Any]:
    """Analyse the tenant's own authored text (WhatsApp, meeting notes, sent email) and store
    a suggested persona_prompt.

    Returns {"suggested": str, "sample_count": int} or {"error": str}.
    """
    texts = await _gather_samples(tenant_id)
    if len(texts) < MIN_SAMPLE:
        return {"error": (
            f"Not enough data yet — Vula found {len(texts)} of your own messages/notes/emails "
            f"(needs {MIN_SAMPLE}). Take over a few more WhatsApp conversations, log a meeting, "
            f"or send a few emails first, then try again."
        )}

    import litellm
    from core.llm_router import resolve_generation_route

    sample_block = "\n".join(f"- {t}" for t in texts)
    prompt = (
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

    try:
        _client().table("vula_tenant_config").update({
            "persona_prompt_suggested": suggested,
            "persona_prompt_suggested_at": _now(),
        }).eq("tenant_id", tenant_id).execute()
    except Exception as exc:
        return {"error": f"{exc} (run migration 119?)"}

    try:
        from vula.api import tenants as _tenants
        _tenants.invalidate(tenant_id)
    except Exception:
        pass

    return {"suggested": suggested, "sample_count": len(texts)}
