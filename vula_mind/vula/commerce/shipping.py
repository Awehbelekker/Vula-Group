"""
vula/commerce/shipping.py — an order's courier booking (23 Nov 2026, vula/couriers).

    quote(tenant, order)      what each service would cost for this order — books nothing
    book(tenant, order, …)    books the service the person chose, at the price they were shown
    cancel / label / refresh  the booking afterwards
    handle_webhook            the courier says something happened
    refresh_open              the same news, fetched, for couriers that don't call (hourly)

Rules this keeps:
  * Booking spends the business's money, so it is always two steps: `quote` shows the price,
    `book` is given that price back (`expected_cents`) and re-quotes first — a price that moved
    is refused with the new one, never booked at a figure nobody saw.
  * One live booking per order. The row is written before the courier is called (status
    `booking`, unique per order — migration 296), so a double click or two people can't book
    twice, and a call that died half way leaves a row saying so.
  * Verified, not reported: a booking is read back from the courier before it's called booked.
    A cancellation is read back too.
  * A courier's webhook is never believed. It only says "look at this waybill"; the status is
    then fetched from the courier with the business's own key.
  * Tracking moves an order forward only (dispatched, delivered), through the same service
    functions as the dashboard, and the customer hears it once (order_updates).

The customer's address on an order is free text. `parse_address` splits it in code (never a
model) and the booking screen shows the parts for the person to correct before anything is priced.
"""
from __future__ import annotations

import hmac
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from vula import couriers
from vula.couriers import CourierError

log = logging.getLogger(__name__)

_PROVINCES = {
    "western cape": "WC", "gauteng": "GP", "kwazulu-natal": "KZN", "kwazulu natal": "KZN", "kzn": "KZN",
    "eastern cape": "EC", "free state": "FS", "limpopo": "LP", "mpumalanga": "MP",
    "north west": "NW", "north-west": "NW", "northern cape": "NC",
    "wc": "WC", "gp": "GP", "ec": "EC", "fs": "FS", "lp": "LP", "mp": "MP", "nw": "NW", "nc": "NC",
}
_ADDRESS_KEYS = ("company", "street_address", "local_area", "city", "zone", "code", "lat", "lng", "type")
_PARCEL_KEYS = ("length_cm", "width_cm", "height_cm", "weight_kg")
_LIVE_NOT = ("cancelled", "failed")
_ORDER_RANK = {"pending_payment": 0, "confirmed": 1, "packing": 2, "dispatched": 3, "delivered": 4}
_NO_BOOKING = ("cancelled", "refunded")


class ShippingError(Exception):
    """A booking that can't go ahead; `detail` carries what the screen needs (e.g. the new price)."""

    def __init__(self, message: str, **detail: Any):
        super().__init__(message)
        self.detail = detail


def _db():
    from vula.commerce import service
    return service._client()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── addresses, contacts, parcels ──────────────────────────────────────────────

def parse_address(text: str) -> dict:
    """'12 Main Rd, Sea Point, Cape Town, 8005' → street / suburb / city / province / code.
    What can't be told is left empty for the person to fill in — nothing is guessed."""
    raw = re.sub(r"\s+", " ", (text or "").replace("\n", ", ")).strip(" ,")
    out = {"company": "", "street_address": "", "local_area": "", "city": "", "zone": "", "code": "",
           "type": "residential"}
    if not raw:
        return out
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    kept: List[str] = []
    for p in parts:
        low = p.lower()
        if low in ("south africa", "rsa", "za", "sa"):
            continue
        m = re.search(r"\b(\d{4})\b\s*$", p)
        if m and not out["code"] and (len(kept) > 0 or len(parts) == 1):
            out["code"] = m.group(1)
            p = p[:m.start()].strip(" ,")
            low = p.lower()
            if not p:
                continue
        if low in _PROVINCES and not out["zone"]:
            out["zone"] = _PROVINCES[low]
            continue
        kept.append(p)
    if kept:
        out["street_address"] = kept[0]
    if len(kept) >= 3:
        out["local_area"], out["city"] = kept[1], kept[-1]
    elif len(kept) == 2:
        out["local_area"] = out["city"] = kept[1]
    return out


def clean_address(a: Optional[dict]) -> dict:
    a = a or {}
    out: Dict[str, Any] = {k: str(a.get(k) or "").strip() for k in _ADDRESS_KEYS if k not in ("lat", "lng")}
    out["type"] = out["type"] if out["type"] in ("residential", "business") else "residential"
    for k in ("lat", "lng"):
        try:
            out[k] = float(a[k]) if a.get(k) not in (None, "") else None
        except (TypeError, ValueError):
            out[k] = None
    return out


def address_gaps(a: dict) -> List[str]:
    """What a courier needs that this address doesn't have, in plain words."""
    gaps = []
    if not a.get("street_address"):
        gaps.append("street address")
    if not a.get("city"):
        gaps.append("town or city")
    if not a.get("code") and (a.get("lat") is None or a.get("lng") is None):
        gaps.append("postal code")
    return gaps


def address_line(a: dict) -> str:
    return ", ".join(str(a[k]) for k in ("street_address", "local_area", "city", "code") if a.get(k))


async def _locate(a: dict) -> dict:
    """Adds lat/lng when the address has none — the courier prices and routes better with them."""
    if a.get("lat") is not None and a.get("lng") is not None or address_gaps(a):
        return a
    try:
        from vula.commerce import geo
        hit = await geo.geocode(address_line(a))
        if hit:
            a = {**a, "lat": hit["lat"], "lng": hit["lng"]}
    except Exception as exc:  # noqa: BLE001
        log.debug("courier address not located: %s", exc)
    return a


def mobile(phone: Any) -> str:
    """0821234567 / 27821234567 / +27 82 123 4567 → +27821234567; '' when it isn't a number."""
    d = re.sub(r"\D", "", str(phone or ""))
    if d.startswith("0") and len(d) == 10:
        d = "27" + d[1:]
    return "+" + d if 10 <= len(d) <= 15 else ""


def clean_parcel(p: Optional[dict]) -> Optional[dict]:
    try:
        out = {k: float((p or {})[k]) for k in _PARCEL_KEYS}
    except (KeyError, TypeError, ValueError):
        return None
    if any(v <= 0 for v in out.values()) or out["weight_kg"] > 70 or max(
            out["length_cm"], out["width_cm"], out["height_cm"]) > 300:
        return None
    return out


def get_settings(tenant_id: str) -> dict:
    try:
        rows = (_db().table("commerce_courier_settings").select("*")
                .eq("tenant_id", tenant_id).limit(1).execute().data or [])
    except Exception as exc:  # noqa: BLE001
        log.debug("courier settings read skipped (run migration 296?): %s", exc)
        rows = []
    r = rows[0] if rows else {}
    return {"collection_address": r.get("collection_address"), "collection_contact": r.get("collection_contact"),
            "default_parcel": r.get("default_parcel"), "collection_point": r.get("collection_point")}


def save_settings(tenant_id: str, patch: dict) -> dict:
    row: Dict[str, Any] = {"tenant_id": tenant_id, "updated_at": _now()}
    if "collection_address" in patch:
        row["collection_address"] = clean_address(patch["collection_address"])
    if "collection_contact" in patch:
        c = patch["collection_contact"] or {}
        row["collection_contact"] = {"name": str(c.get("name") or "").strip()[:80],
                                     "mobile_number": mobile(c.get("mobile_number")),
                                     "email": str(c.get("email") or "").strip()[:120]}
    if "default_parcel" in patch:
        parcel = clean_parcel(patch["default_parcel"])
        if patch["default_parcel"] and not parcel:
            raise ShippingError("The usual parcel needs a length, width and height in cm and a weight in kg.")
        row["default_parcel"] = parcel
    if "collection_point" in patch:
        p = patch["collection_point"] or {}
        row["collection_point"] = ({"id": str(p["id"]).strip(), "name": str(p.get("name") or "").strip()}
                                   if p.get("id") else None)
    _db().table("commerce_courier_settings").upsert(row, on_conflict="tenant_id").execute()
    return get_settings(tenant_id)


def order_parcel(order: dict, settings: dict) -> Dict[str, Any]:
    """One parcel for the order: the usual box, weighing what the products weigh when every line's
    weight is known, else the usual weight. `weight_from` says which, so the screen can."""
    base = clean_parcel(settings.get("default_parcel"))
    if not base:
        return {"parcel": None, "weight_from": None}
    grams, known = 0.0, True
    items = order.get("commerce_order_items") or []
    ids = [it.get("product_id") for it in items if it.get("product_id")]
    weights: Dict[str, Any] = {}
    if ids:
        try:
            rows = (_db().table("commerce_products").select("id,weight_grams")
                    .eq("tenant_id", order["tenant_id"]).in_("id", ids).execute().data or [])
            weights = {r["id"]: r.get("weight_grams") for r in rows}
        except Exception:  # noqa: BLE001
            weights = {}
    for it in items:
        w = weights.get(it.get("product_id"))
        try:
            qty = float(it.get("quantity") or 0)
        except (TypeError, ValueError):
            qty = 0
        if not w or qty <= 0:
            known = False
            break
        grams += float(w) * qty
    if known and items and grams > 0:
        return {"parcel": {**base, "weight_kg": round(grams / 1000, 2)}, "weight_from": "products"}
    return {"parcel": base, "weight_from": "usual"}


def _contacts(order: dict, settings: dict) -> tuple:
    sender = settings.get("collection_contact") or {}
    sender = {"name": sender.get("name") or "", "mobile_number": sender.get("mobile_number") or "",
              "email": sender.get("email") or ""}
    receiver = {"name": order.get("customer_name") or "", "mobile_number": mobile(order.get("customer_phone")),
                "email": order.get("customer_email") or ""}
    return sender, receiver


# ── reading ───────────────────────────────────────────────────────────────────

async def _order(tenant_id: str, order_id: str) -> dict:
    from vula.commerce import service
    try:
        order = await service.get_order(order_id)
    except Exception:  # noqa: BLE001 — .single() raises when there is no such order
        order = None
    if not order or order.get("tenant_id") != tenant_id:
        raise ShippingError("Order not found.")
    return order


def _public(row: dict) -> dict:
    out = {k: row.get(k) for k in (
        "id", "order_id", "provider", "mode", "tracking_reference", "service_code", "service_name",
        "quoted_cents", "charged_cents", "status", "delivery_kind", "pickup_point_id", "pickup_point_name",
        "delivery_address", "parcels", "events", "error", "verified_at", "booked_by", "created_at",
        "cancelled_at", "last_checked_at")}
    out["courier"] = couriers.PROVIDER_LABELS.get(row.get("provider"), row.get("provider"))
    out["status_text"] = ("Booking…" if row.get("status") == "booking" else
                          "Not booked" if row.get("status") == "failed" else
                          couriers.plain_status(row.get("status")))
    out["live"] = row.get("status") not in _LIVE_NOT
    out["can_cancel"] = bool(row.get("tracking_reference")) and row.get("status") in (
        "submitted", "collection-assigned", "collection-unassigned", "awaiting-dropoff", "deposit-pending")
    return out


def shipments_for(tenant_id: str, order_id: str) -> List[dict]:
    try:
        rows = (_db().table("commerce_shipments").select("*").eq("tenant_id", tenant_id)
                .eq("order_id", order_id).order("created_at", desc=True).limit(20).execute().data or [])
    except Exception as exc:  # noqa: BLE001
        log.debug("shipments read skipped (run migration 296?): %s", exc)
        return []
    return [_public(r) for r in rows]


def _shipment(tenant_id: str, shipment_id: str) -> dict:
    rows = (_db().table("commerce_shipments").select("*").eq("tenant_id", tenant_id)
            .eq("id", shipment_id).limit(1).execute().data or [])
    if not rows:
        raise ShippingError("That booking wasn't found.")
    return rows[0]


def _provider(tenant_id: str, provider: Optional[str]) -> tuple:
    row = couriers.provider_row(tenant_id, provider)
    if not row:
        raise ShippingError("No courier is connected yet. Connect one in Settings › Couriers.")
    return row, couriers.get_courier(row["provider"])


# ── quote ─────────────────────────────────────────────────────────────────────

async def _prepare(tenant_id: str, order: dict, *, provider: Optional[str], address: Optional[dict],
                   pickup_point: Optional[dict], parcel: Optional[dict]) -> dict:
    """Everything a quote or a booking needs, with what's missing named."""
    row, courier = _provider(tenant_id, provider)
    settings = get_settings(tenant_id)
    missing: List[str] = []

    collection_point = settings.get("collection_point") if row["provider"] == "pudo" else None
    collection = clean_address(settings.get("collection_address"))
    if not collection_point and address_gaps(collection):
        missing.append("where parcels are collected from (Settings › Couriers)")
    sender, receiver = _contacts(order, settings)
    if not (sender["mobile_number"] or sender["email"]):
        missing.append("who hands over the parcel (Settings › Couriers)")
    if not (receiver["mobile_number"] or receiver["email"]):
        missing.append("the customer's phone number or email")

    point_id = str((pickup_point or {}).get("id") or "").strip() or None
    delivery = clean_address(address) if address else parse_address(order.get("delivery_address") or "")
    delivery = clean_address(delivery)
    if not point_id:
        gaps = address_gaps(delivery)
        if gaps:
            missing.append("the customer's " + " and ".join(gaps))
        else:
            delivery = await _locate(delivery)

    made = order_parcel(order, settings)
    chosen = clean_parcel(parcel) if parcel else made["parcel"]
    if parcel and not chosen:
        missing.append("a parcel size (length, width, height in cm) and weight (kg)")
    elif not chosen:
        missing.append("your usual parcel size and weight (Settings › Couriers)")

    return {"row": row, "courier": courier, "collection": collection, "collection_point": collection_point,
            "sender": sender, "receiver": receiver, "delivery": delivery, "point_id": point_id,
            "point_name": str((pickup_point or {}).get("name") or ""), "parcel": chosen,
            "weight_from": "entered" if parcel else made["weight_from"], "missing": missing}


async def quote(tenant_id: str, order_id: str, *, provider: Optional[str] = None,
                address: Optional[dict] = None, pickup_point: Optional[dict] = None,
                parcel: Optional[dict] = None) -> dict:
    """Prices for this order. Books nothing."""
    order = await _order(tenant_id, order_id)
    if order.get("status") in _NO_BOOKING:
        raise ShippingError(f"Order {order.get('display_id')} is {order.get('status')}, so there's nothing to send.")
    p = await _prepare(tenant_id, order, provider=provider, address=address,
                       pickup_point=pickup_point, parcel=parcel)
    out = {"order": {"id": order["id"], "display_id": order.get("display_id"),
                     "customer_name": order.get("customer_name"), "delivery_address": order.get("delivery_address")},
           "provider": p["row"]["provider"], "courier": couriers.PROVIDER_LABELS[p["row"]["provider"]],
           "mode": p["row"].get("mode") or "live", "address": p["delivery"],
           "pickup_point": {"id": p["point_id"], "name": p["point_name"]} if p["point_id"] else None,
           "parcel": p["parcel"], "weight_from": p["weight_from"], "missing": p["missing"], "rates": [],
           "existing": [s for s in shipments_for(tenant_id, order_id) if s["live"]]}
    if p["missing"]:
        return out
    rates = await p["courier"].rates(
        p["row"]["credentials"], out["mode"], collection=p["collection"], delivery=p["delivery"],
        parcels=[p["parcel"]], pickup_point_id=p["point_id"],
        collection_point_id=(p["collection_point"] or {}).get("id"))
    out["rates"] = [r.as_dict() for r in rates]
    if not rates:
        out["missing"] = ["a service — the courier offered none for this address and parcel"]
    return out


async def pickup_points(tenant_id: str, *, provider: Optional[str] = None, near: str = "",
                        lat: Optional[float] = None, lng: Optional[float] = None) -> List[dict]:
    """The lockers and counters nearest a place (an address, suburb or coordinates)."""
    row, courier = _provider(tenant_id, provider)
    if lat is None or lng is None:
        from vula.commerce import geo
        hit = await geo.geocode(near) if near else None
        if not hit:
            raise ShippingError("That place wasn't found. Try the suburb and town.")
        lat, lng = hit["lat"], hit["lng"]
    points = await courier.pickup_points(row["credentials"], row.get("mode") or "live",
                                         lat=float(lat), lng=float(lng))
    return [pt.as_dict() for pt in points]


# ── book ──────────────────────────────────────────────────────────────────────

async def book(tenant_id: str, order_id: str, *, service_code: str, expected_cents: int,
               provider: Optional[str] = None, address: Optional[dict] = None,
               pickup_point: Optional[dict] = None, parcel: Optional[dict] = None,
               actor: Optional[dict] = None) -> dict:
    """Book `service_code` at the price the person was shown. Returns the booking, read back."""
    order = await _order(tenant_id, order_id)
    if order.get("status") in _NO_BOOKING:
        raise ShippingError(f"Order {order.get('display_id')} is {order.get('status')}, so there's nothing to send.")
    if shipments_for_live(tenant_id, order_id):
        raise ShippingError("This order already has a courier booked. Cancel that one first.")
    p = await _prepare(tenant_id, order, provider=provider, address=address,
                       pickup_point=pickup_point, parcel=parcel)
    if p["missing"]:
        raise ShippingError("Can't book yet — still needed: " + "; ".join(p["missing"]) + ".", missing=p["missing"])
    row, courier, mode = p["row"], p["courier"], p["row"].get("mode") or "live"
    creds = row["credentials"]
    point_args = {"pickup_point_id": p["point_id"], "collection_point_id": (p["collection_point"] or {}).get("id")}

    # The price is checked again right before the money is spent.
    rates = await courier.rates(creds, mode, collection=p["collection"], delivery=p["delivery"],
                                parcels=[p["parcel"]], **point_args)
    rate = next((r for r in rates if r.code == service_code), None)
    if not rate:
        raise ShippingError("That service is no longer offered for this parcel. Get the prices again.")
    if int(rate.cents) != int(expected_cents):
        raise ShippingError(f"The price changed to R{rate.cents / 100:.2f}. Nothing was booked — "
                            "check the new price and book again.", price_cents=rate.cents)

    db = _db()
    shipment = {"tenant_id": tenant_id, "order_id": order_id, "provider": row["provider"], "mode": mode,
                "service_code": rate.code, "service_name": rate.name, "quoted_cents": rate.cents,
                "status": "booking", "delivery_kind": "pickup_point" if p["point_id"] else "door",
                "pickup_point_id": p["point_id"], "pickup_point_name": p["point_name"] or None,
                "collection_point_id": point_args["collection_point_id"],
                "delivery_address": None if p["point_id"] else p["delivery"], "parcels": [p["parcel"]],
                "events": [], "open": True, "booked_by": (actor or {}).get("email") or (actor or {}).get("name")}
    try:
        made = db.table("commerce_shipments").insert(shipment).execute().data or []
    except Exception as exc:  # noqa: BLE001 — the one-live-booking-per-order index
        if shipments_for_live(tenant_id, order_id):
            raise ShippingError("This order already has a courier booked. Cancel that one first.") from exc
        raise
    sid = made[0]["id"]

    def _save(patch: dict) -> None:
        db.table("commerce_shipments").update({**patch, "updated_at": _now()}) \
            .eq("id", sid).eq("tenant_id", tenant_id).execute()

    try:
        booked = await courier.create(
            creds, mode, collection=p["collection"], collection_contact=p["sender"], delivery=p["delivery"],
            delivery_contact=p["receiver"], parcels=[p["parcel"]], service_code=rate.code,
            reference=str(order.get("display_id") or order_id)[:40],
            instructions=order.get("delivery_notes") or "", **point_args)
    except CourierError as exc:
        _save({"status": "failed", "open": False, "error": str(exc)[:400]})
        raise ShippingError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        # Unknown: the courier may or may not have it. The row stays 'booking' so nobody books a second.
        _save({"error": "The courier's answer was lost. Check the courier's own site before booking again."})
        log.error("courier booking outcome unknown for %s order %s: %s", tenant_id, order.get("display_id"), exc)
        raise ShippingError("The courier's answer was lost, so Vula can't tell whether it was booked. "
                            "Check the courier's own site before booking again.") from exc

    _save({"provider_shipment_id": booked.shipment_id, "tracking_reference": booked.tracking_reference,
           "status": booked.status or "submitted", "charged_cents": booked.cents,
           "service_name": booked.service_name or rate.name})

    # Read it back from the courier before saying it's booked.
    verified = False
    try:
        seen = await courier.track(creds, mode, tracking_reference=booked.tracking_reference,
                                   shipment_id=booked.shipment_id)
        if seen is not None:
            verified = True
            _save({"verified_at": _now(), "status": seen.status or booked.status or "submitted",
                   "events": seen.events, "last_checked_at": _now()})
    except CourierError as exc:
        log.warning("courier booking %s not read back: %s", booked.tracking_reference, exc)

    _audit(tenant_id, actor, "courier_booked", order=order.get("display_id"), courier=row["provider"],
           waybill=booked.tracking_reference, service=rate.code, cents=rate.cents, verified=verified)
    out = _public(_shipment(tenant_id, sid))
    out["verified"] = verified
    return out


def shipments_for_live(tenant_id: str, order_id: str) -> List[dict]:
    return [s for s in shipments_for(tenant_id, order_id) if s["live"]]


def _audit(tenant_id: str, actor: Optional[dict], action: str, **detail: Any) -> None:
    try:
        from vula.api.merchant_audit import audit
        audit(tenant_id, actor, action, **detail)
    except Exception:  # noqa: BLE001
        pass


# ── afterwards ────────────────────────────────────────────────────────────────

async def label(tenant_id: str, shipment_id: str, *, sticker: bool = False) -> str:
    s = _shipment(tenant_id, shipment_id)
    if not s.get("provider_shipment_id"):
        raise ShippingError("This booking has no waybill yet.")
    row, courier = _provider(tenant_id, s["provider"])
    try:
        url = await courier.label(row["credentials"], s.get("mode") or "live",
                                  shipment_id=s["provider_shipment_id"], sticker=sticker)
    except CourierError as exc:
        raise ShippingError(str(exc)) from exc
    if not url:
        raise ShippingError("The courier didn't give a label for this booking.")
    return url


async def cancel(tenant_id: str, shipment_id: str, *, actor: Optional[dict] = None) -> dict:
    s = _shipment(tenant_id, shipment_id)
    if s.get("status") in _LIVE_NOT:
        return _public(s)
    db = _db()
    if not s.get("tracking_reference"):
        # Never reached the courier (or its answer was lost and the person has checked): clear it.
        db.table("commerce_shipments").update({"status": "failed", "open": False, "updated_at": _now(),
                                               "error": s.get("error") or "Cleared before it was booked."}) \
            .eq("id", shipment_id).eq("tenant_id", tenant_id).execute()
        _audit(tenant_id, actor, "courier_booking_cleared", shipment=shipment_id)
        return _public(_shipment(tenant_id, shipment_id))
    row, courier = _provider(tenant_id, s["provider"])
    mode = s.get("mode") or "live"
    try:
        await courier.cancel(row["credentials"], mode, tracking_reference=s["tracking_reference"],
                             shipment_id=s.get("provider_shipment_id"))
        seen = await courier.track(row["credentials"], mode, tracking_reference=s["tracking_reference"],
                                   shipment_id=s.get("provider_shipment_id"))
    except CourierError as exc:
        raise ShippingError(str(exc)) from exc
    if not seen or seen.status != "cancelled":
        raise ShippingError("The courier took the cancellation but still shows the booking as "
                            f"{couriers.plain_status(seen.status if seen else '')}. Check the courier's own site.")
    db.table("commerce_shipments").update({"status": "cancelled", "open": False, "cancelled_at": _now(),
                                           "events": seen.events,
                                           "updated_at": _now(), "last_checked_at": _now()}) \
        .eq("id", shipment_id).eq("tenant_id", tenant_id).execute()
    _audit(tenant_id, actor, "courier_cancelled", waybill=s["tracking_reference"], courier=s["provider"])
    return _public(_shipment(tenant_id, shipment_id))


async def refresh(tenant_id: str, shipment_id: str) -> dict:
    """Fetch the courier's news for one booking and act on it."""
    s = _shipment(tenant_id, shipment_id)
    if not s.get("tracking_reference") or s.get("status") in _LIVE_NOT:
        return _public(s)
    row, courier = _provider(tenant_id, s["provider"])
    try:
        seen = await courier.track(row["credentials"], s.get("mode") or "live",
                                   tracking_reference=s["tracking_reference"],
                                   shipment_id=s.get("provider_shipment_id"))
    except CourierError as exc:
        raise ShippingError(str(exc)) from exc
    if seen is None:
        _db().table("commerce_shipments").update({"last_checked_at": _now()}) \
            .eq("id", shipment_id).eq("tenant_id", tenant_id).execute()
        return _public(s)
    await _apply(tenant_id, s, seen)
    return _public(_shipment(tenant_id, shipment_id))


async def _apply(tenant_id: str, s: dict, seen: couriers.Tracking) -> None:
    """Save what the courier says, move the order forward, tell the customer and the team once."""
    status = seen.status or s.get("status")
    patch: Dict[str, Any] = {"status": status, "events": seen.events or s.get("events") or [],
                             "last_checked_at": _now(), "updated_at": _now()}
    if not s.get("verified_at"):
        patch["verified_at"] = _now()
    if status == "cancelled" and not s.get("cancelled_at"):
        patch["cancelled_at"] = _now()
    if status in couriers.CLOSED:
        patch["open"] = False
    tell_team = status in couriers.PROBLEM and s.get("problem_notified") != status
    if tell_team:
        patch["problem_notified"] = status
    _db().table("commerce_shipments").update(patch).eq("id", s["id"]).eq("tenant_id", tenant_id).execute()

    step = couriers.order_status_for(status)
    if step:
        await _move_order(tenant_id, s, step)
    if tell_team:
        await _tell_team(tenant_id, s, status)


async def _move_order(tenant_id: str, s: dict, step: str) -> None:
    from vula.commerce import order_updates, service
    try:
        order = await service.get_order(s["order_id"])
    except Exception:  # noqa: BLE001
        return
    if not order or order.get("tenant_id") != tenant_id:
        return
    now = order.get("status")
    if now not in _ORDER_RANK or _ORDER_RANK[step] <= _ORDER_RANK[now] or now == "pending_payment":
        return                    # never backwards, never an order that's cancelled, refunded or unpaid
    try:
        await service.update_order_status(s["order_id"], step)
        await service.apply_order_stock(s["order_id"], restore=False)
    except Exception as exc:  # noqa: BLE001
        log.warning("order %s not moved to %s from courier news: %s", order.get("display_id"), step, exc)
        return
    if step == "delivered" and order.get("review_requested_at"):
        return
    note = ""
    if step == "dispatched" and s.get("tracking_reference"):
        where = f" to {s['pickup_point_name']}" if s.get("pickup_point_name") else ""
        note = (f"Sent with {couriers.PROVIDER_LABELS.get(s['provider'], 'courier')}{where}. "
                f"Tracking number: {s['tracking_reference']}")
    await order_updates.notify(tenant_id, order, step, note=note)


async def _tell_team(tenant_id: str, s: dict, status: str) -> None:
    try:
        from vula.integrations.notify import notify_team
        rows = (_db().table("commerce_orders").select("display_id,customer_name")
                .eq("tenant_id", tenant_id).eq("id", s["order_id"]).limit(1).execute().data or [{}])
        o = rows[0]
        await notify_team(
            tenant_id, "delivery_problem",
            f"🚚 Delivery problem on order {o.get('display_id') or ''} ({o.get('customer_name') or 'customer'}): "
            f"{couriers.plain_status(status)}. Waybill {s.get('tracking_reference')} with "
            f"{couriers.PROVIDER_LABELS.get(s['provider'], s['provider'])}.",
            idem_key=f"delivery_problem:{s['id']}:{status}")
    except Exception as exc:  # noqa: BLE001
        log.warning("delivery problem not sent to the team: %s", exc)


# ── the courier calls, or we ask ──────────────────────────────────────────────

def _references(payload: Any) -> List[str]:
    """Every waybill number a webhook body names. Nothing else in it is used."""
    refs: List[str] = []
    for item in payload if isinstance(payload, list) else [payload]:
        if not isinstance(item, dict):
            continue
        for k in ("short_tracking_reference", "custom_tracking_reference", "shipment_tracking_reference",
                  "shipment_short_tracking_reference", "shipment_custom_tracking_reference", "tracking_reference"):
            v = item.get(k)
            if isinstance(v, str) and 2 <= len(v) <= 60 and v not in refs:
                refs.append(v)
    return refs[:10]


async def handle_webhook(tenant_id: str, provider: str, token: str, payload: Any) -> int:
    """The courier says something changed. Returns how many bookings were re-read. The body is
    only a pointer: the token must be this business's, and the status comes from the courier."""
    try:
        rows = (_db().table("vula_courier_providers").select("webhook_token,active")
                .eq("tenant_id", tenant_id).eq("provider", provider).limit(1).execute().data or [])
    except Exception:  # noqa: BLE001
        rows = []
    want = (rows[0].get("webhook_token") if rows and rows[0].get("active") else "") or ""
    if not want or not token or not hmac.compare_digest(str(want), str(token)):
        log.warning("courier webhook for %s/%s ignored: wrong address", tenant_id, provider)
        return 0
    refs = _references(payload)
    if not refs:
        return 0
    found = (_db().table("commerce_shipments").select("id,status").eq("tenant_id", tenant_id)
             .eq("provider", provider).in_("tracking_reference", refs).limit(10).execute().data or [])
    done = 0
    for r in found:
        if r.get("status") in _LIVE_NOT:
            continue
        try:
            await refresh(tenant_id, r["id"])
            done += 1
        except ShippingError as exc:
            log.warning("courier webhook: booking %s not re-read: %s", r["id"], exc)
    return done


async def refresh_open(max_rows: int = 200, older_than_minutes: int = 50) -> Dict[str, int]:
    """Every booking still on its way, least recently checked first (the scheduler leader, hourly).
    Bookings older than 30 days are left alone."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    stale = (datetime.now(timezone.utc) - timedelta(minutes=older_than_minutes)).isoformat()
    try:
        rows = (_db().table("commerce_shipments").select("id,tenant_id,status,tracking_reference,last_checked_at")
                .eq("open", True).gte("created_at", cutoff).order("last_checked_at", desc=False)
                .limit(max_rows).execute().data or [])
    except Exception as exc:  # noqa: BLE001
        log.debug("open shipments read skipped (run migration 296?): %s", exc)
        return {"checked": 0, "failed": 0}
    checked = failed = 0
    for r in rows:
        if (r.get("status") in couriers.CLOSED or r.get("status") in _LIVE_NOT + ("booking",)
                or not r.get("tracking_reference") or (r.get("last_checked_at") or "") > stale):
            continue
        try:
            await refresh(r["tenant_id"], r["id"])
            checked += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            log.warning("shipment %s not refreshed: %s", r["id"], exc)
    return {"checked": checked, "failed": failed}


async def run_forever(interval_seconds: int = 3600) -> None:
    import asyncio
    await asyncio.sleep(300)
    while True:
        try:
            res = await refresh_open()
            if res["checked"] or res["failed"]:
                log.info("courier tracking: %s checked, %s failed", res["checked"], res["failed"])
        except Exception as exc:  # noqa: BLE001
            log.warning("courier tracking run failed: %s", exc)
        await asyncio.sleep(interval_seconds)
