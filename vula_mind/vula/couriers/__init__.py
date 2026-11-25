"""
vula/couriers — book a courier from an order (23 Nov 2026).

Each business connects its own courier account, the way it connects its own payment gateway:
the courier bills the business directly, Vula never holds or marks up the delivery money.

    tcg   The Courier Guy       door to door, and to a locker / counter ("pickup point")
                                 live  https://api.portal.thecourierguy.co.za
                                 test  https://api.shiplogic.com  (their "Sandbox Couriers")
    pudo  The Courier Guy Locker (PUDO) — door or locker to door or locker, by box size
                                 live  https://api-tcg.co.za

Both run on the same platform (Ship Logic), so one address and parcel shape serves both. The
request bodies follow the couriers' own published examples (The Courier Guy's Postman collection
and the PUDO production API page, read 23 Nov 2026). Neither publishes example RESPONSES for
The Courier Guy's API, so every reader here takes the fields it needs and tolerates a list or a
wrapped list — and `vula/commerce/shipping.py` reads a booking back before it says it was made.

Money: the couriers quote in rand with decimals ("705.56", 205.5625); it becomes integer cents
here (`rand_to_cents`, half up) and is never worked out by a model.

Credentials are one Fernet-wrapped blob, like the payment gateways (vula/payments).
"""
from __future__ import annotations

import logging
import math
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

PROVIDER_FIELDS = {"tcg": ["api_key"], "pudo": ["api_key"]}
PROVIDER_LABELS = {"tcg": "The Courier Guy", "pudo": "PUDO lockers (The Courier Guy Locker)"}
PROVIDER_MODES = {"tcg": ("live", "test"), "pudo": ("live",)}
PROVIDER_HELP = {
    "tcg": "In The Courier Guy's portal: Integrations › API Keys. Use a sandbox key with Test.",
    "pudo": "In your PUDO account: Settings › API Keys.",
}
# Where the business pays its courier account. Only the live portal: a test account has no money in it.
PROVIDER_PORTALS = {"tcg": "https://portal.thecourierguy.co.za"}
_TIMEOUT = 20.0


class CourierError(Exception):
    """Something the courier refused or that couldn't be reached — the message is safe to show."""


# ── shapes ────────────────────────────────────────────────────────────────────

@dataclass
class Rate:
    code: str
    name: str
    cents: int
    excl_vat_cents: Optional[int] = None
    delivery_from: str = ""
    delivery_to: str = ""
    box: str = ""                     # locker services: the box this price is for

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Booked:
    shipment_id: str
    tracking_reference: str
    status: str
    cents: Optional[int] = None
    service_code: str = ""
    service_name: str = ""


@dataclass
class Tracking:
    status: str
    events: List[dict] = field(default_factory=list)      # [{status, at, message, location}]
    collected_at: Optional[str] = None
    delivered_at: Optional[str] = None


@dataclass
class PickupPoint:
    id: str
    name: str
    address: str = ""
    lat: Optional[float] = None
    lng: Optional[float] = None
    kind: str = ""
    boxes: List[dict] = field(default_factory=list)       # [{name, width, height, length, weight}]
    km: Optional[float] = None

    def as_dict(self) -> dict:
        return asdict(self)


# ── small readers ─────────────────────────────────────────────────────────────

def rand_to_cents(v: Any) -> Optional[int]:
    """'705.56' / 205.5625 / 82.5 → integer cents (half up); None when absent or not a number."""
    if v in (None, "") or isinstance(v, bool):
        return None
    try:
        d = Decimal(str(v).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    if d < 0:
        return None
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def signed_cents(v: Any) -> Optional[int]:
    """An account balance in rand (-129.95 = R129.95 owed) → integer cents, keeping the sign."""
    if v in (None, "") or isinstance(v, bool):
        return None
    try:
        d = Decimal(str(v).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    if not d.is_finite():
        return None
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _num(v: Any) -> Optional[float]:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _rows(data: Any, *keys: str) -> List[dict]:
    """The list in a response, whether it came bare or wrapped under one of `keys`."""
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for k in keys:
            if isinstance(data.get(k), list):
                return [r for r in data[k] if isinstance(r, dict)]
    return []


def _first_url(data: Any) -> Optional[str]:
    """A label call "returns a signed URL" — as a bare string or under some key."""
    if isinstance(data, str):
        s = data.strip().strip('"')
        return s if s.startswith("http") else None
    if isinstance(data, dict):
        for k in ("url", "signed_url", "label_url", "link"):
            if isinstance(data.get(k), str) and data[k].startswith("http"):
                return data[k]
        for v in data.values():
            u = _first_url(v) if isinstance(v, (str, dict)) else None
            if u:
                return u
    return None


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2)
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def _events(raw: Any) -> List[dict]:
    out = []
    for e in raw if isinstance(raw, list) else []:
        if isinstance(e, dict) and e.get("status"):
            out.append({"status": str(e["status"]), "at": str(e.get("date") or ""),
                        "message": str(e.get("message") or ""), "location": str(e.get("location") or "")})
    out.sort(key=lambda e: e["at"])                       # oldest first, whatever order they came in
    return out


def _tracking(d: dict) -> Tracking:
    events = _events(d.get("tracking_events"))
    status = d.get("status") if isinstance(d.get("status"), str) and d.get("status") else ""
    if not status and events:                             # PUDO's public lookup says status: false
        status = events[-1]["status"]
    return Tracking(status=status or "", events=events,
                    collected_at=d.get("shipment_collected_date") or None,
                    delivered_at=d.get("shipment_delivered_date") or None)


def _rates(data: Any) -> List[Rate]:
    out: List[Rate] = []
    for r in _rows(data, "rates"):
        sl = r.get("service_level") if isinstance(r.get("service_level"), dict) else {}
        cents = rand_to_cents(r.get("rate"))
        code = str(sl.get("code") or r.get("service_level_code") or "")
        if cents is None or not code:
            continue
        dims = sl.get("dimensions") if isinstance(sl.get("dimensions"), dict) else None
        box = ""
        if dims:
            box = (f"{sl.get('box_type_name') or ''} up to {dims.get('length')}×{dims.get('width')}×"
                   f"{dims.get('height')} cm, {dims.get('weight')} kg").strip()
        out.append(Rate(code=code, name=str(sl.get("name") or code), cents=cents,
                        excl_vat_cents=rand_to_cents(r.get("rate_excluding_vat")),
                        delivery_from=str(sl.get("delivery_date_from") or ""),
                        delivery_to=str(sl.get("delivery_date_to") or ""), box=box))
    out.sort(key=lambda x: x.cents)
    return out


def _parcels(parcels: List[dict]) -> List[dict]:
    return [{"parcel_description": str(p.get("description") or "Parcel")[:60],
             "submitted_length_cm": float(p["length_cm"]), "submitted_width_cm": float(p["width_cm"]),
             "submitted_height_cm": float(p["height_cm"]), "submitted_weight_kg": float(p["weight_kg"])}
            for p in parcels or []]


async def _call(method: str, url: str, key: str, *, params: dict = None, body: dict = None) -> Any:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.request(method, url, params=params, json=body,
                                headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise CourierError("The courier couldn't be reached. Nothing was booked or changed.") from exc
    if r.status_code in (401, 423):
        raise CourierError("The courier didn't accept the API key. Check it in Settings › Couriers.")
    if r.status_code == 429:
        raise CourierError("The courier is limiting requests right now. Try again in a minute.")
    if r.status_code >= 400:
        raise CourierError(f"The courier refused this: {_reason(r)}")
    try:
        return r.json()
    except ValueError:
        return r.text


def _reason(r: httpx.Response) -> str:
    """The courier's own words for a refusal, kept short. Never a body that could hold a key."""
    try:
        d = r.json()
        msg = d.get("message") or d.get("error") or d.get("detail") if isinstance(d, dict) else None
        if isinstance(msg, str) and msg.strip():
            return msg.strip()[:240]
    except ValueError:
        pass
    text = (r.text or "").strip()
    return text[:240] if text and "<" not in text[:20] else f"error {r.status_code}"


# ── providers ─────────────────────────────────────────────────────────────────

class _Courier:
    name = ""
    lockers = False                   # can deliver to a pickup point
    door = True

    def base(self, mode: str) -> str:
        raise NotImplementedError

    async def check(self, creds: dict, mode: str) -> None:
        """Raise CourierError when the key doesn't work."""
        raise NotImplementedError

    async def account(self, creds: dict, mode: str) -> Optional[dict]:
        """The business's account with the courier, or None where the courier doesn't say."""
        return None


class CourierGuy(_Courier):
    """The Courier Guy's own API (Ship Logic)."""
    name = "tcg"
    lockers = True

    def base(self, mode: str) -> str:
        return "https://api.shiplogic.com" if mode == "test" else "https://api.portal.thecourierguy.co.za"

    def _address(self, a: dict) -> dict:
        out = {"type": a.get("type") or "residential", "company": a.get("company") or "",
               "street_address": a.get("street_address") or "", "local_area": a.get("local_area") or "",
               "city": a.get("city") or "", "zone": a.get("zone") or "", "country": "ZA",
               "code": a.get("code") or ""}
        if a.get("lat") is not None and a.get("lng") is not None:
            out["lat"], out["lng"] = float(a["lat"]), float(a["lng"])
        return out

    def _where(self, body: dict, collection: dict, delivery: dict, pickup_point_id: Optional[str]) -> None:
        body["collection_address"] = self._address(collection)
        if pickup_point_id:
            body["delivery_pickup_point_id"] = pickup_point_id
        else:
            body["delivery_address"] = self._address(delivery)

    async def check(self, creds, mode):
        await _call("GET", f"{self.base(mode)}/shipments", creds["api_key"], params={"limit": 1})

    async def account(self, creds, mode) -> Optional[dict]:
        """Read in the sandbox on 25 Nov 2026: amounts are rand; `balance` goes negative by a
        booking's price when it is booked and back when it is cancelled (negative = owed)."""
        rows = _rows(await _call("GET", f"{self.base(mode)}/accounts", creds["api_key"]), "accounts")
        if not rows:
            return None
        a = rows[0]
        kind = str(a.get("account_type") or "").lower()
        return {"name": str(a.get("name") or "")[:80], "kind": kind if kind in ("prepaid", "postpaid") else "",
                "balance_cents": signed_cents(a.get("balance")),
                "credit_limit_cents": signed_cents(a.get("credit_limit")) if kind == "postpaid" else None,
                "payment_terms_days": a.get("payment_terms") if isinstance(a.get("payment_terms"), int) else None,
                "on_hold": bool(a.get("is_on_hold")), "status": str(a.get("status") or "")}

    async def rates(self, creds, mode, *, collection, delivery, parcels, pickup_point_id=None,
                    collection_point_id=None) -> List[Rate]:
        body: Dict[str, Any] = {"parcels": [{k: v for k, v in p.items() if k != "parcel_description"}
                                            for p in _parcels(parcels)]}
        self._where(body, collection, delivery, pickup_point_id)
        return _rates(await _call("POST", f"{self.base(mode)}/rates", creds["api_key"], body=body))

    async def create(self, creds, mode, *, collection, collection_contact, delivery, delivery_contact,
                     parcels, service_code, reference, pickup_point_id=None, collection_point_id=None,
                     instructions="", reference_name="Order no.") -> Booked:
        body: Dict[str, Any] = {
            "collection_contact": collection_contact, "delivery_contact": delivery_contact,
            "parcels": _parcels(parcels), "service_level_code": service_code,
            "customer_reference_name": reference_name, "customer_reference": reference,
            "special_instructions_delivery": (instructions or "")[:200], "mute_notifications": False,
        }
        self._where(body, collection, delivery, pickup_point_id)
        d = await _call("POST", f"{self.base(mode)}/shipments", creds["api_key"], body=body)
        d = d if isinstance(d, dict) else {}
        ref = str(d.get("short_tracking_reference") or d.get("custom_tracking_reference") or "")
        if not d.get("id") or not ref:
            raise CourierError("The courier answered without a waybill number, so Vula can't tell "
                               "whether the booking was made. Check The Courier Guy's portal before trying again.")
        return Booked(shipment_id=str(d["id"]), tracking_reference=ref, status=str(d.get("status") or "submitted"),
                      cents=rand_to_cents(d.get("rate")), service_code=service_code,
                      service_name=str(d.get("service_level_name") or ""))

    async def track(self, creds, mode, *, tracking_reference, shipment_id=None) -> Optional[Tracking]:
        d = await _call("GET", f"{self.base(mode)}/tracking/shipments", creds["api_key"],
                        params={"tracking_reference": tracking_reference})
        rows = _rows(d, "shipments")
        if not rows and isinstance(d, dict) and (d.get("status") or d.get("tracking_events")):
            rows = [d]
        return _tracking(rows[0]) if rows else None

    async def cancel(self, creds, mode, *, tracking_reference, shipment_id=None) -> None:
        await _call("POST", f"{self.base(mode)}/shipments/cancel", creds["api_key"],
                    body={"tracking_reference": tracking_reference})

    async def label(self, creds, mode, *, shipment_id, sticker=False) -> Optional[str]:
        path = "/shipments/label/stickers" if sticker else "/shipments/label"
        return _first_url(await _call("GET", f"{self.base(mode)}{path}", creds["api_key"],
                                      params={"id": shipment_id}))

    async def pickup_points(self, creds, mode, *, lat, lng, limit=8) -> List[PickupPoint]:
        d = await _call("GET", f"{self.base(mode)}/pickup-points", creds["api_key"],
                        params={"lat": lat, "lng": lng, "order_closest": "true"})
        out = []
        for r in _rows(d, "pickup_points", "results", "data"):
            pid = r.get("pickup_point_id") or r.get("id") or r.get("code")
            if not pid:
                continue
            addr = r.get("address")
            if isinstance(addr, dict):
                plat, plng = _num(addr.get("lat")), _num(addr.get("lng"))
                addr = addr.get("entered_address") or ", ".join(
                    str(addr[k]) for k in ("street_address", "local_area", "city", "code") if addr.get(k))
            else:
                plat, plng = _num(r.get("lat") or r.get("latitude")), _num(r.get("lng") or r.get("longitude"))
            km = haversine_km(lat, lng, plat, plng) if plat is not None and plng is not None else None
            out.append(PickupPoint(id=str(pid), name=str(r.get("name") or pid), address=str(addr or ""),
                                   lat=plat, lng=plng, kind=str(r.get("type") or ""), km=km))
        return out[:limit]


class Pudo(_Courier):
    """The Courier Guy Locker (PUDO) account API. A locker is named by its terminal id (CG54)."""
    name = "pudo"
    lockers = True

    def base(self, mode: str) -> str:
        if mode != "live":
            raise CourierError("PUDO only works on a live account in Vula for now.")
        return "https://api-tcg.co.za"

    def _address(self, a: dict) -> dict:
        entered = ", ".join(str(a[k]) for k in ("street_address", "local_area", "city", "code") if a.get(k))
        out = {"type": a.get("type") or "residential", "company": a.get("company") or "",
               "street_address": a.get("street_address") or "", "local_area": a.get("local_area") or "",
               "suburb": a.get("local_area") or "", "city": a.get("city") or "", "code": a.get("code") or "",
               "zone": a.get("zone") or "", "country": "South Africa",
               "entered_address": entered + ", South Africa"}
        if a.get("lat") is not None and a.get("lng") is not None:
            out["lat"], out["lng"] = float(a["lat"]), float(a["lng"])
        return out

    def _where(self, body, collection, delivery, pickup_point_id, collection_point_id) -> bool:
        """Fills both ends; True when either end is a locker (then the price is by box size)."""
        body["collection_address"] = ({"terminal_id": collection_point_id} if collection_point_id
                                      else self._address(collection))
        body["delivery_address"] = ({"terminal_id": pickup_point_id} if pickup_point_id
                                    else self._address(delivery))
        return bool(collection_point_id or pickup_point_id)

    async def check(self, creds, mode):
        await _call("GET", f"{self.base(mode)}/lockers-data", creds["api_key"])

    async def rates(self, creds, mode, *, collection, delivery, parcels, pickup_point_id=None,
                    collection_point_id=None) -> List[Rate]:
        body: Dict[str, Any] = {"opt_in_rates": [], "opt_in_time_based_rates": []}
        if not self._where(body, collection, delivery, pickup_point_id, collection_point_id):
            body["parcels"] = [{**p, **{k: str(p[k]) for k in p if k.startswith("submitted_")}}
                               for p in _parcels(parcels)]
        return _rates(await _call("POST", f"{self.base(mode)}/rates", creds["api_key"], body=body))

    async def create(self, creds, mode, *, collection, collection_contact, delivery, delivery_contact,
                     parcels, service_code, reference, pickup_point_id=None, collection_point_id=None,
                     instructions="", reference_name="Order no.") -> Booked:
        body: Dict[str, Any] = {
            "collection_contact": collection_contact, "delivery_contact": delivery_contact,
            "service_level_code": service_code, "opt_in_rates": [], "opt_in_time_based_rates": [],
            "special_instructions_collection": f"{reference_name.replace(' no.', '')} {reference}"[:200],
        }
        if not self._where(body, collection, delivery, pickup_point_id, collection_point_id):
            body["parcels"] = [{**p, **{k: str(p[k]) for k in p if k.startswith("submitted_")},
                                "alternative_tracking_reference": ""} for p in _parcels(parcels)]
        d = await _call("POST", f"{self.base(mode)}/shipments", creds["api_key"], body=body)
        d = d if isinstance(d, dict) else {}
        ref = str(d.get("custom_tracking_reference") or "")
        if not d.get("id") or not ref:
            raise CourierError("PUDO answered without a waybill number, so Vula can't tell whether the "
                               "booking was made. Check your PUDO account before trying again.")
        return Booked(shipment_id=str(d["id"]), tracking_reference=ref, status=str(d.get("status") or "submitted"),
                      cents=rand_to_cents(d.get("rate")), service_code=service_code,
                      service_name=str(d.get("service_level_name") or ""))

    async def track(self, creds, mode, *, tracking_reference, shipment_id=None) -> Optional[Tracking]:
        d = await _call("GET", f"{self.base(mode)}/tracking/shipments/public", creds["api_key"],
                        params={"waybill": tracking_reference})
        return _tracking(d) if isinstance(d, dict) and d.get("shipment_id") else None

    async def cancel(self, creds, mode, *, tracking_reference, shipment_id=None) -> None:
        await _call("PUT", f"{self.base(mode)}/shipments/{shipment_id}", creds["api_key"],
                    body={"status": "cancelled"})

    async def label(self, creds, mode, *, shipment_id, sticker=False) -> Optional[str]:
        kind = "sticker" if sticker else "waybill"
        return _first_url(await _call("GET", f"{self.base(mode)}/generate/{kind}/{shipment_id}",
                                      creds["api_key"], params={"api_key": creds["api_key"]}))

    async def pickup_points(self, creds, mode, *, lat, lng, limit=8) -> List[PickupPoint]:
        d = await _call("GET", f"{self.base(mode)}/lockers-data", creds["api_key"])
        out = []
        for r in _rows(d, "lockers", "data"):
            if not r.get("code"):
                continue
            plat, plng = _num(r.get("latitude")), _num(r.get("longitude"))
            km = haversine_km(lat, lng, plat, plng) if plat is not None and plng is not None else None
            boxes = [{"name": b.get("name"), "width": b.get("width"), "height": b.get("height"),
                      "length": b.get("length"), "weight": b.get("weight")}
                     for b in r.get("lstTypesBoxes") or [] if isinstance(b, dict)]
            kind = r.get("type", {}).get("name") if isinstance(r.get("type"), dict) else ""
            out.append(PickupPoint(id=str(r["code"]), name=str(r.get("name") or r["code"]),
                                   address=str(r.get("address") or ""), lat=plat, lng=plng,
                                   kind=str(kind or ""), boxes=boxes, km=km))
        out.sort(key=lambda p: p.km if p.km is not None else 1e9)
        return out[:limit]


_REGISTRY: Dict[str, _Courier] = {c.name: c for c in (CourierGuy(), Pudo())}


def get_courier(name: str) -> Optional[_Courier]:
    return _REGISTRY.get(name)


# ── what a courier status means for the order ────────────────────────────────

MOVING = {"collected", "at-hub", "in-transit", "at-destination-hub", "out-for-delivery",
          "delivery-scheduled", "delivery-assigned", "delivery-unassigned", "delivery-rejected",
          "manifested", "ready-for-dispatch",
          "in-locker", "ready-for-pickup", "swad-dimensions", "swad-imaging", "floor-check"}
DELIVERED = {"delivered", "collected-from-locker", "collected-from-counter"}
PROBLEM = {"collection-exception", "collection-failed-attempt", "delivery-exception",
           "delivery-failed-attempt", "returned-to-hub", "returned-to-sender", "return-to-hub",
           "collect-and-return-to-hub", "undeliverable", "damaged", "on-hold", "under-query", "failed",
           "inbound-customs-and-vat"}
CLOSED = DELIVERED | {"cancelled", "returned-to-sender", "undeliverable"}

PLAIN = {
    "submitted": "Booked", "collection-assigned": "Driver assigned to collect",
    "collection-accepted": "Driver on the way to collect", "awaiting-dropoff": "Waiting to be dropped off",
    "deposit-pending": "Waiting to be dropped off", "collected": "Collected", "at-hub": "At the depot",
    "in-transit": "In transit", "at-destination-hub": "At the depot nearest the customer",
    "out-for-delivery": "Out for delivery", "in-locker": "In the locker",
    "ready-for-pickup": "Ready to collect", "delivered": "Delivered",
    "collected-from-locker": "Collected from the locker", "collected-from-counter": "Collected from the counter",
    "cancelled": "Cancelled", "delivery-failed-attempt": "Delivery attempt failed",
    "delivery-exception": "Problem with delivery", "collection-failed-attempt": "Collection attempt failed",
    "collection-exception": "Problem with collection", "returned-to-sender": "Returned to you",
    "returned-to-hub": "Back at the depot", "undeliverable": "Couldn't be delivered",
    "damaged": "Damaged", "on-hold": "On hold", "on-hold-internal": "On hold at the courier",
    "inbound-customs-and-vat": "Held at customs", "delivery-unassigned": "Waiting for a delivery driver",
    "delivery-rejected": "Waiting for a delivery driver", "collection-unassigned": "Waiting for a driver to collect",
    "collection-rejected": "Waiting for a driver to collect", "under-query": "Being queried by the courier",
    "manifested": "On its way between depots", "ready-for-dispatch": "Ready to leave the depot",
    "delivery-scheduled": "Delivery scheduled", "delivery-assigned": "Driver assigned to deliver",
}


def plain_status(status: str) -> str:
    return PLAIN.get(status or "", (status or "").replace("-", " ").capitalize() or "Booked")


def order_status_for(status: str) -> Optional[str]:
    """The order step a courier status stands for, or None when it changes nothing."""
    if status in DELIVERED:
        return "delivered"
    if status in MOVING:
        return "dispatched"
    return None


# ── database ──────────────────────────────────────────────────────────────────

def _client():
    from vula.commerce import service as cs
    return cs._client()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _encrypt(creds: dict) -> dict:
    from vula.payments import _encrypt_creds
    return _encrypt_creds(creds)


def _decrypt(creds: Optional[dict]) -> dict:
    from vula.payments import _decrypt_creds
    return _decrypt_creds(creds)


def list_providers(tenant_id: str) -> list:
    """Connected couriers, never the key itself."""
    try:
        rows = (_client().table("vula_courier_providers")
                .select("id,provider,mode,is_default,active,webhook_token,credentials,updated_at")
                .eq("tenant_id", tenant_id).execute().data or [])
    except Exception as exc:
        logger.debug("courier providers list skipped (run migration 297?): %s", exc)
        return []
    for r in rows:
        creds = _decrypt(r.pop("credentials", {}))
        r["connected"] = bool(creds.get("api_key"))
        r["label"] = PROVIDER_LABELS.get(r["provider"], r["provider"])
    return rows


def provider_row(tenant_id: str, provider: Optional[str] = None) -> Optional[dict]:
    """The named courier, else the default, with its key decrypted. None when not connected."""
    try:
        rows = (_client().table("vula_courier_providers").select("*")
                .eq("tenant_id", tenant_id).eq("active", True).execute().data or [])
    except Exception:
        return None
    rows = [r for r in rows if r.get("provider") in _REGISTRY]
    for r in rows:
        r["credentials"] = _decrypt(r.get("credentials"))
    rows = [r for r in rows if (r["credentials"] or {}).get("api_key")]
    if provider:
        return next((r for r in rows if r["provider"] == provider), None)
    return next((r for r in rows if r.get("is_default")), rows[0] if rows else None)


def upsert_provider(tenant_id: str, provider: str, api_key: str, mode: str = "live",
                    is_default: bool = False) -> dict:
    if provider not in _REGISTRY:
        raise ValueError(f"Unknown courier {provider}")
    if mode not in PROVIDER_MODES[provider]:
        raise ValueError(f"{PROVIDER_LABELS[provider]} has no {mode} mode")
    import uuid
    db = _client()
    existing = (db.table("vula_courier_providers").select("id,webhook_token,is_default")
                .eq("tenant_id", tenant_id).eq("provider", provider).limit(1).execute().data or [])
    others = (db.table("vula_courier_providers").select("id")
              .eq("tenant_id", tenant_id).neq("provider", provider).execute().data or [])
    is_default = bool(is_default or not others or (existing and existing[0].get("is_default")))
    if is_default and others:
        db.table("vula_courier_providers").update({"is_default": False}).eq("tenant_id", tenant_id).execute()
    row = {"tenant_id": tenant_id, "provider": provider, "credentials": _encrypt({"api_key": api_key}),
           "mode": mode, "is_default": is_default, "active": True, "updated_at": _now()}
    if existing:
        db.table("vula_courier_providers").update(row).eq("id", existing[0]["id"]) \
            .eq("tenant_id", tenant_id).execute()
        row["id"], row["webhook_token"] = existing[0]["id"], existing[0].get("webhook_token")
    else:
        row["id"], row["webhook_token"] = str(uuid.uuid4()), secrets.token_hex(24)
        db.table("vula_courier_providers").insert(row).execute()
    row.pop("credentials", None)
    return row


def delete_provider(tenant_id: str, provider: str) -> None:
    _client().table("vula_courier_providers").delete() \
        .eq("tenant_id", tenant_id).eq("provider", provider).execute()


def set_default(tenant_id: str, provider: str) -> None:
    db = _client()
    db.table("vula_courier_providers").update({"is_default": False}).eq("tenant_id", tenant_id).execute()
    db.table("vula_courier_providers").update({"is_default": True}) \
        .eq("tenant_id", tenant_id).eq("provider", provider).execute()
