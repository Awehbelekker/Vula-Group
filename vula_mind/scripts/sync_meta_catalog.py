"""scripts/sync_meta_catalog.py — push a tenant's products into its connected Meta Commerce
Manager catalog, so customers can browse a real product catalog and build a cart natively inside
WhatsApp (see vula/api/whatsapp.py's _handle_native_order for the receiving side of this — that
side is live regardless of whether any tenant has connected a catalog yet).

Prerequisite (one-time, Meta-side, cannot be done by this script or by Claude): a Commerce
Manager catalog connected to the tenant's WhatsApp Business Account, with its ID recorded in
vula_tenant_config.meta_catalog_id (migration 147). Confirmed live, 2026-08-25: off-the-hook has
no catalog connected yet (GET /{phone_number_id}/whatsapp_commerce_settings returned empty) — this
script will report status "no_catalog_id" until that's done and the id is saved.

API reference (Meta Catalog Batch API, verified via developers.facebook.com, 2026-08-25):
    POST https://graph.facebook.com/{catalog_id}/items_batch
    {"item_type": "PRODUCT_ITEM", "requests": [{"method": "UPDATE"|"CREATE"|"DELETE", "data": {...}}]}
"UPDATE" is used here since it behaves as an upsert in Meta's own examples (creates when the id
doesn't exist yet) — worth confirming against the real response the first time this runs against
an actual catalog, since this hasn't been live-tested (there's nothing to test against yet).

Usage:
    railway run python scripts/sync_meta_catalog.py <tenant_id>
"""
from __future__ import annotations

import asyncio
import sys

import httpx

GRAPH_BASE = "https://graph.facebook.com/v19.0"


async def sync_catalog(tenant_id: str) -> dict:
    from vula.commerce import service
    from vula.api.whatsapp import _get_tenant_wa_creds

    c = service._client()
    try:
        cfg_rows = (c.table("vula_tenant_config").select("meta_catalog_id,store_url")
                    .eq("tenant_id", tenant_id).limit(1).execute().data or [])
    except Exception as exc:
        # migration 147 not run yet — report cleanly rather than a raw APIError. Confirmed live,
        # 2026-08-25: this is the actual current state for every tenant on the platform.
        return {"status": "no_catalog_id",
                "message": f"Couldn't read vula_tenant_config.meta_catalog_id (run migration "
                           f"147?): {exc}"}
    if not cfg_rows or not cfg_rows[0].get("meta_catalog_id"):
        return {"status": "no_catalog_id",
                "message": "vula_tenant_config.meta_catalog_id not set — connect a Commerce "
                           "Manager catalog to this tenant's WhatsApp number first (Meta-side, "
                           "one-time step), then save the catalog id."}
    catalog_id = cfg_rows[0]["meta_catalog_id"]
    store_url = cfg_rows[0].get("store_url")

    creds = await _get_tenant_wa_creds(tenant_id)
    if not creds:
        return {"status": "no_credentials", "message": "No connected WhatsApp credentials for this tenant."}

    products = (c.table("commerce_products").select("*")
                .eq("tenant_id", tenant_id).eq("archived", False).execute().data or [])
    if not products:
        return {"status": "no_products", "message": "No active (non-archived) products to sync."}

    requests = [_catalog_item(p, store_url) for p in products]

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{GRAPH_BASE}/{catalog_id}/items_batch",
            headers={"Authorization": f"Bearer {creds['token']}"},
            json={"item_type": "PRODUCT_ITEM", "requests": requests},
        )
    try:
        resp.raise_for_status()
        return {"status": "success", "synced": len(requests), "response": resp.json()}
    except Exception as exc:
        return {"status": "failed", "error": str(exc), "response_text": resp.text[:2000]}


def _catalog_item(product: dict, store_url: str | None) -> dict:
    """One product -> one Catalog Batch API request. `id` is the product's own slug (not a
    Meta-assigned id) — this is what comes back as product_retailer_id on a real order, letting
    _handle_native_order map it straight back to the real commerce_products row, no separate
    mapping table needed."""
    price_cents = product.get("sale_price_cents") or product.get("price_cents") or 0
    images = product.get("images") or []
    image_url = product.get("image_url") or (images[0] if images else None)
    in_stock = bool(product.get("in_stock")) and (product.get("stock_quantity") or 0) > 0

    data = {
        "id": product["slug"],
        "name": product["name"],
        "description": (product.get("description") or product["name"])[:9999],
        "price": f"{price_cents / 100:.2f} ZAR",
        "currency": "ZAR",
        "availability": "in stock" if in_stock else "out of stock",
    }
    if image_url:
        data["image"] = [{"url": image_url}]
    if store_url:
        data["url"] = f"{store_url.rstrip('/')}/products/{product['slug']}"
    return {"method": "UPDATE", "data": data}


async def _main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    result = await sync_catalog(sys.argv[1])
    print(result)


if __name__ == "__main__":
    asyncio.run(_main())
