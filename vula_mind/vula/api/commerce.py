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
from typing import Optional

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
):
    products = await service.list_products(tenant_id, category=category, in_stock_only=in_stock_only)
    return {"tenant_id": tenant_id, "products": products, "count": len(products)}


@router.get("/{tenant_id}/products/{slug}")
async def get_product(tenant_id: str, slug: str):
    product = await service.get_product_by_slug(tenant_id, slug)
    if not product:
        raise HTTPException(status_code=404, detail=f"Product '{slug}' not found")
    return product


# ── CMS pages (Puck self-serve page builder, P3) ─────────────────────────────
# Public: published pages only. Admin: read-all / upsert / delete. All tenant-scoped.

class PageIn(BaseModel):
    title: Optional[str] = None
    puck_data: dict = {}
    seo: Optional[dict] = None
    status: str = "draft"          # draft | published


@router.get("/{tenant_id}/pages")
async def list_pages(tenant_id: str):
    """Published pages (public) — slugs + titles for nav, not full bodies."""
    try:
        rows = (service._client().table("vula_pages")
                .select("slug,title,seo,status,updated_at")
                .eq("tenant_id", tenant_id).eq("status", "published")
                .order("updated_at", desc=True).execute().data or [])
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


@router.get("/{tenant_id}/admin/pages")
async def admin_list_pages(tenant_id: str):
    """All pages (draft + published) for the store editor."""
    try:
        rows = (service._client().table("vula_pages")
                .select("id,slug,title,status,updated_at")
                .eq("tenant_id", tenant_id).order("updated_at", desc=True).execute().data or [])
    except Exception as exc:
        return {"tenant_id": tenant_id, "pages": [], "error": f"{exc} (run migration 041?)"}
    return {"tenant_id": tenant_id, "pages": rows}


@router.get("/{tenant_id}/admin/pages/{slug}")
async def admin_get_page(tenant_id: str, slug: str):
    rows = (service._client().table("vula_pages").select("*")
            .eq("tenant_id", tenant_id).eq("slug", slug).limit(1).execute().data or [])
    return rows[0] if rows else {"tenant_id": tenant_id, "slug": slug, "puck_data": {}, "status": "draft"}


@router.put("/{tenant_id}/admin/pages/{slug}")
async def upsert_page(tenant_id: str, slug: str, body: PageIn):
    """Create or update a page (store editor / Puck save)."""
    row = {"tenant_id": tenant_id, "slug": slug, "title": body.title,
           "puck_data": body.puck_data or {}, "seo": body.seo or {},
           "status": body.status or "draft", "updated_at": service._now()}
    db = service._client()
    try:
        existing = (db.table("vula_pages").select("id")
                    .eq("tenant_id", tenant_id).eq("slug", slug).limit(1).execute().data or [])
        if existing:
            db.table("vula_pages").update(row).eq("id", existing[0]["id"]).execute()
            row["id"] = existing[0]["id"]
        else:
            db.table("vula_pages").insert(row).execute()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"{exc} (run migration 041?)")
    return {"page": row}


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


# ── Cart ─────────────────────────────────────────────────────────────────────

@router.get("/{tenant_id}/cart/{session_id}")
async def get_cart(tenant_id: str, session_id: str, phone: Optional[str] = Query(None)):
    cart = await service.get_or_create_cart(tenant_id, session_id, customer_phone=phone)
    return cart


@router.post("/{tenant_id}/cart/{session_id}/add")
async def add_to_cart(tenant_id: str, session_id: str, body: AddToCartRequest):
    cart = await service.get_or_create_cart(tenant_id, session_id, customer_phone=body.customer_phone)
    item = await service.add_to_cart(cart["id"], str(body.product_id), body.quantity)
    return {"cart_id": cart["id"], "item": item}


@router.delete("/{tenant_id}/cart/{session_id}/{item_id}")
async def remove_from_cart(tenant_id: str, session_id: str, item_id: str):
    cart = await service.get_or_create_cart(tenant_id, session_id)
    await service.remove_from_cart(cart["id"], item_id)
    return {"removed": item_id}


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


@router.post("/{tenant_id}/checkout")
async def create_checkout(tenant_id: str, body: CheckoutRequest):
    # Fetch cart
    cart = await service.get_or_create_cart(tenant_id, body.session_id, customer_phone=body.customer_phone)
    items = cart.get("commerce_cart_items", [])
    if not items:
        raise HTTPException(status_code=400, detail="Cart is empty")

    # Create order in Supabase
    order = await service.create_order(tenant_id, cart, body.model_dump())

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
    order = await service.get_order(order_id)
    if not order or order.get("tenant_id") != tenant_id:
        raise HTTPException(status_code=404, detail="Order not found")
    await service.update_order_status(order_id, new_status)
    return {"order_id": order_id, "status": new_status}


@router.get("/{tenant_id}/admin/products")
async def admin_list_products(tenant_id: str):
    """List all products including out-of-stock — for merchant product management."""
    products = await service.list_products(tenant_id, in_stock_only=False)
    return {"products": products, "count": len(products)}


@router.patch("/{tenant_id}/admin/products/{product_id}")
async def admin_update_product(tenant_id: str, product_id: str, body: dict):
    """Patch product — toggle stock, update price, edit name/description."""
    allowed = {"in_stock", "price_cents", "name", "description", "notes",
               "is_daily_catch", "stock_quantity", "image_url", "category", "sold_by"}
    update = {k: v for k, v in body.items() if k in allowed}
    if not update:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    result = await service.update_product(tenant_id, product_id, update)
    return result


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


@router.delete("/{tenant_id}/admin/products/{product_id}")
async def admin_delete_product(tenant_id: str, product_id: str):
    """Delete a product."""
    await service.delete_product(tenant_id, product_id)
    return {"deleted": product_id}


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
        history = service.format_history(await service.get_recent_messages(tenant_id, sid, limit=12))
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


# ── Shared inbox (conversations + human handoff) ─────────────────────────────
# Owners see live customer WhatsApp chats and can take over from the bot.
# Handoff state is stored in commerce_conversation_sessions.last_skill:
#   'human_handoff' → a human has taken over; the bot stays quiet for this chat.

_HANDOFF = "human_handoff"


@router.get("/{tenant_id}/admin/conversations")
async def admin_list_conversations(tenant_id: str, limit: int = Query(60, ge=1, le=200)):
    """List customer conversations with a last-message preview + handoff status."""
    db = service._client()
    sessions = (
        db.table("commerce_conversation_sessions").select("*")
        .eq("tenant_id", tenant_id).order("updated_at", desc=True).limit(limit).execute()
    ).data or []
    # Bulk-fetch recent messages → last message per session (avoids N+1).
    msgs = (
        db.table("commerce_conversation_messages")
        .select("session_id,role,content,created_at")
        .eq("tenant_id", tenant_id).order("created_at", desc=True).limit(600).execute()
    ).data or []
    last: dict = {}
    for m in msgs:
        last.setdefault(m["session_id"], m)

    convos = []
    for s in sessions:
        # Only show real customer chats (skip the owner admin sessions).
        if str(s.get("session_key", "")).startswith(("admin:", "webadmin:")):
            continue
        lm = last.get(s["id"]) or {}
        convos.append({
            "session_id": s["id"],
            "customer_name": s.get("customer_name"),
            "customer_phone": s.get("customer_phone") or s.get("session_key"),
            "channel": s.get("channel") or "whatsapp",
            "paused": s.get("last_skill") == _HANDOFF,
            "last_message": (lm.get("content") or "")[:120],
            "last_role": lm.get("role"),
            "last_at": lm.get("created_at") or s.get("updated_at"),
        })
    convos.sort(key=lambda c: c.get("last_at") or "", reverse=True)
    return {"conversations": convos, "count": len(convos)}


@router.get("/{tenant_id}/admin/conversations/{session_id}/messages")
async def admin_conversation_messages(tenant_id: str, session_id: str, limit: int = Query(120, ge=1, le=500)):
    """Full message thread for one conversation."""
    db = service._client()
    msgs = (
        db.table("commerce_conversation_messages")
        .select("role,content,created_at")
        .eq("tenant_id", tenant_id).eq("session_id", session_id)
        .order("created_at", desc=True).limit(limit).execute()
    ).data or []
    sess = (
        db.table("commerce_conversation_sessions").select("*")
        .eq("tenant_id", tenant_id).eq("id", session_id).limit(1).execute()
    ).data
    s0 = sess[0] if sess else {}
    return {
        "messages": list(reversed(msgs)),
        "paused": s0.get("last_skill") == _HANDOFF,
        "customer": s0.get("customer_name"),
        "phone": s0.get("customer_phone") or s0.get("session_key"),
    }


@router.post("/{tenant_id}/admin/conversations/{session_id}/handoff")
async def admin_conversation_handoff(tenant_id: str, session_id: str, body: dict):
    """Pause the bot for this chat (human takes over) or hand it back to the bot."""
    paused = bool(body.get("paused", True))
    service._client().table("commerce_conversation_sessions").update(
        {"last_skill": _HANDOFF if paused else None, "updated_at": service._now()}
    ).eq("tenant_id", tenant_id).eq("id", session_id).execute()
    return {"session_id": session_id, "paused": paused}


@router.post("/{tenant_id}/admin/conversations/{session_id}/reply")
async def admin_conversation_reply(tenant_id: str, session_id: str, body: dict):
    """Owner sends a manual WhatsApp reply — takes over the chat (pauses the bot)."""
    text = (body.get("message") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="message is required")
    db = service._client()
    sess = (
        db.table("commerce_conversation_sessions").select("*")
        .eq("tenant_id", tenant_id).eq("id", session_id).limit(1).execute()
    ).data
    if not sess:
        raise HTTPException(status_code=404, detail="Conversation not found")
    to = sess[0].get("customer_phone") or sess[0].get("session_key")

    from vula.api.whatsapp import _send_reply
    sent = await _send_reply(to, text, tenant_id)

    # Taking over → pause the bot and record the agent message.
    db.table("commerce_conversation_sessions").update(
        {"last_skill": _HANDOFF, "updated_at": service._now()}
    ).eq("tenant_id", tenant_id).eq("id", session_id).execute()
    await service.append_message(tenant_id, session_id, "agent", text)
    return {"sent": sent, "paused": True}


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

    # Invoice summary
    try:
        inv_result = db.table("commerce_invoices").select(
            "total_cents,status"
        ).eq("tenant_id", tenant_id).execute().data or []
        invoice_outstanding = sum(i["total_cents"] for i in inv_result if i["status"] in ("sent",))
        invoice_overdue = sum(i["total_cents"] for i in inv_result if i["status"] == "overdue")
        invoice_paid_month = sum(i["total_cents"] for i in inv_result if i["status"] == "paid")
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


# ── Invoice endpoints ─────────────────────────────────────────────────────────

@router.get("/{tenant_id}/admin/invoices")
async def admin_list_invoices(
    tenant_id: str,
    status: Optional[str] = Query(None),
    doc_type: Optional[str] = Query(None),  # invoice | quote | proforma
    direction: str = Query("outbound"),     # outbound | inbound
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List all invoices/quotes for a tenant."""
    rows = await service.list_invoices(
        tenant_id, status=status, doc_type=doc_type, direction=direction, limit=limit, offset=offset
    )
    return {"invoices": rows, "count": len(rows)}


@router.post("/{tenant_id}/admin/invoices")
async def admin_create_invoice(tenant_id: str, body: InvoiceCreate):
    """Create an invoice, quote, or proforma. Totals computed server-side."""
    created = await service.create_invoice(tenant_id, body.model_dump(mode="json"))
    return created


@router.patch("/{tenant_id}/admin/invoices/{invoice_id}")
async def admin_update_invoice(tenant_id: str, invoice_id: str, body: dict):
    db = service._client()
    allowed = {
        "status", "customer_name", "customer_email", "customer_phone",
        "customer_address", "line_items", "subtotal_cents", "vat_rate",
        "vat_cents", "total_cents", "due_date", "notes", "payment_method",
        "yoco_checkout_id", "yoco_payment_id", "paid_at",
    }
    update = {k: v for k, v in body.items() if k in allowed}
    if not update:
        raise HTTPException(status_code=400, detail="No valid fields")
    update["updated_at"] = service._now()
    result = db.table("commerce_invoices").update(update) \
        .eq("tenant_id", tenant_id).eq("id", invoice_id).execute()
    return result.data[0] if result.data else {}


@router.delete("/{tenant_id}/admin/invoices/{invoice_id}")
async def admin_delete_invoice(tenant_id: str, invoice_id: str):
    service._client().table("commerce_invoices") \
        .delete().eq("tenant_id", tenant_id).eq("id", invoice_id).execute()
    return {"deleted": invoice_id}


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

    from vula.api.email import send_invoice_email
    sent = await send_invoice_email(recipient, invoice, pdf_bytes, tenant_name)
    if not sent:
        raise HTTPException(
            status_code=503,
            detail="Email not sent — Resend is not configured or the send failed.",
        )

    new_status = invoice.get("status")
    if new_status == "draft":
        await service.update_invoice_status(tenant_id, invoice_id, "sent")
        new_status = "sent"

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
    caption = (
        f"Hi {invoice.get('customer_name', 'there')}, here is your {doc_label} "
        f"{number} for {total} from {tenant_name}. Thank you for your business!"
    )
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

    return {"sent": True, "to": recipient, "status": new_status}


# ── Invoice settings (onboarding + look-and-feel) ─────────────────────────────

@router.get("/{tenant_id}/admin/invoice-settings")
async def admin_get_invoice_settings(tenant_id: str):
    """Return the tenant's invoice settings (VAT, address, banking, template).

    ``onboarded`` is False when the tenant has never completed the first-run
    wizard, so the dashboard knows to show it.
    """
    settings = await service.get_invoice_settings(tenant_id)
    return {"settings": settings, "onboarded": bool(settings and settings.get("onboarded"))}


@router.post("/{tenant_id}/admin/invoice-settings")
async def admin_upsert_invoice_settings(tenant_id: str, body: dict):
    """Create or update the tenant's invoice settings (one row per tenant)."""
    try:
        settings = await service.upsert_invoice_settings(tenant_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"settings": settings}


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
    """Create a credit note against an invoice (full, or partial via body.line_items)."""
    try:
        cn = await service.create_credit_note(tenant_id, invoice_id, (body or {}).get("line_items"))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"credit_note": cn}


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
async def admin_convert_quote(tenant_id: str, quote_id: str):
    """Convert an accepted quote/proforma into a draft invoice, linking both."""
    try:
        invoice = await service.convert_quote_to_invoice(tenant_id, quote_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
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

@router.get("/{tenant_id}/admin/expenses")
async def admin_list_expenses(
    tenant_id: str,
    month: Optional[str] = Query(None),  # YYYY-MM
    limit: int = Query(100, ge=1, le=500),
):
    db = service._client()
    q = db.table("commerce_expenses").select("*").eq("tenant_id", tenant_id)
    if month:
        q = q.gte("date", f"{month}-01").lt("date", f"{month[:4]}-{int(month[5:])+1:02d}-01")
    result = q.order("date", desc=True).limit(limit).execute()
    rows = result.data or []
    total = sum(r["amount_cents"] for r in rows)
    return {"expenses": rows, "count": len(rows), "total_cents": total}


@router.post("/{tenant_id}/admin/expenses")
async def admin_create_expense(tenant_id: str, body: dict):
    from uuid import uuid4
    db = service._client()
    row = {
        "id": str(uuid4()),
        "tenant_id": tenant_id,
        "date": body.get("date"),
        "category": body.get("category", "other"),
        "description": body.get("description", ""),
        "amount_cents": int(body.get("amount_cents", 0)),
        "supplier": body.get("supplier"),
        "receipt_url": body.get("receipt_url"),
    }
    result = db.table("commerce_expenses").insert(row).execute()
    return result.data[0] if result.data else row


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


def record_inbound_consent(tenant_id: str, phone: str) -> None:
    """Record implied opt-in on first inbound. Never overrides an existing opt-out."""
    p = _norm_phone(phone)
    if not tenant_id or not p:
        return
    try:
        db = service._client()
        existing = (db.table("commerce_consent").select("status")
                    .eq("tenant_id", tenant_id).eq("phone", p).limit(1).execute().data or [])
        if existing:
            return
        db.table("commerce_consent").insert({
            "tenant_id": tenant_id, "phone": p, "status": "opted_in", "source": "inbound"}).execute()
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

    log_id = str(uuid4())
    db.table("commerce_broadcast_logs").insert({
        "id": log_id, "tenant_id": tenant_id, "name": name,
        "template_name": template or "(free-text)", "audience_filter": audience,
        "status": "sending", "recipient_count": len(recipients),
    }).execute()

    sent = failed = 0
    errors: list[str] = []
    recipient_rows: list[dict] = []
    async with httpx.AsyncClient(timeout=15.0) as client:
        for c in recipients:
            number = _norm_phone(c.get("phone"))
            try:
                if use_twilio:
                    # Twilio free-text broadcast
                    from_addr = settings.twilio_whatsapp_from
                    if not from_addr.startswith("whatsapp:"):
                        from_addr = f"whatsapp:{from_addr}"
                    msg = body_text or f"Hi from {name}!"
                    resp = await client.post(
                        f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json",
                        auth=(settings.twilio_account_sid, settings.twilio_auth_token),
                        data={"From": from_addr, "To": f"whatsapp:+{number}", "Body": msg[:1600]},
                    )
                else:
                    # Meta template broadcast (compliant for proactive sends)
                    resp = await client.post(
                        f"https://graph.facebook.com/v19.0/{creds['phone_id']}/messages",
                        headers={"Authorization": f"Bearer {creds['token']}",
                                 "Content-Type": "application/json"},
                        json={
                            "messaging_product": "whatsapp",
                            "to": number,
                            "type": "template",
                            "template": {"name": template, "language": {"code": language}},
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
                    recipient_rows.append({"tenant_id": tenant_id, "broadcast_id": log_id,
                                           "phone": number, "wamid": wamid, "status": "sent"})
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
            log.debug("recipient rows insert skipped (run migration 020?): %s", exc)

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


@router.post("/{tenant_id}/jobs/stock-alerts")
async def job_stock_alerts(tenant_id: str):
    """Scan for low stock items."""
    low_stock = await service.get_low_stock_products(tenant_id, threshold=5)
    # logic to alert ops
    return {"ok": True, "found": len(low_stock), "items": low_stock}


@router.post("/{tenant_id}/jobs/weekly-specials")
async def job_weekly_specials(tenant_id: str):
    """Fire the weekly specials broadcast."""
    # This logic matches OTH-05: Monday 07:00
    products = await service.list_products(tenant_id, in_stock_only=True)
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


async def _aggregate_customers(tenant_id: str) -> dict[str, dict]:
    """Merge orders + conversation sessions into one contact per phone number.

    Shared by the Customers tab and the broadcast sender so the audience shown
    in the dashboard is exactly the audience a broadcast reaches.
    """
    db = service._client()
    customers: dict[str, dict] = {}

    # Orders → spend & recency
    orders = (
        db.table("commerce_orders")
        .select("customer_phone,customer_name,total_cents,status,created_at")
        .eq("tenant_id", tenant_id)
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

    # Conversation sessions → contacts who messaged in
    sessions = (
        db.table("commerce_conversation_sessions")
        .select("customer_phone,customer_name,channel,updated_at,session_key")
        .eq("tenant_id", tenant_id)
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

    # Imported contact book (existing clients) → merged in, deduped by phone. Guarded so a
    # missing table (migration 046 not yet run) never breaks the Customers tab or broadcasts.
    try:
        contacts = (
            db.table("commerce_contacts")
            .select("phone,name,email,area,product,tags,consent_status")
            .eq("tenant_id", tenant_id).execute()
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
    not_ordered_within_days, min_spend, max_spend, min_orders, channel."""
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


@router.get("/{tenant_id}/admin/customers/{phone}/history")
async def admin_customer_history(tenant_id: str, phone: str):
    """Per-customer interaction timeline: orders + invoices + recent chat (tenant-scoped)."""
    db = service._client()
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
    return {"phone": phone, "events": events[:80]}


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


def _scan_quality_ok(ex: dict) -> bool:
    """Heuristic: did the vision model produce a usable financial extraction?

    Used to decide whether to escalate a weak local read to the cloud model.
    Fails on the common local-model failure modes:
      - parse error / self-reported low confidence
      - missing money total or no line items (llava)
      - line-item totals that don't reconcile with the stated total (qwen often
        reads the structure correctly but grabs the wrong number as the total,
        while still reporting "high" confidence — so we can't trust confidence
        alone; we cross-check the arithmetic instead).
    """
    if not ex or ex.get("raw"):
        return False
    if str(ex.get("confidence") or "").lower() == "low":
        return False
    total = ex.get("total_cents") or 0
    items = ex.get("line_items") or []
    if not total or not items:
        return False

    # Reconciliation: sum of line totals should be within ~30% of the stated
    # total (allowing for VAT, delivery, rounding). A gross mismatch means the
    # model misread at least one money figure → escalate.
    line_sum = 0
    for it in items:
        lt = it.get("total_cents")
        if lt is None:  # fall back to qty × unit price
            q = it.get("quantity") or 0
            up = it.get("unit_price_cents") or 0
            lt = q * up
        line_sum += int(lt or 0)
    if line_sum > 0:
        ratio = line_sum / total
        if ratio > 1.3 or ratio < 0.7:
            return False
    return True


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

    # Build instruction based on doc type
    if body.doc_type == "auto":
        instruction = (
            "Identify what kind of business document this is (receipt, delivery_note, invoice, or order) "
            "for a South African food business, then extract all relevant structured data from it."
        )
    else:
        instruction = _SCAN_PROMPTS.get(body.doc_type, _SCAN_PROMPTS["receipt"])

    system = (
        "You are a document-extraction engine for a food business admin system. "
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
        '  "line_items": [{"description": string, "quantity": number, "unit": "kg|pack|each"|null, "unit_price_cents": integer|null, "total_cents": integer|null}],\n'
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
    Commit a smart-scan result into the books:

    1. Match supplier name against commerce_suppliers → get payment_terms_days
    2. Calculate due_date = date + payment_terms_days
    3. For receipts / petty cash → create commerce_expense
       For invoices / delivery_notes → create commerce_invoice (direction=inbound)
    4. Ingest the document image into the tenant KB so the AI learns from it
    5. Return the created record + a "due in X days" message

    Body:
        extracted:    the JSON from /admin/scan
        image_base64: optional — if provided, ingests into KB as text
        auto_commit:  bool (default true) — if false, returns preview only
    """
    import json as _j
    from uuid import uuid4
    from datetime import date, timedelta

    extracted = body.get("extracted", {})
    auto_commit = body.get("auto_commit", True)

    if not extracted:
        raise HTTPException(status_code=400, detail="No extracted data provided.")

    db = service._client()
    today = date.today()

    # ── 1. Supplier lookup & payment terms (tiered auto-detection) ───────────
    supplier_name = extracted.get("supplier") or ""
    tax_id = extracted.get("tax_id") or ""
    layout_signature = service.compute_layout_signature(extracted)
    payment_terms_days = 30  # default
    supplier_row = None

    supplier_match = await service.match_supplier(
        tenant_id,
        name=supplier_name or None,
        tax_id=tax_id or None,
        layout_signature=layout_signature,
    )
    # Only auto-apply payment terms for a high-confidence match; weaker matches
    # are surfaced (supplier_match in the preview) for the owner to confirm.
    if supplier_match and supplier_match["auto_apply"]:
        supplier_row = supplier_match["supplier"]
        payment_terms_days = supplier_row.get("payment_terms_days", 30)

    # ── 2. Resolve dates ────────────────────────────────────────────────────
    doc_date = today
    if extracted.get("date"):
        try:
            doc_date = date.fromisoformat(extracted["date"])
        except ValueError:
            pass

    due_date = None
    if extracted.get("due_date"):
        try:
            due_date = date.fromisoformat(extracted["due_date"])
        except ValueError:
            pass

    if not due_date and payment_terms_days is not None:
        due_date = doc_date + timedelta(days=payment_terms_days)

    days_until_due = (due_date - today).days if due_date else None

    # ── 3. Determine record type ────────────────────────────────────────────
    doc_type = extracted.get("doc_type", "receipt")
    is_invoice = doc_type in ("invoice", "delivery_note")
    total_cents = int(extracted.get("total_cents") or 0)
    vat_cents = int(extracted.get("vat_cents") or 0)

    # ── 4. Preview mode ─────────────────────────────────────────────────────
    preview = {
        "supplier": supplier_name,
        "supplier_known": supplier_row is not None,
        "supplier_match": (
            {
                "tier": supplier_match["tier"],
                "confidence": supplier_match["confidence"],
                "auto_applied": supplier_match["auto_apply"],
                "supplier_id": supplier_match["supplier"].get("id"),
                "supplier_name": supplier_match["supplier"].get("name"),
            }
            if supplier_match else None
        ),
        "payment_terms_days": payment_terms_days,
        "doc_date": str(doc_date),
        "due_date": str(due_date) if due_date else None,
        "days_until_due": days_until_due,
        "total_cents": total_cents,
        "doc_type": doc_type,
        "record_type": "invoice" if is_invoice else "expense",
    }

    if not auto_commit:
        return {"ok": True, "preview": preview, "committed": False}

    # ── 5. Write to books ───────────────────────────────────────────────────
    record_id = str(uuid4())
    committed_record = None

    if is_invoice:
        row = {
            "id": record_id,
            "tenant_id": tenant_id,
            "direction": "inbound",
            "doc_type": doc_type,
            "status": "draft",
            "supplier": supplier_name,
            "date": str(doc_date),
            "due_date": str(due_date) if due_date else None,
            "payment_terms_days": payment_terms_days,
            "subtotal_cents": total_cents - vat_cents,
            "vat_cents": vat_cents,
            "total_cents": total_cents,
            "line_items": _j.dumps(extracted.get("line_items", [])),
            "notes": extracted.get("notes"),
            "source": "scanner",
            "scan_confidence": extracted.get("confidence"),
        }
        result = db.table("commerce_invoices").insert(row).execute()
        committed_record = result.data[0] if result.data else row
    else:
        row = {
            "id": record_id,
            "tenant_id": tenant_id,
            "date": str(doc_date),
            "due_date": str(due_date) if due_date else None,
            "category": extracted.get("category") or "supplies",
            "description": f"{supplier_name or 'Unknown'} — {doc_type}",
            "amount_cents": total_cents,
            "supplier": supplier_name,
            "payment_terms_days": payment_terms_days,
            "status": "pending",
            "source": "scanner",
            "doc_type": doc_type,
            "line_items": _j.dumps(extracted.get("line_items", [])),
            "scan_confidence": extracted.get("confidence"),
        }
        result = db.table("commerce_expenses").insert(row).execute()
        committed_record = result.data[0] if result.data else row

    # ── 5b. Learn this supplier's layout signature on a confident match ──────
    if supplier_row and layout_signature and not supplier_row.get("layout_signature"):
        try:
            await service.learn_supplier_signature(tenant_id, supplier_row.get("id"), layout_signature)
        except Exception as sig_exc:
            log.warning("Failed to learn supplier signature for %s: %s", record_id, sig_exc)

    # ── 6. Ingest into KB so the AI learns from it ──────────────────────────
    kb_chunks = 0
    try:
        from vula.ingestion.pipeline import VulaIngestionPipeline
        pipeline = VulaIngestionPipeline(tenant_id=tenant_id)

        # Convert extracted data to readable text for KB
        lines = [f"Document type: {doc_type}", f"Supplier: {supplier_name}", f"Date: {doc_date}"]
        if due_date:
            lines.append(f"Due date: {due_date} ({payment_terms_days} day terms)")
        lines.append(f"Total: R{total_cents/100:.2f} (incl VAT R{vat_cents/100:.2f})")
        if extracted.get("line_items"):
            lines.append("Line items:")
            for item in extracted["line_items"][:20]:
                lines.append(f"  - {item.get('description','')} {item.get('quantity','')} {item.get('unit','')} @ R{(item.get('unit_price_cents',0) or 0)/100:.2f}")
        if extracted.get("notes"):
            lines.append(f"Notes: {extracted['notes']}")

        doc_text = "\n".join(lines)
        ingest_result = await pipeline.ingest_text(
            content=doc_text,
            filename=f"{doc_type}_{supplier_name.replace(' ','_')}_{doc_date}.txt",
        )
        kb_chunks = getattr(ingest_result, "chunks_stored", 0)
    except Exception as kb_exc:
        log.warning("KB ingest failed for scan commit %s: %s", record_id, kb_exc)

    # ── 7. Build human-readable message ────────────────────────────────────
    if due_date:
        if days_until_due < 0:
            msg = f"⚠️ OVERDUE by {abs(days_until_due)} days — R{total_cents/100:.0f} to {supplier_name or 'supplier'}"
        elif days_until_due == 0:
            msg = f"🔴 DUE TODAY — R{total_cents/100:.0f} to {supplier_name or 'supplier'}"
        elif days_until_due <= 7:
            msg = f"🟡 Due in {days_until_due} days (by {due_date}) — R{total_cents/100:.0f}"
        else:
            msg = f"✅ Captured — R{total_cents/100:.0f} due {due_date} ({days_until_due} days)"
    else:
        msg = f"✅ Captured — R{total_cents/100:.0f} (no due date)"

    return {
        "ok": True,
        "committed": True,
        "record_type": "invoice" if is_invoice else "expense",
        "record_id": record_id,
        "record": committed_record,
        "supplier_match": preview["supplier_match"],
        "preview": preview,
        "kb_chunks_added": kb_chunks,
        "message": msg,
    }


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


@router.patch("/{tenant_id}/admin/expenses/{expense_id}/pay")
async def admin_mark_expense_paid(tenant_id: str, expense_id: str):
    """Mark an expense as paid — removes it from the Due view."""
    from datetime import datetime
    db = service._client()
    result = db.table("commerce_expenses").update({
        "status": "paid",
        "paid_at": datetime.utcnow().isoformat(),
    }).eq("tenant_id", tenant_id).eq("id", expense_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Expense not found")
    return result.data[0]


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
