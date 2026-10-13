"""
vula/commerce/web_basket.py — a basket filled on the shop's web page, handed to WhatsApp.

2026-10-13 (SeedleMe, Ian: "no selection to order or add to cart and push back to order in
WhatsApp"): the photo page linked from the WhatsApp menu showed products but couldn't take an
order. Now a customer picks colours and quantities there, taps "Order on WhatsApp", and the page
opens their WhatsApp with "Hi! I'd like to order basket K7Q2MX". That message moves the basket
into their WhatsApp cart and checkout carries on in the chat.

The basket is an ordinary commerce cart under session "web-<code>": priced server-side from the
live product rows (the browser only says which products and how many), never trusted for money.
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"        # no 0/O, 1/I — read off a phone screen
_CODE_LEN = 6
MAX_LINES = 30
MAX_QTY = 99
EXPIRES_AFTER = timedelta(days=7)
BASKET_RE = re.compile(r"\bbasket\s+([A-HJ-NP-Z2-9]{6})\b", re.I)


def new_code() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LEN))


def session_id(code: str) -> str:
    return f"web-{code.upper()}"


def wa_number(tenant_id: str) -> str:
    """The shop's own WhatsApp number, digits only ('' when it has none connected)."""
    from vula.commerce import service
    try:
        rows = (service._client().table("vula_whatsapp_accounts").select("phone_number")
                .eq("tenant_id", tenant_id).eq("status", "connected").limit(1).execute().data or [])
    except Exception:
        return ""
    return re.sub(r"\D", "", (rows[0].get("phone_number") if rows else "") or "")


def order_message(code: str) -> str:
    return f"Hi! I'd like to order basket {code}"


def wa_link(number: str, code: str) -> str:
    return f"https://wa.me/{number}?text={quote(order_message(code))}"


def clean_items(raw: Any) -> List[Dict[str, Any]]:
    """[{product_id, qty}] from the browser → merged, whole quantities 1..99, at most 30 lines."""
    out: Dict[str, int] = {}
    for it in (raw if isinstance(raw, list) else [])[:MAX_LINES * 2]:
        if not isinstance(it, dict):
            continue
        pid = str(it.get("product_id") or "").strip()
        try:
            qty = int(it.get("qty") or 0)
        except (TypeError, ValueError):
            continue
        if pid and qty > 0:
            out[pid] = min(MAX_QTY, out.get(pid, 0) + qty)
    return [{"product_id": k, "qty": v} for k, v in list(out.items())[:MAX_LINES]]


async def create(tenant_id: str, raw_items: Any) -> Dict[str, Any]:
    """Save the basket. Only active, in-stock products of this shop are taken; the rest are
    reported back by id so the page can say what was left out."""
    from vula.commerce import service
    items = clean_items(raw_items)
    if not items:
        return {"error": "Your basket is empty."}
    live = {str(p["id"]): p for p in await service.list_products(
        tenant_id, in_stock_only=True, statuses={"active"})}
    kept = [it for it in items if it["product_id"] in live]
    skipped = [it["product_id"] for it in items if it["product_id"] not in live]
    if not kept:
        return {"error": "Those items are no longer available.", "skipped": skipped}
    code = new_code()
    cart = await service.get_or_create_cart(tenant_id, session_id(code))
    for it in kept:
        await service.add_to_cart(tenant_id, cart["id"], it["product_id"], float(it["qty"]))
    subtotal = sum(int(live[it["product_id"]].get("price_cents") or 0) * it["qty"] for it in kept)
    return {"code": code, "lines": len(kept), "subtotal_cents": subtotal, "skipped": skipped}


def _find(tenant_id: str, code: str) -> Optional[dict]:
    from vula.commerce import service
    rows = (service._client().table("commerce_carts")
            .select("id,created_at,commerce_cart_items(product_id,variant_id,quantity)")
            .eq("tenant_id", tenant_id).eq("session_id", session_id(code)).eq("status", "active")
            .limit(1).execute().data or [])
    if not rows:
        return None
    created = rows[0].get("created_at") or ""
    try:
        when = datetime.fromisoformat(created.replace("Z", "+00:00"))
        if datetime.now(timezone.utc) - when > EXPIRES_AFTER:
            return None
    except ValueError:
        pass
    return rows[0]


async def claim(tenant_id: str, phone: str, code: str) -> Optional[int]:
    """Move basket `code` into this customer's WhatsApp cart. Returns how many lines moved, or
    None when there's no such basket (wrong code, already ordered, or over a week old)."""
    from vula.commerce import service
    basket = _find(tenant_id, code)
    if not basket:
        return None
    items = basket.get("commerce_cart_items") or []
    cart = await service.get_or_create_cart(tenant_id, phone, phone)
    for it in items:
        await service.add_to_cart(tenant_id, cart["id"], it["product_id"],
                                  float(it.get("quantity") or 1), variant_id=it.get("variant_id"))
    await service.clear_cart(basket["id"])            # one basket, one order
    return len(items)
