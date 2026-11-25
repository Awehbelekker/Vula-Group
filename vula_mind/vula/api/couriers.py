"""
vula/api/couriers.py — connect a courier and book it from an order (vula/commerce/shipping.py).

Under /v1/commerce/{tenant}/admin/…, so the tenant guard already applies (an accountant's
login reaches none of it). Connecting, removing and the collection details are for the owner or
a manager; booking is for whoever packs the orders.

    GET    …/admin/couriers                          connected + available couriers, the settings
    POST   …/admin/couriers                          {provider, api_key, mode} — checked with the courier first
    POST   …/admin/couriers/{provider}/default
    DELETE …/admin/couriers/{provider}
    PUT    …/admin/couriers/settings                 collection address + contact, usual parcel
    GET    …/admin/couriers/pickup-points?near=      lockers and counters near a place
    GET    …/admin/couriers/account                  what is owed or left with the courier (owner/manager)
    GET    …/admin/couriers/parcels                  parcels sent without an order
    POST   …/admin/couriers/parcels/quote            prices — books nothing
    POST   …/admin/couriers/parcels                  {request_id, service_code, expected_cents, recipient, …} — books
    GET    …/admin/orders/{order}/shipments          this order's bookings
    POST   …/admin/orders/{order}/shipments/quote    prices — books nothing
    POST   …/admin/orders/{order}/shipments          {service_code, expected_cents, …} — books
    POST   …/admin/shipments/{id}/refresh            fetch the courier's news now
    POST   …/admin/shipments/{id}/cancel
    GET    …/admin/shipments/{id}/label[?sticker=1]  a link to the waybill PDF (the courier's, 24 h)

    POST   /v1/couriers/webhook/{tenant}/{provider}/{token}   the courier's tracking news
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from vula import couriers
from vula.commerce import shipping

log = logging.getLogger(__name__)
router = APIRouter(tags=["couriers"])            # mounted at /v1/commerce
webhook_router = APIRouter(tags=["couriers"])    # mounted at /v1/couriers


async def _owner_or_manager(request: Request, tenant_id: str) -> None:
    from vula.api.commerce import _may_apply_stock
    if not await _may_apply_stock(request, tenant_id):
        raise HTTPException(status_code=403, detail="Only the owner or a manager can change the courier set-up.")


async def _actor(request: Request, tenant_id: str) -> dict:
    try:
        from vula.api.tenant_auth import member_scope
        scope = await member_scope(request.headers.get("authorization", ""), tenant_id)
        return {"email": (scope or {}).get("email")}
    except Exception:  # noqa: BLE001
        return {}


def _refuse(exc: Exception) -> HTTPException:
    detail = {"message": str(exc), **getattr(exc, "detail", {})} if getattr(exc, "detail", None) else str(exc)
    return HTTPException(status_code=409 if getattr(exc, "detail", {}).get("price_cents") else 400, detail=detail)


def _webhook_url(tenant_id: str, row: dict) -> Optional[str]:
    if not row.get("webhook_token"):
        return None
    from config import settings
    base = (settings.public_base_url or "").rstrip("/")
    return f"{base}/v1/couriers/webhook/{tenant_id}/{row['provider']}/{row['webhook_token']}"


def _overview(tenant_id: str, full: bool) -> dict:
    connected = couriers.list_providers(tenant_id)
    for r in connected:
        # The address is a secret (it is the only thing a courier's call is checked against).
        r["webhook_url"] = _webhook_url(tenant_id, r) if full and r["provider"] == "tcg" else None
        r.pop("webhook_token", None)
    return {
        "connected": connected,
        "available": [{"id": k, "label": couriers.PROVIDER_LABELS[k], "modes": list(couriers.PROVIDER_MODES[k]),
                       "help": couriers.PROVIDER_HELP[k]} for k in couriers.PROVIDER_FIELDS],
        "settings": shipping.get_settings(tenant_id),
    }


@router.get("/{tenant_id}/admin/couriers")
async def courier_overview(tenant_id: str, request: Request) -> dict:
    from vula.api.commerce import _may_apply_stock
    return _overview(tenant_id, await _may_apply_stock(request, tenant_id))


@router.post("/{tenant_id}/admin/couriers")
async def courier_connect(tenant_id: str, body: dict, request: Request) -> dict:
    await _owner_or_manager(request, tenant_id)
    provider = str((body or {}).get("provider") or "")
    key = str((body or {}).get("api_key") or "").strip()
    mode = str((body or {}).get("mode") or "live")
    courier = couriers.get_courier(provider)
    if not courier or not key:
        raise HTTPException(status_code=400, detail="Choose a courier and paste its API key.")
    if mode not in couriers.PROVIDER_MODES[provider]:
        raise HTTPException(status_code=400, detail=f"{couriers.PROVIDER_LABELS[provider]} has no {mode} mode.")
    try:
        await courier.check({"api_key": key}, mode)          # a key is only saved once it works
    except couriers.CourierError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        couriers.upsert_provider(tenant_id, provider, key, mode, bool((body or {}).get("is_default")))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    shipping._audit(tenant_id, await _actor(request, tenant_id), "courier_connected", courier=provider, mode=mode)
    return _overview(tenant_id, True)


@router.post("/{tenant_id}/admin/couriers/{provider}/default")
async def courier_default(tenant_id: str, provider: str, request: Request) -> dict:
    await _owner_or_manager(request, tenant_id)
    couriers.set_default(tenant_id, provider)
    return _overview(tenant_id, True)


@router.put("/{tenant_id}/admin/couriers/settings")
async def courier_settings(tenant_id: str, body: dict, request: Request) -> dict:
    await _owner_or_manager(request, tenant_id)
    try:
        return {"settings": shipping.save_settings(tenant_id, body or {})}
    except shipping.ShippingError as exc:
        raise _refuse(exc) from exc


@router.post("/{tenant_id}/admin/couriers/address-search")
async def courier_address_search(tenant_id: str, body: dict) -> dict:
    """Addresses matching what was typed, split into street, suburb, town, province and postal code
    with their position (the address forms' Find). Asked on a button press, never per key."""
    from vula.commerce import address_search
    q = str((body or {}).get("query") or "").strip()
    if len(q) < 5:
        raise HTTPException(status_code=400, detail="Type the street and the suburb or town, then press Find.")
    try:
        return {"results": await address_search.search(q)}
    except address_search.LookupDown as exc:
        raise HTTPException(status_code=502, detail="The address lookup isn't answering right now. "
                                                    "Type the address in below, or try again.") from exc


@router.post("/{tenant_id}/admin/couriers/address-suggest")
async def courier_address_suggest(tenant_id: str, body: dict) -> dict:
    """Addresses for what has been typed so far (the dropdown under the address box). Never an
    error: with nothing to offer it answers an empty list and the form works by hand."""
    from vula.commerce import address_search
    return {"results": await address_search.suggest(str((body or {}).get("query") or ""))}


@router.post("/{tenant_id}/admin/couriers/address-at")
async def courier_address_at(tenant_id: str, body: dict) -> dict:
    """The address at a pin dropped on the map, split into the form's fields."""
    from vula.commerce import address_search
    try:
        lat, lng = float((body or {}).get("lat")), float((body or {}).get("lng"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Drop the pin on the map first.") from exc
    try:
        found = await address_search.at(lat, lng)
    except address_search.LookupDown as exc:
        raise HTTPException(status_code=502, detail="The map couldn't name that spot right now. "
                                                    "Type the address in, or try again.") from exc
    if not found:
        raise HTTPException(status_code=404, detail="There's no South African address at that spot. Move the pin onto a street.")
    return {"address": found}


@router.get("/{tenant_id}/admin/couriers/pickup-points")
async def courier_pickup_points(tenant_id: str, near: str = "", provider: Optional[str] = None,
                                lat: Optional[float] = None, lng: Optional[float] = None) -> dict:
    try:
        return {"points": await shipping.pickup_points(tenant_id, provider=provider, near=near, lat=lat, lng=lng)}
    except (shipping.ShippingError, couriers.CourierError) as exc:
        raise _refuse(exc) from exc


@router.get("/{tenant_id}/admin/couriers/account")
async def courier_account(tenant_id: str, request: Request, provider: Optional[str] = None) -> dict:
    await _owner_or_manager(request, tenant_id)
    try:
        return {"account": await shipping.account(tenant_id, provider)}
    except shipping.ShippingError as exc:
        raise _refuse(exc) from exc


@router.get("/{tenant_id}/admin/couriers/parcels")
async def courier_parcels(tenant_id: str) -> dict:
    return {"shipments": shipping.parcels(tenant_id), "connected": bool(couriers.provider_row(tenant_id))}


def _parcel_args(b: dict) -> dict:
    return {"provider": b.get("provider"), "address": b.get("address"), "pickup_point": b.get("pickup_point"),
            "parcel": b.get("parcel"), "recipient": b.get("recipient")}


@router.post("/{tenant_id}/admin/couriers/parcels/quote")
async def courier_parcel_quote(tenant_id: str, body: dict) -> dict:
    try:
        return await shipping.quote_parcel(tenant_id, **_parcel_args(body or {}))
    except (shipping.ShippingError, couriers.CourierError) as exc:
        raise _refuse(exc) from exc


@router.post("/{tenant_id}/admin/couriers/parcels")
async def courier_parcel_book(tenant_id: str, body: dict, request: Request) -> dict:
    b = body or {}
    try:
        cents = int(b.get("expected_cents"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Get the prices first, then book one of them.")
    if not b.get("service_code"):
        raise HTTPException(status_code=400, detail="Get the prices first, then book one of them.")
    try:
        return {"shipment": await shipping.book_parcel(
            tenant_id, request_id=str(b.get("request_id") or ""), service_code=str(b["service_code"]),
            expected_cents=cents, reference=str(b.get("reference") or ""), instructions=str(b.get("instructions") or ""),
            actor=await _actor(request, tenant_id), **_parcel_args(b))}
    except (shipping.ShippingError, couriers.CourierError) as exc:
        raise _refuse(exc) from exc


@router.delete("/{tenant_id}/admin/couriers/{provider}")
async def courier_remove(tenant_id: str, provider: str, request: Request) -> dict:
    await _owner_or_manager(request, tenant_id)
    couriers.delete_provider(tenant_id, provider)
    shipping._audit(tenant_id, await _actor(request, tenant_id), "courier_removed", courier=provider)
    return _overview(tenant_id, True)


@router.get("/{tenant_id}/admin/orders/{order_id}/shipments")
async def order_shipments(tenant_id: str, order_id: str) -> dict:
    return {"shipments": shipping.shipments_for(tenant_id, order_id),
            "connected": bool(couriers.provider_row(tenant_id))}


@router.post("/{tenant_id}/admin/orders/{order_id}/shipments/quote")
async def order_shipment_quote(tenant_id: str, order_id: str, body: dict) -> dict:
    b = body or {}
    try:
        return await shipping.quote(tenant_id, order_id, provider=b.get("provider"), address=b.get("address"),
                                    pickup_point=b.get("pickup_point"), parcel=b.get("parcel"))
    except (shipping.ShippingError, couriers.CourierError) as exc:
        raise _refuse(exc) from exc


@router.post("/{tenant_id}/admin/orders/{order_id}/shipments")
async def order_shipment_book(tenant_id: str, order_id: str, body: dict, request: Request) -> dict:
    b = body or {}
    try:
        cents = int(b.get("expected_cents"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Get the prices first, then book one of them.")
    if not b.get("service_code"):
        raise HTTPException(status_code=400, detail="Get the prices first, then book one of them.")
    try:
        return {"shipment": await shipping.book(
            tenant_id, order_id, service_code=str(b["service_code"]), expected_cents=cents,
            provider=b.get("provider"), address=b.get("address"), pickup_point=b.get("pickup_point"),
            parcel=b.get("parcel"), actor=await _actor(request, tenant_id))}
    except (shipping.ShippingError, couriers.CourierError) as exc:
        raise _refuse(exc) from exc


@router.post("/{tenant_id}/admin/shipments/{shipment_id}/refresh")
async def shipment_refresh(tenant_id: str, shipment_id: str) -> dict:
    try:
        return {"shipment": await shipping.refresh(tenant_id, shipment_id)}
    except shipping.ShippingError as exc:
        raise _refuse(exc) from exc


@router.post("/{tenant_id}/admin/shipments/{shipment_id}/cancel")
async def shipment_cancel(tenant_id: str, shipment_id: str, request: Request) -> dict:
    try:
        return {"shipment": await shipping.cancel(tenant_id, shipment_id, actor=await _actor(request, tenant_id))}
    except shipping.ShippingError as exc:
        raise _refuse(exc) from exc


@router.get("/{tenant_id}/admin/shipments/{shipment_id}/label")
async def shipment_label(tenant_id: str, shipment_id: str, sticker: bool = False) -> dict:
    try:
        return {"url": await shipping.label(tenant_id, shipment_id, sticker=sticker)}
    except shipping.ShippingError as exc:
        raise _refuse(exc) from exc


@webhook_router.post("/webhook/{tenant_id}/{provider}/{token}")
async def courier_webhook(tenant_id: str, provider: str, token: str, request: Request) -> dict:
    """Always 200: a courier that gets errors stops calling. The body is only a pointer to a
    waybill; nothing in it is believed (shipping.handle_webhook)."""
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return {"received": True}
    try:
        await shipping.handle_webhook(tenant_id, provider, token, payload)
    except Exception as exc:  # noqa: BLE001
        log.warning("courier webhook handling failed for %s/%s: %s", tenant_id, provider, exc)
    return {"received": True}
