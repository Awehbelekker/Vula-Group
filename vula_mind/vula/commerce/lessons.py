"""Lessons and gear hire that the business confirms by hand (24 Nov 2026, migration 303) — surf
schools, kite schools, board and wetsuit hire.

A customer asks in the business's WhatsApp chat (vula/commerce/lesson_chat.py) or the team takes
the request by phone in the dashboard. Vula keeps the request and quotes it from the business's own
price list. Nothing is booked until a person at the business confirms it with a time and a coach:
the swell, the wind, the tide and who is teaching decide when a lesson runs, so no time is promised
by a machine.

    requested ─ confirm (time, coach, price) → confirmed ─→ completed | no_show | cancelled
              └ decline (a reason the customer is told)

Every amount is integer cents from the price list, worked out here: a group lesson is a price per
person that depends on how many come; a private lesson is priced for one person (a private lesson
for more is priced by the business when it confirms); a package is a price for its sessions; an
extra (a wetsuit) is per person per session. Gear is priced by how long it is out. A confirmed
request can make a draft invoice of its lines (vula/commerce/service.create_invoice) — that is
where the money is recorded and paid; this module never records a payment.

The evening before, confirmed lessons at a spot with a location get a conditions check
(vula/commerce/conditions.py) sent to the team: the forecast's own figures and a plain rating.
"""
from __future__ import annotations

import logging
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

SAST = ZoneInfo("Africa/Johannesburg")
STATUSES = ("requested", "confirmed", "declined", "cancelled", "completed", "no_show")
OPEN = ("requested", "confirmed")
LEVELS = {"first_timer": "First timer", "beginner": "Beginner (done it once or twice)",
          "intermediate": "Intermediate", "advanced": "Advanced"}
PARTS = {"morning": "Morning", "midday": "Midday", "afternoon": "Afternoon", "any": "Any time"}
DURATIONS = {"2h": "2 hours", "4h": "4 hours", "1d": "1 day", "2d": "2 days", "3d": "3 days", "7d": "A week"}
_DAYS = {"1d": 1, "2d": 2, "3d": 3, "7d": 7}
SPORTS = ("surf", "sup", "kite", "windsurf", "wing", "foil", "bodyboard", "kayak", "skate", "other")
SPORT_NAMES = {"surf": "Surfing", "sup": "Stand-up paddling", "kite": "Kitesurfing", "windsurf": "Windsurfing",
               "wing": "Wing foiling", "foil": "Foiling", "bodyboard": "Bodyboarding", "kayak": "Kayaking",
               "skate": "Skating", "other": "Other"}
DEFAULTS = {"enabled": False, "lessons": [], "hire": [], "spot": None, "form_url": None, "pay_note": None,
            "note": None, "coach_share_bp": None}
# migration 305's columns: read with a default, written only when they are being changed, so a
# database that doesn't have them yet still saves everything else
DIRECTORY_DEFAULTS = {"coach_profiles": [], "listing": {}}
_REF_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_ID_RE = re.compile(r"^[a-z0-9]{1,20}$")
MAX_CENTS = 100_000_00           # R100 000: a typed price above this is a typing slip


class LessonError(Exception):
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
        raise LessonError(f"{what} must be a whole number between {lo} and {hi}.")
    return raw


def _cents(raw: Any, what: str, optional: bool = False) -> Optional[int]:
    if raw is None and optional:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or not 0 <= raw <= MAX_CENTS:
        raise LessonError(f"{what}: enter the price in rand.")
    return raw


# ── settings: the business's own lessons, gear and prices ─────────────────────────────────────
def settings(tenant_id: str) -> dict:
    try:
        rows = (_db().table("commerce_lesson_settings").select("*").eq("tenant_id", tenant_id).limit(1)
                .execute().data or [])
    except Exception as exc:  # noqa: BLE001 — migration 303 not applied: lessons are simply off
        log.debug("lesson settings unreadable for %s: %s", tenant_id, exc)
        rows = []
    return {**DEFAULTS, **DIRECTORY_DEFAULTS, **{k: v for k, v in (rows[0] if rows else {}).items() if v is not None},
            "tenant_id": tenant_id}


def _clean_id(raw: Any, name: str, taken: set) -> str:
    made = re.sub(r"[^a-z0-9]", "", str(raw or name).lower())[:20] or "item"
    if not _ID_RE.match(made):
        raise LessonError(f"{name}: its short name can only have letters and numbers.")
    if made in taken:
        raise LessonError(f"Two entries are both called '{made}'. Give each its own name.")
    taken.add(made)
    return made


def _clean_lessons(raw: Any) -> List[dict]:
    if not isinstance(raw, list) or len(raw) > 9:
        raise LessonError("Add up to 9 kinds of lesson.")
    out, taken = [], set()
    for item in raw:
        if not isinstance(item, dict):
            raise LessonError("Each lesson needs a name and its prices.")
        name = _text(item.get("name"), 24)
        if len(name) < 2:
            raise LessonError("Give each lesson a name, like Surf lesson.")
        tiers = []
        for t in item.get("group") or []:
            if not isinstance(t, dict):
                raise LessonError(f"{name}: each group price needs how many people it is for.")
            lo = _int(t.get("min"), f"{name}: people from", 1, 50)
            hi = None if t.get("max") is None else _int(t.get("max"), f"{name}: people up to", lo, 50)
            tiers.append({"min": lo, "max": hi,
                          "single_cents": _cents(t.get("single_cents"), f"{name}, group of {lo}"),
                          "package_cents": _cents(t.get("package_cents"), f"{name}, package for {lo}", optional=True)})
        tiers.sort(key=lambda t: t["min"])
        for a, b in zip(tiers, tiers[1:]):
            if a["max"] is None or a["max"] >= b["min"]:
                raise LessonError(f"{name}: two group prices cover the same number of people.")
        private = item.get("private")
        if private is not None:
            if not isinstance(private, dict):
                raise LessonError(f"{name}: the private lesson needs a price.")
            private = {"single_cents": _cents(private.get("single_cents"), f"{name}, private"),
                       "package_cents": _cents(private.get("package_cents"), f"{name}, private package", optional=True)}
        if not tiers and not private:
            raise LessonError(f"{name}: add a group price or a private price.")
        extras, extra_ids = [], set()
        for e in (item.get("extras") or [])[:3]:
            if not isinstance(e, dict) or len(_text(e.get("name"), 20)) < 2:
                raise LessonError(f"{name}: each extra needs a name and a price.")
            extras.append({"id": _clean_id(e.get("id"), _text(e.get("name"), 20), extra_ids),
                           "name": _text(e.get("name"), 20), "cents": _cents(e.get("cents"), f"{name}, {e.get('name')}")})
        sport = str(item.get("sport") or "other")
        out.append({"id": _clean_id(item.get("id"), name, taken), "name": name,
                    "sport": sport if sport in SPORTS else "other",
                    "minutes": _int(item.get("minutes", 90), f"{name}: how long it runs (minutes)", 15, 720),
                    "max_per_coach": _int(item.get("max_per_coach", 5), f"{name}: people per coach", 1, 50),
                    "package_sessions": _int(item.get("package_sessions", 4), f"{name}: sessions in a package", 2, 20),
                    "group": tiers, "private": private, "extras": extras,
                    "about": _text(item.get("about"), 200) or None})
    return out


def _clean_hire(raw: Any) -> List[dict]:
    if not isinstance(raw, list) or len(raw) > 9:
        raise LessonError("Add up to 9 kinds of gear for hire.")
    out, taken = [], set()
    for item in raw:
        if not isinstance(item, dict):
            raise LessonError("Each piece of gear needs a name and its prices.")
        name = _text(item.get("name"), 24)
        if len(name) < 2:
            raise LessonError("Give each piece of gear a name, like Surfboard.")
        prices_in = item.get("prices") if isinstance(item.get("prices"), dict) else {}
        prices = {k: _cents(prices_in[k], f"{name}, {DURATIONS[k].lower()}") for k in DURATIONS
                  if k != "7d" and prices_in.get(k) is not None}
        if not prices:
            raise LessonError(f"{name}: add at least one price.")
        out.append({"id": _clean_id(item.get("id"), name, taken), "name": name, "prices": prices,
                    "extra_day_cents": _cents(item.get("extra_day_cents"), f"{name}, each day from 4 days", optional=True)})
    return out


def _clean_spot(raw: Any) -> Optional[dict]:
    if not raw:
        return None
    if not isinstance(raw, dict) or len(_text(raw.get("name"), 60)) < 2:
        raise LessonError("Give the spot a name, like Big Bay.")
    lat, lng = raw.get("lat"), raw.get("lng")
    ok = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (lat, lng))
    if not ok or not -90 <= lat <= 90 or not -180 <= lng <= 180:
        raise LessonError("The spot needs its position on the map.")
    out = {"name": _text(raw.get("name"), 60), "lat": round(float(lat), 4), "lng": round(float(lng), 4)}
    faces = raw.get("faces")          # the compass bearing the beach looks out to sea on (270 = west)
    if faces not in (None, ""):
        if isinstance(faces, bool) or not isinstance(faces, (int, float)) or not 0 <= faces < 360:
            raise LessonError("Choose which way the beach faces.")
        out["faces"] = int(faces)
    return out


def _clean_url(raw: Any) -> Optional[str]:
    url = _text(raw, 300)
    if not url:
        return None
    if not re.match(r"^https://[^\s/]+\.[^\s]+$", url):
        raise LessonError("The form's address must start with https://")
    return url


def save_settings(tenant_id: str, data: dict) -> dict:
    cur = settings(tenant_id)
    d = data or {}
    patch: Dict[str, Any] = {}
    if "enabled" in d:
        if not isinstance(d["enabled"], bool):
            raise LessonError("Requests are either on or off.")
        patch["enabled"] = d["enabled"]
    if "lessons" in d:
        patch["lessons"] = _clean_lessons(d["lessons"])
    if "hire" in d:
        patch["hire"] = _clean_hire(d["hire"])
    if "spot" in d:
        patch["spot"] = _clean_spot(d["spot"])
    if "form_url" in d:
        patch["form_url"] = _clean_url(d["form_url"])
    for key, limit in (("pay_note", 300), ("note", 200)):
        if key in d:
            patch[key] = _text(d[key], limit) or None
    if "coach_share_bp" in d:                       # the coach's part of a lesson, in hundredths of a percent (70% = 7000)
        patch["coach_share_bp"] = None if d["coach_share_bp"] is None else _int(d["coach_share_bp"], "The coach's share", 0, 10000)
    from vula.commerce import lesson_directory
    if "coach_profiles" in d:                       # what the school shows of each coach
        patch["coach_profiles"] = lesson_directory.clean_profiles(tenant_id, d["coach_profiles"])
    if "listing" in d:                              # found in the customer app's directory, or not
        patch["listing"] = lesson_directory.clean_listing(d["listing"])
    merged = {**cur, **patch}
    if merged["enabled"] and not (merged["lessons"] or merged["hire"]):
        raise LessonError("Add a lesson or some gear for hire before taking requests.")
    keys = list(DEFAULTS) + [k for k in DIRECTORY_DEFAULTS if k in patch]
    row = {k: merged[k] for k in keys} | {"tenant_id": tenant_id, "updated_at": _now().isoformat()}
    _db().table("commerce_lesson_settings").upsert(row, on_conflict="tenant_id").execute()
    back = settings(tenant_id)
    if any(back[k] != row[k] for k in keys):
        raise LessonError("The settings didn't save. Nothing changed.", 500)
    lesson_directory.changed()                      # the spot, the prices or the listing may have moved
    return back


def lesson(st: dict, lesson_id: str) -> Optional[dict]:
    return next((x for x in st["lessons"] if x["id"] == lesson_id), None)


def gear(st: dict, item_id: str) -> Optional[dict]:
    return next((x for x in st["hire"] if x["id"] == item_id), None)


# ── the quote: the price list, worked out in code ─────────────────────────────────────────────
def _line(description: str, quantity: int, unit_cents: int) -> dict:
    return {"description": description, "quantity": quantity, "unit_price_cents": unit_cents}


def total(lines: List[dict]) -> int:
    return sum(int(ln["quantity"]) * int(ln["unit_price_cents"]) for ln in lines)


def quote_lesson(st: dict, lesson_id: str, fmt: str, people: int, package: bool,
                 extras: Optional[Dict[str, int]] = None) -> Tuple[Optional[int], List[dict], int]:
    """(total cents or None when the business has no listed price for this, lines, sessions)."""
    ls = lesson(st, lesson_id)
    if not ls:
        raise LessonError("That lesson isn't offered any more.")
    people = _int(people, "How many people", 1, 50)
    if fmt not in ("group", "private"):
        raise LessonError("Choose a group lesson or a private one.")
    sessions = int(ls["package_sessions"]) if package else 1
    lines: List[dict] = []
    priced = True
    if fmt == "group":
        tier = next((t for t in ls["group"] if t["min"] <= people and (t["max"] is None or people <= t["max"])), None)
        each = (tier or {}).get("package_cents" if package else "single_cents")
        if each is None:
            priced = False
        else:
            what = f"{ls['name']} (group)" + (f", {sessions}-lesson package" if package else "")
            lines.append(_line(what + ", per person", people, each))
    else:
        each = (ls.get("private") or {}).get("package_cents" if package else "single_cents")
        if each is None or people != 1:
            priced = False                      # a private lesson for more than one: the business prices it
        else:
            lines.append(_line(f"{ls['name']} (private)" + (f", {sessions}-lesson package" if package else ""), 1, each))
    for e in ls.get("extras") or []:
        count = int((extras or {}).get(e["id"]) or 0)
        if count:
            if not 1 <= count <= people:
                raise LessonError(f"{e['name']}: at most one per person.")
            per = " per lesson" if sessions > 1 else ""
            lines.append(_line(f"{e['name']}{per}", count * sessions, e["cents"]))
    return (total(lines) if priced else None), lines, sessions


def quote_hire(st: dict, item_id: str, duration: str, count: int) -> Tuple[Optional[int], List[dict]]:
    g = gear(st, item_id)
    if not g:
        raise LessonError("That gear isn't for hire any more.")
    count = _int(count, "How many", 1, 20)
    if duration not in DURATIONS:
        raise LessonError("Choose how long you need it.")
    price = g["prices"].get(duration)
    if price is None and duration in _DAYS and _DAYS[duration] >= 4 and g.get("extra_day_cents") is not None:
        price = _DAYS[duration] * int(g["extra_day_cents"])
    if price is None:
        return None, []
    lines = [_line(f"{g['name']} hire, {DURATIONS[duration].lower()}", count, price)]
    return total(lines), lines


def hire_durations(g: dict) -> List[str]:
    return [k for k in DURATIONS if g["prices"].get(k) is not None or (k == "7d" and g.get("extra_day_cents") is not None)]


def price_list_text(tenant_id: str, st: Optional[dict] = None) -> str:
    """The business's lessons and hire prices, exactly as set, for a "how much" question."""
    st = st or settings(tenant_id)
    out: List[str] = []
    for ls in st["lessons"]:
        out.append(f"*{ls['name']}*")
        for t in ls["group"]:
            who = f"{t['min']} " + ("person" if t["min"] == 1 else "people") if t["max"] == t["min"] else \
                (f"{t['min']}+ people" if t["max"] is None else f"{t['min']}–{t['max']} people")
            each = " each" if (t["max"] or 2) > 1 else ""
            pkg = f", {ls['package_sessions']} lessons {rand(t['package_cents'])}{each}" if t.get("package_cents") is not None else ""
            out.append(f"• Group, {who}: {rand(t['single_cents'])}{each}{pkg}")
        if ls.get("private"):
            p = ls["private"]
            pkg = f", {ls['package_sessions']} lessons {rand(p['package_cents'])}" if p.get("package_cents") is not None else ""
            out.append(f"• Private 1-on-1: {rand(p['single_cents'])}{pkg}")
        for e in ls.get("extras") or []:
            out.append(f"• {e['name']}: {rand(e['cents'])} per person per lesson")
    if st["hire"]:
        out.append("*Hire*")
        for g in st["hire"]:
            out.append(f"• {g['name']}: " + ", ".join(f"{DURATIONS[k].lower()} {rand(v)}" for k, v in g["prices"].items()))
    return "\n".join(out)


# ── requests ──────────────────────────────────────────────────────────────────────────────────
def _phone(raw: Any, required: bool = True) -> Optional[str]:
    digits = re.sub(r"[^\d]", "", str(raw or ""))
    if not digits and not required:
        return None
    if not 9 <= len(digits) <= 15:
        raise LessonError("Enter a cellphone number we can confirm on.")
    return "27" + digits[1:] if digits.startswith("0") and len(digits) == 10 else digits


def _when(raw: Any) -> datetime:
    return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))


def when_text(starts_at: Any) -> str:
    s = _when(starts_at).astimezone(SAST)
    return f"{s.strftime('%a')} {s.day} {s.strftime('%b')} at {s.strftime('%H:%M')}"


def day_text(raw: Any) -> str:
    d = date.fromisoformat(str(raw)[:10])
    return f"{d.strftime('%a')} {d.day} {d.strftime('%b')}"


def summary(r: dict) -> str:
    """One line a person can check: what was asked for."""
    if r["kind"] == "hire":
        return f"{r['party_size']} × {r['item_name']} for {DURATIONS.get(r.get('duration') or '', r.get('duration') or '').lower()}"
    bits = [r["item_name"], "private" if r.get("format") == "private" else "group",
            f"{r['party_size']} " + ("person" if r["party_size"] == 1 else "people")]
    if int(r.get("sessions") or 1) > 1:
        bits.append(f"{r['sessions']}-lesson package")
    return ", ".join(bits)


def _out(r: dict) -> dict:
    owed = r.get("price_cents") if r.get("price_cents") is not None else r.get("quote_cents")
    return {**r, "summary": summary(r), "wanted": f"{day_text(r['wanted_date'])}, {PARTS.get(r.get('wanted_part') or 'any', 'Any time').lower()}",
            "when": when_text(r["starts_at"]) if r.get("starts_at") else None,
            "level_text": LEVELS.get(r.get("level") or ""), "amount_cents": owed,
            "guest_coach": bool(r.get("guest_coach_id")),              # a coach from another school (coach_pool)
            "has_account": bool(r.get("customer_user_id")),          # a signed-in customer: their profile can be read
            "amount": rand(owed) if owed is not None else None}


def _get(tenant_id: str, request_id: str) -> dict:
    rows = (_db().table("commerce_lesson_requests").select("*").eq("tenant_id", tenant_id).eq("id", request_id)
            .limit(1).execute().data or [])
    if not rows:
        raise LessonError("That request wasn't found.", 404)
    return rows[0]


async def request(tenant_id: str, *, kind: str, item_id: Any, wanted_date: Any, name: Any, phone: Any,
                  people: Any = 1, fmt: str = "group", package: bool = False, level: str = "",
                  extras: Optional[Dict[str, int]] = None, duration: str = "", wanted_part: str = "any",
                  notes: Any = "", channel: str = "whatsapp", tell_team: bool = True,
                  customer_user_id: Optional[str] = None) -> dict:
    """Keep a request for a lesson or for gear, quoted from the price list. Nothing is booked.
    `customer_user_id`: the customer's own login when they made it signed in (migration 306)."""
    from vula.api import tenants
    st = settings(tenant_id)
    if not st["enabled"] or not tenants.is_active(tenant_id):
        raise LessonError("This business isn't taking requests here right now.", 409)
    who = _text(name, 80)
    if len(who) < 2:
        raise LessonError("Enter the name for the request.")
    number = _phone(phone, required=channel != "dashboard")
    try:
        day = date.fromisoformat(str(wanted_date)[:10])
    except ValueError as exc:
        raise LessonError("Choose a day.") from exc
    today = _now().astimezone(SAST).date()
    if not today <= day <= today + timedelta(days=365):
        raise LessonError("Choose a day from today onwards.")
    if wanted_part not in PARTS:
        wanted_part = "any"
    item_id = str(item_id or "")
    row: Dict[str, Any] = {"tenant_id": tenant_id, "kind": kind, "item_id": item_id, "wanted_date": day.isoformat(),
                           "wanted_part": wanted_part, "customer_name": who, "customer_phone": number,
                           "notes": _text(notes, 300) or None, "channel": channel, "status": "requested"}
    if kind == "lesson":
        cents, lines, sessions = quote_lesson(st, item_id, fmt, people, bool(package), extras)
        ls = lesson(st, item_id)
        chosen = [{"id": e["id"], "name": e["name"], "quantity": int((extras or {}).get(e["id"]) or 0), "cents": e["cents"]}
                  for e in ls.get("extras") or [] if int((extras or {}).get(e["id"]) or 0)]
        row.update(item_name=ls["name"], level=level if level in LEVELS else None, format=fmt, sessions=sessions,
                   party_size=int(people), extras=chosen)
    elif kind == "hire":
        cents, lines = quote_hire(st, item_id, duration, people)
        row.update(item_name=gear(st, item_id)["name"], duration=duration, party_size=int(people), sessions=1, extras=[])
    else:
        raise LessonError("Choose a lesson or gear to hire.")
    row.update(quote_cents=cents, quote_lines=lines,
               ref="L-" + "".join(secrets.choice(_REF_ALPHABET) for _ in range(5)))
    if customer_user_id:                             # only then: the column is migration 306's
        row["customer_user_id"] = customer_user_id
    try:
        _db().table("commerce_lesson_requests").insert(row).execute()
    except Exception as exc:  # noqa: BLE001
        if "customer_user_id" not in row:
            raise
        # a database without migration 306 yet: the request still goes in, just not tied to the login
        log.warning("lesson request kept without its login for %s (migration 306?): %s", tenant_id, exc)
        row.pop("customer_user_id")
        _db().table("commerce_lesson_requests").insert(row).execute()
    rows = (_db().table("commerce_lesson_requests").select("*").eq("tenant_id", tenant_id).eq("ref", row["ref"])
            .limit(1).execute().data or [])
    if not rows:
        raise LessonError("The request didn't save. Please try again.", 500)
    r = _out(rows[0])
    if tell_team:
        try:
            from vula.integrations.notify import notify_team
            price = f" Quote {r['amount']}." if r["amount"] else " No listed price: set one when you confirm."
            await notify_team(tenant_id, "lesson_request",
                              f"🏄 New request ({r['ref']}): {r['customer_name']} — {r['summary']}, {r['wanted']}."
                              f"{price} Confirm the time and coach in Lessons.",
                              idem_key=f"lesson_request:{r['id']}")
        except Exception as exc:  # noqa: BLE001
            log.warning("new lesson request not sent to the team (%s): %s", tenant_id, exc)
    return r


def list_requests(tenant_id: str, status: str = "", since: str = "") -> List[dict]:
    from vula.db_pages import all_rows

    def query():
        q = _db().table("commerce_lesson_requests").select("*").eq("tenant_id", tenant_id)
        if status:
            q = q.eq("status", status)
        if since:
            q = q.gte("wanted_date", since)
        return q
    rows = all_rows(query, cap=5000)
    rows.sort(key=lambda r: (r.get("starts_at") or f"{r['wanted_date']}T99", r.get("created_at") or ""))
    return [_out(r) for r in rows]


def coaches(tenant_id: str) -> List[dict]:
    """The people a lesson can be given to: the active team, names only."""
    try:
        from vula import team_index
        return sorted(({"id": str(m.get("id")), "name": m.get("name") or "", "role": m.get("role") or ""}
                       for m in team_index.active_members(tenant_id) if m.get("id") and m.get("name")
                       and (m.get("role") or "") != "accountant"), key=lambda m: m["name"].lower())
    except Exception as exc:  # noqa: BLE001
        log.warning("team unreadable for %s: %s", tenant_id, exc)
        return []


async def _tell_customer(tenant_id: str, r: dict, text: str, news: str) -> bool:
    """Free text inside the 24-hour window, else the approved order-update template. Never raises."""
    phone = r.get("customer_phone")
    if not phone:
        return False
    try:
        from vula.api.whatsapp import _send_reply, send_template
        from vula.commerce.job_config import template_name
        if await _send_reply(phone, text, tenant_id=tenant_id):
            return True
        return bool(await send_template(tenant_id, phone, template_name(tenant_id, "order_update"),
                                        [r.get("customer_name") or "there", r["ref"], news]))
    except Exception as exc:  # noqa: BLE001
        log.warning("lesson message %s for %s failed: %s", r.get("ref"), tenant_id, exc)
        return False


async def confirm(tenant_id: str, request_id: str, *, starts_at: Any, coach_id: Any = "", price_cents: Any = None,
                  invoice: bool = False, by: str = "", allow_clash: bool = False) -> dict:
    """A person at the business gives the request its time (and coach, and price where none was
    listed). One conditional write, read back; then the customer is told."""
    from vula.api import tenants
    cur = _get(tenant_id, request_id)
    if cur["status"] not in OPEN:
        raise LessonError(f"This request is already {cur['status'].replace('_', ' ')}.", 409)
    try:
        start = _when(starts_at)
    except (ValueError, TypeError) as exc:
        raise LessonError("Choose the day and time.") from exc
    if start.tzinfo is None:
        start = start.replace(tzinfo=SAST)
    if start < _now() - timedelta(minutes=5):
        raise LessonError("That time has already passed.")
    warning = ""
    patch: Dict[str, Any] = {"status": "confirmed", "starts_at": start.astimezone(timezone.utc).isoformat(),
                             "confirmed_by": by or None, "confirmed_at": _now().isoformat(),
                             "updated_at": _now().isoformat(), "reminded_at": None, "conditions_at": None}
    guest = cur.get("guest_coach_id")                 # a coach from another school accepted this lesson
    if guest and not coach_id:
        from vula.commerce import coach_pool
        off = coach_pool.accepted(tenant_id, request_id)
        if off and coach_pool._start_of(off["starts_at"]) != start:
            raise LessonError(f"{(cur.get('coach_name') or 'The guest coach').split(' ')[0]} accepted for "
                              f"{when_text(off['starts_at'])}. Keep that time, or release them first.", 409)
    if coach_id:
        who = next((c for c in coaches(tenant_id) if c["id"] == str(coach_id)), None)
        if not who:
            raise LessonError("That person isn't on the team any more.")
        if guest:                                     # the school's own coach takes it after all
            from vula.commerce import coach_pool
            coach_pool.release(tenant_id, request_id)
        patch.update(coach_id=who["id"], coach_name=who["name"])
        if cur["kind"] == "lesson":                   # the coach's calendar: not two things at once, not too many people
            from vula.commerce import lesson_calendar
            hit = lesson_calendar.check(tenant_id, who["id"], start, cur.get("item_id") or "", int(cur.get("party_size") or 1), request_id)
            if (hit["clash"] or hit["full"]) and not allow_clash:
                raise LessonError(f"{hit['clash'] or hit['full']} Choose another time or coach, or confirm anyway.", 409)
            warning = hit["outside"]
    if price_cents is not None:
        patch["price_cents"] = _cents(price_cents, "The price")
    owed = patch.get("price_cents", cur.get("price_cents") if cur.get("price_cents") is not None else cur.get("quote_cents"))
    if owed is None:
        raise LessonError("There is no listed price for this request. Enter the price.")
    done = (_db().table("commerce_lesson_requests").update(patch).eq("tenant_id", tenant_id).eq("id", request_id)
            .in_("status", list(OPEN)).execute().data or [])
    back = _get(tenant_id, request_id)
    if not done or back["status"] != "confirmed" or back.get("starts_at") is None:
        raise LessonError("The confirmation didn't save. Nothing changed.", 500)
    if invoice and not back.get("invoice_id"):
        back = await _draft_invoice(tenant_id, back, int(owed))
    r = _out(back)
    st = settings(tenant_id)
    lines = [f"✅ Confirmed: {r['summary']} on {r['when']}."]
    if r.get("coach_name") and r["kind"] == "lesson":
        lines.append(f"Your coach is {r['coach_name'].split(' ')[0]}.")
    lines.append(f"Total {r['amount']}." + (f" {st['pay_note']}" if st.get("pay_note") else ""))
    spot = st.get("spot")
    if spot:                                          # where to meet, with a map link the phone opens
        lines.append(f"We meet at {spot['name']}: https://maps.google.com/?q={spot['lat']},{spot['lng']}")
    if st.get("form_url"):
        lines.append(f"Please complete our indemnity form before you come: {st['form_url']}")
    lines.append(f"Reference {r['ref']}. To cancel, reply *cancel my lesson*.\n\n— {tenants.display_name(tenant_id)}")
    r["customer_told"] = await _tell_customer(
        tenant_id, r, "\n".join(lines), f"Your {r['item_name'].lower()} is confirmed for {r['when']}. Total {r['amount']}.")
    r["warning"] = warning                            # the coach is away or outside their hours: said, never refused
    return r


async def _draft_invoice(tenant_id: str, r: dict, owed: int) -> dict:
    """A draft invoice of the request's lines. When the agreed price differs from the quote the
    invoice is one line at the agreed price. A failure leaves the confirmation standing."""
    try:
        from vula.commerce import service
        lines = r.get("quote_lines") or []
        if not lines or total(lines) != owed:
            lines = [_line(summary(r), 1, owed)]
        inv = await service.create_invoice(tenant_id, {
            "customer_name": r["customer_name"], "customer_phone": r.get("customer_phone"), "line_items": lines,
            "prices_include_vat": True, "notes": f"Request {r['ref']}", "status": "draft"})
        _db().table("commerce_lesson_requests").update({"invoice_id": inv["id"]}).eq("tenant_id", tenant_id) \
            .eq("id", r["id"]).execute()
        return _get(tenant_id, r["id"])
    except Exception as exc:  # noqa: BLE001
        log.warning("draft invoice for request %s (%s) failed: %s", r.get("ref"), tenant_id, exc)
        return r


async def decline(tenant_id: str, request_id: str, reason: Any, by: str = "") -> dict:
    why = _text(reason, 200)
    if len(why) < 3:
        raise LessonError("Say why, in a few words. The customer is told.")
    done = (_db().table("commerce_lesson_requests")
            .update({"status": "declined", "decline_reason": why, "confirmed_by": by or None,
                     "updated_at": _now().isoformat()})
            .eq("tenant_id", tenant_id).eq("id", request_id).eq("status", "requested").execute().data or [])
    back = _get(tenant_id, request_id)
    if not done or back["status"] != "declined":
        raise LessonError("Only a request that is still waiting can be declined.", 409)
    from vula.api import tenants
    r = _out(back)
    r["customer_told"] = await _tell_customer(
        tenant_id, r, f"Sorry, we can't do {r['summary']} on {day_text(r['wanted_date'])}: {why}\n\n"
                      f"Reply *lesson* to ask for another day.\n\n— {tenants.display_name(tenant_id)}",
        f"Sorry, we can't do your request for {day_text(r['wanted_date'])}: {why}")
    return r


def set_status(tenant_id: str, request_id: str, status: str) -> dict:
    """completed | no_show | cancelled — from a confirmed request (cancelled also from a waiting one)."""
    allowed = {"completed": ("confirmed",), "no_show": ("confirmed",), "cancelled": OPEN}
    if status not in allowed:
        raise LessonError("Choose completed, no-show or cancelled.")
    done = (_db().table("commerce_lesson_requests").update({"status": status, "updated_at": _now().isoformat()})
            .eq("tenant_id", tenant_id).eq("id", request_id).in_("status", list(allowed[status])).execute().data or [])
    back = _get(tenant_id, request_id)
    if not done or back["status"] != status:
        raise LessonError(f"A request that is {back['status'].replace('_', ' ')} can't be marked {status.replace('_', ' ')}.", 409)
    return _out(back)


def upcoming_for(tenant_id: str, phone: str) -> List[dict]:
    tail = re.sub(r"[^0-9]", "", phone or "")[-9:]
    if len(tail) < 9:
        return []
    today = _now().astimezone(SAST).date().isoformat()
    rows = (_db().table("commerce_lesson_requests").select("*").eq("tenant_id", tenant_id)
            .like("customer_phone", f"%{tail}").in_("status", list(OPEN)).gte("wanted_date", today)
            .order("wanted_date").limit(10).execute().data or [])
    return [_out(r) for r in rows]


def known_name(tenant_id: str, phone: str) -> str:
    tail = re.sub(r"[^0-9]", "", phone or "")[-9:]
    if len(tail) < 9:
        return ""
    rows = (_db().table("commerce_lesson_requests").select("customer_name,customer_phone").eq("tenant_id", tenant_id)
            .like("customer_phone", f"%{tail}").order("created_at", desc=True).limit(1).execute().data or [])
    return (rows[0].get("customer_name") or "") if rows else ""


async def cancel_own(tenant_id: str, phone: str, request_id: str) -> Optional[dict]:
    """A customer cancels a request on their own number. The team is told when it was confirmed."""
    mine = next((r for r in upcoming_for(tenant_id, phone) if r["id"] == request_id), None)
    if not mine:
        return None
    try:
        out = set_status(tenant_id, request_id, "cancelled")
    except LessonError:
        return None
    if mine["status"] == "confirmed":
        try:
            from vula.integrations.notify import notify_team
            await notify_team(tenant_id, "lesson_request",
                              f"❌ {mine['customer_name']} cancelled {mine['summary']} on {mine['when']} ({mine['ref']}).",
                              idem_key=f"lesson_cancelled:{mine['id']}")
        except Exception as exc:  # noqa: BLE001
            log.warning("cancellation not sent to the team (%s): %s", tenant_id, exc)
    return out


# ── the day before: remind the customer, check the conditions ─────────────────────────────────
async def send_reminders(now: Optional[datetime] = None) -> int:
    """A confirmed lesson or hire 90 minutes to 20 hours away hears once, in the daytime
    (07:00–20:00 South African time). The row is marked first, so never twice."""
    now = now or _now()
    if not 7 <= now.astimezone(SAST).hour < 20:
        return 0
    rows = (_db().table("commerce_lesson_requests").select("*").eq("status", "confirmed")
            .gte("starts_at", (now + timedelta(minutes=90)).isoformat())
            .lte("starts_at", (now + timedelta(hours=20)).isoformat()).is_("reminded_at", "null").limit(500)
            .execute().data or [])
    sent = 0
    for b in rows:
        if not b.get("customer_phone"):
            continue
        res = (_db().table("commerce_lesson_requests").update({"reminded_at": now.isoformat()})
               .eq("id", b["id"]).eq("tenant_id", b["tenant_id"]).is_("reminded_at", "null").execute())
        if not res.data:
            continue
        from vula.api import tenants
        r = _out(b)
        news = f"Your {r['item_name'].lower()} with {tenants.display_name(b['tenant_id'])} is on {r['when']}."
        if await _tell_customer(b["tenant_id"], r, f"👋 A reminder: {news} Reference {r['ref']}.", news):
            sent += 1
    return sent


async def check_conditions(now: Optional[datetime] = None) -> int:
    """Between 16:00 and 20:00, tomorrow's confirmed lessons at a spot with a position get the
    forecast for their hour, and the team hears once per business. Each row is marked first."""
    from vula.commerce import conditions
    now = now or _now()
    local = now.astimezone(SAST)
    if not 16 <= local.hour < 20:
        return 0
    tomorrow = local.date() + timedelta(days=1)
    lo = datetime.combine(tomorrow, datetime.min.time(), SAST).astimezone(timezone.utc)
    rows = (_db().table("commerce_lesson_requests").select("*").eq("status", "confirmed").eq("kind", "lesson")
            .gte("starts_at", lo.isoformat()).lt("starts_at", (lo + timedelta(days=1)).isoformat())
            .is_("conditions_at", "null").limit(500).execute().data or [])
    by_tenant: Dict[str, List[dict]] = {}
    for b in rows:
        by_tenant.setdefault(b["tenant_id"], []).append(b)
    told = 0
    for tenant_id, lessons_ in by_tenant.items():
        st = settings(tenant_id)
        spot = st.get("spot")
        if not spot:
            continue
        try:
            hours = conditions.with_shore(await conditions.forecast(spot["lat"], spot["lng"], tomorrow), spot.get("faces"))
        except Exception as exc:  # noqa: BLE001
            log.warning("conditions for %s unavailable: %s", tenant_id, exc)
            continue
        out: List[str] = []
        for b in sorted(lessons_, key=lambda x: x["starts_at"]):
            at = _when(b["starts_at"]).astimezone(SAST)
            hour = hours.get(at.hour)
            if not hour:
                continue
            sport = (lesson(st, b["item_id"]) or {}).get("sport") or "other"
            verdict = conditions.rate(sport, b.get("level") or "", hour)
            res = (_db().table("commerce_lesson_requests")
                   .update({"conditions_at": now.isoformat(), "conditions": {**hour, **verdict}})
                   .eq("id", b["id"]).eq("tenant_id", tenant_id).is_("conditions_at", "null").execute())
            if not res.data:
                continue
            who = f" ({b['coach_name'].split(' ')[0]})" if b.get("coach_name") else ""
            out.append(f"{conditions.ICON[verdict['rating']]} {at.strftime('%H:%M')} {b['customer_name']}, {summary(b)}{who}: "
                       f"{conditions.describe(hour)}" + (f" — {'; '.join(verdict['reasons'])}" if verdict["reasons"] else ""))
        if not out:
            continue
        try:
            from vula.integrations.notify import notify_team
            await notify_team(tenant_id, "lesson_conditions",
                              f"🌊 Tomorrow at {spot['name']} ({day_text(tomorrow.isoformat())}), forecast for each lesson:\n"
                              + "\n".join(out) + "\n\nThe forecast is a guide. You decide whether a lesson runs.",
                              idem_key=f"lesson_conditions:{tenant_id}:{tomorrow.isoformat()}:{len(out)}")
            told += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("conditions not sent to the team (%s): %s", tenant_id, exc)
    return told


async def run_forever(interval_s: int = 1800) -> None:
    import asyncio
    await asyncio.sleep(170)
    while True:
        try:
            n, c = await send_reminders(), await check_conditions()
            if n or c:
                log.info("lessons: %d reminders, %d conditions checks", n, c)
        except Exception as exc:  # noqa: BLE001
            log.warning("lesson reminders run failed: %s", exc)
        await asyncio.sleep(interval_s)


# ── the customer app (vula/api/lessons.py `public`, the dashboard's /flow/ page) ───────────────
def _token_secret() -> str:
    from vula.commerce import order_slip
    return "lesson-request:" + order_slip._secret()


def token_for(request_id: str) -> str:
    """What the customer's phone keeps for a request it made: only this opens or cancels it."""
    from vula.tap.receipt import make_token
    return make_token(_token_secret(), str(request_id), 0)


def id_from_token(token: Any) -> Optional[str]:
    from vula.tap.receipt import read_token
    parsed = read_token(_token_secret(), str(token or "")[:300])
    return parsed[0] if parsed else None


def public_settings(st: dict) -> dict:
    """What a customer may see of the settings: the lessons, the gear, their prices, the spot.
    (Coach profiles are added by the route, which knows the business.)"""
    return {"lessons": st["lessons"], "hire": [{**g, "durations": hire_durations(g)} for g in st["hire"]],
            "spot": {"name": st["spot"]["name"]} if st.get("spot") else None,
            "form_url": st.get("form_url"), "pay_note": st.get("pay_note"), "note": st.get("note"),
            "levels": LEVELS, "parts": PARTS, "durations": DURATIONS}


def _for_customer(r: dict) -> dict:
    """A request as its own customer sees it — never who confirmed it or the team's note."""
    o = _out(r)
    return {"token": token_for(r["id"]), "ref": o["ref"], "kind": o["kind"], "item_name": o["item_name"],
            "summary": o["summary"], "wanted": o["wanted"], "wanted_date": o["wanted_date"], "when": o["when"],
            "starts_at": o.get("starts_at"), "status": o["status"], "amount_cents": o["amount_cents"],
            "amount": o["amount"], "quote_lines": o.get("quote_lines") or [],
            "coach": (o.get("coach_name") or "").split(" ")[0] or None,
            "decline_reason": o.get("decline_reason") if o["status"] == "declined" else None}


def for_customer(r: dict) -> dict:
    return _for_customer(r)


def _with_rating(rows: List[dict]) -> List[dict]:
    """The customer's view of their own rows, each saying whether it can still be rated: a lesson
    the school marked done that has no rating yet."""
    from vula.commerce import lesson_ratings
    rated = lesson_ratings.rated_ids([str(r["id"]) for r in rows if r.get("status") == "completed"])
    return [{**_for_customer(r), "rated": str(r["id"]) in rated,
             "can_rate": r.get("kind") == "lesson" and r.get("status") == "completed" and str(r["id"]) not in rated}
            for r in rows]


def _sport_of(cache: Dict[str, dict]):
    """The sport of a request's lesson, from its own school's settings (read once a school)."""
    def find(r: dict) -> str:
        tid = r["tenant_id"]
        if tid not in cache:
            cache[tid] = settings(tid)
        return (lesson(cache[tid], r.get("item_id") or "") or {}).get("sport") or ""
    return find


def for_account(user_id: str) -> dict:
    """A signed-in customer's own requests, at every school, newest first — found by their login's
    id and nothing else. Each carries its signed token (they made it, so they may open and cancel
    it) and its school. `name` and `phone` are what they gave last, to fill the next form."""
    from vula.api import tenants
    rows = (_db().table("commerce_lesson_requests").select("*").eq("customer_user_id", user_id)
            .order("created_at", desc=True).limit(60).execute().data or [])
    from vula.commerce import lesson_progress
    names: Dict[str, str] = {}
    for r in rows:
        tid = r["tenant_id"]
        if tid not in names:
            names[tid] = tenants.display_name(tid) if tenants.is_active(tid) else ""
    shown = [r for r in rows if names[r["tenant_id"]]]      # a paused or removed school's requests aren't shown
    out = [{**c, "shop": r["tenant_id"].replace("_", "-"), "school": names[r["tenant_id"]]}
           for r, c in zip(shown, _with_rating(shown))]
    last = rows[0] if rows else {}
    return {"requests": out, "name": last.get("customer_name") or "", "phone": last.get("customer_phone") or "",
            "progress": lesson_progress.progress(shown, _sport_of({}))}      # across every school


def by_tokens(tenant_id: str, tokens: List[str]) -> List[dict]:
    """The requests these tokens open, for this business only; a token that opens nothing is left out."""
    ids = [i for i in dict.fromkeys(id_from_token(t) for t in tokens[:20]) if i]
    if not ids:
        return []
    rows = (_db().table("commerce_lesson_requests").select("*").eq("tenant_id", tenant_id).in_("id", ids)
            .limit(20).execute().data or [])
    rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return _with_rating(rows)


def progress_by_tokens(tenant_id: str, tokens: List[str]) -> dict:
    """The progress the requests these tokens open add up to, at this school (what a phone that
    isn't signed in can show: the lessons asked for on it)."""
    from vula.commerce import lesson_progress
    ids = [i for i in dict.fromkeys(id_from_token(t) for t in tokens[:20]) if i]
    rows = (_db().table("commerce_lesson_requests").select("*").eq("tenant_id", tenant_id).in_("id", ids)
            .limit(20).execute().data or []) if ids else []
    return lesson_progress.progress(rows, _sport_of({}))


async def cancel_by_token(tenant_id: str, token: Any) -> Optional[dict]:
    """The customer cancels the request their token opens. The team hears when it was confirmed."""
    rid = id_from_token(token)
    if not rid:
        return None
    try:
        cur = _get(tenant_id, rid)
        was = cur["status"]
        out = set_status(tenant_id, rid, "cancelled")
    except LessonError:
        return None
    if was == "confirmed":
        try:
            from vula.integrations.notify import notify_team
            await notify_team(tenant_id, "lesson_request",
                              f"❌ {out['customer_name']} cancelled {out['summary']} on {when_text(cur['starts_at'])} ({out['ref']}).",
                              idem_key=f"lesson_cancelled:{rid}")
        except Exception as exc:  # noqa: BLE001
            log.warning("cancellation not sent to the team (%s): %s", tenant_id, exc)
    return _for_customer(_get(tenant_id, rid))


# ── the coach's side: their own lessons, and their share ───────────────────────────────────────
def share_of(amount_cents: Any, share_bp: Any) -> Optional[int]:
    """The coach's part of an amount, to the cent, half up — None when no share is set."""
    if share_bp is None or amount_cents is None:
        return None
    return (int(amount_cents) * int(share_bp) + 5000) // 10000


def member_for(tenant_id: str, who: str) -> Optional[dict]:
    """The team member behind a WhatsApp number or login: {"id", "name"}, or None."""
    try:
        from vula import team_index
        digits = re.sub(r"\D", "", who or "")
        for m in team_index.active_members(tenant_id):
            mine = re.sub(r"\D", "", str(m.get("whatsapp") or ""))
            if (len(digits) >= 9 and mine[-9:] == digits[-9:]) or (who and "@" in who and who.lower() == str(m.get("email") or "").lower()):
                return {"id": str(m.get("id") or ""), "name": m.get("name") or ""}
    except Exception as exc:  # noqa: BLE001
        log.debug("member not found for %s: %s", tenant_id, exc)
    return None


def _month_bounds(month: str) -> Tuple[str, str]:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", str(month or "")):
        raise LessonError("Choose a month (YYYY-MM).")
    y, m = int(month[:4]), int(month[5:])
    start = datetime(y, m, 1, tzinfo=SAST)
    end = datetime(y + (m == 12), 1 if m == 12 else m + 1, 1, tzinfo=SAST)
    return start.astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat()


def coach_month(tenant_id: str, month: str, only_coach: str = "") -> dict:
    """Lessons taught in a month (South African time), per coach: how many were done, what they
    were charged at, and the coach's share where the business has set one. A lesson counts once it
    is marked done; what is still to come is counted apart. Hire is nobody's lesson."""
    from vula.db_pages import all_rows
    lo, hi = _month_bounds(month)
    bp = settings(tenant_id).get("coach_share_bp")

    def query():
        q = (_db().table("commerce_lesson_requests").select("*").eq("tenant_id", tenant_id).eq("kind", "lesson")
             .in_("status", ["confirmed", "completed"]).gte("starts_at", lo).lt("starts_at", hi))
        return q.eq("coach_id", only_coach) if only_coach else q
    by: Dict[str, dict] = {}
    fees = 0
    for r in all_rows(query, cap=5000):
        gid = r.get("guest_coach_id") if not r.get("coach_id") else None      # a coach from another school
        key = r.get("coach_id") or (f"guest:{r.get('guest_coach_tenant')}:{gid}" if gid else "")
        c = by.setdefault(key, {"coach_id": (key or None) if not gid else None, "guest": bool(gid),
                                "name": (r.get("coach_name") or "No coach set") if (key or not gid) else "Guest coach",
                                "done": 0, "to_come": 0, "people": 0, "value_cents": 0,
                                "share_cents": 0 if bp is not None else None, "fee_cents": 0 if gid else None})
        if r["status"] != "completed":
            c["to_come"] += 1
            continue
        owed = r.get("price_cents") if r.get("price_cents") is not None else r.get("quote_cents")
        c["done"] += 1
        c["people"] += int(r.get("party_size") or 0)
        c["value_cents"] += int(owed or 0)
        if bp is not None and key:                       # each lesson's share to the cent, then added up
            c["share_cents"] += share_of(owed or 0, bp)
        if gid:                                          # the partner's fee on a lesson a guest coach taught
            fee = share_of(owed or 0, r.get("pool_fee_bp")) or 0
            c["fee_cents"] += fee
            fees += fee
    rows = sorted(by.values(), key=lambda c: (2 if c["guest"] else 3 if c["coach_id"] is None else 1, c["name"].lower()))
    return {"month": month, "coach_share_bp": bp, "coaches": rows, "pool_fee_cents": fees,
            "total": {"done": sum(c["done"] for c in rows), "value_cents": sum(c["value_cents"] for c in rows),
                      "share_cents": sum(c["share_cents"] or 0 for c in rows) if bp is not None else None}}


def mine(tenant_id: str, who: str, month: str) -> dict:
    """The signed-in coach's own lessons still to come, and their own month — nobody else's."""
    me = member_for(tenant_id, who)
    if not me or not me["id"]:
        return {"me": None, "upcoming": [], "month": None}
    now = _now().isoformat()
    rows = (_db().table("commerce_lesson_requests").select("*").eq("tenant_id", tenant_id).eq("coach_id", me["id"])
            .eq("status", "confirmed").gte("starts_at", now).order("starts_at").limit(50).execute().data or [])
    figures = coach_month(tenant_id, month, only_coach=me["id"])
    own = next((c for c in figures["coaches"] if c["coach_id"] == me["id"]), None)
    return {"me": me, "upcoming": [_out(r) for r in rows], "month": own, "coach_share_bp": figures["coach_share_bp"]}
