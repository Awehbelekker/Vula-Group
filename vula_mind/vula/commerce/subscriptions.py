"""
vula/commerce/subscriptions.py — customer product subscriptions (recurring orders).

A standing order ("fish every Friday") that auto-creates a real commerce_order on its
cadence and advances next_run. The scheduler calls process_due() hourly; it only acts on
subscriptions whose next_run has arrived, so it's safe to call often and idempotent per day.

Not to be confused with Vula's own SaaS plan billing (vula_subscriptions / onboarding).
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

CADENCES = ("weekly", "biweekly", "monthly")
_STEP_DAYS = {"weekly": 7, "biweekly": 14}


def _client():
    from vula.commerce import service
    return service._client()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _advance(d: date, cadence: str) -> date:
    if cadence in _STEP_DAYS:
        return d + timedelta(days=_STEP_DAYS[cadence])
    # monthly — same day next month (clamped to month length)
    y, m = (d.year + (1 if d.month == 12 else 0)), (1 if d.month == 12 else d.month + 1)
    import calendar
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _items_total(items: List[dict]) -> int:
    return sum(int(round(float(i.get("quantity") or 0) * int(i.get("unit_price_cents") or 0))) for i in items)


# ── CRUD ──────────────────────────────────────────────────────────────────────

async def create(tenant_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    items = body.get("items") or []
    if not items:
        return {"error": "a subscription needs at least one item"}
    cadence = body.get("cadence") if body.get("cadence") in CADENCES else "weekly"
    try:
        nr = body.get("next_run")
        next_run = date.fromisoformat(nr) if nr else _today() + timedelta(days=_STEP_DAYS.get(cadence, 7))
    except Exception:
        next_run = _today() + timedelta(days=7)
    row = {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "customer_name": body.get("customer_name"),
        "customer_phone": body.get("customer_phone"),
        "customer_email": body.get("customer_email"),
        "delivery_address": body.get("delivery_address"),
        "delivery_slot": body.get("delivery_slot"),
        "items": items,
        "delivery_cents": int(body.get("delivery_cents") or 0),
        "payment_method": body.get("payment_method"),
        "cadence": cadence,
        "next_run": next_run.isoformat(),
        "status": "active",
        "channel": body.get("channel") or "dashboard",
        "note": body.get("note"),
    }
    res = _client().table("commerce_subscriptions").insert(row).execute()
    return {"subscription": (res.data or [row])[0]}


async def list_subs(tenant_id: str, *, status: Optional[str] = None,
                    phone: Optional[str] = None) -> List[dict]:
    q = _client().table("commerce_subscriptions").select("*").eq("tenant_id", tenant_id)
    if status:
        q = q.eq("status", status)
    if phone:
        q = q.eq("customer_phone", phone)
    try:
        return q.order("next_run").execute().data or []
    except Exception as exc:
        log.debug("list subscriptions skipped (run migration 051?): %s", exc)
        return []


async def set_status(tenant_id: str, sub_id: str, status: str) -> dict:
    if status not in ("active", "paused", "cancelled"):
        raise ValueError(f"invalid status: {status}")
    res = (_client().table("commerce_subscriptions")
           .update({"status": status, "updated_at": _now()})
           .eq("tenant_id", tenant_id).eq("id", sub_id).execute())
    return (res.data or [{}])[0]


async def update(tenant_id: str, sub_id: str, patch: Dict[str, Any]) -> dict:
    allowed = {"items", "cadence", "next_run", "delivery_cents", "payment_method",
               "delivery_address", "delivery_slot", "customer_name", "note"}
    row = {k: v for k, v in patch.items() if k in allowed}
    if not row:
        return {}
    row["updated_at"] = _now()
    res = (_client().table("commerce_subscriptions").update(row)
           .eq("tenant_id", tenant_id).eq("id", sub_id).execute())
    return (res.data or [{}])[0]


# ── Order creation + scheduler ────────────────────────────────────────────────

async def _place_order(sub: dict) -> Optional[dict]:
    """Create a real commerce_order from a subscription's items."""
    from vula.commerce import service
    items = sub.get("items") or []
    if not items:
        return None
    subtotal = _items_total(items)
    delivery = int(sub.get("delivery_cents") or 0)
    display_id = await service._next_order_display_id(sub["tenant_id"])
    order = {
        "id": str(uuid.uuid4()),
        "display_id": display_id,
        "tenant_id": sub["tenant_id"],
        "customer_phone": sub.get("customer_phone"),
        "customer_name": sub.get("customer_name"),
        "customer_email": sub.get("customer_email"),
        "delivery_address": sub.get("delivery_address"),
        "delivery_slot": sub.get("delivery_slot") or "morning",
        "delivery_notes": f"Recurring order ({sub.get('cadence')})",
        "subtotal_cents": subtotal,
        "delivery_cents": delivery,
        "total_cents": subtotal + delivery,
        "status": "pending_payment",
        "channel": "subscription",
        "payment_method": sub.get("payment_method"),
        "created_at": _now(),
        "updated_at": _now(),
    }
    db = _client()
    try:
        res = db.table("commerce_orders").insert(order).execute()
    except Exception as exc:
        method = order.pop("payment_method", None)
        if method:
            order["delivery_notes"] = f"[pay:{method}] " + (order.get("delivery_notes") or "")
        log.warning("subscription order insert retried without payment_method: %s", exc)
        res = db.table("commerce_orders").insert(order).execute()
    order_id = res.data[0]["id"]
    order_items = [{
        "id": str(uuid.uuid4()), "order_id": order_id,
        "product_id": i.get("product_id"),
        "product_name": i.get("product_name") or "",
        "quantity": float(i.get("quantity") or 1),
        "unit_price_cents": int(i.get("unit_price_cents") or 0),
        "total_cents": int(round(float(i.get("quantity") or 1) * int(i.get("unit_price_cents") or 0))),
    } for i in items]
    try:
        db.table("commerce_order_items").insert(order_items).execute()
    except Exception as exc:
        log.warning("subscription order items insert failed: %s", exc)
    return res.data[0]


async def _notify(sub: dict, order: dict) -> None:
    """Tell the customer their recurring order is in, with the total + how to pay."""
    phone = (sub.get("customer_phone") or "").strip()
    if not phone:
        return
    try:
        from vula.api.whatsapp import _send_reply
        total = f"R{(order.get('total_cents') or 0) / 100:.2f}"
        pm = sub.get("payment_method")
        pay = ("We'll collect payment on delivery." if pm == "cod"
               else "Reply here and we'll send your payment link." if pm == "online"
               else "Reply here to arrange payment." )
        await _send_reply(phone, (
            f"Hi {sub.get('customer_name') or 'there'}! Your standing order *{order.get('display_id')}* "
            f"is booked in ({total}). {pay} Reply STOP anytime to pause. 🐟"
        ), sub["tenant_id"])
    except Exception as exc:
        log.debug("subscription notify skipped: %s", exc)


async def process_due(tenant_id: Optional[str] = None) -> int:
    """Create orders for all active subscriptions whose next_run has arrived. Returns count."""
    today = _today().isoformat()
    q = (_client().table("commerce_subscriptions").select("*")
         .eq("status", "active").lte("next_run", today))
    if tenant_id:
        q = q.eq("tenant_id", tenant_id)
    try:
        due = q.execute().data or []
    except Exception as exc:
        log.debug("process_due subscriptions skipped (run migration 051?): %s", exc)
        return 0

    made = 0
    for sub in due:
        try:
            order = await _place_order(sub)
            if not order:
                continue
            # advance next_run from the scheduled date (not today) so cadence doesn't drift.
            try:
                base = date.fromisoformat(sub["next_run"])
            except Exception:
                base = _today()
            nxt = _advance(base, sub.get("cadence") or "weekly")
            # if we're catching up (missed runs), keep advancing past today
            while nxt <= _today():
                nxt = _advance(nxt, sub.get("cadence") or "weekly")
            _client().table("commerce_subscriptions").update(
                {"next_run": nxt.isoformat(), "last_run": _now(), "updated_at": _now()}
            ).eq("id", sub["id"]).execute()
            await _notify(sub, order)
            made += 1
        except Exception as exc:
            log.warning("subscription %s run failed: %s", sub.get("id"), exc)
    if made:
        log.info("Subscriptions: created %d recurring order(s)", made)
    return made
