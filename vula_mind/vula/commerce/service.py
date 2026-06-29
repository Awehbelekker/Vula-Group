"""
vula/commerce/service.py — Vula Commerce database service layer.

All commerce data lives in Supabase. One table set, tenant_id on every row.
Prices always stored as integer cents (ZAR). Never floats.
"""
from __future__ import annotations

import difflib
import hashlib
import re
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
        .limit(1)
        .execute()
    )
    if result.data:
        return result.data[0]

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


async def add_to_cart(tenant_id: str, cart_id: str, product_id: str, quantity: int) -> dict:
    # Check existing
    existing = (
        _client()
        .table("commerce_cart_items")
        .select("*")
        .eq("cart_id", cart_id)
        .eq("product_id", product_id)
        .limit(1)
        .execute()
    )

    if existing.data:
        row = existing.data[0]
        result = (
            _client()
            .table("commerce_cart_items")
            .update({"quantity": row["quantity"] + quantity, "updated_at": _now()})
            .eq("id", row["id"])
            .execute()
        )
        return result.data[0]

    # Fetch product to verify it belongs to tenant and get price
    product = (
        _client()
        .table("commerce_products")
        .select("price_cents")
        .eq("tenant_id", tenant_id)
        .eq("id", product_id)
        .single()
        .execute()
    )
    if not product.data:
        raise ValueError(f"Product {product_id} not found for tenant {tenant_id}")
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


async def get_delivery_list(tenant_id: str, date_str: Optional[str] = None) -> List[dict]:
    """Return all orders for a given date (default today) with items, paid/unpaid status."""
    from datetime import date as _date
    target = date_str or _date.today().isoformat()
    result = (
        _client()
        .table("commerce_orders")
        .select("id,display_id,customer_name,customer_phone,customer_email,"
                "delivery_address,delivery_slot,delivery_notes,"
                "total_cents,status,channel,created_at,"
                "commerce_order_items(product_name,quantity,unit_price_cents,total_cents)")
        .eq("tenant_id", tenant_id)
        .gte("created_at", f"{target}T00:00:00+00:00")
        .lt("created_at", f"{target}T23:59:59+00:00")
        .not_.in_("status", ["cancelled", "refunded"])
        .order("delivery_slot")
        .order("created_at")
        .execute()
    )
    return result.data or []


async def get_customers(
    tenant_id: str,
    audience: str = "all",
    search: str = "",
    limit: int = 100,
) -> dict:
    """Aggregate customers from orders + WhatsApp conversations."""
    q = (
        _client()
        .table("commerce_orders")
        .select("customer_name,customer_phone,total_cents,status,created_at")
        .eq("tenant_id", tenant_id)
        .not_.in_("status", ["cancelled", "refunded"])
    )
    result = q.order("created_at", desc=True).limit(500).execute()
    rows = result.data or []

    # Aggregate per phone
    from collections import defaultdict
    from datetime import datetime, timezone, timedelta
    cust: dict[str, dict] = {}
    for r in rows:
        phone = r.get("customer_phone") or ""
        if not phone:
            continue
        if phone not in cust:
            cust[phone] = {
                "name": r.get("customer_name") or "",
                "phone": phone,
                "order_count": 0,
                "total_spent_cents": 0,
                "last_order_at": r.get("created_at"),
            }
        cust[phone]["order_count"] += 1
        cust[phone]["total_spent_cents"] += r.get("total_cents") or 0
        if r.get("created_at", "") > cust[phone]["last_order_at"]:
            cust[phone]["last_order_at"] = r["created_at"]

    customers = list(cust.values())

    # Audience filter
    now = datetime.now(timezone.utc)
    if audience == "active_30d":
        cutoff = (now - timedelta(days=30)).isoformat()
        customers = [c for c in customers if (c.get("last_order_at") or "") >= cutoff]
    elif audience == "high_value":
        customers = [c for c in customers if c["total_spent_cents"] >= 50000]

    # Search filter
    if search:
        sl = search.lower()
        customers = [c for c in customers if sl in c["name"].lower() or sl in c["phone"]]

    customers.sort(key=lambda c: c.get("last_order_at") or "", reverse=True)
    return {"customers": customers[:limit], "count": len(customers), "total_all": len(cust)}


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


async def delete_product(tenant_id: str, product_id: str) -> None:
    """Delete a product (scoped to the tenant)."""
    (
        _client()
        .table("commerce_products")
        .delete()
        .eq("tenant_id", tenant_id)
        .eq("id", product_id)
        .execute()
    )


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
        .limit(1)
        .execute()
    )
    if existing and existing.data:
        return existing.data[0]

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


async def get_recent_messages(tenant_id: str, session_id: str, limit: int = 12) -> List[dict]:
    """Return the most recent messages for a session, oldest first."""
    result = (
        _client()
        .table("commerce_conversation_messages")
        .select("role,content,created_at")
        .eq("tenant_id", tenant_id)
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


def _compute_totals(line_items: List[dict], vat_rate: float,
                    prices_include_vat: bool = False) -> tuple[int, int, int, List[dict]]:
    """Return (subtotal_cents, vat_cents, total_cents, normalised_items).

    Each line's total_cents is recomputed from quantity * unit_price_cents so the
    server is the source of truth. All values are integer cents.
    - exclusive (default): subtotal = sum(lines); vat = subtotal*rate; total = subtotal+vat.
    - inclusive: entered line prices already contain VAT → back it out so subtotal is net,
      vat = entered − net, total = entered. (Correct SA VAT-inclusive handling.)
    """
    normalised: List[dict] = []
    entered = 0
    for item in line_items:
        qty = int(item["quantity"])
        unit = int(item["unit_price_cents"])
        line_total = qty * unit
        entered += line_total
        normalised.append(
            {
                "description": item["description"],
                "quantity": qty,
                "unit_price_cents": unit,
                "total_cents": line_total,
                "product_id": str(item["product_id"]) if item.get("product_id") else None,
            }
        )
    if prices_include_vat and vat_rate > 0:
        net = round(entered / (1 + vat_rate / 100.0))
        return net, entered - net, entered, normalised
    vat_cents = round(entered * (vat_rate / 100.0))
    return entered, vat_cents, entered + vat_cents, normalised


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
    # Respect the tenant's VAT profile: non-registered → 0%; inclusive pricing → back out VAT.
    s = await get_invoice_settings(tenant_id) or {}
    vat_registered = s.get("vat_registered", True)
    prices_include_vat = bool(s.get("prices_include_vat"))
    vat_rate = float(data.get("vat_rate", 15.0)) if vat_registered else 0.0
    subtotal, vat_cents, total, items = _compute_totals(data["line_items"], vat_rate, prices_include_vat)
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
        "status": data.get("status", "draft"),
        "issue_date": data.get("issue_date") or _now()[:10],
        "due_date": data.get("due_date"),
        "valid_until": data.get("valid_until"),
        "order_id": str(data["order_id"]) if data.get("order_id") else None,
        "payment_method": data.get("payment_method"),
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
        .limit(1)
        .execute()
    )
    return result.data[0] if (result and result.data) else None


async def list_invoices(
    tenant_id: str,
    doc_type: Optional[str] = None,
    status: Optional[str] = None,
    direction: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[dict]:
    q = (
        _client()
        .table("commerce_invoices")
        .select("*")
        .eq("tenant_id", tenant_id)
    )
    if doc_type:
        q = q.eq("doc_type", doc_type)
    if status:
        q = q.eq("status", status)
    if direction:
        q = q.eq("direction", direction)
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


# ── Scheduled Jobs Logic ─────────────────────────────────────────────────────

async def get_abandoned_carts(tenant_id: str, hours_old: int = 1) -> List[dict]:
    """Find carts older than hours_old with no associated order."""
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours_old)).isoformat()

    # Get active carts updated before cutoff
    carts = _client().table("commerce_carts") \
        .select("*, commerce_cart_items(*, commerce_products(name))") \
        .eq("tenant_id", tenant_id) \
        .eq("status", "active") \
        .lt("updated_at", cutoff) \
        .execute().data or []

    # Filter for carts that don't have an order
    abandoned = []
    for cart in carts:
        order_check = _client().table("commerce_orders") \
            .select("id") \
            .eq("cart_id", cart["id"]) \
            .limit(1).execute()
        if not order_check.data:
            abandoned.append(cart)

    return abandoned


async def get_reorder_candidates(tenant_id: str, days_ago: int = 7) -> List[dict]:
    """Find customers whose last delivered order was exactly days_ago."""
    from datetime import datetime, timedelta, timezone
    start = (datetime.now(timezone.utc) - timedelta(days=days_ago + 1)).date().isoformat()
    end = (datetime.now(timezone.utc) - timedelta(days=days_ago)).date().isoformat()

    result = _client().table("commerce_orders") \
        .select("customer_phone, customer_name, commerce_order_items(product_name)") \
        .eq("tenant_id", tenant_id) \
        .eq("status", "delivered") \
        .gte("created_at", start) \
        .lt("created_at", end) \
        .execute()
    return result.data or []


async def get_low_stock_products(tenant_id: str, threshold: int = 5) -> List[dict]:
    """Find products below stock threshold."""
    result = _client().table("commerce_products") \
        .select("id, name, stock_quantity") \
        .eq("tenant_id", tenant_id) \
        .eq("in_stock", True) \
        .lt("stock_quantity", threshold) \
        .execute()
    return result.data or []


# ── Suppliers (tiered intake auto-detection) ──────────────────────────────────

# Auto-apply a fuzzy match at/above FUZZY_AUTO; surface for confirmation down to
# FUZZY_MIN; below FUZZY_MIN is treated as no match.
FUZZY_AUTO = 0.90
FUZZY_MIN = 0.75

_NAME_NOISE = re.compile(r"\b(pty|ltd|cc|inc|limited|proprietary|the)\b")


def _norm_name(s: str) -> str:
    """Lowercase, strip company suffixes and punctuation for stable comparison."""
    s = re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower())
    s = _NAME_NOISE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def _norm_tax(s: str) -> str:
    """Reduce a tax/VAT number to comparable alphanumerics only."""
    return re.sub(r"[^0-9a-z]", "", (s or "").lower())


def compute_layout_signature(extracted: dict) -> Optional[str]:
    """Deterministic fingerprint of a supplier document from its line-item
    catalogue. Stable across scans of the same supplier's invoices; used as a
    Tier-4 signal when name/tax-id are missing or unreliable. Returns None when
    there are no usable line items.
    """
    items = extracted.get("line_items") or []
    tokens = sorted({_norm_name(i.get("description", "")) for i in items if i.get("description")})
    tokens = [t for t in tokens if t]
    if not tokens:
        return None
    return hashlib.sha256("|".join(tokens).encode("utf-8")).hexdigest()[:32]


async def list_suppliers(tenant_id: str) -> List[dict]:
    """All known suppliers for a tenant, ordered by name."""
    result = (
        _client().table("commerce_suppliers").select("*")
        .eq("tenant_id", tenant_id).order("name").execute()
    )
    return result.data or []


async def upsert_supplier(tenant_id: str, data: dict) -> dict:
    """Insert or update a supplier (conflict on tenant_id,name)."""
    row = {
        "id": data.get("id") or str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "name": data["name"],
        "aliases": data.get("aliases", []),
        "payment_terms_days": int(data.get("payment_terms_days", 30)),
        "category": data.get("category", "general"),
        "contact_phone": data.get("contact_phone"),
        "contact_email": data.get("contact_email"),
        "account_number": data.get("account_number"),
        "tax_id": data.get("tax_id"),
        "layout_signature": data.get("layout_signature"),
        "notes": data.get("notes"),
    }
    result = (
        _client().table("commerce_suppliers")
        .upsert(row, on_conflict="tenant_id,name").execute()
    )
    return result.data[0] if result.data else row


async def match_supplier(
    tenant_id: str,
    *,
    name: Optional[str] = None,
    tax_id: Optional[str] = None,
    layout_signature: Optional[str] = None,
) -> Optional[dict]:
    """Tiered supplier auto-detection, tenant-scoped.

    Returns ``{"supplier", "tier", "confidence", "auto_apply"}`` or ``None``.
    Tiers, highest confidence first:
      1. ``tax_id`` exact          → confidence 1.0, auto_apply
      2. exact name / alias        → confidence 1.0, auto_apply
      3. fuzzy name / alias        → difflib ratio; auto_apply >= FUZZY_AUTO
      4. layout signature exact    → confidence 0.85, surfaced (not auto)
    """
    suppliers = await list_suppliers(tenant_id)
    if not suppliers:
        return None

    # Tier 1 — tax id exact (strongest identity signal)
    nt = _norm_tax(tax_id) if tax_id else ""
    if nt:
        for s in suppliers:
            if s.get("tax_id") and _norm_tax(s["tax_id"]) == nt:
                return {"supplier": s, "tier": "tax_id", "confidence": 1.0, "auto_apply": True}

    nn = _norm_name(name) if name else ""

    # Tier 2 — exact name / alias
    if nn:
        for s in suppliers:
            candidates = [s.get("name", "")] + list(s.get("aliases") or [])
            if any(_norm_name(c) == nn for c in candidates):
                return {"supplier": s, "tier": "exact_name", "confidence": 1.0, "auto_apply": True}

    # Tier 3 — fuzzy name / alias
    if nn:
        best, best_score = None, 0.0
        for s in suppliers:
            candidates = [s.get("name", "")] + list(s.get("aliases") or [])
            score = max(
                (difflib.SequenceMatcher(None, nn, _norm_name(c)).ratio() for c in candidates if c),
                default=0.0,
            )
            if score > best_score:
                best, best_score = s, score
        if best and best_score >= FUZZY_MIN:
            return {
                "supplier": best,
                "tier": "fuzzy_name",
                "confidence": round(best_score, 3),
                "auto_apply": best_score >= FUZZY_AUTO,
            }

    # Tier 4 — layout signature exact
    if layout_signature:
        for s in suppliers:
            if s.get("layout_signature") and s["layout_signature"] == layout_signature:
                return {"supplier": s, "tier": "layout", "confidence": 0.85, "auto_apply": False}

    return None


async def learn_supplier_signature(tenant_id: str, supplier_id: str, layout_signature: str) -> None:
    """Record a layout signature on a supplier the first time we confidently see
    it, so future scans can match on layout alone. No-op without both ids."""
    if not (supplier_id and layout_signature):
        return
    (
        _client().table("commerce_suppliers")
        .update({"layout_signature": layout_signature})
        .eq("tenant_id", tenant_id).eq("id", supplier_id).execute()
    )


async def delete_supplier(tenant_id: str, supplier_id: str) -> None:
    """Delete a supplier (scoped to the tenant)."""
    (
        _client()
        .table("commerce_suppliers")
        .delete()
        .eq("tenant_id", tenant_id)
        .eq("id", supplier_id)
        .execute()
    )


# ── Invoice settings (onboarding + look-and-feel) ─────────────────────────────

# Fields a tenant may set via the onboarding wizard / settings panel.
_INVOICE_SETTINGS_FIELDS = (
    "company_name", "trading_as", "logo_url", "company_email", "company_phone", "company_reg",
    "vat_number", "registered_address", "vat_registered", "prices_include_vat",
    "account_name", "bank_name", "branch_code", "account_number",
    "template_choice", "accent_color", "onboarded",
)
_TEMPLATE_CHOICES = ("classic", "minimal", "modern", "branded")


async def get_invoice_settings(tenant_id: str) -> Optional[dict]:
    """Return the tenant's invoice settings row, or None if not yet configured."""
    result = (
        _client()
        .table("commerce_invoice_settings")
        .select("*")
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
    )
    return result.data[0] if (result and result.data) else None


async def upsert_invoice_settings(tenant_id: str, data: dict) -> dict:
    """Create or update the tenant's invoice settings (one row per tenant).

    Only whitelisted fields are persisted. ``template_choice`` is validated
    against the known templates. Every write is tenant-scoped.
    """
    patch = {k: data[k] for k in _INVOICE_SETTINGS_FIELDS if k in data}
    choice = patch.get("template_choice")
    if choice is not None and choice not in _TEMPLATE_CHOICES:
        raise ValueError(f"template_choice must be one of {_TEMPLATE_CHOICES}")

    db = _client()
    existing = await get_invoice_settings(tenant_id)
    if existing:
        patch["updated_at"] = _now()
        result = (
            db.table("commerce_invoice_settings")
            .update(patch)
            .eq("tenant_id", tenant_id)
            .execute()
        )
        return result.data[0] if result.data else {**existing, **patch}

    row = {"id": str(uuid.uuid4()), "tenant_id": tenant_id, **patch}
    result = db.table("commerce_invoice_settings").insert(row).execute()
    return result.data[0] if result.data else row


# ── Saved clients / suppliers directory (for invoicing) ───────────────────────

_CLIENT_FIELDS = ("kind", "name", "email", "phone", "address", "vat_number", "notes")


async def list_invoice_clients(tenant_id: str, kind: Optional[str] = None) -> List[dict]:
    q = (_client().table("commerce_invoice_clients").select("*")
         .eq("tenant_id", tenant_id).order("name"))
    if kind:
        q = q.eq("kind", kind)
    return (q.execute().data or [])


async def upsert_invoice_client(tenant_id: str, data: dict) -> dict:
    patch = {k: data[k] for k in _CLIENT_FIELDS if k in data}
    if not patch.get("name"):
        raise ValueError("name is required")
    db = _client()
    if data.get("id"):
        patch["updated_at"] = _now()
        res = (db.table("commerce_invoice_clients").update(patch)
               .eq("id", data["id"]).eq("tenant_id", tenant_id).execute())
        return res.data[0] if res.data else {**patch, "id": data["id"]}
    row = {"id": str(uuid.uuid4()), "tenant_id": tenant_id, **patch}
    res = db.table("commerce_invoice_clients").insert(row).execute()
    return res.data[0] if res.data else row


async def delete_invoice_client(tenant_id: str, client_id: str) -> None:
    _client().table("commerce_invoice_clients").delete() \
        .eq("id", client_id).eq("tenant_id", tenant_id).execute()
