"""
vula/commerce/order_updates.py — the customer hears how their order is going, in WhatsApp.

2026-10-13 (Ian: modern, rich and fun; "order progress" from the plan): when the shop moves an
order on — in the dashboard or by telling Vula on WhatsApp — the customer gets one message for
that step, with a photo of what they bought and a button:

    packing     📦 We're packing your order …            [View order]
    dispatched  🚚 Your order is on its way …            [View order]
    delivered   🏠 Delivered — how was it?   [😍 Loved it] [👍 Good] [😕 Not great]

"View order" opens the order's slip page (vula/commerce/order_slip.py). A rating tap is recorded
like a typed 1–5 (whatsapp._record_rating). These are service messages about an order the
customer placed, so opting out of marketing doesn't stop them; outside WhatsApp's 24-hour window
the shop's approved "order_update" template carries the same news without the buttons.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

STEPS = {
    "packing": "📦 We're packing your order {order} now.",
    "dispatched": "🚚 Your order {order} is on its way!{slot}",
    "delivered": "🏠 Your order {order} has been delivered — we hope you love it! How was it?",
}
RATINGS = (("5", "😍 Loved it"), ("4", "👍 Good"), ("2", "😕 Not great"))


def message(order: Dict[str, Any], status: str, shop: str) -> Optional[str]:
    tpl = STEPS.get(status)
    if not tpl:
        return None
    slot = order.get("delivery_slot") or ""
    text = tpl.format(order=order.get("display_id") or "", slot=f"\nExpected: {slot}." if slot else "")
    first = (order.get("customer_name") or "").split(" ")[0]
    return (f"Hi {first}! " if first else "") + text + f"\n\n— {shop}"


async def _first_photo(order: Dict[str, Any]) -> Optional[str]:
    from vula.commerce import service, web_basket
    for it in order.get("commerce_order_items") or []:
        if not it.get("product_id"):
            continue
        try:
            rows = (service._client().table("commerce_products").select("image_url")
                    .eq("id", it["product_id"]).limit(1).execute().data or [])
        except Exception:
            rows = []
        if rows and rows[0].get("image_url"):
            return web_basket.sized(rows[0]["image_url"], 800)
    return None


async def notify(tenant_id: str, order: Dict[str, Any], status: str) -> bool:
    """Tell the customer about this step. True when something was sent. Never raises."""
    phone = order.get("customer_phone")
    if not phone or status not in STEPS:
        return False
    try:
        from vula.api import tenants
        from vula.api.whatsapp import (_resolve_wa, _send_wa_buttons, _send_wa_cta_url, _wa_number,
                                       send_template)
        from vula.commerce import order_slip
        shop = tenants.display_name(tenant_id)
        text = message(order, status, shop)
        creds = await _resolve_wa(tenant_id)
        photo = await _first_photo(order)
        number = _wa_number(phone)
        sent = False
        if creds and status == "delivered":
            sent = await _send_wa_buttons(creds, number, text,
                                          [{"id": f"rate_{order['id']}_{n}", "title": t} for n, t in RATINGS],
                                          image_url=photo)
        elif creds:
            links = order_slip.urls(order["id"], tenant_id)
            if links:
                sent = await _send_wa_cta_url(creds, number, text, "View order", links["page"], image_url=photo)
        if not sent:                         # outside the 24-hour window, or no button support
            from vula.commerce.job_config import template_name
            sent = await send_template(tenant_id, phone, template_name(tenant_id, "order_update"),
                                       [order.get("customer_name") or "there", order.get("display_id") or "",
                                        text.split("\n")[0].split("! ", 1)[-1]])
        if sent and status == "delivered":
            from vula.commerce import service
            service._client().table("commerce_orders").update({"review_requested_at": service._now()}) \
                .eq("id", order["id"]).eq("tenant_id", tenant_id).execute()
        log.info("order update %s %s → %s", tenant_id, order.get("display_id"), "sent" if sent else "not sent")
        return bool(sent)
    except Exception as exc:
        log.warning("order update for %s %s failed: %s", tenant_id, order.get("display_id"), exc)
        return False
