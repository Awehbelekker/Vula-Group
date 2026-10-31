"""
vula/api/whatsapp_coexist.py — a business's WhatsApp Business app number shared with Vula
(Meta's "coexistence", 25 Oct 2026).

During Embedded Signup the owner can link the WhatsApp Business app number they already use. They
keep the app on their phone; Vula answers on the same number through the Cloud API. Three things
only happen on such a number, and land here from the webhook (vula/api/whatsapp.py):

  smb_message_echoes   the owner replied from the app on their phone → the reply goes into that
                       chat's thread and Vula goes quiet in it, exactly as when someone takes over
                       from the dashboard's Inbox (commerce_conversation_sessions.paused). The
                       existing stale-handoff job hands a chat back to Vula after 2 hours of
                       silence, so a chat the owner walked away from isn't left unanswered.
  history              the chats the owner chose to share while connecting → imported into each
                       chat's thread (text only, the latest HISTORY_PER_CHAT per chat), so Vula and
                       the Inbox see what was said before.
  smb_app_state_sync   the app's contacts → a chat with no customer name gets the owner's name for
                       that contact. Nothing else is stored.

Right after connecting, request_app_sync() asks Meta to send the contacts and history; Meta only
sends them when asked within 24 hours of the owner's choice. Nothing here sends a message.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

log = logging.getLogger(__name__)

from vula.meta_graph import graph_url  # noqa: E402  (the Graph API version lives in config)
COEXIST_FIELDS = ("smb_message_echoes", "history", "smb_app_state_sync")
HISTORY_PER_CHAT = 50
OWNER_ON_PHONE = "Owner (WhatsApp Business app)"


def _db():
    from vula.commerce import service
    return service._client()


def _digits(phone: Any) -> str:
    return re.sub(r"\D", "", str(phone or ""))


def _text_of(msg: Dict[str, Any]) -> str:
    """The words of a message, or a short label for anything that isn't text."""
    kind = msg.get("type") or "text"
    if kind == "text":
        return ((msg.get("text") or {}).get("body") or "").strip()
    caption = ((msg.get(kind) or {}) if isinstance(msg.get(kind), dict) else {}).get("caption") or ""
    label = {"image": "photo", "video": "video", "audio": "voice note", "document": "document",
             "sticker": "sticker", "location": "location", "contacts": "contact card"}.get(kind, kind)
    return f"[{label}] {caption}".strip()


def _when(ts: Any) -> str:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return datetime.now(timezone.utc).isoformat()


# ── After connecting ──────────────────────────────────────────────────────────

async def request_app_sync(phone_number_id: str, token: str) -> Dict[str, str]:
    """Ask Meta for the app's contacts and, if the owner chose to share it, its chat history.
    Returns what Meta said for each ("requested" or its error), stored on the account row."""
    out: Dict[str, str] = {}
    async with httpx.AsyncClient(timeout=20.0) as client:
        for key, sync_type in (("contacts", "smb_app_state_sync"), ("history", "history")):
            try:
                resp = await client.post(graph_url(f"{phone_number_id}/smb_app_data"),
                                         headers={"Authorization": f"Bearer {token}"},
                                         json={"messaging_product": "whatsapp", "sync_type": sync_type})
                out[key] = "requested" if resp.is_success else (
                    ((resp.json() or {}).get("error") or {}).get("message") or resp.text[:200])
            except Exception as exc:
                out[key] = f"not requested: {exc}"[:200]
    log.info("coexistence sync for %s: %s", phone_number_id, out)
    return out


# ── Webhook ───────────────────────────────────────────────────────────────────

def _session_row(tenant_id: str, phone: str) -> Optional[Dict[str, Any]]:
    rows = (_db().table("commerce_conversation_sessions").select("id,paused,customer_name")
            .eq("tenant_id", tenant_id).eq("session_key", phone).limit(1).execute().data or [])
    return rows[0] if rows else None


async def handle(field: str, tenant_id: Optional[str], value: Dict[str, Any]) -> int:
    """One coexistence webhook change. Returns how many items it took in."""
    if not tenant_id:
        log.info("coexistence %s for an unknown number (%s) — ignored", field,
                 (value.get("metadata") or {}).get("phone_number_id"))
        return 0
    if field == "smb_message_echoes":
        return await _echoes(tenant_id, value.get("message_echoes") or [])
    if field == "history":
        return await _history(tenant_id, value.get("history") or [])
    if field == "smb_app_state_sync":
        return _contacts(tenant_id, value.get("state_sync") or [])
    return 0


async def _echoes(tenant_id: str, echoes: List[Dict[str, Any]]) -> int:
    """The owner wrote to a customer from the app: into the thread, and Vula steps back."""
    from vula.api.whatsapp import _durable_dedup_claim
    from vula.commerce import service
    took = 0
    for e in echoes:
        customer, text = _digits(e.get("to")), _text_of(e)
        if not customer or not text or (e.get("id") and not _durable_dedup_claim(f"echo:{e['id']}")):
            continue
        session = await service.get_or_create_session(tenant_id, session_key=customer,
                                                      channel="whatsapp", customer_phone=customer)
        await service.append_message(tenant_id, session["id"], "agent", text)
        if not session.get("paused"):
            await service.set_session_paused(tenant_id, session["id"], True, paused_by=OWNER_ON_PHONE)
        took += 1
    if took:
        log.info("coexistence: %d reply(ies) from %s's phone — Vula quiet in those chats", took, tenant_id)
    return took


async def _history(tenant_id: str, chunks: List[Dict[str, Any]]) -> int:
    """Shared chat history: text messages into each chat's thread, oldest first, capped per chat."""
    from vula.api.whatsapp import _durable_dedup_claim
    from vula.commerce import service
    took = 0
    for chunk in chunks:
        if chunk.get("errors"):        # the owner chose not to share history, or Meta couldn't
            log.info("coexistence history for %s not shared: %s", tenant_id, chunk["errors"])
            continue
        for thread in chunk.get("threads") or []:
            customer = _digits(thread.get("id"))
            msgs = [m for m in (thread.get("messages") or []) if _text_of(m)]
            if not customer or not msgs:
                continue
            msgs = sorted(msgs, key=lambda m: int(m.get("timestamp") or 0))[-HISTORY_PER_CHAT:]
            session = await service.get_or_create_session(tenant_id, session_key=customer,
                                                          channel="whatsapp", customer_phone=customer)
            rows = []
            for m in msgs:
                if m.get("id") and not _durable_dedup_claim(f"hist:{m['id']}"):
                    continue
                rows.append({"tenant_id": tenant_id, "session_id": session["id"],
                             "role": "user" if _digits(m.get("from")) == customer else "agent",
                             "content": _text_of(m)[:4000], "created_at": _when(m.get("timestamp"))})
            if rows:
                import uuid
                _db().table("commerce_conversation_messages").insert(
                    [{"id": str(uuid.uuid4()), **r} for r in rows]).execute()
                took += len(rows)
    if took:
        log.info("coexistence: imported %d earlier message(s) for %s", took, tenant_id)
    return took


def _contacts(tenant_id: str, items: List[Dict[str, Any]]) -> int:
    """The app's contacts: name a chat that has no customer name yet. Nothing else is kept."""
    took = 0
    for it in items:
        c = it.get("contact") or {}
        phone, name = _digits(c.get("phone_number")), (c.get("full_name") or c.get("first_name") or "").strip()
        if it.get("type") != "contact" or it.get("action") == "remove" or not phone or not name:
            continue
        row = _session_row(tenant_id, phone)
        if row and not (row.get("customer_name") or "").strip():
            _db().table("commerce_conversation_sessions").update({"customer_name": name[:120]}) \
                .eq("id", row["id"]).execute()
            took += 1
    return took


async def owner_is_handling(tenant_id: str, phone: str, text: str) -> bool:
    """For lines that don't go through the commerce assistant (a knowledge line): a chat someone
    took over (from the Inbox, or by replying from the app) stays quiet — the customer's message
    is kept in the thread for them. True when Vula should not answer."""
    try:
        row = _session_row(tenant_id, _digits(phone))
        if not (row and row.get("paused")):
            return False
        from vula.commerce import service
        await service.append_message(tenant_id, row["id"], "user", text)
        return True
    except Exception as exc:
        log.debug("handoff check skipped: %s", exc)
        return False
