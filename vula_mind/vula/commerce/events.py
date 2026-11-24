"""Events and tickets (24 Nov 2026, migration 298).

A business puts on an event, sells tickets to it on a public page, and checks people in at the door.

How the money works — nothing new. Each ticket type is backed by a `commerce_products` row
(`product_type` 'ticket', never listed as goods), so buying tickets is an ordinary order: the same
basket, the same atomic stock hold (the hold IS the capacity — two buyers can't both take the last
seat), the same payment gateways (`commerce.checkout_for`), the same books. Prices are integer
cents read from the product row by the server; the page sends ticket-type ids and quantities only.

When the order is paid (`service.mark_order_paid` / a manual "paid"), one `commerce_tickets` row is
issued per ticket bought — once per order line and number (unique in the database), so two payment
notifications can't issue twice. Each ticket has its own unguessable code, shown as a QR on the
buyer's tickets page (`/tickets/<the order's signed token>`). A cancelled or fully refunded order
voids its tickets.

At the door a ticket is checked in by one conditional write (valid → checked_in for this business
and this event); a second scan is told when and by whom it was already used.

More than one ticket on an order: the buyer gets them all on one page, and each ticket also has its
own page (`/ticket/<code>`) with a "Send on WhatsApp" button, so the buyer passes each friend only
theirs. Vula never asks for the friends' numbers and never messages them: they haven't written to the
business, so WhatsApp wouldn't deliver it and they haven't agreed to be contacted.

Not here yet: free events, seat maps, selling at the till, a name per ticket.
"""
from __future__ import annotations

import logging
import re
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

STATUSES = ("draft", "on_sale", "closed", "cancelled")
CODE_PREFIX = "VT-"
MAX_TYPES = 12
MAX_PER_ORDER_CAP = 50
MAX_IMAGES, MAX_ADDONS, MAX_ADDON_QTY = 6, 12, 20
PAID_STATUSES = ("paid", "confirmed", "packing", "dispatched", "delivered")


class EventError(Exception):
    """Something asked for that can't be done as asked — `str()` is for the person; `status` is HTTP."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _db():
    from vula.commerce import service
    return service._client()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _when(raw: Any, what: str, required: bool = False) -> Optional[datetime]:
    if raw in (None, ""):
        if required:
            raise EventError(f"Say {what}.")
        return None
    try:
        dt = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise EventError(f"{what.capitalize()} isn't a date and time Vula can read.") from exc
    if dt.tzinfo is None:
        # A time typed in the dashboard with no zone is South African time.
        from zoneinfo import ZoneInfo
        dt = dt.replace(tzinfo=ZoneInfo("Africa/Johannesburg"))
    return dt


def _text(raw: Any, limit: int, one_line: bool = True) -> str:
    s = str(raw or "")
    s = re.sub(r"\s+", " ", s) if one_line else re.sub(r"[ \t]+", " ", s)
    return s.strip()[:limit].strip()


def _int(raw: Any, what: str, lo: int, hi: int) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise EventError(f"{what} must be a whole number.")
    if not lo <= raw <= hi:
        raise EventError(f"{what} must be between {lo:,} and {hi:,}.")
    return raw


def rand(cents: Any) -> str:
    c = int(cents or 0)
    return f"R{c // 100:,}.{c % 100:02d}"


# ── the event ─────────────────────────────────────────────────────────────────────────────────
def _clean_event(data: dict, current: Optional[dict] = None) -> dict:
    cur = current or {}
    out: Dict[str, Any] = {}
    if "title" in data or not current:
        out["title"] = _text(data.get("title"), 120)
        if not out["title"]:
            raise EventError("Give the event a name.")
    for key, limit, one in (("description", 4000, False), ("venue", 120, True), ("address", 200, True)):
        if key in data:
            out[key] = _text(data.get(key), limit, one) or None
    if "image_url" in data:
        img = _text(data.get("image_url"), 500)
        if img and not img.startswith("https://"):
            raise EventError("The picture's address must start with https://")
        out["image_url"] = img or None
    if "images" in data:
        imgs = data.get("images")
        if not isinstance(imgs, list) or len(imgs) > MAX_IMAGES:
            raise EventError(f"An event can have up to {MAX_IMAGES} photos.")
        clean = []
        for u in imgs:
            u = _text(u, 500)
            if not u.startswith("https://"):
                raise EventError("A photo's address must start with https://")
            if u not in clean:
                clean.append(u)
        out["images"] = clean
        out["image_url"] = clean[0] if clean else None          # the cover is the first photo
    if "addon_product_ids" in data:
        ids = data.get("addon_product_ids")
        if not isinstance(ids, list) or len(ids) > MAX_ADDONS or not all(isinstance(i, str) for i in ids):
            raise EventError(f"An event can offer up to {MAX_ADDONS} products with its tickets.")
        out["addon_product_ids"] = list(dict.fromkeys(ids))
    for key in ("remind_buyers", "thank_buyers"):
        if key in data:
            if not isinstance(data[key], bool):
                raise EventError("Reminders and thank-yous are either on or off.")
            out[key] = data[key]
    if "starts_at" in data or not current:
        out["starts_at"] = _iso(_when(data.get("starts_at"), "when the event starts", required=True))
    for key, what in (("ends_at", "when the event ends"), ("sales_close_at", "when ticket sales close")):
        if key in data:
            dt = _when(data.get(key), what)
            out[key] = _iso(dt) if dt else None
    starts = _when(out.get("starts_at") or cur.get("starts_at"), "when the event starts", required=True)
    ends = _when(out["ends_at"] if "ends_at" in out else cur.get("ends_at"), "when the event ends")
    if ends and ends <= starts:
        raise EventError("The event can't end before it starts.")
    return out


def _event_row(tenant_id: str, event_id: str) -> dict:
    rows = (_db().table("commerce_events").select("*").eq("tenant_id", tenant_id).eq("id", event_id)
            .limit(1).execute().data or [])
    if not rows:
        raise EventError("That event isn't on file.", 404)
    return rows[0]


def _type_rows(tenant_id: str, event_id: str, include_archived: bool = False) -> List[dict]:
    rows = (_db().table("commerce_ticket_types").select("*").eq("tenant_id", tenant_id)
            .eq("event_id", event_id).order("sort_order").limit(200).execute().data or [])
    return [r for r in rows if include_archived or not r.get("archived")]


def _products(tenant_id: str, ids: List[str]) -> Dict[str, dict]:
    if not ids:
        return {}
    rows = (_db().table("commerce_products").select("id,name,price_cents,stock_quantity,in_stock")
            .eq("tenant_id", tenant_id).in_("id", ids).execute().data or [])
    return {r["id"]: r for r in rows}


def _tickets(tenant_id: str, event_id: str) -> List[dict]:
    from vula.db_pages import all_rows
    return all_rows(lambda: _db().table("commerce_tickets").select("*").eq("tenant_id", tenant_id)
                    .eq("event_id", event_id))


def addons_for(tenant_id: str, ids: Any) -> List[dict]:
    """The products an event offers with its tickets, as they can be sold right now: this
    business's own, active, in stock, at one price each (a product with options or sold by weight
    is bought in the shop, where it can be chosen properly). Prices are the product rows'."""
    from vula.commerce import service
    ids = [i for i in (ids or []) if isinstance(i, str)][:MAX_ADDONS]
    if not ids:
        return []
    rows = (_db().table("commerce_products").select("*").eq("tenant_id", tenant_id).in_("id", ids).execute().data or [])
    with_options = {v["product_id"] for v in (_db().table("commerce_product_variants").select("product_id,archived")
                                              .eq("tenant_id", tenant_id).in_("product_id", ids).execute().data or [])
                    if not v.get("archived")}
    by_id = {r["id"]: r for r in rows}
    out = []
    for pid in ids:
        p = by_id.get(pid)
        if (not p or p.get("archived") or p.get("product_type") == service.TICKET_PRODUCT_TYPE
                or (p.get("status") or "active") != "active" or not p.get("in_stock") or pid in with_options
                or p.get("choices") or p.get("pricing_mode") == "per_kg" or p.get("sold_by") == "kg"):
            continue
        price = int(service.effective_price_cents(p) or 0)
        if price <= 0:
            continue
        stock = p.get("stock_quantity")
        most = MAX_ADDON_QTY if stock is None else min(MAX_ADDON_QTY, max(0, int(float(stock))))
        if most < 1:
            continue
        img = str(p.get("image_url") or "")
        out.append({"id": pid, "name": p.get("name") or "Item", "price_cents": price,
                    "image": img if img.startswith("https://") else "", "max": most})
    return out


def _check_addons(tenant_id: str, ids: List[str]) -> None:
    """Every product named is this business's own and not a ticket. (Whether it can be sold today
    is decided when the page is shown.)"""
    from vula.commerce import service
    if not ids:
        return
    rows = (_db().table("commerce_products").select("id,product_type").eq("tenant_id", tenant_id)
            .in_("id", ids).execute().data or [])
    ok = {r["id"] for r in rows if r.get("product_type") != service.TICKET_PRODUCT_TYPE}
    if any(i not in ok for i in ids):
        raise EventError("One of those products isn't in your product list.")


def _left(product: Optional[dict]) -> int:
    try:
        return max(0, int(float((product or {}).get("stock_quantity") or 0)))
    except (TypeError, ValueError):
        return 0


def _with_types(tenant_id: str, event: dict, tickets: Optional[List[dict]] = None) -> dict:
    types = _type_rows(tenant_id, event["id"])
    prods = _products(tenant_id, [t["product_id"] for t in types])
    tickets = _tickets(tenant_id, event["id"]) if tickets is None else tickets
    out_types = []
    for t in types:
        mine = [k for k in tickets if k.get("ticket_type_id") == t["id"] and k.get("status") != "void"]
        p = prods.get(t["product_id"])
        left = _left(p)
        sold = len(mine)
        out_types.append({
            "id": t["id"], "name": t["name"], "price_cents": int((p or {}).get("price_cents") or 0),
            "capacity": int(t["capacity"]), "max_per_order": int(t["max_per_order"]),
            "sold": sold, "checked_in": sum(1 for k in mine if k.get("status") == "checked_in"),
            "left": left,
            # bought but not paid yet: the order holds the seats until it is paid or lapses
            "held": max(0, int(t["capacity"]) - sold - left),
        })
    live = [k for k in tickets if k.get("status") != "void"]
    return {**event, "types": out_types, "sold": len(live),
            "checked_in": sum(1 for k in live if k.get("status") == "checked_in"),
            "capacity": sum(t["capacity"] for t in out_types),
            "takings_cents": None, "selling": selling(event), "url": public_url(tenant_id, event["id"])}


def selling(event: dict) -> bool:
    """Tickets can be bought right now: on sale, and sales haven't closed (their own closing time,
    else the end of the event, else its start)."""
    if event.get("status") != "on_sale":
        return False
    close = event.get("sales_close_at") or event.get("ends_at") or event.get("starts_at")
    try:
        return _now() < _when(close, "the closing time", required=True)
    except EventError:
        return False


def public_url(tenant_id: str, event_id: str) -> str:
    from vula.commerce import web_basket
    base = web_basket.public_base(tenant_id)
    return f"{base}/events/{event_id}" if base else ""


def list_events(tenant_id: str) -> List[dict]:
    rows = (_db().table("commerce_events").select("*").eq("tenant_id", tenant_id)
            .order("starts_at", desc=True).limit(200).execute().data or [])
    return [_with_types(tenant_id, r) for r in rows]


def get(tenant_id: str, event_id: str) -> dict:
    return _with_types(tenant_id, _event_row(tenant_id, event_id))


async def create(tenant_id: str, data: dict, actor: str = "") -> dict:
    row = {"id": str(uuid.uuid4()), "tenant_id": tenant_id, "status": "draft", "created_by": actor or None,
           **_clean_event(data or {})}
    _check_addons(tenant_id, row.get("addon_product_ids") or [])
    _db().table("commerce_events").insert(row).execute()
    if (data or {}).get("types") is not None:
        await save_types(tenant_id, row["id"], data["types"])
    return get(tenant_id, row["id"])


async def update(tenant_id: str, event_id: str, data: dict) -> dict:
    cur = _event_row(tenant_id, event_id)
    patch = _clean_event({k: v for k, v in (data or {}).items() if k != "types"}, cur)
    _check_addons(tenant_id, patch.get("addon_product_ids") or [])
    if patch:
        _db().table("commerce_events").update({**patch, "updated_at": _iso(_now())}) \
            .eq("tenant_id", tenant_id).eq("id", event_id).execute()
    if (data or {}).get("types") is not None:
        await save_types(tenant_id, event_id, data["types"])
    if patch.get("title") and patch["title"] != cur.get("title"):
        _rename_products(tenant_id, event_id, patch["title"])
    return get(tenant_id, event_id)


async def set_status(tenant_id: str, event_id: str, status: str) -> dict:
    """draft → on_sale (needs a ticket type and a future start) → closed; any → cancelled. A
    cancelled event stops selling; its paid orders are refunded one by one in Orders, which voids
    their tickets — money is never moved from here."""
    if status not in STATUSES:
        raise EventError("Status must be draft, on_sale, closed or cancelled.")
    ev = get(tenant_id, event_id)
    if ev["status"] == "cancelled" and status != "cancelled":
        raise EventError("A cancelled event stays cancelled. Make a new one.")
    if status == "draft" and ev["sold"]:
        raise EventError("Tickets have been sold, so this can't go back to a draft. Close sales instead.")
    if status == "on_sale":
        if not ev["types"]:
            raise EventError("Add at least one ticket type first.")
        end = _when(ev.get("ends_at") or ev.get("starts_at"), "the event's time", required=True)
        if end <= _now():
            raise EventError("This event is in the past.")
    _db().table("commerce_events").update({"status": status, "updated_at": _iso(_now())}) \
        .eq("tenant_id", tenant_id).eq("id", event_id).execute()
    back = get(tenant_id, event_id)
    if back["status"] != status:
        raise EventError("The change didn't save. Nothing changed.", 500)
    return back


# ── ticket types ──────────────────────────────────────────────────────────────────────────────
def _product_name(title: str, type_name: str) -> str:
    return f"{title} — {type_name}"[:200]


def _rename_products(tenant_id: str, event_id: str, title: str) -> None:
    for t in _type_rows(tenant_id, event_id, include_archived=True):
        _db().table("commerce_products").update({"name": _product_name(title, t["name"])}) \
            .eq("tenant_id", tenant_id).eq("id", t["product_id"]).execute()


def _clean_type(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise EventError("Each ticket type needs a name, a price and how many there are.")
    name = _text(raw.get("name"), 60)
    if not name:
        raise EventError("Give each ticket type a name, like General or VIP.")
    price = _int(raw.get("price_cents"), f"The price of {name}", 100, 10_000_000)
    return {"id": str(raw.get("id") or "") or None, "name": name, "price_cents": price,
            "capacity": _int(raw.get("capacity"), f"How many {name} tickets", 0, 100_000),
            "max_per_order": _int(raw.get("max_per_order", 10), f"The most {name} tickets per order",
                                  1, MAX_PER_ORDER_CAP)}


async def save_types(tenant_id: str, event_id: str, raw_types: Any) -> None:
    """The event's ticket types as given: new ones are created (with the product they are sold as),
    existing ones changed, and ones left out are withdrawn — only if none were sold. Capacity can
    go up freely and down only as far as what is still unsold and unheld."""
    from vula.commerce import service
    if not isinstance(raw_types, list) or len(raw_types) > MAX_TYPES:
        raise EventError(f"An event can have up to {MAX_TYPES} ticket types.")
    wanted = [_clean_type(t) for t in raw_types]
    if len({w["name"].lower() for w in wanted}) != len(wanted):
        raise EventError("Two ticket types have the same name.")
    event = _event_row(tenant_id, event_id)
    existing = {t["id"]: t for t in _type_rows(tenant_id, event_id)}
    unknown = [w["id"] for w in wanted if w["id"] and w["id"] not in existing]
    if unknown:
        raise EventError("One of those ticket types isn't part of this event.")
    tickets = _tickets(tenant_id, event_id)
    sold_by = {}
    for k in tickets:
        if k.get("status") != "void":
            sold_by[k["ticket_type_id"]] = sold_by.get(k["ticket_type_id"], 0) + 1
    db = _db()
    keep = {w["id"] for w in wanted if w["id"]}
    for tid, t in existing.items():
        if tid in keep:
            continue
        prod = _products(tenant_id, [t["product_id"]]).get(t["product_id"])
        if sold_by.get(tid) or _left(prod) < int(t["capacity"]):
            raise EventError(f"{t['name']} tickets have been sold or are being paid for, so it can't be removed. "
                             "Set how many are left to what's been sold instead.")
        db.table("commerce_ticket_types").update({"archived": True, "updated_at": _iso(_now())}) \
            .eq("tenant_id", tenant_id).eq("id", tid).execute()
        db.table("commerce_products").update({"archived": True, "in_stock": False, "status": "archived"}) \
            .eq("tenant_id", tenant_id).eq("id", t["product_id"]).execute()
    for n, w in enumerate(wanted):
        if not w["id"]:
            product = await service.create_product(tenant_id, {
                "name": _product_name(event["title"], w["name"]), "price_cents": w["price_cents"],
                "stock_quantity": w["capacity"], "in_stock": w["capacity"] > 0, "category": "tickets",
                "product_type": service.TICKET_PRODUCT_TYPE, "status": "unlisted"})
            db.table("commerce_ticket_types").insert({
                "id": str(uuid.uuid4()), "tenant_id": tenant_id, "event_id": event_id,
                "product_id": product["id"], "name": w["name"], "capacity": w["capacity"],
                "max_per_order": w["max_per_order"], "sort_order": n}).execute()
            continue
        t = existing[w["id"]]
        delta = w["capacity"] - int(t["capacity"])
        if delta < 0:
            # Taking seats away is the same atomic hold a sale makes: it fails if they're gone.
            ok = db.rpc("reserve_product_stock_exact", {"p_tenant_id": tenant_id, "p_product_id": t["product_id"],
                                                         "p_qty": -delta}).execute().data
            if not ok:
                raise EventError(f"{w['name']}: that many tickets are already sold or being paid for.")
        elif delta > 0:
            await service.update_product_stock(tenant_id, t["product_id"], -delta)
        db.table("commerce_products").update({"name": _product_name(event["title"], w["name"]),
                                              "price_cents": w["price_cents"]}) \
            .eq("tenant_id", tenant_id).eq("id", t["product_id"]).execute()
        db.table("commerce_ticket_types").update({
            "name": w["name"], "capacity": w["capacity"], "max_per_order": w["max_per_order"],
            "sort_order": n, "updated_at": _iso(_now())}).eq("tenant_id", tenant_id).eq("id", t["id"]).execute()


# ── buying (the public page) ──────────────────────────────────────────────────────────────────
def public_event(event_id: str) -> Optional[dict]:
    """What the public page shows — None for an event that doesn't exist or is still a draft."""
    try:
        uuid.UUID(str(event_id))
    except ValueError:
        return None
    rows = _db().table("commerce_events").select("*").eq("id", str(event_id)).limit(1).execute().data or []
    if not rows or rows[0].get("status") == "draft":
        return None
    ev = rows[0]
    from vula.api import tenants
    if not tenants.is_active(ev["tenant_id"]):
        return None
    full = _with_types(ev["tenant_id"], ev, tickets=[])
    return {"id": ev["id"], "tenant_id": ev["tenant_id"], "title": ev["title"],
            "description": ev.get("description") or "", "venue": ev.get("venue") or "",
            "address": ev.get("address") or "", "starts_at": ev["starts_at"], "ends_at": ev.get("ends_at"),
            "image_url": ev.get("image_url") or "", "status": ev["status"], "selling": full["selling"],
            "images": [u for u in (ev.get("images") or []) if isinstance(u, str) and u.startswith("https://")],
            "addons": addons_for(ev["tenant_id"], ev.get("addon_product_ids")) if full["selling"] else [],
            "url": full["url"],
            "types": [{"id": t["id"], "name": t["name"], "price_cents": t["price_cents"], "left": t["left"],
                       "max_per_order": t["max_per_order"]} for t in full["types"]]}


def _phone(raw: Any) -> str:
    digits = re.sub(r"[^\d+]", "", str(raw or ""))
    plain = digits.lstrip("+")
    if not 9 <= len(plain) <= 15 or not plain.isdigit():
        raise EventError("Enter a cellphone number we can send your tickets to.")
    return "27" + plain[1:] if plain.startswith("0") and len(plain) == 10 else plain


def _clean_buy(event: dict, raw_items: Any) -> List[dict]:
    if not isinstance(raw_items, list) or not raw_items:
        raise EventError("Choose at least one ticket.")
    types = {t["id"]: t for t in event["types"]}
    want: Dict[str, int] = {}
    for it in raw_items[:MAX_TYPES * 2]:
        if not isinstance(it, dict):
            continue
        tid, q = str(it.get("type_id") or ""), it.get("quantity")
        if isinstance(q, bool) or not isinstance(q, int) or q < 0:
            raise EventError("How many tickets must be a whole number.")
        if q == 0:
            continue
        if tid not in types:
            raise EventError("One of those tickets isn't part of this event.")
        want[tid] = want.get(tid, 0) + q
    if not want:
        raise EventError("Choose at least one ticket.")
    out = []
    for tid, q in want.items():
        t = types[tid]
        if q > t["max_per_order"]:
            raise EventError(f"You can buy up to {t['max_per_order']} {t['name']} tickets at a time.")
        if q > t["left"]:
            raise EventError(f"Only {t['left']} {t['name']} ticket{'' if t['left'] == 1 else 's'} left."
                             if t["left"] else f"{t['name']} is sold out.", 409)
        out.append({"type": t, "quantity": q})
    return out


def _clean_addons(event: dict, raw: Any) -> List[dict]:
    """What the buyer added to their tickets: only products this event offers, whole numbers,
    within what is in stock."""
    if raw in (None, []):
        return []
    if not isinstance(raw, list):
        raise EventError("That order couldn't be read. Refresh the page and try again.")
    offered = {a["id"]: a for a in event.get("addons") or []}
    want: Dict[str, int] = {}
    for it in raw[:MAX_ADDONS * 2]:
        if not isinstance(it, dict):
            continue
        pid, q = str(it.get("product_id") or ""), it.get("quantity")
        if isinstance(q, bool) or not isinstance(q, int) or q < 0:
            raise EventError("How many of each item must be a whole number.")
        if q == 0:
            continue
        if pid not in offered:
            raise EventError("One of those items isn't offered with this event any more. Refresh the page.")
        want[pid] = want.get(pid, 0) + q
    out = []
    for pid, q in want.items():
        a = offered[pid]
        if q > a["max"]:
            raise EventError(f"Only {a['max']} of {a['name']} can be added.", 409)
        out.append({"product_id": pid, "quantity": q})
    return out


async def buy(event_id: str, *, items: Any, name: Any, phone: Any, email: Any = "", addons: Any = None) -> dict:
    """Start a ticket purchase: an ordinary order for the chosen tickets (seats held from now),
    and the address of the business's payment page. Prices come from the product rows."""
    from fastapi import HTTPException
    from vula.api import commerce as commerce_api
    from vula.commerce import order_slip, service, web_basket
    event = public_event(event_id)
    if not event:
        raise EventError("That event isn't on sale.", 404)
    if not event["selling"]:
        raise EventError("Ticket sales for this event are closed.", 409)
    tenant_id = event["tenant_id"]
    buyer = _text(name, 80)
    if len(buyer) < 2:
        raise EventError("Enter your name.")
    number = _phone(phone)
    mail = _text(email, 200)
    if mail and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", mail):
        raise EventError("That email address doesn't look right.")
    lines = _clean_buy(event, items)
    extras = _clean_addons(event, addons)
    session = uuid.uuid4().hex
    cart = await service.get_or_create_cart(tenant_id, session, customer_phone=number)
    type_rows = {t["id"]: t for t in _type_rows(tenant_id, event["id"])}
    for ln in lines:
        await service.add_to_cart(tenant_id, cart["id"], type_rows[ln["type"]["id"]]["product_id"], ln["quantity"])
    for ex in extras:
        await service.add_to_cart(tenant_id, cart["id"], ex["product_id"], ex["quantity"])

    base = web_basket.public_base(tenant_id)

    def return_to(order: dict):
        page = f"{base}/tickets/{order_slip.token_for(order['id'])}"
        return page, f"{base}/events/{event['id']}?cancelled=1"

    body = commerce_api.CheckoutRequest(
        session_id=session, customer_name=buyer, customer_phone=number, customer_email=mail or None,
        delivery_address=f"Tickets: {event['title']}"[:200], channel="tickets")
    try:
        out = await commerce_api.checkout_for(tenant_id, body, extra={"fulfilment": "collection"},
                                              return_to=return_to)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else "That didn't go through. Try again."
        if exc.status_code == 409:
            detail = "Those tickets were just taken. Check how many are left and try again."
        raise EventError(detail, exc.status_code) from exc
    return {"redirect_url": out["redirect_url"], "order": out["display_id"], "total_cents": out["total_cents"]}


# ── tickets ───────────────────────────────────────────────────────────────────────────────────
def _new_code() -> str:
    return CODE_PREFIX + secrets.token_urlsafe(16)


def clean_code(raw: Any) -> str:
    """What a scanner or a typed code gives, as the stored code ('' when it can't be one)."""
    s = str(raw or "").strip()
    s = s.rsplit("/", 1)[-1] if "/" in s else s
    return s if re.fullmatch(r"VT-[A-Za-z0-9_-]{16,40}", s) else ""


def _order_items(order_id: str) -> List[dict]:
    return (_db().table("commerce_order_items").select("id,product_id,quantity,product_name")
            .eq("order_id", order_id).limit(200).execute().data or [])


async def issue_for_order(tenant_id: str, order: dict) -> List[dict]:
    """The paid order's tickets, issuing any not yet issued. Safe to call again: a ticket exists
    once per order line and number, enforced by the database. Returns the ones made by THIS call,
    and tells the buyer where their tickets are when there are any."""
    order_id = (order or {}).get("id")
    if not tenant_id or not order_id:
        return []
    items = _order_items(order_id)
    pids = [i["product_id"] for i in items if i.get("product_id")]
    if not pids:
        return []
    types = (_db().table("commerce_ticket_types").select("id,event_id,product_id,name")
             .eq("tenant_id", tenant_id).in_("product_id", pids).execute().data or [])
    if not types:
        return []
    by_product = {t["product_id"]: t for t in types}
    have = {(k["order_item_id"], int(k["seq"])) for k in
            (_db().table("commerce_tickets").select("order_item_id,seq").eq("tenant_id", tenant_id)
             .eq("order_id", order_id).limit(900).execute().data or [])}
    made: List[dict] = []
    for it in items:
        t = by_product.get(it.get("product_id"))
        if not t:
            continue
        for seq in range(1, int(float(it.get("quantity") or 0)) + 1):
            if (it["id"], seq) in have:
                continue
            row = {"id": str(uuid.uuid4()), "tenant_id": tenant_id, "event_id": t["event_id"],
                   "ticket_type_id": t["id"], "order_id": order_id, "order_item_id": it["id"], "seq": seq,
                   "code": _new_code(), "holder_name": order.get("customer_name"),
                   "holder_phone": order.get("customer_phone"), "status": "valid"}
            try:
                _db().table("commerce_tickets").insert(row).execute()
                made.append(row)
            except Exception as exc:  # noqa: BLE001 — another call issued it first (unique line+number)
                log.info("ticket %s/%s for order %s already issued: %s", it["id"], seq, order_id, type(exc).__name__)
    if made:
        log.info("tickets issued: %s order %s × %d", tenant_id, order.get("display_id"), len(made))
        await _tell_buyer(tenant_id, order, len(made))
    return made


def void_for_order(tenant_id: str, order_id: str, reason: str) -> int:
    """The order was cancelled or fully refunded: its tickets no longer get anyone in."""
    if not tenant_id or not order_id:
        return 0
    res = (_db().table("commerce_tickets").update({"status": "void", "voided_at": _iso(_now()),
                                                   "void_reason": _text(reason, 80)})
           .eq("tenant_id", tenant_id).eq("order_id", order_id).neq("status", "void").execute())
    return len(res.data or [])


def tickets_url(tenant_id: str, order_id: str) -> str:
    from vula.commerce import order_slip, web_basket
    base = web_basket.public_base(tenant_id)
    return f"{base}/tickets/{order_slip.token_for(order_id)}" if base else ""


async def _tell_buyer(tenant_id: str, order: dict, count: int) -> bool:
    """One WhatsApp message with a button to the tickets page; outside the 24-hour window the
    shop's approved order-update template carries the news. Never raises: the buyer is also
    returned to the tickets page by the payment gateway."""
    phone = order.get("customer_phone")
    link = tickets_url(tenant_id, order["id"])
    if not phone or not link:
        return False
    try:
        from vula.api import tenants
        from vula.api.whatsapp import _resolve_wa, _send_wa_cta_url, _wa_number, send_template
        first = (order.get("customer_name") or "").split(" ")[0]
        news = f"Your {count} ticket{'' if count == 1 else 's'} for order {order.get('display_id') or ''} " \
               f"{'is' if count == 1 else 'are'} ready."
        text = (f"Hi {first}! " if first else "") + f"🎟️ {news} Show the QR code at the door." \
            + f"\n\n— {tenants.display_name(tenant_id)}"
        creds = await _resolve_wa(tenant_id)
        sent = bool(creds) and await _send_wa_cta_url(creds, _wa_number(phone), text, "View tickets", link)
        if not sent:
            from vula.commerce.job_config import template_name
            sent = await send_template(tenant_id, phone, template_name(tenant_id, "order_update"),
                                       [order.get("customer_name") or "there", order.get("display_id") or "", news])
        return bool(sent)
    except Exception as exc:  # noqa: BLE001
        log.warning("ticket message for %s %s failed: %s", tenant_id, order.get("display_id"), exc)
        return False


async def order_tickets(token: str) -> Optional[dict]:
    """The buyer's tickets page, from the order's signed token: the order, its event(s) and
    tickets. A paid order whose tickets weren't issued yet (the hook failed) gets them now."""
    from vula.commerce import order_slip, service
    order_id = order_slip.order_id_from(token)
    if not order_id:
        return None
    try:
        order = await service.get_order(order_id)
    except Exception:  # noqa: BLE001
        order = None
    if not order:
        return None
    tenant_id = order["tenant_id"]
    paid = order.get("status") in PAID_STATUSES
    if paid:
        await issue_for_order(tenant_id, order)
    tickets = (_db().table("commerce_tickets").select("*").eq("tenant_id", tenant_id)
               .eq("order_id", order_id).order("seq").limit(900).execute().data or [])
    if not tickets and not paid and order.get("channel") != "tickets":
        return None
    type_ids = sorted({k["ticket_type_id"] for k in tickets})
    names = {t["id"]: t["name"] for t in (_db().table("commerce_ticket_types").select("id,name")
                                          .eq("tenant_id", tenant_id).in_("id", type_ids).execute().data or [])} \
        if type_ids else {}
    event_ids = sorted({k["event_id"] for k in tickets})
    events = {e["id"]: e for e in (_db().table("commerce_events").select("*").eq("tenant_id", tenant_id)
                                   .in_("id", event_ids).execute().data or [])} if event_ids else {}
    return {"tenant_id": tenant_id, "paid": paid, "status": order.get("status"),
            "order": order.get("display_id"), "name": order.get("customer_name") or "",
            "tickets": [{"code": k["code"], "status": k["status"], "type": names.get(k["ticket_type_id"], "Ticket"),
                         "event": events.get(k["event_id"], {}), "url": ticket_url(tenant_id, k["code"])}
                        for k in tickets]}


def ticket_url(tenant_id: str, code: str) -> str:
    """One ticket's own page — what the buyer sends a friend, so each person carries only theirs."""
    from vula.commerce import web_basket
    base = web_basket.public_base(tenant_id)
    return f"{base}/ticket/{code}" if base else ""


def single_ticket(raw_code: Any) -> Optional[dict]:
    """One ticket by its code (the code is the ticket: whoever holds the link holds the ticket)."""
    code = clean_code(raw_code)
    if not code:
        return None
    rows = _db().table("commerce_tickets").select("*").eq("code", code).limit(1).execute().data or []
    if not rows:
        return None
    k = rows[0]
    tenant_id = k["tenant_id"]
    ev = (_db().table("commerce_events").select("*").eq("tenant_id", tenant_id).eq("id", k["event_id"])
          .limit(1).execute().data or [{}])[0]
    return {"tenant_id": tenant_id, "code": k["code"], "status": k["status"],
            "type": _ticket_out(tenant_id, k)["type"], "event": ev}


def qr_svg(code: str) -> str:
    import segno
    return segno.make(code, error="m").svg_inline(scale=6, border=2, dark="#000", light="#fff")


# ── the door ──────────────────────────────────────────────────────────────────────────────────
def _ticket_out(tenant_id: str, k: dict) -> dict:
    types = (_db().table("commerce_ticket_types").select("id,name").eq("tenant_id", tenant_id)
             .eq("id", k["ticket_type_id"]).limit(1).execute().data or [{}])
    return {"id": k["id"], "holder_name": k.get("holder_name") or "", "type": types[0].get("name") or "Ticket",
            "status": k["status"], "checked_in_at": k.get("checked_in_at"), "checked_in_by": k.get("checked_in_by")}


def check_in(tenant_id: str, event_id: str, raw_code: Any, by: str = "", at: Optional[str] = None) -> dict:
    """Let one ticket in. `result`: ok | already | void | wrong_event | unknown. The write only
    succeeds for a valid ticket of this business and this event, so two doors scanning the same
    ticket at once let it in once."""
    code = clean_code(raw_code)
    if not code:
        return {"result": "unknown", "message": "That isn't one of our ticket codes."}
    _event_row(tenant_id, event_id)
    now = at or _iso(_now())
    res = (_db().table("commerce_tickets").update({"status": "checked_in", "checked_in_at": now,
                                                   "checked_in_by": _text(by, 120) or None})
           .eq("tenant_id", tenant_id).eq("event_id", event_id).eq("code", code).eq("status", "valid").execute())
    if res.data:
        return {"result": "ok", "message": "Welcome in.", "ticket": _ticket_out(tenant_id, res.data[0])}
    rows = (_db().table("commerce_tickets").select("*").eq("tenant_id", tenant_id).eq("code", code)
            .limit(1).execute().data or [])
    if not rows:
        return {"result": "unknown", "message": "No ticket with that code."}
    k = rows[0]
    if k["event_id"] != event_id:
        return {"result": "wrong_event", "message": "This ticket is for a different event."}
    out = _ticket_out(tenant_id, k)
    if k["status"] == "void":
        return {"result": "void", "message": "This ticket was cancelled or refunded.", "ticket": out}
    return {"result": "already", "message": "This ticket has already been used.", "ticket": out}


def check_in_ticket(tenant_id: str, event_id: str, ticket_id: Any, by: str = "", at: Optional[str] = None) -> dict:
    """Let in a ticket picked from the door list by name (a phone that died, a lost message): the
    same single write as a scan, found by the ticket's id instead of its code."""
    try:
        tid = str(uuid.UUID(str(ticket_id)))
    except ValueError:
        return {"result": "unknown", "message": "That ticket isn't on the list."}
    rows = (_db().table("commerce_tickets").select("code").eq("tenant_id", tenant_id).eq("event_id", event_id)
            .eq("id", tid).limit(1).execute().data or [])
    if not rows:
        return {"result": "unknown", "message": "That ticket isn't on the list."}
    return check_in(tenant_id, event_id, rows[0]["code"], by, at=at)


def undo_check_in(tenant_id: str, event_id: str, ticket_id: str) -> dict:
    """A check-in made by mistake: checked_in → valid. Owner or manager only (the route checks)."""
    res = (_db().table("commerce_tickets").update({"status": "valid", "checked_in_at": None, "checked_in_by": None})
           .eq("tenant_id", tenant_id).eq("event_id", event_id).eq("id", ticket_id).eq("status", "checked_in")
           .execute())
    if not res.data:
        raise EventError("That ticket isn't checked in.", 409)
    return _ticket_out(tenant_id, res.data[0])


def attendees(tenant_id: str, event_id: str) -> dict:
    """Everyone with a ticket, for the door list: name, ticket type, order, state. No codes — a
    code is the ticket."""
    ev = get(tenant_id, event_id)
    tickets = _tickets(tenant_id, event_id)
    names = {t["id"]: t["name"] for t in _type_rows(tenant_id, event_id, include_archived=True)}
    order_ids = sorted({k["order_id"] for k in tickets})
    orders: Dict[str, dict] = {}
    for i in range(0, len(order_ids), 200):
        for o in (_db().table("commerce_orders").select("id,display_id,total_cents,status,refunded_amount_cents")
                  .eq("tenant_id", tenant_id).in_("id", order_ids[i:i + 200]).execute().data or []):
            orders[o["id"]] = o
    paid = sum(int(o.get("total_cents") or 0) - int(o.get("refunded_amount_cents") or 0)
               for o in orders.values() if o.get("status") in PAID_STATUSES)
    rows = [{"id": k["id"], "holder_name": k.get("holder_name") or "", "holder_phone": k.get("holder_phone") or "",
             "type": names.get(k["ticket_type_id"], "Ticket"), "status": k["status"],
             "order": (orders.get(k["order_id"]) or {}).get("display_id") or "",
             "checked_in_at": k.get("checked_in_at"), "checked_in_by": k.get("checked_in_by")}
            for k in tickets]
    rows.sort(key=lambda r: (r["holder_name"].lower(), r["order"], r["type"]))
    return {"event": {**ev, "takings_cents": paid}, "attendees": rows}


# ── the door link ─────────────────────────────────────────────────────────────────────────────
# The people on the door are often hired for the night. They get no login: the owner makes a door
# link for the event, and whoever opens it can do exactly two things for that one event — see who
# is coming (names and ticket types, no phone numbers, no money) and let tickets in. The link stops
# working when the owner turns it off or 12 hours after the event ends. The door page keeps the list
# on the phone, so it goes on working with no signal and sends its check-ins when the signal is back.
DOOR_GRACE_HOURS = 12
DOOR_OPENS_HOURS_BEFORE = 24


def _door_url(tenant_id: str, key: str) -> str:
    from vula.api import tenants
    return f"{tenants.dashboard_url_for(tenant_id)}/door/#{key}"


def door_link(tenant_id: str, event_id: str, *, new: bool = False) -> dict:
    """The event's door link, making one when there is none (or `new`: the old link stops working)."""
    ev = _event_row(tenant_id, event_id)
    if ev.get("status") in ("draft", "cancelled"):
        raise EventError("Put the event on sale before making a door link.")
    key = ev.get("door_key")
    if new or not key:
        key = "D" + secrets.token_urlsafe(24)
        _db().table("commerce_events").update({"door_key": key}).eq("tenant_id", tenant_id).eq("id", event_id).execute()
        if _event_row(tenant_id, event_id).get("door_key") != key:
            raise EventError("The door link didn't save. Nothing changed.", 500)
    return {"url": _door_url(tenant_id, key), "on": True}


def door_off(tenant_id: str, event_id: str) -> dict:
    _db().table("commerce_events").update({"door_key": None}).eq("tenant_id", tenant_id).eq("id", event_id).execute()
    if _event_row(tenant_id, event_id).get("door_key"):
        raise EventError("The door link is still on. Try again.", 500)
    return {"url": "", "on": False}


def door_status(tenant_id: str, event_id: str) -> dict:
    key = _event_row(tenant_id, event_id).get("door_key")
    return {"url": _door_url(tenant_id, key) if key else "", "on": bool(key)}


def _door_event(raw_key: Any) -> dict:
    """The event a door key opens. EventError(404) for anything else — a wrong key, a link turned
    off, an event that ended more than 12 hours ago — all the same answer."""
    key = str(raw_key or "").strip()
    gone = EventError("This door link isn't working. Ask the organiser for a new one.", 404)
    if not re.fullmatch(r"D[A-Za-z0-9_-]{20,60}", key):
        raise gone
    rows = _db().table("commerce_events").select("*").eq("door_key", key).limit(1).execute().data or []
    if not rows or rows[0].get("status") in ("draft", "cancelled"):
        raise gone
    ev = rows[0]
    from datetime import timedelta
    from vula.api import tenants
    end = _when(ev.get("ends_at") or ev.get("starts_at"), "the event's time", required=True)
    if _now() > end + timedelta(hours=DOOR_GRACE_HOURS) or not tenants.is_active(ev["tenant_id"]):
        raise gone
    return ev


def _code_hash(code: str) -> str:
    import hashlib
    return hashlib.sha256(code.encode()).hexdigest()


def door_list(raw_key: Any) -> dict:
    """What the door page keeps on the phone: the event, and every live ticket as a name, a type,
    its state and the SHA-256 of its code — so a scanned code can be matched with no signal, while
    the phone never holds a code it hasn't been shown."""
    ev = _door_event(raw_key)
    tenant_id = ev["tenant_id"]
    names = {t["id"]: t["name"] for t in _type_rows(tenant_id, ev["id"], include_archived=True)}
    rows = [{"id": k["id"], "h": _code_hash(k["code"]), "name": k.get("holder_name") or "",
             "type": names.get(k["ticket_type_id"], "Ticket"), "status": k["status"],
             "at": k.get("checked_in_at"), "by": k.get("checked_in_by")}
            for k in _tickets(tenant_id, ev["id"]) if k.get("status") != "void"]
    rows.sort(key=lambda r: (r["name"].lower(), r["type"], r["id"]))
    return {"event": {"title": ev["title"], "starts_at": ev["starts_at"], "ends_at": ev.get("ends_at"),
                      "venue": ev.get("venue") or ""},
            "tickets": rows, "sold": len(rows), "checked_in": sum(1 for r in rows if r["status"] == "checked_in"),
            "as_at": _iso(_now())}


def _door_by(device: Any) -> str:
    label = _text(device, 40)
    return f"Door: {label}" if label else "Door"


def _scan_time(raw: Any) -> Optional[str]:
    """When the door phone says it scanned a ticket while it had no signal — kept only when it is
    a real time in the last two days and not in the future; otherwise the server's own clock."""
    from datetime import timedelta
    try:
        dt = _when(raw, "the scan time")
    except EventError:
        return None
    if not dt or dt > _now() + timedelta(minutes=5) or dt < _now() - timedelta(days=2):
        return None
    return _iso(dt)


def _door_one(ev: dict, it: dict, by: str) -> dict:
    at = _scan_time(it.get("at"))
    if it.get("ticket_id"):
        return check_in_ticket(ev["tenant_id"], ev["id"], it.get("ticket_id"), by, at=at)
    return check_in(ev["tenant_id"], ev["id"], it.get("code"), by, at=at)


def door_check_in(raw_key: Any, item: Any, device: Any = "") -> dict:
    """One check-in from the door page: {code} from a scan, or {ticket_id} picked from the list."""
    ev = _door_event(raw_key)
    return _door_one(ev, item if isinstance(item, dict) else {}, _door_by(device))


def door_sync(raw_key: Any, items: Any, device: Any = "") -> dict:
    """Check-ins the door phone made with no signal, sent once it is back: each is the same single
    write as a live scan. One that another door let in first comes back as `already`, naming who."""
    ev = _door_event(raw_key)
    if not isinstance(items, list) or len(items) > 500:
        raise EventError("Send up to 500 check-ins at a time.")
    by = _door_by(device)
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        res = _door_one(ev, it, by)
        out.append({"ref": str(it.get("ref") or "")[:80], "result": res["result"],
                    "message": res.get("message"), "ticket": res.get("ticket")})
    return {"results": out}


# ── the buyer hears from the business: the day before, and the day after ──────────────────────
# Both go to the BUYER's own number (the one that bought), once per order, on the business's
# WhatsApp line. The reminder is about something they paid for, so it always goes (when the event
# has it on). The thank-you invites them to the next event, which is marketing: a number that
# opted out doesn't get it. A ticket is marked first and the message sent after, so a crash can
# skip a message but never send one twice.
REMIND_FROM_HOURS, REMIND_UNTIL_HOURS = 30, 2
THANK_FROM_HOURS, THANK_UNTIL_HOURS = 3, 48


async def _message_buyer(tenant_id: str, order: dict, text: str, news: str, button: str, link: str) -> bool:
    try:
        from vula.api.whatsapp import _resolve_wa, _send_wa_cta_url, _wa_number, send_template
        phone = order.get("customer_phone")
        if not phone:
            return False
        creds = await _resolve_wa(tenant_id)
        sent = bool(creds and link) and await _send_wa_cta_url(creds, _wa_number(phone), text, button, link)
        if not sent:                       # outside WhatsApp's 24-hour window: the approved template
            from vula.commerce.job_config import template_name
            sent = await send_template(tenant_id, phone, template_name(tenant_id, "order_update"),
                                       [order.get("customer_name") or "there", order.get("display_id") or "", news])
        return bool(sent)
    except Exception as exc:  # noqa: BLE001
        log.warning("event message for %s %s failed: %s", tenant_id, order.get("display_id"), exc)
        return False


def _claim(tenant_id: str, order_id: str, event_id: str, column: str, statuses: tuple) -> int:
    """Mark this order's tickets as told (reminded / thanked) — only the ones not told yet. The
    number marked by THIS call; 0 means another run got there first, so nothing is sent."""
    n = 0
    for status in statuses:
        res = (_db().table("commerce_tickets").update({column: _iso(_now())}).eq("tenant_id", tenant_id)
               .eq("order_id", order_id).eq("event_id", event_id).eq("status", status).is_(column, "null").execute())
        n += len(res.data or [])
    return n


def _orders(tenant_id: str, ids: List[str]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for i in range(0, len(ids), 200):
        for o in (_db().table("commerce_orders").select("id,display_id,customer_name,customer_phone")
                  .eq("tenant_id", tenant_id).in_("id", ids[i:i + 200]).execute().data or []):
            out[o["id"]] = o
    return out


async def send_reminders(now: Optional[datetime] = None) -> int:
    """Buyers of an event starting in the next 2–30 hours hear once: when, where, and their tickets."""
    from datetime import timedelta
    from vula.api import tenants
    from vula.api.events import when_text
    now = now or _now()
    rows = (_db().table("commerce_events").select("*").in_("status", ["on_sale", "closed"])
            .gte("starts_at", _iso(now + timedelta(hours=REMIND_UNTIL_HOURS)))
            .lte("starts_at", _iso(now + timedelta(hours=REMIND_FROM_HOURS))).limit(500).execute().data or [])
    sent = 0
    for ev in rows:
        tenant_id = ev["tenant_id"]
        if ev.get("remind_buyers") is False or not tenants.is_active(tenant_id):
            continue
        todo = [k for k in _tickets(tenant_id, ev["id"]) if k.get("status") == "valid" and not k.get("reminded_at")]
        orders = _orders(tenant_id, sorted({k["order_id"] for k in todo}))
        where = ", ".join(x for x in (ev.get("venue"), ev.get("address")) if x)
        for order_id, order in orders.items():
            n = _claim(tenant_id, order_id, ev["id"], "reminded_at", ("valid",))
            if not n:
                continue
            first = (order.get("customer_name") or "").split(" ")[0]
            news = f"{ev['title']} is on {when_text(ev['starts_at'], ev.get('ends_at'))}" + (f" at {where}" if where else "") + "."
            text = (f"Hi {first}! " if first else "") + f"⏰ {news} You have {n} ticket{'' if n == 1 else 's'}: " \
                "have the QR code ready at the door." + f"\n\n— {tenants.display_name(tenant_id)}"
            if await _message_buyer(tenant_id, order, text, news, "View tickets", tickets_url(tenant_id, order_id)):
                sent += 1
    return sent


async def send_thanks(now: Optional[datetime] = None) -> int:
    """Buyers who came (a ticket of theirs was checked in) hear once after the event, with the
    business's next event on sale if there is one. Never to a number that opted out."""
    from datetime import timedelta
    from vula import consent
    from vula.api import tenants
    now = now or _now()
    rows = (_db().table("commerce_events").select("*").in_("status", ["on_sale", "closed"])
            .gte("starts_at", _iso(now - timedelta(hours=THANK_UNTIL_HOURS + 24)))
            .lte("starts_at", _iso(now)).limit(500).execute().data or [])
    sent = 0
    for ev in rows:
        tenant_id = ev["tenant_id"]
        end = _when(ev.get("ends_at") or ev.get("starts_at"), "the event's time", required=True)
        if ev.get("thank_buyers") is False or not tenants.is_active(tenant_id) or not (
                end + timedelta(hours=THANK_FROM_HOURS) <= now <= end + timedelta(hours=THANK_UNTIL_HOURS)):
            continue
        todo = [k for k in _tickets(tenant_id, ev["id"]) if k.get("status") == "checked_in" and not k.get("thanked_at")]
        orders = _orders(tenant_id, sorted({k["order_id"] for k in todo}))
        upcoming = [e for e in list_events(tenant_id) if e.get("selling") and e["id"] != ev["id"]]
        upcoming.sort(key=lambda e: e.get("starts_at") or "")
        nxt = upcoming[0] if upcoming else None
        for order_id, order in orders.items():
            if consent.opted_out(tenant_id, order.get("customer_phone") or ""):
                continue
            if not _claim(tenant_id, order_id, ev["id"], "thanked_at", ("checked_in",)):
                continue
            first = (order.get("customer_name") or "").split(" ")[0]
            news = f"Thank you for coming to {ev['title']}."
            text = (f"Hi {first}! " if first else "") + f"🙏 {news}" \
                + (f" Next up: {nxt['title']}. Tickets are on sale now." if nxt else "") \
                + f"\n\n— {tenants.display_name(tenant_id)}"
            link = nxt["url"] if nxt else ""
            # with no next event there is no button to send: the approved template carries the thanks
            if await _message_buyer(tenant_id, order, text, news, "See the next event", link):
                sent += 1
    return sent


async def run_forever(interval_s: int = 1800) -> None:
    """Every half hour on the scheduler leader: reminders, then thank-yous. Never raises."""
    import asyncio
    await asyncio.sleep(120)
    while True:
        try:
            r, t = await send_reminders(), await send_thanks()
            if r or t:
                log.info("event messages: %d reminder(s), %d thank-you(s)", r, t)
        except Exception as exc:  # noqa: BLE001
            log.warning("event messages run failed: %s", exc)
        await asyncio.sleep(interval_s)
