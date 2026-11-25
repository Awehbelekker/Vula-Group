"""Coach profiles and the directory of schools (24 Nov 2026, migration 305).

Until now the customer app opened on ONE school's page: there was no way to find a school or a
coach you didn't already know. Two things, both the school's own choice:

**Coach profiles.** A school shows what it likes of each coach — a short bio, a photo, the levels
and sports they teach. The name is the team member's first name from the team list, never typed,
and a profile is shown only while that person is still on the team and the school ticked `shown`.

**The listing.** A school that asks to be listed (`listing.listed`) can be found in the customer
app's directory: by name, or nearest first when the customer shares where they are. A school is on
it only while it is active, takes lesson requests, has a spot with a position, and asked — all read
when the list is built. Listing publishes the spot's position to about 100 m (a beach, not an
address); a school's own app link still shows only that school.

The customer's own position is used once to sort the list and is never stored or logged.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

ABOUT_MAX, TOWN_MAX, BIO_MAX, MAX_PROFILES = 160, 40, 300, 20
CACHE_S = 300.0
_CACHE: Dict[str, Any] = {"at": 0.0, "rows": None}
_LINK_RE = re.compile(r"https?://|www\.|\b[\w-]+\.(?:co\.za|com|net|org|africa|shop)\b", re.I)
_PHOTO_RE = re.compile(r"^https://[^\s/]+\.[^\s]+$")


def _err(message: str):
    from vula.commerce.lessons import LessonError
    return LessonError(message)


def _line(raw: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(raw or "")).strip()[:limit].strip()


# The ready-made looks a school can give its page in the customer app (25 Nov 2026). Only the name
# is kept; the colours are the app's (flow.css), each checked to read. "ocean" is FlowCrew's own.
THEMES = {"ocean": "Ocean", "sunset": "Sunset", "sand": "Sand", "kelp": "Kelp", "night": "Night"}


def theme_of(st: dict) -> str:
    """The look a school chose, or "" for FlowCrew's own (or its own colours where it set any)."""
    theme = (st.get("listing") or {}).get("theme")
    return theme if theme in THEMES else ""


# ── what a school saves ───────────────────────────────────────────────────────────────────────
def clean_listing(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict) or not isinstance(raw.get("listed"), bool):
        raise _err("Say whether to list your school: listed must be true or false.")
    about, town = _line(raw.get("about"), ABOUT_MAX), _line(raw.get("town"), TOWN_MAX)
    if _LINK_RE.search(about) or _LINK_RE.search(town):
        raise _err("Leave web addresses out — the listing already links to your page.")
    out = {"listed": raw["listed"], "about": about, "town": town}
    theme = raw.get("theme")                      # the look of the school's page in the customer app
    if theme not in (None, ""):
        if theme not in THEMES:
            raise _err("Choose one of the looks: " + ", ".join(THEMES.values()) + ".")
        out["theme"] = theme
    return out


def clean_profiles(tenant_id: str, raw: Any) -> List[dict]:
    """Profiles for people on the team now, one each. The name comes from the team list."""
    from vula.commerce import lessons
    if not isinstance(raw, list) or len(raw) > MAX_PROFILES:
        raise _err(f"Add up to {MAX_PROFILES} coach profiles.")
    team = {c["id"]: c["name"] for c in lessons.coaches(tenant_id)}
    out, seen = [], set()
    for p in raw:
        mid = str(p.get("member_id") or "") if isinstance(p, dict) else ""
        if mid not in team:
            raise _err("A coach profile is for someone on your team. Add them in Team first.")
        if mid in seen:
            raise _err(f"{team[mid]} has two profiles. Keep one.")
        seen.add(mid)
        bio = _line(p.get("bio"), BIO_MAX)
        if _LINK_RE.search(bio):
            raise _err(f"{team[mid]}: leave web addresses out of the bio.")
        photo = _line(p.get("photo_url"), 500) or None
        if photo and not _PHOTO_RE.match(photo):
            raise _err(f"{team[mid]}: the photo's address must start with https://")
        levels = [k for k in lessons.LEVELS if k in (p.get("levels") or [])]
        sports = [k for k in lessons.SPORTS if k in (p.get("sports") or [])]
        out.append({"member_id": mid, "name": team[mid].split(" ")[0], "bio": bio, "photo_url": photo,
                    "levels": levels, "sports": sports, "shown": p.get("shown") is True})
    return out


def public_coaches(tenant_id: str, st: dict) -> List[dict]:
    """The profiles a customer may see: ticked `shown`, and still on the team. No ids."""
    from vula.commerce import lessons
    from vula.commerce import lesson_ratings
    team = {c["id"]: c["name"] for c in lessons.coaches(tenant_id)}
    stars = lesson_ratings.summary(tenant_id)["coaches"]          # customers' stars for each coach
    return [{"name": team[p["member_id"]].split(" ")[0], "bio": p.get("bio") or "", "photo_url": p.get("photo_url"),
             "rating": stars.get(p["member_id"]),
             "levels": [lessons.LEVELS[k].split(" (")[0] for k in p.get("levels") or [] if k in lessons.LEVELS],
             "sports": list(p.get("sports") or [])}
            for p in st.get("coach_profiles") or []
            if isinstance(p, dict) and p.get("shown") is True and p.get("member_id") in team]


def status(tenant_id: str, st: dict) -> Dict[str, Any]:
    """What the school sees: its listing, whether it is showing, and if not, why."""
    from vula.api import tenants
    listing = st.get("listing") if isinstance(st.get("listing"), dict) else {}
    out = {"listed": listing.get("listed") is True, "about": listing.get("about") or "", "town": listing.get("town") or "",
           "about_max": ABOUT_MAX, "town_max": TOWN_MAX, "showing": False, "why_not": "",
           "theme": theme_of(st), "themes": [{"id": k, "name": v} for k, v in THEMES.items()]}
    if not out["listed"]:
        out["why_not"] = "Your school isn't listed."
    elif not tenants.is_active(tenant_id):
        out["why_not"] = "This business is paused."
    elif not st.get("enabled"):
        out["why_not"] = "Turn on lesson requests first."
    elif not st.get("spot"):
        out["why_not"] = "Add your spot and its position on the map, so customers can find you."
    else:
        out["showing"] = True
    return out


def changed() -> None:
    _CACHE["rows"] = None


# ── the directory ─────────────────────────────────────────────────────────────────────────────
def _from_cents(lessons_list: List[dict]) -> Optional[int]:
    prices = [t["single_cents"] for x in lessons_list for t in x.get("group") or [] if t.get("single_cents")]
    prices += [x["private"]["single_cents"] for x in lessons_list if (x.get("private") or {}).get("single_cents")]
    return min(prices) if prices else None


async def _entry(row: dict) -> Optional[Dict[str, Any]]:
    from vula.api import tenants
    from vula.api.commerce import public_brand
    from vula.commerce import lesson_ratings
    tid = row.get("tenant_id") or ""
    listing = row.get("listing") if isinstance(row.get("listing"), dict) else {}
    spot = row.get("spot") if isinstance(row.get("spot"), dict) else None
    if not tid or listing.get("listed") is not True or not spot or not tenants.is_active(tid):
        return None
    brand = await public_brand(tid)
    kinds = row.get("lessons") or []
    coaches = public_coaches(tid, row)
    logo = str(brand.get("logo_url") or "")
    return {"shop": tid.replace("_", "-"), "name": _line(brand.get("name"), 80),
            "logo_url": logo if logo.startswith("https://") else None, "accent_color": brand.get("accent_color"),
            "about": _line(listing.get("about"), ABOUT_MAX), "town": _line(listing.get("town"), TOWN_MAX),
            # a beach to about 100 m — enough for a map and "nearest first", not an address
            "spot": {"name": spot["name"], "lat": round(float(spot["lat"]), 3), "lng": round(float(spot["lng"]), 3)},
            "sports": sorted({x.get("sport") or "other" for x in kinds}),
            "main_sport": (kinds[0].get("sport") or "other") if kinds else "other",      # its first lesson: what "now" is rated for
            "lessons": [x["name"] for x in kinds][:6],
            "from_cents": _from_cents(kinds), "hire": bool(row.get("hire")),
            "rating": lesson_ratings.public(tid),
            "coaches": [{"name": c["name"], "photo_url": c["photo_url"]} for c in coaches[:6]], "coach_count": len(coaches)}


async def entries(fresh: bool = False) -> List[Dict[str, Any]]:
    """The schools on the directory right now, by name. Kept five minutes; a listing change clears it."""
    if not fresh and _CACHE["rows"] is not None and time.monotonic() - _CACHE["at"] < CACHE_S:
        return _CACHE["rows"]
    from vula.commerce import service
    from vula.db_pages import all_rows
    rows = all_rows(lambda: service._client().table("commerce_lesson_settings")
                    .select("tenant_id,enabled,lessons,hire,spot,listing,coach_profiles").eq("enabled", True),
                    order="tenant_id", cap=5000)
    out: List[Dict[str, Any]] = []
    for row in rows:
        try:
            e = await _entry(row)
        except Exception as exc:  # noqa: BLE001 — one school's unreadable brand doesn't empty the list
            log.warning("directory entry failed for %s: %s", row.get("tenant_id"), exc)
            e = None
        if e:
            out.append(e)
    out.sort(key=lambda e: e["name"].lower())
    _CACHE.update(at=time.monotonic(), rows=out)
    return out


def distance_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance, to a tenth of a kilometre."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    return round(2 * 6371.0088 * math.asin(math.sqrt(a)), 1)


def near(rows: List[dict], lat: Optional[float], lng: Optional[float]) -> List[dict]:
    """Copies of the rows, nearest first with `distance_km` when a position is given; else by name."""
    if lat is None or lng is None:
        return [dict(r) for r in rows]
    out = [{**r, "distance_km": distance_km(lat, lng, r["spot"]["lat"], r["spot"]["lng"])} for r in rows]
    out.sort(key=lambda r: (r["distance_km"], r["name"].lower()))
    return out


async def _now_rating(spot: dict, hour: int, day, sport: str = "surf") -> Optional[dict]:
    from vula.commerce import conditions
    try:
        hours = await asyncio.wait_for(conditions.forecast(spot["lat"], spot["lng"], day), timeout=4)
    except Exception:  # noqa: BLE001 — a card without today's conditions is still a card
        return None
    h = hours.get(hour) or hours.get(min(hours, key=lambda k: abs(k - hour))) if hours else None
    return {"text": conditions.describe(h), **conditions.rate(sport, "", h)} if h else None


async def with_conditions(rows: List[dict], limit: int = 12) -> List[dict]:
    """The first `limit` rows get what the forecast says for this hour at their spot."""
    from vula.commerce import lessons
    now = lessons._now().astimezone(lessons.SAST)
    hour = min(max(now.hour, 6), 19)
    got = await asyncio.gather(*[_now_rating(r["spot"], hour, now.date(), r.get("main_sport") or "surf") for r in rows[:limit]])
    return [{**r, "now": got[i] if i < len(got) else None} for i, r in enumerate(rows)]
