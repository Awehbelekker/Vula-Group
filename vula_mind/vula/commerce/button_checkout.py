"""
vula/commerce/button_checkout.py — checkout after a WhatsApp cart, with buttons (8 Nov 2026).

Before this, a WhatsApp cart order (or the cart's Checkout button) was answered with "What's
the delivery address?" and the model took it from there. Now each step is a button and a
database lookup, and no model chooses anything:

  1. the cart, priced from the database, with Continue / Change cart (plus a line for anything
     out of stock or not matched, with Remove it / Ask the team)
  2. Delivery or Collect (only when the shop offers collection)
  3. the address: "Same as last time" for a returning customer, else typed (the one free-text
     step, held as an open question so the reply is taken as the address)
  4. Morning or Afternoon
  5. the summary with the delivery fee and total, and one button per way to pay. Tapping one
     places the order through the same place_order the assistant uses (payment link, invoice,
     shop alert), so there is no second payment path.

Every total is recomputed from the cart rows at each step (integer cents). Button ids carry no
price: "chk_" plus the step, and for removals a cart item id that is checked to belong to this
customer's own cart. Anything typed mid-checkout other than the address goes to the assistant
as usual, with the cart untouched, so a cart is never lost.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

PREFIX = "chk_"
ADDRESS_QUESTION = "checkout_address"
SLOTS = (("morning", "Morning"), ("afternoon", "Afternoon"))
_PAY_TITLES = {"online": "Pay by card", "eft": "EFT", "cod": "Pay on delivery"}
COLLECTION = "Collection"


def _svc():
    from vula.commerce import service
    return service


def _settings(tenant_id: str) -> Dict[str, Any]:
    from vula.commerce import order_workflow as ow
    return ow.get_order_settings(tenant_id)


def _rands(cents: int) -> str:
    return f"R{cents / 100:,.2f}"


async def _cart(tenant_id: str, phone: str) -> Dict[str, Any]:
    return await _svc().get_or_create_cart(tenant_id, phone, phone)


def _lines(cart: Dict[str, Any]) -> Tuple[List[str], int]:
    from core.skills.commerce_assistant import _fmt_qty, _line_cents
    out, subtotal = [], 0
    for it in cart.get("commerce_cart_items") or []:
        prod = it.get("commerce_products") or {}
        cents = _line_cents(it["quantity"], it["unit_price_cents"])
        subtotal += cents
        out.append(f"• {_fmt_qty(prod, it['quantity'])} × {prod.get('name', 'Item')} — {_rands(cents)}")
    return out, subtotal


def short_lines(tenant_id: str, cart: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Cart lines the shop can't fill: the same rule the order's stock reservation applies
    (tracked stock below the quantity). Untracked stock is never refused here."""
    items = [it for it in cart.get("commerce_cart_items") or [] if it.get("product_id") and not it.get("variant_id")]
    if not items:
        return []
    rows = (_svc()._client().table("commerce_products").select("id,name,stock_quantity")
            .eq("tenant_id", tenant_id).in_("id", [it["product_id"] for it in items]).execute().data or [])
    stock = {r["id"]: r for r in rows}
    out = []
    for it in items:
        s = stock.get(it["product_id"]) or {}
        left = s.get("stock_quantity")
        if left is not None and float(left) < float(it.get("quantity") or 0):
            out.append({"item_id": it["id"], "name": s.get("name") or "an item", "left": float(left)})
    return out


def _update_cart(tenant_id: str, cart_id: str, patch: Dict[str, Any]) -> None:
    _svc()._client().table("commerce_carts").update(patch).eq("tenant_id", tenant_id).eq("id", cart_id).execute()


# ── what goes out ──────────────────────────────────────────────────────────────────────────────

async def _buttons(phone: str, tenant_id: str, body: str, buttons: List[Dict[str, str]]) -> bool:
    from vula.api import whatsapp as wa
    creds = await wa._resolve_wa(tenant_id)
    if not creds:
        return False
    return await wa._send_wa_buttons(creds, wa._wa_number(phone), body, buttons[:3])


async def _text(phone: str, tenant_id: str, body: str) -> None:
    from vula.api import whatsapp as wa
    await wa._send_reply(phone, body, tenant_id)


async def _remember(tenant_id: str, phone: str, said: str, answered: str) -> None:
    """Keep the assistant's chat history in step, so a typed question mid-checkout has context."""
    try:
        s = await _svc().get_or_create_session(tenant_id, session_key=phone, channel="whatsapp", customer_phone=phone)
        await _svc().append_message(tenant_id, s["id"], "user", said)
        await _svc().append_message(tenant_id, s["id"], "assistant", answered)
    except Exception as exc:
        log.debug("checkout history skipped: %s", exc)


# ── the steps ──────────────────────────────────────────────────────────────────────────────────

async def start(phone: str, tenant_id: str, *, intro: str = "", unmatched: Optional[List[str]] = None) -> bool:
    """Step 1: the cart with Continue / Change cart. False when there's nothing to check out or
    buttons can't be sent (the caller keeps its old reply)."""
    cart = await _cart(tenant_id, phone)
    lines, subtotal = _lines(cart)
    if not lines:
        return False
    short = short_lines(tenant_id, cart)
    body = (intro + "\n\n" if intro else "") + "🧾 *Your order*\n" + "\n".join(lines) + f"\nSubtotal: {_rands(subtotal)}"
    if unmatched:
        body += (f"\n\n⚠️ {len(unmatched)} item{'s' if len(unmatched) != 1 else ''} from the catalogue "
                 "couldn't be matched to something we sell now, so they're not in your order.")
    if short:
        s = short[0]
        left = int(s["left"]) if s["left"] == int(s["left"]) else s["left"]
        body += f"\n\n⚠️ We only have {left} of {s['name']} left."
        sent = await _buttons(phone, tenant_id, body, [
            {"id": f"{PREFIX}rm_{s['item_id']}", "title": "Remove it"},
            {"id": f"{PREFIX}team", "title": "Ask the team"},
            {"id": f"{PREFIX}edit", "title": "Change cart"}])
    else:
        buttons = [{"id": f"{PREFIX}go", "title": "Continue"}, {"id": f"{PREFIX}edit", "title": "Change cart"}]
        if unmatched:
            buttons.append({"id": f"{PREFIX}team", "title": "Ask the team"})
        sent = await _buttons(phone, tenant_id, body, buttons)
    if sent:
        await _remember(tenant_id, phone, "[checkout]", body)
    return sent


async def _fulfilment(phone: str, tenant_id: str, cart: Dict[str, Any]) -> None:
    cfg = _settings(tenant_id)
    _, subtotal = _lines(cart)
    min_order = cfg.get("min_order_cents")
    if min_order and subtotal < int(min_order):
        await _buttons(phone, tenant_id,
                       f"The minimum order is {_rands(int(min_order))} and your cart is {_rands(subtotal)}. "
                       "Add a little more and check out again.",
                       [{"id": "cart_more", "title": "Keep shopping"}])
        return
    if cfg.get("collection_enabled"):
        note = (cfg.get("collection_note") or "").strip()
        await _buttons(phone, tenant_id, "Delivery or collect?" + (f"\n\nCollection: {note}" if note else ""),
                       [{"id": f"{PREFIX}del", "title": "Delivery"}, {"id": f"{PREFIX}col", "title": "Collect"}])
        return
    await _address(phone, tenant_id, cart)


async def _address(phone: str, tenant_id: str, cart: Dict[str, Any]) -> None:
    profile = None
    try:
        profile = await _svc().get_customer_profile(tenant_id, phone)
    except Exception:
        profile = None
    last = (profile or {}).get("delivery_address")
    if last and last != COLLECTION:
        await _buttons(phone, tenant_id, f"Deliver to the same address as last time?\n\n📍 {last}",
                       [{"id": f"{PREFIX}addr_last", "title": "Same address"},
                        {"id": f"{PREFIX}addr_new", "title": "New address"}])
        return
    await ask_address(phone, tenant_id, cart)


async def ask_address(phone: str, tenant_id: str, cart: Dict[str, Any]) -> None:
    from vula import open_questions as oq
    oq.ask(tenant_id, phone, ADDRESS_QUESTION, cart["id"], "delivery address")
    await _text(phone, tenant_id, "📍 What's the delivery address? Street, suburb and town, in one message.")


async def _slot(phone: str, tenant_id: str) -> None:
    await _buttons(phone, tenant_id, "When should we deliver?",
                   [{"id": f"{PREFIX}slot_{k}", "title": t} for k, t in SLOTS])


async def summary(phone: str, tenant_id: str) -> None:
    """Step 5: what it costs, from the cart rows, and how to pay."""
    from vula.commerce import order_workflow as ow
    cart = await _cart(tenant_id, phone)
    lines, subtotal = _lines(cart)
    if not lines:
        await _text(phone, tenant_id, "Your cart is empty — reply *shop* to browse.")
        return
    collect = (cart.get("delivery_address") or "") == COLLECTION
    fee = 0 if collect else _svc().delivery_fee_cents(tenant_id, cart, subtotal)
    known = collect or _svc().delivery_fee_known(tenant_id)
    cfg = _settings(tenant_id)
    where = (f"Collection{': ' + cfg['collection_note'] if cfg.get('collection_note') else ''}" if collect
             else f"Deliver to: {cart.get('delivery_address')} ({cart.get('delivery_slot') or 'morning'})")
    body = ("🧾 *Please check your order*\n" + "\n".join(lines) + f"\nSubtotal: {_rands(subtotal)}\n"
            + (f"*Total: {_rands(subtotal)}*" if collect
               else f"Delivery: {_rands(fee)}\n*Total: {_rands(subtotal + fee)}*" if known
               else f"Delivery: the team will confirm the fee\n*Total: {_rands(subtotal)} plus delivery*")
            + f"\n{where}\n\nTap how you'd like to pay to place the order.")
    methods = ow.usable_payment_methods(tenant_id, cfg)
    await _buttons(phone, tenant_id, body, [{"id": f"{PREFIX}pay_{m}", "title": _PAY_TITLES.get(m, m)}
                                            for m in methods][:3])
    await _remember(tenant_id, phone, "[checkout: summary]", body)


async def _place(phone: str, tenant_id: str, method: str) -> None:
    """The pay button on the summary is the customer's yes: the order goes through the
    assistant's own place_order (minimum order, payment methods, stock, pay link, invoice,
    shop alert all in one place)."""
    from core.skills.loader import get_skill
    cart = await _cart(tenant_id, phone)
    if not (cart.get("commerce_cart_items") or []):
        await _text(phone, tenant_id, "That order has already been placed — reply *my order* to see it.")
        return
    collect = (cart.get("delivery_address") or "") == COLLECTION
    if not collect and not (cart.get("delivery_address") or "").strip():
        await ask_address(phone, tenant_id, cart)
        return
    skill = get_skill("commerce_assistant")
    res = await skill._exec_place_order(tenant_id, phone, phone, {
        "payment_method": method, "fulfilment": "collection" if collect else "delivery",
        "delivery_address": "" if collect else cart.get("delivery_address"),
        "delivery_slot": cart.get("delivery_slot") or "morning",
        "customer_name": cart.get("customer_name") or ""}, ctx={"current_message": "confirm"})
    if res.get("confirmation"):
        await _text(phone, tenant_id, res["confirmation"])
        await _remember(tenant_id, phone, f"[tapped {_PAY_TITLES.get(method, method)}]", res["confirmation"])
        return
    await _text(phone, tenant_id, (res.get("error") or "That didn't go through.")
                .replace("tell the customer and ask if they'd like to add more. Do not place the order.",
                         "add a little more and check out again."))


async def _ask_team(phone: str, tenant_id: str) -> None:
    from core.skills.loader import get_skill
    cart = await _cart(tenant_id, phone)
    lines, _ = _lines(cart)
    res = await get_skill("commerce_assistant")._exec_ask_team(tenant_id, phone, {
        "question": "A customer's WhatsApp order needs a hand (an item is out of stock or couldn't be "
                    "matched). Their cart: " + "; ".join(l.lstrip("• ") for l in lines)})
    await _text(phone, tenant_id, "I've asked the team — they'll get back to you here shortly. Your cart is kept."
                if res.get("escalated") or res.get("answer_from_team")
                else "I couldn't reach the team just now. Your cart is kept — try again in a moment.")


async def handle(phone: str, tenant_id: str, reply_id: str) -> bool:
    """A tap on a checkout button. True when it was one."""
    if not reply_id.startswith(PREFIX):
        return False
    step = reply_id[len(PREFIX):]
    cart = await _cart(tenant_id, phone)
    if not (cart.get("commerce_cart_items") or []) and not step.startswith("pay_"):
        await _text(phone, tenant_id, "Your cart is empty — reply *shop* to browse.")
        return True
    if step == "go":
        await _fulfilment(phone, tenant_id, cart)
    elif step == "edit":
        from vula.api import whatsapp as wa
        await wa._handle_commerce_message(phone, "I'd like to change my cart", "", tenant_id)
    elif step == "team":
        await _ask_team(phone, tenant_id)
    elif step.startswith("rm_"):
        item_id = step[3:]
        if any(it.get("id") == item_id for it in cart.get("commerce_cart_items") or []):
            await _svc().remove_from_cart(cart["id"], item_id)      # only from this customer's own cart
        if not await start(phone, tenant_id, intro="Removed ✅"):
            await _text(phone, tenant_id, "Removed ✅ Your cart is now empty — reply *shop* to browse.")
    elif step == "del":
        await _address(phone, tenant_id, cart)
    elif step == "col":
        if not _settings(tenant_id).get("collection_enabled"):
            await _address(phone, tenant_id, cart)
        else:
            _update_cart(tenant_id, cart["id"], {"delivery_address": COLLECTION, "delivery_slot": None})
            await summary(phone, tenant_id)
    elif step == "addr_last":
        last = ((await _svc().get_customer_profile(tenant_id, phone)) or {}).get("delivery_address")
        if not last:
            await ask_address(phone, tenant_id, cart)
        else:
            _update_cart(tenant_id, cart["id"], {"delivery_address": last})
            await _slot(phone, tenant_id)
    elif step == "addr_new":
        await ask_address(phone, tenant_id, cart)
    elif step.startswith("slot_"):
        slot = step[5:]
        if slot not in dict(SLOTS):
            await _slot(phone, tenant_id)
        else:
            _update_cart(tenant_id, cart["id"], {"delivery_slot": slot})
            await summary(phone, tenant_id)
    elif step.startswith("pay_"):
        from vula.commerce import order_workflow as ow
        method = step[4:]
        if method not in ow.usable_payment_methods(tenant_id, _settings(tenant_id)):
            await summary(phone, tenant_id)
        else:
            await _place(phone, tenant_id, method)
    else:
        return False
    return True


_NOT_AN_ADDRESS = re.compile(r"\?|^\s*(?:cancel|stop|no|wait|hold on|change|remove|add)\b", re.I)


async def take_address(phone: str, tenant_id: str, text: str, question: Dict[str, Any]) -> bool:
    """The reply to "What's the delivery address?". A question or a change of mind isn't an
    address: it goes to the assistant as usual (False), and the question stays open."""
    from vula import open_questions as oq
    t = (text or "").strip()
    if len(t) < 6 or _NOT_AN_ADDRESS.search(t):
        return False
    cart = await _cart(tenant_id, phone)
    if cart.get("id") != question.get("ref_id") or not (cart.get("commerce_cart_items") or []):
        oq.close(question["id"], status="closed")
        return False
    _update_cart(tenant_id, cart["id"], {"delivery_address": t[:300]})
    oq.close(question["id"], answer=t[:200])
    await _slot(phone, tenant_id)
    return True
