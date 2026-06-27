"""
vula/api/chat.py

Conversational chat API for the client portal.
Uses the same RAG pipeline as WhatsApp but adds persistent message history
so the AI remembers context across a session.

Endpoints:
    POST /v1/chat/{tenant_id}/message  — send a message, get a reply
    GET  /v1/chat/{tenant_id}/history  — retrieve recent conversation
    DELETE /v1/chat/{tenant_id}/history — clear conversation
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from vula.chat.history import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


class ChatMessageRequest(BaseModel):
    message: str
    phone: str = ""   # optional — identifies a specific WhatsApp thread


class ChatMessageResponse(BaseModel):
    reply: str
    tenant_id: str
    message_saved: bool


@router.post("/chat/{tenant_id}/message", response_model=ChatMessageResponse)
async def send_message(tenant_id: str, body: ChatMessageRequest) -> ChatMessageResponse:
    """Send a chat message and receive an AI reply with conversation memory."""
    if not body.message.strip():
        raise HTTPException(status_code=422, detail="message cannot be empty")

    db = get_db()
    phone = body.phone or ""

    # Save user message
    db.save(tenant_id, phone, "user", body.message)

    # Build conversation history for prompt context
    history = db.format_for_prompt(tenant_id, phone, limit=12)

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
        reply = await _rag_reply(tenant_id, body.message, conversation_history=history)
    except Exception as exc:
        logger.error("Chat RAG error for tenant %s: %s", tenant_id, exc)
        reply = "I'm having trouble right now. Please try again in a moment."

    # Save AI reply
    db.save(tenant_id, phone, "assistant", reply)

    return ChatMessageResponse(reply=reply, tenant_id=tenant_id, message_saved=True)


@router.get("/chat/{tenant_id}/history")
async def get_history(tenant_id: str, phone: str = "", limit: int = 30) -> dict:
    """Return recent conversation history for a tenant."""
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


@router.delete("/chat/{tenant_id}/history")
async def clear_history(tenant_id: str, phone: str = "") -> dict:
    """Clear conversation history for a tenant (or a specific phone thread)."""
    db = get_db()
    deleted = db.clear(tenant_id, phone=phone)
    return {"tenant_id": tenant_id, "deleted": deleted, "status": "cleared"}
