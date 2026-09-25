"""
vula/commerce/service.py — Vula Commerce database service layer.

All commerce data lives in Supabase. One table set, tenant_id on every row.
Prices always stored as integer cents (ZAR). Never floats.
"""
from __future__ import annotations

import difflib
import hashlib
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from config import settings
from supabase import create_client, Client

logger = logging.getLogger(__name__)


_client_singleton: Optional[Client] = None


def _client() -> Client:
    """Shared Supabase client. Cached module-wide — previously a fresh client (httpx session +
    auth setup) was created on *every* DB call, adding latency to every request."""
    global _client_singleton
    if _client_singleton is not None:
        return _client_singleton
    # Accept either env var name — Railway has SUPABASE_SERVICE_KEY,
    # newer code uses SUPABASE_SERVICE_ROLE_KEY. Use whichever is set.
    key = settings.supabase_service_role_key or settings.supabase_service_key
    if not key:
        raise RuntimeError(
            "Supabase service key not set. "
            "Set SUPABASE_SERVICE_KEY or SUPABASE_SERVICE_ROLE_KEY in Railway."
        )
    _client_singleton = create_client(settings.supabase_url, key)
    return _client_singleton


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Products ─────────────────────────────────────────────────────────────────

def effective_price_cents(product: dict) -> int:
    """The price actually charged right now: sale price while a sale is active (migration 073),
    else the regular price. Single source of truth — used at cart-add time so sales charge
    correctly everywhere (web, WhatsApp assistant, admin)."""
    from datetime import datetime, timezone
    base = int(product.get("price_cents") or 0)
    sale = product.get("sale_price_cents")
    if not sale:
        return base
    ends = product.get("sale_ends_at")
    if ends:
        try:
            if datetime.fromisoformat(str(ends).replace("Z", "+00:00")) < datetime.now(timezone.utc):
                return base  # sale expired
        except Exception:
            pass
    return int(sale)


async def list_products(tenant_id: str, category: Optional[str] = None, in_stock_only: bool = True,
                        include_archived: bool = False,
                        statuses: Optional[set] = None,
                        with_variant_price_range: bool = False) -> List[dict]:
    """statuses: when given, restrict to rows whose `status` is in this set — used by
    public-facing reads (migration 085) so a draft/archived product is never surfaced outside
    the merchant admin. None (the default) means no status filtering, i.e. admin call sites
    keep seeing every product regardless of status, unchanged.

    with_variant_price_range: when true, attaches `variant_price_range: {min, max}` (in cents)
    to any product that has priced, non-archived variants — used by storefront grid views to
    show "From R{x}" (migration 087, Phase 4). Opt-in and best-effort so the 20+ other call
    sites of this function (WhatsApp assistant, marketing, admin) are unaffected."""
    q = _client().table("commerce_products").select("*").eq("tenant_id", tenant_id)
    if category:
        q = q.eq("category", category)
    if in_stock_only:
        q = q.eq("in_stock", True)
    result = q.order("is_daily_catch", desc=True).order("name").execute()
    rows = result.data or []
    if not include_archived:
        rows = [r for r in rows if not r.get("archived")]
    if statuses is not None:
        rows = [r for r in rows if (r.get("status") or "active") in statuses]
    if with_variant_price_range and rows:
        try:
            ids = [r["id"] for r in rows]
            vrows = (_client().table("commerce_product_variants")
                     .select("product_id,price_cents,archived")
                     .eq("tenant_id", tenant_id).in_("product_id", ids).execute().data or [])
            by_product: dict = {}
            for v in vrows:
                if v.get("archived") or v.get("price_cents") is None:
                    continue
                by_product.setdefault(v["product_id"], []).append(v["price_cents"])
            for r in rows:
                prices = by_product.get(r["id"])
                if prices:
                    r["variant_price_range"] = {"min": min(prices), "max": max(prices)}
        except Exception as exc:
            logger.debug("variant price range skipped (run migration 087?): %s", exc)
    return rows


# ── Popularity + ratings (computed from real order/review data, cached) ───────

_pop_cache: dict = {}     # tenant_id -> (expires_epoch, {product_id: paid_order_count})
_rating_cache: dict = {}  # tenant_id -> (expires_epoch, {product_id: {"avg": x, "count": n}})
_POP_TTL = 600            # 10 min

_PAID_ORDER_STATUSES = ("paid", "confirmed", "packing", "dispatched", "delivered")


def product_order_counts(tenant_id: str) -> dict:
    """{product_id: number of PAID orders containing it}. Cached 10 min."""
    import time as _t
    hit = _pop_cache.get(tenant_id)
    if hit and hit[0] > _t.time():
        return hit[1]
    counts: dict = {}
    try:
        orders = (_client().table("commerce_orders").select("id")
                  .eq("tenant_id", tenant_id).in_("status", list(_PAID_ORDER_STATUSES))
                  .limit(2000).execute().data or [])
        ids = [o["id"] for o in orders]
        if ids:
            items = (_client().table("commerce_order_items").select("order_id,product_id")
                     .in_("order_id", ids).execute().data or [])
            seen: set = set()
            for it in items:
                key = (it.get("order_id"), it.get("product_id"))
                if it.get("product_id") and key not in seen:
                    seen.add(key)
                    counts[it["product_id"]] = counts.get(it["product_id"], 0) + 1
    except Exception as exc:
        logger.debug("popularity compute skipped: %s", exc)
    _pop_cache[tenant_id] = (_t.time() + _POP_TTL, counts)
    return counts


def product_ratings(tenant_id: str) -> dict:
    """{product_id: {avg, count}} from commerce_reviews. Cached 10 min."""
    import time as _t
    hit = _rating_cache.get(tenant_id)
    if hit and hit[0] > _t.time():
        return hit[1]
    out: dict = {}
    try:
        rows = (_client().table("commerce_reviews").select("product_id,rating")
                .eq("tenant_id", tenant_id).limit(5000).execute().data or [])
        agg: dict = {}
        for r in rows:
            pid = r.get("product_id")
            if pid:
                agg.setdefault(pid, []).append(int(r["rating"]))
        for pid, ratings in agg.items():
            out[pid] = {"avg": round(sum(ratings) / len(ratings), 1), "count": len(ratings)}
    except Exception as exc:
        logger.debug("ratings compute skipped: %s", exc)
    _rating_cache[tenant_id] = (_t.time() + _POP_TTL, out)
    return out


def annotate_merchandising(tenant_id: str, rows: List[dict], popular_top: int = 8) -> List[dict]:
    """Attach orders_count / is_popular / rating to product rows (public reads)."""
    counts = product_order_counts(tenant_id)
    ratings = product_ratings(tenant_id)
    ranked = sorted((pid for pid in counts if counts[pid] >= 2),
                    key=lambda p: counts[p], reverse=True)[:popular_top]
    top = set(ranked)
    for r in rows:
        r["orders_count"] = counts.get(r["id"], 0)
        r["is_popular"] = r["id"] in top
        if r["id"] in ratings:
            r["rating"] = ratings[r["id"]]
    return rows


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
    product = result.data
    if product:
        product["variants"] = await list_variants(tenant_id, product["id"], include_archived=False)
    return product


async def get_product_by_slug(tenant_id: str, slug: str,
                              statuses: Optional[set] = None) -> Optional[dict]:
    """statuses: when given, a product whose `status` isn't in this set is treated as not
    found — see list_products' `statuses` param."""
    result = (
        _client()
        .table("commerce_products")
        .select("*")
        .eq("tenant_id", tenant_id)
        .eq("slug", slug)
        .single()
        .execute()
    )
    product = result.data
    if product and statuses is not None and (product.get("status") or "active") not in statuses:
        return None
    if product:
        product["variants"] = await list_variants(tenant_id, product["id"], include_archived=False)
    return product


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
    """Decrement stock by quantity_delta (positive reduces, negative restores). Raw SQL, race-safe."""
    _client().rpc(
        "decrement_product_stock",
        {"p_tenant_id": tenant_id, "p_product_id": product_id, "p_delta": quantity_delta},
    ).execute()


async def update_variant_stock(variant_id: str, quantity_delta: int) -> None:
    """Variant equivalent of update_product_stock (migration 087)."""
    _client().rpc(
        "decrement_variant_stock",
        {"p_variant_id": variant_id, "p_delta": quantity_delta},
    ).execute()


class OutOfStockError(ValueError):
    """Raised by create_order when one or more items can't be reserved.

    `product_name` and `available` (may be None if unknown) are provided for
    a customer-facing message; any stock already reserved for earlier items
    in the same checkout is restored before this is raised, so a failed
    checkout never leaves partial stock held against no order.
    """
    def __init__(self, product_name: str, available: Optional[int] = None):
        self.product_name = product_name
        self.available = available
        msg = f"'{product_name}' doesn't have enough stock available."
        if available is not None:
            msg = f"'{product_name}' only has {available} left in stock."
        super().__init__(msg)


async def _reserve_cart_stock(tenant_id: str, items: list) -> None:
    """Atomically reserve stock for every cart item before an order is created
    (migration 122). Each reservation is a single conditional UPDATE
    (`reserve_product_stock` / `reserve_variant_stock`) that only succeeds if
    enough stock exists, so two concurrent checkouts for the last unit can't
    both succeed. If any item fails, everything reserved so far in this call
    is restored and OutOfStockError is raised — the caller must not insert
    the order in that case.
    """
    reserved: list[tuple[Optional[str], Optional[str], int]] = []  # (product_id, variant_id, qty)
    try:
        for it in items:
            pid = it.get("product_id")
            if not pid:
                continue
            qty = int(round(float(it.get("quantity") or 0)))
            if qty <= 0:
                continue
            vid = it.get("variant_id")
            if vid:
                ok = _client().rpc(
                    "reserve_variant_stock", {"p_variant_id": vid, "p_qty": qty}
                ).execute().data
            else:
                ok = _client().rpc(
                    "reserve_product_stock",
                    {"p_tenant_id": tenant_id, "p_product_id": pid, "p_qty": qty},
                ).execute().data
            if not ok:
                name = it.get("commerce_products", {}).get("name") or "This item"
                raise OutOfStockError(name)
            reserved.append((pid, vid, qty))
    except OutOfStockError:
        for pid, vid, qty in reserved:
            try:
                if vid:
                    await update_variant_stock(vid, -qty)
                else:
                    await update_product_stock(tenant_id, pid, -qty)
            except Exception as exc:
                logger.error("stock rollback failed for product %s variant %s: %s", pid, vid, exc)
        raise


async def apply_order_stock(order_id: str, *, restore: bool = False) -> bool:
    """Decrement (sale) or restore (cancel/refund) product stock for an order's items — ONCE.

    Idempotent via the order's `stock_adjusted` flag (migration 054): a paid/confirmed order
    deducts stock exactly once no matter how many times its status changes, and a cancel/refund
    restores it exactly once. Returns True if it acted. Best-effort: never raises to the caller.
    """
    try:
        order = await get_order(order_id)
    except Exception as exc:
        logger.debug("apply_order_stock: get_order failed: %s", exc)
        return False
    if not order:
        return False
    already = bool(order.get("stock_adjusted"))
    if restore and not already:
        return False          # nothing was deducted → nothing to restore
    if (not restore) and already:
        return False          # already deducted → don't double-count
    tenant_id = order.get("tenant_id")
    items = order.get("commerce_order_items") or []
    for it in items:
        pid = it.get("product_id")
        if not pid:
            continue
        qty = int(round(float(it.get("quantity") or 0)))
        if qty <= 0:
            continue
        delta = -qty if restore else qty   # positive decrements; negative restores
        vid = it.get("variant_id")
        try:
            if vid:
                await update_variant_stock(vid, delta)
            else:
                await update_product_stock(tenant_id, pid, delta)
        except Exception as exc:
            logger.warning("stock adjust failed for product %s variant %s (order %s): %s",
                           pid, vid, order_id, exc)
    try:
        _client().table("commerce_orders").update(
            {"stock_adjusted": (not restore), "updated_at": _now()}
        ).eq("id", order_id).execute()
    except Exception as exc:
        logger.debug("stock_adjusted flag update skipped (run migration 054?): %s", exc)
    logger.info("order %s stock %s", order_id, "restored" if restore else "decremented")
    return True


def _order_item_name(cart_item: dict) -> str:
    """Product name for an order/receipt line — appends the variant's option values
    (e.g. "Hake Fillets — Size: L") when the cart item is for a specific variant."""
    name = cart_item.get("commerce_products", {}).get("name", "")
    variant = cart_item.get("commerce_product_variants")
    options = (variant or {}).get("option_values") or {}
    if options:
        suffix = ", ".join(f"{k}: {v}" for k, v in options.items())
        return f"{name} — {suffix}" if name else suffix
    return name


# ── Cart ─────────────────────────────────────────────────────────────────────

async def get_or_create_cart(tenant_id: str, session_id: str, customer_phone: Optional[str] = None) -> dict:
    result = (
        _client()
        .table("commerce_carts")
        .select("*, commerce_cart_items(*, commerce_products(name, price_cents, image_url), "
                "commerce_product_variants(option_values, sku))")
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


async def add_to_cart(tenant_id: str, cart_id: str, product_id: str, quantity: float,
                      variant_id: Optional[str] = None) -> dict:
    # Check existing — matched on (cart, product, variant) so different variants of the same
    # product are separate lines, and non-variant items keep matching each other as before.
    q = (
        _client()
        .table("commerce_cart_items")
        .select("*")
        .eq("cart_id", cart_id)
        .eq("product_id", product_id)
    )
    q = q.eq("variant_id", variant_id) if variant_id else q.is_("variant_id", "null")
    existing = q.limit(1).execute()

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

    # Fetch product to verify it belongs to tenant and get the EFFECTIVE price (sale-aware).
    product = (
        _client()
        .table("commerce_products")
        .select("price_cents,sale_price_cents,sale_ends_at")
        .eq("tenant_id", tenant_id)
        .eq("id", product_id)
        .single()
        .execute()
    )
    if not product.data:
        raise ValueError(f"Product {product_id} not found for tenant {tenant_id}")
    unit_price = effective_price_cents(product.data)

    if variant_id:
        variant = (
            _client()
            .table("commerce_product_variants")
            .select("price_cents,archived")
            .eq("id", variant_id)
            .eq("product_id", product_id)
            .single()
            .execute()
        )
        if not variant.data or variant.data.get("archived"):
            raise ValueError(f"Variant {variant_id} not found for product {product_id}")
        # A variant's own price is authoritative (no product-level sale layered on top) —
        # a variant with no price of its own inherits the product's sale-aware price.
        if variant.data.get("price_cents") is not None:
            unit_price = int(variant.data["price_cents"])

    item = {
        "id": str(uuid.uuid4()),
        "cart_id": cart_id,
        "product_id": product_id,
        "variant_id": variant_id,
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


# ── Discount codes (migration 091) ────────────────────────────────────────────
# Customer-facing storefront promo codes — distinct from the invoice-level discount_pct
# (migrations 041/053) which is a staff-entered B2B quote/invoice discount.

class DiscountError(ValueError):
    """Raised by resolve_discount_code with a customer-facing reason."""


async def list_discount_codes(tenant_id: str) -> List[dict]:
    result = (_client().table("commerce_discount_codes").select("*")
              .eq("tenant_id", tenant_id).order("created_at", desc=True).execute())
    return result.data or []


async def create_discount_code(tenant_id: str, data: dict) -> dict:
    payload = {
        "id": str(uuid.uuid4()), "tenant_id": tenant_id,
        "created_at": _now(), "updated_at": _now(), **data,
    }
    payload["code"] = (payload.get("code") or "").strip().upper()
    result = _client().table("commerce_discount_codes").insert(payload).execute()
    return result.data[0]


async def update_discount_code(tenant_id: str, code_id: str, data: dict) -> dict:
    data = dict(data)
    data["updated_at"] = _now()
    if "code" in data:
        data["code"] = (data["code"] or "").strip().upper()
    result = (_client().table("commerce_discount_codes").update(data)
              .eq("tenant_id", tenant_id).eq("id", code_id).execute())
    return result.data[0] if result.data else {}


async def delete_discount_code(tenant_id: str, code_id: str) -> None:
    _client().table("commerce_discount_codes").delete().eq("tenant_id", tenant_id).eq("id", code_id).execute()


async def resolve_discount_code(tenant_id: str, code: str, subtotal_cents: int,
                                customer_phone: str = "") -> dict:
    """Validate a discount code against a cart subtotal. Returns
    {code_row, discount_cents, free_shipping}. Raises DiscountError with a message safe to
    show the customer verbatim on any failure. Called both for a storefront's "apply code"
    preview AND again, authoritatively, inside create_order — never trust a client-computed
    discount for the amount actually charged.

    customer_phone (migration 105) powers first_order_only and per_customer_limit — pass ""
    (the preview path, before checkout has a confirmed phone) to skip those two checks; they
    still get enforced authoritatively inside create_order, which always has the phone."""
    code = (code or "").strip()
    if not code:
        raise DiscountError("Enter a discount code.")
    rows = (_client().table("commerce_discount_codes").select("*")
            .eq("tenant_id", tenant_id).ilike("code", code).limit(1).execute().data or [])
    if not rows:
        raise DiscountError(f"'{code}' isn't a valid code.")
    row = rows[0]
    if not row.get("active"):
        raise DiscountError(f"'{code}' is no longer active.")

    now = datetime.now(timezone.utc)
    starts = row.get("starts_at")
    if starts and datetime.fromisoformat(str(starts).replace("Z", "+00:00")) > now:
        raise DiscountError(f"'{code}' isn't active yet.")
    ends = row.get("ends_at")
    if ends and datetime.fromisoformat(str(ends).replace("Z", "+00:00")) < now:
        raise DiscountError(f"'{code}' has expired.")

    limit = row.get("usage_limit")
    if limit is not None and (row.get("usage_count") or 0) >= limit:
        raise DiscountError(f"'{code}' has reached its usage limit.")

    min_order = row.get("min_order_cents")
    if min_order and subtotal_cents < min_order:
        raise DiscountError(f"'{code}' needs a minimum order of R{min_order / 100:.2f}.")

    if customer_phone:
        if row.get("first_order_only"):
            prior = (_client().table("commerce_orders").select("id")
                     .eq("tenant_id", tenant_id).eq("customer_phone", customer_phone)
                     .limit(1).execute().data or [])
            if prior:
                raise DiscountError(f"'{code}' is only valid on your first order.")

        per_customer_limit = row.get("per_customer_limit")
        if per_customer_limit is not None:
            res = (_client().table("commerce_orders").select("id", count="exact")
                   .eq("tenant_id", tenant_id).eq("customer_phone", customer_phone)
                   .ilike("discount_code", code).execute())
            used = res.count if res.count is not None else len(res.data or [])
            if used >= per_customer_limit:
                raise DiscountError(f"'{code}' has already been used the maximum number of times on your account.")

    dtype = row.get("type")
    if dtype == "percent":
        discount_cents = int(round(subtotal_cents * (row.get("value") or 0) / 100.0))
        free_shipping = False
    elif dtype == "fixed":
        discount_cents = min(int(row.get("value") or 0), subtotal_cents)
        free_shipping = False
    else:  # free_shipping
        discount_cents = 0
        free_shipping = True
    return {"code_row": row, "discount_cents": discount_cents, "free_shipping": free_shipping}


async def increment_discount_usage(code_id: str) -> None:
    """Race-safe usage increment (mirrors update_product_stock's RPC pattern) — a raw
    read-then-write here could undercount usage under concurrent checkouts."""
    _client().rpc("increment_discount_code_usage", {"p_code_id": code_id}).execute()


# ── Orders ───────────────────────────────────────────────────────────────────

_ATTRIBUTION_WINDOW_DAYS = 7


async def _attribute_broadcast(tenant_id: str, phone: str) -> Optional[str]:
    """Last-click attribution: if this customer clicked a broadcast link within the last
    7 days, the order about to be placed credits that broadcast (migration 104) — closes
    the "no broadcast->order attribution" gap flagged in the marketing capability audit.
    Best-effort: a lookup failure or missing migration must never block checkout."""
    digits = _norm_phone(phone)
    if not digits:
        return None
    try:
        from datetime import datetime, timedelta, timezone
        since = (datetime.now(timezone.utc) - timedelta(days=_ATTRIBUTION_WINDOW_DAYS)).isoformat()
        rows = (_client().table("commerce_broadcast_recipients")
                .select("broadcast_id,clicked_at")
                .eq("tenant_id", tenant_id).eq("phone", digits)
                .gte("clicked_at", since)
                .order("clicked_at", desc=True).limit(1).execute().data or [])
        return rows[0]["broadcast_id"] if rows else None
    except Exception as exc:
        logger.debug("broadcast attribution lookup skipped (run migration 064?): %s", exc)
        return None


def delivery_fee_cents(tenant_id: str, cart: dict, subtotal_cents: int) -> int:
    """The delivery fee an order of this cart will actually be charged — the single source for
    create_order AND every preview (view_cart, review_order), so what the customer is shown is
    what they pay. Tenant delivery-fee rules (migration 070) override the cart's snapshotted
    default: configured standard fee, and free delivery at/above the configured subtotal."""
    delivery = cart.get("delivery_cents", 8000)
    try:
        from vula.commerce.order_workflow import get_order_settings
        _cfg = get_order_settings(tenant_id)
        if _cfg.get("delivery_fee_cents") is not None:
            delivery = int(_cfg["delivery_fee_cents"])
        free_over = _cfg.get("free_delivery_over_cents")
        if free_over and subtotal_cents >= int(free_over):
            delivery = 0
    except Exception:
        pass
    return delivery


async def create_order(tenant_id: str, cart: dict, checkout_data: dict) -> dict:
    items = cart.get("commerce_cart_items", [])
    # int(round(...)) so per-kg quantities (e.g. 1.5) resolve to exact cents.
    subtotal = sum(int(round(i["quantity"] * i["unit_price_cents"])) for i in items)
    delivery = delivery_fee_cents(tenant_id, cart, subtotal)

    # Discount code (migration 091) — resolved authoritatively here regardless of any
    # client-side preview, since the actual amount charged must never trust the client.
    discount_cents, discount_code, code_row = 0, None, None
    raw_code = (checkout_data.get("discount_code") or "").strip()
    if raw_code:
        try:
            resolved = await resolve_discount_code(
                tenant_id, raw_code, subtotal, checkout_data.get("customer_phone") or "")
            code_row = resolved["code_row"]
            discount_cents = resolved["discount_cents"]
            discount_code = code_row["code"]
            if resolved["free_shipping"]:
                delivery = 0
        except DiscountError as exc:
            logger.info("discount code '%s' rejected at checkout: %s", raw_code, exc)
        except Exception as exc:
            # A bad/missing discount_codes table (migration 091 not run yet) or any other DB
            # hiccup must never break checkout itself — the purchase proceeds without the
            # discount rather than crashing.
            logger.warning("discount code lookup failed at checkout, proceeding without it (%s): %s",
                           raw_code, exc)

    total = max(0, subtotal - discount_cents) + delivery
    display_id = await _next_order_display_id(tenant_id)
    attributed_broadcast_id = await _attribute_broadcast(tenant_id, checkout_data["customer_phone"])

    # Reserve stock atomically BEFORE the order is inserted (migration 122). Previously
    # stock was only decremented later at payment confirmation, so two concurrent
    # checkouts for the last unit of a product would both succeed here and the shortfall
    # would only surface as a silent clamp-to-zero at payment time. Raises OutOfStockError
    # (already restoring anything reserved earlier in this same checkout) if unavailable.
    await _reserve_cart_stock(tenant_id, items)

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
        "discount_code": discount_code,
        "discount_cents": discount_cents,
        "total_cents": total,
        "status": "pending_payment",
        "channel": checkout_data.get("channel", "web"),
        "payment_method": checkout_data.get("payment_method"),  # online | cod | eft (migration 044)
        "attributed_broadcast_id": attributed_broadcast_id,      # migration 104
        "cart_id": cart["id"],
        # Stock was just reserved above, at creation time rather than at payment
        # confirmation — mark it adjusted now so apply_order_stock's idempotency guard
        # (migration 054) correctly no-ops a later decrement and correctly allows a
        # later cancel/refund to restore it exactly once.
        "stock_adjusted": True,
        "created_at": _now(),
        "updated_at": _now(),
    }

    try:
        try:
            result = _client().table("commerce_orders").insert(order).execute()
        except Exception as exc:
            # Any of these newer optional columns might not exist yet on an un-migrated DB
            # (payment_method: migration 044; discount_code/discount_cents: migration 091;
            # attributed_broadcast_id: migration 104) — strip them and retry so ordering
            # never breaks on a missing column.
            method = order.pop("payment_method", None)
            order.pop("discount_code", None)
            order.pop("discount_cents", None)
            order.pop("attributed_broadcast_id", None)
            if method:
                note = order.get("delivery_notes") or ""
                order["delivery_notes"] = (f"[pay:{method}] " + note).strip()
            logger.warning("order insert retried without payment_method/discount/attribution fields (%s): %s", method, exc)
            result = _client().table("commerce_orders").insert(order).execute()
            code_row = None  # discount_cents column didn't exist -> don't count usage below
    except Exception:
        # Order insert failed even after the compatibility retry — stock was already
        # reserved above, so restore it rather than leaving it stranded against an
        # order that was never created.
        for it in items:
            pid = it.get("product_id")
            if not pid:
                continue
            qty = int(round(float(it.get("quantity") or 0)))
            if qty <= 0:
                continue
            vid = it.get("variant_id")
            try:
                if vid:
                    await update_variant_stock(vid, -qty)
                else:
                    await update_product_stock(tenant_id, pid, -qty)
            except Exception as rexc:
                logger.error("stock rollback failed after order-insert failure for product %s variant %s: %s",
                             pid, vid, rexc)
        raise
    order_id = result.data[0]["id"]

    if code_row:
        try:
            await increment_discount_usage(code_row["id"])
        except Exception as exc:
            logger.warning("discount usage increment failed for code %s: %s", code_row["id"], exc)

    # Insert order items
    order_items = [
        {
            "id": str(uuid.uuid4()),
            "order_id": order_id,
            "product_id": i["product_id"],
            "variant_id": i.get("variant_id"),
            "product_name": _order_item_name(i),
            "quantity": i["quantity"],
            "unit_price_cents": i["unit_price_cents"],
            "total_cents": int(round(i["quantity"] * i["unit_price_cents"])),
        }
        for i in items
    ]
    _client().table("commerce_order_items").insert(order_items).execute()

    # The cart is spent — convert it so the next order starts empty. WhatsApp carts are keyed
    # by the customer's phone (one long-lived "active" cart per number), so without this the
    # next order re-charged every item from the previous one and a repeated "yes" placed a
    # duplicate. Never fails the order: it's already placed and the items are recorded.
    try:
        await clear_cart(cart["id"])
    except Exception as exc:
        logger.warning("cart %s not cleared after order %s: %s", cart.get("id"), order_id, exc)

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
        .select("id,display_id,customer_name,customer_phone,total_cents,status,channel,delivery_slot,"
                "created_at,yoco_checkout_id,refund_status")
        .eq("tenant_id", tenant_id)
    )
    if status:
        q = q.eq("status", status)
    result = q.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
    return result.data or []


def orders_for_phone(tenant_id: str, phone: str, columns: str = "*",
                     exclude_statuses: Optional[List[str]] = None, limit: int = 20) -> List[dict]:
    """This customer's orders, newest first, matched on the last 9 phone digits.

    2026-09-25 review: every caller used to load the tenant's latest 30-50 orders and filter by
    phone in Python, so once a shop had more orders than that a returning customer's history
    was invisible (no reorder, no saved address, "couldn't find your order"). The suffix match
    now runs in the database; if stored numbers carry spaces/dashes that defeat it, a wider
    Python scan (the old behaviour, 500 rows) is the fallback."""
    digits = "".join(c for c in (phone or "") if c.isdigit())
    if len(digits) < 9:
        return []
    tail = digits[-9:]

    def _mine(rows):
        return [o for o in rows
                if "".join(c for c in (o.get("customer_phone") or "") if c.isdigit()).endswith(tail)]

    def _q(n):
        q = _client().table("commerce_orders").select(columns).eq("tenant_id", tenant_id)
        if exclude_statuses:
            q = q.not_.in_("status", exclude_statuses)
        return q.order("created_at", desc=True).limit(n)

    rows = _mine(_q(limit).ilike("customer_phone", f"%{tail}").execute().data or [])
    if not rows:
        rows = _mine(_q(500).execute().data or [])[:limit]
    return rows


async def reorder_from_last_order(tenant_id: str, phone: str) -> dict:
    """Find this customer's most recent order and return its line items, for WhatsApp's
    'reorder'/'same as last time' shortcut. Matches on the last 9 digits of the phone number
    (mirrors the defensive suffix matching commerce_assistant.py already uses for cancel/change
    order, since stored customer_phone formatting isn't perfectly consistent). Raises ValueError
    with a message safe to show the customer if there's no prior order to repeat."""
    digits = "".join(c for c in (phone or "") if c.isdigit())
    if not digits:
        raise ValueError("no phone number to look up")
    mine = orders_for_phone(tenant_id, phone, "id,display_id,customer_phone,created_at", limit=1)
    if not mine:
        raise ValueError("no previous order found to repeat")
    last = await get_order(mine[0]["id"])
    items = (last or {}).get("commerce_order_items") or []
    if not items:
        raise ValueError("that order has no items on file to repeat")
    return {"display_id": last.get("display_id"), "items": items}


def _same_business(a: str, b: str) -> bool:
    """Are these two names the same business, allowing for spelling and spacing?

    Real off-the-hook data carries "OfftheHook" (50 rows) and "Off the Hook" (1) for the same
    entity, and the tenant's own display name is "Off the Hook".
    """
    def _key(s) -> str:
        # Coerced rather than assumed a string: the tenant display-name lookup can hand back
        # None, and a caller may pass anything. A crash here would take down document intake
        # for the sake of a name comparison.
        if not isinstance(s, str):
            s = "" if s is None else str(s)
        s = re.sub(r"[^a-z0-9]", "", s.lower())
        # Repeatedly: "ACME (Pty) Ltd" -> "acmeptyltd" needs BOTH suffixes off to match
        # "Acme Pty" -> "acmepty". A single pass left them as "acmepty" vs "acme".
        for _ in range(3):
            stripped = re.sub(r"(pty|ltd|limited|inc|cc)$", "", s)
            if stripped == s:
                break
            s = stripped
        return s
    ka, kb = _key(a), _key(b)
    return bool(ka) and ka == kb


def classify_direction(supplier_name: str, tenant_name: str, tenant_id: str,
                       supplier_known: bool = False) -> tuple:
    """Work out whether a scanned document is ours or theirs, and how sure we are.

    Returns (direction, confident, reason).

    Bulk historical imports are the hard case (Ian, 2026-09-03): a tenant uploads a folder of old
    paperwork that mixes their own client invoices, supplier bills and expense slips. The
    extractor gives us the ISSUER only — there is no bill-to field — so:

      • issuer IS this tenant            -> outbound, confident   (they wrote it)
      • issuer is an ALREADY-KNOWN supplier -> inbound, confident (we buy from them)
      • issuer is an unknown third party -> inbound, NOT confident

    That last case is genuinely ambiguous: an unknown name is equally likely to be a new supplier
    or a client whose invoice is being imported. Guessing corrupts the books in a way that is
    invisible afterwards — it was a silent hardcoded "inbound" that put 51 of off-the-hook's own
    sales invoices (R32,307.97) on the wrong side of the ledger. So the caller marks it for
    review and asks a human rather than committing a guess.
    """
    if _same_business(supplier_name, tenant_name) or _same_business(supplier_name, tenant_id):
        return "outbound", True, "issued by this business"
    if supplier_known:
        return "inbound", True, "known supplier"
    if not (supplier_name or "").strip():
        return "inbound", False, "no issuer found on the document"
    return "inbound", False, "unrecognised party — could be a new supplier or a client"


def _coerce_line_items(value) -> List[dict]:
    """Always store line_items as a real LIST of dicts.

    2026-09-02, real DIGG incident: the document-scan commit path wrote
    `json.dumps(line_items)` while every other write path stored the list itself. The column
    holds JSON, so the dumped string was stored as a JSON *string* — and reading it back gives
    a string, which len() and iteration treat CHARACTER BY CHARACTER. A R1,599.90 supplier
    invoice came back with 260 "line items": '[', '{', '"', 'd', 'e', 's', 'c', ... Every
    email-scanned invoice and expense since the feature shipped is affected, and opening one
    shows hundreds of single-character rows.

    Accepts what the scanner might realistically produce — a list, a JSON string (including a
    double-encoded one), or nothing — and always returns a list of dicts.
    """
    import json as _json
    for _ in range(3):          # unwrap double-encoding rather than trusting one pass
        if value is None or value == "":
            return []
        if isinstance(value, list):
            return [i for i in value if isinstance(i, dict)]
        if isinstance(value, dict):
            return [value]
        if isinstance(value, str):
            try:
                value = _json.loads(value)
            except Exception:
                logger.warning("line_items was an unparseable string (%d chars) — storing empty",
                               len(value))
                return []
        else:
            return []
    return []


async def get_customer_profile(tenant_id: str, phone: str) -> Optional[dict]:
    """What we already know about a returning customer, from their most recent real order.

    2026-09-01, ahead of OTH taking real WhatsApp orders: nothing reused a known customer's
    details, so a repeat buyer was asked for their name and delivery address from scratch on
    every single order even though both were already on file. That's the friction most likely
    to make someone abandon a WhatsApp order. Returns None for a genuinely new customer (never
    invents details), and only ever returns what the customer themselves supplied before.

    Matched on the last 9 digits of the phone number, same defensive suffix matching as
    reorder_from_last_order — stored customer_phone formatting isn't perfectly consistent.
    """
    digits = "".join(c for c in (phone or "") if c.isdigit())
    if not digits:
        return None
    try:
        mine = orders_for_phone(tenant_id, phone,
                                "display_id,customer_name,customer_phone,customer_email,"
                                "delivery_address,delivery_slot,created_at",
                                exclude_statuses=["cancelled", "refunded"], limit=1)
    except Exception as exc:  # never block a live order on a profile lookup
        logger.warning("get_customer_profile lookup failed (tenant=%s): %s", tenant_id, exc)
        return None
    if not mine:
        return None
    last = mine[0]
    return {
        "name": (last.get("customer_name") or "").strip() or None,
        "email": (last.get("customer_email") or "").strip() or None,
        "delivery_address": (last.get("delivery_address") or "").strip() or None,
        "delivery_slot": (last.get("delivery_slot") or "").strip() or None,
        "last_order": last.get("display_id"),
        "last_order_at": last.get("created_at"),
        "order_count": len(mine),
    }


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


# ── Product variants (migration 087, Phase 4) ────────────────────────────────

async def list_variants(tenant_id: str, product_id: str, include_archived: bool = True) -> List[dict]:
    try:
        q = (_client().table("commerce_product_variants").select("*")
             .eq("tenant_id", tenant_id).eq("product_id", product_id))
        if not include_archived:
            q = q.eq("archived", False)
        result = q.order("sort_order").execute()
        return result.data or []
    except Exception as exc:
        logger.debug("variants list skipped (run migration 087?): %s", exc)
        return []


async def create_variant(tenant_id: str, product_id: str, data: dict) -> dict:
    payload = {
        "id": str(uuid.uuid4()), "tenant_id": tenant_id, "product_id": product_id,
        "created_at": _now(), "updated_at": _now(), **data,
    }
    result = _client().table("commerce_product_variants").insert(payload).execute()
    return result.data[0]


async def update_variant(tenant_id: str, variant_id: str, data: dict) -> dict:
    data["updated_at"] = _now()
    result = (_client().table("commerce_product_variants").update(data)
              .eq("tenant_id", tenant_id).eq("id", variant_id).execute())
    return result.data[0] if result.data else {}


async def delete_variant(tenant_id: str, variant_id: str) -> None:
    (_client().table("commerce_product_variants").delete()
     .eq("tenant_id", tenant_id).eq("id", variant_id).execute())


async def find_by_barcode(tenant_id: str, barcode: str) -> Optional[dict]:
    """Barcode scan lookup (Smart Scanner POS prep) — returns the variant plus its parent
    product, or None if nothing matches."""
    result = (_client().table("commerce_product_variants")
              .select("*, commerce_products(*)")
              .eq("tenant_id", tenant_id).eq("barcode", barcode).limit(1).execute())
    return result.data[0] if result.data else None


async def update_order_status(order_id: str, status: str, yoco_checkout_id: Optional[str] = None,
                              payment_method: Optional[str] = None) -> None:
    """payment_method records HOW a walk-in paid (cash, card, eft, snapscan). Invoices have
    carried this since migration 130 via record_invoice_payment; orders — where a shop's cash
    sales actually land — did not, so 'paid' said nothing about whether the money was in the
    till or the bank. Optional and best-effort: the column arrived in migration 044, so a
    failure retries without it rather than losing the status change itself."""
    update = {"status": status, "updated_at": _now()}
    if yoco_checkout_id:
        update["yoco_checkout_id"] = yoco_checkout_id
    if payment_method:
        update["payment_method"] = payment_method
    try:
        result = _client().table("commerce_orders").update(update).eq("id", order_id).execute()
    except Exception as exc:
        if not payment_method:
            raise
        logger.warning("order update retried without payment_method (%s): %s", payment_method, exc)
        update.pop("payment_method", None)
        result = _client().table("commerce_orders").update(update).eq("id", order_id).execute()
    if status in ("paid", "refunded") and result.data:
        try:
            from vula.commerce import ledger
            order = result.data[0]
            if status == "paid":
                ledger.post_order_paid(order["tenant_id"], order)
            else:
                ledger.post_order_refund(order["tenant_id"], order)
        except Exception as exc:
            logger.warning("ledger hook failed for order %s: %s", order_id, exc)


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
    """Race-safe (migration 122) — see `_next_invoice_number` above for why
    this can no longer be a SELECT-last-then-add-1 read/write pair."""
    result = _client().rpc(
        "next_document_number", {"p_tenant_id": tenant_id, "p_counter_key": "order"}
    ).execute()
    num = int(result.data)
    prefix = tenant_id.upper()[:3]
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


async def set_session_language(tenant_id: str, session_key: str, language: str) -> None:
    """Remember the customer's language on their session (best-effort; needs migration 052)."""
    try:
        _client().table("commerce_conversation_sessions").update(
            {"preferred_language": language, "updated_at": _now()}
        ).eq("tenant_id", tenant_id).eq("session_key", session_key).execute()
    except Exception as exc:
        logger.debug("set_session_language skipped (run migration 052?): %s", exc)


async def append_message(tenant_id: str, session_id: str, role: str, content: str) -> None:
    """Persist a single conversation turn and update the session snapshot."""
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
    # Update last-message snapshot on the session row (best-effort).
    try:
        snippet = content[:160]
        _client().table("commerce_conversation_sessions").update({
            "last_message": snippet,
            "last_role": role,
            "last_at": _now(),
            "updated_at": _now(),
        }).eq("id", session_id).execute()
    except Exception:
        pass


async def set_session_paused(tenant_id: str, session_id: str, paused: bool) -> dict:
    """Toggle the human-handoff flag on a session. Returns the updated row."""
    update: dict = {"paused": paused, "updated_at": _now()}
    if paused:
        update["paused_at"] = _now()
    else:
        update["paused_by"] = None
        update["paused_at"] = None
    result = (
        _client()
        .table("commerce_conversation_sessions")
        .update(update)
        .eq("tenant_id", tenant_id)
        .eq("id", session_id)
        .execute()
    )
    return result.data[0] if result.data else {}


async def find_stale_paused_sessions(tenant_id: str, hours: float = 2.0) -> list[dict]:
    """Sessions paused for human handoff that have gone completely silent — no customer or
    staff activity (last_at, the same snapshot append_message() stamps on every message) —
    for `hours`. Human handoff has no expiry today: if the owner who took over a thread gets
    distracted, the bot stays muted on it forever and the customer can be left permanently
    ghosted (2026-09-11). A conversation staff is actively working stays untouched; only one
    nobody has looked at in that long is a candidate for the stale-handoff scheduler
    (server.py) to notify + auto-resume."""
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    try:
        rows = (_client().table("commerce_conversation_sessions").select("*")
                .eq("tenant_id", tenant_id).eq("paused", True)
                .lt("last_at", cutoff)
                .limit(50).execute().data or [])
    except Exception as exc:
        logger.debug("stale paused session lookup skipped: %s", exc)
        return []
    return rows


async def list_conversations(tenant_id: str, limit: int = 50) -> List[dict]:
    """Return recent conversation sessions for the shared inbox list view."""
    base = "id,session_key,customer_phone,customer_name,paused,last_message,last_role,last_at,created_at,updated_at"
    try:
        result = (
            _client().table("commerce_conversation_sessions")
            .select(base + ",assigned_to,agent_note,tags")
            .eq("tenant_id", tenant_id).order("updated_at", desc=True).limit(limit).execute()
        )
    except Exception:
        # team-inbox columns not present yet (migration 048 not run) — fall back gracefully.
        result = (
            _client().table("commerce_conversation_sessions").select(base)
            .eq("tenant_id", tenant_id).order("updated_at", desc=True).limit(limit).execute()
        )
    rows = result.data or []
    # Expose session id as session_id for the frontend.
    for r in rows:
        r["session_id"] = r["id"]
    return rows


async def get_conversation_thread(tenant_id: str, session_id: str) -> Optional[dict]:
    """Return the session header + full message list for the thread view."""
    sessions = (
        _client()
        .table("commerce_conversation_sessions")
        .select("*")
        .eq("tenant_id", tenant_id)
        .eq("id", session_id)
        .limit(1)
        .execute()
    )
    if not sessions.data:
        return None
    session = sessions.data[0]
    # Staleness is only a MODEL-context concern (see get_recent_messages below) — a human
    # reading the shared-inbox thread wants the full history regardless of age, so this call
    # explicitly opts out of the age cutoff.
    messages = await get_recent_messages(tenant_id, session_id, limit=200, max_age_hours=None)
    return {
        "session_id": session["id"],
        "customer": session.get("customer_name") or session.get("customer_phone") or session.get("session_key"),
        "phone": session.get("customer_phone") or session.get("session_key"),
        "paused": bool(session.get("paused")),
        "assigned_to": session.get("assigned_to"),
        "agent_note": session.get("agent_note"),
        "tags": session.get("tags") or [],
        "messages": [{"role": m["role"], "content": m["content"], "created_at": m.get("created_at")} for m in messages],
    }


# Sessions never expire/rotate (commerce_conversation_sessions has no TTL — the same session_id
# is reused forever, keyed only on phone number), so on a low-traffic session "last N messages"
# by COUNT alone can silently reach back hours or days. Confirmed live, 2026-08-27 (gerflor): a
# 7-hour-old, completely unrelated message was still well inside the last-12-messages window and
# got echoed back as if it were fresh context. 24h is a deliberate balance — long enough that a
# same-day, multi-hour gap (e.g. a lunch break mid-conversation) still keeps its context, short
# enough that yesterday's finished topic never leaks into today's.
HISTORY_MAX_AGE_HOURS = 24
DEFAULT_HISTORY_LIMIT = 12


async def get_recent_messages(tenant_id: str, session_id: str, limit: int = DEFAULT_HISTORY_LIMIT,
                              max_age_hours: Optional[float] = HISTORY_MAX_AGE_HOURS) -> List[dict]:
    """Return the most recent messages for a session, oldest first. max_age_hours additionally
    bounds how far back to look (pass None to disable, e.g. for a human-facing thread view)."""
    q = (
        _client()
        .table("commerce_conversation_messages")
        .select("role,content,created_at")
        .eq("tenant_id", tenant_id)
        .eq("session_id", session_id)
    )
    if max_age_hours is not None:
        from core.time_fmt import cutoff_iso
        q = q.gte("created_at", cutoff_iso(max_age_hours))
    result = q.order("created_at", desc=True).limit(limit).execute()
    rows = result.data or []
    return list(reversed(rows))


# Caveat suffixes appended to a reply AFTER the skill produces it (core/verification.py's
# adversarial-review warning) — meant for a human reading the message/transcript, never for the
# model itself. Confirmed live 2026-08-26: a customer-facing session that had one of these
# appended once later re-fed it back into the model's own context via conversation_history, and
# the model tried to actively "resolve" its own past self-doubt annotation out loud WITH THE
# CUSTOMER ("I noticed the automated review flagged possible issues... could you confirm...") —
# a real, confusing, unprofessional leak of internal machinery. Stripped here, not at the point
# the reply is sent/persisted, so the caveat still reaches whoever's actually reading it live.
_HISTORY_STRIP_MARKERS = (
    "\n\n⚠️ Please double-check this answer",
    "\n\n⚠️ I couldn't find a specific document",
)


def _strip_caveats_for_history(content: str) -> str:
    for marker in _HISTORY_STRIP_MARKERS:
        idx = content.find(marker)
        if idx != -1:
            content = content[:idx]
    return content.strip()


def format_history(messages: List[dict]) -> str:
    """Render messages into a compact transcript for the skill's conversation_history. Each
    line is tagged with its actual age (2026-08-27) — previously role+content only, giving the
    model no way to tell a 7-hour-old line apart from one said seconds ago (see
    HISTORY_MAX_AGE_HOURS above for the full incident)."""
    from core.time_fmt import relative_age_label
    label = {"user": "Customer", "assistant": "Assistant"}
    lines = []
    for m in messages:
        if m.get("role") not in ("user", "assistant") or not m.get("content"):
            continue
        age = relative_age_label(m.get("created_at") or "")
        age_tag = f" ({age})" if age else ""
        lines.append(f"{label.get(m['role'], m['role'].title())}{age_tag}: "
                     f"{_strip_caveats_for_history(m['content'])}")
    return "\n".join(lines)


# ── Invoices & Quotes ─────────────────────────────────────────────────────────
# One table (commerce_invoices) serves invoices, quotes, and proformas via
# doc_type. All money is integer cents (ZAR). Every query is tenant-scoped.

_DOC_TYPE_CODE = {"invoice": "INV", "quote": "QTE", "proforma": "PRO", "credit_note": "CRN"}


def _compute_totals(line_items: List[dict], vat_rate: float,
                    prices_include_vat: bool = False,
                    invoice_discount_pct: float = 0.0) -> tuple[int, int, int, int, List[dict]]:
    """Return (subtotal_cents, discount_cents, vat_cents, total_cents, normalised_items).

    Each line's total is recomputed server-side from quantity * unit_price * (1 - line discount).
    An optional invoice-level discount % then comes off the subtotal, and VAT applies to the
    discounted amount. All values are integer cents.
    - exclusive (default): subtotal = sum(lines); taxable = subtotal − discount; vat = taxable*rate.
    - inclusive: entered line prices already contain VAT → discount off the gross, then back out VAT.
    """
    normalised: List[dict] = []
    entered = 0
    for item in line_items:
        qty = float(item["quantity"])          # decimals allowed for per-kg items
        unit = int(item["unit_price_cents"])
        line_disc = float(item.get("discount_pct") or 0)
        line_total = int(round(qty * unit * (1 - line_disc / 100.0)))
        entered += line_total
        normalised.append(
            {
                "description": item["description"],
                "quantity": qty,
                "unit": (item.get("unit") or "").strip() or None,   # ea/no./kg/m/m²/m³/lin.m/hr/day/%
                "unit_price_cents": unit,
                "discount_pct": line_disc or None,
                "total_cents": line_total,
                "product_id": str(item["product_id"]) if item.get("product_id") else None,
                # Optional BoQ-style trade section (e.g. "Demolition", "Structure") — a
                # construction invoice/quote can group lines with a subtotal per section.
                # None for every existing caller, so this is purely additive.
                "section": (item.get("section") or "").strip() or None,
            }
        )
    discount_cents = int(round(entered * (invoice_discount_pct / 100.0))) if invoice_discount_pct else 0
    after_discount = entered - discount_cents
    if prices_include_vat and vat_rate > 0:
        # entered prices already contain VAT. Work in ex-VAT terms so the columns foot:
        # subtotal(ex, pre-discount) − discount(ex) = taxable(ex); taxable + vat = total(incl).
        subtotal = round(entered / (1 + vat_rate / 100.0))
        net_after = round(after_discount / (1 + vat_rate / 100.0))
        return subtotal, subtotal - net_after, after_discount - net_after, after_discount, normalised
    vat_cents = round(after_discount * (vat_rate / 100.0))
    return entered, discount_cents, vat_cents, after_discount + vat_cents, normalised


async def _next_invoice_number(tenant_id: str, doc_type: str,
                               direction: str = "outbound") -> str:
    """Sequential, tenant-scoped, doc-type-scoped number e.g. OTH-INV-00001.

    Race-safe (migration 122): the number comes from a single atomic
    UPSERT...RETURNING RPC (`next_document_number`) rather than a
    SELECT-last-then-add-1-in-Python read/write pair, which two concurrent
    invoice creations could both read before either had written back,
    minting the same number twice.

    2026-09-02: INBOUND documents (a supplier's invoice, filed from email) used to draw from the
    SAME counter as the tenant's own outgoing invoices. Confirmed on real DIGG data: 74 of the
    77 issued DIG-INV numbers had gone to other companies' invoices, leaving DIGG's own series
    reading 51 -> 61 -> 74 -> 85 with large unexplained gaps. SARS expects an unbroken
    sequential tax-invoice series, so that made their books look like dozens of invoices had
    been issued and voided.

    Inbound now uses its own counter and a BILL code, so a supplier bill is clearly OUR internal
    reference for someone else's document and never consumes a number the tenant could be asked
    to account for. Existing rows keep the numbers they were given — renumbering issued
    documents would be worse than the gaps.

    2026-09-08: every inbound doc_type shares the single "BILL" code above, but until this fix
    the counter_key was still split per doc_type ("inbound_invoice" vs "inbound_quote" etc.) —
    two different inbound doc types for the same tenant could both mint e.g. "OTH-BILL-00001",
    tripping the (tenant_id, invoice_number) unique index (migration 032) or, if unenforced,
    silently sharing one reference between two unrelated documents. The counter must match the
    code it's scoped to: one shared "inbound" counter for every inbound doc_type.
    """
    inbound = (direction or "outbound").lower() == "inbound"
    code = "BILL" if inbound else _DOC_TYPE_CODE.get(doc_type, "INV")
    counter_key = "inbound" if inbound else doc_type
    result = _client().rpc(
        "next_document_number", {"p_tenant_id": tenant_id, "p_counter_key": counter_key}
    ).execute()
    num = int(result.data)
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
    inv_disc_pct = float(data.get("discount_pct") or 0)
    subtotal, discount_cents, vat_cents, total, items = _compute_totals(
        data["line_items"], vat_rate, prices_include_vat, inv_disc_pct)
    deposit_cents = int(data.get("deposit_cents") or 0)
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
        "discount_cents": discount_cents,
        "vat_rate": vat_rate,
        "vat_cents": vat_cents,
        "total_cents": total,
        "deposit_cents": deposit_cents,
        "status": data.get("status", "draft"),
        "project": data.get("project"),
        "issue_date": data.get("issue_date") or _now()[:10],
        "due_date": data.get("due_date"),
        "valid_until": data.get("valid_until"),
        "order_id": str(data["order_id"]) if data.get("order_id") else None,
        "payment_method": data.get("payment_method"),
        "notes": data.get("notes"),
        "requires_approval": bool(data.get("requires_approval")),
        "created_at": _now(),
        "updated_at": _now(),
    }
    try:
        result = _client().table("commerce_invoices").insert(invoice).execute()
    except Exception as exc:
        # discount_cents/deposit_cents (053), project (055), or requires_approval (136) columns
        # may not exist yet — drop them and retry so invoicing never breaks on a missing column.
        invoice.pop("discount_cents", None)
        invoice.pop("deposit_cents", None)
        invoice.pop("project", None)
        invoice.pop("requires_approval", None)
        logger.warning("invoice insert retried without discount/deposit/project/approval (run migrations 053/055/136?): %s", exc)
        result = _client().table("commerce_invoices").insert(invoice).execute()
    return result.data[0]


async def send_order_invoice(tenant_id: str, order_id: str) -> Optional[dict]:
    """Auto-generate an invoice for a just-placed order and WhatsApp it to the customer —
    the invoice doubles as the payment request, not a post-payment receipt. Self-contained
    and safe to call fire-and-forget: never raises, so a PDF/WhatsApp hiccup can never break
    order placement itself. The invoice row is always created first (the accounting paper
    trail) and only marked 'sent' if the WhatsApp delivery actually succeeds — if it
    doesn't, the invoice still sits as a draft in the dashboard for an admin to send
    manually via the existing button, rather than being silently lost.
    """
    try:
        order = await get_order(order_id)
    except Exception as exc:
        logger.warning("send_order_invoice: order lookup failed for %s: %s", order_id, exc)
        return None
    if not order:
        logger.warning("send_order_invoice: order %s not found", order_id)
        return None
    phone = (order.get("customer_phone") or "").strip()
    if not phone:
        logger.info("send_order_invoice: order %s has no customer_phone, skipping", order_id)
        return None

    line_items = [
        {"description": it.get("product_name") or "Item", "quantity": it["quantity"],
         "unit_price_cents": it["unit_price_cents"], "product_id": it.get("product_id")}
        for it in (order.get("commerce_order_items") or [])
    ]
    delivery_cents = int(order.get("delivery_cents") or 0)
    if delivery_cents > 0:
        line_items.append({"description": "Delivery", "quantity": 1,
                           "unit_price_cents": delivery_cents})

    try:
        invoice = await create_invoice(tenant_id, {
            "doc_type": "invoice",
            "customer_name": order.get("customer_name"),
            "customer_phone": phone,
            "customer_email": order.get("customer_email"),
            "customer_address": order.get("delivery_address"),
            "line_items": line_items,
            "order_id": order["id"],
            "notes": f"Auto-generated for order {order.get('display_id')}",
        })
    except Exception as exc:
        logger.warning("send_order_invoice: invoice creation failed for order %s: %s", order_id, exc)
        return None

    # Best-effort "Pay now" link — a connected gateway is optional, so this never blocks the
    # invoice itself from being created/sent if no provider is set up or the call fails.
    try:
        from vula import payments as _payments
        api_base = "https://vula-group-production.up.railway.app"
        row = _payments.default_provider_row(tenant_id)
        provider = row["provider"] if row else "yoco"
        link = await _payments.create_pay_link(
            tenant_id, amount_cents=int(invoice["total_cents"]), reference=invoice["id"],
            description=f"Invoice {invoice.get('invoice_number') or ''}".strip(),
            success_url=f"{api_base}/payment/success?invoice={invoice['id']}",
            cancel_url=f"{api_base}/payment/cancel?invoice={invoice['id']}",
            notify_url=f"{api_base}/v1/payments/webhook/{tenant_id}/{provider}",
            customer={"email": invoice.get("customer_email"), "phone": phone})
        if link and link.url:
            _client().table("commerce_invoices").update(
                {"pay_url": link.url, "yoco_checkout_id": link.raw.get("id")}
            ).eq("id", invoice["id"]).execute()
            invoice["pay_url"] = link.url
    except Exception as exc:
        logger.debug("send_order_invoice: pay-link skipped for invoice %s: %s", invoice.get("id"), exc)

    try:
        from vula.commerce.pdf import render_invoice_pdf, merge_branding
        from vula.api.whatsapp import _send_invoice_document

        branding = merge_branding(tenant_id, await get_invoice_settings(tenant_id))
        pdf_bytes = render_invoice_pdf(invoice, branding)

        tenant_name = branding.get("name") or tenant_id.replace("-", " ").title()
        number = invoice.get("invoice_number", invoice["id"])
        total = f"R{(int(invoice.get('total_cents') or 0) / 100):.2f}"
        caption = (
            f"Hi {invoice.get('customer_name', 'there')}, here is your invoice {number} "
            f"for {total} from {tenant_name}. Thank you for your order!"
        )
        if invoice.get("pay_url"):
            caption += f"\n\n💳 Pay now: {invoice['pay_url']}"
        sent = await _send_invoice_document(phone, pdf_bytes, f"{number}.pdf", caption, tenant_id)
        if sent:
            await update_invoice_status(tenant_id, invoice["id"], "sent")
    except Exception as exc:
        logger.warning("send_order_invoice: PDF render/send failed for order %s (invoice %s "
                       "stays draft): %s", order_id, invoice.get("id"), exc)

    return invoice


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
    supplier_id: Optional[str] = None,
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
    if supplier_id:
        q = q.eq("supplier_id", supplier_id)
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
    invoice = result.data[0] if result.data else {}
    if status == "paid" and invoice:
        try:
            from vula.commerce import ledger
            # 2026-09-03: this used to post the SALES entry unconditionally. An inbound supplier
            # bill marked paid would then have been booked as revenue — inventing income and
            # inflating VAT output — because the ledger had no payables posting at all. Which
            # side of the books this belongs on depends entirely on who issued the document.
            if (invoice.get("direction") or "outbound") == "inbound":
                ledger.post_supplier_invoice_paid(tenant_id, invoice)
            else:
                ledger.post_invoice_paid(tenant_id, invoice)
        except Exception as exc:
            logger.warning("ledger hook failed for invoice %s: %s", invoice_id, exc)
        await _settle_linked_order(tenant_id, invoice)
    return invoice


async def _settle_linked_order(tenant_id: str, invoice: dict) -> None:
    """An order's auto-generated invoice (send_order_invoice) was paid → the order is paid too.

    Before this the order stayed pending_payment: never dispatched, and the unpaid-order chase
    kept nagging a customer who had already paid. The order row is flipped directly (NOT via
    update_order_status) because the revenue was just posted to the ledger for the invoice —
    posting it again for the order would double-count it. Conditional on pending_payment so a
    repeat call is a no-op. Never raises."""
    order_id = invoice.get("order_id")
    if not order_id or (invoice.get("direction") or "outbound") == "inbound":
        return
    try:
        res = (_client().table("commerce_orders")
               .update({"status": "paid", "updated_at": _now()})
               .eq("tenant_id", tenant_id).eq("id", order_id).eq("status", "pending_payment")
               .execute())
        if not res.data:
            return
        o = res.data[0]
        from vula.api.yoco import _notify_order_paid
        await _notify_order_paid(tenant_id, o.get("display_id") or "", order_id, o.get("customer_phone"),
                                 o.get("customer_name") or "", int(o.get("total_cents") or 0))
    except Exception as exc:
        logger.warning("linked order %s not settled after invoice %s paid: %s",
                       order_id, invoice.get("id"), exc)


async def convert_quote_to_invoice(tenant_id: str, quote_id: str,
                                    amount_cents: Optional[int] = None) -> dict:
    """Create an invoice from an accepted quote, linking both directions.

    The quote is stamped with converted_invoice_id (on the first conversion only) and its
    invoiced_cents running total is incremented; the new invoice carries source_quote_id back
    to the quote. issue_date is set to today (the day it's actually being invoiced), not
    carried over from the quote.

    amount_cents supports partial (deposit/progress) invoicing: pass it to invoice only part of
    the quote's total, leaving the remainder invoiceable later via further calls. Omitting it
    invoices whatever remains — the whole quote on a first call, preserving the exact original
    line-items-copied-as-is behavior for that (still the overwhelmingly common) case. A partial
    conversion instead generates a single summary line item (real per-line fractions on a formal
    invoice read oddly) with VAT split proportionally, the same ratio-based math
    ledger.py::post_invoice_payment already uses for a partial payment.

    Requires the quote to already be status="accepted" — invoicing for something the
    customer hasn't agreed to yet isn't a real invoice (2026-08-15). Refuses once the quote is
    fully invoiced, or if amount_cents exceeds what's left.
    """
    quote = await get_invoice(tenant_id, quote_id)
    if not quote:
        raise ValueError("quote not found")
    if quote.get("doc_type") not in ("quote", "proforma"):
        raise ValueError("source document is not a quote or proforma")
    if quote.get("status") != "accepted":
        raise ValueError("quote must be marked accepted before it can be converted to an invoice")

    quote_total = int(quote.get("total_cents") or 0)
    already_invoiced = int(quote.get("invoiced_cents") or 0)
    if already_invoiced == 0 and quote.get("converted_invoice_id"):
        # Converted under the pre-partial-invoicing system (before migration 134) — that single
        # conversion was always for the full quote total, so treat it as fully invoiced rather
        # than allowing a second, duplicate full conversion now that invoiced_cents exists.
        already_invoiced = quote_total
    remaining = quote_total - already_invoiced
    if remaining <= 0:
        raise ValueError("this quote has already been fully invoiced")
    if amount_cents is None:
        amount_cents = remaining
    if amount_cents <= 0:
        raise ValueError("amount must be greater than zero")
    if amount_cents > remaining:
        raise ValueError(f"only R{remaining / 100:.2f} remains to be invoiced on this quote")

    is_full_single_shot = already_invoiced == 0 and amount_cents == quote_total
    if is_full_single_shot:
        line_items = quote.get("line_items", [])
        subtotal_cents = quote["subtotal_cents"]
        vat_cents = quote["vat_cents"]
    else:
        quote_vat = int(quote.get("vat_cents") or 0)
        vat_cents = (amount_cents * quote_vat // quote_total) if quote_total else 0
        subtotal_cents = amount_cents - vat_cents
        pct = round(amount_cents / quote_total * 100) if quote_total else 0
        line_items = [{
            "description": f"Progress invoice — {pct}% of quote {quote.get('invoice_number') or ''}".strip(),
            "quantity": 1, "unit": "", "unit_price_cents": subtotal_cents,
            "discount_pct": 0, "total_cents": subtotal_cents,
        }]

    invoice = {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "doc_type": "invoice",
        "invoice_number": await _next_invoice_number(tenant_id, "invoice"),
        "customer_name": quote["customer_name"],
        "customer_email": quote.get("customer_email"),
        "customer_phone": quote.get("customer_phone"),
        "customer_address": quote.get("customer_address"),
        "line_items": line_items,
        "subtotal_cents": subtotal_cents,
        "discount_cents": quote.get("discount_cents", 0) if is_full_single_shot else 0,
        "vat_rate": quote["vat_rate"],
        "vat_cents": vat_cents,
        "total_cents": amount_cents,
        "deposit_cents": quote.get("deposit_cents", 0) if is_full_single_shot else 0,
        "status": "draft",
        "project": quote.get("project"),
        "issue_date": _now()[:10],
        "payment_method": quote.get("payment_method"),
        "source_quote_id": quote_id,
        "notes": quote.get("notes"),
        "created_at": _now(),
        "updated_at": _now(),
    }
    try:
        result = _client().table("commerce_invoices").insert(invoice).execute()
    except Exception as exc:
        # discount_cents/deposit_cents (053) or project (055) columns may not exist yet in
        # this environment — same graceful-degrade retry create_invoice() already uses.
        invoice.pop("discount_cents", None)
        invoice.pop("deposit_cents", None)
        invoice.pop("project", None)
        logger.warning("quote conversion retried without discount/deposit/project (run migrations 053/055?): %s", exc)
        result = _client().table("commerce_invoices").insert(invoice).execute()
    created = result.data[0]

    quote_patch = {"invoiced_cents": already_invoiced + amount_cents, "updated_at": _now()}
    if not quote.get("converted_invoice_id"):
        quote_patch["converted_invoice_id"] = created["id"]
    try:
        _client().table("commerce_invoices").update(quote_patch).eq(
            "tenant_id", tenant_id).eq("id", quote_id).execute()
    except Exception as exc:
        # invoiced_cents column may not exist yet (migration 134) — retry without it so the
        # invoice creation already committed above never gets rolled back by this.
        quote_patch.pop("invoiced_cents", None)
        logger.warning("quote invoiced_cents update skipped (run migration 134?): %s", exc)
        if quote_patch:
            _client().table("commerce_invoices").update(quote_patch).eq(
                "tenant_id", tenant_id).eq("id", quote_id).execute()

    return created


async def record_invoice_payment(tenant_id: str, invoice_id: str, amount_cents: int,
                                  payment_method: Optional[str] = None,
                                  note: Optional[str] = None) -> dict:
    """Record one instalment against an invoice (migration 130). Multiple calls are supported —
    real partial payments across several instalments — status becomes 'part_paid' until the
    running total reaches the invoice's total_cents, then flips to 'paid' automatically (and
    paid_at is stamped, same as a full one-shot payment). Each instalment posts its own ledger
    entry, dated when it was actually received (see ledger.post_invoice_payment) — not one lump
    entry when the balance finally clears."""
    invoice = await get_invoice(tenant_id, invoice_id)
    if not invoice:
        raise ValueError("invoice not found")
    if invoice.get("doc_type") != "invoice":
        raise ValueError("only invoices accept payments, not quotes or credit notes")
    if invoice.get("status") in ("paid", "cancelled"):
        raise ValueError(f"invoice is already {invoice['status']}")
    if amount_cents <= 0:
        raise ValueError("amount must be greater than zero")

    payment = {
        "tenant_id": tenant_id, "invoice_id": invoice_id, "amount_cents": amount_cents,
        "payment_method": payment_method, "note": note, "paid_at": _now(),
    }
    created_payment = _client().table("commerce_invoice_payments").insert(payment).execute().data[0]

    existing = (_client().table("commerce_invoice_payments").select("amount_cents")
                .eq("tenant_id", tenant_id).eq("invoice_id", invoice_id).execute().data or [])
    total_paid = sum(int(p.get("amount_cents") or 0) for p in existing)
    total_due = int(invoice.get("total_cents") or 0)
    new_status = "paid" if total_paid >= total_due else "part_paid"

    # total_paid_cents is kept in sync here so the dashboard can show/compute the real
    # remaining balance from the normal invoice list fetch alone (migration 130).
    patch = {"status": new_status, "total_paid_cents": total_paid, "updated_at": _now()}
    if new_status == "paid":
        patch["paid_at"] = _now()
    updated_result = (_client().table("commerce_invoices").update(patch)
                      .eq("tenant_id", tenant_id).eq("id", invoice_id).execute())
    updated = updated_result.data[0] if updated_result.data else invoice

    try:
        from vula.commerce import ledger
        ledger.post_invoice_payment(tenant_id, invoice, created_payment)
    except Exception as exc:
        logger.warning("ledger hook failed for invoice payment %s: %s", created_payment.get("id"), exc)
    if new_status == "paid":
        await _settle_linked_order(tenant_id, updated)

    return {**updated, "payment": created_payment, "total_paid_cents": total_paid,
            "balance_due_cents": max(0, total_due - total_paid)}


async def list_invoice_payments(tenant_id: str, invoice_id: str) -> List[dict]:
    return (_client().table("commerce_invoice_payments").select("*")
            .eq("tenant_id", tenant_id).eq("invoice_id", invoice_id)
            .order("paid_at").execute().data or [])


async def cancel_invoice(tenant_id: str, invoice_id: str, reason: Optional[str] = None) -> dict:
    """Cancel an invoice that hasn't been paid yet (migration 130). A paid or partially-paid
    invoice already has real ledger entries — reversing those is exactly what a credit note is
    for, so cancel refuses rather than also trying to do that job. No ledger posting happens
    here, same as order cancellation: nothing was ever posted for an invoice that was never
    paid, so there's nothing to reverse."""
    invoice = await get_invoice(tenant_id, invoice_id)
    if not invoice:
        raise ValueError("invoice not found")
    if invoice.get("doc_type") != "invoice":
        raise ValueError("only invoices can be cancelled — quotes use decline/expire instead")
    if invoice.get("status") == "cancelled":
        raise ValueError("this invoice is already cancelled")
    if invoice.get("status") in ("paid", "part_paid"):
        raise ValueError("a paid or partially-paid invoice can't be cancelled — use a credit note instead")

    patch = {"status": "cancelled", "cancel_reason": reason, "cancelled_at": _now(), "updated_at": _now()}
    result = (_client().table("commerce_invoices").update(patch)
              .eq("tenant_id", tenant_id).eq("id", invoice_id).execute())
    return result.data[0] if result.data else {}


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


def _norm_phone(p: Optional[str]) -> str:
    """Normalize a phone number to digits-only E.164-ish (SA: 0xx -> 27xx) — same shape as
    vula.api.commerce's copy (not imported directly: that module already imports this one, so
    importing back would be circular)."""
    if not p:
        return ""
    n = "".join(ch for ch in p if ch.isdigit())
    if n.startswith("0"):
        n = "27" + n[1:]
    return n


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


async def match_invoice_item(tenant_id: str, description: str) -> Optional[dict]:
    """Tiered catalog-item auto-detection for `commerce_invoice_items`, tenant-scoped — same
    exact/fuzzy cascade as match_supplier, adapted to item names instead of supplier names
    (catalog-from-scan, migration 083's per-tenant invoice-line-item quick-pick list).

    Returns ``{"item", "tier", "confidence", "auto_apply"}`` or ``None``.
    """
    nn = _norm_name(description)
    if not nn:
        return None
    items = (_client().table("commerce_invoice_items").select("*")
             .eq("tenant_id", tenant_id).eq("active", True).execute().data or [])
    if not items:
        return None

    # Tier 1 — exact normalized name
    for it in items:
        if _norm_name(it.get("name", "")) == nn:
            return {"item": it, "tier": "exact_name", "confidence": 1.0, "auto_apply": True}

    # Tier 2 — fuzzy name
    best, best_score = None, 0.0
    for it in items:
        score = difflib.SequenceMatcher(None, nn, _norm_name(it.get("name", ""))).ratio()
        if score > best_score:
            best, best_score = it, score
    if best and best_score >= FUZZY_MIN:
        return {
            "item": best, "tier": "fuzzy_name",
            "confidence": round(best_score, 3), "auto_apply": best_score >= FUZZY_AUTO,
        }
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


def upsert_project_boq(tenant_id: str, project: str, total_cents: int,
                       title: Optional[str] = None, source_job: Optional[str] = None,
                       sections: Optional[list] = None) -> None:
    """Persist a project's BoQ/contract value — same upsert shape as the manual dashboard
    endpoint (vula/api/projects.py's set_project_boq), reused here so a real filed BoQ document
    can populate it automatically instead of only via manual dashboard entry. 2026-08-12: a real
    BoQ document was confirmed to get a real total_cents extracted and committed as a quote, but
    nothing ever bridged that total into vula_project_boq — the project's "contract value" stayed
    at 0 regardless of a real, substantial BoQ being on file.

    `sections` (migration 129) is [{"section": "Demolition", "budget_cents": ...}, ...] — the
    BoQ's real trade-section breakdown, so site expenses can eventually be compared against a
    section's own budget, not just the whole project's lump total. Omitted (None) on purpose
    when not explicitly given: PostgREST upsert only touches columns present in the payload, so
    leaving `sections` out here preserves whatever was already set rather than resetting it —
    the auto-bridge from a scanned BoQ (no reliable per-section signal in that extraction) must
    never clobber a real breakdown entered manually."""
    row: Dict[str, Any] = {
        "tenant_id": tenant_id, "project": project, "title": title,
        "total_cents": int(total_cents or 0), "source_job": source_job, "updated_at": _now(),
    }
    if sections is not None:
        row["sections"] = sections
    try:
        _client().table("vula_project_boq").upsert(row, on_conflict="tenant_id,project").execute()
    except Exception as exc:
        logger.debug("upsert_project_boq skipped (run migration 056/129?): %s", exc)


async def commit_inbound_document(
    tenant_id: str, extracted: dict, *, auto_commit: bool = True, source: str = "scanner",
    filed_document_id: Optional[str] = None, project: Optional[str] = None,
    is_boq: bool = False,
) -> dict:
    """Commit an extracted inbound document (invoice/quote/delivery_note/receipt) into the
    books: supplier match (or auto-create for a genuinely new supplier), due-date calc,
    commerce_invoices/commerce_expenses insert, KB ingest. The single commit path shared by
    the Smart Scanner (admin_scan_commit, migration 009-era) and, from migration 102 onward,
    the email/WhatsApp/dashboard-upload document pipelines — one path for every intake channel.

    `project` (which job/site this bill is for) and supplier (who sent it, resolved internally
    below) are orthogonal and both get set on the committed row when known — the Smart Scanner
    never had a project concept, so this is optional and None by default.

    Supplier resolution (store-admin-reconciliation follow-on plan):
    - Tier 1/2/3-at-or-above-auto-apply match → applied directly, no human involved.
    - No match at all, but a usable supplier name on a real B2B document (invoice/quote/
      delivery_note, not a petty-cash receipt — a random till slip is rarely a repeat supplier
      and would just pollute the directory) → a brand-new supplier has nothing to disambiguate
      against, so it's created SILENTLY (no WhatsApp notification — visible anytime in the
      Suppliers tab).
    - A weaker match (fuzzy below the auto-apply threshold, or a layout-signature-only match)
      → genuine ambiguity: `needs_review=True` is set (and, when `filed_document_id` is given,
      written to that row) rather than guessing or creating a possible duplicate supplier. This
      is the one case that should route to human approval (see vula/commerce/approvals.py).
    """
    from uuid import uuid4
    from datetime import date, timedelta

    db = _client()
    today = date.today()

    supplier_name = (extracted.get("supplier") or "").strip()
    tax_id = (extracted.get("tax_id") or "").strip()
    layout_signature = compute_layout_signature(extracted)
    total_cents = int(extracted.get("total_cents") or 0)

    doc_type = extracted.get("doc_type", "receipt")
    # "quote" added here (migration 102's doc_type CHECK already allows it) — the Smart
    # Scanner never produced this doc_type, but the email/WhatsApp document pipeline's
    # "Quote / Estimate" category needs a real commerce_invoices home too.
    is_invoice = doc_type in ("invoice", "delivery_note", "quote")

    payment_terms_days = 30
    supplier_row = None
    needs_review = False
    ask_direction_of = None      # set when we can't tell whose document this is

    supplier_match = await match_supplier(
        tenant_id, name=supplier_name or None, tax_id=tax_id or None, layout_signature=layout_signature,
    )
    if supplier_match and supplier_match["auto_apply"]:
        supplier_row = supplier_match["supplier"]
        payment_terms_days = supplier_row.get("payment_terms_days", 30)
    elif supplier_match:
        needs_review = True
    elif supplier_name and total_cents > 0 and is_invoice:
        supplier_row = await upsert_supplier(tenant_id, {
            "name": supplier_name, "tax_id": tax_id or None,
            "contact_email": extracted.get("supplier_email") or extracted.get("contact_email"),
            "contact_phone": extracted.get("supplier_phone") or extracted.get("contact_phone"),
            "layout_signature": layout_signature,
        })
        supplier_match = {"supplier": supplier_row, "tier": "auto_created",
                          "confidence": 1.0, "auto_apply": True}
        payment_terms_days = supplier_row.get("payment_terms_days", 30)

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
    vat_cents = int(extracted.get("vat_cents") or 0)

    preview = {
        "supplier": supplier_name,
        "supplier_known": supplier_row is not None,
        "supplier_match": (
            {
                "tier": supplier_match["tier"], "confidence": supplier_match["confidence"],
                "auto_applied": supplier_match["auto_apply"],
                "supplier_id": supplier_match["supplier"].get("id"),
                "supplier_name": supplier_match["supplier"].get("name"),
            } if supplier_match else None
        ),
        "needs_review": needs_review,
        "payment_terms_days": payment_terms_days,
        "doc_date": str(doc_date), "due_date": str(due_date) if due_date else None,
        "days_until_due": days_until_due, "total_cents": total_cents, "doc_type": doc_type,
        "record_type": "invoice" if is_invoice else "expense",
    }

    if not auto_commit:
        return {"ok": True, "preview": preview, "committed": False}

    # 2026-08-08 fix — a real invoice/quote/delivery note always has a total; total_cents
    # defaulting to 0 here means extraction failed to find one, not that the document is
    # genuinely worth nothing. Confirmed live: 4 junk R0.00 "draft" quotes (OFF-QTE-00010/11/
    # 12/14) were created this way over several weeks, unnoticed — this path had no equivalent
    # to the price-completeness gate added the same day to commerce_admin.py's _create_invoice
    # (a different tool, doesn't cover this document-intake path at all). The document itself
    # is still filed/in the KB regardless — this only skips the phantom commerce_invoices row.
    # No interactive "ask" here: unlike the WhatsApp tool-calling path, not every intake channel
    # (email attachment, dashboard Smart Scanner) has someone to ask in the moment.
    if is_invoice and total_cents <= 0:
        return {"ok": True, "preview": preview, "committed": False,
                "reason": "no total found on this document — not booked as a draft"}

    record_id = str(uuid4())
    supplier_id = supplier_row.get("id") if supplier_row else None

    if is_invoice:
        # customer_name is NOT NULL on commerce_invoices, designed for the outbound case
        # (who WE are billing) — for an inbound bill there's no real "customer", so the
        # tenant's own name goes there instead (semantically: who this bill is addressed to).
        try:
            from vula.api.tenants import get_config as _get_tenant_config
            tenant_name = (_get_tenant_config(tenant_id) or {}).get("display_name") or tenant_id
        except Exception:
            tenant_name = tenant_id
        # Who issued this? A document the tenant wrote is THEIR invoice (money in), not a
        # supplier bill (money out) — see classify_direction for the real 51-invoice,
        # R32,307.97 misclassification this corrects. When the issuer is an unrecognised party
        # the direction is a genuine coin-flip (new supplier vs an imported client invoice), so
        # it is flagged for review rather than committed as a guess.
        direction, dir_confident, dir_reason = classify_direction(
            supplier_name, tenant_name, tenant_id, supplier_known=bool(supplier_row))
        if not dir_confident:
            needs_review = True
            logger.info("direction unconfident for %s (%s): filed as %s pending review",
                        tenant_id, dir_reason, direction)
            # needs_review alone is NOT enough: the only other consumer of that flag requires a
            # supplier match, which this case by definition doesn't have, so the flag would sit
            # in a column nobody reads. Ask the owner directly instead (supplier bill / our
            # invoice / an expense). Best-effort — if there's nobody to ask, the row stays filed
            # and flagged rather than lost.
            ask_direction_of = record_id
        row = {
            "id": record_id, "tenant_id": tenant_id, "direction": direction, "doc_type": doc_type,
            # A supplier's document must not draw from the tenant's OWN invoice sequence —
            # see _next_invoice_number for the real DIGG numbering damage this caused.
            "invoice_number": await _next_invoice_number(tenant_id, doc_type,
                                                         direction=direction),
            # customer_name is NOT NULL and designed for the outbound case (who WE are billing).
            # For an inbound bill there is no real customer, so the tenant's own name goes there
            # (semantically: who the bill is addressed to). For an outbound one we don't know the
            # customer from the scan alone, so it stays the tenant name until someone edits it.
            "customer_name": tenant_name,
            "status": "draft", "supplier": supplier_name, "supplier_id": supplier_id,
            "project": project,
            "issue_date": str(doc_date), "due_date": str(due_date) if due_date else None,
            "payment_terms_days": payment_terms_days,
            "subtotal_cents": total_cents - vat_cents, "vat_rate": 15.0, "vat_cents": vat_cents,
            "total_cents": total_cents, "discount_cents": 0, "deposit_cents": 0,
            "line_items": _coerce_line_items(extracted.get("line_items")),
            "notes": extracted.get("notes"), "source": source,
            "scan_confidence": extracted.get("confidence"),
        }
        result = db.table("commerce_invoices").insert(row).execute()
        committed_record = result.data[0] if result.data else row
        # Bridge a real BoQ's total into the project's tracked contract value — confirmed live
        # 2026-08-12: a real, substantial BoQ (R240,553.53) got committed here as a quote but
        # never once reached vula_project_boq, leaving the project's "contract value" at 0
        # regardless. Only fires when the project is already confidently known at commit time;
        # doc_filing.resolve_pending_document does the same bridge for the (common) case where
        # the project is only resolved later via the "which project?" WhatsApp answer.
        if is_boq and project:
            upsert_project_boq(tenant_id, project, total_cents)
    else:
        # Reimbursable inference (2026-08-08 fix) — this insert used to omit the key entirely,
        # silently defaulting to the column's `false` regardless of what the document itself
        # said about who paid. create_claim() (expenses.py:223-228) already does this correctly
        # via resolve_paid_with(card_last4/payment_method); this path just never called it.
        # Confirmed live: a card-paid hardware invoice landed with reimbursable=false. No
        # submitter phone is available at this layer (unlike _log_expense_claim's role-based
        # inference), so this only resolves the card-vs-not signal, not who specifically to
        # reimburse — paid_by_name falls back to the resolved payee/counterparty on the doc.
        from vula.commerce.expenses import resolve_paid_with
        paid_with = resolve_paid_with(
            tenant_id, card_last4=extracted.get("card_last4"),
            payment_method=extracted.get("payment_method"))
        reimbursable = paid_with == "personal"
        paid_by_name = None
        try:
            from vula.commerce.party import resolve_party_name
            paid_by_name = resolve_party_name(extracted, exclude=("payer",))
        except Exception:
            pass
        row = {
            "id": record_id, "tenant_id": tenant_id, "date": str(doc_date),
            "due_date": str(due_date) if due_date else None,
            "category": extracted.get("category") or "supplies",
            "description": f"{supplier_name or 'Unknown'} — {doc_type}",
            "amount_cents": total_cents, "supplier": supplier_name, "supplier_id": supplier_id,
            "project": project,
            "payment_terms_days": payment_terms_days, "status": "pending", "source": source,
            "doc_type": doc_type, "line_items": _coerce_line_items(extracted.get("line_items")),
            "scan_confidence": extracted.get("confidence"),
            "paid_with": paid_with, "reimbursable": reimbursable, "paid_by_name": paid_by_name,
        }
        result = db.table("commerce_expenses").insert(row).execute()
        committed_record = result.data[0] if result.data else row

    # Catalog-from-scan — auto-populate the tenant's reusable invoice-item quick-pick list
    # (commerce_invoice_items, migration 083) from this document's line items, so a future
    # invoice/quote can pick them instead of retyping. Matches against the existing catalog first
    # (same fuzzy cascade as supplier matching) so re-scanning a near-identical document never
    # creates duplicates — and the upsert's on_conflict=tenant_id,name is a second safety net even
    # if the fuzzy match missed. Best-effort: never blocks the document commit above.
    catalog_items_added = 0
    try:
        for li in extracted.get("line_items", []) or []:
            desc = (li.get("description") or "").strip()
            if not desc:
                continue
            item_match = await match_invoice_item(tenant_id, desc)
            if item_match and item_match["auto_apply"]:
                continue  # already in the catalog
            db.table("commerce_invoice_items").upsert({
                "tenant_id": tenant_id, "kind": "product", "name": desc[:200],
                "unit": (li.get("unit") or "").strip() or None,
                "unit_price_cents": int(li.get("unit_price_cents") or 0), "active": True,
            }, on_conflict="tenant_id,name").execute()
            catalog_items_added += 1
    except Exception as cat_exc:
        logger.warning("Catalog-from-scan failed for %s: %s", record_id, cat_exc)

    if supplier_row and layout_signature and not supplier_row.get("layout_signature"):
        try:
            await learn_supplier_signature(tenant_id, supplier_row.get("id"), layout_signature)
        except Exception as sig_exc:
            logger.warning("Failed to learn supplier signature for %s: %s", record_id, sig_exc)

    if filed_document_id:
        try:
            db.table("vula_filed_documents").update({
                "commerce_invoice_id": record_id if is_invoice else None,
                "supplier_id": supplier_id,
                "match_confidence": supplier_match.get("confidence") if supplier_match else None,
                "supplier_match_tier": supplier_match.get("tier") if supplier_match else "none",
                "needs_review": needs_review,
            }).eq("id", filed_document_id).execute()
        except Exception as exc:
            logger.warning("Failed to bridge filed_document %s to commit result: %s", filed_document_id, exc)

    # We couldn't tell whose document this is (see classify_direction). Ask the owner on
    # WhatsApp — supplier bill / our invoice / an expense — because filing it silently is what
    # put 51 of off-the-hook's own sales invoices on the wrong side of the books, and a
    # needs_review flag on its own reaches nobody in this case.
    if ask_direction_of:
        try:
            from vula.api.whatsapp import ask_document_kind
            await ask_document_kind(tenant_id, ask_direction_of, supplier_name,
                                    total_cents, doc_type)
        except Exception as exc:
            logger.debug("document-kind ask skipped: %s", exc)

    # Genuine ambiguity (Tier 3 fuzzy-below-auto-apply or Tier 4 layout-only) on a real
    # supplier bill — route the candidate match to the tenant's own admin team for a yes/no,
    # reusing the existing WhatsApp APPROVE/REJECT approval engine (vula/commerce/approvals.py)
    # rather than silently guessing or leaving it unresolved with no path to close it out.
    if needs_review and is_invoice and supplier_match:
        try:
            from vula.commerce.approvals import create_approval, tenant_admin_approvers
            approvers = await tenant_admin_approvers(tenant_id)
            if approvers:
                candidate = supplier_match.get("supplier") or {}
                label = (f"Supplier match: is *{candidate.get('name', 'this supplier')}* who sent "
                         f"{doc_type} for R{total_cents/100:,.2f}?")
                await create_approval(
                    tenant_id=tenant_id, entity_type="inbound_invoice", entity_id=record_id,
                    title=label, approvers=approvers,
                    meta={
                        "filed_document_id": filed_document_id,
                        "candidate_supplier_id": candidate.get("id"),
                        "candidate_supplier_name": candidate.get("name"),
                        "match_tier": supplier_match.get("tier"),
                        "confidence": supplier_match.get("confidence"),
                    },
                )
        except Exception as exc:
            logger.warning("Failed to create supplier-match approval for %s: %s", record_id, exc)

    kb_chunks = 0
    try:
        from vula.ingestion.pipeline import VulaIngestionPipeline
        pipeline = VulaIngestionPipeline(tenant_id=tenant_id)
        lines = [f"Document type: {doc_type}", f"Supplier: {supplier_name}", f"Date: {doc_date}"]
        if due_date:
            lines.append(f"Due date: {due_date} ({payment_terms_days} day terms)")
        lines.append(f"Total: R{total_cents/100:.2f} (incl VAT R{vat_cents/100:.2f})")
        if extracted.get("line_items"):
            lines.append("Line items:")
            for item in extracted["line_items"][:20]:
                lines.append(f"  - {item.get('description','')} {item.get('quantity','')} "
                            f"{item.get('unit','')} @ R{(item.get('unit_price_cents',0) or 0)/100:.2f}")
        if extracted.get("notes"):
            lines.append(f"Notes: {extracted['notes']}")
        doc_text = "\n".join(lines)
        ingest_result = await pipeline.ingest_text(
            content=doc_text, filename=f"{doc_type}_{supplier_name.replace(' ','_')}_{doc_date}.txt",
        )
        kb_chunks = getattr(ingest_result, "chunks_stored", 0)
    except Exception as kb_exc:
        logger.warning("KB ingest failed for scan commit %s: %s", record_id, kb_exc)

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
        "ok": True, "committed": True, "record_type": "invoice" if is_invoice else "expense",
        "record_id": record_id, "record": committed_record,
        "supplier_match": preview["supplier_match"], "needs_review": needs_review,
        "preview": preview, "kb_chunks_added": kb_chunks, "catalog_items_added": catalog_items_added,
        "message": msg,
    }


# ── Invoice settings (onboarding + look-and-feel) ─────────────────────────────

# Fields a tenant may set via the onboarding wizard / settings panel.
_INVOICE_SETTINGS_FIELDS = (
    "company_name", "trading_as", "logo_url", "company_email", "company_phone", "company_reg",
    "vat_number", "registered_address", "vat_registered", "prices_include_vat",
    "account_name", "bank_name", "branch_code", "account_number",
    "template_choice", "accent_color", "onboarded", "menu_header_image_url",
    "ink_color", "font_pairing",
    "footer_text", "show_vat_breakdown", "show_company_reg", "logo_size", "logo_align",
    "header_sticky", "header_nav_position", "header_cta_text", "header_cta_link",
    "signature_url", "signature_name",
)
_TEMPLATE_CHOICES = ("classic", "minimal", "modern", "branded", "digg")


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


_INVOICE_SETTINGS_078_FIELDS = ("ink_color", "font_pairing")  # only exist once migration 078 runs
_INVOICE_SETTINGS_103_FIELDS = (  # only exist once migration 103 runs
    "footer_text", "show_vat_breakdown", "show_company_reg", "logo_size", "logo_align",
)
_INVOICE_SETTINGS_128_FIELDS = (  # only exist once migration 128 runs
    "header_sticky", "header_nav_position", "header_cta_text", "header_cta_link",
)
_INVOICE_SETTINGS_165_FIELDS = ("signature_url", "signature_name")  # only exist once migration 165 runs
_INVOICE_SETTINGS_OPTIONAL_FIELDS = (
    _INVOICE_SETTINGS_078_FIELDS + _INVOICE_SETTINGS_103_FIELDS + _INVOICE_SETTINGS_128_FIELDS
    + _INVOICE_SETTINGS_165_FIELDS
)


async def upsert_invoice_settings(tenant_id: str, data: dict) -> dict:
    """Create or update the tenant's invoice settings (one row per tenant).

    Only whitelisted fields are persisted. ``template_choice`` is validated
    against the known templates. Every write is tenant-scoped.

    Fields added by a migration that hasn't run yet in this environment would make the WHOLE
    write fail with an unknown-column error — degrade gracefully instead: drop every optional
    (migration-gated) field and retry once, so the rest of the settings still save.
    """
    patch = {k: data[k] for k in _INVOICE_SETTINGS_FIELDS if k in data}
    choice = patch.get("template_choice")
    if choice is not None and choice not in _TEMPLATE_CHOICES:
        raise ValueError(f"template_choice must be one of {_TEMPLATE_CHOICES}")

    db = _client()
    existing = await get_invoice_settings(tenant_id)
    try:
        if existing:
            write_patch = {**patch, "updated_at": _now()}
            result = (
                db.table("commerce_invoice_settings")
                .update(write_patch)
                .eq("tenant_id", tenant_id)
                .execute()
            )
            return result.data[0] if result.data else {**existing, **write_patch}

        row = {"id": str(uuid.uuid4()), "tenant_id": tenant_id, **patch}
        result = db.table("commerce_invoice_settings").insert(row).execute()
        return result.data[0] if result.data else row
    except Exception as exc:
        if not any(f in patch for f in _INVOICE_SETTINGS_OPTIONAL_FIELDS):
            raise
        logger.warning("invoice-settings write failed with migration-gated fields present (run migrations 078/103?): %s", exc)
        patch = {k: v for k, v in patch.items() if k not in _INVOICE_SETTINGS_OPTIONAL_FIELDS}
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
    """Create or update a saved invoice client. Dedups on phone when no ``id`` is given — without
    this, typing the same customer's details on two different invoices (the common case: nobody
    remembers to explicitly pick them from the dropdown every time) silently created a second,
    disconnected record for the same person."""
    patch = {k: data[k] for k in _CLIENT_FIELDS if k in data}
    if not patch.get("name"):
        raise ValueError("name is required")
    db = _client()
    client_id = data.get("id")
    if not client_id:
        np = _norm_phone(patch.get("phone"))
        if np:
            existing = (db.table("commerce_invoice_clients").select("id,phone")
                        .eq("tenant_id", tenant_id).execute().data or [])
            match = next((e for e in existing if _norm_phone(e.get("phone")) == np), None)
            if match:
                client_id = match["id"]
    if client_id:
        patch["updated_at"] = _now()
        res = (db.table("commerce_invoice_clients").update(patch)
               .eq("id", client_id).eq("tenant_id", tenant_id).execute())
        return res.data[0] if res.data else {**patch, "id": client_id}
    row = {"id": str(uuid.uuid4()), "tenant_id": tenant_id, **patch}
    res = db.table("commerce_invoice_clients").insert(row).execute()
    return res.data[0] if res.data else row


async def delete_invoice_client(tenant_id: str, client_id: str) -> None:
    _client().table("commerce_invoice_clients").delete() \
        .eq("id", client_id).eq("tenant_id", tenant_id).execute()


# ── Recurring invoices ────────────────────────────────────────────────────────

_RECURRING_FIELDS = ("label", "customer_name", "customer_email", "customer_phone",
                     "customer_address", "line_items", "vat_rate", "cadence",
                     "next_run_at", "active")


def _advance(date_iso: str, cadence: str) -> str:
    from datetime import date as _d
    d = _d.fromisoformat(date_iso[:10])
    if cadence == "weekly":
        return (d + __import__("datetime").timedelta(days=7)).isoformat()
    # monthly — same day next month, clamped to month length
    import calendar
    y, m = (d.year + (d.month // 12)), ((d.month % 12) + 1)
    day = min(d.day, calendar.monthrange(y, m)[1])
    return _d(y, m, day).isoformat()


async def list_recurring(tenant_id: str) -> List[dict]:
    return (_client().table("commerce_recurring_invoices").select("*")
            .eq("tenant_id", tenant_id).order("next_run_at").execute().data or [])


async def upsert_recurring(tenant_id: str, data: dict) -> dict:
    patch = {k: data[k] for k in _RECURRING_FIELDS if k in data}
    if not patch.get("customer_name"):
        raise ValueError("customer_name is required")
    if not patch.get("next_run_at"):
        patch["next_run_at"] = _now()[:10]
    db = _client()
    if data.get("id"):
        patch["updated_at"] = _now()
        res = (db.table("commerce_recurring_invoices").update(patch)
               .eq("id", data["id"]).eq("tenant_id", tenant_id).execute())
        return res.data[0] if res.data else {**patch, "id": data["id"]}
    row = {"id": str(uuid.uuid4()), "tenant_id": tenant_id, **patch}
    res = db.table("commerce_recurring_invoices").insert(row).execute()
    return res.data[0] if res.data else row


async def delete_recurring(tenant_id: str, rec_id: str) -> None:
    _client().table("commerce_recurring_invoices").delete() \
        .eq("id", rec_id).eq("tenant_id", tenant_id).execute()


async def process_due_recurring() -> int:
    """Generate invoices for every active recurring template whose next_run_at has passed.
    Idempotent-ish: advances next_run_at after each generation. Returns count generated."""
    today = _now()[:10]
    try:
        due = (_client().table("commerce_recurring_invoices").select("*")
               .eq("active", True).lte("next_run_at", today).limit(200).execute().data or [])
    except Exception:
        return 0
    n = 0
    for r in due:
        try:
            inv = await create_invoice(r["tenant_id"], {
                "doc_type": "invoice",
                "customer_name": r.get("customer_name"), "customer_email": r.get("customer_email"),
                "customer_phone": r.get("customer_phone"), "customer_address": r.get("customer_address"),
                "line_items": r.get("line_items") or [], "vat_rate": float(r.get("vat_rate") or 15),
                "status": "draft",
                "issue_date": today,
                "notes": (r.get("label") and f"Recurring: {r['label']}") or None,
            })
            _client().table("commerce_recurring_invoices").update({
                "next_run_at": _advance(r["next_run_at"], r.get("cadence") or "monthly"),
                "last_invoice_id": inv.get("id"), "last_run_at": _now(), "updated_at": _now(),
            }).eq("id", r["id"]).execute()
            n += 1
        except Exception:
            continue
    return n


# ── Credit notes ──────────────────────────────────────────────────────────────

async def create_credit_note(tenant_id: str, invoice_id: str, line_items: Optional[List[dict]] = None) -> dict:
    """Create a credit note against an invoice (reuses the invoice engine).
    Defaults to crediting the full invoice; pass line_items for a partial credit."""
    src = await get_invoice(tenant_id, invoice_id)
    if not src:
        raise ValueError("Invoice not found")
    items = line_items or src.get("line_items") or []
    if isinstance(items, str):
        import json as _json
        try: items = _json.loads(items)
        except Exception: items = []
    cn = await create_invoice(tenant_id, {
        "doc_type": "credit_note",
        "customer_name": src.get("customer_name"), "customer_email": src.get("customer_email"),
        "customer_phone": src.get("customer_phone"), "customer_address": src.get("customer_address"),
        "line_items": items, "vat_rate": float(src.get("vat_rate") or 15),
        "status": "sent", "issue_date": _now()[:10],
        "notes": f"Credit note against invoice {src.get('invoice_number')}",
    })
    try:
        _client().table("commerce_invoices").update({"credited_invoice_id": invoice_id}) \
            .eq("id", cn["id"]).eq("tenant_id", tenant_id).execute()
        cn["credited_invoice_id"] = invoice_id
    except Exception:
        pass
    return cn


# ── Document lookup (shared by commerce_admin.py and email_admin.py's find_document tools) ─────

def _document_amount(fields: Dict[str, Any]) -> Optional[float]:
    """Best-effort extracted amount in Rands from a filed document's `fields`, checking every
    schema real extraction paths actually write. 2026-09-21 incident: the money-document
    pipeline (vula/api/whatsapp.py's three-tier extraction, see CLAUDE.md) writes `total_cents`
    for Invoice/Quote/BOQ (the single most common real case) — this only ever checked `amount`/
    `total`/`amount_rands`, the Smart Scanner's plain-Rand schema, so a real invoice's amount
    always came back None even though it was sitting right in `fields`.

    Same-day follow-up, found while building the consistency check below: Proof of Payment
    documents (both the deterministic FNB parser in vula/ingestion/payment_notice.py AND the
    LLM extraction's own declared schema for that category) write `amount_cents`, a THIRD money
    key — confirmed against real production data across multiple tenants, every single Proof of
    Payment row uses it, none use total_cents/amount/total. Checked here too, same conversion.

    tests/test_document_field_schema_consistency.py regex-scans the actual extraction sources
    for every money-shaped field name they declare and asserts this function checks all of them
    — so a future prompt/schema change that introduces a new one fails CI immediately instead of
    silently returning None in production until someone notices via a live transcript, twice."""
    amount = fields.get("amount") or fields.get("total") or fields.get("amount_rands")
    if amount is None:
        cents = fields.get("total_cents")
        if cents is None:
            cents = fields.get("amount_cents")
        if cents is not None:
            try:
                amount = round(cents / 100, 2)
            except (TypeError, ValueError):
                amount = None
    return amount


# find_filed_document: how many rows one SQL search pulls (enough for a real "all invoices from
# X" request — DIGG had 13 from one supplier in 3 weeks) vs how many are handed back to the model
# row-by-row. The count/total always cover every fetched row, so a long list is summarised
# server-side rather than silently truncated to whatever fit.
_FILED_SEARCH_FETCH_CAP = 200
_FILED_SEARCH_RETURN_CAP = 30

# The JSONB `fields` keys a filed document's counterparty name lives under (see
# _SUPPLIER_CHECK_FIELD in vula/api/whatsapp.py, and the `party` read below).
_PARTY_FIELD_KEYS = ("supplier", "payee_name", "customer")


def _document_amount_cents(fields: Dict[str, Any]) -> Optional[int]:
    """_document_amount() as integer cents, so totals are summed server-side in cents (never by
    the LLM, never as floats). Parses the Smart Scanner's string shape ("R7,500.00") too.
    None when there's no usable amount."""
    amount = _document_amount(fields)
    if amount is None or isinstance(amount, bool):
        return None
    if isinstance(amount, (int, float)):
        return int(round(amount * 100))
    cleaned = re.sub(r"[^0-9.\-]", "", str(amount).replace(",", ""))
    try:
        return int(round(float(cleaned) * 100)) if cleaned else None
    except ValueError:
        return None


# 2026-09-23, real DIGG data: "POS Account Refund 21-366230.pdf" was filed as category Invoice
# with a POSITIVE total_cents (extraction has no refund/credit field), so a plain sum counted a
# R954 refund as R954 more spend. Refunds/credit notes are detected by name and subtracted.
_REFUND_DOC_RE = re.compile(r"\b(refund|credit[\s-]*note)s?\b", re.IGNORECASE)


def _is_refund_row(row: Dict[str, Any]) -> bool:
    return bool(_REFUND_DOC_RE.search(f"{row.get('filename') or ''} {row.get('summary') or ''}"))


def _party_of(fields: Dict[str, Any]) -> Optional[str]:
    return fields.get("supplier") or fields.get("payee_name") or fields.get("customer")


def _pg_term(text: str) -> str:
    """Free text made safe to embed in a PostgREST or_() filter — commas/parens are its syntax."""
    return re.sub(r"[,()]", " ", text or "").strip()[:100]


async def _resolve_supplier_names(tenant_id: str, query: str) -> List[str]:
    """Canonical name + aliases of the known supplier (commerce_suppliers) the query refers to,
    or []. Matches when the whole query is that supplier's name/alias (exact, or fuzzy at
    match_supplier's auto-apply bar), or when a name/alias appears as whole words inside it
    ("all jack hammer invoices" -> alias "Jack Hammer" -> GARDENS HANDIMAN CENTRE).
    Fail-open: any error returns []."""
    nq = _norm_name(query)
    if not nq:
        return []
    try:
        suppliers = await list_suppliers(tenant_id)
    except Exception as exc:
        logger.debug("find_filed_document supplier alias resolution skipped: %s", exc)
        return []
    padded = f" {nq} "
    best, best_score = None, 0.0
    for s in suppliers:
        names = [n for n in [s.get("name")] + list(s.get("aliases") or []) if n]
        norms = [_norm_name(n) for n in names]
        if any(n and (n == nq or (len(n) >= 3 and f" {n} " in padded)) for n in norms):
            return names
        score = max((difflib.SequenceMatcher(None, nq, n).ratio() for n in norms if n), default=0.0)
        if score > best_score:
            best, best_score = names, score
    return best if best and best_score >= FUZZY_AUTO else []


# "Jack Hammer is an alias for the Gardens account" — an owner teaching the system a supplier
# nickname. 2026-09-23 (digg-demo): "Can you make a note the jack hammer is a alias to the
# gardens account" matched clickup_admin's "make a note", failed with a ClickUp 404 and never
# reached commerce_suppliers.aliases — the one place find_filed_document already resolves
# nicknames from (see _resolve_supplier_names).
_ALIAS_STATEMENT_RE = re.compile(
    r"^(?:.*?\b(?:make\s+a\s+note|note|remember|save|record)\s+(?:that\s+)?)?"
    r"(?:the\s+)?(?P<alias>[\w'&.\-]+(?:\s+[\w'&.\-]+){0,3}?)\s+(?:is|=)\s+"
    r"(?:an?\s+|the\s+|our\s+)?(?:alias|nickname|another\s+name|short\s+name|short|"
    r"same(?:\s+thing)?\s+as|name)\s*(?:for|to|of|as)?\s+"
    r"(?:the\s+|our\s+)?(?P<target>[\w'&.\-]+(?:\s+[\w'&.\-]+){0,5}?)"
    r"(?:\s+(?:account|supplier|store|shop))?\s*[.!]*$",
    re.IGNORECASE)


def parse_alias_statement(text: str) -> Optional[Tuple[str, str]]:
    """(alias, target) if a sentence of `text` states that one supplier name is an alias for
    another, else None."""
    for sentence in re.split(r"(?<=[.!?])\s+|\.{2,}\s*", text or ""):
        m = _ALIAS_STATEMENT_RE.match(sentence.strip())
        if m:
            alias, target = m.group("alias").strip(" .'"), m.group("target").strip(" .'")
            if _norm_name(alias) and _norm_name(target) and _norm_name(alias) != _norm_name(target):
                return alias, target
    return None


async def learn_supplier_alias(tenant_id: str, alias: str, target: str) -> Dict[str, Any]:
    """Add `alias` to the tenant's supplier matching `target` (commerce_suppliers.aliases), then
    read it back. Target matching: the target's words appear in the supplier's name (or an
    existing alias), or a fuzzy match at FUZZY_MIN. Rows that normalise to the same name are
    one supplier (filing often creates "GARDENS HANDIMAN CENTRE" and "Gardens Handiman Centre")
    and all get the alias. Several different suppliers → "ambiguous" with candidates, never a
    guess. Returns {"status": added|exists|ambiguous|not_found|error, ...}."""
    nt = _norm_name(target)
    try:
        suppliers = await list_suppliers(tenant_id)
    except Exception as exc:
        logger.warning("learn_supplier_alias: supplier lookup failed: %s", exc)
        return {"status": "error"}
    groups: Dict[str, List[dict]] = {}
    for sp in suppliers:
        names = [sp.get("name") or ""] + list(sp.get("aliases") or [])
        norms = [_norm_name(n) for n in names if n]
        hit = any(f" {nt} " in f" {n} " for n in norms) or max(
            (difflib.SequenceMatcher(None, nt, n).ratio() for n in norms), default=0.0) >= FUZZY_MIN
        if hit:
            groups.setdefault(_norm_name(sp.get("name") or ""), []).append(sp)
    if not groups:
        return {"status": "not_found", "target": target}
    if len(groups) > 1:
        return {"status": "ambiguous", "target": target,
                "candidates": [rows[0].get("name") for rows in groups.values()][:5]}
    rows = next(iter(groups.values()))
    name = rows[0].get("name")
    if all(_norm_name(alias) in {_norm_name(a) for a in (r.get("aliases") or [])} for r in rows):
        return {"status": "exists", "supplier": name, "alias": alias}
    try:
        for r in rows:
            current = list(r.get("aliases") or [])
            if _norm_name(alias) not in {_norm_name(a) for a in current}:
                (_client().table("commerce_suppliers").update({"aliases": current + [alias]})
                 .eq("tenant_id", tenant_id).eq("id", r["id"]).execute())
        back = (_client().table("commerce_suppliers").select("id,aliases")
                .eq("tenant_id", tenant_id).in_("id", [r["id"] for r in rows]).execute().data or [])
    except Exception as exc:
        logger.warning("learn_supplier_alias: update failed: %s", exc)
        return {"status": "error"}
    ok = bool(back) and all(
        _norm_name(alias) in {_norm_name(a) for a in (b.get("aliases") or [])} for b in back)
    return {"status": "added" if ok else "error", "supplier": name, "alias": alias}


def _filed_rows_query(tenant_id: str, terms: List[str], category: Optional[str], party_only: bool):
    """vula_filed_documents rows whose filename/summary — or counterparty name in `fields` —
    contains any of `terms` (already _pg_term-sanitised). party_only restricts the match to the
    counterparty fields (used once a supplier has been resolved, so its name appearing in an
    unrelated document's summary doesn't pull that document in)."""
    q = (_client().table("vula_filed_documents")
         .select("id,filename,category,summary,fields,status,created_at,customer_phone")
         .eq("tenant_id", tenant_id).order("created_at", desc=True))
    if category:
        q = q.eq("category", category)
    clauses = []
    for t in terms:
        if not party_only:
            clauses += [f"filename.ilike.%{t}%", f"summary.ilike.%{t}%"]
        clauses += [f"fields->>{k}.ilike.%{t}%" for k in _PARTY_FIELD_KEYS]
    if clauses:
        q = q.or_(",".join(clauses))
    return q.limit(_FILED_SEARCH_FETCH_CAP).execute().data or []


# How many distinct materials/line items _aggregate_line_items hands back (biggest spend first).
_MATERIALS_RETURN_CAP = 40


def _num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _aggregate_line_items(rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """Roll the invoices' extracted line_items up into one materials summary: the same item
    (normalised description) merged across lines and documents, with summed quantity and
    integer-cent spend and the number of documents it appears on. Refund rows subtract. Returns
    (items sorted by spend desc, capped at _MATERIALS_RETURN_CAP; total distinct items).

    2026-09-23: DIGG wants "what materials have we bought from X", not only the Rand total —
    every Invoice/Quote/BOQ is already extracted with line_items (see _analyze_document's
    schema in vula/api/whatsapp.py), so this is aggregation of data already on the row, done
    here so no quantity or amount is ever summed by the LLM."""
    agg: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        sign = -1 if _is_refund_row(r) else 1
        for li in ((r.get("fields") or {}).get("line_items") or []):
            if not isinstance(li, dict):
                continue
            desc = str(li.get("description") or "").strip()
            key = _norm_name(desc)
            if not key:
                continue
            qty = _num(li.get("quantity"))
            cents = _num(li.get("total_cents"))
            if cents is None and qty is not None and _num(li.get("unit_price_cents")) is not None:
                cents = qty * _num(li.get("unit_price_cents"))
            a = agg.setdefault(key, {"description": desc, "quantity": 0.0, "spend_cents": 0,
                                     "documents": set(), "_unpriced": False, "_no_qty": False,
                                     "_unit": []})
            if qty and cents is not None:
                a["_unit"].append(abs(cents / qty))
            if qty is None:
                a["_no_qty"] = True
            else:
                a["quantity"] += sign * qty
            if cents is None:
                a["_unpriced"] = True
            else:
                a["spend_cents"] += sign * int(round(cents))
            a["documents"].add(r.get("id") or r.get("filename"))
    items = []
    for a in sorted(agg.values(), key=lambda a: a["spend_cents"], reverse=True):
        qty = round(a["quantity"], 3)
        item = {"description": a["description"],
                "quantity": int(qty) if qty == int(qty) else qty,
                "spend": f"R{a['spend_cents'] / 100:,.2f}", "spend_cents": a["spend_cents"],
                "documents": len(a["documents"])}
        if a["_no_qty"]:
            item["quantity_incomplete"] = True
        if a["_unpriced"]:
            item["spend_incomplete"] = True
        # Real digg-demo row: "SAND PER BAG ACC" read as qty 1 @ R465 on one invoice, R31/bag on
        # every other — almost certainly 15 bags misread as 1. The line total still reconciles
        # with the invoice total, so nothing upstream catches it; flag it here so the quantity
        # isn't stated as fact.
        units = [u for u in a["_unit"] if u > 0]
        if units and max(units) > 1.5 * min(units):
            item["unit_price_varies"] = True
        items.append(item)
    return items[:_MATERIALS_RETURN_CAP], len(items)


def _filed_rows_result(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    results = []
    total_cents, priced, refunds = 0, 0, 0
    for r in rows:
        fields = r.get("fields") or {}
        cents = _document_amount_cents(fields)
        refund = _is_refund_row(r)
        if cents is not None:
            total_cents += -abs(cents) if refund else cents
            priced += 1
            refunds += refund
        if len(results) < _FILED_SEARCH_RETURN_CAP:
            entry = {
                "id": r.get("id"), "filename": r.get("filename"), "category": r.get("category"),
                "summary": (r.get("summary") or "")[:200],
                "amount": _document_amount(fields),
                "party": _party_of(fields),
                "filed_at": r.get("created_at"),
            }
            if refund:
                entry["is_refund"] = True
            results.append(entry)
    out: Dict[str, Any] = {
        "matches": results, "match_type": "filed_document", "status": "found",
        "total_matches": len(rows),
        "total_amount": f"R{total_cents / 100:,.2f}",
        "total_amount_cents": total_cents,
        "matches_with_amount": priced,
    }
    notes = [f"total_amount is the server-computed sum of the {priced} match(es) with an "
             "extracted amount — quote it as-is rather than re-adding the list."]
    if refunds:
        notes.append(f"{refunds} match(es) marked is_refund are refunds/credit notes and were "
                     "SUBTRACTED from total_amount — mention them as refunds, not purchases.")
    if priced < len(rows):
        notes.append(f"{len(rows) - priced} match(es) have no extracted amount and are NOT in "
                     "that total — say so rather than presenting it as complete.")
    if len(rows) > len(results):
        notes.append(f"Only the {len(results)} most recent of {len(rows)} matches are listed; "
                     "total_amount still covers all of them.")
    if len(rows) >= _FILED_SEARCH_FETCH_CAP:
        notes.append(f"The search stopped at {_FILED_SEARCH_FETCH_CAP} documents — there may "
                     "be more; suggest narrowing by date or category.")
    materials, distinct = _aggregate_line_items(rows)
    if materials:
        out["materials"] = materials
        out["materials_distinct"] = distinct
        notes.append("materials rolls up every line item across ALL matches (same item merged, "
                     "quantity and spend summed server-side, biggest spend first) — use it for a "
                     "what-did-we-buy / materials summary instead of re-adding line items. Line "
                     "spend is per item as printed, so it need not equal total_amount exactly "
                     "(delivery, VAT or rounding lines).")
        if any(m.get("unit_price_varies") for m in materials):
            notes.append("An item marked unit_price_varies was bought at very different unit "
                         "prices across lines — often a misread quantity; flag its quantity as "
                         "unconfirmed rather than stating it.")
        if distinct > len(materials):
            notes.append(f"Only the top {len(materials)} of {distinct} distinct items by spend "
                         "are listed.")
    out["note"] = " ".join(notes)
    # Summary first, the long per-document list last: a caller that truncates the serialised
    # result (email_admin did, at 1,800 chars — 2026-09-23) then loses surplus rows, never the
    # total, the notes or the materials roll-up.
    out["matches"] = out.pop("matches")
    return out


def _dominant_party(results: List[Dict[str, Any]]) -> Optional[str]:
    """The counterparty most semantic hits agree on (ties -> the best-ranked hit's), or None."""
    counts: Dict[str, int] = {}
    first: Dict[str, str] = {}
    for r in results:
        party = (r.get("party") or "").strip()
        if not party:
            continue
        key = _norm_name(party)
        counts[key] = counts.get(key, 0) + 1
        first.setdefault(key, party)
    if not counts:
        return None
    best = max(counts, key=lambda k: counts[k])  # max() keeps the first (best-ranked) on a tie
    return first[best]


# Request filler the model tends to leave in a find_document query ("jack hammer invoices",
# "all Jack Hammer invoice and summary of what was spent"). The SQL match is a single substring
# ilike, so any of these words sinks it; the stripped core ("jack hammer") is searched too.
_QUERY_FILLER = {
    "a", "all", "an", "and", "any", "bill", "bills", "can", "documents", "document", "every",
    "find", "for", "from", "get", "give", "have", "i", "invoice", "invoices", "list", "me",
    "need", "of", "our", "paid", "payments", "please", "receipt", "receipts", "show", "spend",
    "spending", "spent", "summary", "the", "to", "total", "us", "was", "we", "were", "what",
    "with", "materials", "material", "bought", "purchases", "breakdown",
}


def _core_search_term(query: str) -> str:
    """`query` with request filler removed, or "" when nothing distinctive is left."""
    words = re.findall(r"[\w'&.-]+", query or "")
    core = [w for w in words if w.lower().strip("'") not in _QUERY_FILLER]
    return " ".join(core).strip()


def _name_patterns(core: str) -> List[str]:
    """ilike patterns for a name that tolerate how owners space it: "jack hammer" also matches
    "Jackhammer"/"Jack-Hammer's" ("jack%hammer"), and a one-word "jackhammer" also matches
    "Jack Hammer" (every split with 3+ letters each side). Already _pg_term-safe."""
    t = _pg_term(core)
    if not t:
        return []
    pats = [t]
    if " " in t:
        pats.append(re.sub(r"\s+", "%", t))
    elif len(t) >= 6 and t.isalpha():
        pats += [f"{t[:i]}%{t[i:]}" for i in range(3, len(t) - 2)]
    return list(dict.fromkeys(pats))


def _log_find(tenant_id: str, category: Optional[str], stage: str, n: int) -> None:
    # POPIA: stage + counts only, never the query text (it can carry names).
    logger.info("find_filed_document tenant=%s category=%s stage=%s matches=%d",
                tenant_id, category or "-", stage, n)


def _known_parties(tenant_id: str) -> List[str]:
    """Every distinct counterparty name on this tenant's filed documents plus its supplier
    register — the candidates _bridge_party can resolve a nickname to. Fail-open: []."""
    names: Dict[str, str] = {}
    try:
        rows = (_client().table("vula_filed_documents")
                .select("supplier:fields->>supplier,payee:fields->>payee_name,"
                        "customer:fields->>customer")
                .eq("tenant_id", tenant_id).limit(2000).execute().data or [])
        for r in rows:
            for v in r.values():
                if v and _norm_name(v):
                    names.setdefault(_norm_name(v), v)
    except Exception as exc:
        logger.debug("find_filed_document known-party lookup skipped: %s", exc)
    try:
        sup = (_client().table("commerce_suppliers").select("name")
               .eq("tenant_id", tenant_id).execute().data or [])
        for r in sup:
            if r.get("name") and _norm_name(r["name"]):
                names.setdefault(_norm_name(r["name"]), r["name"])
    except Exception as exc:
        logger.debug("find_filed_document supplier-register lookup skipped: %s", exc)
    return list(names.values())


# Words too common in SA business names/addresses to link a document to a party on their own.
_GENERIC_NAME_WORDS = {
    "aluminium", "and", "bathrooms", "build", "cape", "cash", "center", "centre", "city",
    "consulting", "design", "designs", "electrical", "energy", "engineers", "furniture", "gas",
    "glass", "group", "hardware", "head", "hire", "industries", "installations", "management",
    "manufacturing", "motors", "office", "products", "rental", "rentals", "sales", "security",
    "service", "services", "signs", "station", "store", "town", "trading", "warehouse",
}


def _bridge_party(term: str, docs: List[Dict[str, Any]], parties: List[str]) -> Optional[Tuple[str, str]]:
    """Resolve a nickname through a document that names both it and a real counterparty.

    2026-09-23, real DIGG data: "Jack Hammer" appears in no invoice at all — only in the
    filename "ACCOUNT APPLICATION - Jack Hammer's COD account.pdf", whose summary reads "an
    account application form for a COD account with Handiman Centre". That document is the
    owner's own link between the nickname and GARDENS HANDIMAN CENTRE, the supplier on all 16
    real invoices. Of the docs whose filename/summary contain `term`, return (party, filename)
    when exactly one known party is named in them (a two-word run of its name, or a single
    distinctive word for a one-word name); None when zero or several match, since a guess
    between suppliers is worse than no answer."""
    nterm = _norm_name(term)
    if not nterm:
        return None
    hits: Dict[str, Tuple[str, str]] = {}
    for d in docs:
        text = f" {_norm_name((d.get('filename') or '') + ' ' + (d.get('summary') or ''))} "
        if f" {nterm} " not in text and nterm.replace(" ", "") not in text.replace(" ", ""):
            continue
        for p in parties:
            toks = _norm_name(p).split()
            if not toks or " ".join(toks) == nterm:
                continue
            grams = ([toks[i:i + 2] for i in range(len(toks) - 1)] if len(toks) > 1
                     else ([toks] if len(toks[0]) >= 5 else []))
            # A run only counts if every word is 3+ letters and one is distinctive — otherwise
            # "T/A" ("t a") or an address ("City of Cape Town" -> "cape town") would link
            # almost any document to the wrong party.
            grams = [" ".join(g) for g in grams
                     if all(len(t) >= 3 for t in g) and any(t not in _GENERIC_NAME_WORDS for t in g)]
            if any(f" {g} " in text for g in grams):
                hits.setdefault(_norm_name(p), (p, d.get("filename") or "a document"))
    return next(iter(hits.values())) if len(hits) == 1 else None


def _resolved_party_result(tenant_id: str, query: str, party: str, category: Optional[str],
                           why: str) -> Optional[Dict[str, Any]]:
    """Every filed document for `party`, labelled as an inference the owner should confirm —
    or None when that search finds nothing."""
    try:
        prows = _filed_rows_query(tenant_id, [_pg_term(party)], category, party_only=True)
    except Exception as exc:
        logger.debug("find_filed_document party re-search skipped: %s", exc)
        return None
    if not prows:
        return None
    out = _filed_rows_result(prows)
    out["match_type"] = "resolved_via_knowledge_base"
    out["resolved_supplier"] = party
    out["note"] = (f"No filed invoice names '{query}' directly. {why} so these are ALL filed "
                   f"documents for '{party}'. Tell the owner you took '{query}' to mean "
                   f"'{party}' and ask them to confirm. " + out["note"])
    return out


async def find_filed_document(tenant_id: str, query: str, category: Optional[str] = None,
                              limit: int = 5) -> Dict[str, Any]:
    """Search filed documents (invoices/quotes/proof-of-payment/BOQs/receipts) for `query`.

    SQL match against vula_filed_documents (filename/summary ilike) first — cheap and precise
    for an invoice number or an exact customer/supplier name. 2026-09-18 incident: a query
    naming something that only appears INSIDE a document's content (an item description, e.g.
    "jackhammer") never matches a filename or summary, even though the document was ingested
    correctly and its content is sitting in the tenant's knowledge base — so a SQL miss now
    falls back to semantic search over that KB (populated from both emailed and WhatsApp-sent
    documents — see vula/email_imap/sync.py and vula/api/whatsapp.py's document ingest) instead
    of reporting "not found" while the answer is one KB query away.

    2026-09-21 follow-up, real DIGG transcript: "jackhammer" turned out to be the account name
    on a Handiman Centre COD account ("Jack Hammer's COD account.pdf"), not a filename/summary
    match, so this correctly fell to the semantic path — but that path only ever returned raw
    chunk text, never the matched document's real extracted amount, even when the exact same
    document also exists as a normal vula_filed_documents row with `total_cents` filled in (as
    every one of the real POS Account Sale invoices behind this account did). "I don't have the
    specific details... provide me with the invoice amounts" was an honest answer given what the
    tool handed back, not a model failure — the data just never made the return trip. Semantic
    matches now get cross-referenced back to vula_filed_documents by filename so a structured
    amount rides along whenever the same document was also filed normally, which is the common
    case (semantic search and normal filing both run on every ingested document).

    2026-09-23, same DIGG supplier: "all Jack Hammer invoices" found 2 of 13 real invoices
    (R789 of R20,278). Every one of the 13 has fields.supplier = "GARDENS HANDIMAN CENTRE", but
    nothing searched that field, "Jack Hammer" is only the owner's nickname for the supplier,
    and semantic top-k is lossy by design for an exhaustive request. Three changes, all aimed
    at an exact, complete answer instead of a fuzzy partial one:
      - the SQL match also checks the counterparty fields (supplier/payee_name/customer);
      - a query naming a known supplier by name or alias (commerce_suppliers.aliases, editable
        in the dashboard) searches under every one of that supplier's names;
      - when only the semantic path finds anything, the counterparty those hits agree on is
        re-searched exactly, so the ~2 fuzzy hits become all 13 — labelled as a resolution the
        owner should confirm, since it's an inference, not a stated alias.
    SQL results also carry a server-computed total (integer cents) and a full count, fetched
    up to _FILED_SEARCH_FETCH_CAP rows rather than the first `limit`.

    Single implementation so both find_document tools answer identically — see the routing
    incident this was extracted alongside: the same fix landing in one copy and not its sibling
    is exactly how these gaps have recurred before.
    """
    query = (query or "").strip()
    if not query:
        return {"error": "Give a few words about the document — supplier/customer name, "
                          "invoice number, amount, or what it was for."}
    safe_query = _pg_term(query)
    core = _core_search_term(query)
    terms = [t for t in dict.fromkeys([safe_query, _pg_term(core)]) if t]
    aliases = [t for t in (_pg_term(n) for n in await _resolve_supplier_names(tenant_id, query)) if t]
    try:
        rows = _filed_rows_query(tenant_id, terms, category, party_only=False)
        if aliases:
            seen = {r.get("id") for r in rows}
            rows += [r for r in _filed_rows_query(tenant_id, aliases, category, party_only=True)
                     if r.get("id") not in seen]
            rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    except Exception as exc:
        logger.warning("find_filed_document SQL query failed: %s", exc)
        return {"error": "Couldn't search documents right now."}

    # No hit names a counterparty or carries an amount (none at all, or e.g. only the "Jack
    # Hammer's COD account" application) — so it can't answer a spend question on its own. Try
    # to bridge to the real supplier first. The bridge documents are searched WITHOUT the
    # category filter: 2026-09-23 live retest, the linking document is a "General Document",
    # so a model that (reasonably) passed category="Invoice" filtered out the only link.
    if not aliases and core and not any(
            _party_of(r.get("fields") or {}) or _document_amount(r.get("fields") or {}) is not None
            for r in rows):
        bridge_docs = list(rows)
        try:
            bridge_docs += _filed_rows_query(tenant_id, _name_patterns(core), None, party_only=False)
        except Exception as exc:
            logger.debug("find_filed_document bridge-doc search skipped: %s", exc)
        bridged = _bridge_party(core, bridge_docs, _known_parties(tenant_id)) if bridge_docs else None
        if bridged:
            resolved = _resolved_party_result(
                tenant_id, query, bridged[0], category,
                f"'{bridged[1]}' links '{core}' to '{bridged[0]}',")
            if resolved:
                _log_find(tenant_id, category, "bridge_sql", resolved["total_matches"])
                return resolved

    if rows:
        out = _filed_rows_result(rows)
        if aliases:
            out["resolved_supplier"] = aliases[0]
        _log_find(tenant_id, category, "alias" if aliases else "sql", len(rows))
        return out

    try:
        from vula.ingestion.pipeline import VulaIngestionPipeline
        chunks = await VulaIngestionPipeline(tenant_id=tenant_id).query(query, top_k=limit)
    except Exception as exc:
        logger.debug("find_filed_document semantic fallback skipped: %s", exc)
        chunks = []
    if not chunks:
        _log_find(tenant_id, category, "not_found", 0)
        return {"status": "not_found_filed",
                "message": f"No filed document matches '{query}'. Ask the owner for the "
                            "invoice/document number, or to resend it — don't guess."}

    # Cross-reference by filename: the same document that surfaced via vector search is very
    # often also a normal vula_filed_documents row with real extracted fields (both happen on
    # every ingest) — pulling that in gives the model a trustworthy amount instead of only a
    # fuzzy text excerpt.
    filenames = list({c.get("filename") for c in chunks if c.get("filename")})
    filed_by_name: Dict[str, Dict[str, Any]] = {}
    if filenames:
        try:
            frows = (_client().table("vula_filed_documents")
                     .select("filename,category,summary,fields")
                     .eq("tenant_id", tenant_id).in_("filename", filenames).execute().data or [])
            filed_by_name = {fr["filename"]: fr for fr in frows}
        except Exception as exc:
            logger.debug("find_filed_document filename cross-reference skipped: %s", exc)

    results = []
    for c in chunks:
        fname = c.get("filename") or "document"
        filed = filed_by_name.get(fname)
        # A category filter was requested, but the KB chunk itself carries no category payload
        # (ingest_file never writes one — only the unrelated training-KB seeding path does), so
        # the only trustworthy signal is the cross-referenced vula_filed_documents row. Drop
        # anything that doesn't match it, and anything with no row to check at all — an
        # unverifiable chunk can't be trusted against a filter the caller explicitly asked for.
        if category and (not filed or filed.get("category") != category):
            continue
        entry: Dict[str, Any] = {"filename": fname, "excerpt": (c.get("text") or "")[:300],
                                  "score": c.get("score")}
        if filed:
            f = filed.get("fields") or {}
            entry["amount"] = _document_amount(f)
            entry["party"] = _party_of(f)
            entry["category"] = filed.get("category")
        results.append(entry)
    if not results:
        _log_find(tenant_id, category, "not_found", 0)
        return {"status": "not_found_filed",
                "message": f"No filed document matches '{query}'. Ask the owner for the "
                            "invoice/document number, or to resend it — don't guess."}

    # The semantic hits are a lossy top-k sample. First, a hit that names both the queried
    # nickname and a real counterparty (see _bridge_party) is the strongest link there is;
    # failing that, if the hits agree on one counterparty, pull every filed document for it.
    if core:
        docs = [{"filename": fn, "summary": fr.get("summary")} for fn, fr in filed_by_name.items()]
        docs += [{"filename": c.get("filename"), "summary": c.get("text")} for c in chunks]
        bridged = _bridge_party(core, docs, _known_parties(tenant_id))
        if bridged:
            resolved = _resolved_party_result(
                tenant_id, query, bridged[0], category,
                f"'{bridged[1]}' links '{core}' to '{bridged[0]}',")
            if resolved:
                _log_find(tenant_id, category, "bridge_semantic", resolved["total_matches"])
                return resolved
    party = _dominant_party(results)
    if party and _pg_term(party):
        resolved = _resolved_party_result(
            tenant_id, query, party, category,
            f"The closest knowledge-base matches belong to '{party}',")
        if resolved:
            _log_find(tenant_id, category, "semantic_party", resolved["total_matches"])
            return resolved

    _log_find(tenant_id, category, "knowledge_base", len(results))

    return {"match_type": "knowledge_base", "status": "found",
            "note": "Found in the knowledge base — these are fuzzy matches, NOT confirmed to "
                    "be from the supplier/customer the user named. Never present a match's "
                    "amount as theirs unless its 'party' or excerpt actually names them; if "
                    "none does, say you couldn't find their invoices and ask for the name on "
                    "the invoice. A match with a non-null 'amount' is a real "
                    "extracted figure from the filed document (safe to sum/quote) — a match "
                    "with no 'amount' is excerpt-only, so read it for context but confirm any "
                    "figure with the owner before acting on it.",
            "matches": results}


def _rands(v: Any) -> str:
    try:
        return f"R{float(v):,.2f}"
    except (TypeError, ValueError):
        return str(v)


def format_supplier_history_reply(result: Dict[str, Any], query: str = "") -> Optional[str]:
    """A complete WhatsApp answer for a supplier spend/materials question, built only from a
    find_filed_document result: count, server-computed total, refunds, the invoice list and the
    materials roll-up. None when the result isn't a complete filed-document answer (not found,
    or excerpt-only knowledge-base hits), so the caller falls back to the model.

    2026-09-23 (digg-demo): with all 16 invoices and the R21,256.00 total correctly in the tool
    result, the local llama3.1:8b replied "The total amount spent is R942.00" (the first row),
    invented quantities, and described the result as "JSON output from an email tool". Money is
    never left to a model to read back — this writes the reply deterministically instead."""
    if result.get("status") != "found" or "total_amount_cents" not in result:
        return None
    matches = result.get("matches") or []
    total_n = int(result.get("total_matches") or len(matches))
    if not total_n:
        return None
    supplier = result.get("resolved_supplier") or next(
        (m.get("party") for m in matches if m.get("party")), None) or "this supplier"
    refunds = [m for m in matches if m.get("is_refund")]
    head = f"*{supplier}*: {total_n} document{'s' if total_n != 1 else ''}, total spend " \
           f"*{result.get('total_amount')}*"
    if refunds:
        head += (f" (after {len(refunds)} refund{'s' if len(refunds) != 1 else ''} of "
                 f"{', '.join(_rands(r.get('amount')) for r in refunds)})")
    lines = [head + "."]
    if result.get("match_type") == "resolved_via_knowledge_base" and query:
        lines.append(f"I took \"{query}\" to mean {supplier} — tell me if that's wrong, or save "
                     f"it with \"{query} is an alias for {supplier}\".")
    missing = int(result.get("total_matches") or 0) - int(result.get("matches_with_amount") or 0)
    if missing > 0:
        lines.append(f"⚠️ {missing} document{'s have' if missing != 1 else ' has'} no amount on "
                     f"file and {'are' if missing != 1 else 'is'} not in that total.")
    lines.append("")
    lines.append("*Invoices*")
    for m in matches:
        name = re.sub(r"\.(pdf|jpe?g|png)$", "", m.get("filename") or "document", flags=re.I)
        day = (m.get("filed_at") or "")[:10]
        amt = _rands(m["amount"]) if m.get("amount") is not None else "no amount"
        tag = " (refund)" if m.get("is_refund") else ""
        lines.append(f"• {day + ' — ' if day else ''}{name} — {amt}{tag}")
    if total_n > len(matches):
        lines.append(f"…and {total_n - len(matches)} more (all included in the total).")
    materials = result.get("materials") or []
    if materials:
        lines.append("")
        lines.append("*Materials* (biggest spend first)")
        for it in materials[:12]:
            q = it.get("quantity")
            flag = " — ⚠️ quantity to check" if it.get("unit_price_varies") else ""
            lines.append(f"• {it.get('description')} × {q} — {it.get('spend')}{flag}")
        extra = int(result.get("materials_distinct") or len(materials)) - min(len(materials), 12)
        if extra > 0:
            lines.append(f"…plus {extra} more item{'s' if extra != 1 else ''}.")
    return "\n".join(lines)


async def answer_supplier_history(tenant_id: str, question: str) -> Optional[str]:
    """The whole answer to a supplier spend/materials question, with no model involved — when
    the question names a known supplier (commerce_suppliers name or alias) as whole words.
    None otherwise, so the caller runs its normal tool-calling loop.

    2026-09-23 (digg-demo), after #69: the local box was unreachable, the cloud 70B returned an
    empty reply without calling find_document, and the owner got "Done.". Once "Jack Hammer" is
    a saved alias there is nothing left for a model to decide: the supplier is known, the
    search and total are server-side, and format_supplier_history_reply writes the reply."""
    padded = f" {_norm_name(question)} "
    names = await _resolve_supplier_names(tenant_id, question)
    # Whole-word mentions only — _resolve_supplier_names also accepts a fuzzy whole-query match,
    # which is fine for a search box but not for answering without a model.
    if not any(n and f" {n} " in padded for n in (_norm_name(x) for x in names)):
        return None
    result = await find_filed_document(tenant_id, names[0], category="Invoice")
    reply = format_supplier_history_reply(result, query=names[0])
    if reply and _PERIOD_RE.search(question or ""):
        # The filed-document total is all-time; answering "this month?" with it unlabelled
        # read as that month's spend. Say what the figure is rather than imply a period.
        reply += ("\n\nNote: that's the all-time total across these documents — I can't split "
                  "supplier spend by date yet, so check the dates listed above for the period "
                  "you asked about.")
    return reply


_PERIOD_RE = re.compile(
    r"\b(this|last|past|previous)\s+(week|month|quarter|year)\b|\btoday\b|\byesterday\b"
    r"|\bsince\b|\bin\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b"
    r"|\blast\s+\d+\s+(days|weeks|months)\b", re.IGNORECASE)


async def filed_amounts_by_filename(tenant_id: str, filenames: List[str]) -> Dict[str, Dict[str, Any]]:
    """Cross-reference KB-chunk filenames against vula_filed_documents, returning
    {filename: {"amount": ..., "party": ...}} for every one that was also filed normally with a
    real extracted amount. Same lookup find_filed_document's semantic fallback runs (2026-09-21
    fix) — factored out here so RAG-grounded skills (reasoning.py, architecture_planning.py) can
    carry a verified figure in their prompt context instead of leaving the model to read one off
    raw chunk text, which is exactly the failure mode reasoning.py's tenant-data-question guard
    exists to catch after the real R70,400 "logged" fabrication incident. Fail-open: any lookup
    error returns {} rather than raising, same as every other best-effort helper in this module.
    """
    names = list({f for f in filenames if f})
    if not names:
        return {}
    try:
        rows = (_client().table("vula_filed_documents")
                .select("filename,category,fields")
                .eq("tenant_id", tenant_id).in_("filename", names).execute().data or [])
    except Exception as exc:
        logger.debug("filed_amounts_by_filename lookup skipped: %s", exc)
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        f = r.get("fields") or {}
        amount = _document_amount(f)
        if amount is None:
            continue
        out[r["filename"]] = {
            "amount": amount,
            "party": f.get("supplier") or f.get("payee_name") or f.get("customer"),
        }
    return out
