"""
vula/api/twilio_whatsapp.py

Twilio WhatsApp adapter — an alternative to the Meta Graph API path.

Why this exists:
  Meta's direct WhatsApp Business API requires app review + has aggressive
  rate limits during setup. Twilio is a BSP (Business Solution Provider) that
  sits in front of Meta and lets you test the FULL flow immediately via their
  WhatsApp Sandbox — no Meta app review, no setup rate limits.

How it works:
  - Twilio sends inbound messages as form-encoded POST (not JSON like Meta)
  - We extract the same (phone, text) and route to the SAME _handle_message brain
  - Outbound replies go via Twilio's Messages API

Setup (5 minutes):
  1. Create a free account at twilio.com
  2. Console → Messaging → Try it out → Send a WhatsApp message
  3. Join the sandbox: send "join <your-code>" to the Twilio number from your phone
  4. Set the sandbox webhook to: {RAILWAY_URL}/v1/twilio/webhook
  5. Set these env vars:
       TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM (e.g. whatsapp:+14155238886)

Env vars:
  TWILIO_ACCOUNT_SID   — from Twilio console
  TWILIO_AUTH_TOKEN    — from Twilio console
  TWILIO_WHATSAPP_FROM — the sandbox/sender number, e.g. "whatsapp:+14155238886"
"""
from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Form, Request
from fastapi.responses import PlainTextResponse

from config import settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["twilio"])


def _twilio_configured() -> bool:
    return bool(
        getattr(settings, "twilio_account_sid", "")
        and getattr(settings, "twilio_auth_token", "")
        and getattr(settings, "twilio_whatsapp_from", "")
    )


@router.post("/webhook")
async def twilio_inbound(
    request: Request,
    From: str = Form(default=""),
    Body: str = Form(default=""),
    MessageSid: str = Form(default=""),
    NumMedia: str = Form(default="0"),
):
    """Inbound Twilio WhatsApp webhook.

    Twilio sends form-encoded data. 'From' looks like 'whatsapp:+27827077080'.
    We strip the prefix and route to the same handler the Meta path uses.
    """
    # Strip the 'whatsapp:' prefix and leading +
    phone = From.replace("whatsapp:", "").lstrip("+").strip()
    text = (Body or "").strip()

    logger.info("Twilio inbound from %s: %s", phone, text[:80])

    if not phone or not text:
        return PlainTextResponse("", status_code=200)

    # Route to the shared brain. We patch _send_reply to use Twilio for this turn.
    from vula.api import whatsapp as wa

    # Monkeypatch-free approach: temporarily set a context flag so _send_reply
    # knows to use Twilio. Simpler: call the handler, but override the sender.
    original_send = wa._send_reply

    async def _twilio_send(to: str, message: str, tenant_id: str = "") -> bool:
        return await send_twilio_reply(to, message)

    wa._send_reply = _twilio_send
    try:
        await wa._handle_message(phone, text, MessageSid)
    except Exception as exc:
        logger.error("Twilio message handling failed: %s", exc)
        await send_twilio_reply(phone, "Sorry, something went wrong. Please try again.")
    finally:
        wa._send_reply = original_send

    # Twilio expects an empty TwiML response (we send async via API)
    return PlainTextResponse("", status_code=200)


async def send_twilio_reply(to: str, message: str) -> bool:
    """Send a WhatsApp reply via the Twilio Messages API."""
    if not _twilio_configured():
        logger.info("Twilio not configured — skipping reply to %s", to)
        return False

    # Normalise number to whatsapp:+E.164
    number = to.lstrip("+").replace(" ", "").replace("-", "")
    if number.startswith("0"):
        number = "27" + number[1:]
    to_addr = f"whatsapp:+{number}"

    sid = settings.twilio_account_sid
    token = settings.twilio_auth_token
    from_addr = settings.twilio_whatsapp_from
    if not from_addr.startswith("whatsapp:"):
        from_addr = f"whatsapp:{from_addr}"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                auth=(sid, token),
                data={
                    "From": from_addr,
                    "To": to_addr,
                    "Body": message[:1600],  # Twilio limit per message segment
                },
            )
            resp.raise_for_status()
            logger.info("Twilio reply sent to %s", to)
            return True
    except Exception as exc:
        logger.error("Twilio reply failed to %s: %s", to, exc)
        return False


@router.get("/status")
async def twilio_status():
    """Check whether Twilio WhatsApp is configured."""
    return {
        "configured": _twilio_configured(),
        "from": getattr(settings, "twilio_whatsapp_from", "") or "(not set)",
    }
