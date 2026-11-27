"""A school with no free coach asks a coach from another school (25 Nov 2026, migration 310).

Everything here is inside ONE partner (FlowCrew): the schools that came through it, and their
coaches who said — themselves, from their own login — that other schools may ask them. A coach's
home school has no switch for it.

  the coach     `set_open` (their own choice), `mine` (what they were asked, what they took, what it
                earned), `answer` (accept, decline, or "I can't make it" after accepting)
  the school    `candidates` (pool coaches who are free at that time, nearest first), `ask`,
                `release`, `for_request`
  the partner   `partner_month` (its fee, school by school)

A coach asked sees the school, the place, the lesson, the time, how many people and their level,
and what they would earn — never the customer's name or number. The first coach to accept gets
the lesson (one conditional write on the request); the school then confirms it as usual, at the
time the coach accepted for.

Money is a report, like the coach's share: the guest coach earns the asking school's own coach
share of the lesson's price, and the partner's fee is `fee_bp` of that price (kept on the offer as
it was when the school asked). Nothing is paid out or posted to the books from here.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

FEE_BP = 1000            # the partner's fee where it names none: 10% of the lesson's price
MAX_KM = 60              # a coach whose home beach is further than this isn't offered
MAX_ASKED = 10           # coaches asked for one lesson at a time
LIVE = ("offered", "accepted")
# The approved template that tells a coach about a lesson when they haven't written to their
# school's number in the last 24 hours (a plain message is refused then). In the standard set for a
# business with lessons (vula/commerce/wa_templates.py): first name, the school, the news on one line.
COACH_TEMPLATE = "vula_coach_request"
COACH_TEMPLATE_BODY = ("Hi {{1}}, a message from {{2}} about teaching a lesson: {{3}} "
                       "Open Lessons, Calendar, Other schools in your dashboard to see it.")
COACH_TEMPLATE_EXAMPLE = ["Ayanda", "Atlantic Surf Co.", "Can you teach a surf lesson on Sat 28 Nov at 09:00 at Big Bay, 3 people? You'd earn R735."]
TEMPLATE_ROOM = 190      # send_template cuts a value at 200 characters


def _L():
    from vula.commerce import lessons
    return lessons


def _err(message: str, status: int = 400):
    return _L().LessonError(message, status)


def _db():
    return _L()._db()


def partner_id(tenant_id: str) -> Optional[str]:
    from vula.api import tenants
    try:
        p = tenants.partner_of(tenants.get_config(tenant_id))
    except Exception:  # noqa: BLE001 — a business that can't be read is in nobody's pool
        p = None
    return p["id"] if p else None


def fee_bp(tenant_id: str) -> Optional[int]:
    """The partner's fee for a lesson taught by a guest coach, or None outside a partner."""
    from vula.commerce import partner_fees             # the operator can change it; a school can have its own
    return partner_fees.rate_for(tenant_id)


def _need_partner(tenant_id: str) -> str:
    pid = partner_id(tenant_id)
    if not pid:
        raise _err("Asking coaches from other schools isn't available for this business.", 404)
    return pid


# ── telling the coach on WhatsApp ──────────────────────────────────────────────────────────────
def _dashboard(tenant_id: str) -> str:
    from vula.api import tenants
    pid = partner_id(tenant_id)
    domain = (tenants.PARTNERS.get(pid) or {}).get("domain") if pid else None
    return f"https://app.{domain}" if domain else ""


async def tell_coach(coach_tenant: str, member_id: str, school: str, news: str, key: str) -> bool:
    """One WhatsApp message to a coach about a lesson at another school, from their OWN school's
    number — the thread a team member has with it is private to them. A plain message inside the
    24-hour window, else the approved template with the same news on one line. Once per `key`.
    False when it couldn't be sent (no number on record, no line, template not approved yet):
    the coach still sees it in the dashboard. Never raises."""
    import re
    try:
        from vula import team_index
        from vula.api.whatsapp import _send_reply, send_template
        m = next((x for x in team_index.active_members(coach_tenant) if str(x.get("id")) == member_id), None)
        phone = re.sub(r"\D", "", str((m or {}).get("whatsapp") or ""))
        if len(phone) < 9:
            return False
        first = (m.get("name") or "there").split(" ")[0]
        link = _dashboard(coach_tenant)
        text = f"{school}: {news}" + (f"\n\nSee it in Lessons › Calendar › Other schools: {link}" if link else "")
        if await _send_reply(phone, text, tenant_id=coach_tenant, idem_key=key):
            return True
        line = re.sub(r"\s+", " ", news).strip()[:TEMPLATE_ROOM]
        return bool(await send_template(coach_tenant, phone, COACH_TEMPLATE, [first, school, line]))
    except Exception as exc:  # noqa: BLE001 — the message is an extra; the offer stands without it
        log.warning("coach not told on WhatsApp (%s): %s", coach_tenant, exc)
        return False


def _ask_words(o: dict) -> str:
    """The offer in one line, from what the coach may know (`_for_coach`)."""
    L = _L()
    bits = [f"Can you teach a {str(o['lesson'] or 'lesson').lower()} on {o['when']}"]
    if o.get("spot"):
        bits.append(f" at {o['spot']['name']}")
    bits.append(f", {o['people']} {'person' if o['people'] == 1 else 'people'}")
    if o.get("level"):
        bits.append(f" ({o['level'].lower()})")
    bits.append("?")
    if o.get("pay_cents") is not None:
        bits.append(f" You'd earn {L.rand(o['pay_cents'])}.")
    return "".join(bits)


async def tell_guest(tenant_id: str, r: dict, what: str, offer: Optional[dict] = None) -> bool:
    """Tell the guest coach of a lesson what happened to it: `confirmed`, `released` (the school
    no longer needs them) or `cancelled` (the customer or the school called the lesson off)."""
    from vula.api import tenants
    L = _L()
    try:
        o = offer or accepted(tenant_id, str(r["id"]))
        if not o:
            return False
        lesson = str(r.get("item_name") or "lesson").lower()
        when = L.when_text(o["starts_at"])
        news = {"confirmed": f"Your {lesson} on {when} is confirmed. See you there.",
                "released": f"You are no longer needed for the {lesson} on {when}. It is off your calendar.",
                "cancelled": f"The {lesson} on {when} has been cancelled. It is off your calendar."}[what]
        return await tell_coach(o["coach_tenant_id"], o["coach_member_id"], tenants.display_name(tenant_id), news,
                                f"pool_{what}:{o['id']}")
    except Exception as exc:  # noqa: BLE001
        log.warning("guest coach not told (%s): %s", tenant_id, exc)
        return False


# ── the coach's own choice ─────────────────────────────────────────────────────────────────────
def is_open(tenant_id: str, member_id: str) -> bool:
    try:
        rows = (_db().table("flow_pool_coaches").select("open").eq("tenant_id", tenant_id).eq("member_id", member_id)
                .limit(1).execute().data or [])
    except Exception as exc:  # noqa: BLE001 — before the migration nobody is in the pool
        log.debug("pool unreadable: %s", exc)
        return False
    return bool(rows and rows[0].get("open") is True)


def set_open(tenant_id: str, member_id: str, value: Any) -> bool:
    """The coach says whether other schools may ask them. Read back."""
    L = _L()
    _need_partner(tenant_id)
    if member_id not in {c["id"] for c in L.coaches(tenant_id)}:
        raise _err("You aren't on this school's team.", 403)
    want = value is True
    row = {"open": want, "updated_at": L._now().isoformat()}
    try:
        t = _db().table("flow_pool_coaches")
        done = t.update(row).eq("tenant_id", tenant_id).eq("member_id", member_id).execute().data or []
        if not done:
            _db().table("flow_pool_coaches").insert({"tenant_id": tenant_id, "member_id": member_id, **row}).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("pool choice not saved for %s: %s", tenant_id, exc)
        raise _err("That didn't save. Please try again.", 500) from exc
    if is_open(tenant_id, member_id) is not want:
        raise _err("That didn't save. Please try again.", 500)
    return want


# ── who is free ────────────────────────────────────────────────────────────────────────────────
def _when(raw: Any) -> datetime:
    L = _L()
    try:
        start = L._when(raw)
    except (ValueError, TypeError) as exc:
        raise _err("Choose the day and time.") from exc
    if start.tzinfo is None:
        start = start.replace(tzinfo=L.SAST)
    if start < L._now() + timedelta(minutes=30):
        raise _err("Choose a time at least half an hour from now.")
    return start


def _start_of(raw: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _requests(ids: List[str]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    ids = sorted({str(i) for i in ids if i})
    for n in range(0, len(ids), 100):
        for r in _db().table("commerce_lesson_requests").select("*").in_("id", ids[n:n + 100]).execute().data or []:
            out[str(r["id"])] = r
    return out


def _taken(coach_tenant: str, member_id: str) -> List[dict]:
    """The coach's accepted lessons at other schools that still stand, each with its request."""
    L = _L()
    offers = (_db().table("flow_coach_offers").select("*").eq("coach_tenant_id", coach_tenant).eq("coach_member_id", member_id)
              .eq("status", "accepted").order("starts_at").limit(500).execute().data or [])
    reqs = _requests([o["request_id"] for o in offers])
    return [{**o, "request": reqs[str(o["request_id"])]} for o in offers
            if (reqs.get(str(o["request_id"])) or {}).get("status") in (*L.OPEN, "completed")]


def _free(coach_tenant: str, member_id: str, start: datetime, end: datetime) -> bool:
    """Nothing in the coach's own school's calendar then, no lesson taken elsewhere then, and
    inside the hours they gave (a coach who gave none can be asked at any time)."""
    from vula.commerce import lesson_calendar as cal
    L = _L()
    st = L.settings(coach_tenant)
    for r in cal._rows(coach_tenant, start - timedelta(days=1), start + timedelta(days=1), member_id):
        s = cal._start(r)
        if s and s < end and start < s + timedelta(minutes=cal._minutes(st, r)):
            return False
    for o in _taken(coach_tenant, member_id):
        s = _start_of(o["starts_at"])
        if s and o["request"]["status"] in L.OPEN and s < end and start < s + timedelta(minutes=int(o["minutes"])):
            return False
    desk, avail = cal._availability(coach_tenant)
    if desk is not None:
        try:
            if desk._fits(avail, member_id, start, end):
                return False
        except Exception as exc:  # noqa: BLE001
            log.debug("pool availability check failed: %s", exc)
    return True


def _open_request(tenant_id: str, request_id: str) -> dict:
    cur = _L()._get(tenant_id, request_id)
    if cur["kind"] != "lesson":
        raise _err("A guest coach is for a lesson.")
    if cur["status"] != "requested":
        raise _err("Ask a guest coach before the lesson is confirmed. To change a confirmed lesson, change it first.", 409)
    return cur


def candidates(tenant_id: str, request_id: str, starts_at: Any) -> dict:
    """Coaches of other schools of the same partner who said they may be asked, teach this sport,
    are within reach and are free then — nearest first — with what asking one costs."""
    from vula.api import tenants
    from vula.commerce import lesson_directory, lesson_ratings
    from vula.db_pages import all_rows
    L = _L()
    pid = _need_partner(tenant_id)
    cur = _open_request(tenant_id, request_id)
    st = L.settings(tenant_id)
    if st.get("coach_share_bp") is None:
        raise _err("Set the coach's share first (Lessons and prices), so a guest coach knows what they would earn.")
    start = _when(starts_at)
    item = L.lesson(st, cur.get("item_id") or "") or {}
    minutes = int(item.get("minutes") or 90)
    end = start + timedelta(minutes=minutes)
    sport = item.get("sport") or "other"
    here = st.get("spot") or None
    asked = {(o["coach_tenant_id"], o["coach_member_id"]): o["status"] for o in _offers(tenant_id, request_id)}
    rows = all_rows(lambda: _db().table("flow_pool_coaches").select("*").eq("open", True), order="tenant_id", cap=3000)
    out, homes = [], {}
    for row in rows:
        home, mid = row["tenant_id"], row["member_id"]
        if home == tenant_id or partner_id(home) != pid or not tenants.is_active(home):
            continue
        if home not in homes:
            hst = L.settings(home)
            homes[home] = {"st": hst, "team": {c["id"]: c["name"] for c in L.coaches(home)},
                           "stars": lesson_ratings.summary(home)["coaches"], "name": tenants.display_name(home)}
        h = homes[home]
        if mid not in h["team"]:
            continue
        prof = next((p for p in h["st"].get("coach_profiles") or [] if isinstance(p, dict) and p.get("member_id") == mid), {})
        if prof.get("sports") and sport not in prof["sports"]:
            continue
        there = h["st"].get("spot") or None
        km = round(lesson_directory.distance_km(here["lat"], here["lng"], there["lat"], there["lng"]), 1) if here and there else None
        if km is not None and km > MAX_KM:
            continue
        if not _free(home, mid, start, end):
            continue
        out.append({"key": f"{home}:{mid}", "name": h["team"][mid].split(" ")[0], "school": h["name"], "km": km,
                    "bio": prof.get("bio") or "", "photo_url": prof.get("photo_url"),
                    "levels": [L.LEVELS[k].split(" (")[0] for k in prof.get("levels") or [] if k in L.LEVELS],
                    "rating": h["stars"].get(mid), "asked": asked.get((home, mid))})
    out.sort(key=lambda c: (c["km"] is None, c["km"] or 0, c["name"].lower()))
    owed = cur.get("price_cents") if cur.get("price_cents") is not None else cur.get("quote_cents")
    fee = fee_bp(tenant_id)
    return {"coaches": out, "starts_at": start.isoformat(), "when": L.when_text(start.isoformat()), "minutes": minutes,
            "share_bp": st["coach_share_bp"], "fee_bp": fee, "price_cents": owed,
            "coach_cents": L.share_of(owed, st["coach_share_bp"]), "fee_cents": L.share_of(owed, fee)}


# ── the school asks ────────────────────────────────────────────────────────────────────────────
def _offers(tenant_id: str, request_id: str) -> List[dict]:
    try:
        return (_db().table("flow_coach_offers").select("*").eq("tenant_id", tenant_id).eq("request_id", request_id)
                .order("created_at").limit(200).execute().data or [])
    except Exception as exc:  # noqa: BLE001 — before the migration nobody has been asked
        log.debug("offers unreadable: %s", exc)
        return []


def for_request(tenant_id: str, request_id: str) -> List[dict]:
    """Who was asked for this lesson and what they said (the school's view)."""
    from vula.api import tenants
    L = _L()
    return [{"id": o["id"], "name": o["coach_name"].split(" ")[0], "school": tenants.display_name(o["coach_tenant_id"]),
             "status": o["status"], "starts_at": o["starts_at"], "when": L.when_text(o["starts_at"])}
            for o in _offers(tenant_id, request_id) if o["status"] != "withdrawn"]


def accepted(tenant_id: str, request_id: str) -> Optional[dict]:
    return next((o for o in _offers(tenant_id, request_id) if o["status"] == "accepted"), None)


async def ask(tenant_id: str, request_id: str, starts_at: Any, keys: Any, by: str = "") -> dict:
    """Ask the chosen coaches for this lesson at this time. Each must be one `candidates` offers
    right now. Asking at a new time withdraws what was asked at the old one."""
    L = _L()
    found = candidates(tenant_id, request_id, starts_at)
    cur = L._get(tenant_id, request_id)
    if cur.get("guest_coach_id"):
        raise _err(f"{(cur.get('coach_name') or 'A coach').split(' ')[0]} has already accepted. Release them to ask someone else.", 409)
    keys = [str(k) for k in keys] if isinstance(keys, list) else []
    if not keys or len(keys) > MAX_ASKED:
        raise _err(f"Choose 1 to {MAX_ASKED} coaches to ask.")
    free = {c["key"] for c in found["coaches"]}
    if any(k not in free for k in keys):
        raise _err("One of those coaches is no longer free then. Look again.", 409)
    start = found["starts_at"]
    now = L._now().isoformat()
    for o in _offers(tenant_id, request_id):
        if o["status"] == "offered" and _start_of(o["starts_at"]) != _start_of(start):
            _db().table("flow_coach_offers").update({"status": "withdrawn", "answered_at": now}).eq("id", o["id"]).eq("status", "offered").execute()
    from vula.api import tenants
    live = {(o["coach_tenant_id"], o["coach_member_id"]) for o in _offers(tenant_id, request_id) if o["status"] in LIVE}
    fresh = []
    for k in keys:
        home, mid = k.split(":", 1)
        if (home, mid) in live:
            continue
        fresh.append((home, mid))
        name = next((c["name"] for c in L.coaches(home) if c["id"] == mid), "")
        _db().table("flow_coach_offers").insert({
            "tenant_id": tenant_id, "request_id": request_id, "coach_tenant_id": home, "coach_member_id": mid, "coach_name": name,
            "starts_at": _start_of(start).astimezone(timezone.utc).isoformat(), "minutes": found["minutes"],
            "share_bp": found["share_bp"], "fee_bp": found["fee_bp"], "status": "offered", "asked_by": by or None,
            "created_at": now}).execute()
    told = 0                                          # each newly asked coach hears it on WhatsApp, once
    school = tenants.display_name(tenant_id)
    for o in _offers(tenant_id, request_id):
        if o["status"] == "offered" and (o["coach_tenant_id"], o["coach_member_id"]) in fresh:
            told += bool(await tell_coach(o["coach_tenant_id"], o["coach_member_id"], school,
                                          _ask_words(_for_coach(o, cur)), f"pool_ask:{o['id']}"))
    return {"offers": for_request(tenant_id, request_id), "asked": len(fresh), "told": told}


def _clear_guest(tenant_id: str, request_id: str) -> None:
    L = _L()
    _db().table("commerce_lesson_requests").update(
        {"guest_coach_tenant": None, "guest_coach_id": None, "pool_fee_bp": None, "coach_name": None,
         "updated_at": L._now().isoformat()}).eq("tenant_id", tenant_id).eq("id", request_id).execute()


def release(tenant_id: str, request_id: str) -> dict:
    """The school lets the guest coach go and withdraws what it asked. Read back."""
    L = _L()
    cur = L._get(tenant_id, request_id)
    if cur["status"] not in L.OPEN:
        raise _err(f"This request is already {cur['status'].replace('_', ' ')}.", 409)
    now = L._now().isoformat()
    dropped = None
    for o in _offers(tenant_id, request_id):
        if o["status"] in LIVE:
            dropped = o if o["status"] == "accepted" else dropped
            _db().table("flow_coach_offers").update({"status": "withdrawn", "answered_at": now}).eq("id", o["id"]).execute()
    if cur.get("guest_coach_id"):
        _clear_guest(tenant_id, request_id)
    back = L._get(tenant_id, request_id)
    if back.get("guest_coach_id") or any(o["status"] in LIVE for o in _offers(tenant_id, request_id)):
        raise _err("That didn't save. Nothing changed.", 500)
    return {"request": L._out(back), "offers": for_request(tenant_id, request_id), "dropped": dropped}


async def release_and_tell(tenant_id: str, request_id: str) -> dict:
    """`release`, then the coach who had accepted is told they are no longer needed."""
    cur = _L()._get(tenant_id, request_id)
    out = release(tenant_id, request_id)
    dropped = out.pop("dropped", None)
    out["coach_told"] = bool(dropped and await tell_guest(tenant_id, cur, "released", dropped))
    return out


# ── the coach's side ───────────────────────────────────────────────────────────────────────────
def _for_coach(o: dict, r: dict) -> dict:
    """What a coach from another school may know about a lesson: never who the customer is."""
    from vula.api import tenants
    L = _L()
    st = L.settings(o["tenant_id"])
    spot = st.get("spot") or None
    owed = r.get("price_cents") if r.get("price_cents") is not None else r.get("quote_cents")
    return {"id": o["id"], "status": o["status"], "school": tenants.display_name(o["tenant_id"]),
            "spot": {"name": spot["name"], "map": f"https://maps.google.com/?q={spot['lat']},{spot['lng']}"} if spot else None,
            "lesson": r.get("item_name"), "starts_at": o["starts_at"], "when": L.when_text(o["starts_at"]), "minutes": o["minutes"],
            "people": int(r.get("party_size") or 1), "level": (L.LEVELS.get(r.get("level") or "") or "").split(" (")[0],
            "share_bp": o["share_bp"], "pay_cents": L.share_of(owed, o["share_bp"]),
            "confirmed": r.get("status") == "confirmed"}


def mine(tenant_id: str, member_id: str, month: str = "") -> dict:
    """The signed-in coach: their choice, what other schools are asking, what they have taken that
    is still to come, and what lessons at other schools earned them in a month."""
    L = _L()
    out = {"available": partner_id(tenant_id) is not None, "open": is_open(tenant_id, member_id),
           "asked": [], "taken": [], "month": None}
    if not out["available"]:
        return out
    try:
        offers = (_db().table("flow_coach_offers").select("*").eq("coach_tenant_id", tenant_id).eq("coach_member_id", member_id)
                  .in_("status", list(LIVE)).order("starts_at").limit(500).execute().data or [])
    except Exception as exc:  # noqa: BLE001
        log.debug("offers unreadable: %s", exc)
        return out
    reqs = _requests([o["request_id"] for o in offers])
    now = L._now()
    lo, hi = L._month_bounds(month) if month else ("", "")
    done, earned = 0, 0
    for o in offers:
        r = reqs.get(str(o["request_id"]))
        start = _start_of(o["starts_at"])
        if not r or not start:
            continue
        if o["status"] == "offered" and r["status"] == "requested" and not r.get("guest_coach_id") and start > now:
            out["asked"].append(_for_coach(o, r))
        elif o["status"] == "accepted" and r["status"] in L.OPEN and start > now - timedelta(hours=3):
            out["taken"].append(_for_coach(o, r))
        elif o["status"] == "accepted" and r["status"] == "completed" and month and lo <= start.astimezone(timezone.utc).isoformat() < hi:
            owed = r.get("price_cents") if r.get("price_cents") is not None else r.get("quote_cents")
            done += 1
            earned += L.share_of(owed or 0, o["share_bp"]) or 0
    if month:
        out["month"] = {"month": month, "done": done, "share_cents": earned}
    return out


async def _tell_school(tenant_id: str, text: str, key: str) -> None:
    try:
        from vula.integrations.notify import notify_team
        await notify_team(tenant_id, "lesson_request", text, idem_key=key)
    except Exception as exc:  # noqa: BLE001
        log.warning("guest coach news not sent to %s: %s", tenant_id, exc)


async def answer(tenant_id: str, member_id: str, offer_id: str, accept: bool) -> dict:
    """The coach answers an offer made to THEM (their school and id come from their login).
    Accept: the first coach wins, by one conditional write on the request, read back. Decline —
    also after accepting ("I can't make it"), which gives the lesson back and tells the school."""
    from vula.api import tenants
    L = _L()
    rows = (_db().table("flow_coach_offers").select("*").eq("id", offer_id).eq("coach_tenant_id", tenant_id)
            .eq("coach_member_id", member_id).limit(1).execute().data or [])
    if not rows:
        raise _err("That offer wasn't found.", 404)
    o = rows[0]
    asking, rid = o["tenant_id"], str(o["request_id"])
    now = L._now()
    first = o["coach_name"].split(" ")[0]
    home = tenants.display_name(tenant_id)
    t = lambda: _db().table("flow_coach_offers")  # noqa: E731
    if not accept:
        if o["status"] not in LIVE:
            raise _err("That offer is no longer open.", 409)
        was = o["status"]
        done = t().update({"status": "declined", "answered_at": now.isoformat()}).eq("id", o["id"]).eq("status", was).execute().data or []
        if not done:
            raise _err("That offer is no longer open.", 409)
        if was == "accepted":
            r = L._get(asking, rid)
            if r.get("guest_coach_id") == member_id and r.get("guest_coach_tenant") == tenant_id and r["status"] in L.OPEN:
                _clear_guest(asking, rid)
                await _tell_school(asking, f"⚠️ {first} ({home}) can no longer take {r.get('item_name', 'the lesson').lower()} "
                                           f"({r['ref']}) on {L.when_text(o['starts_at'])}. It needs a coach again.",
                                   f"pool_dropped:{o['id']}")
        return mine(tenant_id, member_id)
    if o["status"] != "offered":
        raise _err("Another coach has taken this lesson." if o["status"] == "taken" else "That offer is no longer open.", 409)
    start = _start_of(o["starts_at"])
    if not start or start <= now:
        raise _err("That lesson's time has passed.", 409)
    if not is_open(tenant_id, member_id) or member_id not in {c["id"] for c in L.coaches(tenant_id)}:
        raise _err("Switch on working for other schools first.", 409)
    if not _free(tenant_id, member_id, start, start + timedelta(minutes=int(o["minutes"]))):
        raise _err("You already have a lesson then, or you are away.", 409)
    won = (_db().table("commerce_lesson_requests").update(
        {"guest_coach_tenant": tenant_id, "guest_coach_id": member_id, "coach_name": o["coach_name"], "pool_fee_bp": o["fee_bp"],
         "updated_at": now.isoformat()}).eq("tenant_id", asking).eq("id", rid).eq("status", "requested")
        .is_("guest_coach_id", "null").is_("coach_id", "null").execute().data or [])
    back = L._get(asking, rid)
    if not won or back.get("guest_coach_id") != member_id or back.get("guest_coach_tenant") != tenant_id:
        t().update({"status": "taken", "answered_at": now.isoformat()}).eq("id", o["id"]).eq("status", "offered").execute()
        raise _err("Another coach has taken this lesson, or the school no longer needs one.", 409)
    t().update({"status": "accepted", "answered_at": now.isoformat()}).eq("id", o["id"]).eq("status", "offered").execute()
    for other in _offers(asking, rid):
        if other["id"] != o["id"] and other["status"] == "offered":
            t().update({"status": "taken", "answered_at": now.isoformat()}).eq("id", other["id"]).eq("status", "offered").execute()
    await _tell_school(asking, f"🤝 {first} from {home} will take {back.get('item_name', 'the lesson').lower()} ({back['ref']}) on "
                               f"{L.when_text(o['starts_at'])}. Confirm it in Lessons so the customer is told.",
                       f"pool_accepted:{o['id']}")
    return mine(tenant_id, member_id)


def elsewhere(tenant_id: str, member_id: str, lo: datetime, hi: datetime) -> List[dict]:
    """The coach's lessons at other schools between two times (their own calendar)."""
    try:
        rows = _taken(tenant_id, member_id)
    except Exception as exc:  # noqa: BLE001
        log.debug("guest lessons unreadable: %s", exc)
        return []
    out = []
    for o in rows:
        s = _start_of(o["starts_at"])
        if s and lo <= s < hi:
            out.append(_for_coach(o, o["request"]))
    return out


# ── the partner's fee ──────────────────────────────────────────────────────────────────────────
def partner_month(pid: str, month: str) -> dict:
    """The partner's fee for a month, school by school: lessons a guest coach taught that were
    marked done. A report — nothing is charged from here."""
    from vula.api import tenants
    from vula.db_pages import all_rows
    L = _L()
    if pid not in tenants.PARTNERS:
        raise _err("That partner isn't known.", 404)
    lo, hi = L._month_bounds(month)
    offers = all_rows(lambda: _db().table("flow_coach_offers").select("*").eq("status", "accepted").gte("starts_at", lo).lt("starts_at", hi),
                      cap=20000)
    reqs = _requests([o["request_id"] for o in offers])
    by: Dict[str, dict] = {}
    for o in offers:
        r = reqs.get(str(o["request_id"]))
        if not r or r.get("status") != "completed" or partner_id(o["tenant_id"]) != pid:
            continue
        owed = r.get("price_cents") if r.get("price_cents") is not None else r.get("quote_cents")
        s = by.setdefault(o["tenant_id"], {"tenant_id": o["tenant_id"], "school": tenants.display_name(o["tenant_id"]),
                                           "lessons": 0, "value_cents": 0, "coach_cents": 0, "fee_cents": 0})
        s["lessons"] += 1
        s["value_cents"] += int(owed or 0)
        s["coach_cents"] += L.share_of(owed or 0, o["share_bp"]) or 0
        s["fee_cents"] += L.share_of(owed or 0, o["fee_bp"]) or 0
    rows = sorted(by.values(), key=lambda s: s["school"].lower())
    return {"partner": pid, "month": month, "schools": rows,
            "total": {k: sum(s[k] for s in rows) for k in ("lessons", "value_cents", "coach_cents", "fee_cents")}}
