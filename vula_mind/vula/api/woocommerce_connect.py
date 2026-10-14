"""
vula/api/woocommerce_connect.py — "Connect WooCommerce" in Settings, and WooCommerce's calls back.

Dashboard (under /v1/commerce/{tenant}/admin/…, so the tenant guard already applies; changing
the connection is for the owner or a manager):
    GET    /v1/commerce/{tenant}/admin/woocommerce            connection + last sync (no keys)
    POST   /v1/commerce/{tenant}/admin/woocommerce/connect    {store_url, consumer_key, consumer_secret}
    POST   /v1/commerce/{tenant}/admin/woocommerce/sync       read the whole catalogue now
    DELETE /v1/commerce/{tenant}/admin/woocommerce            forget the keys (and Vula's webhooks)

WooCommerce (public; every delivery signed with the secret Vula gave it):
    POST   /v1/woocommerce/webhook/{tenant}
See vula/integrations/woocommerce.py for the why.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)
router = APIRouter(tags=["woocommerce"])            # mounted at /v1/commerce
webhook_router = APIRouter(tags=["woocommerce"])    # mounted at /v1/woocommerce


async def _owner_or_manager(request: Request, tenant_id: str) -> None:
    from vula.api.commerce import _may_apply_stock
    if not await _may_apply_stock(request, tenant_id):
        raise HTTPException(status_code=403, detail="Only the owner or a manager can change the WooCommerce connection.")


def _business(tenant_id: str) -> str:
    try:
        from vula.api.tenants import get_config
        return (get_config(tenant_id) or {}).get("display_name") or ""
    except Exception:
        return ""


@router.get("/{tenant_id}/admin/woocommerce")
async def woo_status(tenant_id: str):
    from vula.commerce import site_import
    from vula.integrations import woocommerce
    try:
        view = woocommerce.public_view(woocommerce.get_account(tenant_id))
    except Exception:
        view = {"connected": False}
    view["sync"] = site_import.status(tenant_id, woocommerce.JOB)
    return view


@router.post("/{tenant_id}/admin/woocommerce/connect")
async def woo_connect(tenant_id: str, body: dict, request: Request):
    from vula.commerce.background_tasks import run_background
    from vula.integrations import woocommerce
    await _owner_or_manager(request, tenant_id)
    body = body or {}
    try:
        view = await woocommerce.connect(tenant_id, body.get("store_url") or "", body.get("consumer_key") or "",
                                         body.get("consumer_secret") or "", connected_by=body.get("connected_by"))
    except woocommerce.WooError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    run_background(tenant_id, "woocommerce_sync", woocommerce.sync(tenant_id, _business(tenant_id)))
    return {**view, "syncing": True}


@router.post("/{tenant_id}/admin/woocommerce/sync")
async def woo_sync_now(tenant_id: str, request: Request):
    from vula.commerce import site_import
    from vula.commerce.background_tasks import run_background
    from vula.integrations import woocommerce
    await _owner_or_manager(request, tenant_id)
    if not woocommerce.get_account(tenant_id):
        raise HTTPException(status_code=404, detail="No WooCommerce store is connected.")
    if site_import.status(tenant_id, woocommerce.JOB).get("running"):
        return {"started": False, "sync": site_import.status(tenant_id, woocommerce.JOB)}
    run_background(tenant_id, "woocommerce_sync", woocommerce.sync(tenant_id, _business(tenant_id)))
    return {"started": True}


@router.delete("/{tenant_id}/admin/woocommerce")
async def woo_disconnect(tenant_id: str, request: Request):
    from vula.integrations import woocommerce
    await _owner_or_manager(request, tenant_id)
    if not woocommerce.get_account(tenant_id):
        return {"connected": False}
    await woocommerce.disconnect(tenant_id)
    return {"connected": False, "note": "Vula has forgotten the keys. Products stay; delete the "
                                        "“Vula” key in WooCommerce › Settings › Advanced › REST API."}


@webhook_router.post("/webhook/{tenant_id}")
async def woo_webhook(tenant_id: str, request: Request):
    """WooCommerce first sends an unsigned ping ("webhook_id=…") when a webhook is created and
    only keeps the webhook if it gets a 2xx — answered and ignored. Every real delivery is
    verified, answered at once, and handled in the background."""
    from vula.commerce.background_tasks import run_background
    from vula.integrations import woocommerce
    raw = await request.body()
    if raw.startswith(b"webhook_id="):
        return {"ok": True, "ping": True}
    try:
        acct = woocommerce.get_account(tenant_id)
    except Exception:
        acct = None
    if not acct or not woocommerce.verify_webhook(acct, raw, request.headers.get("x-wc-webhook-signature", "")):
        return JSONResponse({"detail": "invalid signature"}, status_code=401)
    from vula.integrations import shopify
    if not shopify.first_delivery("woo:" + request.headers.get("x-wc-webhook-delivery-id", "")):
        return {"ok": True, "duplicate": True}
    try:
        payload = json.loads(raw or b"{}")
    except ValueError:
        return {"ok": True, "ignored": "not json"}
    topic = request.headers.get("x-wc-webhook-topic", "")
    run_background(tenant_id, f"woocommerce_webhook:{topic}", woocommerce.handle_webhook(tenant_id, topic, payload))
    return {"ok": True}
