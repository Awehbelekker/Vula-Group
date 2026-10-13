"""
vula/api/shopify_connect.py — "Connect Shopify" in Settings, and Shopify's calls back to Vula.

Dashboard (under /v1/commerce/{tenant}/admin/…, so the tenant guard already applies; changing
the connection is for the owner or a manager):
    GET    /v1/commerce/{tenant}/admin/shopify             connection + last sync (no secrets)
    POST   /v1/commerce/{tenant}/admin/shopify/connect     {shop_domain, client_id+client_secret | access_token}
    POST   /v1/commerce/{tenant}/admin/shopify/sync        read the whole catalogue now
    DELETE /v1/commerce/{tenant}/admin/shopify             forget the store's credentials

Shopify (public, verified by the HMAC Shopify signs every delivery with the app's secret):
    POST   /v1/shopify/webhook/{tenant}
See vula/integrations/shopify.py for the why.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)
router = APIRouter(tags=["shopify"])            # mounted at /v1/commerce
webhook_router = APIRouter(tags=["shopify"])    # mounted at /v1/shopify


async def _owner_or_manager(request: Request, tenant_id: str) -> None:
    from vula.api.commerce import _may_apply_stock
    if not await _may_apply_stock(request, tenant_id):
        raise HTTPException(status_code=403, detail="Only the owner or a manager can change the Shopify connection.")


def _business(tenant_id: str) -> str:
    try:
        from vula.api.tenants import get_config
        return (get_config(tenant_id) or {}).get("display_name") or ""
    except Exception:
        return ""


@router.get("/{tenant_id}/admin/shopify")
async def shopify_status(tenant_id: str):
    from vula.commerce import site_import
    from vula.integrations import shopify
    try:
        view = shopify.public_view(shopify.get_account(tenant_id))
    except Exception:
        view = {"connected": False}
    view["sync"] = site_import.status(tenant_id, shopify.JOB)
    return view


@router.post("/{tenant_id}/admin/shopify/connect")
async def shopify_connect(tenant_id: str, body: dict, request: Request):
    from vula.commerce.background_tasks import run_background
    from vula.integrations import shopify
    await _owner_or_manager(request, tenant_id)
    body = body or {}
    try:
        view = await shopify.connect(
            tenant_id, body.get("shop_domain") or "",
            client_id=body.get("client_id") or "", client_secret=body.get("client_secret") or "",
            access_token_value=body.get("access_token") or "", connected_by=body.get("connected_by"))
    except shopify.ShopifyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    run_background(tenant_id, "shopify_sync", shopify.sync(tenant_id, _business(tenant_id)))
    return {**view, "syncing": True}


@router.post("/{tenant_id}/admin/shopify/sync")
async def shopify_sync_now(tenant_id: str, request: Request):
    from vula.commerce import site_import
    from vula.commerce.background_tasks import run_background
    from vula.integrations import shopify
    await _owner_or_manager(request, tenant_id)
    if not shopify.get_account(tenant_id):
        raise HTTPException(status_code=404, detail="No Shopify store is connected.")
    if site_import.status(tenant_id, shopify.JOB).get("running"):
        return {"started": False, "sync": site_import.status(tenant_id, shopify.JOB)}
    run_background(tenant_id, "shopify_sync", shopify.sync(tenant_id, _business(tenant_id)))
    return {"started": True}


@router.delete("/{tenant_id}/admin/shopify")
async def shopify_disconnect(tenant_id: str, request: Request):
    from vula.integrations import shopify
    await _owner_or_manager(request, tenant_id)
    if not shopify.get_account(tenant_id):
        return {"connected": False}
    shopify.disconnect(tenant_id)
    return {"connected": False, "note": "Vula has forgotten the store's credentials. Products stay; "
                                        "uninstall the app in Shopify to stop it completely."}


@webhook_router.post("/webhook/{tenant_id}")
async def shopify_webhook(tenant_id: str, request: Request):
    """Verify, answer 200 at once (Shopify wants a reply within seconds and retries otherwise),
    then do the work in the background."""
    from vula.commerce.background_tasks import run_background
    from vula.integrations import shopify
    raw = await request.body()
    try:
        acct = shopify.get_account(tenant_id)
    except Exception:
        acct = None
    if not acct or not shopify.verify_webhook(acct, raw, request.headers.get("x-shopify-hmac-sha256", "")):
        return JSONResponse({"detail": "invalid signature"}, status_code=401)
    shop = (request.headers.get("x-shopify-shop-domain") or "").lower()
    if shop and shop != acct.get("shop_domain"):
        return JSONResponse({"detail": "wrong shop"}, status_code=401)
    if not shopify.first_delivery(request.headers.get("x-shopify-webhook-id", "")):
        return {"ok": True, "duplicate": True}
    try:
        payload = json.loads(raw or b"{}")
    except ValueError:
        return {"ok": True, "ignored": "not json"}
    topic = request.headers.get("x-shopify-topic", "")
    run_background(tenant_id, f"shopify_webhook:{topic}", shopify.handle_webhook(tenant_id, topic, payload))
    return {"ok": True}
