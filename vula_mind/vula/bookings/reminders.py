"""
vula/bookings/reminders.py — WhatsApp reminders for upcoming appointments.

Called by the scheduler (POST /v1/bookings/{tenant}/jobs/reminders). Sends a single
reminder per booking in a look-ahead window and marks reminder_sent so it never
double-sends. Best-effort: a send failure just leaves reminder_sent false to retry.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from vula.bookings import service as bs

log = logging.getLogger(__name__)

# Remind for confirmed bookings starting within the next REMINDER_WINDOW hours.
REMINDER_WINDOW_HOURS = 24


async def send_due_reminders(tenant_id: str) -> int:
    """Send reminders for confirmed bookings starting soon. Returns count sent."""
    cfg = await bs.get_settings(tenant_id)
    tz = bs._tz(cfg["timezone"])
    now = bs._now_utc()
    horizon = (now + timedelta(hours=REMINDER_WINDOW_HOURS)).isoformat()

    try:
        rows = (bs._client().table("commerce_bookings")
                .select("id,customer_name,customer_phone,service_name,start_at,reminder_sent,status")
                .eq("tenant_id", tenant_id).eq("status", "confirmed")
                .eq("reminder_sent", False)
                .gte("start_at", now.isoformat()).lte("start_at", horizon)
                .execute().data or [])
    except Exception as exc:
        log.debug("booking reminders skipped (run migration 050?): %s", exc)
        return 0

    if not rows:
        return 0

    from vula.api.whatsapp import _send_reply
    from vula.api import tenants as _tenants
    name = (_tenants.get_config(tenant_id).get("display_name")) or tenant_id

    sent = 0
    for b in rows:
        phone = (b.get("customer_phone") or "").strip()
        if not phone:
            continue
        try:
            when = bs._parse(b["start_at"]).astimezone(tz).strftime("%a %d %b at %H:%M")
        except Exception:
            when = b["start_at"]
        svc = b.get("service_name") or "your appointment"
        body = (f"Hi {b.get('customer_name') or 'there'}! Reminder: *{svc}* with {name} "
                f"is booked for {when}. Reply here to reschedule or cancel. See you then! 📅")
        ok = await _send_reply(phone, body, tenant_id)
        if ok:
            try:
                bs._client().table("commerce_bookings").update(
                    {"reminder_sent": True, "updated_at": bs._now_utc().isoformat()}
                ).eq("tenant_id", tenant_id).eq("id", b["id"]).execute()
                sent += 1
            except Exception as exc:
                log.warning("mark reminder_sent failed for %s: %s", b["id"], exc)
    if sent:
        log.info("Bookings: sent %d reminder(s) for %s", sent, tenant_id)
    return sent
