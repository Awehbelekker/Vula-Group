"""Table bookings and specials for restaurants (24 Nov 2026, migration 299).

A restaurant sets its sittings, how many people it seats at once and how long a table is kept. A
customer books on the business's booking page or in its WhatsApp chat; the team sees the day's
bookings in the dashboard and can add a phone booking by hand.

The rule: the people already booked for any part of the time this party would be sitting, plus this
party, must fit in the room (`max_covers`). The check and the insert are one database call under a
per-business lock (`book_table`), so the last table can't be given twice. Times are South African
time; nothing is worked out by a model.

Specials are the restaurant's own words and prices (integer cents), shown on the booking page and
sent when a customer asks "specials" — only the ones that run today.
"""
from __future__ import annotations

import logging
import re
import secrets
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

SAST = ZoneInfo("Africa/Johannesburg")
ACTIVE = ("confirmed", "seated")
STATUSES = ("confirmed", "seated", "completed", "no_show", "cancelled")
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
DEFAULTS = {"enabled": False, "sittings": [], "slot_minutes": 30, "seating_minutes": 120, "max_covers": 40,
            "max_party": 10, "lead_minutes": 60, "horizon_days": 60, "note": None}
_REF_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class TableError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _db():
    from vula.commerce import service
    return service._client()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(raw: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(raw or "")).strip()[:limit].strip()


def rand(cents: Any) -> str:
    c = int(cents or 0)
    return f"R{c // 100:,}.{c % 100:02d}" if c % 100 else f"R{c // 100:,}"


def _int(raw: Any, what: str, lo: int, hi: int) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or not lo <= raw <= hi:
        raise TableError(f"{what} must be a whole number between {lo} and {hi}.")
    return raw


# ── settings ──────────────────────────────────────────────────────────────────────────────────
def settings(tenant_id: str) -> dict:
    try:
        rows = (_db().table("commerce_table_settings").select("*").eq("tenant_id", tenant_id).limit(1)
                .execute().data or [])
    except Exception as exc:  # noqa: BLE001 — migration 299 not applied: bookings are simply off
        log.debug("table settings unreadable for %s: %s", tenant_id, exc)
        rows = []
    return {**DEFAULTS, **{k: v for k, v in (rows[0] if rows else {}).items() if v is not None},
            "tenant_id": tenant_id}


def _clean_sittings(raw: Any) -> List[dict]:
    if not isinstance(raw, list) or len(raw) > 6:
        raise TableError("Add up to 6 sittings, like Lunch and Dinner.")
    out = []
    for s in raw:
        if not isinstance(s, dict):
            raise TableError("Each sitting needs a name, its days and its times.")
        name = _text(s.get("name"), 30)
        start, end = str(s.get("start") or ""), str(s.get("end") or "")
        days = s.get("days")
        if not name:
            raise TableError("Give each sitting a name, like Lunch.")
        if not _TIME_RE.match(start) or not _TIME_RE.match(end) or end <= start:
            raise TableError(f"{name}: give a start and an end time, the end after the start (like 18:00 to 22:00).")
        if not isinstance(days, list) or not days or not all(isinstance(d, int) and not isinstance(d, bool)
                                                              and 0 <= d <= 6 for d in days):
            raise TableError(f"{name}: tick the days it runs.")
        out.append({"name": name, "days": sorted(set(days)), "start": start, "end": end})
    return out


def save_settings(tenant_id: str, data: dict) -> dict:
    cur = settings(tenant_id)
    patch: Dict[str, Any] = {}
    d = data or {}
    if "enabled" in d:
        if not isinstance(d["enabled"], bool):
            raise TableError("Bookings are either on or off.")
        patch["enabled"] = d["enabled"]
    if "sittings" in d:
        patch["sittings"] = _clean_sittings(d["sittings"])
    for key, what, lo, hi in (("seating_minutes", "How long a table is kept (minutes)", 30, 480),
                              ("max_covers", "How many people you can seat at once", 1, 2000),
                              ("max_party", "The biggest group that can book online", 1, 100),
                              ("lead_minutes", "How far ahead a booking must be made (minutes)", 0, 10080),
                              ("horizon_days", "How many days ahead people can book", 1, 365)):
        if key in d:
            patch[key] = _int(d[key], what, lo, hi)
    if "slot_minutes" in d:
        if d["slot_minutes"] not in (15, 30, 60):
            raise TableError("Booking times are every 15, 30 or 60 minutes.")
        patch["slot_minutes"] = d["slot_minutes"]
    if "note" in d:
        patch["note"] = _text(d["note"], 200) or None
    merged = {**cur, **patch}
    if merged["enabled"] and not merged["sittings"]:
        raise TableError("Add at least one sitting before taking bookings.")
    row = {k: merged[k] for k in DEFAULTS} | {"tenant_id": tenant_id, "updated_at": _now().isoformat()}
    _db().table("commerce_table_settings").upsert(row, on_conflict="tenant_id").execute()
    back = settings(tenant_id)
    if any(back[k] != row[k] for k in DEFAULTS):
        raise TableError("The settings didn't save. Nothing changed.", 500)
    return back


# ── when can a party sit? ─────────────────────────────────────────────────────────────────────
def _parse_day(raw: Any) -> date:
    try:
        return date.fromisoformat(str(raw))
    except ValueError as exc:
        raise TableError("Choose a day.") from exc


def _at(day: date, hhmm: str) -> datetime:
    h, m = hhmm.split(":")
    return datetime.combine(day, time(int(h), int(m)), tzinfo=SAST)


def _day_bookings(tenant_id: str, day: date) -> List[dict]:
    lo = datetime.combine(day, time(0, 0), tzinfo=SAST) - timedelta(hours=8)
    hi = datetime.combine(day, time(23, 59), tzinfo=SAST) + timedelta(hours=8)
    from vula.db_pages import all_rows
    return all_rows(lambda: _db().table("commerce_table_bookings").select("*").eq("tenant_id", tenant_id)
                    .gte("starts_at", lo.isoformat()).lte("starts_at", hi.isoformat()))


def _when(raw: Any) -> datetime:
    return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))


def _taken(bookings: List[dict], start: datetime, end: datetime) -> int:
    return sum(int(b["party_size"]) for b in bookings
               if b.get("status") in ACTIVE and _when(b["starts_at"]) < end and _when(b["ends_at"]) > start)


def open_days(tenant_id: str, count: int = 7, st: Optional[dict] = None) -> List[date]:
    """The next days the restaurant has a sitting on, from today, within its booking horizon."""
    st = st or settings(tenant_id)
    if not st["enabled"]:
        return []
    today = _now().astimezone(SAST).date()
    out = []
    for n in range(min(int(st["horizon_days"]), 90) + 1):
        d = today + timedelta(days=n)
        if any(d.weekday() in s["days"] for s in st["sittings"]):
            out.append(d)
            if len(out) >= count:
                break
    return out


def slots(tenant_id: str, day: Any, party: Any, st: Optional[dict] = None) -> List[dict]:
    """Every time a party of this size could sit on this day: [{sitting, time "18:30", starts_at,
    free}] — only times with room, far enough ahead, inside the booking horizon."""
    st = st or settings(tenant_id)
    d = day if isinstance(day, date) else _parse_day(day)
    if not st["enabled"]:
        return []
    size = _int(party, "How many people", 1, 100)
    if size > st["max_party"]:
        raise TableError(f"For more than {st['max_party']} people, message us and we'll arrange it.", 409)
    now = _now()
    today = now.astimezone(SAST).date()
    if d < today or d > today + timedelta(days=int(st["horizon_days"])):
        return []
    bookings = _day_bookings(tenant_id, d)
    earliest = now + timedelta(minutes=int(st["lead_minutes"]))
    seat = timedelta(minutes=int(st["seating_minutes"]))
    out = []
    for s in st["sittings"]:
        if d.weekday() not in s["days"]:
            continue
        t, last = _at(d, s["start"]), _at(d, s["end"])
        while t < last:
            if t >= earliest:
                free = int(st["max_covers"]) - _taken(bookings, t, t + seat)
                if free >= size:
                    out.append({"sitting": s["name"], "time": t.strftime("%H:%M"),
                                "starts_at": t.astimezone(timezone.utc).isoformat(), "free": free})
            t += timedelta(minutes=int(st["slot_minutes"]))
    return out


# ── bookings ──────────────────────────────────────────────────────────────────────────────────
def _phone(raw: Any, required: bool = True) -> Optional[str]:
    digits = re.sub(r"[^\d]", "", str(raw or ""))
    if not digits and not required:
        return None
    if not 9 <= len(digits) <= 15:
        raise TableError("Enter a cellphone number we can confirm the booking on.")
    return "27" + digits[1:] if digits.startswith("0") and len(digits) == 10 else digits


def when_text(starts_at: Any) -> str:
    """Sat 6 Dec at 18:30 — South African time."""
    s = _when(starts_at).astimezone(SAST)
    return f"{s.strftime('%a')} {s.day} {s.strftime('%b')} at {s.strftime('%H:%M')}"


def _out(b: dict) -> dict:
    return {"id": b["id"], "ref": b["ref"], "starts_at": b["starts_at"], "ends_at": b["ends_at"],
            "when": when_text(b["starts_at"]), "party_size": int(b["party_size"]),
            "customer_name": b.get("customer_name") or "", "customer_phone": b.get("customer_phone") or "",
            "notes": b.get("notes") or "", "status": b.get("status"), "channel": b.get("channel")}


async def book(tenant_id: str, *, starts_at: Any, party: Any, name: Any, phone: Any, notes: Any = "",
               channel: str = "web", booked_by: str = "", tell_team: bool = True) -> dict:
    """Book a table. The time must be one the restaurant offers (a slot of one of its sittings),
    and the party must fit — decided by the database, not by this check alone."""
    from vula.api import tenants
    st = settings(tenant_id)
    if not st["enabled"] or not tenants.is_active(tenant_id):
        raise TableError("This restaurant isn't taking bookings here right now.", 409)
    size = _int(party, "How many people", 1, 100)
    who = _text(name, 80)
    if len(who) < 2:
        raise TableError("Enter the name for the booking.")
    number = _phone(phone, required=channel != "dashboard")
    try:
        start = _when(starts_at)
    except ValueError as exc:
        raise TableError("Choose a time.") from exc
    if start.tzinfo is None:
        start = start.replace(tzinfo=SAST)
    day = start.astimezone(SAST).date()
    offered = {s["starts_at"] for s in slots(tenant_id, day, size, st)} if channel != "dashboard" \
        else {s["starts_at"] for s in _all_slots(st, day)}
    key = start.astimezone(timezone.utc).isoformat()
    if key not in offered:
        raise TableError("That time isn't available any more. Choose another.", 409)
    end = start + timedelta(minutes=int(st["seating_minutes"]))
    ref = "T-" + "".join(secrets.choice(_REF_ALPHABET) for _ in range(5))
    made = _db().rpc("book_table", {
        "p_tenant_id": tenant_id, "p_ref": ref, "p_starts_at": key,
        "p_ends_at": end.astimezone(timezone.utc).isoformat(), "p_party": size,
        "p_max_covers": int(st["max_covers"]), "p_name": who, "p_phone": number,
        "p_notes": _text(notes, 300) or None, "p_channel": channel, "p_booked_by": booked_by or None}).execute().data
    if not made:
        raise TableError("That time has just filled up. Choose another.", 409)
    rows = (_db().table("commerce_table_bookings").select("*").eq("tenant_id", tenant_id).eq("ref", ref)
            .limit(1).execute().data or [])
    if not rows:
        raise TableError("The booking didn't save. Please try again.", 500)
    b = _out(rows[0])
    if tell_team:
        try:
            from vula.integrations.notify import notify_team
            await notify_team(tenant_id, "table_booking",
                              f"🍽️ New booking: {b['customer_name']}, {b['party_size']} people, {b['when']} "
                              f"({b['ref']})." + (f" Note: {b['notes']}" if b["notes"] else ""),
                              idem_key=f"table_booking:{b['id']}")
        except Exception as exc:  # noqa: BLE001
            log.warning("new booking not sent to the team (%s): %s", tenant_id, exc)
    return b


def _all_slots(st: dict, d: date) -> List[dict]:
    """Every slot of the day's sittings, full or not — the team may book by hand up to the room's limit."""
    out = []
    for s in st["sittings"]:
        if d.weekday() not in s["days"]:
            continue
        t, last = _at(d, s["start"]), _at(d, s["end"])
        while t < last:
            out.append({"sitting": s["name"], "time": t.strftime("%H:%M"),
                        "starts_at": t.astimezone(timezone.utc).isoformat()})
            t += timedelta(minutes=int(st["slot_minutes"]))
    return out


def day_sheet(tenant_id: str, day: Any) -> dict:
    """The day for the team: its bookings in time order, the covers booked, and the times."""
    st = settings(tenant_id)
    d = _parse_day(day)
    rows = [b for b in _day_bookings(tenant_id, d) if _when(b["starts_at"]).astimezone(SAST).date() == d]
    rows.sort(key=lambda b: (b["starts_at"], b.get("customer_name") or ""))
    live = [b for b in rows if b.get("status") in ACTIVE + ("completed",)]
    return {"date": d.isoformat(), "bookings": [_out(b) for b in rows], "covers": sum(int(b["party_size"]) for b in live),
            "max_covers": int(st["max_covers"]), "times": _all_slots(st, d)}


def set_status(tenant_id: str, booking_id: str, status: str) -> dict:
    if status not in STATUSES:
        raise TableError("Status must be confirmed, seated, completed, no_show or cancelled.")
    patch = {"status": status, "updated_at": _now().isoformat()}
    if status == "cancelled":
        patch["cancelled_at"] = _now().isoformat()
    res = (_db().table("commerce_table_bookings").update(patch).eq("tenant_id", tenant_id).eq("id", booking_id).execute())
    if not res.data:
        raise TableError("That booking isn't on file.", 404)
    return _out(res.data[0])


def upcoming_for(tenant_id: str, phone: str) -> List[dict]:
    """This number's own bookings that are still to come."""
    digits = re.sub(r"[^\d]", "", phone or "")[-9:]
    if len(digits) < 9:
        return []
    rows = (_db().table("commerce_table_bookings").select("*").eq("tenant_id", tenant_id).eq("status", "confirmed")
            .like("customer_phone", f"%{digits}").gte("starts_at", (_now() - timedelta(hours=1)).isoformat())
            .order("starts_at").limit(10).execute().data or [])
    return [_out(b) for b in rows]


def cancel_own(tenant_id: str, phone: str, booking_id: str) -> Optional[dict]:
    """A customer cancels their own booking — only one made on their number, only before it starts."""
    mine = {b["id"] for b in upcoming_for(tenant_id, phone)}
    if booking_id not in mine:
        return None
    return set_status(tenant_id, booking_id, "cancelled")


# ── specials ──────────────────────────────────────────────────────────────────────────────────
def _clean_special(d: dict) -> dict:
    title = _text(d.get("title"), 80)
    if not title:
        raise TableError("Give the special a name.")
    price = d.get("price_cents")
    if price is not None and (isinstance(price, bool) or not isinstance(price, int) or not 0 <= price <= 10_000_000):
        raise TableError("The price must be an amount in cents, or left out.")
    img = _text(d.get("image_url"), 500)
    if img and not img.startswith("https://"):
        raise TableError("The photo's address must start with https://")
    days = d.get("days") or []
    if not isinstance(days, list) or not all(isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= 6 for x in days):
        raise TableError("Tick the days the special runs, or none for every day.")
    out = {"title": title, "description": re.sub(r"[ \t]+", " ", str(d.get("description") or "")).strip()[:500] or None,
           "price_cents": price, "image_url": img or None, "days": sorted(set(days)),
           "active": d.get("active") is not False}
    for key in ("starts_on", "ends_on"):
        v = d.get(key)
        out[key] = _parse_day(v).isoformat() if v else None
    if out["starts_on"] and out["ends_on"] and out["ends_on"] < out["starts_on"]:
        raise TableError("The special can't end before it starts.")
    return out


def list_specials(tenant_id: str) -> List[dict]:
    try:
        return (_db().table("commerce_specials").select("*").eq("tenant_id", tenant_id)
                .order("sort_order").limit(200).execute().data or [])
    except Exception as exc:  # noqa: BLE001
        log.debug("specials unreadable for %s: %s", tenant_id, exc)
        return []


def save_special(tenant_id: str, data: dict) -> dict:
    row = _clean_special(data or {})
    sid = str((data or {}).get("id") or "")
    if sid:
        res = (_db().table("commerce_specials").update({**row, "updated_at": _now().isoformat()})
               .eq("tenant_id", tenant_id).eq("id", sid).execute())
        if not res.data:
            raise TableError("That special isn't on file.", 404)
        return res.data[0]
    row = {"id": str(uuid.uuid4()), "tenant_id": tenant_id, **row}
    _db().table("commerce_specials").insert(row).execute()
    return row


def delete_special(tenant_id: str, special_id: str) -> None:
    _db().table("commerce_specials").delete().eq("tenant_id", tenant_id).eq("id", special_id).execute()


def runs_on(special: dict, day: date) -> bool:
    if special.get("active") is False:
        return False
    if special.get("days") and day.weekday() not in special["days"]:
        return False
    iso = day.isoformat()
    return not ((special.get("starts_on") and iso < str(special["starts_on"]))
                or (special.get("ends_on") and iso > str(special["ends_on"])))


def todays_specials(tenant_id: str, day: Optional[date] = None) -> List[dict]:
    d = day or _now().astimezone(SAST).date()
    return [s for s in list_specials(tenant_id) if runs_on(s, d)]


def specials_text(tenant_id: str, specials: List[dict]) -> str:
    """Today's specials as a WhatsApp message — the restaurant's own words and prices."""
    lines = []
    for s in specials[:10]:
        head = f"*{s['title']}*" + (f" — {rand(s['price_cents'])}" if s.get("price_cents") is not None else "")
        lines.append(head + (f"\n{s['description']}" if s.get("description") else ""))
    return "🍽️ Today's specials\n\n" + "\n\n".join(lines)


def booking_url(tenant_id: str) -> str:
    from vula.commerce import web_basket
    base = web_basket.public_base(tenant_id)
    return f"{base}/book/{tenant_id}" if base else ""


# ── the reminder ──────────────────────────────────────────────────────────────────────────────
async def send_reminders(now: Optional[datetime] = None) -> int:
    """A booking 2–5 hours away hears once, on WhatsApp. The row is marked first, so never twice."""
    now = now or _now()
    rows = (_db().table("commerce_table_bookings").select("*").eq("status", "confirmed")
            .gte("starts_at", (now + timedelta(hours=2)).isoformat())
            .lte("starts_at", (now + timedelta(hours=5)).isoformat()).is_("reminded_at", "null").limit(500)
            .execute().data or [])
    sent = 0
    for b in rows:
        if not b.get("customer_phone"):
            continue
        res = (_db().table("commerce_table_bookings").update({"reminded_at": now.isoformat()})
               .eq("id", b["id"]).eq("tenant_id", b["tenant_id"]).is_("reminded_at", "null").execute())
        if not res.data:
            continue
        try:
            from vula.api import tenants
            from vula.api.whatsapp import send_template
            from vula.commerce.job_config import template_name
            news = f"Your table for {b['party_size']} at {tenants.display_name(b['tenant_id'])} is booked for {when_text(b['starts_at'])}."
            if await send_template(b["tenant_id"], b["customer_phone"], template_name(b["tenant_id"], "order_update"),
                                   [b.get("customer_name") or "there", b["ref"], news]):
                sent += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("table reminder %s failed: %s", b.get("ref"), exc)
    return sent


async def run_forever(interval_s: int = 1800) -> None:
    import asyncio
    await asyncio.sleep(150)
    while True:
        try:
            n = await send_reminders()
            if n:
                log.info("table booking reminders: %d", n)
        except Exception as exc:  # noqa: BLE001
            log.warning("table reminders run failed: %s", exc)
        await asyncio.sleep(interval_s)
