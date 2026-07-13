"""
vula/bookings/service.py — bookable services, availability, and appointments.

All times are stored in UTC (timestamptz). Slot generation happens in the tenant's
local timezone (default Africa/Johannesburg — SA has no DST, so a fixed +02:00 is
also correct) then converted to UTC for storage and comparison.

The availability engine is deliberately simple and single-resource (one chair / one
practitioner): a slot is free if it fits inside working hours, is far enough in the
future (lead time), and doesn't overlap an existing pending/confirmed booking (+buffer).
Multi-staff resourcing is a later enhancement.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

log = logging.getLogger(__name__)

_SAST = timezone(timedelta(hours=2))                # fallback if zoneinfo unavailable
_ACTIVE = ("pending", "confirmed")                  # statuses that occupy a slot
_DEFAULTS = {
    "timezone": "Africa/Johannesburg", "slot_minutes": 30, "workdays": [1, 2, 3, 4, 5],
    "open_time": "09:00", "close_time": "17:00", "buffer_min": 0, "lead_hours": 2,
    "horizon_days": 30,
}


def _client():
    from vula.commerce import service as cs
    return cs._client()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _tz(name: str):
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return _SAST


def _hm(value: str, fallback: str) -> tuple[int, int]:
    try:
        h, m = (value or fallback).split(":")
        return int(h), int(m)
    except Exception:
        h, m = fallback.split(":")
        return int(h), int(m)


# ── Settings ──────────────────────────────────────────────────────────────────

async def get_settings(tenant_id: str) -> Dict[str, Any]:
    try:
        rows = (_client().table("commerce_booking_settings").select("*")
                .eq("tenant_id", tenant_id).limit(1).execute().data or [])
    except Exception as exc:
        log.debug("booking settings read skipped (run migration 050?): %s", exc)
        rows = []
    cfg = dict(_DEFAULTS)
    if rows:
        for k, v in rows[0].items():
            if v is not None:
                cfg[k] = v
    return cfg


async def save_settings(tenant_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    allowed = {"timezone", "slot_minutes", "workdays", "open_time", "close_time",
               "buffer_min", "lead_hours", "horizon_days"}
    row = {k: v for k, v in patch.items() if k in allowed}
    row["tenant_id"] = tenant_id
    row["updated_at"] = _now_utc().isoformat()
    _client().table("commerce_booking_settings").upsert(row, on_conflict="tenant_id").execute()
    return await get_settings(tenant_id)


# ── Services (bookable types) ─────────────────────────────────────────────────

async def list_services(tenant_id: str, active_only: bool = True) -> List[dict]:
    q = _client().table("commerce_services").select("*").eq("tenant_id", tenant_id)
    if active_only:
        q = q.eq("active", True)
    try:
        return q.order("sort").order("name").execute().data or []
    except Exception as exc:
        log.debug("list_services skipped: %s", exc)
        return []


async def upsert_service(tenant_id: str, body: Dict[str, Any]) -> dict:
    row = {
        "tenant_id": tenant_id,
        "name": (body.get("name") or "").strip(),
        "description": body.get("description"),
        "duration_min": int(body.get("duration_min") or 30),
        "price_cents": int(body.get("price_cents") or 0),
        "active": bool(body.get("active", True)),
        "sort": int(body.get("sort") or 0),
    }
    if not row["name"]:
        raise ValueError("service name is required")
    if body.get("id"):
        res = (_client().table("commerce_services").update(row)
               .eq("tenant_id", tenant_id).eq("id", body["id"]).execute())
        return (res.data or [row])[0]
    res = _client().table("commerce_services").insert(row).execute()
    return (res.data or [row])[0]


async def delete_service(tenant_id: str, service_id: str) -> None:
    _client().table("commerce_services").delete() \
        .eq("tenant_id", tenant_id).eq("id", service_id).execute()


async def _get_service(tenant_id: str, service_id: Optional[str]) -> Optional[dict]:
    if not service_id:
        return None
    rows = (_client().table("commerce_services").select("*")
            .eq("tenant_id", tenant_id).eq("id", service_id).limit(1).execute().data or [])
    return rows[0] if rows else None


# ── Bookings ──────────────────────────────────────────────────────────────────

def _overlaps(start: datetime, end: datetime, busy: List[tuple[datetime, datetime]],
              buffer_min: int) -> bool:
    buf = timedelta(minutes=buffer_min)
    for bs, be in busy:
        if start < (be + buf) and (end + buf) > bs:
            return True
    return False


async def _busy(tenant_id: str, day_start: datetime, day_end: datetime) -> List[tuple[datetime, datetime]]:
    try:
        rows = (_client().table("commerce_bookings").select("start_at,end_at,status")
                .eq("tenant_id", tenant_id).in_("status", list(_ACTIVE))
                .gte("start_at", day_start.isoformat()).lt("start_at", day_end.isoformat())
                .execute().data or [])
    except Exception as exc:
        log.debug("booking busy-lookup skipped (run migration 050?): %s", exc)
        return []
    out: List[tuple[datetime, datetime]] = []
    for r in rows:
        try:
            out.append((_parse(r["start_at"]), _parse(r["end_at"])))
        except Exception:
            continue
    return out


def _parse(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def availability(tenant_id: str, date_str: str, service_id: Optional[str] = None,
                       duration_min: Optional[int] = None) -> Dict[str, Any]:
    """Free slots for one local date. Returns {date, slots:[{start_local, start_utc, label}]}."""
    cfg = await get_settings(tenant_id)
    tz = _tz(cfg["timezone"])
    try:
        y, mo, d = (int(x) for x in date_str.split("-"))
        local_day = datetime(y, mo, d, tzinfo=tz)
    except Exception:
        raise ValueError("date must be YYYY-MM-DD")

    if local_day.isoweekday() not in (cfg["workdays"] or []):
        return {"date": date_str, "slots": [], "closed": True}

    svc = await _get_service(tenant_id, service_id)
    dur = int(duration_min or (svc or {}).get("duration_min") or cfg["slot_minutes"])
    step = int(cfg["slot_minutes"])
    oh, om = _hm(cfg["open_time"], "09:00")
    ch, cm = _hm(cfg["close_time"], "17:00")
    open_dt = local_day.replace(hour=oh, minute=om)
    close_dt = local_day.replace(hour=ch, minute=cm)
    earliest = _now_utc() + timedelta(hours=int(cfg["lead_hours"]))

    day_start_utc = open_dt.astimezone(timezone.utc)
    day_end_utc = close_dt.astimezone(timezone.utc)
    busy = await _busy(tenant_id, day_start_utc, day_end_utc)

    slots = []
    cur = open_dt
    while cur + timedelta(minutes=dur) <= close_dt:
        start_utc = cur.astimezone(timezone.utc)
        end_utc = (cur + timedelta(minutes=dur)).astimezone(timezone.utc)
        if start_utc >= earliest and not _overlaps(start_utc, end_utc, busy, int(cfg["buffer_min"])):
            slots.append({
                "start_local": cur.strftime("%Y-%m-%dT%H:%M"),
                "start_utc": start_utc.isoformat(),
                "label": cur.strftime("%H:%M"),
            })
        cur += timedelta(minutes=step)
    return {"date": date_str, "slots": slots, "duration_min": dur,
            "service": (svc or {}).get("name")}


async def create_booking(tenant_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Book a slot. `start` may be local (YYYY-MM-DDTHH:MM) or a full ISO/UTC string.
    Returns {booking} or {error} if the slot is taken / outside hours."""
    cfg = await get_settings(tenant_id)
    tz = _tz(cfg["timezone"])
    svc = await _get_service(tenant_id, body.get("service_id"))
    dur = int(body.get("duration_min") or (svc or {}).get("duration_min") or cfg["slot_minutes"])

    raw = (body.get("start") or "").strip()
    if not raw:
        return {"error": "start time is required"}
    try:
        if raw.endswith("Z") or "+" in raw[10:]:
            start_utc = _parse(raw)
        else:
            naive = datetime.fromisoformat(raw)
            start_utc = naive.replace(tzinfo=tz).astimezone(timezone.utc)
    except Exception:
        return {"error": "invalid start time (use YYYY-MM-DDTHH:MM)"}

    end_utc = start_utc + timedelta(minutes=dur)
    if start_utc < _now_utc():
        return {"error": "that time is in the past"}

    # Concurrency-safe enough for single-resource: re-check overlap immediately before insert.
    busy = await _busy(tenant_id, start_utc - timedelta(hours=6), end_utc + timedelta(hours=6))
    if _overlaps(start_utc, end_utc, busy, int(cfg["buffer_min"])):
        return {"error": "that slot is no longer available"}

    row = {
        "tenant_id": tenant_id,
        "service_id": body.get("service_id"),
        "service_name": (svc or {}).get("name") or body.get("service_name"),
        "customer_name": body.get("customer_name"),
        "customer_phone": body.get("customer_phone"),
        "customer_email": body.get("customer_email"),
        "start_at": start_utc.isoformat(),
        "end_at": end_utc.isoformat(),
        "status": body.get("status") or "confirmed",
        "notes": body.get("notes"),
        "channel": body.get("channel") or "web",
    }
    res = _client().table("commerce_bookings").insert(row).execute()
    booking = (res.data or [row])[0]
    booking["start_local"] = start_utc.astimezone(tz).strftime("%Y-%m-%d %H:%M")
    return {"booking": booking}


async def list_bookings(tenant_id: str, *, status: Optional[str] = None,
                        from_utc: Optional[str] = None, phone: Optional[str] = None,
                        limit: int = 200) -> List[dict]:
    q = _client().table("commerce_bookings").select("*").eq("tenant_id", tenant_id)
    if status:
        q = q.eq("status", status)
    if phone:
        q = q.eq("customer_phone", phone)
    if from_utc:
        q = q.gte("start_at", from_utc)
    try:
        return q.order("start_at").limit(limit).execute().data or []
    except Exception as exc:
        log.debug("list_bookings skipped: %s", exc)
        return []


async def set_status(tenant_id: str, booking_id: str, status: str) -> dict:
    valid = {"pending", "confirmed", "cancelled", "completed", "no_show"}
    if status not in valid:
        raise ValueError(f"invalid status: {status}")
    res = (_client().table("commerce_bookings")
           .update({"status": status, "updated_at": _now_utc().isoformat()})
           .eq("tenant_id", tenant_id).eq("id", booking_id).execute())
    return (res.data or [{}])[0]
