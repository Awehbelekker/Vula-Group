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
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from config import settings
from vula.commerce import service
from vula.commerce.models import (
    AddToCartRequest,
    CreateOrderRequest,
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
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    db = service._client()
    q = db.table("commerce_invoices").select("*").eq("tenant_id", tenant_id)
    if status:
        q = q.eq("status", status)
    if doc_type:
        q = q.eq("doc_type", doc_type)
    result = q.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
    rows = result.data or []
    return {"invoices": rows, "count": len(rows)}


@router.post("/{tenant_id}/admin/invoices")
async def admin_create_invoice(tenant_id: str, body: dict):
    from uuid import uuid4
    db = service._client()

    # Auto-generate invoice number
    last = db.table("commerce_invoices").select("invoice_number") \
        .eq("tenant_id", tenant_id).order("created_at", desc=True).limit(1).execute()
    prefix = tenant_id.upper()[:3]
    if last.data:
        try:
            num = int(last.data[0]["invoice_number"].split("-")[-1]) + 1
        except Exception:
            num = 1
    else:
        num = 1
    invoice_number = f"{prefix}-{num:04d}"

    # Calculate totals
    line_items = body.get("line_items", [])
    subtotal = sum(int(i.get("total_cents", 0)) for i in line_items)
    vat_rate = float(body.get("vat_rate", 15.0))
    vat_cents = round(subtotal * vat_rate / 100)
    total_cents = subtotal + vat_cents

    row = {
        "id": str(uuid4()),
        "tenant_id": tenant_id,
        "invoice_number": invoice_number,
        "customer_name": body.get("customer_name", ""),
        "customer_email": body.get("customer_email"),
        "customer_phone": body.get("customer_phone"),
        "customer_address": body.get("customer_address"),
        "line_items": line_items,
        "subtotal_cents": subtotal,
        "vat_rate": vat_rate,
        "vat_cents": vat_cents,
        "total_cents": total_cents,
        "status": body.get("status", "draft"),
        "issue_date": body.get("issue_date"),
        "due_date": body.get("due_date"),
        "order_id": body.get("order_id"),
        "notes": body.get("notes"),
        "payment_method": body.get("payment_method"),
    }
    result = db.table("commerce_invoices").insert(row).execute()
    return result.data[0] if result.data else row


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
        from core.llm_router import resolve_vision_route
        litellm.drop_params = True

        # Local-first vision: local llava via Ollama when reachable, else the
        # OpenRouter vision model. Mirrors the generation routing policy.
        model, api_key, api_base = await resolve_vision_route()

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
        # Strip markdown fences / think tags
        content = _re.sub(r"<think>.*?</think>", "", content, flags=_re.DOTALL)
        content = _re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=_re.MULTILINE).strip()

        try:
            extracted = _json.loads(content)
        except Exception:
            # Try to find a JSON object in the text
            m = _re.search(r"\{.*\}", content, _re.DOTALL)
            extracted = _json.loads(m.group(0)) if m else {"raw": content, "confidence": "low"}

        return {"ok": True, "extracted": extracted, "model": model}

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
    image_b64 = body.get("image_base64", "")

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
            text=doc_text,
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
