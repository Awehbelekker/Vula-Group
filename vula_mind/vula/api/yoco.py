"""
vula/api/yoco.py — Yoco payment webhook handler.
Mounted at /v1/yoco in server.py.

Yoco POSTs here when a payment succeeds or fails.
We update the order status and fire the WhatsApp confirmation.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request

from config import settings
from vula.commerce import service as commerce

log = logging.getLogger(__name__)
router = APIRouter(tags=["yoco"])


@router.post("/webhook")
async def yoco_webhook(request: Request) -> dict:
    raw_body = await request.body()
    signature = request.headers.get("yoco-signature", "")

    # Verify HMAC-SHA256 signature
    if settings.yoco_webhook_secret:
        expected = hmac.new(
            settings.yoco_webhook_secret.encode(),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise HTTPException(status_code=401, detail="Invalid Yoco webhook signature")

    try:
        payload: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event_type = payload.get("type", "")
    data = payload.get("payload", payload)  # Yoco wraps in 'payload' key
    metadata = data.get("metadata", {})

    order_id = metadata.get("order_id")
    tenant_id = metadata.get("tenant_id")
    customer_phone = metadata.get("customer_phone")
    customer_name = metadata.get("customer_name", "")
    display_id = metadata.get("display_id", "")
    amount_cents = data.get("amount", 0)

    if not order_id:
        log.warning("Yoco webhook missing order_id in metadata")
        return {"received": True}

    if event_type in ("payment.succeeded", "checkout.completed"):
        await commerce.update_order_status(order_id, "paid")
        log.info("Order %s paid via Yoco", display_id)

        # Fire n8n workflow — sends WhatsApp confirmation + ops alert
        n8n_base = settings.n8n_webhook_base
        if n8n_base and customer_phone:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    await client.post(
                        f"{n8n_base}/yoco-payment-success",
                        json={
                            "order_id": order_id,
                            "display_id": display_id,
                            "tenant_id": tenant_id,
                            "customer_phone": customer_phone,
                            "customer_name": customer_name,
                            "amount_cents": amount_cents,
                            "amount_rands": f"R{amount_cents / 100:.2f}",
                        },
                    )
            except Exception as exc:
                log.warning("n8n notification failed (non-fatal): %s", exc)

    elif event_type in ("payment.failed", "checkout.expired"):
        await commerce.update_order_status(order_id, "cancelled")
        log.info("Order %s payment failed/expired", display_id)

    return {"received": True}
