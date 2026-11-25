"""
vula/commerce/till.py — selling over the counter (24 Nov 2026).

The dashboard's Till screen: scan or tap products, take the money, hand over the goods. A till
sale is an ordinary order (channel `till`), so stock, the books, reports and reviews treat it like
every other sale — it is just made, paid and handed over in one go.

    products(tenant)                 what the till shows: the price charged NOW, stock, barcodes
    sale(tenant, items, method, …)   one sale: priced on the server, paid, handed over, read back
    summary(tenant, day)             the day's takings by how they were paid and by who rang them up

Rules this keeps:
  * Money is integer cents and every figure is the server's. The till sends product ids and
    quantities, never prices; the total, and the change for cash, come back from here. Cash
    tendered below the total is refused — nothing is sold.
  * One tap, one sale. The till sends a `sale_id` it made up for this basket; ringing the same
    one up again (a double tap, a retry on bad signal) returns the first sale, never a second.
  * Verified, not reported: the order is read back as paid before the till says "done".
  * No delivery fee (the customer is standing there) and no WhatsApp to the customer.
  * How it was paid is recorded (cash, card, EFT, SnapScan, Zapper). Card here means the
    business's own card machine took it; Vula records it, it does not charge the card.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

METHODS = {"cash": "Cash", "card": "Card machine", "eft": "EFT", "snapscan": "SnapScan", "zapper": "Zapper"}
WALK_IN = "Walk-in customer"
CHANNEL = "till"
MAX_LINES = 80
_SALE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_SAST = timezone(timedelta(hours=2))


class TillError(Exception):
    """A sale that can't go ahead — the message is for the person at the till."""

    def __init__(self, message: str, status: int = 400, **detail: Any):
        super().__init__(message)
        self.status, self.detail = status, detail


def _db():
    from vula.commerce import service
    return service._client()


def rand(cents: Any) -> str:
    return f"R{int(cents or 0) / 100:,.2f}"


# ── what the till shows ───────────────────────────────────────────────────────

async def products(tenant_id: str) -> List[dict]:
    """Every product that can be sold now, with the price a sale would charge (sale-aware — the
    same `effective_price_cents` the cart uses), its stock and barcodes."""
    from vula.commerce import service
    from vula.db_pages import all_rows
    rows = await service.list_products(tenant_id, in_stock_only=False, statuses={"active"})
    by_product: Dict[str, List[dict]] = {}
    try:
        for v in all_rows(lambda: _db().table("commerce_product_variants")
                          .select("id,product_id,option_values,price_cents,barcode,sku,stock_quantity,in_stock,"
                                  "archived,sort_order").eq("tenant_id", tenant_id), cap=20000):
            by_product.setdefault(v["product_id"], []).append(v)
    except Exception as exc:  # noqa: BLE001
        log.debug("till: variants not read (run migration 087?): %s", exc)
    out = []
    for p in rows:
        if p.get("archived"):
            continue
        variants = []
        for v in sorted(by_product.get(p["id"]) or [], key=lambda x: x.get("sort_order") or 0):
            if v.get("archived"):
                continue
            label = " / ".join(str(x) for x in (v.get("option_values") or {}).values() if x)
            variants.append({"id": v["id"], "label": label or v.get("sku") or "Option",
                             "price_cents": int(v.get("price_cents") or 0) or service.effective_price_cents(p),
                             "barcode": v.get("barcode"), "sku": v.get("sku"),
                             "stock": v.get("stock_quantity"), "in_stock": v.get("in_stock") is not False})
        out.append({"id": p["id"], "name": p.get("name") or "Item", "category": p.get("category") or "",
                    "price_cents": service.effective_price_cents(p),
                    "on_sale": service.effective_price_cents(p) != int(p.get("price_cents") or 0),
                    "by_kg": str(p.get("sold_by") or "").lower() == "kg",
                    "image": p.get("image_url"), "barcode": p.get("barcode"), "sku": p.get("sku"),
                    "stock": p.get("stock_quantity"), "in_stock": p.get("in_stock") is not False,
                    "variants": variants})
    out.sort(key=lambda r: r["name"].lower())
    return out


# ── one sale ──────────────────────────────────────────────────────────────────

def _clean_items(raw: Any) -> List[dict]:
    items = []
    for it in raw if isinstance(raw, list) else []:
        if not isinstance(it, dict) or not it.get("product_id"):
            continue
        try:
            qty = float(it.get("quantity") or 0)
        except (TypeError, ValueError):
            continue
        if qty <= 0 or qty > 9999:
            continue
        items.append({"product_id": str(it["product_id"]),
                      "variant_id": str(it["variant_id"]) if it.get("variant_id") else None, "quantity": qty})
    return items[:MAX_LINES]


def _existing(tenant_id: str, session_id: str) -> Optional[dict]:
    """The order already made for this till `sale_id`, if any (whatever became of its cart)."""
    try:
        carts = (_db().table("commerce_carts").select("id").eq("tenant_id", tenant_id)
                 .eq("session_id", session_id).limit(5).execute().data or [])
        if not carts:
            return None
        rows = (_db().table("commerce_orders").select("*").eq("tenant_id", tenant_id)
                .in_("cart_id", [c["id"] for c in carts]).limit(1).execute().data or [])
        return rows[0] if rows else None
    except Exception as exc:  # noqa: BLE001
        log.warning("till: earlier sale lookup failed: %s", exc)
        return None


def _lines(order_id: str) -> List[dict]:
    try:
        rows = (_db().table("commerce_order_items").select("product_name,quantity,unit_price_cents,total_cents")
                .eq("order_id", order_id).limit(MAX_LINES).execute().data or [])
    except Exception:  # noqa: BLE001 — a column that isn't there yet
        rows = (_db().table("commerce_order_items").select("*").eq("order_id", order_id)
                .limit(MAX_LINES).execute().data or [])
    out = []
    for r in rows:
        unit = int(r.get("unit_price_cents") or r.get("price_cents") or 0)
        try:
            q = float(r.get("quantity") or 0)
        except (TypeError, ValueError):
            q = 0.0
        total = r.get("total_cents")
        out.append({"name": r.get("product_name") or r.get("name") or "Item", "quantity": q,
                    "unit_cents": unit, "total_cents": int(total) if total is not None else int(round(q * unit))})
    return out


def _receipt(tenant_id: str, order: dict, *, tendered_cents: Optional[int], repeat: bool = False) -> dict:
    from vula.commerce import order_slip
    total = int(order.get("total_cents") or 0)
    method = str(order.get("payment_method") or "")
    change = (int(tendered_cents) - total) if method == "cash" and tendered_cents is not None else None
    try:
        links = order_slip.urls(order["id"], tenant_id)
    except Exception:  # noqa: BLE001
        links = {}
    return {"order_id": order["id"], "number": order.get("display_id"), "status": order.get("status"),
            "paid": order.get("status") not in ("pending_payment", "cancelled"),
            "lines": _lines(order["id"]), "subtotal_cents": int(order.get("subtotal_cents") or 0),
            "discount_cents": int(order.get("discount_cents") or 0), "total_cents": total,
            "method": method, "method_label": METHODS.get(method, method or "—"),
            "tendered_cents": tendered_cents if method == "cash" else None,
            "change_cents": change if change is not None and change >= 0 else None,
            "customer_name": order.get("customer_name"), "receipt_url": links.get("page"),
            "at": order.get("created_at"), "repeat": repeat}


async def sale(tenant_id: str, *, items: Any, method: str, sale_id: str,
               tendered_cents: Optional[int] = None, customer_name: str = "", customer_phone: str = "",
               discount_code: str = "", actor: Optional[str] = None) -> dict:
    """Ring up one sale. Returns the receipt, read back from the database."""
    from vula.commerce import service
    method = str(method or "").strip().lower()
    if method not in METHODS:
        raise TillError("Choose how the customer paid.")
    if not _SALE_ID_RE.match(str(sale_id or "")):
        raise TillError("This sale has no reference — refresh the till and ring it up again.")
    session_id = f"till-{sale_id}"

    earlier = _existing(tenant_id, session_id)
    if earlier:                                   # the same basket rung up twice: one sale
        return _receipt(tenant_id, earlier, tendered_cents=tendered_cents, repeat=True)

    lines = _clean_items(items)
    if not lines:
        raise TillError("The basket is empty.")
    if tendered_cents is not None:
        try:
            tendered_cents = int(tendered_cents)
        except (TypeError, ValueError):
            raise TillError("Enter the cash received as an amount.")
        if tendered_cents < 0:
            raise TillError("Enter the cash received as an amount.")

    cart = await service.get_or_create_cart(tenant_id, session_id, customer_phone=customer_phone or None)
    skipped = 0
    for it in lines:
        try:
            await service.add_to_cart(tenant_id, cart["id"], it["product_id"], it["quantity"],
                                      variant_id=it["variant_id"])
        except Exception as exc:  # noqa: BLE001
            skipped += 1
            log.warning("till: item not added (%s): %s", it["product_id"], exc)
    if skipped:
        # Never sell a basket that isn't the one on the screen.
        await _drop_cart(cart["id"])
        raise TillError("One of the items couldn't be added — it may have been removed. "
                        "Refresh the till and ring the basket up again.", status=409)
    cart = await service.get_or_create_cart(tenant_id, session_id)

    # Cash is checked against the server's own total BEFORE anything is sold.
    subtotal = sum(int(round(float(i["quantity"]) * int(i["unit_price_cents"])))
                   for i in cart.get("commerce_cart_items") or [])
    if method == "cash" and tendered_cents is not None and not discount_code and tendered_cents < subtotal:
        await _drop_cart(cart["id"])
        raise TillError(f"{rand(tendered_cents)} is less than the total of {rand(subtotal)}.",
                        total_cents=subtotal)

    try:
        order = await service.create_order(tenant_id, cart, {
            "customer_phone": (customer_phone or "").strip(), "customer_name": (customer_name or "").strip() or WALK_IN,
            "delivery_address": "Sold at the till", "delivery_slot": "morning", "fulfilment": "collection",
            "channel": CHANNEL, "payment_method": method, "discount_code": (discount_code or "").strip() or None,
            **({"created_by": actor} if actor else {}),
        })
    except service.OutOfStockError as exc:
        raise TillError(str(exc), status=409) from exc

    total = int(order.get("total_cents") or 0)
    if method == "cash" and tendered_cents is not None and tendered_cents < total:
        # Only reachable with a discount code that didn't apply: undo, sell nothing.
        await _undo(order["id"])
        raise TillError(f"{rand(tendered_cents)} is less than the total of {rand(total)}.", total_cents=total)

    paid = await service.mark_order_paid(tenant_id, order["id"], method)
    if not paid:
        await _undo(order["id"])
        raise TillError("The sale couldn't be recorded as paid, so nothing was sold. Ring it up again.", status=409)
    try:                                          # handed over the counter: nothing left to fulfil
        await service.update_order_status(order["id"], "delivered")
    except Exception as exc:  # noqa: BLE001
        log.warning("till: sale %s paid but not marked handed over: %s", order.get("display_id"), exc)

    rows = (_db().table("commerce_orders").select("*").eq("tenant_id", tenant_id)
            .eq("id", order["id"]).limit(1).execute().data or [])
    if not rows or rows[0].get("status") in ("pending_payment", "cancelled"):
        raise TillError("The sale was made but couldn't be read back as paid. Check Orders before "
                        "ringing it up again.", status=409)
    return _receipt(tenant_id, rows[0], tendered_cents=tendered_cents)


async def _drop_cart(cart_id: str) -> None:
    try:
        from vula.commerce import service
        await service.clear_cart(cart_id)
    except Exception as exc:  # noqa: BLE001
        log.debug("till: cart %s not cleared: %s", cart_id, exc)


async def _undo(order_id: str) -> None:
    """A sale that didn't complete: cancel the order and give its stock back."""
    from vula.commerce import service
    try:
        await service.update_order_status(order_id, "cancelled")
        await service.apply_order_stock(order_id, restore=True)
    except Exception as exc:  # noqa: BLE001
        log.error("till: unfinished sale %s not undone: %s", order_id, exc)


# ── the day's takings ─────────────────────────────────────────────────────────

def _day_bounds(day: Optional[str]) -> tuple:
    try:
        d = date.fromisoformat(day) if day else datetime.now(_SAST).date()
    except ValueError:
        raise TillError("Use a date like 2026-11-24.")
    start = datetime(d.year, d.month, d.day, tzinfo=_SAST)
    return d, start.astimezone(timezone.utc).isoformat(), (start + timedelta(days=1)).astimezone(timezone.utc).isoformat()


def summary(tenant_id: str, day: Optional[str] = None) -> dict:
    """Till sales for one day (South African time): how many, how much, by how they were paid and
    by who rang them up. Refunded and cancelled sales are counted apart, never in the takings.
    Sums of stored cents; nothing is estimated."""
    from vula.db_pages import all_rows
    d, start, end = _day_bounds(day)
    rows = all_rows(lambda: _db().table("commerce_orders")
                    .select("id,display_id,total_cents,payment_method,status,created_by,created_at,"
                            "refunded_amount_cents,customer_name")
                    .eq("tenant_id", tenant_id).eq("channel", CHANNEL)
                    .gte("created_at", start).lt("created_at", end), cap=20000)
    by_method: Dict[str, Dict[str, int]] = {}
    by_person: Dict[str, Dict[str, int]] = {}
    count = total = undone = refunded = 0
    recent = []
    for r in rows:
        cents = int(r.get("total_cents") or 0)
        if r.get("status") in ("cancelled", "pending_payment"):
            undone += 1
            continue
        back = int(r.get("refunded_amount_cents") or 0) if r.get("status") == "refunded" else 0
        if r.get("status") == "refunded" and not back:
            back = cents
        refunded += back
        count += 1
        total += cents
        m = str(r.get("payment_method") or "other")
        slot = by_method.setdefault(m, {"count": 0, "cents": 0})
        slot["count"] += 1
        slot["cents"] += cents
        who = str(r.get("created_by") or "Not recorded")
        slot = by_person.setdefault(who, {"count": 0, "cents": 0})
        slot["count"] += 1
        slot["cents"] += cents
        recent.append({"number": r.get("display_id"), "at": r.get("created_at"), "cents": cents,
                       "method": METHODS.get(m, m), "refunded": r.get("status") == "refunded"})
    recent.sort(key=lambda x: str(x["at"]), reverse=True)
    return {"date": d.isoformat(), "sales": count, "total_cents": total, "refunded_cents": refunded,
            "net_cents": total - refunded, "not_completed": undone,
            "by_method": [{"method": k, "label": METHODS.get(k, k.title()), **v}
                          for k, v in sorted(by_method.items(), key=lambda kv: -kv[1]["cents"])],
            "by_person": [{"who": k, **v} for k, v in sorted(by_person.items(), key=lambda kv: -kv[1]["cents"])],
            "recent": recent[:30]}
