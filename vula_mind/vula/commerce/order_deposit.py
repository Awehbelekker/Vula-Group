"""
vula/commerce/order_deposit.py — an order that needs a deposit, paid from the WhatsApp chat.

2026-10-13 (Ian): "If admin processes an order and needs the client to pay a deposit — can admin
send an order number by Vula and the client pays by WhatsApp?" The admin takes the order (on
WhatsApp: "order 2 Round basins for Thandi 082…, R1,500 deposit", or from the dashboard) and the
customer gets one message: their order number, what's on it, the deposit and the balance, and a
**Pay deposit** button that opens the shop's pay page (card through the connected gateway, plus
EFT details). WhatsApp Pay itself isn't offered in South Africa, so this is the in-chat way.

The deposit is an ordinary invoice linked to the order (order_id) and marked in its notes
("Deposit for order …"). When the gateway's signed webhook — or the owner, for an EFT — marks it
paid, service._settle_linked_order moves the order from pending_payment to confirmed, holds the
stock and tells the customer and the team; the order is never shown as paid in full until the
balance invoice ("Balance for order …") is paid too. Every figure comes from the saved rows.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

DEPOSIT_NOTE = "Deposit for order"
BALANCE_NOTE = "Balance for order"


def rands(cents: int) -> str:
    return f"R{cents / 100:,.2f}"


def deposit_cents(total_cents: int, amount_rands: Optional[float] = None,
                  pct: Optional[float] = None) -> int:
    """The deposit asked for: a rand amount, or a percentage of the order total (rounded to the
    rand). 0 when neither is given or it isn't less than the total — then it's not a deposit."""
    cents = 0
    try:
        if amount_rands:
            cents = int(round(float(amount_rands) * 100))
        elif pct:
            cents = int(round(total_cents * float(pct) / 100 / 100)) * 100
    except (TypeError, ValueError):
        return 0
    return cents if 0 < cents < total_cents else 0


def kind_of(invoice: Dict[str, Any]) -> str:
    """'deposit' / 'balance' for the invoices made here, '' for any other invoice."""
    notes = invoice.get("notes") or ""
    return "deposit" if notes.startswith(DEPOSIT_NOTE) else "balance" if notes.startswith(BALANCE_NOTE) else ""


def _linked(tenant_id: str, order_id: str) -> list:
    from vula.commerce import service
    return (service._client().table("commerce_invoices")
            .select("id,invoice_number,status,total_cents,total_paid_cents,notes")
            .eq("tenant_id", tenant_id).eq("order_id", order_id).execute().data or [])


def paid_cents(tenant_id: str, order_id: str) -> int:
    """What the customer has paid towards this order, from its linked invoices."""
    total = 0
    for inv in _linked(tenant_id, order_id):
        if inv.get("status") == "paid":
            total += int(inv.get("total_cents") or 0)
        elif inv.get("status") == "part_paid":
            total += int(inv.get("total_paid_cents") or 0)
    return total


def open_request(tenant_id: str, order_id: str) -> Optional[dict]:
    """A deposit/balance request already sent and not yet paid — sent again, never doubled."""
    for inv in _linked(tenant_id, order_id):
        if kind_of(inv) and inv.get("status") in ("draft", "sent", "overdue"):
            return inv
    return None


def _pay_url(tenant_id: str, invoice_id: str) -> str:
    from vula.commerce import web_basket
    base = web_basket.public_base(tenant_id)
    return f"{base}/v1/commerce/{tenant_id}/pay/{invoice_id}" if base else ""


def message(order: Dict[str, Any], kind: str, amount_cents: int, paid: int, shop: str) -> str:
    first = (order.get("customer_name") or "").split(" ")[0]
    lines = []
    for it in (order.get("commerce_order_items") or [])[:8]:
        q = float(it.get("quantity") or 1)
        lines.append(f"• {int(q) if q == int(q) else q} × {it.get('product_name') or 'Item'}")
    total = int(order.get("total_cents") or 0)
    head = (f"Hi {first}! " if first else "Hi! ") + (
        f"Your {shop} order *{order.get('display_id')}* is ready to go ahead." if kind == "deposit"
        else f"Here's the balance for your {shop} order *{order.get('display_id')}*.")
    body = [head, "", *lines, "", f"Order total: *{rands(total)}*"]
    if kind == "deposit":
        body += [f"Deposit to confirm it: *{rands(amount_cents)}*",
                 f"Balance later: {rands(total - amount_cents)}", "",
                 "Tap *Pay deposit* to pay by card or EFT — we'll confirm here the moment it's in."]
    else:
        if paid:
            body.append(f"Already paid: {rands(paid)}")
        body += [f"Balance due: *{rands(amount_cents)}*", "", "Tap *Pay balance* to pay by card or EFT."]
    return "\n".join(body) + f"\n\n— {shop}"


async def request(tenant_id: str, order: Dict[str, Any], kind: str,
                  amount_cents: Optional[int] = None) -> Dict[str, Any]:
    """Create the deposit (or balance) invoice for this order and WhatsApp the customer a Pay
    button. Returns what was saved and whether it was delivered. Never raises."""
    from vula.commerce import pay_page, service
    if not order.get("customer_phone"):
        return {"error": f"Order {order.get('display_id')} has no customer WhatsApp number."}
    card = await pay_page.has_gateway(tenant_id)
    if not card and not pay_page.eft_details(tenant_id):
        return {"error": "No way to take payment is set up yet — connect a card gateway under "
                         "Settings › Payments, or add your EFT banking details."}
    total = int(order.get("total_cents") or 0)
    paid = paid_cents(tenant_id, order["id"])
    if kind == "balance":
        amount_cents = total - paid
        if amount_cents <= 0:
            return {"error": f"Order {order.get('display_id')} is already paid in full."}
    elif not amount_cents or amount_cents >= total:
        return {"error": "The deposit must be more than R0 and less than the order total "
                         f"({rands(total)})."}
    existing = open_request(tenant_id, order["id"])
    if existing and kind_of(existing) == kind:
        inv = existing
    else:
        note = DEPOSIT_NOTE if kind == "deposit" else BALANCE_NOTE
        inv = await service.create_invoice(tenant_id, {
            "doc_type": "invoice", "customer_name": order.get("customer_name") or "Customer",
            "customer_phone": order["customer_phone"], "order_id": order["id"],
            "line_items": [{"description": f"{note} {order.get('display_id')}", "quantity": 1,
                            "unit_price_cents": amount_cents}],
            "prices_include_vat": True, "status": "draft",
            "notes": f"{note} {order.get('display_id')}"})
        if not inv.get("id"):
            return {"error": "The invoice couldn't be created — nothing was sent."}
    if card and not inv.get("pay_url"):
        try:
            await pay_page.gateway_link(tenant_id, inv)
        except Exception as exc:
            log.warning("deposit card link for %s failed: %s", inv.get("id"), exc)
    saved = await service.get_invoice(tenant_id, inv["id"]) or inv
    amount = int(saved.get("total_cents") or 0)
    from vula.api import tenants
    from vula.api.whatsapp import _resolve_wa, _send_wa_cta_url, _wa_number, send_template
    from vula.commerce import order_updates
    shop = tenants.display_name(tenant_id)
    text = message(order, kind, amount, paid, shop)
    url = _pay_url(tenant_id, inv["id"])
    sent = False
    creds = await _resolve_wa(tenant_id)
    if creds and url:
        sent = await _send_wa_cta_url(creds, _wa_number(order["customer_phone"]), text,
                                      "Pay deposit" if kind == "deposit" else "Pay balance", url,
                                      image_url=await order_updates._first_photo(order))
    via_template = False
    if not sent:      # outside the 24-hour window: the approved chase template opens the chat
        from vula.commerce.job_config import template_name
        sent = via_template = bool(await send_template(
            tenant_id, order["customer_phone"], template_name(tenant_id, "unpaid_order_chase"),
            [order.get("customer_name") or "there", order.get("display_id") or "", rands(amount)]))
    if sent and saved.get("status") == "draft":
        await service.update_invoice_status(tenant_id, inv["id"], "sent")
    if kind == "deposit":
        # The 2-hour unpaid-order chase would ask for the full total — the deposit request is
        # this order's payment ask, so mark the chase as done.
        try:
            (service._client().table("commerce_orders").update({"followup_sent_at": service._now()})
             .eq("id", order["id"]).eq("tenant_id", tenant_id).execute())
        except Exception as exc:
            log.debug("chase mark skipped for %s: %s", order.get("display_id"), exc)
    log.info("order %s %s request %s → %s", tenant_id, order.get("display_id"), kind,
             "template" if via_template else "sent" if sent else "not sent")
    return {"order": order.get("display_id"), "kind": kind, "invoice_number": saved.get("invoice_number"),
            "amount_cents": amount, "total_cents": total, "paid_cents": paid,
            "sent": bool(sent), "via_template": via_template, "link": url}


async def on_paid(tenant_id: str, invoice: Dict[str, Any], order: Dict[str, Any]) -> None:
    """A deposit or balance invoice was paid: tell the customer (with the order's slip page)
    and the team. Called from service._settle_linked_order once the order row is updated."""
    from vula.api import tenants
    from vula.api.whatsapp import _resolve_wa, _send_reply, _send_wa_cta_url, _wa_number
    from vula.commerce import order_slip
    kind = kind_of(invoice)
    total = int(order.get("total_cents") or 0)
    paid = paid_cents(tenant_id, order["id"])
    left = max(0, total - paid)
    name = order.get("customer_name") or ""
    first = name.split(" ")[0]
    if kind == "deposit":
        text = (f"✅ Thanks{(' ' + first) if first else ''}! We've received your deposit of "
                f"*{rands(int(invoice.get('total_cents') or 0))}* — order *{order.get('display_id')}* "
                f"is confirmed.\nBalance: {rands(left)}. We'll be in touch about delivery.")
    else:
        text = (f"✅ Paid in full — thank you{(' ' + first) if first else ''}! Order "
                f"*{order.get('display_id')}* ({rands(total)}) is all settled.")
    text += f"\n\n— {tenants.display_name(tenant_id)}"
    phone = order.get("customer_phone")
    if phone:
        sent = False
        try:
            creds = await _resolve_wa(tenant_id)
            links = order_slip.urls(order["id"], tenant_id)
            if creds and links:
                sent = await _send_wa_cta_url(creds, _wa_number(phone), text, "View order", links["page"],
                                              image_url=links["image"] if kind == "balance" else None)
        except Exception as exc:
            log.debug("deposit paid button skipped: %s", exc)
        if not sent:
            await _send_reply(phone, text, tenant_id)
    try:
        from vula.integrations.notify import notify_team
        what = (f"💰 Deposit {rands(int(invoice.get('total_cents') or 0))} received for order "
                f"{order.get('display_id')} ({name or phone}). Balance {rands(left)} — say "
                f"\"send the balance for {order.get('display_id')}\" when it's due."
                if kind == "deposit" else
                f"💰 Order {order.get('display_id')} ({name or phone}) is now paid in full ({rands(total)}).")
        await notify_team(tenant_id, "payment_received", what)
    except Exception as exc:
        log.debug("deposit team notify skipped: %s", exc)
