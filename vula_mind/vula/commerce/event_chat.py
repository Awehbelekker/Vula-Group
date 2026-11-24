"""Tickets in the WhatsApp chat (24 Nov 2026) — vula/commerce/events.py does the selling.

A customer writes "tickets" to the business's number and buys without leaving the chat:

    "tickets"            → the events on sale (one event: straight to its tickets)
    tap an event         → its ticket types, with prices and how many are left
    tap a ticket type    → how many? (each row shows the total, worked out here from integer cents)
    tap a number         → the order is made (seats held) and a Pay button comes back
    paid                 → the tickets arrive in the same chat (events._tell_buyer)

Every step is an exact id from a list — no model reads a price or picks an event. The order goes
through `events.buy`, the same door the web page uses, so the price is the product row's and the
seats are held by the same atomic write. A first-time customer is asked their name once (an open
question, vula/open_questions.py) because the name is what the door list shows; a returning one is
not asked again.

"my tickets" sends back the buttons to the tickets this number has already paid for.

One ticket type per order in the chat — a mixed order (2 General + 1 VIP) is made on the event's
page, which the events list links to.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

NAME_QUESTION = "ticket_name"
_ASK_RE = re.compile(r"\b(tickets?|kaartjies?|e-?tickets?)\b", re.I)
_MINE_RE = re.compile(r"\b(my|our|where|resend|send|lost|didn'?t get|haven'?t (?:got|received))\b.*\b(tickets?|kaartjies?)\b", re.I)
MAX_CHAT_QTY = 10


def is_ticket_request(text: str) -> bool:
    t = (text or "").strip()
    return bool(t) and len(t) <= 120 and bool(_ASK_RE.search(t))


def is_my_tickets(text: str) -> bool:
    return is_ticket_request(text) and bool(_MINE_RE.search(text or ""))


async def _wa(tenant_id: str):
    from vula.api import whatsapp as wa
    return wa, await wa._resolve_wa(tenant_id)


async def _say(phone: str, tenant_id: str, body: str) -> None:
    from vula.api import whatsapp as wa
    await wa._send_reply(phone, body, tenant_id=tenant_id)


def _selling(tenant_id: str) -> List[dict]:
    from vula.commerce import events
    out = [e for e in events.list_events(tenant_id) if e.get("selling") and e.get("types")]
    out.sort(key=lambda e: e.get("starts_at") or "")
    return out


def _when(ev: dict) -> str:
    from vula.api.events import when_text
    return when_text(ev.get("starts_at") or "", ev.get("ends_at"))


# ── the steps ─────────────────────────────────────────────────────────────────────────────────
async def start(phone: str, tenant_id: str, text: str) -> bool:
    """A message asking about tickets. True when this took it; False leaves it to the assistant
    (a business with no events on sale and none recently — "ticket" may mean something else)."""
    if not is_ticket_request(text):
        return False
    if is_my_tickets(text) and await send_my_tickets(phone, tenant_id):
        return True
    selling = _selling(tenant_id)
    if not selling:
        return False
    if len(selling) == 1:
        return await show_types(phone, tenant_id, selling[0]["id"])
    wa, creds = await _wa(tenant_id)
    rows = [{"id": f"evt_e_{e['id']}", "title": e["title"][:24], "description": _when(e)[:72]} for e in selling[:10]]
    if creds and await wa._send_wa_list(creds, wa._wa_number(phone), "Events", "Which event would you like tickets for?",
                                        "Tap to choose", "See events", [{"title": "On sale now", "rows": rows}]):
        return True
    await _say(phone, tenant_id, "Tickets are on sale for:\n" + "\n".join(
        f"• {e['title']} — {_when(e)}\n{e['url']}" for e in selling[:5]))
    return True


async def show_types(phone: str, tenant_id: str, event_id: str) -> bool:
    from vula.commerce import events
    ev = events.public_event(event_id)
    if not ev or ev["tenant_id"] != tenant_id or not ev["selling"]:
        await _say(phone, tenant_id, "Ticket sales for that event are closed.")
        return True
    where = ", ".join(x for x in (ev["venue"], ev["address"]) if x)
    body = f"*{ev['title']}*\n{_when(ev)}" + (f"\n{where}" if where else "") + "\n\nWhich ticket would you like?"
    rows = []
    for t in ev["types"][:10]:
        left = "Sold out" if not t["left"] else (f"{t['left']} left" if t["left"] <= 10 else "")
        rows.append({"id": f"evt_t_{t['id']}" if t["left"] else f"evt_x_{t['id']}", "title": t["name"][:24],
                     "description": " · ".join(x for x in (events.rand(t["price_cents"]), left) if x)[:72]})
    wa, creds = await _wa(tenant_id)
    if creds and await wa._send_wa_list(creds, wa._wa_number(phone), "Tickets", body[:1024], "Tap to choose",
                                        "See tickets", [{"title": "Tickets", "rows": rows}]):
        return True
    await _say(phone, tenant_id, f"{body}\n\nBuy here: {ev['url']}")
    return True


def _type_and_event(tenant_id: str, type_id: str) -> Optional[tuple]:
    """(the event as the public page shows it, this ticket type in it) — None when it isn't this
    business's, isn't on sale any more, or doesn't exist."""
    from vula.commerce import events
    rows = (events._db().table("commerce_ticket_types").select("id,event_id").eq("tenant_id", tenant_id)
            .eq("id", type_id).limit(1).execute().data or [])
    if not rows:
        return None
    ev = events.public_event(rows[0]["event_id"])
    if not ev or ev["tenant_id"] != tenant_id or not ev["selling"]:
        return None
    t = next((x for x in ev["types"] if x["id"] == type_id), None)
    return (ev, t) if t else None


async def ask_quantity(phone: str, tenant_id: str, type_id: str) -> bool:
    from vula.commerce import events
    found = _type_and_event(tenant_id, type_id)
    if not found:
        await _say(phone, tenant_id, "Those tickets aren't on sale any more. Reply *tickets* to see what is.")
        return True
    ev, t = found
    most = min(t["max_per_order"], t["left"], MAX_CHAT_QTY)
    if most < 1:
        await _say(phone, tenant_id, f"{t['name']} is sold out. Reply *tickets* to see what's left.")
        return True
    rows = [{"id": f"evt_q_{t['id']}_{n}", "title": f"{n} ticket{'' if n == 1 else 's'}",
             "description": events.rand(n * int(t["price_cents"]))} for n in range(1, most + 1)]
    body = f"*{t['name']}* for {ev['title']} — {events.rand(t['price_cents'])} each.\n\nHow many would you like?"
    wa, creds = await _wa(tenant_id)
    if creds and await wa._send_wa_list(creds, wa._wa_number(phone), "How many?", body[:1024], "Tap to choose",
                                        "Choose", [{"title": "Tickets", "rows": rows}]):
        return True
    await _say(phone, tenant_id, f"{body}\n\nBuy here: {ev['url']}")
    return True


async def _known_name(tenant_id: str, phone: str) -> str:
    from vula.commerce import service
    try:
        prof = await service.get_customer_profile(tenant_id, phone)
    except Exception:  # noqa: BLE001
        prof = None
    return ((prof or {}).get("name") or "").strip()


async def choose_quantity(phone: str, tenant_id: str, type_id: str, quantity: int) -> bool:
    """The customer picked how many. With a name on file the order is made now; otherwise they're
    asked their name once, and the order is made when they answer."""
    name = await _known_name(tenant_id, phone)
    if name:
        return await place(phone, tenant_id, type_id, quantity, name)
    from vula import open_questions as oq
    oq.ask(tenant_id, phone, NAME_QUESTION, f"{type_id}:{quantity}", "name for tickets")
    await _say(phone, tenant_id, "What name should the tickets be under? Just your name and surname.")
    return True


def clean_name(text: str) -> str:
    """A reply that reads as a person's name ('' when it doesn't — a question, a link, a number)."""
    t = re.sub(r"\s+", " ", (text or "")).strip(" .!,")
    t = re.sub(r"^(?:my name is|it'?s|i'?m|i am|under|name:?)\s+", "", t, flags=re.I).strip()
    if not 2 <= len(t) <= 60 or "?" in t or "http" in t.lower() or re.search(r"\d", t):
        return ""
    return t if len(t.split()) <= 5 and re.fullmatch(r"[^\W\d_][\w .'’-]*", t, re.U) else ""


async def take_name(phone: str, tenant_id: str, text: str, question: Dict[str, Any]) -> bool:
    """The answer to "what name should the tickets be under?". False when it isn't a name, so the
    message is handled as whatever else it is."""
    name = clean_name(text)
    if not name:
        return False
    from vula import open_questions as oq
    type_id, _, qty = str(question.get("ref_id") or "").partition(":")
    oq.close(question["id"], name)
    if not qty.isdigit():
        return False
    return await place(phone, tenant_id, type_id, int(qty), name)


async def place(phone: str, tenant_id: str, type_id: str, quantity: int, name: str) -> bool:
    """Make the order and send the Pay button. The amount on the button's message is the order's
    own total, read back from what was made."""
    from vula.commerce import events
    found = _type_and_event(tenant_id, type_id)
    if not found:
        await _say(phone, tenant_id, "Those tickets aren't on sale any more. Reply *tickets* to see what is.")
        return True
    ev, t = found
    try:
        out = await events.buy(ev["id"], items=[{"type_id": type_id, "quantity": quantity}], name=name, phone=phone)
    except events.EventError as exc:
        await _say(phone, tenant_id, f"{exc} Reply *tickets* to try again.")
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("ticket order in chat failed for %s: %s", tenant_id, exc)
        await _say(phone, tenant_id, f"Sorry — that didn't go through. You can buy here instead: {ev['url']}")
        return True
    body = (f"🎟️ {quantity} × {t['name']} for *{ev['title']}* — {events.rand(out['total_cents'])}.\n\n"
            f"Your seats are held under {name}. Pay to get your tickets; they arrive here as soon as it goes through.")
    wa, creds = await _wa(tenant_id)
    if not (creds and await wa._send_wa_cta_url(creds, wa._wa_number(phone), body,
                                                f"Pay {events.rand(out['total_cents'])}"[:20], out["redirect_url"])):
        await _say(phone, tenant_id, f"{body}\n\nPay here: {out['redirect_url']}")
    return True


async def send_my_tickets(phone: str, tenant_id: str) -> bool:
    """The tickets this number has paid for, for events that aren't over. False when there are none."""
    from vula.commerce import events, service
    digits = "".join(c for c in phone if c.isdigit())[-9:]
    if len(digits) < 9:
        return False
    rows = (events._db().table("commerce_tickets").select("order_id,event_id,holder_phone,status")
            .eq("tenant_id", tenant_id).eq("status", "valid").like("holder_phone", f"%{digits}").limit(200)
            .execute().data or [])
    if not rows:
        return False
    live = {e["id"] for e in events.list_events(tenant_id)
            if (e.get("ends_at") or e.get("starts_at") or "") >= service._now()[:10]}
    orders: Dict[str, int] = {}
    for r in rows:
        if r["event_id"] in live:
            orders[r["order_id"]] = orders.get(r["order_id"], 0) + 1
    if not orders:
        return False
    wa, creds = await _wa(tenant_id)
    for order_id, n in list(orders.items())[:3]:
        link = events.tickets_url(tenant_id, order_id)
        body = f"🎟️ Here {'is your ticket' if n == 1 else f'are your {n} tickets'}. Show the QR code at the door."
        if not (creds and await wa._send_wa_cta_url(creds, wa._wa_number(phone), body, "View tickets", link)):
            await _say(phone, tenant_id, f"{body}\n{link}")
    return True


async def handle_tap(phone: str, tenant_id: str, reply_id: str) -> bool:
    """A tap on one of this module's list rows (ids starting evt_)."""
    kind, _, rest = reply_id[4:].partition("_")
    if kind == "e":
        return await show_types(phone, tenant_id, rest)
    if kind == "t":
        return await ask_quantity(phone, tenant_id, rest)
    if kind == "x":
        await _say(phone, tenant_id, "That one is sold out. Reply *tickets* to see what's left.")
        return True
    if kind == "q":
        type_id, _, qty = rest.rpartition("_")
        if not qty.isdigit() or not 1 <= int(qty) <= MAX_CHAT_QTY:
            return False
        return await choose_quantity(phone, tenant_id, type_id, int(qty))
    return False
