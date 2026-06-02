"""
vula/commerce/service.py — Vula Commerce database service layer.

All commerce data lives in Supabase. One table set, tenant_id on every row.
Prices always stored as integer cents (ZAR). Never floats.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from config import settings
from supabase import create_client, Client


def _client() -> Client:
    # Accept either env var name — Railway has SUPABASE_SERVICE_KEY,
    # newer code uses SUPABASE_SERVICE_ROLE_KEY. Use whichever is set.
    key = settings.supabase_service_role_key or settings.supabase_service_key
    if not key:
        raise RuntimeError(
            "Supabase service key not set. "
            "Set SUPABASE_SERVICE_KEY or SUPABASE_SERVICE_ROLE_KEY in Railway."
        )
    return create_client(settings.supabase_url, key)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Products ─────────────────────────────────────────────────────────────────

async def list_products(tenant_id: str, category: Optional[str] = None, in_stock_only: bool = True) -> List[dict]:
    q = _client().table("commerce_products").select("*").eq("tenant_id", tenant_id)
    if category:
        q = q.eq("category", category)
    if in_stock_only:
        q = q.eq("in_stock", True)
    result = q.order("is_daily_catch", desc=True).order("name").execute()
    return result.data or []


async def get_product(tenant_id: str, product_id: str) -> Optional[dict]:
    result = (
        _client()
        .table("commerce_products")
        .select("*")
        .eq("tenant_id", tenant_id)
        .eq("id", product_id)
        .single()
        .execute()
    )
    return result.data


async def get_product_by_slug(tenant_id: str, slug: str) -> Optional[dict]:
    result = (
        _client()
        .table("commerce_products")
        .select("*")
        .eq("tenant_id", tenant_id)
        .eq("slug", slug)
        .single()
        .execute()
    )
    return result.data


async def create_product(tenant_id: str, data: dict) -> dict:
    payload = {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "created_at": _now(),
        "updated_at": _now(),
        **data,
    }
    result = _client().table("commerce_products").insert(payload).execute()
    return result.data[0]


async def update_product_stock(tenant_id: str, product_id: str, quantity_delta: int) -> None:
    """Decrement stock by quantity_delta. Uses raw SQL to avoid race conditions."""
    _client().rpc(
        "decrement_product_stock",
        {"p_tenant_id": tenant_id, "p_product_id": product_id, "p_delta": quantity_delta},
    ).execute()


# ── Cart ─────────────────────────────────────────────────────────────────────

async def get_or_create_cart(tenant_id: str, session_id: str, customer_phone: Optional[str] = None) -> dict:
    result = (
        _client()
        .table("commerce_carts")
        .select("*, commerce_cart_items(*, commerce_products(name, price_cents, image_url))")
        .eq("tenant_id", tenant_id)
        .eq("session_id", session_id)
        .eq("status", "active")
        .maybe_single()
        .execute()
    )
    if result.data:
        return result.data

    cart_id = str(uuid.uuid4())
    new_cart = {
        "id": cart_id,
        "tenant_id": tenant_id,
        "session_id": session_id,
        "customer_phone": customer_phone,
        "status": "active",
        "delivery_cents": 8000,
        "created_at": _now(),
        "updated_at": _now(),
    }
    result = _client().table("commerce_carts").insert(new_cart).execute()
    return result.data[0]


async def add_to_cart(cart_id: str, product_id: str, quantity: int) -> dict:
    # Upsert — increment quantity if already in cart
    existing = (
        _client()
        .table("commerce_cart_items")
        .select("*")
        .eq("cart_id", cart_id)
        .eq("product_id", product_id)
        .maybe_single()
        .execute()
    )

    if existing.data:
        result = (
            _client()
            .table("commerce_cart_items")
            .update({"quantity": existing.data["quantity"] + quantity, "updated_at": _now()})
            .eq("id", existing.data["id"])
            .execute()
        )
        return result.data[0]

    product = _client().table("commerce_products").select("price_cents").eq("id", product_id).single().execute()
    unit_price = product.data["price_cents"]

    item = {
        "id": str(uuid.uuid4()),
        "cart_id": cart_id,
        "product_id": product_id,
        "quantity": quantity,
        "unit_price_cents": unit_price,
        "created_at": _now(),
        "updated_at": _now(),
    }
    result = _client().table("commerce_cart_items").insert(item).execute()
    return result.data[0]


async def remove_from_cart(cart_id: str, item_id: str) -> None:
    _client().table("commerce_cart_items").delete().eq("id", item_id).eq("cart_id", cart_id).execute()


async def clear_cart(cart_id: str) -> None:
    _client().table("commerce_cart_items").delete().eq("cart_id", cart_id).execute()
    _client().table("commerce_carts").update({"status": "converted", "updated_at": _now()}).eq("id", cart_id).execute()


# ── Orders ───────────────────────────────────────────────────────────────────

async def create_order(tenant_id: str, cart: dict, checkout_data: dict) -> dict:
    items = cart.get("commerce_cart_items", [])
    subtotal = sum(i["quantity"] * i["unit_price_cents"] for i in items)
    delivery = cart.get("delivery_cents", 8000)
    total = subtotal + delivery
    display_id = await _next_order_display_id(tenant_id)

    order = {
        "id": str(uuid.uuid4()),
        "display_id": display_id,
        "tenant_id": tenant_id,
        "customer_phone": checkout_data["customer_phone"],
        "customer_name": checkout_data["customer_name"],
        "customer_email": checkout_data.get("customer_email"),
        "delivery_address": checkout_data["delivery_address"],
        "delivery_slot": checkout_data.get("delivery_slot", "morning"),
        "delivery_notes": checkout_data.get("delivery_notes"),
        "subtotal_cents": subtotal,
        "delivery_cents": delivery,
        "total_cents": total,
        "status": "pending_payment",
        "channel": checkout_data.get("channel", "web"),
        "cart_id": cart["id"],
        "created_at": _now(),
        "updated_at": _now(),
    }

    result = _client().table("commerce_orders").insert(order).execute()
    order_id = result.data[0]["id"]

    # Insert order items
    order_items = [
        {
            "id": str(uuid.uuid4()),
            "order_id": order_id,
            "product_id": i["product_id"],
            "product_name": i.get("commerce_products", {}).get("name", ""),
            "quantity": i["quantity"],
            "unit_price_cents": i["unit_price_cents"],
            "total_cents": i["quantity"] * i["unit_price_cents"],
        }
        for i in items
    ]
    _client().table("commerce_order_items").insert(order_items).execute()

    return result.data[0]


async def list_orders(
    tenant_id: str,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[dict]:
    q = (
        _client()
        .table("commerce_orders")
        .select("id,display_id,customer_name,customer_phone,total_cents,status,channel,delivery_slot,created_at")
        .eq("tenant_id", tenant_id)
    )
    if status:
        q = q.eq("status", status)
    result = q.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
    return result.data or []


async def update_product(tenant_id: str, product_id: str, data: dict) -> dict:
    """Patch any product fields — stock, price, in_stock toggle, etc."""
    data["updated_at"] = _now()
    result = (
        _client()
        .table("commerce_products")
        .update(data)
        .eq("tenant_id", tenant_id)
        .eq("id", product_id)
        .execute()
    )
    return result.data[0] if result.data else {}


async def update_order_status(order_id: str, status: str, yoco_checkout_id: Optional[str] = None) -> None:
    update = {"status": status, "updated_at": _now()}
    if yoco_checkout_id:
        update["yoco_checkout_id"] = yoco_checkout_id
    _client().table("commerce_orders").update(update).eq("id", order_id).execute()


async def get_order(order_id: str) -> Optional[dict]:
    result = (
        _client()
        .table("commerce_orders")
        .select("*, commerce_order_items(*)")
        .eq("id", order_id)
        .single()
        .execute()
    )
    return result.data


async def _next_order_display_id(tenant_id: str) -> str:
    result = (
        _client()
        .table("commerce_orders")
        .select("display_id")
        .eq("tenant_id", tenant_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    last = result.data[0]["display_id"] if result.data else None
    prefix = tenant_id.upper()[:3]
    if last:
        num = int(last.split("-")[-1]) + 1
    else:
        num = 1
    return f"{prefix}-{num:05d}"


# ── Conversation sessions (multi-turn memory) ─────────────────────────────────

async def get_or_create_session(
    tenant_id: str,
    session_key: str,
    channel: str = "whatsapp",
    customer_phone: Optional[str] = None,
) -> dict:
    """Return the conversation session for (tenant_id, session_key), creating it if absent."""
    existing = (
        _client()
        .table("commerce_conversation_sessions")
        .select("*")
        .eq("tenant_id", tenant_id)
        .eq("session_key", session_key)
        .maybe_single()
        .execute()
    )
    if existing and existing.data:
        return existing.data

    session = {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "session_key": session_key,
        "channel": channel,
        "customer_phone": customer_phone,
        "created_at": _now(),
        "updated_at": _now(),
    }
    result = _client().table("commerce_conversation_sessions").insert(session).execute()
    return result.data[0]


async def append_message(tenant_id: str, session_id: str, role: str, content: str) -> None:
    """Persist a single conversation turn."""
    _client().table("commerce_conversation_messages").insert(
        {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "session_id": session_id,
            "role": role,
            "content": content,
            "created_at": _now(),
        }
    ).execute()


async def get_recent_messages(session_id: str, limit: int = 12) -> List[dict]:
    """Return the most recent messages for a session, oldest first."""
    result = (
        _client()
        .table("commerce_conversation_messages")
        .select("role,content,created_at")
        .eq("session_id", session_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    rows = result.data or []
    return list(reversed(rows))


def format_history(messages: List[dict]) -> str:
    """Render messages into a compact transcript for the skill's conversation_history."""
    label = {"user": "Customer", "assistant": "Assistant"}
    return "\n".join(
        f"{label.get(m['role'], m['role'].title())}: {m['content']}"
        for m in messages
        if m.get("role") in ("user", "assistant") and m.get("content")
    )


# ── Invoices & Quotes ─────────────────────────────────────────────────────────
# One table (commerce_invoices) serves invoices, quotes, and proformas via
# doc_type. All money is integer cents (ZAR). Every query is tenant-scoped.

_DOC_TYPE_CODE = {"invoice": "INV", "quote": "QTE", "proforma": "PRO"}


def _compute_totals(line_items: List[dict], vat_rate: float) -> tuple[int, int, int, List[dict]]:
    """Return (subtotal_cents, vat_cents, total_cents, normalised_items).

    Each line's total_cents is recomputed from quantity * unit_price_cents so the
    server is the source of truth. VAT is computed on the subtotal and rounded to
    the nearest cent. All values are integer cents.
    """
    normalised: List[dict] = []
    subtotal = 0
    for item in line_items:
        qty = int(item["quantity"])
        unit = int(item["unit_price_cents"])
        line_total = qty * unit
        subtotal += line_total
        normalised.append(
            {
                "description": item["description"],
                "quantity": qty,
                "unit_price_cents": unit,
                "total_cents": line_total,
                "product_id": str(item["product_id"]) if item.get("product_id") else None,
            }
        )
    vat_cents = round(subtotal * (vat_rate / 100.0))
    total = subtotal + vat_cents
    return subtotal, vat_cents, total, normalised


async def _next_invoice_number(tenant_id: str, doc_type: str) -> str:
    """Sequential, tenant-scoped, doc-type-scoped number e.g. OTH-INV-00001."""
    code = _DOC_TYPE_CODE.get(doc_type, "INV")
    result = (
        _client()
        .table("commerce_invoices")
        .select("invoice_number")
        .eq("tenant_id", tenant_id)
        .eq("doc_type", doc_type)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    last = result.data[0]["invoice_number"] if result.data else None
    num = int(last.split("-")[-1]) + 1 if last else 1
    prefix = tenant_id.upper()[:3]
    return f"{prefix}-{code}-{num:05d}"


async def create_invoice(tenant_id: str, data: dict) -> dict:
    """Create an invoice, quote, or proforma. Totals are computed server-side."""
    doc_type = data.get("doc_type", "invoice")
    vat_rate = float(data.get("vat_rate", 15.0))
    subtotal, vat_cents, total, items = _compute_totals(data["line_items"], vat_rate)
    invoice = {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "doc_type": doc_type,
        "invoice_number": await _next_invoice_number(tenant_id, doc_type),
        "customer_name": data["customer_name"],
        "customer_email": data.get("customer_email"),
        "customer_phone": data.get("customer_phone"),
        "customer_address": data.get("customer_address"),
        "line_items": items,
        "subtotal_cents": subtotal,
        "vat_rate": vat_rate,
        "vat_cents": vat_cents,
        "total_cents": total,
        "status": "draft",
        "due_date": data.get("due_date"),
        "valid_until": data.get("valid_until"),
        "order_id": str(data["order_id"]) if data.get("order_id") else None,
        "notes": data.get("notes"),
        "created_at": _now(),
        "updated_at": _now(),
    }
    result = _client().table("commerce_invoices").insert(invoice).execute()
    return result.data[0]


async def get_invoice(tenant_id: str, invoice_id: str) -> Optional[dict]:
    result = (
        _client()
        .table("commerce_invoices")
        .select("*")
        .eq("tenant_id", tenant_id)
        .eq("id", invoice_id)
        .maybe_single()
        .execute()
    )
    return result.data if result else None


async def list_invoices(
    tenant_id: str,
    doc_type: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[dict]:
    q = (
        _client()
        .table("commerce_invoices")
        .select(
            "id,doc_type,invoice_number,customer_name,customer_phone,"
            "total_cents,status,issue_date,due_date,valid_until,created_at"
        )
        .eq("tenant_id", tenant_id)
    )
    if doc_type:
        q = q.eq("doc_type", doc_type)
    if status:
        q = q.eq("status", status)
    result = q.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
    return result.data or []


async def update_invoice_status(tenant_id: str, invoice_id: str, status: str) -> dict:
    """Transition an invoice/quote status. Stamps paid_at when marked paid."""
    patch: dict = {"status": status, "updated_at": _now()}
    if status == "paid":
        patch["paid_at"] = _now()
    result = (
        _client()
        .table("commerce_invoices")
        .update(patch)
        .eq("tenant_id", tenant_id)
        .eq("id", invoice_id)
        .execute()
    )
    return result.data[0] if result.data else {}


async def convert_quote_to_invoice(tenant_id: str, quote_id: str) -> dict:
    """Create an invoice from an accepted quote, linking both directions.

    The quote is marked 'accepted' and stamped with converted_invoice_id; the new
    invoice carries source_quote_id back to the quote. Totals are copied as-is.
    """
    quote = await get_invoice(tenant_id, quote_id)
    if not quote:
        raise ValueError("quote not found")
    if quote.get("doc_type") not in ("quote", "proforma"):
        raise ValueError("source document is not a quote or proforma")

    invoice = {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "doc_type": "invoice",
        "invoice_number": await _next_invoice_number(tenant_id, "invoice"),
        "customer_name": quote["customer_name"],
        "customer_email": quote.get("customer_email"),
        "customer_phone": quote.get("customer_phone"),
        "customer_address": quote.get("customer_address"),
        "line_items": quote.get("line_items", []),
        "subtotal_cents": quote["subtotal_cents"],
        "vat_rate": quote["vat_rate"],
        "vat_cents": quote["vat_cents"],
        "total_cents": quote["total_cents"],
        "status": "draft",
        "source_quote_id": quote_id,
        "notes": quote.get("notes"),
        "created_at": _now(),
        "updated_at": _now(),
    }
    created = _client().table("commerce_invoices").insert(invoice).execute().data[0]

    _client().table("commerce_invoices").update(
        {"status": "accepted", "converted_invoice_id": created["id"], "updated_at": _now()}
    ).eq("tenant_id", tenant_id).eq("id", quote_id).execute()

    return created
