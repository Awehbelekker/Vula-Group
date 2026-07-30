"""
vula/commerce/voice_profile.py — learn a tenant's actual writing tone from their own messages.

persona_prompt (migration 116) is a static field someone types once. This module analyses real
agent-authored WhatsApp text (commerce_conversation_messages.role='agent' — the owner/staff's own
replies sent via the shared-inbox handoff, NOT Vula's AI-generated replies) and asks the LLM to
*describe* the tone/formality/phrasing it sees, in a few sentences. The result is stored as a
SUGGESTION only (vula_tenant_config.persona_prompt_suggested) — never auto-applied. The owner
accepts, edits, or dismisses it via the dashboard.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict

log = logging.getLogger(__name__)

MIN_SAMPLE = 15
SAMPLE_LIMIT = 40


def _client():
    from vula.commerce import service
    return service._client()


def _clean(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def sample_count(tenant_id: str) -> int:
    """How many agent-authored messages exist for this tenant (for the "not enough data yet" guard)."""
    try:
        rows = (_client().table("commerce_conversation_messages")
                .select("id").eq("tenant_id", tenant_id).eq("role", "agent")
                .limit(SAMPLE_LIMIT).execute().data or [])
    except Exception as exc:
        log.debug("voice_profile: sample_count skipped: %s", exc)
        return 0
    return len(rows)


async def analyze_voice(tenant_id: str) -> Dict[str, Any]:
    """Analyse the tenant's own agent-authored replies and store a suggested persona_prompt.

    Returns {"suggested": str, "sample_count": int} or {"error": str}.
    """
    try:
        rows = (_client().table("commerce_conversation_messages")
                .select("content,created_at").eq("tenant_id", tenant_id).eq("role", "agent")
                .order("created_at", desc=True).limit(SAMPLE_LIMIT).execute().data or [])
    except Exception as exc:
        return {"error": f"{exc} (run migration 048?)"}

    texts = [r["content"].strip() for r in rows if (r.get("content") or "").strip()]
    if len(texts) < MIN_SAMPLE:
        return {"error": (
            f"Not enough data yet — Vula found {len(texts)} of your own replies "
            f"(needs {MIN_SAMPLE}). Take over a few more conversations in the inbox first, "
            f"then try again."
        )}

    import litellm
    from core.llm_router import resolve_generation_route

    sample_block = "\n".join(f"- {t}" for t in texts[:SAMPLE_LIMIT])
    prompt = (
        "Below are real WhatsApp messages a small-business owner personally typed to their "
        "customers. Describe how they write — tone, formality, emoji use, typical greetings "
        "or sign-offs, sentence length, any recurring phrases. Write 2-4 sentences as direct "
        "instructions for an AI assistant to sound like them (e.g. 'Warm and casual, short "
        "replies, uses \"howzit\" and a thumbs-up emoji, never uses full stops on short "
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
