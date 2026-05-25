"""
vula/api/whatsapp.py

Vula WhatsApp inbound webhook.

Meta sends inbound messages here via POST.
GET is the verification handshake Meta calls once when you register the webhook.

Flow:
  Client sends WhatsApp → Meta Graph API → POST /v1/whatsapp/webhook
  → extract phone + message text
  → look up tenant by phone number in Supabase
  → run RAG query against tenant knowledge base
  → reply via Graph API send message

When no tenant is found for a number, we reply with a helpful fallback.
When the RAG pipeline is unavailable, we reply gracefully.
"""
from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, HTTPException, Query, Request

from config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["whatsapp"])


# ─── Meta verification handshake ─────────────────────────────────────────────

@router.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(alias="hub.mode", default=""),
    hub_verify_token: str = Query(alias="hub.verify_token", default=""),
    hub_challenge: str = Query(alias="hub.challenge", default=""),
) -> int:
    """Meta calls this once to verify your webhook URL.

    In Meta App Dashboard → WhatsApp → Configuration:
    - Callback URL: https://app.vula.ai/v1/whatsapp/webhook
    - Verify Token: must match WHATSAPP_VERIFY_TOKEN in .env
    """
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        logger.info("WhatsApp webhook verified")
        return int(hub_challenge)
    raise HTTPException(status_code=403, detail="Webhook verification failed")


# ─── Inbound message handler ─────────────────────────────────────────────────

@router.post("/webhook")
async def receive_message(request: Request) -> dict:
    """Receive inbound WhatsApp messages from Meta.

    Meta sends a JSON payload every time a user messages your WhatsApp number.
    We extract the sender's phone number and message text, look up their
    tenant, run RAG, and reply.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Meta wraps everything in entry[].changes[].value
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            messages = value.get("messages", [])
            for msg in messages:
                if msg.get("type") != "text":
                    continue  # ignore non-text (voice, image, etc.)
                phone = msg.get("from", "")
                text = msg.get("text", {}).get("body", "").strip()
                msg_id = msg.get("id", "")
                if phone and text:
                    await _handle_message(phone, text, msg_id)

    return {"status": "ok"}


async def _handle_message(phone: str, text: str, msg_id: str) -> None:
    """Route an inbound message to the correct tenant's RAG pipeline."""
    logger.info("WhatsApp inbound from %s: %s", phone, text[:80])

    tenant_id = await _tenant_for_phone(phone)
    if tenant_id:
        reply = await _rag_reply(tenant_id, text)
    else:
        reply = (
            "Hi! I'm Vula, your business AI assistant. "
            "I couldn't find an account linked to this number. "
            "Contact your Vula representative to get set up."
        )

    await _send_reply(phone, reply)


async def _tenant_for_phone(phone: str) -> str | None:
    """Look up a tenant by their WhatsApp number.

    Normalises the number (strips leading + or country code prefix differences)
    and queries Supabase. Returns None when Supabase is not configured or
    the number is not registered.
    """
    if not settings.supabase_url or not settings.supabase_service_key:
        return None

    # Normalise: strip leading +, spaces, dashes
    normalised = phone.lstrip("+").replace(" ", "").replace("-", "")
    # SA numbers: both 27821234567 and 0821234567 should match
    variants = {normalised}
    if normalised.startswith("27") and len(normalised) == 11:
        variants.add("0" + normalised[2:])
    elif normalised.startswith("0") and len(normalised) == 10:
        variants.add("27" + normalised[1:])

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            for variant in variants:
                resp = await client.get(
                    f"{settings.supabase_url}/rest/v1/vula_tenants"
                    f"?whatsapp=eq.{variant}&select=tenant_id&limit=1",
                    headers={
                        "apikey": settings.supabase_service_key,
                        "Authorization": f"Bearer {settings.supabase_service_key}",
                    },
                )
                rows = resp.json() if resp.is_success else []
                if rows:
                    return rows[0]["tenant_id"]
    except Exception as exc:
        logger.error("Tenant lookup failed for %s: %s", phone, exc)

    return None


async def _rag_reply(tenant_id: str, question: str) -> str:
    """Run the question through the tenant's RAG pipeline and return a reply."""
    try:
        from vula.ingestion.pipeline import VulaIngestionPipeline
        pipeline = VulaIngestionPipeline(tenant_id=tenant_id)
        sources = await pipeline.query(question, top_k=5)
        if not sources:
            return (
                "I don't have enough information in your documents to answer that yet. "
                "Try uploading more files at app.vula.ai or ask your Vula rep."
            )
        answer = await pipeline.answer(question)
        return answer
    except Exception as exc:
        logger.error("RAG pipeline error for tenant %s: %s", tenant_id, exc)
        return "I'm having trouble accessing your knowledge base right now. Please try again in a few minutes."


async def _send_reply(to: str, message: str) -> bool:
    """Send a WhatsApp text message via the Meta Graph API."""
    if not settings.whatsapp_token or not settings.whatsapp_phone_id:
        logger.info("WhatsApp not configured — skipping reply to %s", to)
        return False

    # Normalise to E.164 without leading +
    number = to.lstrip("+")
    if number.startswith("0"):
        number = "27" + number[1:]

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{settings.whatsapp_api_url}/{settings.whatsapp_phone_id}/messages",
                headers={
                    "Authorization": f"Bearer {settings.whatsapp_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "messaging_product": "whatsapp",
                    "to": number,
                    "type": "text",
                    "text": {"body": message[:4096]},  # WhatsApp 4096 char limit
                },
            )
            resp.raise_for_status()
            logger.info("WhatsApp reply sent to %s", to)
            return True
    except Exception as exc:
        logger.error("WhatsApp reply failed to %s: %s", to, exc)
        return False
