"""
vula/api/chat.py

Conversational chat API for the client portal (vula_mobile's thin-client staff app — the
dashboard has no caller for this today).
Uses the same RAG pipeline as WhatsApp but adds persistent message history
so the AI remembers context across a session.

Endpoints:
    POST /v1/chat/{tenant_id}/message  — send a message, get a reply
    GET  /v1/chat/{tenant_id}/history  — retrieve recent conversation
    DELETE /v1/chat/{tenant_id}/history — clear conversation

2026-09-15: all three had NO auth dependency at all, and weren't matched by server.py's
tenant_admin_guard middleware either (that regex only covers /v1/commerce/{id}/admin,
/v1/team/{id}, /v1/users/{id}) — any caller who knew a tenant_id (a readable slug like
"off-the-hook", not a random one) could read, inject into, or wipe that tenant's entire
conversation history with zero auth. vula_mobile/src/api/vula.js — the one real caller —
already sends X-API-Key on every one of these calls, so require_auth (moved to
vula/api/master_auth.py to avoid a circular import with server.py) closes this with no
frontend change: parity with every sibling legacy endpoint (/query, /ingest, /scrape/*),
not a new auth model.
"""
from __future__ import annotations

import logging

import secrets
from typing import Tuple

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from vula.api.master_auth import require_auth
from vula.chat.history import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


class ChatMessageRequest(BaseModel):
    message: str
    phone: str = ""   # optional — identifies a specific WhatsApp thread


async def _thread_for(request: Request, tenant_id: str, phone: str) -> Tuple[str, bool]:
    """(thread key, may this caller search the thread owner's private documents).

    13 Nov 2026 audit: the routes took `phone` from the request, so any team member could name a
    colleague's number, be answered from that colleague's private documents and read or delete
    their thread. Now:
      * the server API key (the staff app) may name a phone: it acts for that person;
      * a signed-in member gets their own thread only, and naming another number is refused;
      * any other login that passed require_auth (a master) may read or clear a thread, but
        never searches as another person.
    """
    from config import settings
    from vula.team_index import digits_za
    key = request.headers.get("x-api-key") or ""
    if settings.api_key and key and secrets.compare_digest(key, settings.api_key):
        return phone, bool(phone)
    from vula.identity import person_for_login
    person = await person_for_login(request.headers.get("authorization", ""), tenant_id)
    if person is not None:
        own = person.phone or ""
        if phone and digits_za(phone) != digits_za(own):
            raise HTTPException(status_code=403, detail="You can only use your own conversation.")
        if own:
            return own, True
        if not person.web_session_key:
            raise HTTPException(status_code=403, detail="Sign in again to use the chat.")
        return person.web_session_key, False
    return phone, False


class ChatMessageResponse(BaseModel):
    reply: str
    tenant_id: str
    message_saved: bool


@router.post("/chat/{tenant_id}/message", response_model=ChatMessageResponse,
             dependencies=[Depends(require_auth)])
async def send_message(tenant_id: str, body: ChatMessageRequest, request: Request) -> ChatMessageResponse:
    """Send a chat message and receive an AI reply with conversation memory."""
    if not body.message.strip():
        raise HTTPException(status_code=422, detail="message cannot be empty")

    db = get_db()
    phone, own_private = await _thread_for(request, tenant_id, body.phone or "")

    # Save user message
    db.save(tenant_id, phone, "user", body.message)

    # Who is this? This endpoint serves vula_mobile's STAFF app, so a caller we can put a name
    # to is a member of the business, not a client of it — without this the history below
    # labelled every one of their turns "Client:" and the skills were told nothing (see
    # core/skills/base.py::caller_block for the DIGG incident). Needs the optional `phone` to
    # match them against vula_team_members; with none supplied we stay on the old generic label.
    from vula.api.whatsapp import _caller_identity, _is_insider
    caller_name, caller_role = _caller_identity(tenant_id, phone) if own_private else (None, None)
    user_label = (f"{caller_name} ({caller_role})" if caller_name else str(caller_role)) \
        if _is_insider(caller_role) else "Client"

    # Build conversation history for prompt context
    history = db.format_for_prompt(tenant_id, phone, limit=12, user_label=user_label)

    # Project-aware: inject the referenced project's context (codes, team, client).
    try:
        from vula.integrations.project_context import project_context_block
        pc = project_context_block(tenant_id, body.message)
        if pc:
            history = f"{pc}\n\n{history}" if history else pc
    except Exception:
        pass

    # Get RAG reply (same logic as WhatsApp, reused here)
    try:
        from vula.api.whatsapp import _rag_reply
        from vula.ingestion.pipeline import kb_viewer
        from vula.private_docs import member_id_for_phone
        viewer = member_id_for_phone(tenant_id, phone) if own_private else None
        with kb_viewer(viewer):    # the caller's own private documents, never someone else's
            reply = await _rag_reply(tenant_id, body.message, conversation_history=history,
                                     caller_name=caller_name, caller_role=caller_role)
    except Exception as exc:
        logger.error("Chat RAG error for tenant %s: %s", tenant_id, exc)
        reply = "I'm having trouble right now. Please try again in a moment."

    # Save AI reply
    db.save(tenant_id, phone, "assistant", reply)

    return ChatMessageResponse(reply=reply, tenant_id=tenant_id, message_saved=True)


@router.get("/chat/{tenant_id}/history", dependencies=[Depends(require_auth)])
async def get_history(tenant_id: str, request: Request, phone: str = "", limit: int = 30) -> dict:
    """Return recent conversation history: the caller's own thread for a signed-in member."""
    phone, _ = await _thread_for(request, tenant_id, phone)
    db = get_db()
    msgs = db.get(tenant_id, phone=phone, limit=min(limit, 100))
    return {
        "tenant_id": tenant_id,
        "phone": phone,
        "messages": [
            {"role": m.role, "text": m.text, "created_at": m.created_at}
            for m in msgs
        ],
        "count": len(msgs),
    }


@router.delete("/chat/{tenant_id}/history", dependencies=[Depends(require_auth)])
async def clear_history(tenant_id: str, request: Request, phone: str = "") -> dict:
    """Clear a conversation thread: the caller's own thread for a signed-in member."""
    phone, _ = await _thread_for(request, tenant_id, phone)
    db = get_db()
    deleted = db.clear(tenant_id, phone=phone)
    return {"tenant_id": tenant_id, "deleted": deleted, "status": "cleared"}
