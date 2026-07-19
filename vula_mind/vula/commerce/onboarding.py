"""
vula/commerce/onboarding.py — Staci-controlled opt-in / intro campaign for imported contacts.

Flow (state lives in commerce_contacts.onboarding_state, migration 047):
    invited            → nothing sent yet (import default)
    awaiting_reply     → intro template sent, waiting for them to reply
    confirming_optin   → they replied once; DOUBLE OPT-IN — asked to confirm explicitly before
                          consent_status flips to opted_in (new state value, no migration needed —
                          onboarding_state is a plain text column, see migration 047)
    collecting_name    → confirmed opted in; ask name
    collecting_address → ask delivery address
    collecting_email   → ask email (optional, SKIP allowed)
    complete           → onboarded; hand to normal ordering
    opted_out          → replied STOP (at any stage)

Consent: DOUBLE opt-in — the first reply only advances the conversation, it does NOT set
consent_status. A second reply (any non-STOP reply, made after seeing the explicit consent
question) is what marks consent_status='opted_in'; this is the stronger proof-of-consent pattern
(GDPR/Meta best practice, see project memory) versus the earlier single-reply design. STOP opts
out at any point. Nothing is sent proactively except the batch the merchant explicitly triggers —
and only to consent='unknown' contacts — so the WhatsApp number is never blasted. Marketing
broadcasts target opted_in only.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_STOP_RE = re.compile(r"^\s*(stop|unsubscribe|opt\s*out|cancel)\b", re.IGNORECASE)
_SKIP_RE = re.compile(r"^\s*(skip|no|none|n/?a)\s*$", re.IGNORECASE)
ACTIVE_STATES = ("awaiting_reply", "confirming_optin", "collecting_name", "collecting_address", "collecting_email")


def _client():
    from vula.commerce import service as cs
    return cs._client()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digits(phone: str) -> str:
    d = re.sub(r"\D", "", phone or "")
    return "27" + d[1:] if d.startswith("0") else d


def first_name(name: str) -> str:
    """A greeting-friendly first token from the (often messy, address-laden) imported name."""
    return (name or "").strip().split(" ")[0][:40] or "there"


def get_contact(tenant_id: str, phone: str) -> Optional[dict]:
    d = _digits(phone)
    if not d:
        return None
    try:
        rows = (_client().table("commerce_contacts").select("*")
                .eq("tenant_id", tenant_id).eq("phone", d).limit(1).execute().data or [])
    except Exception as exc:
        logger.debug("contact lookup skipped (run migration 046/047?): %s", exc)
        return None
    return rows[0] if rows else None


def active_capture(tenant_id: str, phone: str) -> Optional[dict]:
    """The contact if they're mid-onboarding (their next message is part of the flow)."""
    c = get_contact(tenant_id, phone)
    return c if c and c.get("onboarding_state") in ACTIVE_STATES else None


def _update(tenant_id: str, phone: str, patch: dict) -> None:
    patch["updated_at"] = _now()
    try:
        _client().table("commerce_contacts").update(patch).eq("tenant_id", tenant_id).eq(
            "phone", _digits(phone)).execute()
    except Exception as exc:
        logger.warning("contact update failed: %s", exc)


async def handle_capture(tenant_id: str, phone: str, text: str) -> Optional[str]:
    """Advance the onboarding state machine for one inbound message. Returns the reply to send,
    or None if this message is NOT part of onboarding (caller falls through to normal ordering)."""
    c = active_capture(tenant_id, phone)
    if not c:
        return None
    state = c.get("onboarding_state")
    body = (text or "").strip()

    if _STOP_RE.match(body):
        _update(tenant_id, phone, {"onboarding_state": "opted_out",
                                   "consent_status": "opted_out", "consent_at": _now()})
        try:
            from vula.api.commerce import record_opt_out
            record_opt_out(tenant_id, phone, source="onboarding_stop")
        except Exception:
            pass
        return "No problem — you won't get marketing messages from us. You can still order anytime. 🐟"

    if state == "awaiting_reply":
        # First reply — DOUBLE OPT-IN: this only advances the conversation, it does NOT set
        # consent yet. Ask an explicit confirmation question; consent is recorded only on the
        # NEXT reply (confirming_optin below), which is the stronger proof-of-consent step.
        _update(tenant_id, phone, {"onboarding_state": "confirming_optin"})
        return ("Hi! 👋 Just confirming — reply to this message if you'd like to receive order "
                "updates and specials from *Off the Hook* here on WhatsApp. Reply *STOP* anytime "
                "to opt out.")

    if state == "confirming_optin":
        # Second reply = explicit confirmation. This is what actually flips consent — the first
        # reply above only got them here, it was never recorded as consent.
        _update(tenant_id, phone, {"onboarding_state": "collecting_name",
                                   "consent_status": "opted_in", "consent_at": _now()})
        try:
            from vula.api.commerce import record_inbound_consent
            record_inbound_consent(tenant_id, phone, source="onboarding_double_optin_confirmed")
        except Exception:
            pass
        return ("🎉 Welcome to *Off the Hook* on WhatsApp! Let's set up your account so ordering is "
                "quick.\n\nFirst — what name should we put on your orders?")

    if state == "collecting_name":
        _update(tenant_id, phone, {"name": body[:80], "onboarding_state": "collecting_address"})
        return (f"Thanks {first_name(body)}! 📍 What's your *delivery address*? You can type it, "
                "or tap the 📎 attach icon and share your *location* pin instead.")

    if state == "collecting_address":
        _update(tenant_id, phone, {"address": body[:250], "onboarding_state": "collecting_email"})
        return ("Perfect. Last one — your *email* for receipts & specials? "
                "(or reply *SKIP* if you'd rather not)")

    if state == "collecting_email":
        if not _SKIP_RE.match(body) and "@" in body:
            email = body[:120]
            _update(tenant_id, phone, {"email": email, "onboarding_state": "complete"})
            try:  # feed the email-campaign list too
                _client().table("vula_email_contacts").upsert(
                    {"tenant_id": tenant_id, "email": email, "source": "onboarding"},
                    on_conflict="tenant_id,email").execute()
            except Exception:
                pass
        else:
            _update(tenant_id, phone, {"onboarding_state": "complete"})
        return ("You're all set! 🎣 You'll get first dibs on the daily catch.\n\n"
                "What can we get you today? Reply *menu* to see what's fresh.")

    return None


def pending_invites(tenant_id: str, limit: int = 50) -> list[dict]:
    """Contacts not yet invited (consent unknown, state 'invited') — the next batch to send."""
    try:
        return (_client().table("commerce_contacts")
                .select("phone,name").eq("tenant_id", tenant_id)
                .eq("consent_status", "unknown").eq("onboarding_state", "invited")
                .limit(limit).execute().data or [])
    except Exception as exc:
        logger.debug("pending invites skipped (run migration 047?): %s", exc)
        return []


def onboarding_stats(tenant_id: str) -> dict:
    """Counts per onboarding_state for the dashboard progress panel."""
    try:
        rows = (_client().table("commerce_contacts").select("onboarding_state,consent_status")
                .eq("tenant_id", tenant_id).limit(5000).execute().data or [])
    except Exception:
        rows = []
    from collections import Counter
    st = Counter(r.get("onboarding_state") for r in rows)
    return {
        "total": len(rows),
        "invited": st.get("invited", 0),
        "awaiting_reply": st.get("awaiting_reply", 0),
        "confirming_optin": st.get("confirming_optin", 0),
        "in_progress": st.get("collecting_name", 0) + st.get("collecting_address", 0) + st.get("collecting_email", 0),
        "complete": st.get("complete", 0),
        "opted_out": st.get("opted_out", 0),
        "opted_in": sum(1 for r in rows if r.get("consent_status") == "opted_in"),
    }


async def send_batch(tenant_id: str, *, template: str, language: str = "en",
                     limit: int = 50, test_phone: Optional[str] = None) -> dict:
    """Send the intro template to the next `limit` un-invited contacts (or just test_phone),
    filling {{1}} with the first name, and mark them awaiting_reply. `limit` caps batch SIZE
    (Staci sends a batch at a time so the number ramps gently) — the sends within a batch are
    additionally PACED at 10/s to stay under Meta's throughput/pair-rate limits (confirmed gap,
    2026-07-15: this loop previously fired unthrottled)."""
    from vula.api.whatsapp import _get_tenant_wa_creds
    creds = await _get_tenant_wa_creds(tenant_id)
    if not creds:
        return {"error": "No WhatsApp credentials for this tenant."}

    if test_phone:
        targets = [{"phone": _digits(test_phone), "name": "there"}]
    else:
        targets = pending_invites(tenant_id, limit)
    if not targets:
        return {"sent": 0, "failed": 0, "remaining": 0, "note": "no un-invited contacts left"}

    sent = failed = 0
    async with httpx.AsyncClient(timeout=15.0) as client:
        for i, c in enumerate(targets):
            if i:
                await asyncio.sleep(0.1)
            number = _digits(c.get("phone"))
            if not number:
                continue
            try:
                resp = await client.post(
                    f"https://graph.facebook.com/v19.0/{creds['phone_id']}/messages",
                    headers={"Authorization": f"Bearer {creds['token']}", "Content-Type": "application/json"},
                    json={"messaging_product": "whatsapp", "to": number, "type": "template",
                          "template": {"name": template, "language": {"code": language},
                                       "components": [{"type": "body", "parameters": [
                                           {"type": "text", "text": first_name(c.get("name"))}]}]}},
                )
                if resp.is_success:
                    sent += 1
                    if not test_phone:
                        _update(tenant_id, number, {"onboarding_state": "awaiting_reply",
                                                    "onboarding_at": _now()})
                else:
                    failed += 1
                    logger.warning("onboarding send failed %s: %s", number, resp.text[:200])
            except Exception as exc:
                failed += 1
                logger.warning("onboarding send error %s: %s", number, exc)

    remaining = 0 if test_phone else len(pending_invites(tenant_id, 5000))
    return {"sent": sent, "failed": failed, "remaining": remaining}
