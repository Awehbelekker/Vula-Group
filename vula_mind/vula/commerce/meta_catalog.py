"""
vula/commerce/meta_catalog.py — a business's products in WhatsApp's own catalogue (Meta Commerce
Manager), so customers browse photos, pick a colour and build a cart inside WhatsApp. The order
comes back to vula/api/whatsapp.py::_handle_native_order, which seeds a real Vula cart from each
item's id (= the product's slug) and carries on to checkout and the payment link.

2026-10-13 (SeedleMe, 325 colour-products in ~94 ranges): each colour is pushed as its own item
sharing its range's item_group_id with a `color`, so WhatsApp shows one product with a colour
picker (vula/commerce/product_families.py does the grouping). Field names are the catalogue
feed's (title, link, image_link, additional_image_link, item_group_id, color) — Meta's Catalog
Batch API, POST /{catalog_id}/items_batch, item_type PRODUCT_ITEM.

Prerequisite, on Meta's side (cannot be done from code): a catalogue in Commerce Manager owned by
the business that owns the WhatsApp account, connected to the number (WhatsApp Manager ›
Catalogue), and the system user whose token Vula sends with given access to it. Its ID is saved
in vula_tenant_config.meta_catalog_id (migration 147) — Master › tenant › WhatsApp catalogue.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

log = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.facebook.com/v19.0"
BATCH = 1000                      # Meta allows 5,000 a request and suggests under 3,000


def catalog_items(products: List[Dict[str, Any]], link_base: str, brand: str = "") -> List[Dict[str, Any]]:
    """Every product → one items_batch request; colours of a range share an item_group_id."""
    from vula.commerce import product_families as pf
    out: List[Dict[str, Any]] = []
    for r in pf.group(products):
        colour_of = {c["product"]["slug"]: c["colour"] for c in r["colours"]}
        for p in r["members"]:
            out.append(_item(p, link_base, brand,
                             group=r["id"] if r["colours"] else None, colour=colour_of.get(p["slug"])))
    return out


def _item(p: Dict[str, Any], link_base: str, brand: str = "", group: Optional[str] = None,
          colour: Optional[str] = None) -> Dict[str, Any]:
    price = int(p.get("sale_price_cents") or p.get("price_cents") or 0)
    images = [u for u in ([p.get("image_url")] + list(p.get("images") or [])) if u]
    images = list(dict.fromkeys(images))
    stock = p.get("stock_quantity")
    # Untracked stock (None) is "available while the shop says so"; a counted 0 is out.
    available = bool(p.get("in_stock", True)) and (stock is None or stock > 0)
    data: Dict[str, Any] = {
        "id": p["slug"],
        "title": (p.get("name") or p["slug"])[:200],
        "description": ((p.get("description") or "").strip() or p.get("name") or p["slug"])[:9999],
        "availability": "in stock" if available else "out of stock",
        "condition": "new",
        "price": f"{price / 100:.2f} ZAR",
        "link": f"{link_base.rstrip('/')}/products/{p['slug']}",
    }
    if images:
        data["image_link"] = images[0]
    if len(images) > 1:
        data["additional_image_link"] = images[1:11]
    if brand:
        data["brand"] = brand[:100]
    if group:
        data["item_group_id"] = group
    if colour:
        data["color"] = colour
    return {"method": "UPDATE", "data": data}


def _config(tenant_id: str) -> Dict[str, Any]:
    from vula.commerce import service
    rows = (service._client().table("vula_tenant_config")
            .select("meta_catalog_id,store_url,display_name").eq("tenant_id", tenant_id)
            .limit(1).execute().data or [])
    return rows[0] if rows else {}


async def sync(tenant_id: str, catalog_id: Optional[str] = None) -> Dict[str, Any]:
    """Push every active product to the tenant's WhatsApp catalogue. Reports, never raises."""
    from config import settings
    from vula.api.whatsapp import _get_tenant_wa_creds
    from vula.commerce import service
    try:
        cfg = _config(tenant_id)
    except Exception as exc:
        return {"status": "failed", "message": f"Couldn't read the tenant's settings: {exc}"}
    catalog_id = (catalog_id or cfg.get("meta_catalog_id") or "").strip()
    if not catalog_id:
        return {"status": "no_catalog_id", "message": "No WhatsApp catalogue is connected for this business yet."}
    creds = await _get_tenant_wa_creds(tenant_id)
    if not creds or not creds.get("token"):
        return {"status": "no_credentials", "message": "No WhatsApp connection for this business."}
    products = await service.list_products(tenant_id, in_stock_only=False, statuses={"active"})
    if not products:
        return {"status": "no_products", "message": "No active products to put in the catalogue."}
    link_base = cfg.get("store_url") or f"{settings.public_base_url.rstrip('/')}/menu/{tenant_id}"
    requests = catalog_items(products, link_base, cfg.get("display_name") or "")
    handles, errors = [], []
    async with httpx.AsyncClient(timeout=60.0) as client:
        for i in range(0, len(requests), BATCH):
            resp = await client.post(f"{GRAPH_BASE}/{catalog_id}/items_batch",
                                     headers={"Authorization": f"Bearer {creds['token']}"},
                                     json={"item_type": "PRODUCT_ITEM", "requests": requests[i:i + BATCH]})
            body = {}
            try:
                body = resp.json()
            except Exception:
                pass
            if resp.status_code >= 400:
                msg = ((body.get("error") or {}).get("message") if isinstance(body, dict) else None) or resp.text[:300]
                errors.append(msg)
                break
            handles += body.get("handles") or []
    if errors:
        hint = (" — the token Vula uses needs access to this catalogue (Business settings › System "
                "users › assign the catalogue)" if any("permission" in e.lower() for e in errors) else "")
        return {"status": "failed", "error": errors[0] + hint}
    groups = len({r["data"].get("item_group_id") for r in requests if r["data"].get("item_group_id")})
    log.info("meta catalogue %s → %s: %d items, %d colour groups", tenant_id, catalog_id, len(requests), groups)
    return {"status": "success", "synced": len(requests), "colour_groups": groups,
            "catalog_id": catalog_id, "handles": handles}


async def sync_if_connected(tenant_id: str) -> None:
    """After an import or a Shopify sync: keep the WhatsApp catalogue in step, if there is one."""
    try:
        if (_config(tenant_id).get("meta_catalog_id") or "").strip():
            res = await sync(tenant_id)
            if res.get("status") != "success":
                log.warning("meta catalogue resync for %s: %s", tenant_id, res.get("error") or res.get("message"))
    except Exception as exc:
        log.debug("meta catalogue resync skipped for %s: %s", tenant_id, exc)
