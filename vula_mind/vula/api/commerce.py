"""
vula/api/commerce.py — Vula Commerce API routes.

Mounted at /v1/commerce in server.py.

Endpoints:
    GET  /v1/commerce/{tenant_id}/products                — list products
    GET  /v1/commerce/{tenant_id}/products/{slug}         — product detail
    GET  /v1/commerce/{tenant_id}/cart/{session_id}       — get/create cart
    POST /v1/commerce/{tenant_id}/cart/{session_id}/add   — add item
    DELETE /v1/commerce/{tenant_id}/cart/{session_id}/{item_id} — remove item
    POST /v1/commerce/{tenant_id}/checkout                — create order + Yoco checkout
    GET  /v1/commerce/{tenant_id}/orders/{order_id}       — order detail
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from config import settings
from vula.commerce import service
from vula.commerce.models import (
    AddToCartRequest,
    DeliverySlot,
    InvoiceCreate,
)

log = logging.getLogger(__name__)
router = APIRouter(tags=["commerce"])


# ── Products ─────────────────────────────────────────────────────────────────

@router.get("/{tenant_id}/products")
async def list_products(
    tenant_id: str,
    category: Optional[str] = Query(None),
    in_stock_only: bool = Query(True),
    sort: Optional[str] = Query(None, description="'popular' ranks by paid-order count"),
):
    products = await service.list_products(tenant_id, category=category, in_stock_only=in_stock_only,
                                           statuses={"active"}, with_variant_price_range=True)
    # Real merchandising data: orders_count / is_popular / rating (cached 10 min).
    products = service.annotate_merchandising(tenant_id, products)
    if sort == "popular":
        products.sort(key=lambda p: p.get("orders_count", 0), reverse=True)
    return {"tenant_id": tenant_id, "products": products, "count": len(products)}


@router.get("/{tenant_id}/products/{slug}")
async def get_product(tenant_id: str, slug: str):
    product = await service.get_product_by_slug(tenant_id, slug, statuses={"active", "unlisted"})
    if not product:
        raise HTTPException(status_code=404, detail=f"Product '{slug}' not found")
    service.annotate_merchandising(tenant_id, [product])
    # Bundles: expand component items so the storefront can show "what's inside".
    if product.get("product_type") == "bundle" and product.get("bundle_items"):
        try:
            ids = [i.get("product_id") for i in product["bundle_items"] if i.get("product_id")]
            comps = (service._client().table("commerce_products")
                     .select("id,name,slug,image_url,price_cents,pack_size")
                     .eq("tenant_id", tenant_id).in_("id", ids).execute().data or []) if ids else []
            by_id = {c["id"]: c for c in comps}
            product["bundle_contents"] = [
                {**by_id.get(i["product_id"], {}), "quantity": i.get("quantity", 1)}
                for i in product["bundle_items"] if i.get("product_id") in by_id
            ]
        except Exception as exc:
            log.debug("bundle expand skipped: %s", exc)
    return product


@router.get("/{tenant_id}/products/{slug}/related")
async def get_related_products(tenant_id: str, slug: str, limit: int = Query(4, ge=1, le=8)):
    """'Goes well with' — manual related_ids first, topped up with popular same-category items."""
    product = await service.get_product_by_slug(tenant_id, slug, statuses={"active", "unlisted"})
    if not product:
        raise HTTPException(status_code=404, detail=f"Product '{slug}' not found")
    all_products = await service.list_products(tenant_id, in_stock_only=True, statuses={"active"})
    service.annotate_merchandising(tenant_id, all_products)
    by_id = {p["id"]: p for p in all_products}

    picked: list = []
    for rid in (product.get("related_ids") or []):
        if rid in by_id and rid != product["id"]:
            picked.append(by_id[rid])
    if len(picked) < limit:
        fillers = sorted(
            (p for p in all_products
             if p["id"] != product["id"] and p not in picked
             and p.get("category") == product.get("category")),
            key=lambda p: p.get("orders_count", 0), reverse=True)
        picked.extend(fillers[: limit - len(picked)])
    if len(picked) < limit:  # cross-category popular as last resort
        fillers = sorted((p for p in all_products if p["id"] != product["id"] and p not in picked),
                         key=lambda p: p.get("orders_count", 0), reverse=True)
        picked.extend(fillers[: limit - len(picked)])
    keys = {"id", "name", "slug", "image_url", "price_cents", "pack_size", "category",
            "is_popular", "rating", "sold_by"}
    return {"related": [{k: p.get(k) for k in keys} for p in picked[:limit]]}


# ── Reviews (real ratings, collected via WhatsApp after delivery) ─────────────

class ReviewIn(BaseModel):
    rating: int
    comment: Optional[str] = None
    customer_phone: Optional[str] = None
    customer_name: Optional[str] = None
    product_id: Optional[str] = None
    order_id: Optional[str] = None
    source: str = "whatsapp"


@router.post("/{tenant_id}/reviews")
async def create_review(tenant_id: str, body: ReviewIn):
    if not 1 <= body.rating <= 5:
        raise HTTPException(status_code=400, detail="rating must be 1–5")
    row = {k: v for k, v in body.model_dump().items() if v is not None}
    row["tenant_id"] = tenant_id
    service._client().table("commerce_reviews").insert(row).execute()
    service._rating_cache.pop(tenant_id, None)  # bust cache so stars update
    if body.order_id:
        try:
            service._client().table("commerce_orders").update(
                {"review_rating": body.rating}).eq("id", body.order_id).eq("tenant_id", tenant_id).execute()
        except Exception:
            pass
    return {"ok": True}


@router.get("/{tenant_id}/admin/reviews")
async def admin_list_reviews(tenant_id: str, limit: int = Query(100, ge=1, le=500)):
    rows = (service._client().table("commerce_reviews").select("*")
            .eq("tenant_id", tenant_id).order("created_at", desc=True).limit(limit).execute().data or [])
    avg = round(sum(r["rating"] for r in rows) / len(rows), 2) if rows else None
    return {"reviews": rows, "count": len(rows), "average": avg}


# ── CMS pages (Puck self-serve page builder, P3) ─────────────────────────────
# Public: published pages only. Admin: read-all / upsert / delete. All tenant-scoped.

class PageIn(BaseModel):
    title: Optional[str] = None
    puck_data: dict = {}
    seo: Optional[dict] = None
    status: str = "draft"          # draft | published


@router.get("/{tenant_id}/pages")
async def list_pages(tenant_id: str):
    """Published pages (public) — slugs + titles for nav, not full bodies. Ordered the same way
    the tenant already controls via the admin page-list's drag-reorder (sort_order), since this
    list doubles as the storefront's nav menu — not just an arbitrary listing."""
    try:
        rows = (service._client().table("vula_pages")
                .select("slug,title,seo,status,updated_at")
                .eq("tenant_id", tenant_id).eq("status", "published")
                .order("sort_order").order("updated_at", desc=True).execute().data or [])
    except Exception as exc:
        log.debug("pages list skipped (run migration 041?): %s", exc)
        rows = []
    return {"tenant_id": tenant_id, "pages": rows}


@router.get("/{tenant_id}/pages/{slug}")
async def get_page(tenant_id: str, slug: str):
    """A single published page with its Puck document (public render)."""
    try:
        rows = (service._client().table("vula_pages").select("*")
                .eq("tenant_id", tenant_id).eq("slug", slug)
                .eq("status", "published").limit(1).execute().data or [])
    except Exception as exc:
        log.debug("page get skipped (run migration 041?): %s", exc)
        rows = []
    if not rows:
        raise HTTPException(status_code=404, detail=f"Page '{slug}' not found")
    return rows[0]


@router.get("/{tenant_id}/sitemap.xml")
async def page_sitemap(tenant_id: str):
    """XML sitemap of every published page, under the tenant's REAL public domain
    (store_url) — not Vula's own hash-routed page renderer, which isn't a URL shape
    search engines should index. Ready to use once wired into that domain's hosting
    (e.g. a rewrite from offthehook.co.za/sitemap.xml to this endpoint) — that wiring
    lives in the tenant's own storefront repo, outside Vula's."""
    from fastapi.responses import Response
    from vula.api import tenants as _tenants

    base = _tenants.store_url(tenant_id)
    if not base:
        xml = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>'
        return Response(content=xml, media_type="application/xml")

    try:
        rows = (service._client().table("vula_pages").select("slug,updated_at")
                .eq("tenant_id", tenant_id).eq("status", "published")
                .order("updated_at", desc=True).execute().data or [])
    except Exception as exc:
        log.debug("sitemap generation skipped (run migration 041?): %s", exc)
        rows = []

    base = base.rstrip("/")
    entries = "\n".join(
        f"  <url><loc>{base}/p/{r['slug']}</loc><lastmod>{(r.get('updated_at') or '')[:10]}</lastmod></url>"
        for r in rows if r.get("slug")
    )
    xml = (f'<?xml version="1.0" encoding="UTF-8"?>\n'
           f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{entries}\n</urlset>')
    return Response(content=xml, media_type="application/xml")


@router.get("/{tenant_id}/robots.txt")
async def page_robots(tenant_id: str):
    """robots.txt pointing at the sitemap above — same wiring caveat: useful once a
    rewrite on the tenant's real domain forwards /robots.txt here."""
    from fastapi.responses import Response
    from vula.api import tenants as _tenants

    base = _tenants.store_url(tenant_id)
    lines = ["User-agent: *", "Allow: /"]
    if base:
        lines.append(f"Sitemap: {base.rstrip('/')}/sitemap.xml")
    return Response(content="\n".join(lines) + "\n", media_type="text/plain")


@router.get("/{tenant_id}/admin/pages")
async def admin_list_pages(tenant_id: str):
    """All pages (draft + published) for the store editor."""
    try:
        rows = (service._client().table("vula_pages")
                .select("id,slug,title,status,updated_at,sort_order")
                .eq("tenant_id", tenant_id).order("sort_order").order("updated_at", desc=True)
                .execute().data or [])
    except Exception as exc:
        return {"tenant_id": tenant_id, "pages": [], "error": f"{exc} (run migration 041?)"}
    return {"tenant_id": tenant_id, "pages": rows}


@router.post("/{tenant_id}/admin/pages/reorder")
async def admin_reorder_pages(tenant_id: str, body: dict):
    """Set the display order of pages — body: {order: [slug, slug, ...]} (P4)."""
    db = service._client()
    for i, slug in enumerate((body or {}).get("order") or []):
        db.table("vula_pages").update({"sort_order": i}).eq("tenant_id", tenant_id).eq("slug", slug).execute()
    return {"ok": True}


@router.get("/{tenant_id}/admin/pages/{slug}")
async def admin_get_page(tenant_id: str, slug: str):
    rows = (service._client().table("vula_pages").select("*")
            .eq("tenant_id", tenant_id).eq("slug", slug).limit(1).execute().data or [])
    return rows[0] if rows else {"tenant_id": tenant_id, "slug": slug, "puck_data": {}, "status": "draft"}


@router.put("/{tenant_id}/admin/pages/{slug}")
async def upsert_page(tenant_id: str, slug: str, body: PageIn):
    """Create or update a page (store editor / Puck save). Publishing an existing page snapshots
    its PRE-update state into vula_page_versions first (P4 revision history), so there's always
    something to restore even though this endpoint itself only ever holds the current version."""
    row = {"tenant_id": tenant_id, "slug": slug, "title": body.title,
           "puck_data": body.puck_data or {}, "seo": body.seo or {},
           "status": body.status or "draft", "updated_at": service._now()}
    db = service._client()
    try:
        existing = (db.table("vula_pages").select("*")
                    .eq("tenant_id", tenant_id).eq("slug", slug).limit(1).execute().data or [])
        if existing:
            if (body.status or "draft") == "published":
                try:
                    prev = existing[0]
                    db.table("vula_page_versions").insert({
                        "page_id": prev["id"], "title": prev.get("title"),
                        "puck_data": prev.get("puck_data"), "seo": prev.get("seo"),
                        "status": prev.get("status"),
                    }).execute()
                except Exception as exc:
                    log.debug("page version snapshot skipped (run migration 081?): %s", exc)
            db.table("vula_pages").update(row).eq("id", existing[0]["id"]).execute()
            row["id"] = existing[0]["id"]
        else:
            db.table("vula_pages").insert(row).execute()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"{exc} (run migration 041?)")
    return {"page": row}


@router.get("/{tenant_id}/admin/pages/{slug}/versions")
async def admin_list_page_versions(tenant_id: str, slug: str):
    """Recent published-version snapshots for this page (P4 revision history)."""
    db = service._client()
    page = (db.table("vula_pages").select("id")
            .eq("tenant_id", tenant_id).eq("slug", slug).limit(1).execute().data or [])
    if not page:
        return {"versions": []}
    try:
        rows = (db.table("vula_page_versions").select("id,title,status,created_at")
                .eq("page_id", page[0]["id"]).order("created_at", desc=True).limit(20).execute().data or [])
    except Exception as exc:
        return {"versions": [], "error": f"{exc} (run migration 081?)"}
    return {"versions": rows}


@router.post("/{tenant_id}/admin/pages/{slug}/versions/{version_id}/restore")
async def admin_restore_page_version(tenant_id: str, slug: str, version_id: str):
    """Bring back an earlier version — restored into DRAFT so it can be reviewed before
    re-publishing, never auto-published over the current live version."""
    db = service._client()
    page = (db.table("vula_pages").select("id")
            .eq("tenant_id", tenant_id).eq("slug", slug).limit(1).execute().data or [])
    if not page:
        raise HTTPException(status_code=404, detail="page not found")
    version = (db.table("vula_page_versions").select("*")
               .eq("id", version_id).eq("page_id", page[0]["id"]).limit(1).execute().data or [])
    if not version:
        raise HTTPException(status_code=404, detail="version not found")
    v = version[0]
    db.table("vula_pages").update({
        "title": v.get("title"), "puck_data": v.get("puck_data"), "seo": v.get("seo"),
        "status": "draft", "updated_at": service._now(),
    }).eq("id", page[0]["id"]).execute()
    return {"restored": version_id}


@router.delete("/{tenant_id}/admin/pages/{slug}")
async def delete_page(tenant_id: str, slug: str):
    service._client().table("vula_pages").delete() \
        .eq("tenant_id", tenant_id).eq("slug", slug).execute()
    return {"removed": slug}


# ── Order workflow settings ───────────────────────────────────────────────────
@router.get("/{tenant_id}/admin/order-settings")
async def get_order_settings_ep(tenant_id: str):
    from vula.commerce.order_workflow import get_order_settings
    return {"settings": get_order_settings(tenant_id)}


@router.put("/{tenant_id}/admin/order-settings")
async def put_order_settings_ep(tenant_id: str, body: dict):
    from vula.commerce.order_workflow import upsert_order_settings
    return {"settings": upsert_order_settings(tenant_id, body or {})}


@router.get("/{tenant_id}/settings")
async def public_store_settings(tenant_id: str):
    """Public, read-only storefront presentation settings (hero copy, announcements,
    delivery fees, cutoff time) — the one real source for any storefront's own homepage/
    layout rendering, replacing what used to live only in off_the_hook's local
    data/store-settings.json (migrations 084/086, store-admin-reconciliation plan)."""
    from vula.commerce.order_workflow import get_order_settings
    cfg = get_order_settings(tenant_id)
    raw = {
        "announcements": cfg.get("announcements"),
        "delivery_fee_cents": cfg.get("delivery_fee_cents"),
        "free_delivery_threshold_cents": cfg.get("free_delivery_over_cents"),
        "express_delivery_extra_cents": cfg.get("express_delivery_extra_cents"),
        "cutoff_time": cfg.get("cutoff_time"),
        "hero_tagline": cfg.get("hero_tagline"),
        "hero_subtitle": cfg.get("hero_subtitle"),
        "featured_product_ids": cfg.get("featured_product_ids"),
    }
    return {"settings": {k: v for k, v in raw.items() if v is not None}}


# ── Cart ─────────────────────────────────────────────────────────────────────

@router.get("/{tenant_id}/cart/{session_id}")
async def get_cart(tenant_id: str, session_id: str, phone: Optional[str] = Query(None)):
    cart = await service.get_or_create_cart(tenant_id, session_id, customer_phone=phone)
    return cart


@router.post("/{tenant_id}/cart/{session_id}/add")
async def add_to_cart(tenant_id: str, session_id: str, body: AddToCartRequest):
    cart = await service.get_or_create_cart(tenant_id, session_id, customer_phone=body.customer_phone)
    item = await service.add_to_cart(tenant_id, cart["id"], str(body.product_id), body.quantity,
                                     variant_id=str(body.variant_id) if body.variant_id else None)
    return {"cart_id": cart["id"], "item": item}


@router.delete("/{tenant_id}/cart/{session_id}/{item_id}")
async def remove_from_cart(tenant_id: str, session_id: str, item_id: str):
    cart = await service.get_or_create_cart(tenant_id, session_id)
    await service.remove_from_cart(cart["id"], item_id)
    return {"removed": item_id}


class CartSyncItem(BaseModel):
    product_id: str
    quantity: float
    variant_id: Optional[str] = None


class CartSyncRequest(BaseModel):
    items: list[CartSyncItem]
    customer_phone: Optional[str] = None


@router.post("/{tenant_id}/cart/{session_id}/sync")
async def sync_cart(tenant_id: str, session_id: str, body: CartSyncRequest):
    """Replace the server cart with the client's current state (P0 fix 2026-07-17): the
    storefront edits its cart locally, but checkout charges from the SERVER cart — before this
    endpoint, removals/quantity changes never reached the server, so a customer could be
    charged for items they removed. The storefront now calls this on every edit AND right
    before checkout as a final reconcile."""
    cart = await service.get_or_create_cart(tenant_id, session_id, customer_phone=body.customer_phone)
    db = service._client()
    db.table("commerce_cart_items").delete().eq("cart_id", cart["id"]).execute()
    added = []
    for it in body.items:
        if it.quantity and it.quantity > 0:
            try:
                added.append(await service.add_to_cart(tenant_id, cart["id"], it.product_id, it.quantity,
                                                        variant_id=it.variant_id))
            except Exception as exc:
                log.warning("cart sync item skipped (%s): %s", it.product_id, exc)
    return {"cart_id": cart["id"], "items": len(added)}


# ── Checkout ─────────────────────────────────────────────────────────────────

class CheckoutRequest(BaseModel):
    session_id: str
    customer_name: str
    customer_phone: str
    customer_email: Optional[str] = None
    delivery_address: str
    delivery_slot: DeliverySlot = DeliverySlot.morning
    delivery_notes: Optional[str] = None
    channel: str = "web"
    discount_code: Optional[str] = None


# ── Discount codes (migration 091) ────────────────────────────────────────────

DISCOUNT_CODE_FIELDS = {"code", "type", "value", "min_order_cents", "starts_at", "ends_at",
                        "usage_limit", "active"}


@router.get("/{tenant_id}/admin/discount-codes")
async def admin_list_discount_codes(tenant_id: str):
    return {"discount_codes": await service.list_discount_codes(tenant_id)}


@router.post("/{tenant_id}/admin/discount-codes")
async def admin_create_discount_code(tenant_id: str, body: dict):
    data = {k: v for k, v in (body or {}).items() if k in DISCOUNT_CODE_FIELDS}
    if not (data.get("code") or "").strip():
        raise HTTPException(status_code=400, detail="code is required")
    if data.get("type") not in ("percent", "fixed", "free_shipping"):
        raise HTTPException(status_code=400, detail="type must be percent, fixed, or free_shipping")
    try:
        return await service.create_discount_code(tenant_id, data)
    except Exception as exc:
        if "idx_discount_codes_tenant_code" in str(exc) or "23505" in str(exc):
            raise HTTPException(status_code=400, detail=f"Code '{data['code']}' already exists.")
        raise HTTPException(status_code=400, detail=str(exc))


@router.patch("/{tenant_id}/admin/discount-codes/{code_id}")
async def admin_update_discount_code(tenant_id: str, code_id: str, body: dict):
    data = {k: v for k, v in (body or {}).items() if k in DISCOUNT_CODE_FIELDS}
    if not data:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    try:
        return await service.update_discount_code(tenant_id, code_id, data)
    except Exception as exc:
        if "idx_discount_codes_tenant_code" in str(exc) or "23505" in str(exc):
            raise HTTPException(status_code=400, detail=f"Code '{data.get('code')}' already exists.")
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/{tenant_id}/admin/discount-codes/{code_id}")
async def admin_delete_discount_code(tenant_id: str, code_id: str):
    await service.delete_discount_code(tenant_id, code_id)
    return {"deleted": code_id}


class ValidateDiscountRequest(BaseModel):
    code: str
    subtotal_cents: int


@router.post("/{tenant_id}/discount-codes/validate")
async def public_validate_discount_code(tenant_id: str, body: ValidateDiscountRequest):
    """Public preview — lets the storefront show 'code applied, -R50' before checkout. The
    actual charge always re-validates inside create_order; this never touches usage_count."""
    try:
        resolved = await service.resolve_discount_code(tenant_id, body.code, body.subtotal_cents)
    except service.DiscountError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log.warning("discount code validate failed (migration 091 not run?): %s", exc)
        raise HTTPException(status_code=400, detail=f"'{body.code}' isn't a valid code.")
    return {
        "code": resolved["code_row"]["code"],
        "discount_cents": resolved["discount_cents"],
        "free_shipping": resolved["free_shipping"],
    }


@router.post("/{tenant_id}/checkout")
async def create_checkout(tenant_id: str, body: CheckoutRequest):
    from vula.api import tenants as _tenants
    if not _tenants.is_active(tenant_id):
        raise HTTPException(status_code=403, detail="This store isn't accepting orders right now.")

    # Fetch cart
    cart = await service.get_or_create_cart(tenant_id, body.session_id, customer_phone=body.customer_phone)
    items = cart.get("commerce_cart_items", [])
    if not items:
        raise HTTPException(status_code=400, detail="Cart is empty")

    # Create order in Supabase
    try:
        order = await service.create_order(tenant_id, cart, body.model_dump())
    except service.OutOfStockError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    # Resolve Yoco credentials — per-tenant from Supabase, env var fallback
    from vula.api.yoco import _get_tenant_yoco_creds
    yoco_creds = await _get_tenant_yoco_creds(tenant_id)
    if not yoco_creds or not yoco_creds.get("secret_key"):
        raise HTTPException(
            status_code=503,
            detail=f"Payment gateway not configured for {tenant_id}. "
                   f"Connect Yoco in Vula Admin.",
        )
    yoco_secret = yoco_creds["secret_key"]

    from vula.api import tenants as _tenants
    store_url = _tenants.store_url(tenant_id) or "https://offthehook.co.za"

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            "https://payments.yoco.com/api/checkouts",
            headers={"Authorization": f"Bearer {yoco_secret}", "Content-Type": "application/json"},
            json={
                "amount": order["total_cents"],
                "currency": "ZAR",
                "successUrl": f"{store_url}/payment/success?order={order['id']}",
                "cancelUrl": f"{store_url}/payment/cancel?order={order['id']}",
                "failureUrl": f"{store_url}/payment/failed?order={order['id']}",
                "metadata": {
                    "order_id": order["id"],
                    "display_id": order["display_id"],
                    "tenant_id": tenant_id,
                    "customer_phone": body.customer_phone,
                    "customer_name": body.customer_name,
                },
            },
        )

    if not resp.is_success:
        log.error("Yoco checkout failed: %s", resp.text)
        raise HTTPException(status_code=502, detail="Payment gateway error — please try again")

    yoco_data = resp.json()
    await service.update_order_status(order["id"], "pending_payment", yoco_checkout_id=yoco_data["id"])

    # Fire-and-forget — the customer is waiting to be redirected to pay, so PDF render +
    # WhatsApp media upload (a couple of seconds) must not sit in this response's critical
    # path. Same asyncio.create_task convention already used elsewhere in this file for
    # background side-effects (statement ingestion jobs).
    import asyncio
    from vula.commerce.service import send_order_invoice
    asyncio.create_task(send_order_invoice(tenant_id, order["id"]))

    return {
        "order_id": order["id"],
        "display_id": order["display_id"],
        "redirect_url": yoco_data["redirectUrl"],
        "total_cents": order["total_cents"],
        "total_rands": f"R{order['total_cents'] / 100:.2f}",
    }


# ── Orders ───────────────────────────────────────────────────────────────────

@router.get("/{tenant_id}/orders/{order_id}")
async def get_order(tenant_id: str, order_id: str):
    order = await service.get_order(order_id)
    if not order or order.get("tenant_id") != tenant_id:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


# ── Invoice client approval (public, opt-in per invoice, migration 136) ───────
# The token is the only credential — no login. It can only ever reveal or act on the ONE
# invoice it was issued for; every failure mode (wrong invoice, wrong/missing token, approval
# not enabled) returns the same 404 rather than distinguishing them, so a guess can't be used to
# probe which part was wrong. Same "opaque scoped token, single allowed action" trust shape as
# the existing Yoco pay-link/webhook flow.

def _check_approval_token(invoice: dict, tenant_id: str, token: str) -> None:
    import secrets as _secrets
    real_token = invoice.get("approval_token") if invoice else None
    if (not invoice or invoice.get("tenant_id") != tenant_id
            or not invoice.get("requires_approval") or not real_token
            or not _secrets.compare_digest(token, real_token)):
        raise HTTPException(status_code=404, detail="Not found")


@router.get("/{tenant_id}/invoices/{invoice_id}/approve")
async def get_invoice_for_approval(tenant_id: str, invoice_id: str, token: str = Query(...)):
    """Read-only summary for the public approval page — no login."""
    invoice = await service.get_invoice(tenant_id, invoice_id)
    _check_approval_token(invoice, tenant_id, token)
    return {
        "invoice_number": invoice.get("invoice_number"),
        "customer_name": invoice.get("customer_name"),
        "line_items": invoice.get("line_items"),
        "subtotal_cents": invoice.get("subtotal_cents"),
        "vat_cents": invoice.get("vat_cents"),
        "total_cents": invoice.get("total_cents"),
        "due_date": invoice.get("due_date"),
        "approved_at": invoice.get("approved_at"),
        "approved_by": invoice.get("approved_by"),
    }


class InvoiceApprovalIn(BaseModel):
    token: str
    approved_by: Optional[str] = None


@router.post("/{tenant_id}/invoices/{invoice_id}/approve")
async def approve_invoice(tenant_id: str, invoice_id: str, body: InvoiceApprovalIn):
    """Informational only — never gates Mark paid/Record payment, which stay fully usable
    regardless of approval status."""
    invoice = await service.get_invoice(tenant_id, invoice_id)
    _check_approval_token(invoice, tenant_id, body.token)
    if invoice.get("approved_at"):
        return {"approved": True, "approved_at": invoice["approved_at"], "approved_by": invoice.get("approved_by")}
    patch = {"approved_at": service._now()}
    if body.approved_by and body.approved_by.strip():
        patch["approved_by"] = body.approved_by.strip()[:200]
    result = (service._client().table("commerce_invoices").update(patch)
              .eq("tenant_id", tenant_id).eq("id", invoice_id).execute())
    updated = result.data[0] if result.data else patch
    return {"approved": True, "approved_at": updated.get("approved_at"), "approved_by": updated.get("approved_by")}


# ── Admin endpoints ───────────────────────────────────────────────────────────

@router.get("/{tenant_id}/admin/orders")
async def admin_list_orders(
    tenant_id: str,
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List all orders for a tenant — for the merchant admin dashboard."""
    orders = await service.list_orders(tenant_id, status=status, limit=limit, offset=offset)
    return {"orders": orders, "count": len(orders)}


@router.patch("/{tenant_id}/admin/orders/{order_id}/status")
async def admin_update_order_status(tenant_id: str, order_id: str, body: dict):
    """Update order status — confirmed, packing, dispatched, delivered, cancelled."""
    valid = {"confirmed", "packing", "dispatched", "delivered", "cancelled", "refunded"}
    new_status = body.get("status")
    if new_status not in valid:
        raise HTTPException(status_code=400, detail=f"status must be one of {valid}")
    try:
        order = await service.get_order(order_id)
    except Exception:
        order = None            # get_order uses .single() → raises when the order doesn't exist
    if not order or order.get("tenant_id") != tenant_id:
        raise HTTPException(status_code=404, detail="Order not found")
    await service.update_order_status(order_id, new_status)
    # Keep stock in sync: restore on cancel/refund, deduct once when it enters fulfilment.
    if new_status in ("cancelled", "refunded"):
        await service.apply_order_stock(order_id, restore=True)
    elif new_status in ("confirmed", "packing", "dispatched", "delivered"):
        await service.apply_order_stock(order_id, restore=False)
    # A refund moves real money. Vula always restores stock; whether it also calls the gateway
    # is opt-in (body.auto_refund) — this endpoint also handles routine status transitions, and
    # a real charge-reversal call should never fire without the owner explicitly asking for it.
    refund_result = None
    if new_status == "refunded":
        checkout_id = order.get("yoco_checkout_id")
        if body.get("auto_refund") and checkout_id:
            amount = int(body.get("amount_cents") or order.get("total_cents") or 0)
            from vula.api.yoco import refund_yoco_payment
            result = await refund_yoco_payment(tenant_id, checkout_id, amount)
            now = service._now()
            if result.get("ok"):
                refund_result = {"gateway": "yoco", "status": "pending", "amount_cents": amount}
                patch = {"refund_status": "pending", "refunded_amount_cents": amount,
                         "refunded_at": now, "yoco_refund_id": result.get("refund_id")}
            else:
                refund_result = {"gateway": "yoco", "status": "failed", "detail": result.get("detail")}
                patch = {"refund_status": "failed"}
            try:
                service._client().table("commerce_orders").update(patch) \
                    .eq("id", order_id).eq("tenant_id", tenant_id).execute()
            except Exception as exc:
                log.warning("refund tracking update failed for order %s: %s", order_id, exc)
        try:
            from vula.integrations.notify import notify_team
            if refund_result and refund_result["status"] == "pending":
                msg = (f"Order {order.get('display_id') or order_id} refunded automatically via "
                       f"Yoco (R{refund_result['amount_cents']/100:.2f}).")
            elif refund_result and refund_result["status"] == "failed":
                msg = (f"Refund needed: order {order.get('display_id') or order_id} "
                       f"(R{(order.get('total_cents') or 0)/100:.2f}) — automatic Yoco refund "
                       f"failed ({refund_result.get('detail')}), please process it manually.")
            else:
                msg = (f"Refund needed: order {order.get('display_id') or order_id} "
                       f"(R{(order.get('total_cents') or 0)/100:.2f}) — process it in your payment gateway.")
            await notify_team(tenant_id, "refund", msg)
        except Exception as exc:
            log.debug("refund team notify skipped: %s", exc)

    # Delivered → ask the customer for a rating over WhatsApp (once per order,
    # best-effort — usually inside the 24h service window since they just ordered).
    if new_status == "delivered" and not order.get("review_requested_at") and order.get("customer_phone"):
        try:
            from vula.api.whatsapp import _send_reply
            first = (order.get("customer_name") or "").split(" ")[0]
            sent = await _send_reply(
                order["customer_phone"],
                (f"Hi {first}! 🐟 Your order {order.get('display_id') or ''} has been delivered — "
                 "we hope everything is perfect. How was it? Reply with a rating from 1 to 5 "
                 "(5 = excellent). Thank you!").replace("  ", " "),
                tenant_id,
            )
            if sent:
                service._client().table("commerce_orders").update(
                    {"review_requested_at": service._now()}
                ).eq("id", order_id).eq("tenant_id", tenant_id).execute()
        except Exception as exc:
            log.debug("review request skipped: %s", exc)

    return {"order_id": order_id, "status": new_status, "refund": refund_result}


_ORDER_DELETABLE_STATUSES = ("pending_payment", "cancelled")


async def _delete_order_if_safe(tenant_id: str, order_id: str) -> Optional[str]:
    """Delete one order + its items if it never became real fulfilment. Returns an error string
    on failure/skip, or None on success — so callers (single + bulk) share one guard."""
    order = await service.get_order(order_id)
    if not order or order.get("tenant_id") != tenant_id:
        return "not found"
    if order.get("status") not in _ORDER_DELETABLE_STATUSES:
        return f"status is '{order.get('status')}' — only pending/cancelled orders can be deleted"
    db = service._client()
    db.table("commerce_order_items").delete().eq("order_id", order_id).execute()
    db.table("commerce_orders").delete().eq("id", order_id).execute()
    return None


@router.delete("/{tenant_id}/admin/orders/{order_id}")
async def admin_delete_order(tenant_id: str, order_id: str):
    """Permanently remove an order — restricted to orders that never became real fulfilment
    (pending_payment/cancelled only), so a paid/dispatched/delivered order can never be deleted
    this way, only cancelled or refunded first. Exists for clearing out test/duplicate orders
    without a manual DB delete every time."""
    err = await _delete_order_if_safe(tenant_id, order_id)
    if err == "not found":
        raise HTTPException(status_code=404, detail="Order not found")
    if err:
        raise HTTPException(status_code=400, detail=err)
    return {"deleted": order_id}


@router.post("/{tenant_id}/admin/orders/bulk-delete")
async def admin_bulk_delete_orders(tenant_id: str, body: dict):
    """Delete multiple orders in one call — same pending/cancelled-only guard as the single
    endpoint, applied independently per order so one ineligible order in the batch doesn't block
    the rest. Returns which ids were actually removed vs skipped and why."""
    order_ids = [str(x) for x in (body.get("order_ids") or [])]
    if not order_ids:
        raise HTTPException(status_code=400, detail="order_ids is required")
    deleted, skipped = [], []
    for oid in order_ids:
        err = await _delete_order_if_safe(tenant_id, oid)
        if err:
            skipped.append({"order_id": oid, "reason": err})
        else:
            deleted.append(oid)
    return {"deleted": deleted, "skipped": skipped}


@router.get("/{tenant_id}/admin/products")
async def admin_list_products(tenant_id: str):
    """List all products including out-of-stock AND archived — merchant product management."""
    products = await service.list_products(tenant_id, in_stock_only=False, include_archived=True)
    return {"products": products, "count": len(products)}


@router.patch("/{tenant_id}/admin/products/{product_id}")
async def admin_update_product(tenant_id: str, product_id: str, body: dict):
    """Patch product — everything the merchant may edit (widened for migration 073 depth:
    gallery images, sale pricing, pack/weight/serves, archive, ordering)."""
    allowed = {"in_stock", "price_cents", "name", "description", "notes",
               "is_daily_catch", "stock_quantity", "image_url", "category", "sold_by",
               "images", "sale_price_cents", "sale_ends_at", "sort_order", "archived",
               "pack_size", "weight_grams", "serves",
               "reorder_threshold", "reorder_qty", "default_supplier_id",
               "pricing_mode", "price_per_kg_cents", "min_weight_g", "max_weight_g",
               "reference_weight_g", "catch_source", "fisherman_name", "seo", "status", "options",
               "product_type", "bundle_items", "cooking_tips", "related_ids"}
    update = {k: v for k, v in body.items() if k in allowed}
    if not update:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    # Keep the cover in sync: first gallery image becomes image_url unless explicitly set.
    if "images" in update and update["images"] and "image_url" not in update:
        update["image_url"] = update["images"][0]
    result = await service.update_product(tenant_id, product_id, update)
    return result


@router.post("/{tenant_id}/admin/geo/geocode")
async def admin_geocode(tenant_id: str, body: dict):
    """Find coordinates for an address/suburb (Delivery settings map picker, migration 098)."""
    from vula.commerce import geo
    q = (body or {}).get("query") or ""
    result = await geo.geocode(q)
    if not result:
        raise HTTPException(status_code=404, detail=f"Couldn't find '{q}' — try a suburb + city.")
    return result


# ── Per-tenant categories (migration 073 — replaces the hardcoded CHECK constraint) ──

@router.get("/{tenant_id}/admin/categories")
async def admin_list_categories(tenant_id: str):
    try:
        rows = (service._client().table("commerce_categories").select("*")
                .eq("tenant_id", tenant_id).order("sort_order").execute().data or [])
    except Exception as exc:
        log.debug("categories list skipped (run migration 073?): %s", exc)
        rows = []
    return {"categories": rows}


@router.post("/{tenant_id}/admin/categories")
async def admin_upsert_category(tenant_id: str, body: dict):
    import re as _re
    label = (body.get("label") or "").strip()
    if not label:
        raise HTTPException(status_code=400, detail="label required")
    key = (body.get("key") or "").strip() or _re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    row = {"tenant_id": tenant_id, "key": key, "label": label,
           "sort_order": int(body.get("sort_order") or 0)}
    try:
        res = (service._client().table("commerce_categories")
               .upsert(row, on_conflict="tenant_id,key").execute())
        return res.data[0] if res.data else row
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"{exc} (run migration 073?)")


@router.delete("/{tenant_id}/admin/categories/{key}")
async def admin_delete_category(tenant_id: str, key: str):
    service._client().table("commerce_categories").delete() \
        .eq("tenant_id", tenant_id).eq("key", key).execute()
    return {"deleted": key}


@router.post("/{tenant_id}/admin/products")
async def admin_create_product(tenant_id: str, body: dict):
    """Create a new product. Auto-generates a slug from the name."""
    import re as _re
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    try:
        price_cents = int(body.get("price_cents") or 0)
    except (TypeError, ValueError):
        price_cents = 0
    if price_cents <= 0:
        raise HTTPException(status_code=400, detail="price_cents must be a positive integer")

    slug = _re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "product"
    # Ensure slug uniqueness within the tenant
    existing = {p.get("slug") for p in await service.list_products(tenant_id, in_stock_only=False)}
    base, n = slug, 2
    while slug in existing:
        slug = f"{base}-{n}"; n += 1

    # Enforce the DB CHECK constraints.
    VALID_CATEGORIES = {"fresh_fish", "fresh_chicken", "frozen_chicken", "frozen_seafood", "extras"}
    category = body.get("category") if body.get("category") in VALID_CATEGORIES else "extras"
    sold_by = body.get("sold_by") if body.get("sold_by") in ("kg", "pack") else "pack"

    payload = {
        "name": name,
        "slug": slug,
        "price_cents": price_cents,
        "category": category,
        "sold_by": sold_by,
        "description": body.get("description") or "",
        "image_url": body.get("image_url"),
        "in_stock": body.get("in_stock", True),
        "stock_quantity": body.get("stock_quantity"),
    }
    return await service.create_product(tenant_id, payload)


def _csv_bool(v, default: bool) -> bool:
    if v is None or v == "":
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "y")


@router.post("/{tenant_id}/admin/products/import")
async def admin_import_products(tenant_id: str, body: dict):
    """Bulk CSV product import (P3.2). The client does the CSV parse + dry-run preview; this
    endpoint commits the rows it's given. Upserts on (tenant_id, slug) — mirrors the
    commerce_categories upsert pattern. Unknown categories are auto-created rather than
    rejected (matches the platform's low-friction onboarding ethos). Returns a per-row result
    so one bad row doesn't fail the whole batch."""
    import re as _re
    rows = body.get("rows") or []
    if not isinstance(rows, list) or not rows:
        raise HTTPException(status_code=400, detail="rows required")

    try:
        known = {c["key"] for c in (service._client().table("commerce_categories")
                 .select("key").eq("tenant_id", tenant_id).execute().data or [])}
    except Exception:
        known = set()
    known |= {"fresh_fish", "fresh_chicken", "frozen_chicken", "frozen_seafood", "extras"}

    existing = {p["slug"]: p for p in
                await service.list_products(tenant_id, in_stock_only=False, include_archived=True)}

    NUMERIC_FIELDS = ("price_per_kg_cents", "min_weight_g", "max_weight_g", "reference_weight_g",
                      "weight_grams", "stock_quantity")
    results = []
    for i, row in enumerate(rows):
        row = row or {}
        slug_for_report = (row.get("slug") or row.get("name") or f"row {i + 1}")
        try:
            name = (row.get("name") or "").strip()
            if not name:
                raise ValueError("name is required")
            try:
                price_cents = int(round(float(row.get("price_cents") or 0)))
            except (TypeError, ValueError):
                raise ValueError("price_cents must be a number")
            if price_cents <= 0:
                raise ValueError("price_cents must be a positive number")

            slug = (row.get("slug") or "").strip() or _re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "product"
            slug_for_report = slug

            category = (row.get("category") or "extras").strip() or "extras"
            if category not in known:
                try:
                    cat_row = {"tenant_id": tenant_id, "key": category,
                               "label": category.replace("_", " ").title(), "sort_order": 0}
                    service._client().table("commerce_categories").upsert(
                        cat_row, on_conflict="tenant_id,key").execute()
                    known.add(category)
                except Exception:
                    pass  # categories table may not exist (pre-migration-073) — keep the string anyway

            warning = None
            image_url = (row.get("image_url") or "").strip() or None
            if image_url and not image_url.startswith(("http://", "https://")):
                warning = f"image_url '{image_url}' doesn't look like a URL — skipped"
                image_url = None

            payload = {
                "name": name, "slug": slug, "price_cents": price_cents, "category": category,
                "sold_by": row.get("sold_by") if row.get("sold_by") in ("kg", "pack") else "pack",
                "description": row.get("description") or "",
                "image_url": image_url,
                "in_stock": _csv_bool(row.get("in_stock"), True),
                "is_daily_catch": _csv_bool(row.get("is_daily_catch"), False),
                "pricing_mode": row.get("pricing_mode") if row.get("pricing_mode") in ("fixed", "by_weight") else "fixed",
                "catch_source": (row.get("catch_source") or "").strip() or None,
                "fisherman_name": (row.get("fisherman_name") or "").strip() or None,
                "status": row.get("status") if row.get("status") in ("active", "draft", "unlisted", "archived") else "active",
            }
            seo_title = (row.get("seo_title") or "").strip()
            seo_description = (row.get("seo_description") or "").strip()
            if seo_title or seo_description:
                payload["seo"] = {k: v for k, v in {"title": seo_title or None, "description": seo_description or None}.items() if v}
            for f in NUMERIC_FIELDS:
                if row.get(f) not in (None, ""):
                    try:
                        payload[f] = int(round(float(row[f])))
                    except (TypeError, ValueError):
                        pass

            if slug in existing:
                await service.update_product(tenant_id, existing[slug]["id"], payload)
                results.append({"row": i, "slug": slug, "status": "updated", "warning": warning})
            else:
                created = await service.create_product(tenant_id, payload)
                existing[slug] = created
                results.append({"row": i, "slug": slug, "status": "created", "warning": warning})
        except Exception as exc:
            results.append({"row": i, "slug": slug_for_report, "status": "error", "error": str(exc)})

    return {
        "results": results,
        "created": sum(1 for r in results if r["status"] == "created"),
        "updated": sum(1 for r in results if r["status"] == "updated"),
        "errors": sum(1 for r in results if r["status"] == "error"),
    }


@router.delete("/{tenant_id}/admin/products/{product_id}")
async def admin_delete_product(tenant_id: str, product_id: str):
    """Delete a product — but ARCHIVE instead when any order references it, so order history
    keeps its product links intact (migration 073). Archived products vanish from the
    storefront/menu but stay restorable in the admin."""
    try:
        refs = (service._client().table("commerce_order_items").select("id")
                .eq("product_id", product_id).limit(1).execute().data or [])
    except Exception:
        refs = []
    if refs:
        await service.update_product(tenant_id, product_id, {"archived": True, "in_stock": False})
        return {"archived": product_id}
    await service.delete_product(tenant_id, product_id)
    return {"deleted": product_id}


# ── AI product photo (generate / restyle) ─────────────────────────────────────

class GeneratePhotoRequest(BaseModel):
    mode: str = "generate"               # 'generate' (from product info) | 'restyle' (perfect a real snap)
    photo_base64: Optional[str] = None   # required for restyle: the tenant's own photo


def _photo_subject(product: dict) -> str:
    """Category-aware, realism-first subject line (raw cuts / retail packs, never cooked)."""
    name = product["name"]
    pack = product.get("pack_size") or ""
    cat = product.get("category") or ""
    pack_txt = f" ({pack})" if pack else ""
    if cat == "fresh_fish":
        return f"fresh raw {name}{pack_txt}, as sold at a premium fishmonger"
    if cat == "fresh_chicken":
        return f"fresh raw pasture-raised chicken — {name}{pack_txt}, uncooked, as sold at a butcher"
    if cat in ("frozen_chicken", "frozen_seafood"):
        return f"frozen — {name}{pack_txt}, shown as the frozen retail portions as sold"
    return f"{name}{pack_txt}, the retail product as sold"


@router.post("/{tenant_id}/admin/products/{product_id}/generate-photo")
async def admin_generate_product_photo(tenant_id: str, product_id: str, body: GeneratePhotoRequest):
    """AI product photo, styled on the tenant's own reference photo.

    mode='generate' — create a production-quality catalog photo from the product info.
    mode='restyle'  — the tenant snapped a real photo (photo_base64); re-shoot it in the
                      house style so it's store-perfect. The real product stays the subject.
    Works for every tenant: the style anchor is the tenant's first real product photo.
    """
    import base64 as _b64
    import time as _time

    from core.image_gen import (
        ImageGenError, build_product_prompt, generate_image, qa_check_image,
    )
    from vula.api.whatsapp import _upload_to_storage

    db = service._client()
    prod = (db.table("commerce_products").select("*")
            .eq("tenant_id", tenant_id).eq("id", product_id).limit(1).execute()).data
    if not prod:
        raise HTTPException(status_code=404, detail="Product not found")
    prod = prod[0]
    # Prompt generator: LLM crafts a visually concrete subject (works for any
    # product vertical); the food template is only the fallback. Brand kit
    # (trading name + tagline) grounds it in the tenant's tone.
    brand_context = ""
    try:
        bs = (db.table("commerce_invoice_settings").select("trading_as,company_name")
              .eq("tenant_id", tenant_id).limit(1).execute()).data or []
        if bs:
            brand_context = bs[0].get("trading_as") or bs[0].get("company_name") or ""
    except Exception:
        pass
    try:
        from core.image_gen import craft_photo_subject
        subject = await craft_photo_subject(prod, brand_context)
    except Exception:
        subject = _photo_subject(prod)

    # Style anchor: the tenant's first NON-AI product photo (their real house style),
    # falling back to any existing photo.
    ref_bytes = None
    others = (db.table("commerce_products").select("image_url")
              .eq("tenant_id", tenant_id).neq("id", product_id)
              .not_.is_("image_url", "null").limit(20).execute()).data or []
    ref_url = next((r["image_url"] for r in others if "-ai-" not in (r["image_url"] or "")),
                   (others[0]["image_url"] if others else None))
    if ref_url:
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                ref_bytes = (await c.get(ref_url)).content
        except Exception:
            ref_bytes = None

    if body.mode == "restyle":
        if not body.photo_base64:
            raise HTTPException(status_code=400, detail="photo_base64 required for restyle mode")
        raw = body.photo_base64.split(",", 1)[-1]
        try:
            own_photo = _b64.b64decode(raw)
        except Exception:
            raise HTTPException(status_code=400, detail="photo_base64 is not valid base64")
        prompt = (
            "Re-shoot the product in the FIRST attached photo as a photorealistic professional "
            "food product photograph, shot on a DSLR. Keep the exact same real product as the "
            "subject — same cut, portioning, quantity and true colours — but present it in the "
            "clean studio style of the SECOND attached reference photo (same surface, backdrop, "
            "lighting and angle). Square 1:1 crop, subject centred, no text or watermark, no "
            "illustration/CGI look."
        )
        # Model receives: instruction + tenant's own photo (+ style reference when present).
        content_ref = own_photo if not ref_bytes else None
        try:
            if ref_bytes:
                # two-image call: own photo first, reference second
                import base64 as b64mod
                from core.image_gen import OPENROUTER_BASE
                from config import settings as _s
                imgs = [own_photo, ref_bytes]
                content = [{"type": "text", "text": prompt}] + [
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{b64mod.b64encode(i).decode()}"}}
                    for i in imgs
                ]
                async with httpx.AsyncClient(timeout=120) as c:
                    resp = await c.post(
                        f"{OPENROUTER_BASE}/chat/completions",
                        headers={"Authorization": f"Bearer {_s.openrouter_api_key}"},
                        json={"model": _s.model_image,
                              "messages": [{"role": "user", "content": content}],
                              "modalities": ["image", "text"]},
                    )
                if not resp.is_success:
                    raise ImageGenError(f"OpenRouter {resp.status_code}: {resp.text[:200]}")
                import re as _re
                url0 = resp.json()["choices"][0]["message"]["images"][0]["image_url"]["url"]
                img = _b64.b64decode(_re.match(r"data:image/[a-zA-Z+]+;base64,(.+)", url0).group(1))
            else:
                img = await generate_image(prompt, content_ref)
        except (ImageGenError, Exception) as exc:
            raise HTTPException(status_code=502, detail=f"Photo generation failed: {exc}")
    else:
        prompt = build_product_prompt(subject, None, has_reference_image=bool(ref_bytes))
        try:
            img = await generate_image(prompt, ref_bytes)
        except ImageGenError as exc:
            raise HTTPException(status_code=502, detail=f"Photo generation failed: {exc}")

    # Realism QA gate — one retry, then refuse rather than publish a bad image.
    if not await qa_check_image(img, subject):
        try:
            img = await generate_image(prompt, ref_bytes)
        except ImageGenError as exc:
            raise HTTPException(status_code=502, detail=f"Photo generation failed: {exc}")
        if not await qa_check_image(img, subject):
            raise HTTPException(status_code=422, detail="Generated image failed the realism check — please try again or upload a photo.")

    slug = prod.get("slug") or product_id
    path = f"{tenant_id}/products/{int(_time.time() * 1000)}-ai-{slug}.jpg"
    url = _upload_to_storage("product-images", path, img, "image/jpeg")
    if not url:
        raise HTTPException(status_code=502, detail="Image upload failed")

    # New photo becomes the cover; keep any previous images in the gallery after it.
    gallery = [url] + [u for u in (prod.get("images") or []) if u and u != url][:4]
    await service.update_product(tenant_id, product_id, {"image_url": url, "images": gallery})

    # Meter the generation cost against the tenant (vula_ai_usage).
    try:
        from vula.integrations.metering import record_image
        from config import settings as _cfg
        record_image(tenant_id, _cfg.model_image)
    except Exception:
        pass

    return {"image_url": url, "gallery": gallery, "mode": body.mode}


@router.post("/{tenant_id}/admin/products/{product_id}/generate-cooking-tips")
async def admin_generate_cooking_tips(tenant_id: str, product_id: str):
    """Write a short, practical 'how to cook it' for the product page (cheap LLM).
    Saved to cooking_tips; the owner can edit it afterwards."""
    prod = await service.get_product(tenant_id, product_id)
    if not prod:
        raise HTTPException(status_code=404, detail="Product not found")

    import litellm
    from core.llm_router import resolve_cheap_route
    litellm.drop_params = True
    model, api_key, api_base = await resolve_cheap_route()

    resp = await litellm.acompletion(
        model=model,
        messages=[{"role": "user", "content": (
            "Write 2–3 sentences of practical South African home-cooking advice for this "
            f"product: {prod['name']} ({prod.get('category')}, {prod.get('pack_size') or ''}). "
            "Direct and specific: best cooking method (braai/pan/oven), rough time/temperature, "
            "one simple flavour suggestion. No intro, no bullet points, no exclamation marks — "
            "just the advice, as if a fishmonger or butcher is talking to a customer."
        )}],
        temperature=0.4, max_tokens=160, api_key=api_key, api_base=api_base,
    )
    import re as _re
    tips = _re.sub(r"<think>.*?</think>", "", resp.choices[0].message.content or "",
                   flags=_re.DOTALL).strip()
    if not tips:
        raise HTTPException(status_code=502, detail="Could not write cooking tips — try again")
    await service.update_product(tenant_id, product_id, {"cooking_tips": tips})
    return {"cooking_tips": tips}


# ── Product variants (migration 087, Phase 4) ─────────────────────────────────

VARIANT_FIELDS = {"option_values", "sku", "barcode", "price_cents", "stock_quantity",
                  "in_stock", "sort_order", "archived", "reorder_threshold", "reorder_qty",
                  "default_supplier_id"}


@router.get("/{tenant_id}/admin/products/{product_id}/variants")
async def admin_list_variants(tenant_id: str, product_id: str):
    variants = await service.list_variants(tenant_id, product_id, include_archived=True)
    return {"variants": variants}


def _variant_barcode_conflict(exc: Exception, barcode) -> HTTPException:
    """Turn the raw unique-constraint error (migration 087's per-tenant barcode index) into a
    message a merchant can act on, instead of a bare 500."""
    if "idx_variants_tenant_barcode" in str(exc) or "23505" in str(exc):
        return HTTPException(status_code=400, detail=f"Barcode '{barcode}' is already used by another variant.")
    return HTTPException(status_code=400, detail=str(exc))


@router.post("/{tenant_id}/admin/products/{product_id}/variants")
async def admin_create_variant(tenant_id: str, product_id: str, body: dict):
    data = {k: v for k, v in (body or {}).items() if k in VARIANT_FIELDS}
    try:
        return await service.create_variant(tenant_id, product_id, data)
    except Exception as exc:
        raise _variant_barcode_conflict(exc, data.get("barcode"))


@router.patch("/{tenant_id}/admin/products/{product_id}/variants/{variant_id}")
async def admin_update_variant(tenant_id: str, product_id: str, variant_id: str, body: dict):
    data = {k: v for k, v in (body or {}).items() if k in VARIANT_FIELDS}
    if not data:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    try:
        return await service.update_variant(tenant_id, variant_id, data)
    except Exception as exc:
        raise _variant_barcode_conflict(exc, data.get("barcode"))


@router.delete("/{tenant_id}/admin/products/{product_id}/variants/{variant_id}")
async def admin_delete_variant(tenant_id: str, product_id: str, variant_id: str):
    await service.delete_variant(tenant_id, variant_id)
    return {"deleted": variant_id}


@router.get("/{tenant_id}/admin/products/lookup")
async def admin_lookup_barcode(tenant_id: str, barcode: str = Query(...)):
    """Barcode scan lookup — Smart Scanner POS prep (not yet consumed by anything; adding it
    now means the future scanner integration is a pure consumer, not another schema change)."""
    match = await service.find_by_barcode(tenant_id, barcode)
    if not match:
        raise HTTPException(status_code=404, detail=f"No variant found for barcode '{barcode}'")
    product = match.pop("commerce_products", None)
    return {"variant": match, "product": product}


# ── In-portal admin assistant (chat to Vula from the dashboard) ───────────────

class AssistantRequest(BaseModel):
    message: str
    session_id: Optional[str] = None     # web chat session key (per browser/user)


@router.post("/{tenant_id}/admin/assistant")
async def admin_assistant(tenant_id: str, body: AssistantRequest):
    """Chat to the Vula admin agent from inside the web portal.

    Same commerce_admin skill that powers the WhatsApp owner agent — so the
    tenant can run the shop (sales, orders, stock, invoices, expenses,
    broadcast previews) by chatting here instead of WhatsApp.
    """
    from core.skills.base import SkillInput
    from core.skills.loader import get_skill

    if not (body.message or "").strip():
        raise HTTPException(status_code=400, detail="message is required")

    session_key = f"webadmin:{body.session_id or 'default'}"
    history, sid = "", None
    try:
        session = await service.get_or_create_session(tenant_id, session_key=session_key, channel="web")
        sid = session["id"]
        history = service.format_history(
            await service.get_recent_messages(tenant_id, sid, limit=service.DEFAULT_HISTORY_LIMIT))
    except Exception as exc:
        log.debug("Assistant session/history load failed (non-fatal): %s", exc)

    skill = get_skill("commerce_admin")
    out = await skill(SkillInput(
        question=body.message, tenant_id=tenant_id,
        conversation_history=history, metadata={"session_id": session_key},
    ))
    answer = out.answer if (out.success and out.answer) else (out.error or "Sorry, I couldn't process that just now.")

    if sid:
        try:
            await service.append_message(tenant_id, sid, "user", body.message)
            await service.append_message(tenant_id, sid, "assistant", answer)
        except Exception:
            pass

    return {"answer": answer, "ok": bool(out.success and out.answer)}


# Tools that use the confirm=true two-layer gate (widened confirm-gate coverage) — a call with
# confirm not truthy was only a PREVIEW, nothing actually happened yet. These are also the
# highest-stakes actions (real spend, real stock, real money), so they get a visual marker in
# the activity feed regardless of whether this particular call was a preview or a confirmed one.
_CONSEQUENTIAL_TOOLS = {
    "update_stock", "create_purchase_order", "send_purchase_order", "update_po_status",
    "create_discount_code", "update_discount_code", "delete_discount_code", "cancel_booking",
    "delete_supplier", "record_payment", "send_broadcast", "draft_storefront_page",
    "add_storefront_section", "create_automation_rule",
    # 2026-08-24 (chat-accuracy audit Phase 5): create_invoice/send_invoice/
    # convert_quote_to_invoice were confirm-gated-in-spirit already (real money, real customer
    # messages) but never flagged consequential here — an owner reviewing Agent Activity had no
    # visual cue these happened. Plus the 6 tools newly given a real confirm=true gate this pass.
    "create_invoice", "send_invoice", "convert_quote_to_invoice",
    "create_manual_order", "create_product", "update_product", "upsert_supplier",
    "create_booking", "create_subscription",
}


def _truthy(v) -> bool:
    return str(v).strip().lower() in ("true", "1", "yes")


def _confirmed_prefix(args: dict) -> str:
    """For confirm-gated tools: say whether this call actually did anything or only previewed."""
    return "Confirmed:" if _truthy(args.get("confirm")) else "Previewed (not yet confirmed):"


def _narrate(tool: str, args: dict) -> Optional[str]:
    """Turn a raw (tool_name, args) telemetry pair into a business-readable sentence for the
    Agent Activity feed. `args` is the already-PII-stripped, value-truncated dict the tool was
    actually called with — returns None for any tool without a template (the caller falls back
    to showing the raw args, so unrecognised tools still render, just less prose-y)."""
    try:
        a = args if isinstance(args, dict) else {}
        g = a.get
        if tool == "update_stock":
            return f"{_confirmed_prefix(a)} set {g('product', 'a product')} stock to {g('quantity', '?')}"
        if tool == "create_purchase_order":
            return f"{_confirmed_prefix(a)} created a purchase order for {g('supplier_name', 'a supplier')}"
        if tool == "send_purchase_order":
            return f"{_confirmed_prefix(a)} sent purchase order {g('po_ref', '')} via {g('channel', 'email')}"
        if tool == "update_po_status":
            return f"{_confirmed_prefix(a)} marked purchase order {g('po_ref', '')} as {g('status', '?')}"
        if tool == "create_discount_code":
            return f"{_confirmed_prefix(a)} created discount code {g('code', '')}"
        if tool == "update_discount_code":
            return f"{_confirmed_prefix(a)} updated discount code {g('code', '')}"
        if tool == "delete_discount_code":
            return f"{_confirmed_prefix(a)} deleted discount code {g('code', '')}"
        if tool == "cancel_booking":
            return f"{_confirmed_prefix(a)} cancelled booking {g('booking_id', '')}"
        if tool == "delete_supplier":
            return f"{_confirmed_prefix(a)} deleted supplier {g('name', '')}"
        if tool == "record_payment":
            amt = g("amount_rands")
            return (f"{_confirmed_prefix(a)} recorded a payment of R{amt} against invoice "
                    f"{g('invoice_number', '')}") if amt else \
                   f"{_confirmed_prefix(a)} recorded a payment against invoice {g('invoice_number', '')}"
        if tool == "send_broadcast":
            return f"{_confirmed_prefix(a)} sent broadcast '{g('template_name', '')}' to {g('audience', 'all')}"
        if tool == "draft_storefront_page":
            return f"{_confirmed_prefix(a)} drafted a change to the '{g('slug', '')}' page: {g('instruction', '')}"
        if tool == "add_storefront_section":
            return f"{_confirmed_prefix(a)} added a {g('feature', '')} section to the '{g('slug', '')}' page"
        if tool == "create_automation_rule":
            return f"{_confirmed_prefix(a)} taught Vula a new automation rule: {g('description', '')}"
        if tool == "create_invoice":
            doc = g("doc_type", "invoice")
            return f"Created a {doc} for {g('customer_name', 'a customer')}"
        if tool == "send_invoice":
            return f"Sent invoice {g('invoice_number', '')} to the customer"
        if tool == "convert_quote_to_invoice":
            return f"Converted quote {g('quote_number', '')} to an invoice"
        if tool == "update_quote_status":
            return f"Marked quote {g('quote_number', '')} as {g('status', '?')}"
        if tool == "update_order_status":
            return f"Updated order {g('order_id') or g('display_id', '')} to {g('status', '?')}"
        if tool == "create_booking":
            return f"{_confirmed_prefix(a)} booked an appointment for {g('customer_name', 'a customer')} at {g('start', '')}"
        if tool == "add_expense":
            return f"Logged an expense of R{g('amount_rands', '?')} ({g('description', '')})"
        if tool == "create_manual_order":
            return f"{_confirmed_prefix(a)} logged an order for {g('customer_name', 'a customer')}"
        if tool == "create_product":
            return f"{_confirmed_prefix(a)} added product {g('name', '')} at R{g('price_rands', '?')}"
        if tool == "update_product":
            return f"{_confirmed_prefix(a)} updated product {g('product', '')}"
        if tool == "upsert_supplier":
            return f"{_confirmed_prefix(a)} saved supplier {g('name', '')}"
        if tool == "create_subscription":
            return f"{_confirmed_prefix(a)} set up a {g('cadence', 'recurring')} standing order for {g('customer_name', 'a customer')}"
    except Exception:
        return None
    return None


@router.get("/{tenant_id}/admin/agent-activity")
async def admin_agent_activity(tenant_id: str, limit: int = 60):
    """Observe the agent: recent tool calls + local/cloud routing decisions (from telemetry).
    Tool-call events are narrated into business-readable text where a template exists; anything
    without one still renders via the raw `args` (graceful fallback, not blocking on 100%
    template coverage)."""
    db = service._client()
    try:
        rows = (db.table("vula_reasoning_telemetry")
                .select("system,task,outcome,reason,escalated,extra,created_at")
                .eq("tenant_id", tenant_id)
                .in_("system", ["vula-agent-tool", "vula-llm-router"])
                .order("created_at", desc=True).limit(min(limit, 200)).execute().data or [])
    except Exception as exc:
        return {"events": [], "error": f"{exc} (run migration 045?)"}
    events = []
    for r in rows:
        if r.get("system") == "vula-agent-tool":
            tool = r.get("task")
            args = (r.get("extra") or {}).get("args") or {}
            events.append({"kind": "tool", "who": r.get("outcome"), "tool": tool,
                           "args": args, "narrated": _narrate(tool, args),
                           "consequential": tool in _CONSEQUENTIAL_TOOLS,
                           "at": r.get("created_at")})
        else:  # router decision
            events.append({"kind": "route", "task": r.get("task"), "outcome": r.get("outcome"),
                           "backend": (r.get("extra") or {}).get("backend") or r.get("reason"),
                           "escalated": r.get("escalated"), "reason": r.get("reason"), "at": r.get("created_at")})
    return {"events": events}


# ── Sales rep dashboard: contacts, reminders, call sheet ────────────────────────
# Thin admin endpoints backing the new "My Work" rep dashboard (2026-08). Each accepts an
# optional rep-scoping param (created_by/filed_by/rep_phone) — the frontend already resolves
# its own phone from the server-verified /v1/team/{tid}/me lookup, so this is a display filter,
# not a new auth boundary (route-level access is still tenant_admin_guard, same as every other
# /admin/ route).

@router.get("/{tenant_id}/admin/contacts")
async def admin_list_contacts(tenant_id: str, created_by: str = "", search: str = ""):
    q = (service._client().table("commerce_contacts").select("*").eq("tenant_id", tenant_id))
    if created_by:
        q = q.eq("created_by", created_by)
    if search:
        q = q.ilike("name", f"%{search}%")
    rows = q.order("created_at", desc=True).limit(200).execute().data or []
    return {"contacts": rows}


class ContactIn(BaseModel):
    name: str
    phone: str = ""
    email: str = ""
    company: str = ""
    title: str = ""
    notes: str = ""
    created_by: str = ""


@router.post("/{tenant_id}/admin/contacts")
async def admin_upsert_contact(tenant_id: str, body: ContactIn):
    from uuid import uuid4
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    phone = "".join(c for c in (body.phone or "") if c.isdigit())
    if phone.startswith("0"):
        phone = "27" + phone[1:]
    if not phone:
        phone = f"nophone-{uuid4().hex[:10]}"
    row = {"tenant_id": tenant_id, "phone": phone, "name": name,
           "email": body.email or None, "company": body.company or None,
           "title": body.title or None, "notes": body.notes or None,
           "source": "dashboard", "created_by": body.created_by or None}
    res = (service._client().table("commerce_contacts")
           .upsert(row, on_conflict="tenant_id,phone").execute())
    return {"saved": True, "contact": (res.data or [row])[0]}


@router.get("/{tenant_id}/admin/reminders")
async def admin_list_reminders(tenant_id: str, created_by: str = "", status: str = "open"):
    q = (service._client().table("vula_reminders").select("id,text,due_at,status,created_at")
         .eq("tenant_id", tenant_id))
    if created_by:
        q = q.eq("created_by", created_by)
    if status != "all":
        q = q.eq("status", status)
    rows = q.order("due_at", desc=False).limit(50).execute().data or []
    return {"reminders": rows}


@router.patch("/{tenant_id}/admin/reminders/{reminder_id}")
async def admin_complete_reminder(tenant_id: str, reminder_id: str):
    res = (service._client().table("vula_reminders")
           .update({"status": "done", "completed_at": service._now()})
           .eq("id", reminder_id).eq("tenant_id", tenant_id).execute())
    if not res.data:
        raise HTTPException(status_code=404, detail="reminder not found")
    return {"completed": True}


@router.get("/{tenant_id}/admin/call-sheet")
async def admin_get_call_sheet(tenant_id: str, rep_phone: str):
    """The calling rep's own standing config + current open call sheet — resolves strictly by
    rep_phone (their vula_team_members.whatsapp), never returns another rep's data."""
    if not rep_phone:
        raise HTTPException(status_code=400, detail="rep_phone is required")
    from vula.commerce import call_sheet
    rows = (service._client().table("vula_team_members").select(
                "whatsapp,name,call_sheet_recipient_email,call_sheet_recipient_phone,"
                "call_sheet_channel,call_sheet_day_of_week,call_sheet_hour,call_sheet_minute,"
                "call_sheet_last_sent_at")
            .eq("tenant_id", tenant_id).eq("whatsapp", rep_phone).limit(1).execute().data or [])
    config = rows[0] if rows else {}
    sheet = call_sheet.get_or_create_open_call_sheet(tenant_id, rep_phone)
    return {"config": config, "entries": sheet.get("entries") or []}


class CallSheetConfigIn(BaseModel):
    rep_phone: str
    recipient_email: Optional[str] = None
    recipient_phone: Optional[str] = None
    channel: Optional[str] = None
    day_of_week: Optional[int] = None
    hour: Optional[int] = None
    minute: Optional[int] = None
    budget_rands: Optional[float] = None


@router.post("/{tenant_id}/admin/call-sheet/configure")
async def admin_configure_call_sheet(tenant_id: str, body: CallSheetConfigIn):
    upd: Dict[str, Any] = {}
    if body.recipient_email is not None:
        upd["call_sheet_recipient_email"] = body.recipient_email or None
    if body.recipient_phone is not None:
        upd["call_sheet_recipient_phone"] = body.recipient_phone or None
    if body.channel is not None:
        if body.channel not in ("email", "whatsapp", "both"):
            raise HTTPException(status_code=400, detail="channel must be email, whatsapp, or both")
        upd["call_sheet_channel"] = body.channel
    if body.day_of_week is not None:
        if not 0 <= body.day_of_week <= 6:
            raise HTTPException(status_code=400, detail="day_of_week must be 0 (Monday)..6 (Sunday)")
        upd["call_sheet_day_of_week"] = body.day_of_week
    if body.hour is not None:
        upd["call_sheet_hour"] = body.hour
    if body.minute is not None:
        upd["call_sheet_minute"] = body.minute
    if body.budget_rands is not None:
        upd["expense_budget_cents"] = round(body.budget_rands * 100)
    if not upd:
        raise HTTPException(status_code=400, detail="nothing to update")
    res = (service._client().table("vula_team_members").update(upd)
           .eq("tenant_id", tenant_id).eq("whatsapp", body.rep_phone).execute())
    if not res.data:
        raise HTTPException(status_code=404, detail="rep not found")
    return {"saved": True, "config": res.data[0]}


class CallSheetUpdateIn(BaseModel):
    rep_phone: str
    instruction: str
    confirm: bool = False


@router.post("/{tenant_id}/admin/call-sheet/update")
async def admin_update_call_sheet(tenant_id: str, body: CallSheetUpdateIn):
    from vula.commerce import call_sheet
    instruction = (body.instruction or "").strip()
    if not instruction:
        raise HTTPException(status_code=400, detail="instruction is required")
    row = call_sheet.get_or_create_open_call_sheet(tenant_id, body.rep_phone)
    parsed = await call_sheet.parse_update_instruction(row.get("entries") or [], instruction)
    if parsed.get("error"):
        return parsed
    if not body.confirm:
        preview = (f"Add: \"{parsed['text']}\"" if parsed["action"] == "add"
                   else f"Change entry to: \"{parsed['text']}\"" if parsed["action"] == "edit"
                   else "Remove that entry")
        return {"preview": True, "change": preview}
    updated = call_sheet.apply_edit(tenant_id, body.rep_phone, parsed["action"],
                                     parsed.get("entry_id"), parsed.get("text"))
    return {"applied": True, "entries": updated.get("entries") or []}


class TeachRequest(BaseModel):
    question: str
    answer: str


@router.post("/{tenant_id}/admin/agent-teach")
async def admin_agent_teach(tenant_id: str, body: TeachRequest):
    """Teach the agent: store a correct answer as authoritative KB reference so future
    replies use it. Feeds the same retrieval the assistants ground on."""
    q, a = (body.question or "").strip(), (body.answer or "").strip()
    if not q or not a:
        raise HTTPException(status_code=400, detail="question and answer are required")
    try:
        from vula.ingestion.pipeline import VulaIngestionPipeline
        import hashlib
        doc_id = "taught_" + hashlib.sha1(q.lower().encode()).hexdigest()[:16]
        await VulaIngestionPipeline(tenant_id=tenant_id).ingest_text(
            content=f"Q: {q}\nA: {a}", filename="owner-taught.txt", doc_id=doc_id, source_type="reference")
    except Exception as exc:
        return {"error": f"could not teach: {exc}"}
    try:
        from core.reasoning_telemetry import emit
        emit(system="vula-agent-teach", task="correction", tenant_id=tenant_id, outcome="learned")
    except Exception:
        pass
    return {"ok": True, "learned": q}


@router.post("/{tenant_id}/admin/agent-unteach")
async def admin_agent_unteach(tenant_id: str, body: dict):
    """Remove something previously taught (by the same question)."""
    q = (body or {}).get("question", "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="question is required")
    import hashlib
    doc_id = "taught_" + hashlib.sha1(q.lower().encode()).hexdigest()[:16]
    try:
        from vula.ingestion.pipeline import VulaIngestionPipeline
        await VulaIngestionPipeline(tenant_id=tenant_id).store.delete_document(tenant_id, doc_id)
    except Exception as exc:
        return {"error": str(exc)}
    return {"ok": True, "removed": q}


@router.get("/{tenant_id}/admin/persona")
async def admin_get_persona(tenant_id: str):
    """Current + suggested voice/tone (persona_prompt, migration 116+119)."""
    from vula.api import tenants as _tenants
    cfg = _tenants.get_config(tenant_id, fresh=True)
    return {
        "persona_prompt": cfg.get("persona_prompt") or "",
        "persona_prompt_suggested": cfg.get("persona_prompt_suggested") or "",
        "persona_prompt_suggested_at": cfg.get("persona_prompt_suggested_at"),
    }


class PersonaPatch(BaseModel):
    persona_prompt: str


@router.patch("/{tenant_id}/admin/persona")
async def admin_set_persona(tenant_id: str, body: PersonaPatch):
    """Owner sets/edits how Vula should sound — read by commerce_admin/commerce_assistant's
    persona_block. Accepting a suggestion is just a PATCH with the suggested text as the value;
    also clears the pending suggestion either way so old suggestions don't linger stale."""
    from vula.api import tenants as _tenants
    try:
        service._client().table("vula_tenant_config").update({
            "persona_prompt": body.persona_prompt.strip(),
            "persona_prompt_suggested": None,
            "persona_prompt_suggested_at": None,
        }).eq("tenant_id", tenant_id).execute()
    except Exception as exc:
        return {"error": f"{exc} (run migration 116/119?)"}
    _tenants.invalidate(tenant_id)
    return {"ok": True, "persona_prompt": body.persona_prompt.strip()}


@router.post("/{tenant_id}/admin/persona/analyze")
async def admin_analyze_persona(tenant_id: str):
    """Analyse the owner/staff's own WhatsApp replies (role='agent') and suggest a persona."""
    from vula.commerce import voice_profile
    result = await voice_profile.analyze_voice(tenant_id)
    if "error" in result:
        return result
    try:
        from core.reasoning_telemetry import emit
        emit(system="vula-agent-teach", task="voice_profile", tenant_id=tenant_id, outcome="suggested")
    except Exception:
        pass
    return {"ok": True, **result}


class PageAiDraftRequest(BaseModel):
    content: list = []
    description: str = ""
    mood: str = ""   # optional curated MOOD_PRESETS key — see page_copy.MOOD_PRESETS


@router.post("/{tenant_id}/admin/pages/ai-draft")
async def admin_ai_draft_page_copy(tenant_id: str, body: PageAiDraftRequest):
    """AI-drafted real copy for a page-builder template's marketing-copy fields, grounded in the
    tenant's real business_type/invoice-and-order settings/persona_prompt/ingested KB (if any)
    plus an optional one-line description. Never invents contact facts (phone/email/address come
    only from real settings) and never touches Testimonials (see page_copy.py). Proposal only —
    the dashboard drops the returned content into whatever page is currently open in the editor;
    Save draft / Publish there is still the explicit review/accept step, same as any manual edit.

    If `mood` is a recognized preset key, the response also includes a `theme` suggestion (font/
    color/motion) — a pure deterministic lookup, no extra LLM call — and motion (`animation`
    props) is assigned across the drafted content accordingly. Motion is always assigned with a
    restrained "subtle" default even without a mood, per the same design-restraint principle.

    Before motion is assigned, every draft always gets a bounded polish pass
    (page_copy.polish_page) over the ASSEMBLED content — one mandatory holistic "does this feel
    specific and finished, not generic" pass, plus one further targeted fix pass if a
    deterministic check (Hero title too long, a thin/unfinished section, weak button-text
    contrast against the resolved accent color) still finds something afterward. Hard-capped at
    2 total polish LLM calls — bounded, not an unbounded refine loop. Runs here (not inside
    generate_page_copy) because the accent-contrast check needs the resolved theme, which isn't
    known until after copy generation."""
    if not body.content:
        raise HTTPException(status_code=400, detail="content is required")
    from vula.commerce import page_copy
    result = await page_copy.generate_page_copy(tenant_id, body.content, description=body.description)
    if "error" in result:
        return result
    theme = page_copy.suggest_theme(mood=body.mood or None)
    result["content"] = await page_copy.polish_page(result["content"], theme)
    result["content"] = page_copy.assign_motion(result["content"], theme["motion_intensity"])
    if body.mood:
        result["theme"] = theme
    return result


class PageAiRefineRequest(BaseModel):
    content: list = []
    instruction: str = ""
    add_features: list = []   # feature keys from analyze-reference's features_found, e.g. ["booking", "faq"]


@router.post("/{tenant_id}/admin/pages/ai-refine")
async def admin_ai_refine_page_copy(tenant_id: str, body: PageAiRefineRequest):
    """Adjust the CURRENT draft (already AI-generated or manually edited) per a specific
    free-text instruction from the owner — e.g. "make the headline punchier". Same safety
    guarantees as ai-draft: contact facts never LLM-written, Testimonials never touched,
    per-field defensive merge (see page_copy.refine_page_copy). Proposal only, same review
    posture as ai-draft — nothing is saved here.

    If `add_features` is given (feature keys from analyze-reference's own response), each
    recognized one (page_copy.FEATURE_BLOCK_MAP) is appended as a new block via
    page_copy.add_block, then filled with real business-specific copy via a scoped internal
    instruction — the SAME refine_page_copy pipeline, so the rest of the page is never touched.
    Unrecognized/unsupported feature keys are silently skipped here — the dashboard already knows
    which are unsupported from analyze-reference's response and is responsible for telling the
    tenant, not this endpoint."""
    if not body.content and not body.add_features:
        raise HTTPException(status_code=400, detail="content is required")
    from vula.commerce import page_copy
    content = list(body.content)
    added_ids = []
    for feature in body.add_features:
        block_type = page_copy.FEATURE_BLOCK_MAP.get(feature)
        if not block_type:
            continue
        content = page_copy.add_block(content, block_type)
        added_ids.append(content[-1]["props"]["id"])

    if added_ids:
        instruction = ("Write real, business-specific content for the new section(s) you just "
                        f"added (block ids: {', '.join(added_ids)}). Leave every other section "
                        "exactly as it already is.")
        if body.instruction and body.instruction.strip():
            instruction += f" Also: {body.instruction.strip()}"
        return await page_copy.refine_page_copy(tenant_id, content, instruction)

    if not body.instruction or not body.instruction.strip():
        raise HTTPException(status_code=400, detail="instruction is required")
    return await page_copy.refine_page_copy(tenant_id, content, body.instruction)


class PageAnalyzeReferenceRequest(BaseModel):
    urls: list = []


@router.post("/{tenant_id}/admin/pages/analyze-reference")
async def admin_analyze_reference(tenant_id: str, body: PageAnalyzeReferenceRequest):
    """Safely fetch and analyze up to 3 tenant-supplied "sites I like" URLs for known features
    (booking/faq/pricing/shop_grid/etc — see reference_url.KNOWN_FEATURES), constrained to a
    fixed vocabulary so the model classifies rather than invents. Text/structure analysis only —
    no visual/screenshot analysis (no headless browser exists in this deployment; see
    reference_url.py's module docstring). Uses a purpose-built, SSRF-hardened fetcher — NOT
    web_scraper.py's WebFetcher, which has no protection against a tenant-supplied URL resolving
    to an internal/cloud-metadata address. Never persists anything; the dashboard uses the
    response to ask the tenant which features to add via ai-refine's add_features."""
    if not body.urls:
        raise HTTPException(status_code=400, detail="urls is required")
    from vula.commerce import reference_url
    return await reference_url.analyze_reference_urls(body.urls)


@router.post("/{tenant_id}/admin/pages/design-reference")
async def admin_design_reference(tenant_id: str, body: dict):
    """Analyze a tenant-uploaded reference image (a look they like — a website screenshot, photo,
    or mood board) and suggest a storefront theme. Mirrors admin_clone_invoice_style's shape
    (fetch already-uploaded file_url, downscale + re-encode as base64 JPEG, call the strong cloud
    vision model) with one deliberate accuracy change: the vision model's ONLY job is picking the
    closest MOOD_PRESETS bucket (a constrained classification, not open-ended generation) — the
    actual accent color is computed deterministically from the image's own pixels
    (page_copy.extract_dominant_color, zero LLM involvement) and snapped to that preset's own
    pre-vetted, contrast-checked shortlist. Proposal only — nothing is saved here."""
    import asyncio
    import base64
    import io
    import json as _json
    import re as _re
    from vula.commerce import page_copy

    file_url = (body or {}).get("file_url")
    if not file_url:
        raise HTTPException(status_code=400, detail="file_url is required")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(file_url)
            resp.raise_for_status()
            data = resp.content
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Couldn't fetch that file: {exc}")
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File is too large (max 15MB)")

    from PIL import Image
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img.thumbnail((1600, 1600))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        img_b64 = base64.b64encode(buf.getvalue()).decode()
    except Exception as exc:
        log.warning("Design reference: couldn't read uploaded file for %s: %s", tenant_id, exc)
        raise HTTPException(status_code=400, detail="Couldn't read that image — try a different file")

    # Deterministic, non-LLM color extraction — happens regardless of whether the vision call
    # below succeeds, so a flaky/unavailable vision model degrades to "preset default color"
    # rather than blocking the whole feature.
    dominant_hex = page_copy.extract_dominant_color(img)

    from core.llm_router import resolve_cloud_vision_route
    cloud = resolve_cloud_vision_route()
    mood = None
    style_note = ""
    if cloud:
        model, api_key, api_base = cloud
        try:
            import litellm
            litellm.drop_params = True
            response = await asyncio.wait_for(litellm.acompletion(
                model=model,
                messages=[
                    {"role": "system", "content": page_copy.DESIGN_REFERENCE_PROMPT},
                    {"role": "user", "content": [
                        {"type": "text", "text": "Classify this reference image's design mood."},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                    ]},
                ],
                temperature=0.1, max_tokens=200, api_key=api_key, api_base=api_base,
            ), timeout=30)
            content = response.choices[0].message.content or "{}"
            content = _re.sub(r"<think>.*?</think>", "", content, flags=_re.DOTALL)
            content = _re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=_re.MULTILINE).strip()
            try:
                raw = _json.loads(content)
            except Exception:
                m = _re.search(r"\{.*\}", content, _re.DOTALL)
                raw = _json.loads(m.group(0)) if m else {}
            cleaned = page_copy.clean_design_reference(raw if isinstance(raw, dict) else {})
            mood = cleaned.get("mood")
            style_note = cleaned.get("style_note", "")
        except Exception as exc:
            log.warning("Design reference: vision call failed for %s: %s", tenant_id, exc)
            # Fall through with mood=None — suggest_theme() defaults sanely either way.

    theme = page_copy.suggest_theme(mood=mood, computed_dominant_hex=dominant_hex)
    return {"theme": theme, "style_note": style_note}


class MarketingRequest(BaseModel):
    kind: str = "specials"          # specials | product | promo | broadcast
    topic: str = ""                 # product name (for 'product') or the offer/subject
    tone: str = ""
    variants: int = 2


@router.post("/{tenant_id}/admin/marketing/generate")
async def admin_marketing_generate(tenant_id: str, body: MarketingRequest):
    """Generate grounded marketing copy (specials post, product description, promo, broadcast)."""
    from vula.commerce import marketing
    return await marketing.generate(
        tenant_id, kind=body.kind, topic=body.topic, tone=body.tone, variants=body.variants,
    )


# ── Saved marketing copy library (P2.1, 2026-07-19) ──────────────────────────

@router.get("/{tenant_id}/admin/marketing/saved")
async def admin_list_saved_copy(tenant_id: str):
    try:
        rows = (service._client().table("commerce_saved_copy").select("*")
                .eq("tenant_id", tenant_id).order("created_at", desc=True).limit(100).execute().data or [])
    except Exception as exc:
        log.debug("saved copy list skipped (run migration 075?): %s", exc)
        rows = []
    return {"saved": rows}


@router.post("/{tenant_id}/admin/marketing/saved")
async def admin_save_copy(tenant_id: str, body: dict):
    b = body or {}
    text = (b.get("body") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="body required")
    row = {"tenant_id": tenant_id, "kind": b.get("kind") or "broadcast",
           "topic": b.get("topic"), "tone": b.get("tone"), "body": text}
    res = service._client().table("commerce_saved_copy").insert(row).execute()
    return res.data[0] if res.data else {"error": "insert failed"}


@router.delete("/{tenant_id}/admin/marketing/saved/{copy_id}")
async def admin_delete_saved_copy(tenant_id: str, copy_id: str):
    service._client().table("commerce_saved_copy").delete() \
        .eq("tenant_id", tenant_id).eq("id", copy_id).execute()
    return {"deleted": copy_id}


@router.get("/{tenant_id}/admin/finances/insights")
async def admin_finances_insights(tenant_id: str, days: int = 30, explain: bool = False):
    """Plain-language financial insights: P&L, VAT collected, receivables, top customers.
    Deterministic numbers always; a short LLM narrative when explain=true."""
    from vula.commerce import finances
    data = await finances.insights(tenant_id, days=days)
    if explain:
        data["summary"] = await finances.narrate(tenant_id, data)
    return data


# ── Bank reconciliation (read the statement → match invoices/expenses) ─────────
class BankSettingsIn(BaseModel):
    password: str            # statement PDF password (e.g. ID number) — stored encrypted
    bank: str = "Capitec"


@router.post("/{tenant_id}/admin/bank/settings")
async def admin_bank_settings(tenant_id: str, body: BankSettingsIn):
    """Store the (encrypted) statement password so Vula can auto-unlock weekly statements."""
    from vula.commerce import bank_rec
    try:
        bank_rec.set_statement_password(tenant_id, body.password, body.bank)
    except Exception as exc:
        return {"error": f"{exc} (connect email + run migration 057 first?)"}
    return {"ok": True, "bank": body.bank}


class BankStatementIn(BaseModel):
    pdf_base64: str
    password: Optional[str] = None
    filename: Optional[str] = None


@router.post("/{tenant_id}/admin/bank/statement")
async def admin_bank_statement(tenant_id: str, body: BankStatementIn):
    """Upload a statement PDF (base64) → decrypt, parse, reconcile. Returns immediately —
    reconciliation runs server-side (minutes); poll GET admin/bank/reconciliation. The owner
    gets a WhatsApp summary + review of anything unallocated."""
    import base64 as _b64, os, tempfile
    from pathlib import Path
    from vula.commerce import bank_rec
    data = body.pdf_base64.split(",", 1)[-1] if body.pdf_base64.startswith("data:") else body.pdf_base64
    try:
        raw = _b64.b64decode(data)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid pdf_base64")
    fd, tmp = tempfile.mkstemp(suffix=".pdf")
    with os.fdopen(fd, "wb") as f:
        f.write(raw)

    async def _run():
        try:
            return await bank_rec.ingest_statement(tenant_id, Path(tmp), password=body.password,
                                                   source_file=body.filename or "upload.pdf")
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass

    _statement_job(tenant_id, _run())
    return {"processing": True,
            "note": "Statement received — reconciling now. Check back in a few minutes."}


def _statement_job(tenant_id: str, coro, notify: bool = True) -> None:
    """Run a statement-reconciliation coroutine to completion (statements take minutes —
    longer than the HTTP gateway allows), then WhatsApp the owner the summary and start
    the review loop for anything Vula couldn't allocate."""
    from vula.commerce.background_tasks import run_background

    async def _notify(rec):
        if not (notify and isinstance(rec, dict) and not rec.get("error")):
            return
        from vula.commerce import bank_review
        await bank_review.kickoff(tenant_id, rec)

    run_background(tenant_id, "bank_statement", coro, notify_builder=_notify)


@router.post("/{tenant_id}/admin/bank/statement/from-text")
async def admin_bank_statement_from_text(tenant_id: str, body: dict):
    """Reconcile a statement from its raw TEXT — same pipeline as the PDF path, minus
    decryption. Body: {text, filename?, background?=true}. Statements take minutes, so by
    default this returns immediately and the job continues server-side (poll
    GET admin/bank/reconciliation); the owner also gets a WhatsApp summary + review."""
    from vula.commerce import bank_rec
    text = (body or {}).get("text") or ""
    if not text.strip():
        raise HTTPException(status_code=400, detail="text required")

    async def _run():
        txns = await bank_rec.extract_transactions(text)
        if not txns:
            return {"error": "no transactions found in the text"}
        return await bank_rec.reconcile(tenant_id, txns,
                                        source_file=(body or {}).get("filename") or "pasted-statement")

    if (body or {}).get("background", True):
        _statement_job(tenant_id, _run())
        return {"processing": True,
                "note": "Statement is being reconciled — check the Bank tab in a few minutes."}
    return await _run()


@router.post("/{tenant_id}/admin/bank/statement/from-upload")
async def admin_bank_statement_from_upload(tenant_id: str, body: dict):
    """Reconcile a statement PDF that was already uploaded (e.g. WhatsApp'd before statement
    detection existed). Body: {filename} — looked up in the tenant's upload directory."""
    from pathlib import Path
    from config import settings as _s
    from vula.commerce import bank_rec
    fname = ((body or {}).get("filename") or "").replace("/", "_").replace("\\", "_")
    if not fname:
        raise HTTPException(status_code=400, detail="filename required")
    path = Path(_s.upload_dir) / tenant_id / fname
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"no uploaded file named {fname}")
    _statement_job(tenant_id, bank_rec.ingest_statement(
        tenant_id, path, source_file=fname))
    return {"processing": True, "note": "Reconciling — check the Bank tab in a few minutes."}


@router.post("/{tenant_id}/admin/bank/recategorize")
async def admin_bank_recategorize(tenant_id: str):
    """Re-run AI categorisation over every transaction still waiting for input (e.g. after a
    failed batch call left them on the default account). Learned rules apply first; anything
    the model still can't place stays flagged for the owner."""
    from vula.commerce import accounting, bank_review
    db = service._client()
    rows = bank_review.pending_txns(tenant_id)
    if not rows:
        return {"reviewed": 0, "updated": 0, "still_pending": 0}
    accounts = accounting.ensure_chart(tenant_id)
    acc_map = {a["code"]: a for a in accounts}
    txns = [{"description": r.get("description"), "reference": r.get("reference"),
             "amount_cents": r.get("amount_cents"), "direction": r.get("direction")} for r in rows]
    cats = await accounting.categorize_batch(tenant_id, txns, accounts)
    vat_reg = accounting.is_vat_registered(tenant_id)
    updated = 0
    for r, c in zip(rows, cats):
        if c["source"] == "default":
            continue        # still unsure — stays in the review queue
        acct = acc_map.get(c["account_code"])
        db.table("commerce_bank_transactions").update({
            "account_code": c["account_code"],
            "vat_cents": accounting.vat_for(acct, int(r.get("amount_cents") or 0), vat_reg),
            "vat_treatment": (acct or {}).get("vat_treatment"),
            "categorized_by": c["source"],
        }).eq("id", r["id"]).execute()
        updated += 1
    return {"reviewed": len(rows), "updated": updated, "still_pending": len(rows) - updated}


@router.post("/{tenant_id}/admin/bank/review/start")
async def admin_bank_review_start(tenant_id: str):
    """Start the WhatsApp review loop: sends the owner the first unallocated transaction as a
    question; their replies allocate + teach, one at a time."""
    from vula.commerce import bank_review
    from vula.integrations.notify import notify_team
    q = bank_review.start_review(tenant_id)
    if not q:
        return {"started": False, "note": "nothing needs review"}
    sent = await notify_team(tenant_id, "bank_review", q)
    return {"started": True, "sent_to": sent}


@router.get("/{tenant_id}/admin/bank/transactions")
async def admin_bank_transactions(tenant_id: str, status: Optional[str] = None, limit: int = 200):
    db = service._client()
    try:
        q = db.table("commerce_bank_transactions").select("*").eq("tenant_id", tenant_id)
        if status == "needs_input":
            q = q.in_("categorized_by", ["default", "asked"])   # Vula unsure — owner to allocate
        elif status:
            q = q.eq("match_status", status)
        rows = q.order("txn_date", desc=True).limit(min(limit, 500)).execute().data or []
    except Exception as exc:
        return {"transactions": [], "error": f"{exc} (run migration 057?)"}
    return {"transactions": rows}


@router.get("/{tenant_id}/admin/bank/reconciliation")
async def admin_bank_reconciliation(tenant_id: str):
    """Summary: money in/out from statements, match counts, and invoices sent-vs-paid."""
    db = service._client()
    try:
        rows = (db.table("commerce_bank_transactions").select("amount_cents,direction,match_status,categorized_by")
                .eq("tenant_id", tenant_id).limit(5000).execute().data or [])
    except Exception as exc:
        return {"error": f"{exc} (run migration 057?)"}
    money_in = sum(int(r.get("amount_cents") or 0) for r in rows if r.get("direction") == "in")
    money_out = sum(int(r.get("amount_cents") or 0) for r in rows if r.get("direction") == "out")
    matched = len([r for r in rows if r.get("match_status") == "matched"])
    unmatched_in = len([r for r in rows if r.get("direction") == "in" and r.get("match_status") == "unmatched"])
    unmatched_out = len([r for r in rows if r.get("direction") == "out" and r.get("match_status") == "unmatched"])
    needs_input = len([r for r in rows if r.get("categorized_by") in ("default", "asked")])   # Vula unsure → owner allocates
    try:
        inv = (db.table("commerce_invoices").select("status,total_cents,doc_type")
               .eq("tenant_id", tenant_id).limit(3000).execute().data or [])
        inv = [i for i in inv if (i.get("doc_type") or "invoice") == "invoice"]
    except Exception:
        inv = []
    sent = sum(int(i.get("total_cents") or 0) for i in inv if i.get("status") in ("sent", "overdue"))
    paid = sum(int(i.get("total_cents") or 0) for i in inv if i.get("status") == "paid")
    return {"money_in_cents": money_in, "money_out_cents": money_out, "matched": matched,
            "unmatched_credits": unmatched_in, "unmatched_debits": unmatched_out,
            "needs_input": needs_input,
            "invoices_sent_unpaid_cents": sent, "invoices_paid_cents": paid, "txn_count": len(rows)}


class BankMatchIn(BaseModel):
    action: str = "match"          # match | ignore | expense
    invoice_id: Optional[str] = None
    order_id: Optional[str] = None
    category: Optional[str] = None


@router.post("/{tenant_id}/admin/bank/transactions/{txn_id}/match")
async def admin_bank_match(tenant_id: str, txn_id: str, body: BankMatchIn):
    """One-tap review of an unmatched transaction: match to an invoice or order (mark paid),
    log as an expense, or ignore."""
    db = service._client()
    rows = (db.table("commerce_bank_transactions").select("*")
            .eq("tenant_id", tenant_id).eq("id", txn_id).limit(1).execute().data or [])
    if not rows:
        raise HTTPException(status_code=404, detail="transaction not found")
    txn = rows[0]
    patch = {}
    if body.action == "match" and body.invoice_id:
        await service.update_invoice_status(tenant_id, body.invoice_id, "paid")
        patch = {"matched_invoice_id": body.invoice_id, "match_status": "matched"}
    elif body.action == "match" and body.order_id:
        orows = (db.table("commerce_orders").select("id,display_id,customer_name,customer_phone,total_cents")
                 .eq("tenant_id", tenant_id).eq("id", body.order_id).limit(1).execute().data or [])
        if not orows:
            raise HTTPException(status_code=404, detail="order not found")
        o = orows[0]
        await service.update_order_status(o["id"], "paid")
        try:
            from vula.api.yoco import _notify_order_paid
            await _notify_order_paid(tenant_id, o.get("display_id") or o["id"], o["id"],
                                     o.get("customer_phone"), o.get("customer_name") or "",
                                     int(o.get("total_cents") or 0))
        except Exception as exc:
            log.warning("order-paid notify failed for manual bank match: %s", exc)
        patch = {"matched_order_id": body.order_id, "match_status": "matched"}
    elif body.action == "expense":
        from uuid import uuid4
        exp = {"id": str(uuid4()), "tenant_id": tenant_id, "date": txn.get("txn_date"),
               "category": body.category or "other", "description": txn.get("description") or "Bank debit",
               "amount_cents": int(txn.get("amount_cents") or 0)}
        try:
            res = db.table("commerce_expenses").insert(exp).execute()
            patch = {"matched_expense_id": (res.data or [exp])[0].get("id"), "match_status": "matched"}
        except Exception as exc:
            return {"error": str(exc)}
    elif body.action == "ignore":
        patch = {"match_status": "ignored"}
    else:
        raise HTTPException(status_code=400, detail="action must be match|expense|ignore")
    db.table("commerce_bank_transactions").update(patch).eq("id", txn_id).execute()
    return {"ok": True, **patch}


# ── Accounting: chart of accounts, categorisation, reports (Stage 2) ───────────
@router.get("/{tenant_id}/admin/accounts")
async def admin_accounts(tenant_id: str):
    """Tenant's chart of accounts (seeded with SA defaults on first call)."""
    from vula.commerce import accounting
    return {"accounts": accounting.ensure_chart(tenant_id),
            "vat_registered": accounting.is_vat_registered(tenant_id)}


class AccountIn(BaseModel):
    code: str
    name: str
    type: str = "expense"
    vat_treatment: str = "standard"
    deductible: bool = True
    sort: int = 0
    active: bool = True


@router.post("/{tenant_id}/admin/accounts")
async def admin_upsert_account(tenant_id: str, body: AccountIn):
    db = service._client()
    row = {"tenant_id": tenant_id, **body.model_dump()}
    try:
        db.table("commerce_accounts").upsert(row, on_conflict="tenant_id,code").execute()
    except Exception as exc:
        return {"error": f"{exc} (run migration 058?)"}
    return {"account": row}


class CategorizeIn(BaseModel):
    account_code: str


@router.post("/{tenant_id}/admin/bank/transactions/{txn_id}/categorize")
async def admin_categorize_txn(tenant_id: str, txn_id: str, body: CategorizeIn):
    """Set a transaction's account (owner correction) → recompute VAT + LEARN the rule."""
    from vula.commerce import accounting
    db = service._client()
    rows = (db.table("commerce_bank_transactions").select("*")
            .eq("tenant_id", tenant_id).eq("id", txn_id).limit(1).execute().data or [])
    if not rows:
        raise HTTPException(status_code=404, detail="transaction not found")
    txn = rows[0]
    accts = {a["code"]: a for a in accounting.ensure_chart(tenant_id)}
    acc = accts.get(body.account_code)
    if not acc:
        raise HTTPException(status_code=400, detail="unknown account_code")
    vat = accounting.vat_for(acc, int(txn.get("amount_cents") or 0), accounting.is_vat_registered(tenant_id))
    db.table("commerce_bank_transactions").update(
        {"account_code": body.account_code, "vat_cents": vat,
         "vat_treatment": acc.get("vat_treatment"), "categorized_by": "owner"}
    ).eq("id", txn_id).execute()
    accounting.learn_category_rule(tenant_id, txn, body.account_code)   # so similar txns auto-fill
    return {"ok": True, "account_code": body.account_code, "vat_cents": vat}


@router.get("/{tenant_id}/admin/reports/pnl")
async def admin_report_pnl(tenant_id: str, since: Optional[str] = None, until: Optional[str] = None):
    from vula.commerce import accounting
    return accounting.pnl(tenant_id, since, until)


@router.get("/{tenant_id}/admin/reports/vat")
async def admin_report_vat(tenant_id: str, since: Optional[str] = None, until: Optional[str] = None):
    from vula.commerce import accounting
    return accounting.vat_return(tenant_id, since, until)


@router.get("/{tenant_id}/admin/reports/ledger")
async def admin_report_ledger(tenant_id: str, since: Optional[str] = None, until: Optional[str] = None):
    from vula.commerce import accounting
    return {"ledger": accounting.general_ledger(tenant_id, since, until)}


@router.get("/{tenant_id}/admin/reports/ledger.csv")
async def admin_report_ledger_csv(tenant_id: str, since: Optional[str] = None, until: Optional[str] = None):
    """General ledger as CSV — the audit trail her accountant can file from."""
    import csv, io
    from fastapi.responses import Response
    from vula.commerce import accounting
    rows = accounting.general_ledger(tenant_id, since, until)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Date", "Description", "Account", "Direction", "Amount (R)", "VAT (R)", "Reference", "Source"])
    for r in rows:
        w.writerow([r.get("date"), r.get("description"), r.get("account"), r.get("direction"),
                    f"{(r.get('amount_cents') or 0)/100:.2f}", f"{(r.get('vat_cents') or 0)/100:.2f}",
                    r.get("reference") or "", r.get("source") or ""])
    return Response(content=buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="ledger-{tenant_id}.csv"'})


# ── Casual labour: worker register + payment recognition + report ─────────────
class WorkerIn(BaseModel):
    id: Optional[str] = None
    name: str
    id_number: Optional[str] = None
    bank_account: Optional[str] = None
    phone: Optional[str] = None
    rate_rands: Optional[float] = None
    rate_period: str = "daily"
    type: str = "casual"
    default_project: Optional[str] = None
    active: bool = True


@router.get("/{tenant_id}/admin/workers")
async def admin_list_workers(tenant_id: str, all: bool = False):
    from vula.commerce import labour
    return {"workers": labour.list_workers(tenant_id, active_only=not all)}


@router.post("/{tenant_id}/admin/workers")
async def admin_upsert_worker(tenant_id: str, body: WorkerIn):
    from vula.commerce import labour
    try:
        return {"worker": labour.upsert_worker(tenant_id, body.model_dump())}
    except ValueError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        return {"error": f"{exc} (run migration 059?)"}


@router.delete("/{tenant_id}/admin/workers/{worker_id}")
async def admin_delete_worker(tenant_id: str, worker_id: str):
    from vula.commerce import labour
    labour.delete_worker(tenant_id, worker_id)
    return {"removed": worker_id}


class AssignWorkerIn(BaseModel):
    worker_id: str


@router.post("/{tenant_id}/admin/bank/transactions/{txn_id}/worker")
async def admin_assign_worker(tenant_id: str, txn_id: str, body: AssignWorkerIn):
    """Assign a payment to a worker → categorise as casual labour, tag the project, and LEARN it."""
    from vula.commerce import accounting
    db = service._client()
    rows = (db.table("commerce_bank_transactions").select("*")
            .eq("tenant_id", tenant_id).eq("id", txn_id).limit(1).execute().data or [])
    if not rows:
        raise HTTPException(status_code=404, detail="transaction not found")
    txn = rows[0]
    wrows = (db.table("commerce_workers").select("*")
             .eq("tenant_id", tenant_id).eq("id", body.worker_id).limit(1).execute().data or [])
    if not wrows:
        raise HTTPException(status_code=404, detail="worker not found")
    w = wrows[0]
    db.table("commerce_bank_transactions").update(
        {"worker_id": body.worker_id, "project": w.get("default_project"),
         "account_code": "casual_labour", "vat_cents": 0, "vat_treatment": "none",
         "categorized_by": "owner", "match_status": "matched"}).eq("id", txn_id).execute()
    accounting.learn_category_rule(tenant_id, txn, "casual_labour")   # so similar payments auto-file
    return {"ok": True, "worker": w.get("name"), "project": w.get("default_project")}


@router.get("/{tenant_id}/admin/reports/labour")
async def admin_report_labour(tenant_id: str, since: Optional[str] = None,
                              until: Optional[str] = None, project: Optional[str] = None):
    from vula.commerce import labour
    return labour.labour_report(tenant_id, since, until, project)


# ── Expense claims (who paid for the business + reimbursement) ────────────────
class ExpenseIn(BaseModel):
    amount_cents: int
    description: str
    supplier: Optional[str] = None
    date: Optional[str] = None
    vat_cents: Optional[int] = None
    category: Optional[str] = None
    account_code: Optional[str] = None
    project: Optional[str] = None
    section: Optional[str] = None
    paid_by: Optional[str] = None
    paid_by_name: Optional[str] = None
    reimbursable: bool = False
    paid_with: Optional[str] = None
    card_last4: Optional[str] = None
    notes: Optional[str] = None
    due_date: Optional[str] = None   # set → a one-off pending bill, not an expense claim (see below)


class ExpenseAssignIn(BaseModel):
    project: Optional[str] = None
    account_code: Optional[str] = None
    category: Optional[str] = None
    notes: Optional[str] = None
    section: Optional[str] = None
    purpose_category: Optional[str] = None   # 'petrol' | 'clients' | 'accommodation' | 'other'


@router.get("/{tenant_id}/admin/expenses")
async def admin_list_expenses(tenant_id: str, status: Optional[str] = None,
                              reimbursable: Optional[bool] = None, project: Optional[str] = None,
                              since: Optional[str] = None, until: Optional[str] = None,
                              paid_by: Optional[str] = None):
    from vula.commerce import expenses, accounting
    # Real chart-of-accounts categories for the dashboard's category dropdown — deliberately
    # curated/finite (not free text), unlike `project` which is inherently open-ended. Expense
    # accounts only; an expense is never income.
    accounts = [a for a in accounting.ensure_chart(tenant_id) if a.get("type") == "expense"]
    return {"expenses": expenses.list_claims(tenant_id, status=status, reimbursable=reimbursable,
                                             project=project, since=since, until=until, paid_by=paid_by),
            "sections": expenses.known_sections(tenant_id, project=project),
            "projects": expenses.known_projects(tenant_id),
            "categories": [{"code": a["code"], "name": a["name"]} for a in accounts],
            "purpose_categories": list(expenses.PURPOSE_CATEGORIES)}


@router.post("/{tenant_id}/admin/expenses")
async def admin_create_expense(tenant_id: str, body: ExpenseIn):
    if body.due_date:
        # A one-off bill not due yet — status='pending', not an expense CLAIM (which is money
        # already spent, status='submitted' immediately). Same shape recurring_bills.py spawns,
        # just entered by hand instead of by a schedule. Feeds the existing "Payments due" panel
        # and PATCH .../pay action — both already expect exactly this.
        import uuid as _uuid
        db = service._client()
        row = {
            "id": str(_uuid.uuid4()), "tenant_id": tenant_id,
            "date": body.date or service._now()[:10],
            "category": body.category or "other", "description": body.description,
            "amount_cents": body.amount_cents, "supplier": body.supplier,
            "due_date": body.due_date, "status": "pending", "source": "manual",
            "account_code": body.account_code,
        }
        try:
            res = db.table("commerce_expenses").insert(row).execute()
        except Exception as exc:
            return {"error": f"{exc} (run migration 009/074?)"}
        return {"expense": (res.data or [row])[0]}

    from vula.commerce import expenses
    try:
        claim = await expenses.create_claim(
            tenant_id, channel="dashboard",
            **body.model_dump(exclude={"due_date"}))
        return {"expense": claim}
    except Exception as exc:
        return {"error": f"{exc} (run migration 060?)"}


@router.post("/{tenant_id}/admin/expenses/{expense_id}/assign")
async def admin_assign_expense(tenant_id: str, expense_id: str, body: ExpenseAssignIn):
    """Allocate/correct a claim (project + account) and LEARN it for next time."""
    from vula.commerce import expenses
    try:
        return {"expense": expenses.assign(tenant_id, expense_id, **body.model_dump())}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{tenant_id}/admin/expenses/{expense_id}/status")
async def admin_expense_status(tenant_id: str, expense_id: str, body: dict):
    """Move a claim through its lifecycle: approved / reimbursed / paid / rejected."""
    from vula.commerce import expenses
    status = (body or {}).get("status") or ""
    if status not in ("submitted", "approved", "reimbursed", "paid", "rejected", "cancelled"):
        raise HTTPException(status_code=400, detail="bad status")
    return {"expense": expenses.set_status(tenant_id, expense_id, status)}


@router.get("/{tenant_id}/admin/reports/expenses")
async def admin_report_expenses(tenant_id: str, since: Optional[str] = None,
                                until: Optional[str] = None, project: Optional[str] = None):
    from vula.commerce import expenses
    return expenses.report(tenant_id, since=since, until=until, project=project)


@router.get("/{tenant_id}/admin/reports/trial-balance")
async def admin_report_trial_balance(tenant_id: str, since: Optional[str] = None,
                                     until: Optional[str] = None):
    from vula.commerce import ledger
    return ledger.trial_balance(tenant_id, since=since, until=until)


# ── Company cards (whose money paid — drives reimbursement + bank matching) ───
@router.get("/{tenant_id}/admin/cards")
async def admin_list_cards(tenant_id: str):
    from vula.commerce import expenses
    return {"cards": expenses.list_cards(tenant_id, active_only=False)}


@router.post("/{tenant_id}/admin/cards")
async def admin_add_card(tenant_id: str, body: dict):
    from vula.commerce import expenses
    try:
        return {"card": expenses.upsert_card(tenant_id, (body or {}).get("last4") or "",
                                             (body or {}).get("label"))}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        return {"error": f"{exc} (run migration 061?)"}


@router.delete("/{tenant_id}/admin/cards/{card_id}")
async def admin_delete_card(tenant_id: str, card_id: str):
    from vula.commerce import expenses
    expenses.delete_card(tenant_id, card_id)
    return {"removed": card_id}


# ── Historical onboarding: reconstruct past orders from comms (review → commit) ──
class ImportExtractIn(BaseModel):
    text: str


@router.post("/{tenant_id}/admin/import/extract")
async def admin_import_extract(tenant_id: str, body: ImportExtractIn):
    """Draft structured orders from pasted/uploaded history (WhatsApp export, email). For review —
    does NOT create anything."""
    from vula.commerce import importer
    orders = await importer.extract_orders(body.text or "")
    return {"orders": orders, "count": len(orders)}


@router.post("/{tenant_id}/admin/import/invoices/extract")
async def admin_import_invoices_extract(tenant_id: str, body: ImportExtractIn):
    """Draft historical invoices from a pasted export (e.g. Xero CSV). Review only."""
    from vula.commerce import importer
    invoices = await importer.extract_invoices(body.text or "")
    return {"invoices": invoices, "count": len(invoices)}


@router.post("/{tenant_id}/admin/import/invoices/commit")
async def admin_import_invoices_commit(tenant_id: str, body: dict):
    """Register reviewed historical invoices (old numbering) so bank credits can match them."""
    from vula.commerce import importer
    invoices = (body or {}).get("invoices") or []
    if not invoices:
        raise HTTPException(status_code=400, detail="no invoices to commit")
    return importer.commit_invoices(tenant_id, invoices)


@router.post("/{tenant_id}/admin/import/commit")
async def admin_import_commit(tenant_id: str, body: dict):
    """Create real historical orders + contacts from the reviewed drafts."""
    from vula.commerce import importer
    orders = (body or {}).get("orders") or []
    if not orders:
        return {"created": 0, "skipped": 0}
    return await importer.commit_orders(tenant_id, orders)


@router.delete("/{tenant_id}/admin/import/orders")
async def admin_import_undo(tenant_id: str):
    """Undo a historical import — remove all import-tagged orders (safety net)."""
    from vula.commerce.importer import IMPORT_TAG
    db = service._client()
    try:
        rows = (db.table("commerce_orders").select("id").eq("tenant_id", tenant_id)
                .like("delivery_notes", f"{IMPORT_TAG}%").limit(5000).execute().data or [])
        ids = [r["id"] for r in rows]
        for oid in ids:
            db.table("commerce_order_items").delete().eq("order_id", oid).execute()
        if ids:
            db.table("commerce_orders").delete().eq("tenant_id", tenant_id) \
                .like("delivery_notes", f"{IMPORT_TAG}%").execute()
    except Exception as exc:
        return {"error": str(exc)}
    return {"removed": len(ids)}


@router.get("/{tenant_id}/admin/stats")
async def admin_stats(tenant_id: str):
    """Revenue/order stats including invoice summary for the merchant dashboard."""
    from datetime import datetime, timezone
    db = service._client()
    today = datetime.now(timezone.utc).date().isoformat()

    all_orders = db.table("commerce_orders").select(
        "id,total_cents,status,created_at"
    ).eq("tenant_id", tenant_id).execute().data or []

    paid = [o for o in all_orders if o["status"] not in ("pending_payment", "cancelled", "refunded")]
    today_paid = [o for o in paid if o["created_at"][:10] == today]

    # Invoice summary. Must filter doc_type=="invoice" — this table also stores quotes and
    # credit notes, and credit notes need netting OUT of outstanding (they're money owed BACK
    # to the customer, not additional money owed to the tenant) — same pattern already used
    # correctly by admin_customer_statement's balance_due_cents below. Before this fix
    # (2026-08-15), an unaccepted quote counted as "owed to you", and every credit note made
    # the figure go UP instead of down.
    try:
        inv_result = db.table("commerce_invoices").select(
            "total_cents,status,doc_type"
        ).eq("tenant_id", tenant_id).execute().data or []
        credited = sum(i["total_cents"] for i in inv_result if i.get("doc_type") == "credit_note")
        invoice_outstanding = sum(i["total_cents"] for i in inv_result
                                  if i.get("doc_type") == "invoice" and i["status"] in ("sent",)) - credited
        invoice_overdue = sum(i["total_cents"] for i in inv_result
                              if i.get("doc_type") == "invoice" and i["status"] == "overdue")
        invoice_paid_month = sum(i["total_cents"] for i in inv_result
                                 if i.get("doc_type") == "invoice" and i["status"] == "paid")
    except Exception:
        invoice_outstanding = invoice_overdue = invoice_paid_month = 0

    # 7-day revenue trend (oldest → newest) from paid orders.
    from datetime import date as _date, timedelta as _td
    today_d = _date.fromisoformat(today)
    series = []
    for i in range(6, -1, -1):
        d = (today_d - _td(days=i)).isoformat()
        day_orders = [o for o in paid if o["created_at"][:10] == d]
        series.append({"date": d, "revenue_cents": sum(o["total_cents"] for o in day_orders),
                       "orders": len(day_orders)})

    try:
        low_stock = await service.get_low_stock_products(tenant_id, threshold=5)
        low_stock_count = len(low_stock)
    except Exception:
        low_stock_count = 0

    # Escalations waiting on a human — the customer is literally waiting; surface it on Home.
    open_escalations, oldest_escalation = 0, None
    try:
        esc = (db.table("vula_escalations").select("question,created_at")
               .eq("tenant_id", tenant_id).eq("status", "open")
               .order("created_at").limit(10).execute().data or [])
        open_escalations = len(esc)
        if esc:
            oldest_escalation = {"question": (esc[0].get("question") or "")[:120],
                                 "created_at": esc[0].get("created_at")}
    except Exception:
        pass

    # Knowledge pulling through — make what the assistant knows VISIBLE (UI overhaul Phase 3).
    knowledge = {}
    try:
        learned = (db.table("vula_learned_answers").select("source")
                   .eq("tenant_id", tenant_id).limit(1000).execute().data or [])
        knowledge = {
            "learned_answers": len(learned),
            "taught": sum(1 for r in learned if (r.get("source") or "") != "escalation"),
        }
    except Exception:
        pass

    # What the assistant did recently (glass-box AI) — tool calls from the telemetry sink.
    agent_recent = []
    try:
        rows = (db.table("vula_reasoning_telemetry").select("task,outcome,created_at")
                .eq("tenant_id", tenant_id).eq("system", "vula-agent-tool")
                .order("created_at", desc=True).limit(6).execute().data or [])
        agent_recent = [{"tool": r.get("task"), "skill": r.get("outcome"),
                         "at": r.get("created_at")} for r in rows]
    except Exception:
        pass

    return {
        "total_orders": len(paid),
        "total_revenue_cents": sum(o["total_cents"] for o in paid),
        "today_orders": len(today_paid),
        "today_revenue_cents": sum(o["total_cents"] for o in today_paid),
        "pending_payment": len([o for o in all_orders if o["status"] == "pending_payment"]),
        "to_dispatch": len([o for o in all_orders if o["status"] in ("paid", "confirmed", "packing")]),
        "invoice_outstanding_cents": invoice_outstanding,
        "invoice_overdue_cents": invoice_overdue,
        "invoice_paid_month_cents": invoice_paid_month,
        "low_stock_count": low_stock_count,
        "daily_revenue": series,
        "open_escalations": open_escalations,
        "oldest_escalation": oldest_escalation,
        "knowledge": knowledge,
        "agent_recent": agent_recent,
    }


@router.get("/{tenant_id}/admin/reports")
async def admin_reports(tenant_id: str, days: int = 30):
    """Sales-trend + product-performance report (tenant-scoped, integer cents)."""
    from datetime import datetime, timezone, timedelta as _td
    db = service._client()
    today = datetime.now(timezone.utc).date()
    since = (today - _td(days=days - 1)).isoformat()
    orders = (db.table("commerce_orders").select("id,total_cents,status,created_at")
              .eq("tenant_id", tenant_id).gte("created_at", since).execute().data or [])
    paid = [o for o in orders if o["status"] not in ("pending_payment", "cancelled", "refunded")]

    trend = []
    for i in range(days - 1, -1, -1):
        d = (today - _td(days=i)).isoformat()
        day = [o for o in paid if (o.get("created_at") or "")[:10] == d]
        trend.append({"date": d, "revenue_cents": sum(o["total_cents"] for o in day), "orders": len(day)})

    paid_ids = [o["id"] for o in paid]
    items = []
    if paid_ids:
        items = (db.table("commerce_order_items").select("product_name,quantity,total_cents,order_id")
                 .in_("order_id", paid_ids).execute().data or [])
    agg: dict = {}
    for it in items:
        name = it.get("product_name") or "—"
        a = agg.setdefault(name, {"name": name, "units": 0, "revenue_cents": 0})
        a["units"] += int(it.get("quantity") or 0)
        a["revenue_cents"] += int(it.get("total_cents") or 0)
    top = sorted(agg.values(), key=lambda x: x["revenue_cents"], reverse=True)[:10]
    return {"days": days, "revenue_trend": trend, "top_products": top,
            "total_revenue_cents": sum(o["total_cents"] for o in paid), "total_orders": len(paid)}


# ── Products & services catalog (quick-add on invoices/quotes, migration 083) ──
# Distinct from commerce_products (OTH's storefront/stock catalog): no slug/stock, and the
# description may carry a {project} token the invoice UI substitutes at add-time.

@router.get("/{tenant_id}/admin/invoice-items")
async def admin_list_invoice_items(tenant_id: str):
    try:
        rows = (service._client().table("commerce_invoice_items").select("*")
                .eq("tenant_id", tenant_id).eq("active", True)
                .order("sort").order("name").execute().data or [])
    except Exception as exc:
        log.debug("invoice items list skipped (run migration 083?): %s", exc)
        rows = []
    return {"items": rows, "count": len(rows)}


@router.post("/{tenant_id}/admin/invoice-items")
async def admin_create_invoice_item(tenant_id: str, body: dict):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    kind = body.get("kind") if body.get("kind") in ("product", "service") else "service"
    try:
        price_cents = int(body.get("unit_price_cents") or 0)
    except (TypeError, ValueError):
        price_cents = 0
    payload = {
        "tenant_id": tenant_id, "kind": kind, "name": name,
        "description": (body.get("description") or "").strip() or None,
        "unit": (body.get("unit") or "").strip() or None,
        "unit_price_cents": price_cents,
        "sort": int(body.get("sort") or 0),
    }
    try:
        res = service._client().table("commerce_invoice_items").upsert(
            payload, on_conflict="tenant_id,name").execute()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"{exc} (run migration 083?)")
    row = res.data[0] if res.data else payload
    await _ingest_invoice_item_kb(tenant_id, row)
    return row


@router.patch("/{tenant_id}/admin/invoice-items/{item_id}")
async def admin_update_invoice_item(tenant_id: str, item_id: str, body: dict):
    allowed = {"name", "description", "unit", "unit_price_cents", "kind", "active", "sort"}
    update = {k: v for k, v in body.items() if k in allowed}
    if not update:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    res = (service._client().table("commerce_invoice_items").update(update)
           .eq("tenant_id", tenant_id).eq("id", item_id).execute())
    row = res.data[0] if res.data else {"id": item_id, **update}
    if row.get("active", True):
        await _ingest_invoice_item_kb(tenant_id, row)
    return row


@router.delete("/{tenant_id}/admin/invoice-items/{item_id}")
async def admin_delete_invoice_item(tenant_id: str, item_id: str):
    service._client().table("commerce_invoice_items").delete() \
        .eq("tenant_id", tenant_id).eq("id", item_id).execute()
    return {"deleted": item_id}


async def _ingest_invoice_item_kb(tenant_id: str, item: dict) -> None:
    """Mirror a product/service into the tenant's KB so the AI assistant can answer questions
    about rates/services (same pattern as the master code library, projects.py:add_code)."""
    if not item.get("id"):
        return
    try:
        from vula.ingestion.pipeline import VulaIngestionPipeline
        price = f"R{(item.get('unit_price_cents') or 0) / 100:.2f}"
        if item.get("unit"):
            price += f"/{item['unit']}"
        text = f"{item.get('name')} ({item.get('kind', 'service')}) — {price}"
        if item.get("description"):
            text += f"\n{item['description']}"
        doc_id = f"invoice_item_{item['id']}"
        await VulaIngestionPipeline(tenant_id=tenant_id).ingest_text(
            content=text, filename=f"{doc_id}.txt", doc_id=doc_id, source_type="reference",
        )
    except Exception as exc:
        log.debug("invoice item KB ingest skipped for %s: %s", item.get("id"), exc)


# ── Invoice endpoints ─────────────────────────────────────────────────────────

@router.get("/{tenant_id}/admin/invoices")
async def admin_list_invoices(
    tenant_id: str,
    status: Optional[str] = Query(None),
    doc_type: Optional[str] = Query(None),  # invoice | quote | proforma
    direction: str = Query("outbound"),     # outbound | inbound
    supplier_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List all invoices/quotes for a tenant."""
    rows = await service.list_invoices(
        tenant_id, status=status, doc_type=doc_type, direction=direction,
        supplier_id=supplier_id, limit=limit, offset=offset,
    )
    return {"invoices": rows, "count": len(rows)}


@router.post("/{tenant_id}/admin/invoices")
async def admin_create_invoice(tenant_id: str, body: InvoiceCreate):
    """Create an invoice, quote, or proforma. Totals computed server-side."""
    created = await service.create_invoice(tenant_id, body.model_dump(mode="json"))
    return created


@router.get("/{tenant_id}/admin/customers/{phone}/statement")
async def admin_customer_statement(tenant_id: str, phone: str):
    """One customer's invoice history + running balance (P2.4) — what an accountant/customer
    asks for as a 'statement of account'."""
    import re as _re
    digits = _re.sub(r"\D", "", phone or "")
    rows = (service._client().table("commerce_invoices").select("*")
            .eq("tenant_id", tenant_id).eq("direction", "outbound")
            .order("created_at").execute().data or [])
    mine = [r for r in rows if _re.sub(r"\D", "", r.get("customer_phone") or "") == digits]
    invoiced = sum(r.get("total_cents") or 0 for r in mine if r.get("doc_type") == "invoice")
    paid = sum(r.get("total_cents") or 0 for r in mine if r.get("doc_type") == "invoice" and r.get("status") == "paid")
    credited = sum(r.get("total_cents") or 0 for r in mine if r.get("doc_type") == "credit_note")
    return {
        "phone": digits, "customer_name": (mine[0].get("customer_name") if mine else None),
        "invoices": mine,
        "total_invoiced_cents": invoiced, "total_paid_cents": paid, "total_credited_cents": credited,
        "balance_due_cents": invoiced - paid - credited,
    }


@router.patch("/{tenant_id}/admin/invoices/{invoice_id}")
async def admin_update_invoice(tenant_id: str, invoice_id: str, body: dict):
    db = service._client()
    allowed = {
        "status", "customer_name", "customer_email", "customer_phone",
        "customer_address", "line_items", "subtotal_cents", "vat_rate",
        "vat_cents", "total_cents", "due_date", "notes", "payment_method",
        "yoco_checkout_id", "yoco_payment_id", "paid_at",
        "discount_cents", "deposit_cents", "sent_channel", "requires_approval",
    }
    update = {k: v for k, v in body.items() if k in allowed}
    if not update:
        raise HTTPException(status_code=400, detail="No valid fields")

    # A status change is routed through update_invoice_status so a transition to "paid"
    # always stamps paid_at server-side AND posts to the general ledger — a raw field write
    # here silently skipped both (2026-08-15 fix: the dashboard's "Mark paid" button used this
    # endpoint directly, so every invoice marked paid this way was invisible to the trial
    # balance report). Never trust a client-supplied paid_at either — always server time.
    new_status = update.pop("status", None)
    update.pop("paid_at", None)
    if new_status:
        result = await service.update_invoice_status(tenant_id, invoice_id, new_status)
        if new_status == "sent" and update.get("sent_channel"):
            update["sent_at"] = service._now()  # server time, never trust a client value
        if update:  # any other fields sent in the same request still get applied
            update["updated_at"] = service._now()
            patched = (db.table("commerce_invoices").update(update)
                      .eq("tenant_id", tenant_id).eq("id", invoice_id).execute())
            if patched.data:
                result = patched.data[0]
        return result

    update["updated_at"] = service._now()
    result = db.table("commerce_invoices").update(update) \
        .eq("tenant_id", tenant_id).eq("id", invoice_id).execute()
    return result.data[0] if result.data else {}


@router.delete("/{tenant_id}/admin/invoices/{invoice_id}")
async def admin_delete_invoice(tenant_id: str, invoice_id: str):
    try:
        service._client().table("commerce_invoices") \
            .delete().eq("tenant_id", tenant_id).eq("id", invoice_id).execute()
    except Exception as exc:
        # A quote linked to a converted invoice (converted_invoice_id / source_quote_id, both
        # FKs) can't be deleted out from under the other side — surface a clear reason instead
        # of a raw 500, more likely to be hit now that a quote can link to SEVERAL invoices
        # (partial/deposit invoicing, 2026-08-17).
        if "foreign key" in str(exc).lower() or "23503" in str(exc):
            raise HTTPException(
                status_code=400,
                detail="Can't delete this — it's linked to a converted quote/invoice. "
                       "Delete or cancel the linked document first.",
            )
        raise HTTPException(status_code=500, detail="Could not delete this document.")
    return {"deleted": invoice_id}


class InvoicePaymentIn(BaseModel):
    amount_cents: int
    payment_method: Optional[str] = None
    note: Optional[str] = None


@router.post("/{tenant_id}/admin/invoices/{invoice_id}/payments")
async def admin_record_invoice_payment(tenant_id: str, invoice_id: str, body: InvoicePaymentIn):
    """Record one instalment against an invoice — supports real partial payments across
    multiple calls. Status becomes 'part_paid' until the running total reaches the invoice
    total, then flips to 'paid' automatically."""
    try:
        return await service.record_invoice_payment(
            tenant_id, invoice_id, body.amount_cents, body.payment_method, body.note)
    except ValueError as exc:
        status = 404 if "not found" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc))


@router.get("/{tenant_id}/admin/invoices/{invoice_id}/payments")
async def admin_list_invoice_payments(tenant_id: str, invoice_id: str):
    return {"payments": await service.list_invoice_payments(tenant_id, invoice_id)}


class InvoiceCancelIn(BaseModel):
    reason: Optional[str] = None


@router.post("/{tenant_id}/admin/invoices/{invoice_id}/cancel")
async def admin_cancel_invoice(tenant_id: str, invoice_id: str, body: InvoiceCancelIn):
    """Cancel an invoice that hasn't been paid yet. A paid/part_paid invoice must be reversed
    via a credit note instead — see service.cancel_invoice's docstring for why."""
    try:
        return await service.cancel_invoice(tenant_id, invoice_id, body.reason)
    except ValueError as exc:
        status = 404 if "not found" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc))


@router.post("/{tenant_id}/admin/quotes/bulk-delete")
async def admin_bulk_delete_quotes(tenant_id: str, body: dict):
    """Delete multiple quotes at once — restricted to doc_type='quote' with status draft/sent, so
    a quote the customer already accepted/declined (or one already converted to a real invoice,
    which would fail on the source_quote_id FK anyway) can't be silently removed this way."""
    quote_ids = [str(x) for x in (body.get("quote_ids") or [])]
    if not quote_ids:
        raise HTTPException(status_code=400, detail="quote_ids is required")
    db = service._client()
    deleted, skipped = [], []
    for qid in quote_ids:
        rows = (db.table("commerce_invoices").select("id,doc_type,status")
                .eq("tenant_id", tenant_id).eq("id", qid).limit(1).execute().data or [])
        row = rows[0] if rows else None
        if not row:
            skipped.append({"quote_id": qid, "reason": "not found"}); continue
        if row.get("doc_type") != "quote":
            skipped.append({"quote_id": qid, "reason": "not a quote"}); continue
        if row.get("status") not in ("draft", "sent"):
            skipped.append({"quote_id": qid, "reason": f"status is '{row.get('status')}' — only draft/sent quotes can be deleted"}); continue
        try:
            db.table("commerce_invoices").delete().eq("tenant_id", tenant_id).eq("id", qid).execute()
            deleted.append(qid)
        except Exception as exc:
            skipped.append({"quote_id": qid, "reason": f"already converted to an invoice ({exc})"})
    return {"deleted": deleted, "skipped": skipped}


@router.post("/{tenant_id}/admin/invoices/{invoice_id}/request-approval")
async def admin_request_invoice_approval(tenant_id: str, invoice_id: str, body: dict):
    """Route an invoice for approval before it's sent to the client.

    body: {
      "approvers": [{"phone": "...", "name": "...", "role": "architect"}],
      "deliver_via": "whatsapp" | "email" | "both",   # how to send once approved
      "requested_by": "<owner phone>"                  # optional
    }
    Every approver must reply APPROVE on WhatsApp; once all do, the invoice is
    delivered to the client automatically via the chosen channel(s).
    """
    invoice = await service.get_invoice(tenant_id, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    approvers = body.get("approvers") or []
    if not approvers:
        raise HTTPException(status_code=400, detail="At least one approver is required")

    from vula.commerce.approvals import create_approval
    label = f"Invoice {invoice.get('invoice_number', invoice_id)} for {invoice.get('customer_name', 'client')} — R{(invoice.get('total_cents', 0) or 0) / 100:.2f}"
    approval = await create_approval(
        tenant_id=tenant_id, entity_type="invoice", entity_id=invoice_id,
        title=label, approvers=approvers,
        requested_by=body.get("requested_by", ""),
        deliver_via=(body.get("deliver_via") or "whatsapp"),
    )
    # Mark the invoice as awaiting approval (kept distinct from 'sent').
    try:
        service._client().table("commerce_invoices").update(
            {"status": "draft", "updated_at": service._now()}
        ).eq("tenant_id", tenant_id).eq("id", invoice_id).execute()
    except Exception:
        pass
    return {"approval_id": approval["id"], "status": "pending", "approvers": len(approvers)}


@router.get("/{tenant_id}/admin/invoices/{invoice_id}/pdf")
async def admin_get_invoice_pdf(tenant_id: str, invoice_id: str):
    """Generate and stream a PDF for an invoice or quote.

    Returns application/pdf with Content-Disposition: attachment so browsers
    download the file directly. Filename is the invoice number.
    """
    invoice = await service.get_invoice(tenant_id, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    try:
        from vula.commerce.pdf import render_invoice_pdf, merge_branding
        settings = await service.get_invoice_settings(tenant_id)
        pdf_bytes = render_invoice_pdf(invoice, merge_branding(tenant_id, settings))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        log.error("PDF render failed for %s/%s: %s", tenant_id, invoice_id, exc)
        raise HTTPException(status_code=500, detail="PDF generation failed")

    invoice_number = invoice.get("invoice_number", invoice_id)
    filename = f"{invoice_number}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _ensure_approval_link(tenant_id: str, invoice: dict) -> str:
    """When an invoice is opted into client approval (migration 136), generate its token on
    first use and return the public link to embed in the outgoing message. Empty string when
    approval isn't required — callers treat that as "don't include a link"."""
    if not invoice.get("requires_approval"):
        return ""
    token = invoice.get("approval_token")
    if not token:
        import secrets
        token = secrets.token_urlsafe(24)
        try:
            service._client().table("commerce_invoices").update(
                {"approval_token": token}
            ).eq("tenant_id", tenant_id).eq("id", invoice["id"]).execute()
        except Exception as exc:
            log.debug("approval_token save skipped (run migration 136?): %s", exc)
            return ""
    from config import settings as app_settings
    return f"{app_settings.dashboard_url}/#/approve-invoice/{tenant_id}/{invoice['id']}?token={token}"


@router.post("/{tenant_id}/admin/invoices/{invoice_id}/send-email")
async def admin_send_invoice_email(tenant_id: str, invoice_id: str, body: Optional[dict] = None):
    """Render the invoice/quote PDF and email it to the customer via Resend.

    The recipient defaults to the invoice's customer_email; an optional
    ``email`` field in the body overrides it. Draft documents are marked
    ``sent`` once the email is dispatched.
    """
    invoice = await service.get_invoice(tenant_id, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    recipient = ((body or {}).get("email") or invoice.get("customer_email") or "").strip()
    if not recipient:
        raise HTTPException(status_code=400, detail="No recipient email address available")

    try:
        from vula.commerce.pdf import render_invoice_pdf, merge_branding
        settings = await service.get_invoice_settings(tenant_id)
        branding = merge_branding(tenant_id, settings)
        pdf_bytes = render_invoice_pdf(invoice, branding)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        log.error("PDF render failed for %s/%s: %s", tenant_id, invoice_id, exc)
        raise HTTPException(status_code=500, detail="PDF generation failed")

    tenant_name = branding.get("name") or tenant_id.replace("-", " ").title()
    approval_link = _ensure_approval_link(tenant_id, invoice)

    from vula.api.email import send_invoice_email
    sent = await send_invoice_email(recipient, invoice, pdf_bytes, tenant_name, approval_link)
    if not sent:
        raise HTTPException(
            status_code=503,
            detail="Email not sent — Resend is not configured or the send failed.",
        )

    new_status = invoice.get("status")
    if new_status == "draft":
        await service.update_invoice_status(tenant_id, invoice_id, "sent")
        new_status = "sent"
    try:
        service._client().table("commerce_invoices").update(
            {"sent_channel": "email", "sent_at": service._now()}
        ).eq("tenant_id", tenant_id).eq("id", invoice_id).execute()
    except Exception as exc:
        log.debug("sent_channel update skipped (run migration 135?): %s", exc)

    return {"sent": True, "to": recipient, "status": new_status}


@router.post("/{tenant_id}/admin/invoices/{invoice_id}/send-whatsapp")
async def admin_send_invoice_whatsapp(tenant_id: str, invoice_id: str, body: Optional[dict] = None):
    """Render the invoice/quote PDF and deliver it to the customer over WhatsApp.

    The recipient defaults to the invoice's customer_phone; an optional
    ``phone`` field in the body overrides it. The PDF is sent as a WhatsApp
    document with a short caption. Draft documents are marked ``sent`` once the
    message is dispatched.
    """
    invoice = await service.get_invoice(tenant_id, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    recipient = ((body or {}).get("phone") or invoice.get("customer_phone") or "").strip()
    if not recipient:
        raise HTTPException(status_code=400, detail="No recipient phone number available")

    try:
        from vula.commerce.pdf import render_invoice_pdf, merge_branding
        settings = await service.get_invoice_settings(tenant_id)
        branding = merge_branding(tenant_id, settings)
        pdf_bytes = render_invoice_pdf(invoice, branding)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        log.error("PDF render failed for %s/%s: %s", tenant_id, invoice_id, exc)
        raise HTTPException(status_code=500, detail="PDF generation failed")

    tenant_name = branding.get("name") or tenant_id.replace("-", " ").title()
    doc_label = {"invoice": "invoice", "quote": "quotation", "proforma": "pro forma invoice"}.get(
        invoice.get("doc_type", "invoice"), "document"
    )
    number = invoice.get("invoice_number", invoice_id)
    total = f"R{(int(invoice.get('total_cents') or 0) / 100):.2f}"
    approval_link = _ensure_approval_link(tenant_id, invoice)
    caption = (
        f"Hi {invoice.get('customer_name', 'there')}, here is your {doc_label} "
        f"{number} for {total} from {tenant_name}. Thank you for your business!"
    )
    if approval_link:
        caption += f"\n\nPlease review and approve: {approval_link}"
    filename = f"{number}.pdf"

    from vula.api.whatsapp import _send_invoice_document
    sent = await _send_invoice_document(recipient, pdf_bytes, filename, caption, tenant_id)
    if not sent:
        raise HTTPException(
            status_code=503,
            detail="WhatsApp not sent — WhatsApp is not configured or the send failed.",
        )

    new_status = invoice.get("status")
    if new_status == "draft":
        await service.update_invoice_status(tenant_id, invoice_id, "sent")
        new_status = "sent"
    try:
        service._client().table("commerce_invoices").update(
            {"sent_channel": "whatsapp", "sent_at": service._now()}
        ).eq("tenant_id", tenant_id).eq("id", invoice_id).execute()
    except Exception as exc:
        log.debug("sent_channel update skipped (run migration 135?): %s", exc)

    return {"sent": True, "to": recipient, "status": new_status}


# ── Invoice settings (onboarding + look-and-feel) ─────────────────────────────

@router.get("/{tenant_id}/admin/invoice-settings")
async def admin_get_invoice_settings(tenant_id: str):
    """Return the tenant's invoice settings (VAT, address, banking, template).

    ``onboarded`` is False when the tenant has never completed the first-run
    wizard, so the dashboard knows to show it.
    """
    settings = await service.get_invoice_settings(tenant_id)
    from config import settings as app_settings
    return {"settings": settings, "onboarded": bool(settings and settings.get("onboarded")),
            "email_configured": bool(app_settings.resend_api_key)}


@router.post("/{tenant_id}/admin/invoice-settings")
async def admin_upsert_invoice_settings(tenant_id: str, body: dict):
    """Create or update the tenant's invoice settings (one row per tenant)."""
    try:
        settings = await service.upsert_invoice_settings(tenant_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"settings": settings}


@router.post("/{tenant_id}/admin/invoice-settings/preview")
async def admin_preview_invoice_settings(tenant_id: str, body: dict):
    """Render a sample invoice PDF against IN-PROGRESS, UNSAVED branding settings — never touches
    the DB (no read or write of the real commerce_invoice_settings row, no real invoice created).
    Lets the dashboard show a live preview while a tenant is still adjusting the form, before
    they've hit Save."""
    from vula.commerce.pdf import render_invoice_pdf, merge_branding
    sample_invoice = {
        "tenant_id": tenant_id, "direction": "outbound", "doc_type": "invoice",
        "invoice_number": "SAMPLE-001", "status": "sent", "issue_date": service._now()[:10],
        "customer_name": "Sample Customer", "customer_email": "customer@example.com",
        "customer_phone": "082 123 4567", "customer_address": "1 Example Street\nCape Town",
        "line_items": [
            {"description": "Sample product", "quantity": 2, "unit_price_cents": 15000, "unit": "each"},
            {"description": "Sample service", "quantity": 1, "unit_price_cents": 45000, "unit": "job"},
        ],
        "subtotal_cents": 75000, "vat_cents": 11250, "total_cents": 86250,
        "notes": "This is a live preview using sample data — nothing here is a real invoice.",
    }
    try:
        pdf_bytes = render_invoice_pdf(sample_invoice, merge_branding(tenant_id, body or {}))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        log.error("Invoice settings preview render failed for %s: %s", tenant_id, exc)
        raise HTTPException(status_code=500, detail="Preview generation failed")
    return Response(content=pdf_bytes, media_type="application/pdf")


_CLONE_STYLE_PROMPT = """You are a design analyst. Look at this image of an old invoice/letterhead and
return ONLY a JSON object (no prose, no markdown fences) describing its visual style, mapped onto
this exact schema:
{
  "accent_color": "#RRGGBB" or null,
  "ink_color": "#RRGGBB" or null,
  "logo_align": "left" | "center" | null,
  "logo_size": "sm" | "md" | "lg" | null,
  "font_pairing": "vula" | "modern" | "editorial" | "classic" | "" | null,
  "template_choice": "classic" | "minimal" | "modern" | "branded" | "digg" | null,
  "show_company_reg": true | false | null,
  "footer_text": "<verbatim recurring footer/terms line, if any>" | null
}
Field meanings:
- accent_color: the dominant brand colour used for headings/accents (not body text).
- ink_color: the body-text colour, ONLY if it's visibly a colour other than plain black/dark grey.
- logo_align: "left" if the logo/header sits left-aligned or split left/right; "center" if the
  whole header (logo, name, everything) is centred on the page.
- logo_size: how large the logo is relative to the page width — sm (small, understated),
  md (moderate), lg (large, dominant).
- font_pairing: classify the heading font's visual style — vula=elegant serif (like Cormorant
  Garamond), modern=humanist sans-serif (like Poppins), editorial=dramatic display serif (like
  Playfair Display), classic=traditional serif (like Merriweather), ""=plain system sans-serif.
- template_choice: pick the closest overall layout — filled/coloured table header + rounded
  totals box -> "modern" or "branded"; hairline rules, monochrome, minimal fills -> "minimal";
  a plain two-column description/amount table with bold running-subtotal rows (no qty/unit/price
  columns) -> "digg"; anything else / uncertain -> "classic".
- show_company_reg: true only if a company registration number is visibly printed anywhere.
- footer_text: copy the exact recurring footer/terms line if one is visible, else null.
Return null for any field you are not confident about. Do not guess."""

_CLONE_VALID = {
    "logo_align": {"left", "center"},
    "logo_size": {"sm", "md", "lg"},
    "font_pairing": {"", "vula", "modern", "editorial", "classic"},
    "template_choice": set(service._TEMPLATE_CHOICES),
}


def _clean_clone_suggestion(raw: dict) -> dict:
    """Whitelist the vision model's output field-by-field — never trust a raw LLM response
    into settings that control real invoice rendering. Anything invalid, missing, or explicitly
    null is dropped rather than guessed at; the caller only ever pre-fills what survives here."""
    import re as _re
    hex_re = _re.compile(r"^#[0-9A-Fa-f]{6}$")
    out: dict = {}
    for field in ("accent_color", "ink_color"):
        v = raw.get(field)
        if isinstance(v, str) and hex_re.match(v):
            out[field] = v
    for field, allowed in _CLONE_VALID.items():
        v = raw.get(field)
        if isinstance(v, str) and v in allowed:
            out[field] = v
    if isinstance(raw.get("show_company_reg"), bool):
        out["show_company_reg"] = raw["show_company_reg"]
    footer = raw.get("footer_text")
    if isinstance(footer, str) and footer.strip():
        out["footer_text"] = footer.strip()[:300]
    return out


@router.post("/{tenant_id}/admin/invoice-settings/clone-from-upload")
async def admin_clone_invoice_style(tenant_id: str, body: dict):
    """Analyze an uploaded old invoice/letterhead (PDF or image) and suggest branding settings
    matching its visual style. Proposes only — never writes to commerce_invoice_settings; the
    dashboard pre-fills the settings form with whatever comes back and the tenant still has to
    hit Save to keep it (same trust model as the existing /preview endpoint: look, don't touch)."""
    import asyncio
    import base64
    import io
    import json as _json
    import re as _re

    file_url = (body or {}).get("file_url")
    if not file_url:
        raise HTTPException(status_code=400, detail="file_url is required")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(file_url)
            resp.raise_for_status()
            data = resp.content
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Couldn't fetch that file: {exc}")
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File is too large (max 15MB)")

    from PIL import Image
    content_type = resp.headers.get("content-type", "")
    is_pdf = "pdf" in content_type or file_url.lower().endswith(".pdf")
    try:
        if is_pdf:
            from pdf2image import convert_from_bytes
            pages = convert_from_bytes(data, dpi=200, first_page=1, last_page=1)
            if not pages:
                raise ValueError("no pages rendered")
            img = pages[0]
        else:
            img = Image.open(io.BytesIO(data)).convert("RGB")
        img.thumbnail((1600, 1600))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        img_b64 = base64.b64encode(buf.getvalue()).decode()
    except Exception as exc:
        log.warning("Invoice clone: couldn't read uploaded file for %s: %s", tenant_id, exc)
        raise HTTPException(status_code=400, detail="Couldn't read that file — try a clearer photo or scan")

    from core.llm_router import resolve_cloud_vision_route
    cloud = resolve_cloud_vision_route()
    if not cloud:
        raise HTTPException(status_code=503, detail="Style analysis isn't configured on this server")
    model, api_key, api_base = cloud

    try:
        import litellm
        litellm.drop_params = True
        response = await asyncio.wait_for(litellm.acompletion(
            model=model,
            messages=[
                {"role": "system", "content": _CLONE_STYLE_PROMPT},
                {"role": "user", "content": [
                    {"type": "text", "text": "Analyze this invoice's visual style."},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                ]},
            ],
            temperature=0.1, max_tokens=500, api_key=api_key, api_base=api_base,
        ), timeout=30)
        content = response.choices[0].message.content or "{}"
    except Exception as exc:
        log.warning("Invoice clone: vision call failed for %s: %s", tenant_id, exc)
        raise HTTPException(status_code=502, detail="Couldn't analyze that file — try again or a clearer image")

    content = _re.sub(r"<think>.*?</think>", "", content, flags=_re.DOTALL)
    content = _re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=_re.MULTILINE).strip()
    try:
        raw = _json.loads(content)
    except Exception:
        m = _re.search(r"\{.*\}", content, _re.DOTALL)
        raw = _json.loads(m.group(0)) if m else {}

    suggested = _clean_clone_suggestion(raw if isinstance(raw, dict) else {})
    if not suggested:
        raise HTTPException(status_code=422, detail="Couldn't confidently extract any style details from that file")
    return {"suggested": suggested}


@router.get("/{tenant_id}/brand")
async def public_brand(tenant_id: str):
    """Public, read-only, non-secret brand fields — deliberately OUTSIDE the /admin/ path so the
    tenant-auth guard never blocks it (public storefront pages have no login). Single source of
    truth for brand: the same commerce_invoice_settings row the dashboard's Brand Kit card writes,
    replacing the old split where the storefront read a separate, never-updated tenant_config.theme."""
    settings = await service.get_invoice_settings(tenant_id) or {}
    return {
        "name": settings.get("trading_as") or settings.get("company_name"),
        "logo_url": settings.get("logo_url"),
        "accent_color": settings.get("accent_color"),
        "ink_color": settings.get("ink_color"),
        "font_pairing": settings.get("font_pairing"),
        "logo_align": settings.get("logo_align") or "left",
        "logo_size": settings.get("logo_size") or "md",
        "header_sticky": settings.get("header_sticky", True),
        "header_nav_position": settings.get("header_nav_position") or "right",
        "header_cta_text": settings.get("header_cta_text"),
        "header_cta_link": settings.get("header_cta_link"),
        "whatsapp": settings.get("company_phone"),  # utility bar contact link — already tenant-editable, no new field
    }


# ── Saved clients / suppliers (invoicing) ─────────────────────────────────────

@router.get("/{tenant_id}/admin/invoice-clients")
async def admin_list_invoice_clients(tenant_id: str, kind: Optional[str] = None):
    return {"clients": await service.list_invoice_clients(tenant_id, kind)}


@router.post("/{tenant_id}/admin/invoice-clients")
async def admin_upsert_invoice_client(tenant_id: str, body: dict):
    try:
        client = await service.upsert_invoice_client(tenant_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"client": client}


@router.delete("/{tenant_id}/admin/invoice-clients/{client_id}")
async def admin_delete_invoice_client(tenant_id: str, client_id: str):
    await service.delete_invoice_client(tenant_id, client_id)
    return {"deleted": client_id}


# ── Recurring invoices ────────────────────────────────────────────────────────

@router.get("/{tenant_id}/admin/recurring-invoices")
async def admin_list_recurring(tenant_id: str):
    return {"recurring": await service.list_recurring(tenant_id)}


@router.post("/{tenant_id}/admin/recurring-invoices")
async def admin_upsert_recurring(tenant_id: str, body: dict):
    try:
        rec = await service.upsert_recurring(tenant_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"recurring": rec}


@router.delete("/{tenant_id}/admin/recurring-invoices/{rec_id}")
async def admin_delete_recurring(tenant_id: str, rec_id: str):
    await service.delete_recurring(tenant_id, rec_id)
    return {"deleted": rec_id}


@router.post("/cron/recurring-invoices")
async def cron_recurring_invoices():
    """Generate invoices for all due recurring templates (called by the scheduler)."""
    return {"generated": await service.process_due_recurring()}


@router.post("/{tenant_id}/admin/invoices/{invoice_id}/credit-note")
async def admin_create_credit_note(tenant_id: str, invoice_id: str, body: dict = None):
    """Create a credit note against an invoice (full, or partial via body.line_items).
    Invoices have no 'refunded' status (unlike orders) — a credit note IS the refund record —
    so body.auto_refund here does what body.auto_refund does on the order status endpoint:
    opt-in, real money-back-to-customer via Yoco, keyed off the invoice's own checkout id."""
    body = body or {}
    try:
        cn = await service.create_credit_note(tenant_id, invoice_id, body.get("line_items"))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    refund_result = None
    if body.get("auto_refund"):
        src = await service.get_invoice(tenant_id, invoice_id)
        checkout_id = (src or {}).get("yoco_checkout_id")
        amount = int(cn.get("total_cents") or 0)
        if not checkout_id:
            refund_result = {"gateway": "yoco", "status": "failed",
                              "detail": "This invoice has no online (Yoco) payment on record."}
        elif amount <= 0:
            refund_result = {"gateway": "yoco", "status": "failed", "detail": "Nothing to refund."}
        else:
            from vula.api.yoco import refund_yoco_payment
            result = await refund_yoco_payment(tenant_id, checkout_id, amount)
            now = service._now()
            if result.get("ok"):
                refund_result = {"gateway": "yoco", "status": "pending", "amount_cents": amount}
                patch = {"refund_status": "pending", "refunded_amount_cents": amount,
                         "refunded_at": now, "yoco_refund_id": result.get("refund_id")}
            else:
                refund_result = {"gateway": "yoco", "status": "failed", "detail": result.get("detail")}
                patch = {"refund_status": "failed"}
            try:
                service._client().table("commerce_invoices").update(patch) \
                    .eq("id", invoice_id).eq("tenant_id", tenant_id).execute()
            except Exception as exc:
                log.warning("refund tracking update failed for invoice %s: %s", invoice_id, exc)
    return {"credit_note": cn, "refund": refund_result}


@router.post("/{tenant_id}/admin/invoices/{invoice_id}/pay-link")
async def admin_invoice_pay_link(tenant_id: str, invoice_id: str):
    """Create a Yoco 'Pay now' checkout for an invoice; store + return the link."""
    inv = await service.get_invoice(tenant_id, invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if inv.get("status") == "paid":
        return {"already_paid": True, "pay_url": inv.get("pay_url")}
    from vula import payments
    from vula.api import tenants as _tenants
    store_url = _tenants.store_url(tenant_id) or "https://offthehook.co.za"
    api_base = "https://vula-group-production.up.railway.app"
    row = payments.default_provider_row(tenant_id)
    provider = row["provider"] if row else "yoco"
    notify_url = f"{api_base}/v1/payments/webhook/{tenant_id}/{provider}"
    try:
        link = await payments.create_pay_link(
            tenant_id, amount_cents=int(inv["total_cents"]), reference=invoice_id,
            description=f"Invoice {inv.get('invoice_number') or ''}".strip(),
            success_url=f"{store_url}/payment/success?invoice={invoice_id}",
            cancel_url=f"{store_url}/payment/cancel?invoice={invoice_id}",
            notify_url=notify_url,
            customer={"email": inv.get("customer_email"), "phone": inv.get("customer_phone")})
    except Exception as exc:
        log.error("Pay-link create failed (%s): %s", provider, exc)
        raise HTTPException(status_code=502, detail="Payment gateway error — check your gateway keys.")
    if not link or not link.url:
        raise HTTPException(status_code=503, detail="No payment gateway connected — connect one in Payments.")
    try:
        service._client().table("commerce_invoices").update(
            {"pay_url": link.url, "yoco_checkout_id": link.raw.get("id"), "updated_at": service._now()}
        ).eq("id", invoice_id).eq("tenant_id", tenant_id).execute()
    except Exception:
        pass
    return {"pay_url": link.url, "provider": link.provider, "amount_cents": int(inv["total_cents"])}


# ── Quote / proforma endpoints ────────────────────────────────────────────────
# Quotes and proformas share commerce_invoices (doc_type). These routes use the
# service layer so totals are computed server-side and numbering is doc-type
# scoped (e.g. OTH-QTE-00001). Convert turns an accepted quote into an invoice.

@router.get("/{tenant_id}/admin/quotes")
async def admin_list_quotes(
    tenant_id: str,
    doc_type: str = Query("quote"),  # quote | proforma
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    quotes = await service.list_invoices(
        tenant_id, doc_type=doc_type, status=status, limit=limit, offset=offset
    )
    return {"quotes": quotes, "count": len(quotes)}


@router.post("/{tenant_id}/admin/quotes")
async def admin_create_quote(tenant_id: str, body: InvoiceCreate):
    """Create a quote or proforma. Totals computed server-side in integer cents."""
    if body.doc_type.value not in ("quote", "proforma"):
        raise HTTPException(status_code=400, detail="doc_type must be 'quote' or 'proforma'")
    created = await service.create_invoice(tenant_id, body.model_dump(mode="json"))
    return created


@router.post("/{tenant_id}/admin/quotes/{quote_id}/convert")
async def admin_convert_quote(tenant_id: str, quote_id: str, body: dict = None):
    """Convert an accepted quote/proforma into a draft invoice, linking both. Optional body
    {"amount_cents": int} invoices only part of the quote (a deposit/progress invoice), leaving
    the remainder convertible later — omit it to invoice whatever remains."""
    amount_cents = (body or {}).get("amount_cents")
    try:
        invoice = await service.convert_quote_to_invoice(tenant_id, quote_id, amount_cents)
    except ValueError as exc:
        # "not found" is a real 404; "must be accepted"/"already converted" are business-rule
        # rejections (400), not a missing resource — distinct enough to matter to the caller.
        status = 404 if "not found" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc))
    return invoice


@router.patch("/{tenant_id}/admin/quotes/{quote_id}/status")
async def admin_update_quote_status(tenant_id: str, quote_id: str, body: dict):
    """Transition a quote/proforma status — sent, accepted, declined, expired."""
    valid = {"draft", "sent", "accepted", "declined", "expired", "cancelled"}
    new_status = body.get("status")
    if new_status not in valid:
        raise HTTPException(status_code=400, detail=f"status must be one of {valid}")
    result = await service.update_invoice_status(tenant_id, quote_id, new_status)
    if not result:
        raise HTTPException(status_code=404, detail="Quote not found")
    return result


# ── Expense endpoints ─────────────────────────────────────────────────────────
# (list/create moved to the expense-claims workflow above — see admin_list_expenses /
# admin_create_expense near the top of the file, migration 060)

@router.delete("/{tenant_id}/admin/expenses/{expense_id}")
async def admin_delete_expense(tenant_id: str, expense_id: str):
    service._client().table("commerce_expenses") \
        .delete().eq("tenant_id", tenant_id).eq("id", expense_id).execute()
    return {"deleted": expense_id}


# ── Broadcast endpoints ───────────────────────────────────────────────────────

@router.get("/{tenant_id}/admin/broadcasts")
async def admin_list_broadcasts(tenant_id: str, limit: int = Query(50)):
    db = service._client()
    result = db.table("commerce_broadcast_logs").select("*") \
        .eq("tenant_id", tenant_id).order("created_at", desc=True).limit(limit).execute()
    return {"broadcasts": result.data or [], "count": len(result.data or [])}


@router.get("/{tenant_id}/admin/broadcasts/analytics")
async def admin_broadcast_analytics(tenant_id: str, days: int = Query(90)):
    """Aggregated marketing analytics — rolls up every broadcast in the window into totals,
    a weekly reach trend, and a best-performing ranking, plus attributed revenue (migration
    104). Per-broadcast funnels (admin_broadcast_recipients) and per-customer engagement
    already existed; nothing summarized them across campaigns — the gap flagged in the
    marketing capability audit."""
    from datetime import datetime, timedelta, timezone

    db = service._client()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    logs = (db.table("commerce_broadcast_logs").select("*")
            .eq("tenant_id", tenant_id).gte("created_at", since)
            .order("created_at").limit(500).execute().data or [])

    totals = {
        "broadcasts": len(logs),
        "sent": sum(int(l.get("sent_count") or 0) for l in logs),
        "delivered": sum(int(l.get("delivered_count") or 0) for l in logs),
        "read": sum(int(l.get("read_count") or 0) for l in logs),
        "clicked": sum(int(l.get("clicked_count") or 0) for l in logs),
        "failed": sum(int(l.get("failed_count") or 0) for l in logs),
    }
    totals["open_rate"] = round(100 * totals["read"] / totals["delivered"], 1) if totals["delivered"] else 0
    totals["click_rate"] = round(100 * totals["clicked"] / totals["delivered"], 1) if totals["delivered"] else 0

    # Attributed revenue across every broadcast in the window, one query rather than N.
    broadcast_ids = [l["id"] for l in logs]
    attributed_orders, attributed_revenue_cents = 0, 0
    if broadcast_ids:
        try:
            orders = (db.table("commerce_orders").select("total_cents,status,attributed_broadcast_id")
                      .eq("tenant_id", tenant_id).in_("attributed_broadcast_id", broadcast_ids)
                      .limit(2000).execute().data or [])
            counted = [o for o in orders if o.get("status") not in ("pending_payment", "cancelled", "refunded")]
            attributed_orders = len(counted)
            attributed_revenue_cents = sum(int(o.get("total_cents") or 0) for o in counted)
        except Exception as exc:
            log.debug("broadcast analytics attribution rollup skipped (run migration 104?): %s", exc)
    totals["attributed_orders"] = attributed_orders
    totals["attributed_revenue_cents"] = attributed_revenue_cents

    # Best-performing by click rate — require a minimum of 3 delivered so a single-recipient
    # test send can't top the list off a 100% rate.
    def _click_rate(l):
        delivered = int(l.get("delivered_count") or 0)
        return (int(l.get("clicked_count") or 0) / delivered) if delivered else 0

    ranked = sorted((l for l in logs if int(l.get("delivered_count") or 0) >= 3),
                    key=_click_rate, reverse=True)
    best_performing = [
        {"id": l["id"], "name": l.get("name") or l.get("template_name"),
         "sent": l.get("sent_count") or 0, "clicked": l.get("clicked_count") or 0,
         "click_rate": round(100 * _click_rate(l), 1), "sent_at": l.get("sent_at") or l.get("created_at")}
        for l in ranked[:5]
    ]

    # Weekly reach trend — buckets by the Monday of the week each broadcast was created.
    trend_map: dict[str, dict] = {}
    for l in logs:
        created = l.get("created_at")
        if not created:
            continue
        try:
            dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
        except Exception:
            continue
        week_start = (dt - timedelta(days=dt.weekday())).date().isoformat()
        bucket = trend_map.setdefault(week_start, {"week": week_start, "sent": 0, "delivered": 0,
                                                    "read": 0, "clicked": 0})
        bucket["sent"] += int(l.get("sent_count") or 0)
        bucket["delivered"] += int(l.get("delivered_count") or 0)
        bucket["read"] += int(l.get("read_count") or 0)
        bucket["clicked"] += int(l.get("clicked_count") or 0)
    trend = sorted(trend_map.values(), key=lambda b: b["week"])

    return {"totals": totals, "best_performing": best_performing, "trend": trend, "days": days}


@router.post("/{tenant_id}/admin/broadcasts/draft")
async def admin_draft_broadcast(tenant_id: str, body: dict):
    """AI-write a WhatsApp broadcast from rough details the owner provides.

    Body:
        details:      raw info, e.g. "yellowtail 180, snoek 95, kob 220, free delivery over R500"
        message_type: weekly_fish | new_stock | reorder | promo | delivery (optional)

    Returns a polished, on-brand WhatsApp broadcast message the owner can
    review, edit, and send.
    """
    details = (body.get("details") or "").strip()
    msg_type = body.get("message_type", "weekly_fish")
    if not details:
        raise HTTPException(status_code=400, detail="Provide some details to write from.")

    # Pull the tenant's brand voice from its KB for on-brand tone
    brand_context = ""
    try:
        from vula.ingestion.pipeline import VulaIngestionPipeline
        kb = VulaIngestionPipeline(tenant_id=tenant_id)
        chunks = await kb.query("brand voice tone about us what we sell", top_k=2)
        brand_context = "\n".join(c.get("text", "")[:400] for c in chunks)
    except Exception:
        pass

    type_hint = {
        "weekly_fish": "this week's fresh catch with prices",
        "new_stock":   "a new stock arrival announcement",
        "reorder":     "a friendly reminder to reorder",
        "promo":       "a special promotion or limited-time offer",
        "delivery":    "a delivery-day reminder",
    }.get(msg_type, "a customer update")

    try:
        import litellm
        from core.llm_router import resolve_generation_route
        litellm.drop_params = True
        model, api_key, api_base = await resolve_generation_route()

        resp = await litellm.acompletion(
            model=model,
            messages=[
                {"role": "system", "content":
                    "You write short, warm WhatsApp broadcast messages for a South African "
                    "food business. Rules: under 60 words, friendly but not cheesy, ZAR prices, "
                    "1-2 relevant emoji max, end with a clear call to action to order on WhatsApp. "
                    "Output ONLY the message text — no preamble, no quotes." +
                    (f"\n\nBrand voice:\n{brand_context}" if brand_context else "")},
                {"role": "user", "content":
                    f"Write {type_hint}. Details from the owner: {details}"},
            ],
            temperature=0.6,
            max_tokens=200,
            api_key=api_key,
            api_base=api_base,
        )
        import re as _re
        text = (resp.choices[0].message.content or "").strip()
        text = _re.sub(r"<think>.*?</think>", "", text, flags=_re.DOTALL).strip()
        text = text.strip('"').strip()
        return {"ok": True, "message": text, "message_type": msg_type}
    except Exception as exc:
        log.error("Broadcast draft failed for %s: %s", tenant_id, exc)
        raise HTTPException(status_code=502, detail=f"Could not draft message: {exc}")


# ── Consent / suppression + delivery status (POPIA + analytics) ───────────────

def _suppressed_phones(tenant_id: str) -> set[str]:
    """Normalized phones the tenant must NOT broadcast to (opted out)."""
    try:
        rows = (service._client().table("commerce_consent").select("phone")
                .eq("tenant_id", tenant_id).eq("status", "opted_out").execute().data or [])
        return {_norm_phone(r["phone"]) for r in rows}
    except Exception:
        return set()


def _log_consent_event(tenant_id: str, phone: str, action: str, source: str) -> None:
    """Append-only audit trail (migration 064) — POPIA enforcement wants proof of WHEN/HOW consent
    changed, not just current state. Never overwritten, never deleted. Best-effort — a logging
    failure must never block the actual opt-in/opt-out from taking effect."""
    try:
        service._client().table("commerce_consent_log").insert({
            "tenant_id": tenant_id, "phone": phone, "action": action, "source": source}).execute()
    except Exception as exc:
        log.debug("consent audit log skipped (run migration 064?): %s", exc)


def record_inbound_consent(tenant_id: str, phone: str, source: str = "inbound") -> None:
    """Record opt-in. Never overrides an existing opt-out. `source` distinguishes implied
    single-reply consent ('inbound') from an explicit double-opt-in confirmation
    ('onboarding_double_optin_confirmed') in the audit trail — see commerce_consent_log."""
    p = _norm_phone(phone)
    if not tenant_id or not p:
        return
    try:
        db = service._client()
        existing = (db.table("commerce_consent").select("status")
                    .eq("tenant_id", tenant_id).eq("phone", p).limit(1).execute().data or [])
        existing_status = existing[0]["status"] if existing else None
        if existing_status == "opted_out":
            return  # never override an explicit opt-out
        if not existing:
            db.table("commerce_consent").insert({
                "tenant_id": tenant_id, "phone": p, "status": "opted_in", "source": source}).execute()
        # Always log the event, even if the current-state row already says opted_in — an
        # explicit re-confirmation (e.g. double opt-in) is a real audit-worthy event on its
        # own, distinct from whether it changes the derived current-state row.
        _log_consent_event(tenant_id, p, "opted_in", source)
    except Exception as exc:
        log.debug("consent record skipped (run migration 020?): %s", exc)


def record_opt_out(tenant_id: str, phone: str, source: str = "stop_keyword") -> None:
    """Persist a do-not-contact suppression (kept even after PII deletion)."""
    p = _norm_phone(phone)
    if not tenant_id or not p:
        return
    try:
        service._client().table("commerce_consent").upsert({
            "tenant_id": tenant_id, "phone": p, "status": "opted_out",
            "source": source, "updated_at": "now()"}, on_conflict="tenant_id,phone").execute()
        _log_consent_event(tenant_id, p, "opted_out", source)
    except Exception as exc:
        log.debug("opt-out record skipped: %s", exc)


def record_message_status(wamid: str, status: str, error: Optional[str] = None) -> None:
    """Update a broadcast recipient by wamid (from Meta status callbacks) and roll the
    delivered/read/failed counts up into commerce_broadcast_logs. Best-effort."""
    if not wamid or not status:
        return
    _RANK = {"sent": 1, "failed": 1, "delivered": 2, "read": 3}
    try:
        db = service._client()
        rows = (db.table("commerce_broadcast_recipients").select("broadcast_id,status")
                .eq("wamid", wamid).limit(1).execute().data or [])
        if not rows:
            return
        bid, cur = rows[0]["broadcast_id"], rows[0].get("status") or "sent"
        new = status if _RANK.get(status, 0) >= _RANK.get(cur, 0) else cur  # never downgrade
        db.table("commerce_broadcast_recipients").update({
            "status": new, "error": error, "updated_at": "now()"}).eq("wamid", wamid).execute()
        allrows = (db.table("commerce_broadcast_recipients").select("status")
                   .eq("broadcast_id", bid).execute().data or [])
        delivered = sum(1 for r in allrows if r["status"] in ("delivered", "read"))
        read = sum(1 for r in allrows if r["status"] == "read")
        failed = sum(1 for r in allrows if r["status"] == "failed")
        db.table("commerce_broadcast_logs").update({
            "delivered_count": delivered, "read_count": read, "failed_count": failed}).eq("id", bid).execute()
    except Exception as exc:
        log.debug("record_message_status skipped: %s", exc)


# ── Email campaigns (migration 106) ────────────────────────────────────────────
# Bulk/campaign email, distinct from the 1:1 business correspondence in vula/email_imap.
# Reuses the exact same audience targeting as WhatsApp broadcasts — a customer's email is
# already merged into _aggregate_customers, so no separate segmentation logic is needed.

def _suppressed_emails(tenant_id: str) -> set[str]:
    """Normalized (lowercased) emails the tenant must NOT campaign to (opted out)."""
    try:
        rows = (service._client().table("commerce_email_consent").select("email")
                .eq("tenant_id", tenant_id).eq("status", "opted_out").execute().data or [])
        return {(r["email"] or "").strip().lower() for r in rows}
    except Exception:
        return set()


@router.get("/{tenant_id}/admin/email-campaigns")
async def admin_list_email_campaigns(tenant_id: str, limit: int = Query(50)):
    db = service._client()
    result = (db.table("commerce_email_campaigns").select("*")
              .eq("tenant_id", tenant_id).order("created_at", desc=True).limit(limit).execute())
    return {"campaigns": result.data or [], "count": len(result.data or [])}


@router.post("/{tenant_id}/admin/email-campaigns/send")
async def admin_send_email_campaign(tenant_id: str, body: dict):
    """
    Bulk/campaign email — sent via the tenant's connected mailbox (one SMTP connection
    for the whole batch, vula/email_imap/service.py:send_batch), reusing the exact same
    audience targeting as WhatsApp broadcasts.

    Body:
        subject:          required
        body:             required (plain text — an unsubscribe footer is appended)
        audience_filter:  all | active_30d | high_value | seg:<id> | comma-joined multi-select
        dry_run:          bool, DEFAULT TRUE — preview the audience without sending
        name:             campaign label
        test_email:       send only to this address (skips audience + suppression)
    """
    from urllib.parse import quote
    from uuid import uuid4
    from config import settings as _settings
    from vula.email_imap import service as email_service
    from vula.email_imap.credentials import get_email_creds

    db = service._client()
    subject = (body.get("subject") or "").strip()
    msg_body = body.get("body") or ""
    audience = body.get("audience_filter", "all")
    dry_run = body.get("dry_run", True)
    name = body.get("name") or subject
    test_email = (body.get("test_email") or "").strip().lower()

    if not subject or not msg_body:
        raise HTTPException(status_code=400, detail="subject and body are required")

    # Resolve the exact audience (same source as the Customers tab / WhatsApp broadcasts);
    # audience may be a built-in, a saved segment ('seg:<id>'), or a comma-joined multi-select.
    customers = await _aggregate_customers(tenant_id)
    if isinstance(audience, list):
        toks = [str(t).strip() for t in audience if str(t).strip()]
    else:
        toks = [t.strip() for t in str(audience or "all").split(",") if t.strip()]
    toks = toks or ["all"]
    audience = ",".join(toks)
    seen: set = set()
    rows = []
    for t in toks:
        for c in _filter_audience(customers, _resolve_audience(tenant_id, t)):
            em = (c.get("email") or "").strip().lower()
            if em and em not in seen:
                seen.add(em)
                rows.append(c)
    # Only contacts with a usable email; same opt-in-first posture as WhatsApp broadcasts
    # (imported contacts who haven't opted in yet are excluded).
    recipients = [c for c in rows if c.get("consent") != "unknown"]

    suppressed = _suppressed_emails(tenant_id)
    suppressed_count = 0
    if suppressed:
        before = len(recipients)
        recipients = [c for c in recipients if (c.get("email") or "").strip().lower() not in suppressed]
        suppressed_count = before - len(recipients)

    if test_email:
        recipients = [{"name": "Test", "email": test_email}]
        suppressed_count = 0
        audience = "test"
        name = f"{name} (test)"

    if dry_run:
        return {
            "dry_run": True,
            "audience": audience,
            "recipient_count": len(recipients),
            "suppressed_count": suppressed_count,
            "sample": [{"name": c.get("name") or "Unknown", "email": c.get("email")} for c in recipients[:10]],
            "note": "Preview only — no emails sent. Send with dry_run=false to go live.",
        }

    if not recipients:
        return {"sent": 0, "failed": 0, "recipient_count": 0}

    creds = get_email_creds(tenant_id)
    if not creds:
        raise HTTPException(status_code=503, detail="No email account connected — connect one in Settings.")

    campaign_id = str(uuid4())
    base = (getattr(_settings, "public_base_url", "") or "").rstrip("/")
    messages = []
    for c in recipients:
        em = c["email"]
        full_body = msg_body
        if base:
            unsub = f"{base}/email/unsubscribe?tenant={quote(tenant_id)}&email={quote(em)}"
            full_body = f"{msg_body}\n\n---\nDon't want these emails? Unsubscribe: {unsub}"
        messages.append({"to": em, "subject": subject, "body": full_body})

    results = await email_service.send_batch(creds, messages)
    sent = sum(1 for r in results if r.get("sent"))
    failed = len(results) - sent

    try:
        db.table("commerce_email_campaigns").insert({
            "id": campaign_id, "tenant_id": tenant_id, "name": name, "subject": subject,
            "body": msg_body, "audience_filter": audience, "recipient_count": len(recipients),
            "sent_count": sent, "failed_count": failed,
            "status": "sent" if sent > 0 else "failed", "sent_at": "now()",
        }).execute()
        rec_rows = [{"tenant_id": tenant_id, "campaign_id": campaign_id, "email": r["to"],
                    "status": "sent" if r.get("sent") else "failed", "error": r.get("error")}
                   for r in results]
        if rec_rows:
            db.table("commerce_email_campaign_recipients").insert(rec_rows).execute()
    except Exception as exc:
        log.warning("email campaign log write failed (run migration 106?): %s", exc)

    return {"sent": sent, "failed": failed, "recipient_count": len(recipients), "campaign_id": campaign_id}


# ── WhatsApp template management (create/submit/track/delete via Graph API) ───
# Plain dict body (not a Pydantic model) — Optional[List[str]] fields have repeatedly hit a
# Pydantic "TypeAdapter ... is not fully defined" schema-build error in this codebase (same
# issue hit earlier with ImportCommitIn); a dict sidesteps it entirely.
@router.get("/{tenant_id}/admin/wa-templates")
async def admin_list_wa_templates(tenant_id: str):
    from vula.commerce import wa_templates
    return {"templates": await wa_templates.list_templates(tenant_id)}


@router.post("/{tenant_id}/admin/wa-templates")
async def admin_create_wa_template(tenant_id: str, body: dict):
    from vula.commerce import wa_templates
    name = (body or {}).get("name") or ""
    body_text = (body or {}).get("body_text") or ""
    if not name or not body_text:
        raise HTTPException(status_code=400, detail="name and body_text are required")
    result = await wa_templates.create_template(
        tenant_id, name, body_text,
        category=(body or {}).get("category") or "UTILITY",
        language=(body or {}).get("language") or "en",
        example_params=(body or {}).get("example_params"),
        created_by=(body or {}).get("created_by"),
        header_type=(body or {}).get("header_type"),
        header_example_url=(body or {}).get("header_example_url"),
        buttons=(body or {}).get("buttons"))
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.delete("/{tenant_id}/admin/wa-templates/{name}")
async def admin_delete_wa_template(tenant_id: str, name: str):
    from vula.commerce import wa_templates
    result = await wa_templates.delete_template(tenant_id, name)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/{tenant_id}/admin/broadcasts/send")
async def admin_send_broadcast(tenant_id: str, body: dict):
    """
    WhatsApp broadcast — sent directly via the Meta Graph API (no n8n).

    Resolves the audience from the tenant's client list, then sends a
    pre-approved WhatsApp template to each recipient using the tenant's own
    Meta credentials. Delivery counts are tracked in commerce_broadcast_logs.

    Body:
        template_name:   Meta-approved template name (required)
        audience_filter: all | active_30d | high_value
        language:        template language code (default 'en')
        dry_run:         bool, DEFAULT TRUE — preview the audience without
                         sending. Must pass dry_run=false to actually send.
        name:            campaign label
    """
    from uuid import uuid4

    db = service._client()
    template = body.get("template_name", "")
    audience = body.get("audience_filter", "all")
    language = body.get("language", "en")
    dry_run = body.get("dry_run", True)
    name = body.get("name", template)
    test_phone = body.get("test_phone")

    if not template and not body.get("body"):
        raise HTTPException(status_code=400, detail="template_name or body required")

    # Resolve the exact audience (same source as the Customers tab); audience may be
    # a built-in (all/active_30d/high_value) or a saved custom segment ('seg:<id>').
    customers = await _aggregate_customers(tenant_id)
    # Audience may be one token, a comma-joined string, or a list → UNION the recipients
    # (overlaps de-duplicated by phone). Powers multi-select audiences.
    if isinstance(audience, list):
        toks = [str(t).strip() for t in audience if str(t).strip()]
    else:
        toks = [t.strip() for t in str(audience or "all").split(",") if t.strip()]
    toks = toks or ["all"]
    audience = ",".join(toks)  # normalised for storage + response
    seen: set = set()
    rows = []
    for t in toks:
        for c in _filter_audience(customers, _resolve_audience(tenant_id, t)):
            ph = _norm_phone(c.get("phone"))
            if ph and ph not in seen:
                seen.add(ph)
                rows.append(c)
    # Only WhatsApp-reachable contacts with a usable number, and — for the opt-in-first posture —
    # exclude imported contacts who haven't opted in yet (consent 'unknown'). Existing order/chat
    # customers (no consent field) and explicitly opted-in contacts are kept.
    recipients = [c for c in rows
                  if _norm_phone(c.get("phone")).isdigit() and c.get("consent") != "unknown"]

    # Honour opt-outs (POPIA suppression) before anything is sent.
    suppressed = _suppressed_phones(tenant_id)
    suppressed_count = 0
    if suppressed:
        before = len(recipients)
        recipients = [c for c in recipients if _norm_phone(c.get("phone")) not in suppressed]
        suppressed_count = before - len(recipients)

    # Test send → only this number (skip audience + suppression), so you can preview the
    # exact message on your own phone before going live.
    if test_phone:
        _tp = _norm_phone(test_phone)
        recipients = [{"name": "Test", "phone": _tp, "total_spent_cents": 0}] if _tp and _tp.isdigit() else []
        suppressed_count = 0
        audience = "test"
        name = f"{name} (test)"

    # ── Dry-run / preview — DEFAULT. Nothing is sent. ────────────────────────
    if dry_run:
        return {
            "dry_run": True,
            "template": template,
            "audience": audience,
            "recipient_count": len(recipients),
            "suppressed_count": suppressed_count,
            "sample": [
                {"name": c.get("name") or "Unknown", "phone": c.get("phone")}
                for c in recipients[:10]
            ],
            "note": "Preview only — no messages sent. Send with dry_run=false to go live.",
        }

    # ── Live send ────────────────────────────────────────────────────────────
    # Channel resolution: Meta (templates) preferred; Twilio fallback for
    # free-text broadcasts (works within the 24h customer session window).
    body_text = body.get("body", "")  # free-text alternative to a Meta template
    # Optional trackable link — one short code per recipient, so a click can be attributed to
    # who clicked (not just "someone did"). On free text the link is embedded directly; on a
    # Meta template it fills a URL button's dynamic {{1}} suffix (the button's own URL must be
    # authored as "{public_base_url}/l/{{1}}" at template-creation time for this to redirect
    # through Vula's tracker — see wa_templates.py / the 📨 Templates tab help text).
    target_url = (body.get("target_url") or "").strip()
    header_image_url = (body.get("header_image_url") or "").strip()
    from vula.api.whatsapp import _get_tenant_wa_creds
    creds = await _get_tenant_wa_creds(tenant_id)
    use_twilio = (not creds) and bool(
        getattr(settings, "twilio_account_sid", "")
        and getattr(settings, "twilio_auth_token", "")
        and getattr(settings, "twilio_whatsapp_from", "")
    )

    if not creds and not use_twilio:
        raise HTTPException(
            status_code=503,
            detail="No WhatsApp channel configured (connect Meta or set Twilio creds).",
        )

    # Rich-media metadata for the selected template (header image, buttons needing a dynamic
    # parameter) — looked up once, not per-recipient.
    tpl_row = None
    if not use_twilio and template:
        try:
            tpl_row = (db.table("commerce_wa_templates").select("header_type,buttons")
                       .eq("tenant_id", tenant_id).eq("name", template)
                       .limit(1).execute().data or [None])[0]
        except Exception:
            tpl_row = None
    tpl_needs_button_param = bool(
        tpl_row and tpl_row.get("buttons") and
        any("{{1}}" in (b.get("url") or "") for b in tpl_row["buttons"] if b.get("type") == "URL")
    )

    log_id = str(uuid4())
    db.table("commerce_broadcast_logs").insert({
        "id": log_id, "tenant_id": tenant_id, "name": name,
        "template_name": template or "(free-text)", "audience_filter": audience,
        "status": "sending", "recipient_count": len(recipients),
    }).execute()

    sent = failed = 0
    errors: list[str] = []
    recipient_rows: list[dict] = []
    # Pace sends — Meta's Cloud API throughput cap (~80 msg/s on standard tier) and per-recipient
    # pair-rate limits mean a tight unthrottled loop risks silent throttling/blocking at real list
    # sizes (confirmed gap, 2026-07-15: this loop had zero pacing before). 10/s is comfortably under
    # every published tier while still finishing a 1,000-contact batch in under 2 minutes.
    import asyncio as _asyncio
    async with httpx.AsyncClient(timeout=15.0) as client:
        for i, c in enumerate(recipients):
            if i:
                await _asyncio.sleep(0.1)
            number = _norm_phone(c.get("phone"))
            short_code = None
            try:
                if use_twilio:
                    # Twilio free-text broadcast
                    from_addr = settings.twilio_whatsapp_from
                    if not from_addr.startswith("whatsapp:"):
                        from_addr = f"whatsapp:{from_addr}"
                    msg = body_text or f"Hi from {name}!"
                    if target_url:
                        short_code = uuid4().hex[:10]
                        msg = f"{msg}\n\n{settings.public_base_url.rstrip('/')}/l/{short_code}"
                    resp = await client.post(
                        f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json",
                        auth=(settings.twilio_account_sid, settings.twilio_auth_token),
                        data={"From": from_addr, "To": f"whatsapp:+{number}", "Body": msg[:1600]},
                    )
                else:
                    # Meta template broadcast (compliant for proactive sends)
                    tpl_components: list[dict] = []
                    if tpl_row and tpl_row.get("header_type") == "IMAGE" and header_image_url:
                        tpl_components.append({"type": "header", "parameters": [
                            {"type": "image", "image": {"link": header_image_url}}]})
                    if tpl_needs_button_param and target_url:
                        short_code = uuid4().hex[:10]
                        tpl_components.append({"type": "button", "sub_type": "url", "index": "0",
                                               "parameters": [{"type": "text", "text": short_code}]})
                    tpl_payload: dict = {"name": template, "language": {"code": language}}
                    if tpl_components:
                        tpl_payload["components"] = tpl_components
                    resp = await client.post(
                        f"https://graph.facebook.com/v19.0/{creds['phone_id']}/messages",
                        headers={"Authorization": f"Bearer {creds['token']}",
                                 "Content-Type": "application/json"},
                        json={
                            "messaging_product": "whatsapp",
                            "to": number,
                            "type": "template",
                            "template": tpl_payload,
                        },
                    )
                if resp.is_success:
                    sent += 1
                    wamid = None
                    try:
                        if use_twilio:
                            wamid = resp.json().get("sid")
                        else:
                            wamid = (resp.json().get("messages") or [{}])[0].get("id")
                    except Exception:
                        wamid = None
                    row = {"tenant_id": tenant_id, "broadcast_id": log_id,
                          "phone": number, "wamid": wamid, "status": "sent"}
                    if short_code:
                        row["short_code"] = short_code
                        row["target_url"] = target_url
                    recipient_rows.append(row)
                else:
                    failed += 1
                    recipient_rows.append({"tenant_id": tenant_id, "broadcast_id": log_id,
                                           "phone": number, "status": "failed",
                                           "error": resp.text[:200]})
                    if len(errors) < 3:
                        errors.append(resp.text[:200])
            except Exception as exc:
                failed += 1
                recipient_rows.append({"tenant_id": tenant_id, "broadcast_id": log_id,
                                       "phone": number, "status": "failed", "error": str(exc)[:200]})
                if len(errors) < 3:
                    errors.append(str(exc)[:200])

    # Persist per-recipient rows so Meta status callbacks can update delivery/read.
    if recipient_rows:
        try:
            db.table("commerce_broadcast_recipients").insert(recipient_rows).execute()
        except Exception as exc:
            log.debug("recipient rows insert retrying without click-tracking cols (run migration 064?): %s", exc)
            for r in recipient_rows:
                r.pop("short_code", None)
                r.pop("target_url", None)
            try:
                db.table("commerce_broadcast_recipients").insert(recipient_rows).execute()
            except Exception as exc2:
                log.debug("recipient rows insert skipped (run migration 020?): %s", exc2)

    db.table("commerce_broadcast_logs").update({
        "status": "sent" if sent else "failed",
        "sent_count": sent,
        "failed_count": failed,
        "last_error": errors[0] if errors else None,
    }).eq("id", log_id).execute()

    return {
        "broadcast_id": log_id, "dry_run": False,
        "channel": "twilio" if use_twilio else "meta",
        "template": template or "(free-text)",
        "audience": audience, "recipient_count": len(recipients),
        "suppressed_count": suppressed_count,
        "sent": sent, "failed": failed,
        "errors": errors or None,
    }


# ── Scheduled & recurring campaigns ───────────────────────────────────────────

def _advance(next_run_at: str, recurrence: str):
    """Next fire time for a recurring campaign, or None for 'once'."""
    from datetime import datetime, timezone, timedelta
    delta = {"daily": timedelta(days=1), "weekly": timedelta(weeks=1),
             "monthly": timedelta(days=30)}.get(recurrence)
    if not delta:
        return None
    nxt = datetime.fromisoformat(next_run_at.replace("Z", "+00:00")) + delta
    now = datetime.now(timezone.utc)
    while nxt <= now:               # catch up if runs were missed
        nxt += delta
    return nxt.isoformat()


@router.get("/{tenant_id}/admin/onboarding/status")
async def admin_onboarding_status(tenant_id: str):
    """Onboarding funnel counts for the dashboard panel."""
    from vula.commerce import onboarding
    return {"tenant_id": tenant_id, "stats": onboarding.onboarding_stats(tenant_id)}


@router.post("/{tenant_id}/admin/onboarding/send-batch")
async def admin_onboarding_send_batch(tenant_id: str, body: dict):
    """Send the intro/opt-in template to the next N un-invited contacts. Staci controls when + how
    many (the number ramps gently, protecting quality rating). Pass test_phone to preview to a
    single number without touching the queue."""
    from vula.commerce import onboarding
    b = body or {}
    result = await onboarding.send_batch(
        tenant_id, template=b.get("template") or "oth_intro_optin",
        language=b.get("language") or "en", limit=int(b.get("batch_size") or 25),
        test_phone=b.get("test_phone"))
    return {"tenant_id": tenant_id, **result}


@router.post("/{tenant_id}/admin/recipes")
async def admin_add_recipe(tenant_id: str, body: dict):
    """Staci adds a recipe → ingested into the tenant KB so the assistant can recommend it while
    a customer is ordering."""
    b = body or {}
    title = (b.get("title") or "").strip()
    text = (b.get("text") or "").strip()
    if not (title and text):
        raise HTTPException(status_code=400, detail="title and text are required")
    slug = "".join(c if c.isalnum() else "-" for c in title.lower()).strip("-")[:60] or "recipe"
    from vula.ingestion.pipeline import VulaIngestionPipeline
    await VulaIngestionPipeline(tenant_id=tenant_id).ingest_text(
        content=f"# {title}\n\n{text}\n\n(Off the Hook recipe)", filename=f"recipe-{slug}.md")
    return {"ok": True, "title": title}


@router.get("/{tenant_id}/admin/campaigns")
async def admin_list_campaigns(tenant_id: str):
    try:
        rows = (service._client().table("commerce_campaigns").select("*")
                .eq("tenant_id", tenant_id).order("next_run_at").limit(200).execute().data or [])
    except Exception as exc:
        log.debug("campaigns list skipped (run migration 021?): %s", exc)
        rows = []
    return {"tenant_id": tenant_id, "campaigns": rows}


@router.post("/{tenant_id}/admin/campaigns")
async def admin_create_campaign(tenant_id: str, body: dict):
    template = body.get("template_name", "")
    text = body.get("body", "")
    if not template and not text:
        raise HTTPException(status_code=400, detail="template_name or body required")
    run_at = body.get("run_at") or body.get("next_run_at")
    if not run_at:
        raise HTTPException(status_code=400, detail="run_at (ISO datetime) required")
    row = {
        "tenant_id": tenant_id, "name": body.get("name") or template or "Campaign",
        "template_name": template or None, "body": text or None,
        "language": body.get("language", "en"),
        "audience_filter": body.get("audience_filter", "all"),
        "recurrence": body.get("recurrence", "once"),
        "next_run_at": run_at, "active": True, "created_by": body.get("created_by"),
    }
    res = service._client().table("commerce_campaigns").insert(row).execute()
    return res.data[0] if res.data else {"error": "insert failed"}


@router.delete("/{tenant_id}/admin/campaigns/{campaign_id}")
async def admin_delete_campaign(tenant_id: str, campaign_id: str):
    service._client().table("commerce_campaigns").update(
        {"active": False}).eq("id", campaign_id).execute()
    return {"id": campaign_id, "active": False}


async def process_due_campaigns() -> int:
    """Fire any campaigns whose next_run_at has passed; advance/deactivate. Returns count."""
    from datetime import datetime, timezone
    db = service._client()
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        due = (db.table("commerce_campaigns").select("*")
               .eq("active", True).lte("next_run_at", now_iso).limit(50).execute().data or [])
    except Exception as exc:
        log.debug("campaign poll skipped (run migration 021?): %s", exc)
        return 0
    n = 0
    for camp in due:
        try:
            await admin_send_broadcast(camp["tenant_id"], {
                "dry_run": False, "template_name": camp.get("template_name") or "",
                "body": camp.get("body") or "",
                "audience_filter": camp.get("audience_filter") or "all",
                "language": camp.get("language") or "en",
                "name": camp.get("name") or "Campaign"})
            n += 1
        except Exception as exc:
            log.warning("campaign %s send failed: %s", camp.get("id"), exc)
        upd = {"last_run_at": now_iso}
        nxt = _advance(camp["next_run_at"], camp.get("recurrence") or "once")
        upd["next_run_at" if nxt else "active"] = nxt if nxt else False
        try:
            db.table("commerce_campaigns").update(upd).eq("id", camp["id"]).execute()
        except Exception:
            pass
    return n


@router.post("/cron/campaigns")
async def cron_process_campaigns():
    """Backstop trigger for the scheduler (also runs in-process every 60s)."""
    return {"processed": await process_due_campaigns()}


# ── Scheduled system jobs (⏰ Scheduling tab) ─────────────────────────────────
# Per-tenant config for the 5 built-in WhatsApp jobs (delivery briefing, unpaid chase,
# low stock, weekly specials reminder, sales summary) — template + fire time were Python
# constants until migration 069. Body is a plain dict, same reason as wa-templates above.

@router.get("/{tenant_id}/admin/scheduled-jobs")
async def admin_list_scheduled_jobs(tenant_id: str):
    """All 5 system jobs' effective config (defaults where unset) + the tenant's APPROVED
    templates so the UI can offer a valid picker in one round trip."""
    from vula.commerce import job_config, wa_templates
    approved = []
    try:
        approved = [t["name"] for t in await wa_templates.list_templates(tenant_id)
                    if t.get("status") == "APPROVED"]
    except Exception as exc:
        log.debug("approved template list skipped: %s", exc)
    return {"tenant_id": tenant_id,
            "jobs": list(job_config.get_configs(tenant_id).values()),
            "approved_templates": approved}


@router.post("/{tenant_id}/admin/scheduled-jobs")
async def admin_upsert_scheduled_job(tenant_id: str, body: dict):
    """Update one job's schedule/template/enabled state. template_name must be one of the
    tenant's APPROVED templates (or null to use the built-in default)."""
    from vula.commerce import job_config, wa_templates
    b = body or {}
    job_type = (b.get("job_type") or "").strip()
    if job_type not in job_config.JOB_TYPES:
        raise HTTPException(status_code=400,
                            detail=f"job_type must be one of {sorted(job_config.JOB_TYPES)}")
    kind = job_config.JOB_TYPES[job_type]["kind"]
    patch: dict = {}
    if "enabled" in b:
        patch["enabled"] = bool(b["enabled"])
    if "template_name" in b:
        tpl = (b.get("template_name") or "").strip() or None
        if tpl:
            try:
                approved = {t["name"] for t in await wa_templates.list_templates(tenant_id)
                            if t.get("status") == "APPROVED"}
            except Exception:
                approved = set()
            if tpl not in approved:
                raise HTTPException(status_code=400,
                                    detail=f"'{tpl}' is not an APPROVED template for this tenant")
        patch["template_name"] = tpl
    for field, lo, hi, kinds in (("hour", 0, 23, ("daily", "weekly")),
                                 ("minute", 0, 59, ("daily", "weekly")),
                                 ("day_of_week", 0, 6, ("weekly",)),
                                 ("interval_minutes", 5, 1440, ("interval",))):
        if field in b and b[field] is not None:
            if kind not in kinds:
                raise HTTPException(status_code=400,
                                    detail=f"{field} does not apply to a {kind} job")
            try:
                v = int(b[field])
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail=f"{field} must be an integer")
            if not (lo <= v <= hi):
                raise HTTPException(status_code=400, detail=f"{field} must be {lo}–{hi}")
            patch[field] = v
    if not patch:
        raise HTTPException(status_code=400, detail="nothing to update")
    result = job_config.upsert_config(tenant_id, job_type, patch)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


# ── Custom audience segments ──────────────────────────────────────────────────

@router.get("/{tenant_id}/admin/segments")
async def admin_list_segments(tenant_id: str):
    try:
        rows = (service._client().table("commerce_segments").select("*")
                .eq("tenant_id", tenant_id).order("created_at").limit(200).execute().data or [])
    except Exception as exc:
        log.debug("segments list skipped (run migration 022?): %s", exc)
        rows = []
    return {"tenant_id": tenant_id, "segments": rows}


@router.post("/{tenant_id}/admin/segments")
async def admin_create_segment(tenant_id: str, body: dict):
    name = (body.get("name") or "").strip()
    criteria = body.get("criteria") or {}
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    if not isinstance(criteria, dict) or not criteria:
        raise HTTPException(status_code=400, detail="at least one criterion required")
    row = {"tenant_id": tenant_id, "name": name, "criteria": criteria,
           "created_by": body.get("created_by")}
    res = service._client().table("commerce_segments").insert(row).execute()
    return res.data[0] if res.data else {"error": "insert failed"}


@router.delete("/{tenant_id}/admin/segments/{segment_id}")
async def admin_delete_segment(tenant_id: str, segment_id: str):
    service._client().table("commerce_segments").delete().eq("id", segment_id).execute()
    return {"id": segment_id, "deleted": True}


# ── Scheduled Jobs ──────────────────────────────────────────────────────────

@router.post("/{tenant_id}/jobs/abandoned-carts")
async def job_abandoned_carts(tenant_id: str):
    """Scan for abandoned carts and trigger notifications."""
    abandoned = await service.get_abandoned_carts(tenant_id, hours_old=1)
    # Filter for carts that haven't received recovery yet
    to_notify = [c for c in abandoned if not (c.get("metadata") or {}).get("recovery_sent")]

    count = 0
    for cart in to_notify:
        # In a real app, you'd fire a WhatsApp template here or n8n
        # For now, we'll just mark them so we don't repeat
        meta = cart.get("metadata") or {}
        meta["recovery_sent"] = True
        service._client().table("commerce_carts") \
            .update({"metadata": meta, "updated_at": service._now()}) \
            .eq("id", cart["id"]).execute()
        count += 1

    return {"ok": True, "processed": count, "found": len(abandoned)}


@router.post("/{tenant_id}/jobs/reorder-reminders")
async def job_reorder_reminders(tenant_id: str):
    """Scan for customers who ordered 7 days ago."""
    candidates = await service.get_reorder_candidates(tenant_id, days_ago=7)
    # logic to fire WhatsApp messages via n8n or direct
    return {"ok": True, "candidates": len(candidates)}


_last_stock_alert: dict[str, str] = {}   # tenant → date, so we alert at most once a day


async def _process_stock_alerts(tenant_id: str, force: bool = False) -> int:
    """Alert the team about low-stock items (at most once per day per tenant)."""
    from datetime import date as _date
    low = await service.get_low_stock_products(tenant_id, threshold=5)
    if not low:
        return 0
    today = _date.today().isoformat()
    if not force and _last_stock_alert.get(tenant_id) == today:
        return 0   # already alerted today
    lines = "\n".join(f"• {p['name']} — {p.get('stock_quantity', 0)} left" for p in low[:20])
    try:
        from vula.integrations.notify import notify_team
        await notify_team(tenant_id, "low_stock",
                          f"⚠️ Low stock ({len(low)} item{'s' if len(low) != 1 else ''}):\n{lines}\n\nTime to restock.")
        _last_stock_alert[tenant_id] = today
    except Exception as exc:
        log.debug("stock alert send skipped: %s", exc)
    return len(low)


_REMINDER_STAGE_ORDER = {None: 0, "pre_due": 1, "due": 2, "firm": 3, "escalated": 4}


def _reminder_stage_for(days_over: int) -> Optional[str]:
    """days_over is (today - due_date).days — negative before the due date."""
    if days_over < -3:
        return None
    if days_over < 0:
        return "pre_due"
    if days_over < 7:
        return "due"
    if days_over < 14:
        return "firm"
    return "escalated"


def _reminder_message(stage: str, iv: dict, days_over: int) -> str:
    name = iv.get("customer_name") or "there"
    num = iv.get("invoice_number")
    amount = (iv.get("total_cents") or 0) / 100
    if stage == "pre_due":
        days_left = abs(days_over)
        return (f"Hi {name}, just a heads-up — invoice *{num}* for R{amount:.2f} is due in "
                f"{days_left} day{'s' if days_left != 1 else ''}. Let us know if you have any "
                f"questions. Thank you! 🙏")
    if stage == "due":
        return (f"Hi {name}, a friendly reminder that invoice *{num}* for R{amount:.2f} is now "
                f"past its due date. Please arrange payment when you can — reply here with any "
                f"questions. Thank you! 🙏")
    if stage == "firm":
        return (f"Hi {name}, invoice *{num}* for R{amount:.2f} is now {days_over} days overdue. "
                f"Please arrange payment as soon as possible, or reply here if there's an issue "
                f"we should know about.")
    return (f"Hi {name}, invoice *{num}* for R{amount:.2f} is now {days_over} days overdue. "
            f"Please arrange payment urgently, or reply here so we can sort this out together.")


async def _process_overdue_invoices(tenant_id: str) -> int:
    """Staged overdue-invoice reminder cadence: pre_due (-3d) / due (0d) / firm (+7d) /
    escalated (+14d, also alerts the internal team). Each stage fires exactly once per invoice,
    claimed via a conditional update matching the invoice's previous reminder_stage — the same
    idempotency shape commerce_orders.followup_sent_at uses, generalized from a boolean to an
    ordered stage so a daily job re-run (or two overlapping runs) can never double-send.
    Requires migration 131 (reminder_stage/last_reminded_at columns)."""
    from datetime import date as _date
    db = service._client()
    today = _date.today()
    try:
        rows = (db.table("commerce_invoices")
                .select("id,invoice_number,customer_name,customer_phone,total_cents,due_date,"
                        "doc_type,status,reminder_stage")
                .eq("tenant_id", tenant_id).in_("status", ["sent", "overdue"]).execute().data or [])
    except Exception as exc:
        log.debug("overdue scan skipped: %s", exc)
        return 0

    reminded = 0
    for iv in rows:
        if (iv.get("doc_type") or "invoice") != "invoice":
            continue   # only invoices go overdue — not quotes/proformas
        due = iv.get("due_date")
        if not due:
            continue
        try:
            due_date = _date.fromisoformat(str(due)[:10])
        except Exception:
            continue
        days_over = (today - due_date).days
        target = _reminder_stage_for(days_over)
        current = iv.get("reminder_stage")
        if target is None or _REMINDER_STAGE_ORDER[target] <= _REMINDER_STAGE_ORDER.get(current, 0):
            continue

        patch = {"reminder_stage": target, "last_reminded_at": service._now(), "updated_at": service._now()}
        if days_over >= 0 and iv.get("status") == "sent":
            patch["status"] = "overdue"
        q = db.table("commerce_invoices").update(patch).eq("id", iv["id"]).eq("tenant_id", tenant_id)
        q = q.is_("reminder_stage", "null") if current is None else q.eq("reminder_stage", current)
        try:
            claimed = q.execute().data
        except Exception as exc:
            log.debug("overdue stage claim failed for %s: %s", iv.get("id"), exc)
            continue
        if not claimed:
            continue   # another run already claimed this stage — race-safe skip

        phone = (iv.get("customer_phone") or "").strip()
        if phone:
            try:
                from vula.api.whatsapp import _send_reply
                await _send_reply(phone, _reminder_message(target, iv, days_over), tenant_id)
                reminded += 1
            except Exception as exc:
                log.debug("overdue reminder send failed: %s", exc)

        if target == "escalated":
            try:
                from vula.integrations.notify import notify_team
                await notify_team(tenant_id, "invoice_overdue_escalated", (
                    f"🔴 Invoice {iv.get('invoice_number')} for {iv.get('customer_name') or 'a customer'} "
                    f"(R{(iv.get('total_cents') or 0) / 100:.2f}) is now {days_over} days overdue with no "
                    f"response to reminders. May need a personal follow-up call."))
            except Exception as exc:
                log.debug("overdue escalation alert failed: %s", exc)

    if reminded:
        log.info("Overdue invoices: reminded %d customer(s) for %s", reminded, tenant_id)
    return reminded


@router.post("/{tenant_id}/jobs/stock-alerts")
async def job_stock_alerts(tenant_id: str):
    """Alert the team about low-stock items (scheduler + manual trigger)."""
    return {"ok": True, "alerted": await _process_stock_alerts(tenant_id, force=True)}


@router.post("/{tenant_id}/jobs/overdue-invoices")
async def job_overdue_invoices(tenant_id: str):
    """Mark past-due invoices overdue + remind customers (scheduler + manual trigger)."""
    return {"ok": True, "reminded": await _process_overdue_invoices(tenant_id)}


@router.post("/{tenant_id}/jobs/weekly-specials")
async def job_weekly_specials(tenant_id: str):
    """Fire the weekly specials broadcast."""
    # This logic matches OTH-05: Monday 07:00
    products = await service.list_products(tenant_id, in_stock_only=True, statuses={"active"})
    specials = [p for p in products if p.get("is_daily_catch")]
    # Fire n8n broadcast
    return {"ok": True, "specials_count": len(specials)}



# ── Customers (client list / CRM) ─────────────────────────────────────────────

def _norm_phone(p: Optional[str]) -> str:
    """Normalise a phone number to digits-only E.164-ish (SA: 0xx → 27xx)."""
    if not p:
        return ""
    n = "".join(ch for ch in p if ch.isdigit())
    if n.startswith("0"):
        n = "27" + n[1:]
    return n


_AGG_CACHE: dict[str, tuple[float, dict]] = {}
_AGG_TTL = 45.0   # seconds — the audience is stable; opt-outs are applied fresh downstream


async def _aggregate_customers(tenant_id: str, use_cache: bool = True) -> dict[str, dict]:
    """Merge orders + conversation sessions + imported contacts into one contact per phone.

    Shared by the Customers tab and the broadcast sender so the audience shown in the dashboard is
    exactly the audience a broadcast reaches. Cached for a few seconds and each source is capped, so
    it stays fast as data grows (this used to pull *every* order + session + contact on every call).
    Opt-outs are applied downstream (`_suppressed_phones`), so a stale cache never mis-sends.
    """
    import time as _time
    if use_cache:
        hit = _AGG_CACHE.get(tenant_id)
        if hit and (_time.monotonic() - hit[0]) < _AGG_TTL:
            return hit[1]

    db = service._client()
    customers: dict[str, dict] = {}

    # Orders → spend & recency (cap to recent so it stays fast as history grows)
    orders = (
        db.table("commerce_orders")
        .select("customer_phone,customer_name,total_cents,status,created_at")
        .eq("tenant_id", tenant_id)
        .order("created_at", desc=True)
        .limit(3000)
        .execute()
    ).data or []
    for o in orders:
        key = _norm_phone(o.get("customer_phone"))
        if not key:
            continue
        c = customers.setdefault(key, {
            "phone": o.get("customer_phone"), "name": o.get("customer_name"),
            "orders": 0, "total_spent_cents": 0, "last_order_at": None,
            "source": "order", "channel": "web",
        })
        if o.get("status") not in ("pending_payment", "cancelled", "refunded"):
            c["orders"] += 1
            c["total_spent_cents"] += int(o.get("total_cents") or 0)
        if o.get("customer_name") and not c.get("name"):
            c["name"] = o["customer_name"]
        ca = o.get("created_at")
        if ca and (not c["last_order_at"] or ca > c["last_order_at"]):
            c["last_order_at"] = ca

    # Conversation sessions → contacts who messaged in (cap to recent)
    sessions = (
        db.table("commerce_conversation_sessions")
        .select("customer_phone,customer_name,channel,updated_at,session_key,preferred_language")
        .eq("tenant_id", tenant_id)
        .order("updated_at", desc=True)
        .limit(5000)
        .execute()
    ).data or []
    for s in sessions:
        key = _norm_phone(s.get("customer_phone") or s.get("session_key"))
        if not key or not key.isdigit():
            continue
        c = customers.setdefault(key, {
            "phone": s.get("customer_phone") or s.get("session_key"),
            "name": s.get("customer_name"), "orders": 0, "total_spent_cents": 0,
            "last_order_at": None, "source": "chat", "channel": s.get("channel") or "whatsapp",
        })
        if s.get("customer_name") and not c.get("name"):
            c["name"] = s["customer_name"]
        if s.get("channel"):
            c["channel"] = s["channel"]
        c["last_seen_at"] = s.get("updated_at")
        # First (newest, since sessions are queried newest-first) non-empty value wins —
        # captured so segments can target by language (migration 052 already stores it per
        # customer, just wasn't pulled into the aggregate used for segmentation until now).
        if s.get("preferred_language") and not c.get("preferred_language"):
            c["preferred_language"] = s["preferred_language"]

    # Imported contact book (existing clients) → merged in, deduped by phone. Guarded so a
    # missing table (migration 046 not yet run) never breaks the Customers tab or broadcasts.
    try:
        contacts = (
            db.table("commerce_contacts")
            .select("phone,name,email,area,product,tags,consent_status")
            .eq("tenant_id", tenant_id).limit(20000).execute()
        ).data or []
    except Exception as exc:
        log.debug("contacts merge skipped (run migration 046?): %s", exc)
        contacts = []
    for ct in contacts:
        key = _norm_phone(ct.get("phone"))
        if not key or not key.isdigit():
            continue
        c = customers.setdefault(key, {
            "phone": ct.get("phone"), "name": ct.get("name"), "orders": 0,
            "total_spent_cents": 0, "last_order_at": None, "source": "import", "channel": "whatsapp",
        })
        if ct.get("name") and not c.get("name"):
            c["name"] = ct["name"]
        if ct.get("email") and not c.get("email"):
            c["email"] = ct["email"]
        # Carry tags + consent so segments and the opt-in campaign can target them.
        c.setdefault("area", ct.get("area"))
        c.setdefault("product", ct.get("product"))
        c.setdefault("tags", ct.get("tags") or [])
        c["consent"] = ct.get("consent_status") or "unknown"

    _AGG_CACHE[tenant_id] = (_time.monotonic(), customers)
    return customers


def _days_since(c, now):
    ts = c.get("last_order_at") or c.get("last_seen_at")
    if not ts:
        return None
    from datetime import datetime
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return (now - dt).days
    except Exception:
        return None


def _apply_criteria(rows: list[dict], crit: dict) -> list[dict]:
    """Filter customers by a custom segment's criteria (all ANDed). Keys: ordered_within_days,
    not_ordered_within_days, min_spend, max_spend, min_orders, channel, tags (list — matches if
    the customer has ANY of the given tags), area, language (both exact match, case-insensitive).
    tags/area come from the imported contact book (commerce_contacts); language from
    commerce_conversation_sessions.preferred_language — both already merged into each customer
    row by _aggregate_customers."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    out = []
    for c in rows:
        ds = _days_since(c, now)
        spend = (c.get("total_spent_cents") or 0) / 100.0
        if (v := crit.get("ordered_within_days")) is not None and (ds is None or ds > v):
            continue
        if (v := crit.get("not_ordered_within_days")) is not None and (ds is not None and ds <= v):
            continue
        if (v := crit.get("min_spend")) is not None and spend < v:
            continue
        if (v := crit.get("max_spend")) is not None and spend > v:
            continue
        if (v := crit.get("min_orders")) is not None and (c.get("orders") or 0) < v:
            continue
        if (v := crit.get("channel")) and c.get("channel") != v:
            continue
        if (v := crit.get("tags")) and not (set(t.lower() for t in v) &
                                            set(t.lower() for t in (c.get("tags") or []))):
            continue
        if (v := crit.get("area")) and (c.get("area") or "").strip().lower() != v.strip().lower():
            continue
        if (v := crit.get("language")) and (c.get("preferred_language") or "").strip().lower() != v.strip().lower():
            continue
        out.append(c)
    return out


def _filter_audience(customers: dict[str, dict], audience) -> list[dict]:
    """Apply a broadcast audience filter: a built-in (all | active_30d | high_value)
    or a custom criteria dict (from a saved segment)."""
    from datetime import datetime, timezone, timedelta

    rows = list(customers.values())
    if isinstance(audience, dict):
        return _apply_criteria(rows, audience)

    now = datetime.now(timezone.utc)

    def _recent(c, days):
        ts = c.get("last_order_at") or c.get("last_seen_at")
        if not ts:
            return False
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return (now - dt) <= timedelta(days=days)
        except Exception:
            return False

    if audience == "active_30d":
        rows = [c for c in rows if _recent(c, 30)]
    elif audience == "high_value":
        rows = [c for c in rows if c["total_spent_cents"] >= 50000]  # > R500
    return rows


def _resolve_audience(tenant_id: str, audience):
    """Turn an audience_filter that's 'seg:<id>' into the saved segment's criteria dict."""
    if isinstance(audience, str) and audience.startswith("seg:"):
        try:
            rows = (service._client().table("commerce_segments").select("criteria")
                    .eq("id", audience[4:]).limit(1).execute().data or [])
            if rows:
                return rows[0].get("criteria") or {}
        except Exception:
            pass
    return audience


@router.get("/{tenant_id}/admin/audience-counts")
async def admin_audience_counts(tenant_id: str):
    """Reachable count (WhatsApp-able, opt-outs excluded) per audience — for the broadcast UI."""
    customers = await _aggregate_customers(tenant_id)
    suppressed = _suppressed_phones(tenant_id)

    def _count(aud):
        n = 0
        for c in _filter_audience(customers, _resolve_audience(tenant_id, aud)):
            ph = _norm_phone(c.get("phone"))
            if ph.isdigit() and ph not in suppressed:
                n += 1
        return n

    counts = {a: _count(a) for a in ("all", "active_30d", "high_value")}
    segments = {}
    try:
        srows = (service._client().table("commerce_segments").select("id")
                 .eq("tenant_id", tenant_id).execute().data or [])
        for sg in srows:
            segments[sg["id"]] = _count(f"seg:{sg['id']}")
    except Exception:
        pass
    return {"counts": counts, "segments": segments}


@router.get("/{tenant_id}/admin/customers")
async def admin_list_customers(
    tenant_id: str,
    audience: str = Query("all"),          # all | active_30d | high_value
    search: Optional[str] = Query(None),
):
    """Aggregated client list for a tenant — the source of truth for broadcasts."""
    customers = await _aggregate_customers(tenant_id)
    rows = _filter_audience(customers, _resolve_audience(tenant_id, audience))

    if search:
        s = search.lower()
        rows = [c for c in rows if s in (c.get("name") or "").lower() or s in (c.get("phone") or "")]

    rows.sort(key=lambda c: c["total_spent_cents"], reverse=True)
    return {
        "customers": rows,
        "count": len(rows),
        "total_all": len(customers),
        "audience": audience,
    }


def _customer_timeline(db, tenant_id: str, phone: str) -> list:
    """Per-customer interaction timeline: orders + invoices + recent chat (tenant-scoped)."""
    digits = _norm_phone(phone)
    events: list = []
    try:
        orders = (db.table("commerce_orders")
                  .select("display_id,total_cents,status,created_at")
                  .eq("tenant_id", tenant_id).eq("customer_phone", phone)
                  .order("created_at", desc=True).limit(50).execute().data or [])
        for o in orders:
            events.append({"type": "order", "at": o.get("created_at"), "title": f"Order {o.get('display_id') or ''}".strip(),
                           "detail": o.get("status"), "amount_cents": o.get("total_cents")})
    except Exception:
        pass
    try:
        invs = (db.table("commerce_invoices")
                .select("invoice_number,total_cents,status,created_at,doc_type")
                .eq("tenant_id", tenant_id).eq("customer_phone", phone)
                .order("created_at", desc=True).limit(50).execute().data or [])
        for iv in invs:
            events.append({"type": iv.get("doc_type") or "invoice", "at": iv.get("created_at"),
                           "title": iv.get("invoice_number"), "detail": iv.get("status"), "amount_cents": iv.get("total_cents")})
    except Exception:
        pass
    # Commerce order-bot conversations (the OTH WhatsApp chats live here).
    try:
        sess = (db.table("commerce_conversation_sessions").select("id")
                .eq("tenant_id", tenant_id).eq("customer_phone", phone).execute().data or [])
        sids = [s["id"] for s in sess]
        if sids:
            cmsgs = (db.table("commerce_conversation_messages").select("role,content,created_at")
                     .eq("tenant_id", tenant_id).in_("session_id", sids)
                     .order("created_at", desc=True).limit(40).execute().data or [])
            for m in cmsgs:
                events.append({"type": "message", "at": m.get("created_at"),
                               "title": ("Vula" if m.get("role") == "assistant" else "Customer"),
                               "detail": (m.get("content") or "")[:200]})
    except Exception:
        pass
    # Knowledge/portal chats (durable store, for non-commerce tenants).
    try:
        from vula.chat.history import get_db
        for m in get_db().get(tenant_id, phone=digits, limit=20):
            events.append({"type": "message", "at": getattr(m, "created_at", None),
                           "title": ("Vula" if m.role == "assistant" else "Customer"),
                           "detail": (m.text or "")[:200]})
    except Exception:
        pass
    events.sort(key=lambda e: e.get("at") or "", reverse=True)
    return events[:80]


@router.get("/{tenant_id}/admin/customers/{phone}/history")
async def admin_customer_history(tenant_id: str, phone: str):
    """Per-customer interaction timeline (tenant-scoped)."""
    return {"phone": phone, "events": _customer_timeline(service._client(), tenant_id, phone)}


@router.get("/{tenant_id}/admin/customers/{phone}/detail")
async def admin_customer_detail(tenant_id: str, phone: str):
    """Full customer profile: lifetime value, order stats, language, note + timeline."""
    db = service._client()
    orders, invs, sess = [], [], []
    try:
        orders = (db.table("commerce_orders")
                  .select("total_cents,status,created_at,customer_name,customer_email")
                  .eq("tenant_id", tenant_id).eq("customer_phone", phone).execute().data or [])
    except Exception:
        pass
    paid = [o for o in orders if o.get("status") not in ("pending_payment", "cancelled", "refunded")]
    ltv = sum(int(o.get("total_cents") or 0) for o in paid)
    paid_count = len(paid)
    dates = sorted(o["created_at"] for o in orders if o.get("created_at"))
    try:
        invs = (db.table("commerce_invoices")
                .select("total_cents,status,doc_type,due_date,paid_at")
                .eq("tenant_id", tenant_id).eq("customer_phone", phone).execute().data or [])
    except Exception:
        pass
    # doc_type=="invoice" only (this table also stores quotes/credit notes for this customer);
    # credit notes net OUT — see admin_stats' identical fix above for the full rationale.
    _inv_credited = sum(int(i.get("total_cents") or 0) for i in invs if i.get("doc_type") == "credit_note")
    inv_outstanding = sum(int(i.get("total_cents") or 0) for i in invs
                          if i.get("doc_type") == "invoice" and i.get("status") in ("sent", "overdue")) - _inv_credited
    from vula.commerce.payment_behavior import customer_payment_behavior
    payment_behavior = customer_payment_behavior([i for i in invs if i.get("doc_type") == "invoice"])
    # Session holds the language preference + internal note (agent_note), keyed by phone.
    lang, note, sess_name = None, None, None
    try:
        sess = (db.table("commerce_conversation_sessions")
                .select("preferred_language,agent_note,customer_name")
                .eq("tenant_id", tenant_id).eq("customer_phone", phone).limit(1).execute().data or [])
        if sess:
            lang = sess[0].get("preferred_language")
            note = sess[0].get("agent_note")
            sess_name = sess[0].get("customer_name")
    except Exception:
        pass
    name = next((o.get("customer_name") for o in orders if o.get("customer_name")), None) or sess_name
    email = next((o.get("customer_email") for o in orders if o.get("customer_email")), None)

    # The actual WhatsApp conversation (last few turns) — Customer-360 depth (UI overhaul P3).
    conversation = []
    try:
        s2 = (db.table("commerce_conversation_sessions").select("id")
              .eq("tenant_id", tenant_id).eq("customer_phone", phone).limit(1).execute().data or [])
        if s2:
            msgs = (db.table("commerce_conversation_messages").select("role,content,created_at")
                    .eq("session_id", s2[0]["id"]).order("created_at", desc=True)
                    .limit(8).execute().data or [])
            conversation = [{"role": m.get("role"), "text": (m.get("content") or "")[:300],
                             "at": m.get("created_at")} for m in reversed(msgs)]
    except Exception:
        pass

    # Broadcast engagement — delivered/read/clicked per campaign this customer received.
    engagement = []
    try:
        recs = (db.table("commerce_broadcast_recipients")
                .select("broadcast_id,status,clicked_at,created_at")
                .eq("tenant_id", tenant_id).eq("phone", phone)
                .order("created_at", desc=True).limit(6).execute().data or [])
        b_ids = list({r["broadcast_id"] for r in recs if r.get("broadcast_id")})
        names = {}
        if b_ids:
            logs = (db.table("commerce_broadcast_logs").select("id,name")
                    .in_("id", b_ids).execute().data or [])
            names = {l["id"]: l.get("name") for l in logs}
        engagement = [{"campaign": names.get(r.get("broadcast_id")) or "Broadcast",
                       "status": "clicked" if r.get("clicked_at") else (r.get("status") or "sent"),
                       "at": r.get("created_at")} for r in recs]
    except Exception:
        pass

    return {
        "profile": {"name": name, "phone": phone, "email": email,
                    "preferred_language": lang,
                    "first_order_at": dates[0] if dates else None,
                    "last_order_at": dates[-1] if dates else None},
        "stats": {"lifetime_value_cents": ltv, "order_count": len(orders),
                  "paid_order_count": paid_count,
                  "avg_order_cents": int(ltv / paid_count) if paid_count else 0,
                  "invoice_outstanding_cents": inv_outstanding},
        "payment_behavior": payment_behavior,
        "note": note or "",
        "events": _customer_timeline(db, tenant_id, phone),
        "conversation": conversation,
        "engagement": engagement,
    }


@router.get("/{tenant_id}/admin/orders/{order_id}/detail")
async def admin_order_detail(tenant_id: str, order_id: str):
    """Order drawer (UI overhaul P3): items + status timeline + the WhatsApp exchange around it."""
    db = service._client()
    rows = (db.table("commerce_orders").select("*")
            .eq("tenant_id", tenant_id).eq("id", order_id).limit(1).execute().data or [])
    if not rows:
        raise HTTPException(status_code=404, detail="order not found")
    o = rows[0]
    try:
        items = (db.table("commerce_order_items").select("product_name,quantity,unit_price_cents")
                 .eq("order_id", order_id).execute().data or [])
    except Exception:
        items = []

    timeline = [{"label": f"Order placed via {o.get('channel') or 'web'}", "at": o.get("created_at")}]
    if o.get("status") not in ("pending_payment", "cancelled"):
        timeline.append({"label": f"Payment — {o.get('payment_method') or 'recorded'}",
                         "at": o.get("paid_at") or o.get("updated_at") or o.get("created_at")})
    if o.get("stock_adjusted"):
        timeline.append({"label": "Stock adjusted", "at": o.get("updated_at")})
    if o.get("followup_sent_at"):
        timeline.append({"label": "Payment chase sent to customer", "at": o["followup_sent_at"]})
    if o.get("status") in ("dispatched", "delivered"):
        timeline.append({"label": o["status"].capitalize(), "at": o.get("updated_at")})
    if o.get("status") in ("cancelled", "refunded"):
        timeline.append({"label": o["status"].capitalize(), "at": o.get("updated_at")})

    conversation = []
    try:
        s2 = (db.table("commerce_conversation_sessions").select("id")
              .eq("tenant_id", tenant_id).eq("customer_phone", o.get("customer_phone"))
              .limit(1).execute().data or [])
        if s2:
            msgs = (db.table("commerce_conversation_messages").select("role,content,created_at")
                    .eq("session_id", s2[0]["id"]).lte("created_at", o.get("created_at") or "9999")
                    .order("created_at", desc=True).limit(4).execute().data or [])
            conversation = [{"role": m.get("role"), "text": (m.get("content") or "")[:240],
                             "at": m.get("created_at")} for m in reversed(msgs)]
    except Exception:
        pass

    return {"order": o, "items": items, "timeline": timeline, "conversation": conversation}


@router.get("/{tenant_id}/admin/broadcasts/{broadcast_id}/recipients")
async def admin_broadcast_recipients(tenant_id: str, broadcast_id: str):
    """Broadcast funnel drawer (UI overhaul P3): per-recipient delivery/read/click outcomes,
    plus attributed revenue (migration 104) — orders placed by a customer within 7 days of
    clicking this broadcast's link (service.py:_attribute_broadcast, set at order creation)."""
    db = service._client()
    recs = (db.table("commerce_broadcast_recipients")
            .select("phone,status,error,clicked_at,click_count")
            .eq("tenant_id", tenant_id).eq("broadcast_id", broadcast_id)
            .limit(1000).execute().data or [])
    funnel = {"sent": len(recs),
              "delivered": sum(1 for r in recs if r.get("status") in ("delivered", "read")),
              "read": sum(1 for r in recs if r.get("status") == "read"),
              "clicked": sum(1 for r in recs if r.get("clicked_at")),
              "failed": sum(1 for r in recs if r.get("status") == "failed")}

    try:
        orders = (db.table("commerce_orders").select("total_cents,status")
                  .eq("tenant_id", tenant_id).eq("attributed_broadcast_id", broadcast_id)
                  .limit(1000).execute().data or [])
        counted = [o for o in orders if o.get("status") not in ("pending_payment", "cancelled", "refunded")]
        funnel["attributed_orders"] = len(counted)
        funnel["attributed_revenue_cents"] = sum(int(o.get("total_cents") or 0) for o in counted)
    except Exception as exc:
        log.debug("broadcast attribution rollup skipped (run migration 104?): %s", exc)
        funnel["attributed_orders"] = 0
        funnel["attributed_revenue_cents"] = 0

    return {"funnel": funnel, "recipients": recs}


@router.post("/{tenant_id}/admin/customers/{phone}/tags")
async def admin_customer_tags(tenant_id: str, phone: str, body: dict):
    """Set a customer's tags + area — the data segments actually filter on (_apply_criteria).
    Writes to commerce_contacts (the imported contact book), upserting on (tenant_id, phone)
    so this works even for a customer who never came from an import (order/chat-only)."""
    tags = [str(t).strip() for t in (body or {}).get("tags") or [] if str(t).strip()]
    area = ((body or {}).get("area") or "").strip() or None
    digits = _norm_phone(phone)
    if not digits:
        raise HTTPException(status_code=400, detail="Invalid phone number")
    try:
        service._client().table("commerce_contacts").upsert(
            {"tenant_id": tenant_id, "phone": digits, "tags": tags, "area": area},
            on_conflict="tenant_id,phone",
        ).execute()
    except Exception as exc:
        return {"error": f"{exc} (run migration 046?)"}
    return {"ok": True, "tags": tags, "area": area}


@router.post("/{tenant_id}/admin/customers/{phone}/note")
async def admin_customer_note(tenant_id: str, phone: str, body: dict):
    """Save an internal CRM note against a customer (shared with the inbox agent_note)."""
    from uuid import uuid4
    note = (body or {}).get("note", "")
    db = service._client()
    try:
        res = (db.table("commerce_conversation_sessions").update({"agent_note": note})
               .eq("tenant_id", tenant_id).eq("customer_phone", phone).execute())
        if not res.data:
            # No session yet (e.g. web-only customer) — create a lightweight one to hold the note.
            db.table("commerce_conversation_sessions").insert({
                "id": str(uuid4()), "tenant_id": tenant_id, "session_key": phone,
                "channel": "web", "customer_phone": phone, "agent_note": note,
            }).execute()
    except Exception as exc:
        return {"error": f"{exc} (run migration 048?)"}
    return {"ok": True, "note": note}


# ── Delivery list ─────────────────────────────────────────────────────────────

@router.get("/{tenant_id}/admin/delivery-list")
async def admin_delivery_list(
    tenant_id: str,
    date: Optional[str] = Query(None, description="YYYY-MM-DD, defaults to today"),
):
    """Today's delivery run — orders with items and paid/unpaid status."""
    from datetime import date as _date
    target = date or _date.today().isoformat()
    orders = await service.get_delivery_list(tenant_id, target)
    _PAID = {"paid", "confirmed", "packing", "dispatched", "delivered"}
    paid   = [o for o in orders if o["status"] in _PAID]
    unpaid = [o for o in orders if o["status"] == "pending_payment"]
    return {
        "date": target,
        "orders": orders,
        "total": len(orders),
        "paid_count": len(paid),
        "unpaid_count": len(unpaid),
        "paid_revenue_cents": sum(o["total_cents"] for o in paid),
        "unpaid_revenue_cents": sum(o["total_cents"] for o in unpaid),
    }


# ── AI Smart Scanner ──────────────────────────────────────────────────────────
# Photograph any document → AI extracts structured data.
# Handles: supplier invoices/receipts (→ expense), delivery notes (→ stock),
# customer order notes (→ order), business cards (→ contact).

class ScanRequest(BaseModel):
    image_base64: str                    # data URL or raw base64
    doc_type: str = "auto"               # 'auto'|'receipt'|'delivery_note'|'invoice'|'order'
    tenant_id: Optional[str] = None


_SCAN_PROMPTS = {
    "receipt": (
        "This is a supplier receipt or expense slip for a South African food business. "
        "Extract: supplier name, date (YYYY-MM-DD), total amount in Rands, and a best-guess "
        "category from [stock, delivery, packaging, marketing, equipment, staff, rent, utilities, other]. "
        "Also list individual line items if visible."
    ),
    "delivery_note": (
        "This is a delivery note / packing slip for fresh food stock (fish, chicken, seafood). "
        "Extract each product line: product name, quantity, and unit (kg or pack). "
        "Also extract supplier name and date if visible."
    ),
    "invoice": (
        "This is an invoice. Extract: customer/supplier name, invoice number, date, due date, "
        "each line item (description, qty, unit price in Rands, line total), subtotal, VAT, and total."
    ),
    "order": (
        "This is a handwritten or printed customer order for a food business. "
        "Extract: customer name, phone if present, and each item ordered (product, quantity, unit)."
    ),
}


# Moved to vula/commerce/extraction_quality.py (migration 102 follow-on) so the email/WhatsApp
# document pipeline can share the same arithmetic-verification heuristic — kept as a local alias
# so every existing call site below is unchanged.
from vula.commerce.extraction_quality import scan_quality_ok as _scan_quality_ok


@router.post("/{tenant_id}/admin/scan")
async def admin_smart_scan(tenant_id: str, body: ScanRequest):
    """
    AI Smart Scanner — vision LLM extracts structured data from a photographed document.
    Returns JSON the frontend can use to pre-fill an expense, stock update, or invoice.
    """
    import json as _json
    import re as _re

    img = body.image_base64
    if img.startswith("data:"):
        img = img.split(",", 1)[-1]  # strip data URL prefix

    # Business-type-aware context, so extraction fits the tenant (architecture/trades vs food) —
    # was hardcoded to "food business", which biased units + categories for other verticals.
    try:
        from vula.api.tenants import get_config
        _bt = (get_config(tenant_id) or {}).get("business_type") or "other"
    except Exception:
        _bt = "other"
    _BIZ = {
        "food": ("a South African food / restaurant business", "kg|pack|each"),
        "retail": ("a South African retail / e-commerce business", "each|pack|unit"),
        "services": ("a South African professional services / architecture firm", "hr|day|item|m²|no."),
        "trades": ("a South African construction / trades business", "m|m²|m³|kg|no.|hr|item"),
        "health": ("a South African health / wellness practice", "session|item|hr"),
        "other": ("a South African small business", "each|kg|hr|item"),
    }
    biz, unit_hint = _BIZ.get(_bt, _BIZ["other"])

    # Build instruction based on doc type
    if body.doc_type == "auto":
        instruction = (
            "Identify what kind of business document this is (receipt, delivery_note, invoice, or order) "
            f"for {biz}, then extract all relevant structured data from it."
        )
    else:
        instruction = _SCAN_PROMPTS.get(body.doc_type, _SCAN_PROMPTS["receipt"])

    system = (
        f"You are a document-extraction engine for {biz}. "
        "Look at the image and return ONLY a valid JSON object — no prose, no markdown fences. "
        "Use this schema:\n"
        "{\n"
        '  "doc_type": "receipt|delivery_note|invoice|order",\n'
        '  "supplier": string|null,\n'
        '  "tax_id": string|null,  // supplier VAT / tax registration number if shown\n'
        '  "customer": string|null,\n'
        '  "date": "YYYY-MM-DD"|null,\n'
        '  "due_date": "YYYY-MM-DD"|null,\n'
        '  "category": string|null,\n'
        '  "total_cents": integer|null,  // Rands × 100\n'
        '  "vat_cents": integer|null,\n'
        '  "line_items": [{"description": string, "quantity": number, "unit": "' + unit_hint + '"|null, "unit_price_cents": integer|null, "total_cents": integer|null}],\n'
        '  "confidence": "high|medium|low",\n'
        '  "notes": string|null\n'
        "}\n"
        "All money values are in CENTS (Rands multiplied by 100). If unsure, use null."
    )

    try:
        import litellm
        from core.llm_router import resolve_vision_route, resolve_cloud_vision_route
        litellm.drop_params = True

        async def _extract(model, api_key, api_base):
            """One vision pass → parsed dict (with 'raw' on parse failure)."""
            response = await litellm.acompletion(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": instruction},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}"}},
                        ],
                    },
                ],
                temperature=0.1,
                max_tokens=2000,
                api_key=api_key,
                api_base=api_base,
            )
            content = response.choices[0].message.content or "{}"
            content = _re.sub(r"<think>.*?</think>", "", content, flags=_re.DOTALL)
            content = _re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=_re.MULTILINE).strip()
            try:
                return _json.loads(content)
            except Exception:
                m = _re.search(r"\{.*\}", content, _re.DOTALL)
                return _json.loads(m.group(0)) if m else {"raw": content, "confidence": "low"}

        # ── Local-first: try the on-device vision model (free, private) ──────
        model, api_key, api_base = await resolve_vision_route()
        extracted = await _extract(model, api_key, api_base)
        used_model = model
        escalated = False

        # ── Escalate to cloud if the local read looks unreliable ─────────────
        # Triggers: explicit low confidence, missing/zero total, no line items,
        # or an unparseable response. Only escalates when the primary was local
        # and a cloud route is actually configured.
        if model.startswith("ollama/") and not _scan_quality_ok(extracted):
            cloud = resolve_cloud_vision_route()
            if cloud:
                log.info("Smart scan: local read weak (%s) — escalating to cloud %s",
                         extracted.get("confidence"), cloud[0])
                try:
                    cloud_extracted = await _extract(*cloud)
                    if _scan_quality_ok(cloud_extracted) or cloud_extracted.get("total_cents"):
                        extracted = cloud_extracted
                        used_model = cloud[0]
                        escalated = True
                except Exception as cloud_exc:
                    log.warning("Cloud vision escalation failed: %s", cloud_exc)

        return {"ok": True, "extracted": extracted, "model": used_model, "escalated": escalated}

    except Exception as exc:
        log.error("Smart scan failed for %s: %s", tenant_id, exc)
        raise HTTPException(status_code=502, detail=f"Scan failed: {exc}")


# ── Scan → Commit (scan result → expense/invoice + KB ingest) ────────────────

@router.post("/{tenant_id}/admin/scan/commit")
async def admin_scan_commit(tenant_id: str, body: dict):
    """
    Commit a smart-scan result into the books — a thin wrapper around
    service.commit_inbound_document(), the shared commit path also used by the
    email/WhatsApp/dashboard-upload document pipelines (migration 102).

    Body:
        extracted:    the JSON from /admin/scan
        image_base64: optional — if provided, ingests into KB as text
        auto_commit:  bool (default true) — if false, returns preview only
    """
    extracted = body.get("extracted", {})
    auto_commit = body.get("auto_commit", True)
    if not extracted:
        raise HTTPException(status_code=400, detail="No extracted data provided.")
    return await service.commit_inbound_document(
        tenant_id, extracted, auto_commit=auto_commit, source="scanner",
    )


@router.get("/{tenant_id}/admin/expenses/due")
async def admin_expenses_due(
    tenant_id: str,
    days_ahead: int = Query(30, ge=0, le=365),
):
    """Return all pending payments due within the next N days — for the Budget 'Due' tab."""
    from datetime import date, timedelta
    db = service._client()
    cutoff = str(date.today() + timedelta(days=days_ahead))
    today = str(date.today())

    # Overdue expenses
    overdue = db.table("commerce_expenses").select("*") \
        .eq("tenant_id", tenant_id) \
        .eq("status", "pending") \
        .lt("due_date", today) \
        .not_.is_("due_date", "null") \
        .order("due_date").execute()

    # Due soon
    upcoming = db.table("commerce_expenses").select("*") \
        .eq("tenant_id", tenant_id) \
        .eq("status", "pending") \
        .gte("due_date", today) \
        .lte("due_date", cutoff) \
        .not_.is_("due_date", "null") \
        .order("due_date").execute()

    # Inbound invoices due
    inv_due = db.table("commerce_invoices").select("*") \
        .eq("tenant_id", tenant_id) \
        .eq("direction", "inbound") \
        .in_("status", ["draft", "sent"]) \
        .lte("due_date", cutoff) \
        .not_.is_("due_date", "null") \
        .order("due_date").execute()

    overdue_list = overdue.data or []
    upcoming_list = upcoming.data or []
    inv_list = inv_due.data or []

    total_overdue = sum(r["amount_cents"] for r in overdue_list)
    total_upcoming = sum(r["amount_cents"] for r in upcoming_list)
    total_inv = sum(r.get("total_cents", 0) for r in inv_list)

    return {
        "tenant_id": tenant_id,
        "days_ahead": days_ahead,
        "overdue": overdue_list,
        "overdue_total_cents": total_overdue,
        "upcoming": upcoming_list,
        "upcoming_total_cents": total_upcoming,
        "invoices_due": inv_list,
        "invoices_total_cents": total_inv,
        "grand_total_cents": total_overdue + total_upcoming + total_inv,
    }


@router.get("/{tenant_id}/admin/suppliers")
async def admin_list_suppliers(tenant_id: str):
    """List known suppliers for this tenant — used by Smart Scanner for payment-term lookup."""
    suppliers = await service.list_suppliers(tenant_id)
    return {"suppliers": suppliers, "count": len(suppliers)}


@router.post("/{tenant_id}/admin/suppliers")
async def admin_upsert_supplier(tenant_id: str, body: dict):
    """Add or update a supplier with payment terms."""
    if not body.get("name"):
        raise HTTPException(status_code=400, detail="Supplier name is required.")
    return await service.upsert_supplier(tenant_id, body)


@router.delete("/{tenant_id}/admin/suppliers/{supplier_id}")
async def admin_delete_supplier(tenant_id: str, supplier_id: str):
    """Delete a supplier."""
    await service.delete_supplier(tenant_id, supplier_id)
    return {"ok": True, "deleted": supplier_id}


# ── Purchase orders + auto-reorder (P3.3, 2026-07-19) ────────────────────────
# Suppliers previously existed only for bill-matching. This closes the loop: see what's low,
# order more from the right supplier, receive it, stock updates.

@router.get("/{tenant_id}/admin/reorder-suggestions")
async def admin_reorder_suggestions(tenant_id: str):
    """Products (or, for a variant product, each individual variant) at/below their reorder
    threshold, grouped by default supplier — the 'what should I order' view. Anything without
    a threshold set is never suggested. Shared with draft_reorder_pos()'s auto-drafting
    (vula/commerce/purchase_orders.py) so both agree on what counts as low stock."""
    from vula.commerce import purchase_orders
    return await purchase_orders.get_reorder_suggestions(tenant_id)


@router.get("/{tenant_id}/admin/purchase-orders")
async def admin_list_purchase_orders(tenant_id: str, status: Optional[str] = None):
    q = (service._client().table("commerce_purchase_orders").select("*")
         .eq("tenant_id", tenant_id).order("created_at", desc=True).limit(100))
    if status:
        q = q.eq("status", status)
    return {"purchase_orders": q.execute().data or []}


@router.post("/{tenant_id}/admin/purchase-orders")
async def admin_create_purchase_order(tenant_id: str, body: dict):
    b = body or {}
    items = b.get("items") or []
    if not items:
        raise HTTPException(status_code=400, detail="At least one item is required")
    total = sum(int(it.get("quantity") or 0) * int(it.get("unit_cost_cents") or 0) for it in items)
    row = {
        "tenant_id": tenant_id, "supplier_id": b.get("supplier_id"), "supplier_name": b.get("supplier_name"),
        "items": items, "total_cents": total, "notes": b.get("notes"), "status": "draft",
    }
    res = service._client().table("commerce_purchase_orders").insert(row).execute()
    return res.data[0] if res.data else {"error": "insert failed"}


@router.patch("/{tenant_id}/admin/purchase-orders/{po_id}/status")
async def admin_update_po_status(tenant_id: str, po_id: str, body: dict):
    """draft → sent → received (bumps stock_quantity for every line item) | cancelled."""
    status = (body or {}).get("status")
    if status not in ("draft", "sent", "received", "cancelled"):
        raise HTTPException(status_code=400, detail="invalid status")
    db = service._client()
    rows = (db.table("commerce_purchase_orders").select("*")
            .eq("tenant_id", tenant_id).eq("id", po_id).limit(1).execute().data or [])
    if not rows:
        raise HTTPException(status_code=404, detail="purchase order not found")
    po = rows[0]
    patch: dict = {"status": status}
    if status == "sent":
        patch["sent_at"] = service._now()
    if status == "received" and po["status"] != "received":
        patch["received_at"] = service._now()
        for it in (po.get("items") or []):
            try:
                prod = (db.table("commerce_products").select("stock_quantity")
                        .eq("id", it["product_id"]).limit(1).execute().data or [None])[0]
                if prod is not None:
                    new_qty = (prod.get("stock_quantity") or 0) + int(it.get("quantity") or 0)
                    db.table("commerce_products").update({"stock_quantity": new_qty}).eq("id", it["product_id"]).execute()
            except Exception as exc:
                log.warning("PO receive stock bump failed for %s: %s", it.get("product_id"), exc)
    result = db.table("commerce_purchase_orders").update(patch).eq("tenant_id", tenant_id).eq("id", po_id).execute()
    return result.data[0] if result.data else {}


@router.post("/{tenant_id}/admin/purchase-orders/{po_id}/send")
async def admin_send_purchase_order(tenant_id: str, po_id: str, body: dict):
    """Actually dispatches the PO (email and/or WhatsApp) — distinct from the generic status
    PATCH above, which is just a manual flag for orders placed outside Vula (e.g. by phone)."""
    channel = (body or {}).get("channel", "email")
    if channel not in ("email", "whatsapp", "both"):
        raise HTTPException(status_code=400, detail="channel must be email, whatsapp, or both")
    from vula.commerce import purchase_orders
    return await purchase_orders.send_purchase_order(tenant_id, po_id, channel)


# ── Automations builder (P3, 2026-07-19) ─────────────────────────────────────
# Lean trigger→action rules: order reaches a status / product hits its reorder threshold →
# WhatsApp the customer or the team helper. Evaluated by a poller (vula.commerce.automations),
# not hooked into every write path.

@router.get("/{tenant_id}/admin/automations")
async def admin_list_automations(tenant_id: str):
    from vula.commerce import automations
    return {"automations": automations.list_automations(tenant_id)}


@router.post("/{tenant_id}/admin/automations")
async def admin_create_automation(tenant_id: str, body: dict):
    from vula.commerce import automations
    try:
        return automations.create_automation(tenant_id, body or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.patch("/{tenant_id}/admin/automations/{automation_id}")
async def admin_update_automation(tenant_id: str, automation_id: str, body: dict):
    from vula.commerce import automations
    return automations.update_automation(tenant_id, automation_id, body or {})


@router.delete("/{tenant_id}/admin/automations/{automation_id}")
async def admin_delete_automation(tenant_id: str, automation_id: str):
    from vula.commerce import automations
    automations.delete_automation(tenant_id, automation_id)
    return {"deleted": automation_id}


# 2026-08 (migration 137): a trigger match used to send its WhatsApp action immediately —
# now it stages a pending firing here instead; the owner approves/rejects before anything
# actually sends. Also lets a plain-language rule description ("teaching mode") create a real
# automation without a developer, via the same never-auto-execute firing path below.

@router.get("/{tenant_id}/admin/automations/firings")
async def admin_list_automation_firings(tenant_id: str):
    from vula.commerce import automations
    return {"firings": automations.list_pending_firings(tenant_id)}


@router.post("/{tenant_id}/admin/automations/firings/{firing_id}/approve")
async def admin_approve_automation_firing(tenant_id: str, firing_id: str):
    from vula.commerce import automations
    return await automations.approve_firing(tenant_id, firing_id)


@router.post("/{tenant_id}/admin/automations/firings/{firing_id}/reject")
async def admin_reject_automation_firing(tenant_id: str, firing_id: str):
    from vula.commerce import automations
    return automations.reject_firing(tenant_id, firing_id)


@router.post("/{tenant_id}/admin/automations/from-text")
async def admin_create_automation_from_text(tenant_id: str, body: dict):
    from vula.commerce import automations
    return await automations.parse_rule_from_text(tenant_id, (body or {}).get("text", ""))


# ── Conversation flow builder (MVP, migration 118) ───────────────────────────
# Owner-configurable no-code WhatsApp flows: trigger phrases → a linear sequence of
# steps → a terminal action drawn from the commerce_admin tool vocabulary. Evaluated
# inline by the WhatsApp router (vula.commerce.flows), not a poller.

@router.get("/{tenant_id}/admin/flows")
async def admin_list_flows(tenant_id: str):
    from vula.commerce import flows
    return {"flows": flows.list_flows(tenant_id)}


@router.post("/{tenant_id}/admin/flows")
async def admin_create_flow(tenant_id: str, body: dict):
    from vula.commerce import flows
    try:
        return flows.create_flow(tenant_id, body or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.patch("/{tenant_id}/admin/flows/{flow_id}")
async def admin_update_flow(tenant_id: str, flow_id: str, body: dict):
    from vula.commerce import flows
    try:
        return flows.update_flow(tenant_id, flow_id, body or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/{tenant_id}/admin/flows/{flow_id}")
async def admin_delete_flow(tenant_id: str, flow_id: str):
    from vula.commerce import flows
    flows.delete_flow(tenant_id, flow_id)
    return {"deleted": flow_id}


@router.post("/{tenant_id}/admin/invoices/{invoice_id}/match-supplier")
async def admin_match_invoice_supplier(tenant_id: str, invoice_id: str):
    """Run tiered supplier auto-detection against a stored inbound invoice.

    Returns the matched supplier with the tier and confidence, or no_match.
    Does not mutate the invoice — the dashboard decides whether to apply.
    """
    invoice = await service.get_invoice(tenant_id, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    raw_items = invoice.get("line_items")
    if isinstance(raw_items, str):
        import json as _j
        try:
            raw_items = _j.loads(raw_items)
        except (ValueError, TypeError):
            raw_items = []
    layout_signature = service.compute_layout_signature({"line_items": raw_items or []})

    result = await service.match_supplier(
        tenant_id,
        name=invoice.get("supplier") or None,
        tax_id=invoice.get("tax_id") or None,
        layout_signature=layout_signature,
    )
    if not result:
        return {"ok": True, "matched": False, "supplier": None}
    return {
        "ok": True,
        "matched": True,
        "tier": result["tier"],
        "confidence": result["confidence"],
        "auto_apply": result["auto_apply"],
        "supplier": result["supplier"],
    }


@router.post("/{tenant_id}/admin/invoices/{invoice_id}/request-supplier-approval")
async def admin_request_supplier_approval(tenant_id: str, invoice_id: str, body: dict):
    """Manually route a match-supplier suggestion to the tenant's own admin team for a
    yes/no over WhatsApp — the dashboard counterpart to the automatic approval
    commit_inbound_document() raises for a genuinely ambiguous match (migration 102 plan,
    Phase 5). Mirrors request-approval's shape but needs no explicit approvers: unlike an
    outbound invoice sent to a client, there's no external stakeholder to pick here.

    body: {"candidate_supplier_id": "...", "candidate_supplier_name": "...",
           "tier": "fuzzy_name", "confidence": 0.82}   # from match-supplier's response
    """
    invoice = await service.get_invoice(tenant_id, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    candidate_id = body.get("candidate_supplier_id")
    if not candidate_id:
        raise HTTPException(status_code=400, detail="candidate_supplier_id is required")

    from vula.commerce.approvals import create_approval, tenant_admin_approvers
    approvers = await tenant_admin_approvers(tenant_id)
    if not approvers:
        raise HTTPException(status_code=400, detail="No admin team member has a WhatsApp number on file to approve this.")

    filed = (service._client().table("vula_filed_documents").select("id")
             .eq("tenant_id", tenant_id).eq("commerce_invoice_id", invoice_id)
             .limit(1).execute().data or [{}])
    total = (invoice.get("total_cents") or 0) / 100
    label = (f"Supplier match: is *{body.get('candidate_supplier_name', 'this supplier')}* who sent "
             f"{invoice.get('doc_type', 'invoice')} {invoice.get('invoice_number', invoice_id)} "
             f"for R{total:,.2f}?")
    approval = await create_approval(
        tenant_id=tenant_id, entity_type="inbound_invoice", entity_id=invoice_id,
        title=label, approvers=approvers,
        meta={
            "filed_document_id": filed[0].get("id") if filed else None,
            "candidate_supplier_id": candidate_id,
            "candidate_supplier_name": body.get("candidate_supplier_name"),
            "match_tier": body.get("tier"), "confidence": body.get("confidence"),
        },
    )
    return {"approval_id": approval["id"], "status": "pending", "approvers": len(approvers)}


@router.patch("/{tenant_id}/admin/expenses/{expense_id}/pay")
async def admin_mark_expense_paid(tenant_id: str, expense_id: str):
    """Mark an expense as paid — removes it from the Due view."""
    from datetime import datetime, timezone
    db = service._client()
    result = db.table("commerce_expenses").update({
        "status": "paid",
        # datetime.now(timezone.utc) — matches the created_at/updated_at convention used
        # everywhere else on this table (service._now()); datetime.utcnow() was both
        # deprecated and, more importantly, inconsistent with those fields' format
        # (naive vs. timezone-aware isoformat).
        "paid_at": datetime.now(timezone.utc).isoformat(),
    }).eq("tenant_id", tenant_id).eq("id", expense_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Expense not found")
    return result.data[0]


# ── Manual order creation (P1.3, 2026-07-18) ─────────────────────────────────
# Phone/walk-in orders could not be captured at all before — the only ways in were the web
# storefront and the WhatsApp assistant.


@router.post("/{tenant_id}/admin/orders/manual")
async def admin_create_manual_order(tenant_id: str, body: dict):
    """Create an order on a customer's behalf: items [{product_id, quantity}], customer
    name/phone, address, slot, payment_method, mark_paid. Uses the same cart→order path as
    the storefront so sale pricing and stock behave identically."""
    b = body or {}
    items = b.get("items") or []
    phone = (b.get("customer_phone") or "").strip()
    name = (b.get("customer_name") or "").strip()
    if not items or not phone or not name:
        raise HTTPException(status_code=400, detail="items, customer_name and customer_phone required")
    from uuid import uuid4
    session_id = f"manual-{uuid4().hex[:12]}"
    cart = await service.get_or_create_cart(tenant_id, session_id, customer_phone=phone)
    added = 0
    for it in items:
        try:
            qty = float(it.get("quantity") or 1)
            if qty > 0:
                await service.add_to_cart(tenant_id, cart["id"], str(it.get("product_id")), qty,
                                          variant_id=it.get("variant_id"))
                added += 1
        except Exception as exc:
            log.warning("manual order item skipped: %s", exc)
    if not added:
        raise HTTPException(status_code=400, detail="no valid items")
    cart = await service.get_or_create_cart(tenant_id, session_id)  # re-read with items joined
    try:
        order = await service.create_order(tenant_id, cart, {
            "customer_phone": phone, "customer_name": name,
            "customer_email": b.get("customer_email"),
            "delivery_address": b.get("delivery_address") or "Collection / to arrange",
            "delivery_slot": b.get("delivery_slot") or "morning",
            "delivery_notes": b.get("delivery_notes"),
            "channel": "manual", "payment_method": b.get("payment_method") or "cod",
        })
    except service.OutOfStockError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if b.get("mark_paid"):
        await service.update_order_status(order["id"], "paid")
        try:
            await service.apply_order_stock(order["id"], restore=False)
        except Exception:
            pass
        order["status"] = "paid"
    return {"order": order}


# ── Escalation inbox (P1.1, 2026-07-18) ──────────────────────────────────────
# Open customer questions previously lived ONLY on the helper's WhatsApp. These endpoints put
# them in the dashboard: view, answer (same relay+learn path as a WhatsApp reply), or dismiss.


@router.get("/{tenant_id}/admin/escalations")
async def admin_list_escalations(tenant_id: str, status: str = "open", limit: int = 50):
    try:
        rows = (service._client().table("vula_escalations").select("*")
                .eq("tenant_id", tenant_id).eq("status", status)
                .order("created_at", desc=True).limit(min(limit, 200)).execute().data or [])
    except Exception as exc:
        log.debug("escalations list skipped (run migration 042?): %s", exc)
        rows = []
    return {"escalations": rows, "open_count": len(rows) if status == "open" else None}


@router.post("/{tenant_id}/admin/escalations/{esc_id}/answer")
async def admin_answer_escalation(tenant_id: str, esc_id: str, body: dict):
    """Answer from the dashboard — identical effect to the helper replying on WhatsApp:
    the customer gets the relay, and the answer is learned for next time."""
    answer = ((body or {}).get("answer") or "").strip()
    if not answer:
        raise HTTPException(status_code=400, detail="answer required")
    db = service._client()
    rows = (db.table("vula_escalations").select("*")
            .eq("tenant_id", tenant_id).eq("id", esc_id).limit(1).execute().data or [])
    if not rows:
        raise HTTPException(status_code=404, detail="escalation not found")
    from vula import escalation as esc_mod
    info = esc_mod.answer_escalation(rows[0], answer)
    if not info:
        raise HTTPException(status_code=409, detail="already answered")
    from vula.api.whatsapp import _send_reply
    await _send_reply(info["customer_phone"], f"About your question — {answer}", tenant_id=tenant_id)
    return {"answered": esc_id}


@router.post("/{tenant_id}/admin/escalations/{esc_id}/dismiss")
async def admin_dismiss_escalation(tenant_id: str, esc_id: str):
    service._client().table("vula_escalations").update({"status": "expired"}) \
        .eq("tenant_id", tenant_id).eq("id", esc_id).execute()
    return {"dismissed": esc_id}


# ── Shared Inbox ──────────────────────────────────────────────────────────────
# These four endpoints back VulaInbox.jsx (conversation list, thread view,
# human handoff toggle, and manual agent reply via WhatsApp).


@router.get("/{tenant_id}/admin/conversations")
async def admin_list_conversations(tenant_id: str, limit: int = Query(50, ge=1, le=200)):
    """Return recent WhatsApp conversation sessions for the shared inbox."""
    conversations = await service.list_conversations(tenant_id, limit=limit)
    return {"tenant_id": tenant_id, "conversations": conversations}


@router.get("/{tenant_id}/admin/conversations/{session_id}/messages")
async def admin_get_thread(tenant_id: str, session_id: str):
    """Return the full thread for one conversation (header + all messages)."""
    thread = await service.get_conversation_thread(tenant_id, session_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return thread


class HandoffRequest(BaseModel):
    paused: bool


@router.post("/{tenant_id}/admin/conversations/{session_id}/handoff")
async def admin_handoff(tenant_id: str, session_id: str, body: HandoffRequest):
    """Toggle human handoff on a session.

    paused=true  → bot goes quiet; human replies via the /reply endpoint.
    paused=false → bot resumes handling messages.
    """
    updated = await service.set_session_paused(tenant_id, session_id, body.paused)
    if not updated:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"ok": True, "session_id": session_id, "paused": body.paused}


class AgentReplyRequest(BaseModel):
    message: str


@router.post("/{tenant_id}/admin/conversations/{session_id}/reply")
async def admin_reply(tenant_id: str, session_id: str, body: AgentReplyRequest):
    """Send a manual WhatsApp reply from the human agent and log it in the thread.

    Automatically takes over the session (sets paused=true) if not already paused,
    so the bot stays quiet after the agent replies.
    """
    text = (body.message or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    thread = await service.get_conversation_thread(tenant_id, session_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Conversation not found")

    phone = thread.get("phone")
    if not phone:
        raise HTTPException(status_code=400, detail="No customer phone number on this session")

    # Auto-pause if not already — agent replied, so bot should stay quiet.
    if not thread.get("paused"):
        await service.set_session_paused(tenant_id, session_id, True)

    # Send via WhatsApp.
    try:
        from vula.api.whatsapp import _send_reply
        await _send_reply(phone, text, tenant_id=tenant_id)
    except Exception as exc:
        log.warning("Admin reply WhatsApp send failed for %s: %s", session_id, exc)
        raise HTTPException(status_code=502, detail=f"WhatsApp send failed: {exc}") from exc

    # Persist the agent's message in the thread.
    await service.append_message(tenant_id, session_id, "agent", text)

    return {"ok": True, "session_id": session_id, "sent": text}


# ── Team inbox: assignment, notes, tags, canned replies (migration 048) ───────
class ConvMetaRequest(BaseModel):
    assigned_to: Optional[str] = None
    agent_note: Optional[str] = None
    tags: Optional[list] = None


@router.post("/{tenant_id}/admin/conversations/{session_id}/meta")
async def admin_conv_meta(tenant_id: str, session_id: str, body: ConvMetaRequest):
    """Assign a conversation to a team member, add an internal note, or set triage tags."""
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if not patch:
        return {"ok": True, "session_id": session_id, "unchanged": True}
    patch["updated_at"] = service._now()
    try:
        service._client().table("commerce_conversation_sessions").update(patch).eq(
            "tenant_id", tenant_id).eq("id", session_id).execute()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"{exc} (run migration 048?)")
    return {"ok": True, "session_id": session_id, **patch}


@router.get("/{tenant_id}/admin/canned-replies")
async def admin_list_canned(tenant_id: str):
    try:
        rows = (service._client().table("commerce_canned_replies").select("*")
                .eq("tenant_id", tenant_id).order("sort").execute().data or [])
    except Exception as exc:
        log.debug("canned replies skipped (run migration 048?): %s", exc)
        rows = []
    return {"tenant_id": tenant_id, "canned_replies": rows}


@router.post("/{tenant_id}/admin/canned-replies")
async def admin_add_canned(tenant_id: str, body: dict):
    b = body or {}
    title, txt = (b.get("title") or "").strip(), (b.get("body") or "").strip()
    if not (title and txt):
        raise HTTPException(status_code=400, detail="title and body are required")
    row = {"tenant_id": tenant_id, "title": title[:60], "body": txt, "sort": int(b.get("sort") or 99)}
    res = service._client().table("commerce_canned_replies").insert(row).execute()
    return {"ok": True, "canned_reply": (res.data or [row])[0]}


@router.delete("/{tenant_id}/admin/canned-replies/{reply_id}")
async def admin_delete_canned(tenant_id: str, reply_id: str):
    service._client().table("commerce_canned_replies").delete().eq(
        "tenant_id", tenant_id).eq("id", reply_id).execute()
    return {"ok": True, "id": reply_id}


@router.get("/{tenant_id}/admin/cs-metrics")
async def admin_cs_metrics(tenant_id: str):
    """Customer-service economics — including cost-per-conversation, the number that beats Cue's
    ~R20/AI-resolution because local-first inference makes conversations near-free at the margin."""
    from datetime import datetime, timezone, timedelta
    ZAR = 18.5  # rough USD→ZAR
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30))
    db = service._client()

    ai_usd = calls = 0.0
    try:
        for r in (db.table("vula_ai_usage").select("est_cost_usd,calls")
                  .eq("tenant_id", tenant_id).gte("day", cutoff.date().isoformat()).execute().data or []):
            ai_usd += float(r.get("est_cost_usd") or 0)
            calls += int(r.get("calls") or 0)
    except Exception:
        pass

    convos = 0
    try:
        convos = len((db.table("commerce_conversation_sessions").select("id")
                      .eq("tenant_id", tenant_id).gte("updated_at", cutoff.isoformat())
                      .limit(5000).execute().data or []))
    except Exception:
        pass

    # Local-first share (near-zero marginal cost) from the routing telemetry.
    local = cloud = 0
    try:
        for r in (db.table("vula_reasoning_telemetry").select("outcome")
                  .eq("system", "vula-llm-router").gte("created_at", cutoff.isoformat())
                  .limit(5000).execute().data or []):
            if r.get("outcome") == "local":
                local += 1
            elif r.get("outcome") == "cloud":
                cloud += 1
    except Exception:
        pass
    total_dec = local + cloud

    cpc_zar = round((ai_usd * ZAR) / convos, 2) if convos else 0.0
    return {
        "tenant_id": tenant_id, "period_days": 30,
        "conversations": convos, "ai_calls": int(calls),
        "ai_cost_zar": round(ai_usd * ZAR, 2),
        "cost_per_conversation_zar": cpc_zar,
        "pct_handled_local": round(100 * local / total_dec) if total_dec else None,
        "benchmark_cue_per_resolution_zar": 20,  # Cue £0.89 ≈ ~R20
    }
