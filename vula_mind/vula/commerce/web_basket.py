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


def shop_enabled(tenant_id: str, products: Optional[list] = None) -> bool:
    """The WhatsApp shop is on: the tenant has the `wa_shop` module (standard for food and retail
    shops from 2026-10-13; switched on per tenant in Master for existing ones), or its products
    come in colour ranges — a range of eleven colours doesn't work as one row per colour."""
    try:
        from vula.api.tenants import enabled_modules
        if "wa_shop" in enabled_modules(tenant_id):
            return True
    except Exception:
        pass
    if products:
        from vula.commerce import product_families as pf
        return any(r["colours"] for r in pf.group(products))
    return False


async def shop_enabled_for(tenant_id: str) -> bool:
    """shop_enabled, reading the shop's live products itself."""
    from vula.commerce import service
    try:
        products = await service.list_products(tenant_id, in_stock_only=True, statuses={"active"})
    except Exception:
        products = []
    return shop_enabled(tenant_id, products)


def sold_by_kg(product: Optional[dict]) -> bool:
    return str((product or {}).get("sold_by") or "").lower() == "kg"


def norm_qty(product: Optional[dict], raw: Any) -> float:
    """Whole items, or kilos in half-kilo steps for a product sold by the kilo; 0 when invalid."""
    try:
        q = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if q <= 0:
        return 0.0
    if sold_by_kg(product):
        return min(float(MAX_QTY), max(0.5, round(q * 2) / 2))
    return float(min(MAX_QTY, int(q)))


def qty_label(product: Optional[dict], qty: float) -> str:
    n = int(qty) if qty == int(qty) else qty
    return f"{n} kg" if sold_by_kg(product) else str(n)


def new_code() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LEN))


def session_id(code: str) -> str:
    return f"web-{code.upper()}"


def wa_number(tenant_id: str) -> str:
    """The shop's own WhatsApp number, digits only ('' when it has none connected)."""
    from vula import whatsapp_numbers
    from vula.commerce import service
    try:   # its main number, when it has several (vula/whatsapp_numbers.py)
        row = whatsapp_numbers.main_row(whatsapp_numbers.rows_for(tenant_id, service._client()))
    except Exception:
        return ""
    return re.sub(r"\D", "", ((row or {}).get("phone_number")) or "")


def order_message(code: str) -> str:
    return f"Hi! I'd like to order basket {code}"


def wa_link(number: str, code: str) -> str:
    return f"https://wa.me/{number}?text={quote(order_message(code))}"


def clean_items(raw: Any) -> List[Dict[str, Any]]:
    """[{product_id, qty}] from the browser → merged per product, at most 30 lines. Quantities
    are checked against each product when the basket is saved (whole items, or half kilos)."""
    out: Dict[str, float] = {}
    for it in (raw if isinstance(raw, list) else [])[:MAX_LINES * 2]:
        if not isinstance(it, dict):
            continue
        pid = str(it.get("product_id") or "").strip()
        try:
            qty = float(it.get("qty") or 0)
        except (TypeError, ValueError):
            continue
        if pid and qty > 0:
            out[pid] = min(float(MAX_QTY), out.get(pid, 0.0) + qty)
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
    kept, skipped = [], []
    for it in items:
        qty = norm_qty(live.get(it["product_id"]), it["qty"]) if it["product_id"] in live else 0
        (kept.append({**it, "qty": qty}) if qty else skipped.append(it["product_id"]))
    if not kept:
        return {"error": "Those items are no longer available.", "skipped": skipped}
    code = new_code()
    cart = await service.get_or_create_cart(tenant_id, session_id(code))
    for it in kept:
        await service.add_to_cart(tenant_id, cart["id"], it["product_id"], it["qty"])
    subtotal = sum(int(round(int(live[it["product_id"]].get("price_cents") or 0) * it["qty"])) for it in kept)
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


# ── Speed and the shop's own address (2026-10-13, Ian: "the link shows Vula and it takes away
# from the client's brand"; and make it fast) ─────────────────────────────────────────────────

_PRODUCTS: Dict[tuple, tuple] = {}
PRODUCTS_TTL_S = 60.0
_SHOP_PREFIXES = ("shop.", "order.", "buy.", "store.")


async def shop_products(tenant_id: str, category: Optional[str] = None) -> List[dict]:
    """In-stock active products, kept for a minute — a browse is several taps, each of which
    used to read the whole catalogue again (325 rows for SeedleMe)."""
    import time
    from vula.commerce import service
    fn = service.list_products
    key = (tenant_id, category or "")
    hit = _PRODUCTS.get(key)
    if hit and hit[2] is fn and time.monotonic() - hit[0] < PRODUCTS_TTL_S:
        return hit[1]
    try:
        rows = await fn(tenant_id, category=category, in_stock_only=True, statuses={"active"})
    except Exception:
        return hit[1] if hit and hit[2] is fn else []
    if len(_PRODUCTS) > 500:
        _PRODUCTS.clear()
    _PRODUCTS[key] = (time.monotonic(), rows, fn)
    return rows


def sized(url: Optional[str], width: int = 800) -> Optional[str]:
    """A phone-sized copy of a product photo where the image host can make one (Shopify's CDN
    resizes on request): cards and pages load in a fraction of the time on mobile data, and
    WhatsApp won't take a header image over 5 MB."""
    if not url or "cdn.shopify.com" not in url:
        return url
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k not in ("width", "height")] + [("width", str(width))]
    return urlunsplit(parts._replace(query=urlencode(query)))


def configured_shop_host(tenant_id: str) -> str:
    """The shop's own web address as configured, e.g. shop.seedleme.com — the first of the
    tenant's `domains` that starts with shop./order./buy./store. ('' when it has none)."""
    try:
        from vula.api.tenants import get_config
        doms = (get_config(tenant_id) or {}).get("domains") or []
    except Exception:
        return ""
    for d in doms:
        d = str(d or "").strip().lower()
        if d.startswith(_SHOP_PREFIXES):
            return d
    return ""


# Is the shop's own address actually working yet (DNS, certificate, pointing at us)? Checked in
# the background and remembered, so links switch over by themselves the moment it is — and
# until then stay on Vula's address, hidden behind the WhatsApp buttons (Ian, 13 Oct: "Vula
# automatically knows if the domain is done… it's one process, just not visible").
_READY: Dict[str, tuple] = {}
READY_TTL_S, NOT_READY_TTL_S = 6 * 3600.0, 300.0


async def check_shop_host(host: str, tenant_id: str) -> bool:
    import time
    import httpx
    ok = False
    try:
        async with httpx.AsyncClient(timeout=6.0, follow_redirects=False) as client:
            r = await client.get(f"https://{host}/shop-ping")
            ok = r.status_code == 200 and (r.json() or {}).get("shop") == tenant_id
    except Exception:
        ok = False
    _READY[host] = (time.monotonic(), ok)
    return ok


def _host_ready(host: str, tenant_id: str) -> bool:
    import asyncio
    import time
    hit = _READY.get(host)
    fresh = hit and time.monotonic() - hit[0] < (READY_TTL_S if hit[1] else NOT_READY_TTL_S)
    if not fresh:
        try:
            asyncio.get_running_loop().create_task(check_shop_host(host, tenant_id))
            _READY[host] = (time.monotonic(), bool(hit and hit[1]))    # one check at a time
        except RuntimeError:
            pass
    return bool(hit and hit[1])


def shop_host(tenant_id: str) -> str:
    """The shop's own address once it's live, else '' (links use Vula's, behind buttons)."""
    host = configured_shop_host(tenant_id)
    return host if host and _host_ready(host, tenant_id) else ""


def tenant_for_shop_host(host: str) -> Optional[str]:
    h = (host or "").strip().lower().split(":")[0]
    if not h.startswith(_SHOP_PREFIXES):
        return None
    try:
        from vula.api.tenants import tenant_for_host
        return tenant_for_host(h)
    except Exception:
        return None


def public_base(tenant_id: str) -> str:
    """Where this shop's pages live: its own address when it has one, else Vula's."""
    host = shop_host(tenant_id)
    if host:
        return f"https://{host}"
    from config import settings
    return (settings.public_base_url or "").rstrip("/")


def shop_url(tenant_id: str, anchor: str = "") -> str:
    host = shop_host(tenant_id)
    if host:
        return f"https://{host}/{anchor}"
    base = public_base(tenant_id)
    return f"{base}/menu/{tenant_id}{anchor}" if base else ""
