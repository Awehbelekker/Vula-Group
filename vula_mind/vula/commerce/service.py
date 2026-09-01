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
from typing import Any, Dict, List, Optional

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


async def create_order(tenant_id: str, cart: dict, checkout_data: dict) -> dict:
    items = cart.get("commerce_cart_items", [])
    # int(round(...)) so per-kg quantities (e.g. 1.5) resolve to exact cents.
    subtotal = sum(int(round(i["quantity"] * i["unit_price_cents"])) for i in items)
    delivery = cart.get("delivery_cents", 8000)
    # Tenant delivery-fee rules (migration 070) override the cart's snapshotted default:
    # configured standard fee, and free delivery at/above the configured subtotal.
    try:
        from vula.commerce.order_workflow import get_order_settings
        _cfg = get_order_settings(tenant_id)
        if _cfg.get("delivery_fee_cents") is not None:
            delivery = int(_cfg["delivery_fee_cents"])
        free_over = _cfg.get("free_delivery_over_cents")
        if free_over and subtotal >= int(free_over):
            delivery = 0
    except Exception:
        pass

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


async def reorder_from_last_order(tenant_id: str, phone: str) -> dict:
    """Find this customer's most recent order and return its line items, for WhatsApp's
    'reorder'/'same as last time' shortcut. Matches on the last 9 digits of the phone number
    (mirrors the defensive suffix matching commerce_assistant.py already uses for cancel/change
    order, since stored customer_phone formatting isn't perfectly consistent). Raises ValueError
    with a message safe to show the customer if there's no prior order to repeat."""
    digits = "".join(c for c in (phone or "") if c.isdigit())
    if not digits:
        raise ValueError("no phone number to look up")
    orders = (_client().table("commerce_orders")
              .select("id,display_id,customer_phone,created_at")
              .eq("tenant_id", tenant_id).order("created_at", desc=True).limit(50).execute().data or [])
    mine = [o for o in orders
            if "".join(c for c in (o.get("customer_phone") or "") if c.isdigit()).endswith(digits[-9:])]
    if not mine:
        raise ValueError("no previous order found to repeat")
    last = await get_order(mine[0]["id"])
    items = (last or {}).get("commerce_order_items") or []
    if not items:
        raise ValueError("that order has no items on file to repeat")
    return {"display_id": last.get("display_id"), "items": items}


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
        orders = (_client().table("commerce_orders")
                  .select("display_id,customer_name,customer_phone,customer_email,"
                          "delivery_address,delivery_slot,created_at")
                  .eq("tenant_id", tenant_id)
                  .not_.in_("status", ["cancelled", "refunded"])
                  .order("created_at", desc=True).limit(50).execute().data or [])
    except Exception as exc:  # never block a live order on a profile lookup
        logger.warning("get_customer_profile lookup failed (tenant=%s): %s", tenant_id, exc)
        return None
    mine = [o for o in orders
            if "".join(c for c in (o.get("customer_phone") or "") if c.isdigit()).endswith(digits[-9:])]
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


async def update_order_status(order_id: str, status: str, yoco_checkout_id: Optional[str] = None) -> None:
    update = {"status": status, "updated_at": _now()}
    if yoco_checkout_id:
        update["yoco_checkout_id"] = yoco_checkout_id
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


async def _next_invoice_number(tenant_id: str, doc_type: str) -> str:
    """Sequential, tenant-scoped, doc-type-scoped number e.g. OTH-INV-00001.

    Race-safe (migration 122): the number comes from a single atomic
    UPSERT...RETURNING RPC (`next_document_number`) rather than a
    SELECT-last-then-add-1-in-Python read/write pair, which two concurrent
    invoice creations could both read before either had written back,
    minting the same number twice.
    """
    code = _DOC_TYPE_CODE.get(doc_type, "INV")
    result = _client().rpc(
        "next_document_number", {"p_tenant_id": tenant_id, "p_counter_key": doc_type}
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
            ledger.post_invoice_paid(tenant_id, invoice)
        except Exception as exc:
            logger.warning("ledger hook failed for invoice %s: %s", invoice_id, exc)
    return invoice


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
    import json as _j
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
        row = {
            "id": record_id, "tenant_id": tenant_id, "direction": "inbound", "doc_type": doc_type,
            "invoice_number": await _next_invoice_number(tenant_id, doc_type),
            "customer_name": tenant_name,
            "status": "draft", "supplier": supplier_name, "supplier_id": supplier_id,
            "project": project,
            "issue_date": str(doc_date), "due_date": str(due_date) if due_date else None,
            "payment_terms_days": payment_terms_days,
            "subtotal_cents": total_cents - vat_cents, "vat_rate": 15.0, "vat_cents": vat_cents,
            "total_cents": total_cents, "discount_cents": 0, "deposit_cents": 0,
            "line_items": _j.dumps(extracted.get("line_items", [])),
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
            "doc_type": doc_type, "line_items": _j.dumps(extracted.get("line_items", [])),
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
_INVOICE_SETTINGS_OPTIONAL_FIELDS = (
    _INVOICE_SETTINGS_078_FIELDS + _INVOICE_SETTINGS_103_FIELDS + _INVOICE_SETTINGS_128_FIELDS
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
