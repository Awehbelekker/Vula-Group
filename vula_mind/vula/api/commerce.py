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

    store_url = settings.store_urls.get(tenant_id, "https://offthehook.co.za")

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
    allowed = {"in_stock", "price_cents", "name", "description", "notes", "is_weekly_special", "stock_quantity"}
    update = {k: v for k, v in body.items() if k in allowed}
    if not update:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    result = await service.update_product(tenant_id, product_id, update)
    return result


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
    }


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
        from vula.commerce.pdf import render_invoice_pdf
        pdf_bytes = render_invoice_pdf(invoice)
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
        from vula.commerce.pdf import render_invoice_pdf, _TENANT_DEFAULTS
        pdf_bytes = render_invoice_pdf(invoice)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        log.error("PDF render failed for %s/%s: %s", tenant_id, invoice_id, exc)
        raise HTTPException(status_code=500, detail="PDF generation failed")

    tenant_name = _TENANT_DEFAULTS.get(tenant_id, {}).get(
        "name", tenant_id.replace("-", " ").title()
    )

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


@router.post("/{tenant_id}/admin/broadcasts/send")
async def admin_send_broadcast(tenant_id: str, body: dict):
    """
    Fire a WhatsApp broadcast via n8n webhook.
    n8n fetches the audience, sends Meta template messages, updates delivery counts.
    """
    from uuid import uuid4
    import httpx as _httpx

    db = service._client()
    template = body.get("template_name", "")
    audience = body.get("audience_filter", "all")
    name = body.get("name", template)

    if not template:
        raise HTTPException(status_code=400, detail="template_name required")

    # Insert broadcast log
    log_id = str(uuid4())
    db.table("commerce_broadcast_logs").insert({
        "id": log_id,
        "tenant_id": tenant_id,
        "name": name,
        "template_name": template,
        "audience_filter": audience,
        "status": "sending",
    }).execute()

    # Fire n8n webhook (non-blocking)
    n8n_base = settings.n8n_webhook_base
    if n8n_base:
        try:
            async with _httpx.AsyncClient(timeout=5.0) as client:
                await client.post(
                    f"{n8n_base}/whatsapp-broadcast",
                    json={
                        "tenant_id": tenant_id,
                        "broadcast_id": log_id,
                        "template_name": template,
                        "audience_filter": audience,
                    },
                )
        except Exception as exc:
            log.warning("Broadcast n8n webhook failed (non-fatal): %s", exc)

    return {"broadcast_id": log_id, "status": "sending", "template": template, "audience": audience}


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
    specials = [p for p in products if p.get("is_daily_catch") or p.get("is_weekly_special")]
    # Fire n8n broadcast
    return {"ok": True, "specials_count": len(specials)}



# ── Customers (client list / CRM) ─────────────────────────────────────────────

@router.get("/{tenant_id}/admin/customers")
async def admin_list_customers(
    tenant_id: str,
    audience: str = Query("all"),          # all | active_30d | high_value
    search: Optional[str] = Query(None),
):
    """
    Aggregated client list for a tenant — the source of truth for broadcasts.

    Built by merging two sources on phone number:
      - commerce_orders: name, spend, order count, last order date
      - commerce_conversation_sessions: WhatsApp/web contacts who messaged in
        (auto-captured by the AI assistant) but may not have ordered yet.

    `audience` mirrors the broadcast filters so the dashboard can show exactly
    who a campaign would reach.
    """
    from datetime import datetime, timezone, timedelta

    db = service._client()

    def _norm(p: Optional[str]) -> str:
        if not p:
            return ""
        n = "".join(ch for ch in p if ch.isdigit())
        if n.startswith("0"):
            n = "27" + n[1:]
        return n

    customers: dict[str, dict] = {}

    # ── Orders → spend & recency ─────────────────────────────────────────────
    orders = (
        db.table("commerce_orders")
        .select("customer_phone,customer_name,total_cents,status,created_at")
        .eq("tenant_id", tenant_id)
        .execute()
    ).data or []
    for o in orders:
        key = _norm(o.get("customer_phone"))
        if not key:
            continue
        c = customers.setdefault(key, {
            "phone": o.get("customer_phone"), "name": o.get("customer_name"),
            "orders": 0, "total_spent_cents": 0, "last_order_at": None,
            "source": "order", "channel": "web",
        })
        # Only count revenue from orders that were actually paid/fulfilled
        if o.get("status") not in ("pending_payment", "cancelled", "refunded"):
            c["orders"] += 1
            c["total_spent_cents"] += int(o.get("total_cents") or 0)
        if o.get("customer_name") and not c.get("name"):
            c["name"] = o["customer_name"]
        ca = o.get("created_at")
        if ca and (not c["last_order_at"] or ca > c["last_order_at"]):
            c["last_order_at"] = ca

    # ── Conversation sessions → contacts who messaged in ─────────────────────
    sessions = (
        db.table("commerce_conversation_sessions")
        .select("customer_phone,customer_name,channel,updated_at,session_key")
        .eq("tenant_id", tenant_id)
        .execute()
    ).data or []
    for s in sessions:
        key = _norm(s.get("customer_phone") or s.get("session_key"))
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

    rows = list(customers.values())

    # ── Audience filter ──────────────────────────────────────────────────────
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


# ── Delivery list ─────────────────────────────────────────────────────────────

@router.get("/{tenant_id}/admin/delivery-list")
async def admin_delivery_list(
    tenant_id: str,
    date: Optional[str] = Query(None, description="YYYY-MM-DD, defaults to today"),
):
    """Today's delivery run — orders with items and paid/unpaid status."""
    from datetime import date as _date
    target = date or _date.today().isoformat()
    db = service._client()
    result = (
        db.table("commerce_orders")
        .select(
            "id,display_id,customer_name,customer_phone,"
            "delivery_address,delivery_slot,delivery_notes,"
            "total_cents,status,channel,created_at,"
            "commerce_order_items(product_name,quantity,unit_price_cents,total_cents)"
        )
        .eq("tenant_id", tenant_id)
        .gte("created_at", f"{target}T00:00:00+00:00")
        .lt("created_at", f"{target}T23:59:59+00:00")
        .not_.in_("status", ["cancelled", "refunded"])
        .order("delivery_slot")
        .order("created_at")
        .execute()
    )
    orders = result.data or []
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

    # ── 1. Supplier lookup & payment terms ──────────────────────────────────
    supplier_name = extracted.get("supplier") or ""
    payment_terms_days = 30  # default
    supplier_row = None

    if supplier_name:
        # Try exact match first, then alias match
        result = db.table("commerce_suppliers") \
            .select("*") \
            .eq("tenant_id", tenant_id) \
            .ilike("name", supplier_name) \
            .limit(1).execute()

        if not result.data:
            # Search aliases array
            result = db.table("commerce_suppliers") \
                .select("*") \
                .eq("tenant_id", tenant_id) \
                .contains("aliases", [supplier_name]) \
                .limit(1).execute()

        if result.data:
            supplier_row = result.data[0]
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
    db = service._client()
    result = db.table("commerce_suppliers").select("*") \
        .eq("tenant_id", tenant_id).order("name").execute()
    return {"suppliers": result.data or [], "count": len(result.data or [])}


@router.post("/{tenant_id}/admin/suppliers")
async def admin_upsert_supplier(tenant_id: str, body: dict):
    """Add or update a supplier with payment terms."""
    from uuid import uuid4
    db = service._client()
    row = {
        "id": body.get("id") or str(uuid4()),
        "tenant_id": tenant_id,
        "name": body["name"],
        "aliases": body.get("aliases", []),
        "payment_terms_days": int(body.get("payment_terms_days", 30)),
        "category": body.get("category", "general"),
        "contact_phone": body.get("contact_phone"),
        "contact_email": body.get("contact_email"),
        "account_number": body.get("account_number"),
        "notes": body.get("notes"),
    }
    result = db.table("commerce_suppliers") \
        .upsert(row, on_conflict="tenant_id,name").execute()
    return result.data[0] if result.data else row


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
