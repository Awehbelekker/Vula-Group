"""Booking a table, and asking for the specials, in the WhatsApp chat (24 Nov 2026) —
vula/commerce/tables.py does the booking.

    "book a table"   → which day? → how many people? → which time? → booked, with a reference
    "specials"       → today's specials, in the restaurant's own words, and a way to book
    "cancel my booking" → their own bookings still to come → cancelled

Every step is an exact id from a list: no model reads a date, a time or a party size, so nothing is
booked that the customer didn't tap. Only times the restaurant has room for are offered, and the
booking itself is decided by the database (`book_table`). A first-time customer is asked their name
once (open question `table_name`).

Only a business with bookings switched on takes these messages; anyone else's "book" goes on to the
assistant as before.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any, Dict, List

log = logging.getLogger(__name__)

NAME_QUESTION = "table_name"
_BOOK_RE = re.compile(r"\b(book(?:ing)?|reserv(?:e|ation)|bespreek|bespreking)\b.*\b(table|tafel|dinner|lunch|breakfast|supper|for \d+)\b"
                      r"|\b(table|tafel)\s+for\b|^\s*(book|booking|reservation|reserve|bespreek)\s*[.!?]*\s*$", re.I)
_SPECIALS_RE = re.compile(r"\b(specials?|spesiale|aanbiedings?)\b", re.I)
_CANCEL_RE = re.compile(r"\b(cancel|kanselleer|can'?t make|change)\b.*\b(booking|reservation|table|bespreking)\b|\bmy (booking|reservation)s?\b", re.I)


def is_booking_request(text: str) -> bool:
    t = (text or "").strip()
    return bool(t) and len(t) <= 160 and bool(_BOOK_RE.search(t))


def is_specials_request(text: str) -> bool:
    t = (text or "").strip()
    return bool(t) and len(t) <= 80 and bool(_SPECIALS_RE.search(t))


def is_own_booking_request(text: str) -> bool:
    t = (text or "").strip()
    return bool(t) and len(t) <= 160 and bool(_CANCEL_RE.search(t))


async def _wa(tenant_id: str):
    from vula.api import whatsapp as wa
    return wa, await wa._resolve_wa(tenant_id)


async def _say(phone: str, tenant_id: str, body: str) -> None:
    from vula.api import whatsapp as wa
    await wa._send_reply(phone, body, tenant_id=tenant_id)


async def _list(phone: str, tenant_id: str, header: str, body: str, button: str, rows: List[dict], fallback: str) -> None:
    wa, creds = await _wa(tenant_id)
    if not (creds and await wa._send_wa_list(creds, wa._wa_number(phone), header, body, "Tap to choose", button,
                                             [{"title": header[:24], "rows": rows[:10]}])):
        await _say(phone, tenant_id, fallback)


def _day_label(d: date, today: date) -> str:
    if d == today:
        return "Today"
    if (d - today).days == 1:
        return "Tomorrow"
    return f"{d.strftime('%A')} {d.day} {d.strftime('%b')}"


async def start(phone: str, tenant_id: str, text: str) -> bool:
    """A message about a table or the specials. True when this took it."""
    from vula.commerce import tables
    wants_specials, wants_own, wants_table = is_specials_request(text), is_own_booking_request(text), is_booking_request(text)
    if not (wants_specials or wants_own or wants_table):
        return False
    st = tables.settings(tenant_id)
    if wants_own and st["enabled"]:
        return await show_own(phone, tenant_id)
    if wants_specials and not wants_table:
        specials = tables.todays_specials(tenant_id)
        if not specials:
            return False                       # nothing to show: the assistant answers as before
        body = tables.specials_text(tenant_id, specials)
        wa, creds = await _wa(tenant_id)
        if st["enabled"] and creds and await wa._send_wa_buttons(creds, wa._wa_number(phone), body[:1024],
                                                                  [{"id": "tbl_start", "title": "Book a table"}]):
            return True
        await _say(phone, tenant_id, body)
        return True
    if not st["enabled"]:
        return False
    return await ask_day(phone, tenant_id)


async def ask_day(phone: str, tenant_id: str) -> bool:
    from vula.commerce import tables
    days = tables.open_days(tenant_id, 10)
    if not days:
        await _say(phone, tenant_id, "We aren't taking bookings for the coming days. Send us a message and we'll help.")
        return True
    today = tables._now().astimezone(tables.SAST).date()
    rows = [{"id": f"tbl_d_{d.isoformat()}", "title": _day_label(d, today)[:24]} for d in days]
    await _list(phone, tenant_id, "Book a table", "Which day would you like to come?", "Choose a day", rows,
                f"Book here: {tables.booking_url(tenant_id)}")
    return True


async def ask_party(phone: str, tenant_id: str, day: str) -> bool:
    from vula.commerce import tables
    st = tables.settings(tenant_id)
    most = min(int(st["max_party"]), 10)
    rows = [{"id": f"tbl_p_{day}_{n}", "title": f"{n} {'person' if n == 1 else 'people'}"} for n in range(1, most + 1)]
    more = f"\n\nMore than {most}? Send us a message and we'll arrange it." if most >= 1 else ""
    await _list(phone, tenant_id, "How many?", "How many people?" + more, "Choose", rows,
                f"Book here: {tables.booking_url(tenant_id)}")
    return True


def _spread(items: List[dict], n: int = 10) -> List[dict]:
    """At most n of the items, evenly spread, the first always kept."""
    if len(items) <= n:
        return items
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]


async def ask_time(phone: str, tenant_id: str, day: str, party: int, sitting: str = "") -> bool:
    from vula.commerce import tables
    try:
        slots = tables.slots(tenant_id, day, party)
    except tables.TableError as exc:
        await _say(phone, tenant_id, str(exc))
        return True
    if not slots:
        await _say(phone, tenant_id, f"We have no table for {party} that day. Reply *book* to try another day.")
        return True
    names = list(dict.fromkeys(s["sitting"] for s in slots))
    if len(slots) > 10 and len(names) > 1 and not sitting:
        rows = [{"id": f"tbl_s_{day}_{party}_{i}", "title": n[:24],
                 "description": f"{next(s['time'] for s in slots if s['sitting'] == n)} onwards"} for i, n in enumerate(names)]
        await _list(phone, tenant_id, "When?", "Which sitting?", "Choose", rows, f"Book here: {tables.booking_url(tenant_id)}")
        return True
    if sitting:
        slots = [s for s in slots if s["sitting"] == sitting] or slots
    rows = [{"id": f"tbl_t_{day}_{party}_{s['time'].replace(':', '')}", "title": s["time"], "description": s["sitting"][:72]}
            for s in _spread(slots)]
    await _list(phone, tenant_id, "What time?", f"These times are free for {party} on {day}.", "Choose a time", rows,
                f"Book here: {tables.booking_url(tenant_id)}")
    return True


async def _known_name(tenant_id: str, phone: str) -> str:
    from vula.commerce import service, tables
    mine = tables._db().table("commerce_table_bookings").select("customer_name,customer_phone").eq("tenant_id", tenant_id) \
        .like("customer_phone", f"%{re.sub(r'[^0-9]', '', phone)[-9:]}").order("created_at", desc=True).limit(1).execute().data or []
    if mine and mine[0].get("customer_name"):
        return mine[0]["customer_name"]
    try:
        prof = await service.get_customer_profile(tenant_id, phone)
    except Exception:  # noqa: BLE001
        prof = None
    return ((prof or {}).get("name") or "").strip()


async def choose_time(phone: str, tenant_id: str, day: str, party: int, hhmm: str) -> bool:
    from vula.commerce import tables
    try:
        starts = tables._at(tables._parse_day(day), f"{hhmm[:2]}:{hhmm[2:]}").isoformat()
    except (tables.TableError, ValueError):
        return False
    name = await _known_name(tenant_id, phone)
    if name:
        return await place(phone, tenant_id, starts, party, name)
    from vula import open_questions as oq
    oq.ask(tenant_id, phone, NAME_QUESTION, f"{starts}|{party}", "name for the booking")
    await _say(phone, tenant_id, "What name should the booking be under?")
    return True


async def take_name(phone: str, tenant_id: str, text: str, question: Dict[str, Any]) -> bool:
    from vula.commerce.event_chat import clean_name
    name = clean_name(text)
    if not name:
        return False
    from vula import open_questions as oq
    starts, _, party = str(question.get("ref_id") or "").partition("|")
    oq.close(question["id"], name)
    if not party.isdigit():
        return False
    return await place(phone, tenant_id, starts, int(party), name)


async def place(phone: str, tenant_id: str, starts_at: str, party: int, name: str) -> bool:
    from vula.commerce import tables
    try:
        if len(tables.upcoming_for(tenant_id, phone)) >= 3:
            raise tables.TableError("You already have three bookings coming up. Reply *my booking* to see them.")
        b = await tables.book(tenant_id, starts_at=starts_at, party=party, name=name, phone=phone, channel="whatsapp")
    except tables.TableError as exc:
        await _say(phone, tenant_id, f"{exc} Reply *book* to pick another time.")
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("table booking in chat failed for %s: %s", tenant_id, exc)
        await _say(phone, tenant_id, "Sorry — that didn't go through. Please try again in a moment.")
        return True
    await _say(phone, tenant_id, f"✅ Booked: a table for {b['party_size']} on {b['when']}, under {b['customer_name']}.\n"
                                 f"Reference {b['ref']}.\n\nTo cancel, reply *cancel my booking*.")
    return True


async def show_own(phone: str, tenant_id: str) -> bool:
    from vula.commerce import tables
    mine = tables.upcoming_for(tenant_id, phone)
    if not mine:
        await _say(phone, tenant_id, "You have no bookings coming up. Reply *book* to make one.")
        return True
    rows = [{"id": f"tbl_c_{b['id']}", "title": f"Cancel {b['ref']}"[:24], "description": f"{b['party_size']} people, {b['when']}"[:72]}
            for b in mine]
    lines = "\n".join(f"• {b['when']} for {b['party_size']} ({b['ref']})" for b in mine)
    await _list(phone, tenant_id, "Your bookings", f"Your bookings:\n{lines}\n\nTap one to cancel it.", "Cancel one", rows,
                f"Your bookings:\n{lines}\n\nTo cancel, message us with the reference.")
    return True


async def handle_tap(phone: str, tenant_id: str, reply_id: str) -> bool:
    """A tap on one of this module's rows or buttons (ids starting tbl_)."""
    from vula.commerce import tables
    if not tables.settings(tenant_id)["enabled"]:
        return False
    if reply_id == "tbl_start":
        return await ask_day(phone, tenant_id)
    kind, _, rest = reply_id[4:].partition("_")
    parts = rest.split("_")
    if kind == "d" and len(parts) == 1:
        return await ask_party(phone, tenant_id, parts[0])
    if kind == "p" and len(parts) == 2 and parts[1].isdigit():
        return await ask_time(phone, tenant_id, parts[0], int(parts[1]))
    if kind == "s" and len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
        try:
            names = list(dict.fromkeys(s["sitting"] for s in tables.slots(tenant_id, parts[0], int(parts[1]))))
        except tables.TableError:
            names = []
        idx = int(parts[2])
        return await ask_time(phone, tenant_id, parts[0], int(parts[1]), names[idx] if idx < len(names) else "")
    if kind == "t" and len(parts) == 3 and parts[1].isdigit() and re.fullmatch(r"\d{4}", parts[2]):
        return await choose_time(phone, tenant_id, parts[0], int(parts[1]), parts[2])
    if kind == "c" and len(parts) == 1:
        done = tables.cancel_own(tenant_id, phone, parts[0])
        await _say(phone, tenant_id, f"Your booking for {done['when']} ({done['ref']}) is cancelled." if done
                   else "I couldn't find that booking under your number. It may already be cancelled.")
        return True
    return False
