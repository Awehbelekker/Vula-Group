"""
vula/commerce/bring_back.py — bringing customers back: the basket they left, and "order again?".

2026-10-13 (the WhatsApp shop plan, Ian: "modern, rich and fun"). Two scheduled jobs, both in the
⏰ Scheduling tab (job_config) so a shop can switch them off or on:

  basket_reminder  A customer filled their WhatsApp cart 1–20 hours ago and didn't check out:
                   one reminder — a photo of what's in it, the cart, and Checkout / Keep
                   shopping. Only inside WhatsApp's 24-hour window (a service message about
                   their own cart, never a template), once per cart. On for WhatsApp shops.
  reorder_nudge    A customer's last order was delivered REORDER_AFTER_DAYS ago and they haven't
                   ordered since: "Fancy the same again?" on the shop's approved template, with
                   what they had. Marketing, so OFF until the shop switches it on.

Both leave out anyone who opted out (commerce_consent, POPIA) and send at most once per cart /
per order (the outbound send-once key, shared by every worker).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

log = logging.getLogger(__name__)

REMIND_AFTER_HOURS = 1
REMIND_WITHIN_HOURS = 20          # well inside the 24-hour window the cart was filled in
REORDER_AFTER_DAYS = 30


def _opted_out(tenant_id: str) -> set:
    from vula.api.commerce import _suppressed_phones
    return _suppressed_phones(tenant_id)


def _digits(phone: Optional[str]) -> str:
    d = "".join(c for c in (phone or "") if c.isdigit())
    return "27" + d[1:] if d.startswith("0") else d


def _when(value: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


async def basket_reminders(tenant_id: str) -> int:
    """Remind customers about a WhatsApp cart they didn't check out. Returns how many were sent."""
    from vula.api.whatsapp import _claim_outbound, _resolve_wa, _send_wa_buttons, _wa_number
    from vula.commerce import product_families as pf
    from vula.commerce import service, web_basket
    creds = await _resolve_wa(tenant_id)
    if not creds:
        return 0
    now = datetime.now(timezone.utc)
    try:
        carts = await service.get_abandoned_carts(tenant_id, hours_old=REMIND_AFTER_HOURS)
    except Exception as exc:
        log.debug("basket reminders skipped for %s: %s", tenant_id, exc)
        return 0
    out, sent = _opted_out(tenant_id), 0
    for c in carts:
        session = str(c.get("session_id") or "")
        phone = _digits(c.get("customer_phone") or session)
        items = c.get("commerce_cart_items") or []
        when = _when(c.get("updated_at"))
        # WhatsApp carts only: a web basket has no phone, a manual/admin cart isn't the customer's.
        if not items or len(phone) < 9 or session.startswith(("web-", "manual-", "admin:")) \
                or phone in out or not when or now - when > timedelta(hours=REMIND_WITHIN_HOURS):
            continue
        key = f"basket:{c['id']}:{c.get('updated_at')}"
        if not await asyncio.to_thread(_claim_outbound, tenant_id, key):
            continue
        lines, subtotal, photo = [], 0, None
        for it in items[:8]:
            q = float(it.get("quantity") or 0)
            cents = int(round(q * int(it.get("unit_price_cents") or 0)))
            subtotal += cents
            prod = it.get("commerce_products") or {}
            photo = photo or prod.get("image_url")
            lines.append(f"• {int(q) if q == int(q) else q} × {prod.get('name') or 'item'}")
        body = ("👋 Still thinking it over? Your cart is saved:\n\n" + "\n".join(lines)
                + f"\n\nSubtotal: {pf.rands(subtotal)}\nTap *Checkout* when you're ready.")
        ok = await _send_wa_buttons(creds, _wa_number(phone), body,
                                    [{"id": "cart_checkout", "title": "Checkout"},
                                     {"id": "cart_more", "title": "Keep shopping"}],
                                    image_url=web_basket.sized(photo, 800) if photo else None)
        if ok:
            sent += 1
        else:   # outside the window or Meta refused — the key stays claimed: never retried, never a template
            log.info("basket reminder for %s not delivered (%s)", tenant_id, key)
    if sent:
        log.info("basket reminders %s: %d sent", tenant_id, sent)
    return sent


async def reorder_nudges(tenant_id: str, template: str) -> int:
    """"Fancy the same again?" to customers whose last order was delivered REORDER_AFTER_DAYS
    ago and who haven't ordered since. Returns how many were sent."""
    from vula.api import tenants
    from vula.api.whatsapp import _claim_outbound, _release_outbound, send_template
    from vula.commerce import service
    try:
        rows = await service.get_reorder_candidates(tenant_id, days_ago=REORDER_AFTER_DAYS)
    except Exception as exc:
        log.debug("reorder nudges skipped for %s: %s", tenant_id, exc)
        return 0
    out, shop, sent, seen = _opted_out(tenant_id), tenants.display_name(tenant_id), 0, set()
    for o in rows:
        phone = _digits(o.get("customer_phone"))
        if len(phone) < 9 or phone in out or phone in seen:
            continue
        seen.add(phone)
        latest = service.orders_for_phone(tenant_id, phone, "id,created_at", limit=1)
        if latest and o.get("id") and latest[0].get("id") != o["id"]:
            continue                                   # they've ordered since
        key = f"reorder:{phone}:{o.get('id') or o.get('created_at')}"
        if not await asyncio.to_thread(_claim_outbound, tenant_id, key):
            continue
        names = [i.get("product_name") for i in (o.get("commerce_order_items") or []) if i.get("product_name")]
        what = ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
        first = (o.get("customer_name") or "there").split(" ")[0] or "there"
        if await send_template(tenant_id, phone, template, [first, shop, (what or "your usual")[:120]]):
            sent += 1
        else:
            await asyncio.to_thread(_release_outbound, tenant_id, key)
    if sent:
        log.info("reorder nudges %s: %d sent", tenant_id, sent)
    return sent
