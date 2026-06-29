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

# Per-tenant team phones — receive order alerts when a payment lands.
# Format: tenant_id → list of (name, phone_e164, role)
_TENANT_TEAM: dict[str, list[tuple[str, str, str]]] = {
    "off-the-hook": [
        ("Stacy", "27722684085", "owner"),
        ("Roland", "27721822828", "operations"),
    ],
}


def _tenant_team(tenant_id: str) -> list[tuple[str, str, str]]:
    """Who gets order alerts — DB-driven (vula_team_members) so a new tenant's team
    needs no code change; falls back to the static map. Owners/managers/operations
    and anyone who opted into an order notify event."""
    try:
        from vula.commerce import service as cs
        rows = (cs._client().table("vula_team_members")
                .select("name,whatsapp,role,notify,active")
                .eq("tenant_id", tenant_id).eq("active", True).execute().data or [])
        team = []
        for r in rows:
            phone = (r.get("whatsapp") or "").strip()
            if not phone:
                continue
            notify = r.get("notify") or []
            if r.get("role") in ("owner", "manager", "operations") or \
               any(n in notify for n in ("order_paid", "new_order", "orders")):
                team.append((r.get("name") or "", phone, r.get("role") or "staff"))
        if team:
            return team
    except Exception as exc:
        log.debug("team DB lookup skipped: %s", exc)
    return _TENANT_TEAM.get(tenant_id, [])

# Per-tenant WhatsApp Business phone number IDs (Meta phone number ID)
_TENANT_PHONE_IDS: dict[str, str] = {
    "off-the-hook": "1124076000792176",  # +27 67 363 6081 (system-user WABA)
}


async def _notify_order_paid(
    tenant_id: str,
    display_id: str,
    order_id: str,
    customer_phone: Optional[str],
    customer_name: str,
    amount_cents: int,
) -> None:
    """Send WhatsApp order confirmation to customer and alert the team."""
    if not settings.whatsapp_token:
        return

    phone_id = _TENANT_PHONE_IDS.get(tenant_id) or settings.whatsapp_phone_id
    amount = f"R{amount_cents / 100:.2f}"

    async with httpx.AsyncClient(timeout=10.0) as client:
        headers = {
            "Authorization": f"Bearer {settings.whatsapp_token}",
            "Content-Type": "application/json",
        }

        async def _send(to: str, body: str) -> None:
            number = to.lstrip("+").replace(" ", "").replace("-", "")
            if number.startswith("0"):
                number = "27" + number[1:]
            try:
                await client.post(
                    f"https://graph.facebook.com/v19.0/{phone_id}/messages",
                    headers=headers,
                    json={
                        "messaging_product": "whatsapp",
                        "to": number,
                        "type": "text",
                        "text": {"body": body[:4096]},
                    },
                )
            except Exception as exc:
                log.warning("WhatsApp notify failed to %s: %s", to, exc)

        # Customer confirmation
        if customer_phone:
            from vula.api import tenants as _tenants
            store_url = _tenants.store_url(tenant_id) or "offthehook.co.za"
            await _send(
                customer_phone,
                f"Hi {customer_name or 'there'}! Your Off the Hook order *{display_id}* "
                f"is confirmed ({amount}). We'll be in touch with your delivery time. "
                f"Track at {store_url} or reply here with any questions. "
                f"Thank you!"
            )

        # Team alerts
        for name, phone, role in _tenant_team(tenant_id):
            try:
                order = await commerce.get_order(order_id)
                items = order.get("commerce_order_items") or []
                item_lines = "\n".join(
                    f"  • {i.get('product_name','?')} x{i.get('quantity',1)}"
                    for i in items[:8]
                ) or "  (items not loaded)"
            except Exception:
                item_lines = "  (see dashboard)"

            await _send(
                phone,
                f"New order {display_id} — {amount}\n"
                f"Customer: {customer_name or 'Unknown'} ({customer_phone or 'no phone'})\n"
                f"{item_lines}\n\n"
                f"Vula dashboard → Off the Hook → Orders"
            )


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

    # Invoice pay-links carry invoice_id (not order_id) → mark the invoice paid.
    invoice_id = metadata.get("invoice_id")
    if invoice_id and event_type in ("payment.succeeded", "checkout.completed"):
        try:
            from vula.commerce import service as _svc
            _svc._client().table("commerce_invoices").update(
                {"status": "paid", "updated_at": _svc._now()}
            ).eq("id", invoice_id).eq("tenant_id", tenant_id).execute()
            log.info("Invoice %s paid via Yoco", metadata.get("invoice_number", invoice_id))
        except Exception as exc:
            log.warning("invoice mark-paid failed: %s", exc)
        return {"received": True}

    if not order_id:
        log.warning("Yoco webhook missing order_id in metadata")
        return {"received": True}

    if event_type in ("payment.succeeded", "checkout.completed"):
        await commerce.update_order_status(order_id, "paid")
        log.info("Order %s paid via Yoco", display_id)

        # Send WhatsApp confirmation to customer + team alerts
        await _notify_order_paid(
            tenant_id=tenant_id,
            display_id=display_id,
            order_id=order_id,
            customer_phone=customer_phone,
            customer_name=customer_name,
            amount_cents=amount_cents,
        )

        # Also fire n8n if configured (for advanced automation)
        n8n_base = settings.n8n_webhook_base
        if n8n_base:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    await client.post(
                        f"{n8n_base}/yoco-payment-success",
                        json={
                            "order_id": order_id, "display_id": display_id,
                            "tenant_id": tenant_id, "customer_phone": customer_phone,
                            "customer_name": customer_name, "amount_cents": amount_cents,
                        },
                    )
            except Exception as exc:
                log.warning("n8n notification failed (non-fatal): %s", exc)

    elif event_type in ("payment.failed", "checkout.expired"):
        await commerce.update_order_status(order_id, "cancelled")
        log.info("Order %s payment failed/expired", display_id)

    return {"received": True}
