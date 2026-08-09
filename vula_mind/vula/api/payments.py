"""
vula/api/payments.py — connect SA payment gateways + receive their webhooks.

    GET    /v1/payments/{tenant}/providers              connected + available gateways
    POST   /v1/payments/{tenant}/providers              connect / update a gateway
    POST   /v1/payments/{tenant}/providers/{p}/default   make it the default
    DELETE /v1/payments/{tenant}/providers/{p}
    POST   /v1/payments/webhook/{tenant}/{provider}      gateway payment notification
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel

from vula import payments

log = logging.getLogger(__name__)
router = APIRouter(tags=["payments"])


@router.get("/{tenant_id}/providers")
async def list_providers(tenant_id: str) -> dict:
    return {
        "connected": payments.list_providers(tenant_id),
        "available": [{"id": k, "label": payments.PROVIDER_LABELS[k], "fields": v}
                      for k, v in payments.PROVIDER_FIELDS.items()],
    }


class ConnectIn(BaseModel):
    provider: str
    credentials: dict = {}
    mode: str = "live"
    is_default: bool = False


@router.post("/{tenant_id}/providers")
async def connect_provider(tenant_id: str, body: ConnectIn) -> dict:
    try:
        row = payments.upsert_provider(tenant_id, body.provider, body.credentials, body.mode, body.is_default)
    except ValueError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        return {"error": f"{exc} (run migration 039?)"}
    return {"provider": row}


@router.post("/{tenant_id}/providers/{provider}/default")
async def make_default(tenant_id: str, provider: str) -> dict:
    payments.set_default(tenant_id, provider)
    return {"default": provider}


@router.delete("/{tenant_id}/providers/{provider}")
async def remove_provider(tenant_id: str, provider: str) -> dict:
    payments.delete_provider(tenant_id, provider)
    return {"removed": provider}


@router.post("/webhook/{tenant_id}/{provider}")
async def payment_webhook(tenant_id: str, provider: str, request: Request) -> dict:
    """Verify a gateway notification and mark the referenced invoice paid."""
    if provider == "yoco":
        # Yoco.verify_webhook() deliberately skips HMAC verification ("handled in the existing
        # yoco webhook" — see its docstring) because Yoco's Checkout API silently ignores the
        # notifyUrl this module passes in and always calls back to the account-wide URL
        # configured in the Yoco dashboard (/v1/yoco/webhook, which IS HMAC-verified). That
        # means this generic route was never reachable by real Yoco traffic — but it was still
        # live and unauthenticated, so a forged POST here with a guessed tenant/invoice id
        # could mark an invoice paid for free. Yoco must only ever be handled at /v1/yoco/webhook.
        return {"received": True}
    prov = payments.get_provider(provider)
    if not prov:
        return {"received": True}
    # Load this provider's creds (may differ from default).
    creds = {}
    try:
        rows = (payments._client().table("vula_payment_providers").select("credentials")
                .eq("tenant_id", tenant_id).eq("provider", provider).limit(1).execute().data or [])
        creds = payments._decrypt_creds(rows[0].get("credentials") if rows else {})
    except Exception:
        pass
    raw = await request.body()
    form = {}
    try:
        ct = request.headers.get("content-type", "")
        if "application/x-www-form-urlencoded" in ct or "multipart/form-data" in ct:
            form = dict(await request.form())
    except Exception:
        pass
    try:
        result = await prov.verify_webhook(creds, dict(request.headers), raw, form)
    except Exception as exc:
        log.warning("payment webhook verify failed (%s): %s", provider, exc)
        return {"received": True}
    if result and result.get("paid") and result.get("reference"):
        ref = result["reference"]
        from vula.commerce import service as cs
        db = cs._client()
        # 1. Invoice? (invoice pay-links use reference = invoice id)
        try:
            upd = (db.table("commerce_invoices")
                   .update({"status": "paid", "updated_at": cs._now()})
                   .eq("tenant_id", tenant_id).eq("id", ref).execute())
            if upd.data:
                log.info("Invoice %s paid via %s", ref, provider)
                return {"received": True}
        except Exception as exc:
            log.warning("invoice mark-paid failed: %s", exc)
        # 2. Order? (order pay-links use reference = display_id) — mark paid + trigger fulfilment.
        try:
            rows = (db.table("commerce_orders")
                    .select("id,display_id,customer_phone,customer_name,total_cents,status")
                    .eq("tenant_id", tenant_id).eq("display_id", ref).limit(1).execute().data or [])
            if rows:
                o = rows[0]
                if o.get("status") == "pending_payment":
                    db.table("commerce_orders").update(
                        {"status": "paid", "payment_method": "online", "updated_at": cs._now()}
                    ).eq("id", o["id"]).execute()
                    from vula.api.yoco import _notify_order_paid
                    await _notify_order_paid(
                        tenant_id, o["display_id"], o["id"], o.get("customer_phone"),
                        o.get("customer_name") or "", int(o.get("total_cents") or 0))
                log.info("Order %s paid via %s", ref, provider)
        except Exception as exc:
            log.warning("order mark-paid failed: %s", exc)
    return {"received": True}
