"""A customer rates a finished lesson: the school, and its coach (25 Nov 2026, migration 308).

One rating a lesson, by the customer who had it — the request's own signed token opens it, as it
opens the request. Only a lesson the school marked done can be rated; gear hire can't. The stars
are public as averages with their count (the school's on its page and in the list of schools, a
coach's on their profile). The comment is for the school only and is never shown to other
customers, so nobody's words about a named person are published unread.

Every figure is counted from the rows when it is shown. Before the migration is applied (or with
the database away) there are simply no ratings: nothing here stops a lesson being asked for.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)
COMMENT_MAX = 300
_CACHE: Dict[str, tuple] = {}          # tenant → (at, summary)
_TTL = 300.0


class RatingError(Exception):
    pass


def _db():
    from vula.commerce import lessons
    return lessons._db()


def _stars(raw: Any, what: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= 5:
        raise RatingError(f"{what}: choose 1 to 5 stars.")
    return raw


def rated_ids(request_ids: List[str]) -> set:
    """Which of these requests already have a rating. Empty when the ratings can't be read."""
    ids = [i for i in request_ids if i][:200]
    if not ids:
        return set()
    try:
        rows = _db().table("commerce_lesson_ratings").select("request_id").in_("request_id", ids).limit(200).execute().data or []
    except Exception as exc:  # noqa: BLE001 — migration 308 not applied: nothing is rated
        log.debug("lesson ratings unreadable: %s", exc)
        return set()
    return {str(r["request_id"]) for r in rows}


def rate(tenant_id: str, token: Any, stars: Any, coach_stars: Any = None, comment: Any = "") -> dict:
    """Save the customer's rating for the lesson their token opens. Once; a finished lesson only."""
    from vula.commerce import lessons
    rid = lessons.id_from_token(token)
    if not rid:
        raise RatingError("That lesson couldn't be found.")
    try:
        r = lessons._get(tenant_id, rid)
    except lessons.LessonError as exc:
        raise RatingError("That lesson couldn't be found.") from exc
    if r.get("kind") != "lesson" or r.get("status") != "completed":
        raise RatingError("You can rate a lesson once it is done.")
    row = {"tenant_id": tenant_id, "request_id": rid, "stars": _stars(stars, "The school"),
           "comment": lessons._text(comment, COMMENT_MAX) or None}
    if r.get("coach_id") and coach_stars is not None:
        row["coach_id"], row["coach_stars"] = r["coach_id"], _stars(coach_stars, "Your coach")
    if rid in rated_ids([rid]):
        raise RatingError("You've already rated this lesson. Thank you.")
    try:
        _db().table("commerce_lesson_ratings").insert(row).execute()
    except Exception as exc:  # noqa: BLE001 — the unique index (a second tap) or no table yet
        if rid in rated_ids([rid]):
            raise RatingError("You've already rated this lesson. Thank you.") from exc
        log.warning("lesson rating not saved (%s): %s", tenant_id, type(exc).__name__)
        raise RatingError("Your rating couldn't be saved just now. Try again later.") from exc
    back = _rows(tenant_id, request_id=rid)
    if not back or back[0]["stars"] != row["stars"]:
        raise RatingError("Your rating couldn't be saved just now. Try again later.")
    _CACHE.pop(tenant_id, None)
    return {"stars": back[0]["stars"], "coach_stars": back[0].get("coach_stars")}


def _rows(tenant_id: str, request_id: str = "") -> List[dict]:
    from vula.db_pages import all_rows

    def q():
        query = _db().table("commerce_lesson_ratings").select("*").eq("tenant_id", tenant_id)
        return query.eq("request_id", request_id) if request_id else query
    return all_rows(q, cap=20000)


def _avg(values: List[int]) -> Optional[float]:
    return round(sum(values) / len(values), 1) if values else None


def summary(tenant_id: str) -> dict:
    """{"stars": 4.6 | None, "count": n, "coaches": {coach id: {"stars", "count"}}} — kept five
    minutes. With no ratings (or none readable) the average is None, never a made-up figure."""
    hit = _CACHE.get(tenant_id)
    if hit and time.monotonic() - hit[0] < _TTL:
        return hit[1]
    try:
        rows = _rows(tenant_id)
    except Exception as exc:  # noqa: BLE001
        log.debug("lesson ratings unreadable for %s: %s", tenant_id, exc)
        return {"stars": None, "count": 0, "coaches": {}}
    by_coach: Dict[str, List[int]] = {}
    for r in rows:
        if r.get("coach_id") and r.get("coach_stars"):
            by_coach.setdefault(r["coach_id"], []).append(int(r["coach_stars"]))
    out = {"stars": _avg([int(r["stars"]) for r in rows]), "count": len(rows),
           "coaches": {k: {"stars": _avg(v), "count": len(v)} for k, v in by_coach.items()}}
    _CACHE[tenant_id] = (time.monotonic(), out)
    return out


def public(tenant_id: str) -> Optional[dict]:
    """The school's average for customers: {"stars", "count"}, or None while nobody has rated it."""
    s = summary(tenant_id)
    return {"stars": s["stars"], "count": s["count"]} if s["count"] else None


def for_school(tenant_id: str) -> dict:
    """What the school's owner or manager sees: the averages, each coach's by name, and the latest
    comments with the lesson they were about (the customer's first name only)."""
    from vula.commerce import lessons
    s = summary(tenant_id)
    names = {c["id"]: c["name"] for c in lessons.coaches(tenant_id)}
    try:
        rows = sorted(_rows(tenant_id), key=lambda r: r.get("created_at") or "", reverse=True)[:30]
    except Exception:  # noqa: BLE001
        rows = []
    asked: Dict[str, dict] = {}
    ids = [str(r["request_id"]) for r in rows]
    if ids:
        for q in (_db().table("commerce_lesson_requests").select("id,ref,customer_name,item_name,starts_at")
                  .eq("tenant_id", tenant_id).in_("id", ids).limit(30).execute().data or []):
            asked[str(q["id"])] = q
    latest = []
    for r in rows:
        q = asked.get(str(r["request_id"])) or {}
        latest.append({"stars": r["stars"], "coach_stars": r.get("coach_stars"), "coach": names.get(r.get("coach_id") or ""),
                       "comment": r.get("comment") or "", "ref": q.get("ref"), "lesson": q.get("item_name"),
                       "customer": (q.get("customer_name") or "").split(" ")[0],
                       "when": lessons.when_text(q["starts_at"]) if q.get("starts_at") else None})
    return {"stars": s["stars"], "count": s["count"], "latest": latest,
            "coaches": sorted(({"name": names[k], **v} for k, v in s["coaches"].items() if k in names),
                              key=lambda c: (-(c["stars"] or 0), c["name"]))}
