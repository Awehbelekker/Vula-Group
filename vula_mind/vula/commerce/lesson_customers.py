"""A customer's own profile in the customer app (25 Nov 2026, migration 309).

A signed-in customer keeps one profile across every school: who they are, what gear fits them,
whether they can swim, an emergency contact, a note for their coach, and — for each sport — their
level and what they can already do. The customer fills it in. After a lesson the school marked
done, a coach can confirm the level for that lesson's sport; the confirmed level is shown beside
the customer's own and is what the next school can rely on.

It is the customer's, not a school's: a school reads it only through a booking that customer made
with it (waiting, confirmed or done), and only what a coach needs for that lesson. The emergency
contact and the note are stored encrypted. The customer can delete it.

`my_coaches` is the coaches who taught them, from their finished lessons, with their own stars.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)
TABLE = "flow_customer_profiles"
WETSUITS = ("XS", "S", "M", "L", "XL", "XXL", "Kids 6", "Kids 8", "Kids 10", "Kids 12", "Kids 14")
NOTE_MAX = 300
# What a customer can tick for each sport, in the order a learner meets them. Plain words; a sport
# with none listed still has a level.
SKILLS: Dict[str, List[tuple]] = {
    "surf": [("paddle", "Paddle out on my own"), ("stand", "Stand up in the white water"),
             ("green", "Catch unbroken waves"), ("turn", "Turn along the wave"), ("duck", "Duck dive or turtle roll")],
    "sup": [("stand", "Stand and paddle on flat water"), ("turn", "Turn the board"), ("wind", "Paddle back into the wind"),
            ("waves", "Ride small waves")],
    "kite": [("fly", "Fly a trainer kite"), ("launch", "Launch and land safely"), ("drag", "Body drag upwind"),
             ("start", "Water start"), ("upwind", "Ride upwind"), ("turn", "Change direction without stopping")],
    "windsurf": [("uphaul", "Uphaul the sail"), ("sail", "Sail across and back"), ("tack", "Tack"),
                 ("harness", "Use the harness"), ("start", "Water start")],
    "wing": [("handle", "Handle the wing on land"), ("knees", "Ride on my knees"), ("stand", "Stand and ride"),
             ("foil", "Get up on the foil"), ("turn", "Turn and stay on the foil")],
    "foil": [("tow", "Ride the foil behind a boat or on a wave"), ("pump", "Pump the foil"), ("turn", "Turn on the foil")],
    "bodyboard": [("catch", "Catch white water"), ("green", "Catch unbroken waves"), ("trim", "Ride along the wave")],
    "kayak": [("paddle", "Paddle in a straight line"), ("turn", "Turn and stop"), ("exit", "Get out after a capsize"),
              ("surf", "Launch and land through small waves")],
    "skate": [("push", "Push and roll"), ("turn", "Carve a turn"), ("stop", "Stop safely"), ("pump", "Pump for speed")],
}


class ProfileError(Exception):
    pass


def _db():
    from vula.commerce import lessons
    return lessons._db()


def catalog() -> dict:
    """The sports a profile can hold, each with its name and its skills, the levels and the sizes."""
    from vula.commerce import lessons
    return {"sports": [{"id": k, "name": lessons.SPORT_NAMES[k], "skills": [{"id": i, "name": n} for i, n in SKILLS.get(k, [])]}
                       for k in lessons.SPORTS if k != "other"],
            "levels": {k: v.split(" (")[0] for k, v in lessons.LEVELS.items()}, "wetsuits": list(WETSUITS)}


def _seal(text: str) -> Optional[str]:
    from vula import secret_keys
    return secret_keys.encrypt(text) if text else None


def _open(stored: Any) -> str:
    if not stored:
        return ""
    from vula import secret_keys
    try:
        return secret_keys.decrypt(stored) or ""
    except Exception:  # noqa: BLE001 — a key that changed: shown as empty, never as garbage
        return ""


def _row(user_id: str) -> Optional[dict]:
    try:
        rows = _db().table(TABLE).select("*").eq("user_id", user_id).limit(1).execute().data or []
    except Exception as exc:  # noqa: BLE001 — migration 309 not applied: there is simply no profile
        log.debug("customer profile unreadable: %s", exc)
        return None
    return rows[0] if rows else None


def _shape(row: Optional[dict]) -> dict:
    r = row or {}
    name, _, number = _open(r.get("emergency_sealed")).partition("\n")
    return {"name": r.get("name") or "", "phone": r.get("phone") or "", "birth_year": r.get("birth_year"),
            "photo_url": r.get("photo_url"), "can_swim": r.get("can_swim"),
            "gear": r.get("gear") if isinstance(r.get("gear"), dict) else {},
            "sports": r.get("sports") if isinstance(r.get("sports"), dict) else {},
            "emergency": {"name": name, "phone": number}, "medical": _open(r.get("medical_sealed")),
            "saved": bool(row)}


def get(user_id: str) -> dict:
    return _shape(_row(user_id))


def _number(raw: Any, what: str, lo: int, hi: int) -> Optional[int]:
    if raw in (None, ""):
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not lo <= raw <= hi:
        raise ProfileError(f"{what} should be between {lo} and {hi}.")
    return int(raw)


def _clean_sports(raw: Any, kept: dict) -> dict:
    """The customer's own level and skills for each sport. A coach's confirmation already on the
    profile is carried over untouched: the customer can't write one, or remove one."""
    from vula.commerce import lessons
    if raw is None:
        return kept
    if not isinstance(raw, dict):
        raise ProfileError("Your sports couldn't be read.")
    out: Dict[str, dict] = {}
    for sport in lessons.SPORTS:
        if sport == "other":
            continue
        mine = raw.get(sport)
        confirmed = (kept.get(sport) or {}).get("confirmed")
        if not isinstance(mine, dict) or not mine.get("level"):
            if confirmed:                                   # a level a coach confirmed stays on the profile
                out[sport] = {**(kept.get(sport) or {})}
            continue
        if mine["level"] not in lessons.LEVELS:
            raise ProfileError(f"{lessons.SPORT_NAMES[sport]}: choose a level.")
        allowed = [i for i, _ in SKILLS.get(sport, [])]
        entry = {"level": mine["level"], "skills": [i for i in allowed if i in (mine.get("skills") or [])]}
        if confirmed:
            entry["confirmed"] = confirmed
        out[sport] = entry
    return out


def save(user_id: str, data: dict, photo_url: Optional[str] = None) -> dict:
    """Save what the customer filled in, and read it back. Only the fields sent are changed."""
    from vula.commerce import lessons
    from datetime import date
    d = data if isinstance(data, dict) else {}
    cur = _row(user_id) or {}
    row: Dict[str, Any] = {"user_id": user_id, "updated_at": lessons._now().isoformat()}
    if "name" in d:
        row["name"] = lessons._text(d["name"], 60) or None
    if "phone" in d:
        try:
            row["phone"] = lessons._phone(d["phone"], required=False)
        except lessons.LessonError as exc:
            raise ProfileError(str(exc)) from exc
    if "birth_year" in d:
        row["birth_year"] = _number(d["birth_year"], "Your year of birth", 1920, date.today().year - 3)
    if "can_swim" in d:
        if d["can_swim"] not in (True, False, None):
            raise ProfileError("Say whether you can swim 50 metres: yes or no.")
        row["can_swim"] = d["can_swim"]
    if "gear" in d:
        g = d["gear"] if isinstance(d["gear"], dict) else {}
        size = g.get("wetsuit") or None
        if size and size not in WETSUITS:
            raise ProfileError("Choose a wetsuit size from the list.")
        gear = {"height_cm": _number(g.get("height_cm"), "Your height in centimetres", 80, 230),
                "weight_kg": _number(g.get("weight_kg"), "Your weight in kilograms", 15, 200), "wetsuit": size}
        row["gear"] = {k: v for k, v in gear.items() if v is not None}
    if "emergency" in d:
        e = d["emergency"] if isinstance(d["emergency"], dict) else {}
        who = lessons._text(e.get("name"), 60)
        try:
            number = lessons._phone(e.get("phone"), required=False) or ""
        except lessons.LessonError as exc:
            raise ProfileError("The emergency contact's number doesn't look right.") from exc
        row["emergency_sealed"] = _seal(f"{who}\n{number}") if (who or number) else None
    if "medical" in d:
        row["medical_sealed"] = _seal(lessons._text(d["medical"], NOTE_MAX))
    if "sports" in d:
        row["sports"] = _clean_sports(d["sports"], cur.get("sports") if isinstance(cur.get("sports"), dict) else {})
    if "photo" in d:                                        # keep the sign-in account's picture, or not
        ok = d["photo"] is True and isinstance(photo_url, str) and photo_url.startswith("https://") and len(photo_url) <= 500
        row["photo_url"] = photo_url if ok else None
    try:
        _db().table(TABLE).upsert(row, on_conflict="user_id").execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("customer profile not saved: %s", type(exc).__name__)
        raise ProfileError("Your profile couldn't be saved just now. Try again later.") from exc
    back = _row(user_id)
    if not back:
        raise ProfileError("Your profile couldn't be saved just now. Try again later.")
    return _shape(back)


def erase(user_id: str) -> bool:
    """Delete the customer's profile. Their lesson records stay with the schools, like a booking."""
    try:
        _db().table(TABLE).delete().eq("user_id", user_id).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("customer profile not deleted: %s", type(exc).__name__)
        raise ProfileError("Your profile couldn't be deleted just now. Try again later.") from exc
    return _row(user_id) is None


# ── the school's side: only through that customer's booking with it ────────────────────────────
def _booking(tenant_id: str, request_id: str) -> dict:
    from vula.commerce import lessons
    try:
        r = lessons._get(tenant_id, request_id)
    except lessons.LessonError as exc:
        raise ProfileError("That booking couldn't be found.") from exc
    if not r.get("customer_user_id"):
        raise ProfileError("This customer didn't sign in, so they have no profile.")
    return r


def _sport_of(tenant_id: str, r: dict) -> str:
    from vula.commerce import lessons
    return (lessons.lesson(lessons.settings(tenant_id), r.get("item_id") or "") or {}).get("sport") or ""


def for_school(tenant_id: str, request_id: str) -> dict:
    """What a coach needs for this booking: the customer's level and skills in this lesson's sport,
    their gear sizes, whether they swim, the emergency contact and their note. Only while the
    booking is waiting, confirmed or done."""
    from vula.commerce import lessons
    r = _booking(tenant_id, request_id)
    if r.get("status") not in ("requested", "confirmed", "completed"):
        raise ProfileError("The profile is shown only for a booking that is waiting, confirmed or done.")
    p = get(str(r["customer_user_id"]))
    sport = _sport_of(tenant_id, r)
    mine = p["sports"].get(sport) or {}
    names = dict(SKILLS.get(sport, []))
    return {"name": p["name"] or r.get("customer_name") or "", "photo_url": p["photo_url"], "birth_year": p["birth_year"],
            "can_swim": p["can_swim"], "gear": p["gear"], "emergency": p["emergency"], "medical": p["medical"],
            "sport": sport, "sport_name": lessons.SPORT_NAMES.get(sport, ""),
            "level": lessons.LEVELS.get(mine.get("level") or "", "").split(" (")[0] or None,
            "skills": [names[i] for i in mine.get("skills") or [] if i in names],
            "confirmed": mine.get("confirmed"), "has_profile": p["saved"],
            "can_confirm": r.get("kind") == "lesson" and r.get("status") == "completed" and bool(sport)}


def confirm_level(tenant_id: str, request_id: str, level: Any, by: str = "") -> dict:
    """A coach confirms the customer's level in this lesson's sport, once the lesson is done.
    It becomes their level, marked as confirmed by this school on this day."""
    from vula.api import tenants
    from vula.commerce import lessons
    r = _booking(tenant_id, request_id)
    if r.get("kind") != "lesson" or r.get("status") != "completed":
        raise ProfileError("A level is confirmed once the lesson is done.")
    if level not in lessons.LEVELS:
        raise ProfileError("Choose the level.")
    sport = _sport_of(tenant_id, r)
    if not sport or sport == "other":
        raise ProfileError("This lesson has no sport set. Set it in the lesson's settings first.")
    uid = str(r["customer_user_id"])
    cur = _row(uid) or {}
    sports = dict(cur.get("sports") if isinstance(cur.get("sports"), dict) else {})
    entry = dict(sports.get(sport) or {"skills": []})
    entry["level"] = level
    entry["confirmed"] = {"level": level, "school": tenants.display_name(tenant_id),
                          "at": lessons._now().astimezone(lessons.SAST).date().isoformat()}
    sports[sport] = entry
    try:
        _db().table(TABLE).upsert({"user_id": uid, "sports": sports, "updated_at": lessons._now().isoformat()},
                                  on_conflict="user_id").execute()
    except Exception as exc:  # noqa: BLE001
        raise ProfileError("The level couldn't be saved just now. Try again later.") from exc
    back = ((_row(uid) or {}).get("sports") or {}).get(sport) or {}
    if (back.get("confirmed") or {}).get("level") != level:
        raise ProfileError("The level couldn't be saved just now. Try again later.")
    try:
        from vula.api.merchant_audit import audit
        audit(tenant_id, {"name": by} if by else None, "lesson_level_confirmed", request=r.get("ref"), sport=sport, level=level)
    except Exception:  # noqa: BLE001 — the record of who did it is an extra
        pass
    return for_school(tenant_id, request_id)


# ── the coaches who taught them ────────────────────────────────────────────────────────────────
def my_coaches(user_id: str) -> List[dict]:
    """Each coach who taught this customer a lesson that was marked done: first name, school,
    how many lessons, the last one, and the customer's own stars for them. Newest first."""
    from vula.api import tenants
    from vula.commerce import lessons
    rows = (_db().table("commerce_lesson_requests").select("id,tenant_id,coach_id,coach_name,starts_at,item_id")
            .eq("customer_user_id", user_id).eq("status", "completed").eq("kind", "lesson")
            .order("starts_at", desc=True).limit(200).execute().data or [])
    rows = [r for r in rows if r.get("coach_id") and r.get("coach_name")]
    stars: Dict[str, int] = {}
    ids = [str(r["id"]) for r in rows]
    if ids:
        try:
            for x in (_db().table("commerce_lesson_ratings").select("request_id,coach_stars").in_("request_id", ids)
                      .limit(200).execute().data or []):
                if x.get("coach_stars"):
                    stars[str(x["request_id"])] = int(x["coach_stars"])
        except Exception:  # noqa: BLE001 — no ratings table yet
            stars = {}
    out: Dict[tuple, dict] = {}
    photos: Dict[str, dict] = {}
    for r in rows:
        tid = r["tenant_id"]
        if not tenants.is_active(tid):
            continue
        if tid not in photos:
            st = lessons.settings(tid)
            photos[tid] = {p.get("member_id"): p.get("photo_url") for p in st.get("coach_profiles") or []
                           if isinstance(p, dict) and p.get("shown") is True}
        key = (tid, r["coach_id"])
        c = out.setdefault(key, {"name": r["coach_name"].split(" ")[0], "school": tenants.display_name(tid),
                                 "shop": tid.replace("_", "-"), "photo_url": photos[tid].get(r["coach_id"]),
                                 "lessons": 0, "last": lessons.when_text(r["starts_at"]) if r.get("starts_at") else None,
                                 "lesson_id": r.get("item_id"), "_stars": []})
        c["lessons"] += 1
        if str(r["id"]) in stars:
            c["_stars"].append(stars[str(r["id"])])
    done = []
    for c in out.values():
        given = c.pop("_stars")
        done.append({**c, "my_stars": round(sum(given) / len(given), 1) if given else None})
    return done
