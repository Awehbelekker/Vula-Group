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
