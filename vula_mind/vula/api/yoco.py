"""
vula/api/yoco.py — Yoco payment webhook handler.
Mounted at /v1/yoco in server.py.

Yoco POSTs here when a payment succeeds or fails.
We update the order status and fire the WhatsApp confirmation.

Per-tenant credentials are pulled from Supabase (vula_yoco_accounts).
Falls back to global YOCO_WEBHOOK_SECRET env var for backwards compat.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request

from config import settings
from vula.commerce import service as commerce

log = logging.getLogger(__name__)
router = APIRouter(tags=["yoco"])


_yoco_creds_cache: dict[str, dict] = {}


async def _get_tenant_yoco_creds(tenant_id: str) -> Optional[dict]:
    """
    Get Yoco credentials for a tenant from Supabase, falling back to env vars.
    Returns: {'secret_key': str, 'webhook_secret': str, 'public_key': str, 'mode': str}
    """
    if tenant_id in _yoco_creds_cache:
        return _yoco_creds_cache[tenant_id]

    try:
        from supabase import create_client
        client = create_client(
            settings.supabase_url,
            settings.supabase_service_role_key or settings.supabase_service_key,
        )
        result = (
            client.table("vula_yoco_accounts")
            .select("secret_key,webhook_secret,public_key,mode,status")
            .eq("tenant_id", tenant_id)
            .eq("status", "connected")
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if rows and rows[0].get("secret_key"):
            creds = rows[0]
            _yoco_creds_cache[tenant_id] = creds
            return creds
    except Exception as exc:
        log.debug("Supabase Yoco creds lookup failed for %s: %s", tenant_id, exc)

    # Fallback to env vars
    if settings.yoco_secret_key:
        return {
            "secret_key": settings.yoco_secret_key,
            "webhook_secret": settings.yoco_webhook_secret,
            "public_key": settings.yoco_public_key,
            "mode": "live",
        }
    return None


@router.post("/webhook")
async def yoco_webhook(request: Request) -> dict:
    raw_body = await request.body()
    signature = request.headers.get("yoco-signature", "")

    # Try to identify the tenant from the payload metadata
    try:
        payload_preview: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    data = payload_preview.get("payload", payload_preview)
    metadata = data.get("metadata", {}) or {}
    tenant_id = metadata.get("tenant_id", "")

    # Verify HMAC using per-tenant webhook secret if we have it
    webhook_secret = None
    if tenant_id:
        creds = await _get_tenant_yoco_creds(tenant_id)
        if creds:
            webhook_secret = creds.get("webhook_secret")
    webhook_secret = webhook_secret or settings.yoco_webhook_secret

    if webhook_secret:
        expected = hmac.new(
            webhook_secret.encode(),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise HTTPException(status_code=401, detail="Invalid Yoco webhook signature")

    # payload_preview was already parsed above; reuse it
    payload = payload_preview
    event_type = payload.get("type", "")
    data = payload.get("payload", payload)  # Yoco wraps in 'payload' key
    metadata = data.get("metadata", {})

    order_id = metadata.get("order_id")
    # tenant_id already extracted above for webhook verification
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
