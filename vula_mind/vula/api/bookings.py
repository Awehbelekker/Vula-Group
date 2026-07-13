"""
vula/api/bookings.py — Bookings / Appointments REST API.

    GET    /v1/bookings/{tenant}/services              list bookable services
    POST   /v1/bookings/{tenant}/services              create / update a service
    DELETE /v1/bookings/{tenant}/services/{id}
    GET    /v1/bookings/{tenant}/settings              availability rules
    PUT    /v1/bookings/{tenant}/settings              update rules
    GET    /v1/bookings/{tenant}/availability?date=YYYY-MM-DD[&service_id=]
    GET    /v1/bookings/{tenant}                        list bookings (?status=&from=&phone=)
    POST   /v1/bookings/{tenant}                        create a booking
    POST   /v1/bookings/{tenant}/{id}/status           set status (cancel/complete/no_show)
    POST   /v1/bookings/{tenant}/jobs/reminders        send due confirmations/reminders (scheduler)
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from vula.bookings import service as bs

log = logging.getLogger(__name__)
router = APIRouter(tags=["bookings"])


# ── Services ──────────────────────────────────────────────────────────────────
class ServiceIn(BaseModel):
    id: Optional[str] = None
    name: str
    description: Optional[str] = None
    duration_min: int = 30
    price_cents: int = 0
    active: bool = True
    sort: int = 0


@router.get("/{tenant_id}/services")
async def list_services(tenant_id: str, all: bool = False) -> dict:
    return {"services": await bs.list_services(tenant_id, active_only=not all)}


@router.post("/{tenant_id}/services")
async def upsert_service(tenant_id: str, body: ServiceIn) -> dict:
    try:
        return {"service": await bs.upsert_service(tenant_id, body.model_dump())}
    except ValueError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        return {"error": f"{exc} (run migration 050?)"}


@router.delete("/{tenant_id}/services/{service_id}")
async def delete_service(tenant_id: str, service_id: str) -> dict:
    await bs.delete_service(tenant_id, service_id)
    return {"removed": service_id}


# ── Settings ──────────────────────────────────────────────────────────────────
class SettingsIn(BaseModel):
    timezone: Optional[str] = None
    slot_minutes: Optional[int] = None
    workdays: Optional[list[int]] = None
    open_time: Optional[str] = None
    close_time: Optional[str] = None
    buffer_min: Optional[int] = None
    lead_hours: Optional[int] = None
    horizon_days: Optional[int] = None


@router.get("/{tenant_id}/settings")
async def get_settings(tenant_id: str) -> dict:
    return {"settings": await bs.get_settings(tenant_id)}


@router.put("/{tenant_id}/settings")
async def put_settings(tenant_id: str, body: SettingsIn) -> dict:
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        return {"settings": await bs.save_settings(tenant_id, patch)}
    except Exception as exc:
        return {"error": f"{exc} (run migration 050?)"}


# ── Availability ──────────────────────────────────────────────────────────────
@router.get("/{tenant_id}/availability")
async def availability(tenant_id: str, date: str = Query(...),
                       service_id: Optional[str] = None,
                       duration_min: Optional[int] = None) -> dict:
    try:
        return await bs.availability(tenant_id, date, service_id, duration_min)
    except ValueError as exc:
        return {"error": str(exc)}


# ── Bookings ──────────────────────────────────────────────────────────────────
class BookingIn(BaseModel):
    service_id: Optional[str] = None
    service_name: Optional[str] = None
    duration_min: Optional[int] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    customer_email: Optional[str] = None
    start: str
    notes: Optional[str] = None
    channel: str = "web"
    status: Optional[str] = None


@router.get("/{tenant_id}")
async def list_bookings(tenant_id: str, status: Optional[str] = None,
                        from_: Optional[str] = Query(None, alias="from"),
                        phone: Optional[str] = None) -> dict:
    return {"bookings": await bs.list_bookings(tenant_id, status=status, from_utc=from_, phone=phone)}


@router.post("/{tenant_id}")
async def create_booking(tenant_id: str, body: BookingIn) -> dict:
    try:
        return await bs.create_booking(tenant_id, body.model_dump())
    except Exception as exc:
        return {"error": f"{exc} (run migration 050?)"}


class StatusIn(BaseModel):
    status: str


@router.post("/{tenant_id}/{booking_id}/status")
async def set_status(tenant_id: str, booking_id: str, body: StatusIn) -> dict:
    try:
        return {"booking": await bs.set_status(tenant_id, booking_id, body.status)}
    except ValueError as exc:
        return {"error": str(exc)}


@router.post("/{tenant_id}/jobs/reminders")
async def job_reminders(tenant_id: str) -> dict:
    from vula.bookings.reminders import send_due_reminders
    return {"sent": await send_due_reminders(tenant_id)}
