"""Each coach's week, and what a new lesson would run into (25 Nov 2026, no migration).

A coach's calendar is read from what is already kept: the lessons confirmed for them
(`commerce_lesson_requests`, each as long as its lesson's `minutes`) and the times they said they
can work or are away (the roster's `kb_availability`, the same a restaurant's staff use). Several
bookings for the same lesson at the same time with the same coach are ONE session — that is a
group lesson — with its people added up against the lesson's `max_per_coach`.

`check` says what confirming a lesson with a coach at a time would run into:
  clash    the coach is teaching something else then            (refused unless the school insists)
  full     the group would go over the lesson's people per coach (refused unless the school insists)
  outside  the coach is away, or hasn't said they can work then  (a warning, never a refusal)
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)


def _lessons():
    from vula.commerce import lessons
    return lessons


def _start(r: dict) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(r.get("starts_at")).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _minutes(st: dict, r: dict) -> int:
    L = _lessons()
    return int((L.lesson(st, r.get("item_id") or "") or {}).get("minutes") or 90)


def _cap(st: dict, r: dict) -> int:
    L = _lessons()
    return int((L.lesson(st, r.get("item_id") or "") or {}).get("max_per_coach") or 5)


def _rows(tenant_id: str, lo: datetime, hi: datetime, coach_id: str = "") -> List[dict]:
    L = _lessons()
    q = (L._db().table("commerce_lesson_requests").select("*").eq("tenant_id", tenant_id).eq("kind", "lesson")
         .in_("status", ["confirmed", "completed"]).gte("starts_at", lo.astimezone(timezone.utc).isoformat())
         .lt("starts_at", hi.astimezone(timezone.utc).isoformat()))
    if coach_id:
        q = q.eq("coach_id", coach_id)
    return q.order("starts_at").limit(900).execute().data or []


def sessions(st: dict, rows: List[dict]) -> List[dict]:
    """Bookings folded into sessions: the same coach, lesson and start are one group lesson."""
    L = _lessons()
    out: Dict[Tuple[str, str, str], dict] = {}
    for r in rows:
        start = _start(r)
        if not start:
            continue
        key = (r.get("coach_id") or "", r.get("item_id") or "", start.isoformat())
        s = out.get(key)
        if not s:
            end = start + timedelta(minutes=_minutes(st, r))
            local, until = start.astimezone(L.SAST), end.astimezone(L.SAST)
            s = out[key] = {"coach_id": r.get("coach_id") or None, "item_id": r.get("item_id"), "item_name": r.get("item_name"),
                            "start": start, "end": end, "date": local.date().isoformat(),
                            "time": f"{local.strftime('%H:%M')}–{until.strftime('%H:%M')}", "people": 0, "max": _cap(st, r),
                            "bookings": []}
        s["people"] += int(r.get("party_size") or 1)
        s["bookings"].append({"id": r["id"], "ref": r.get("ref"), "customer_name": r.get("customer_name"),
                              "people": int(r.get("party_size") or 1), "level": L.LEVELS.get(r.get("level") or "", "").split(" (")[0]})
    return sorted(out.values(), key=lambda s: s["start"])


def _availability(tenant_id: str) -> Tuple[Any, List[dict]]:
    """The roster's availability rows, or nothing when they can't be read (no roster yet)."""
    try:
        from vula.tap import api as tap_api
        svc = tap_api.get_service()
        return svc.shifts, svc.repo.availability(tenant_id)
    except Exception as exc:  # noqa: BLE001 — a school with no roster: nobody has said anything
        log.debug("availability unreadable for %s: %s", tenant_id, exc)
        return None, []


def _public(s: dict) -> dict:
    return {k: v for k, v in s.items() if k not in ("start", "end")} | {"starts_at": s["start"].isoformat()}


def week(tenant_id: str, day: Any = None, only_coach: str = "") -> dict:
    """Monday to Sunday for each coach: their sessions day by day, the days they are away and the
    hours they said they can work. `only_coach`: just that coach (a coach's own view)."""
    L = _lessons()
    try:
        d = date.fromisoformat(str(day)) if day else L._now().astimezone(L.SAST).date()
    except ValueError as exc:
        raise L.LessonError("Choose a week.") from exc
    monday = d - timedelta(days=d.weekday())
    lo = datetime.combine(monday, time(0, 0), tzinfo=L.SAST)
    days = [(monday + timedelta(days=n)).isoformat() for n in range(7)]
    st = L.settings(tenant_id)
    found = sessions(st, _rows(tenant_id, lo, lo + timedelta(days=7), only_coach))
    _desk, avail = _availability(tenant_id)
    team = [c for c in L.coaches(tenant_id) if not only_coach or c["id"] == only_coach]
    out = []
    for c in team:
        mine = [r for r in avail if r.get("member_id") == c["id"]]
        away = {dd: (r.get("note") or "Away") for dd in days for r in mine
                if r.get("kind") == "off" and str(r.get("from_date")) <= dd <= str(r.get("to_date"))}
        hours = sorted(({"weekday": r["weekday"], "start": r["start_time"], "end": r["end_time"]} for r in mine if r.get("kind") == "weekly"),
                       key=lambda h: (h["weekday"], h["start"]))
        out.append({"coach_id": c["id"], "name": c["name"], "away": away, "hours": hours,
                    "days": {dd: [_public(s) for s in found if s["coach_id"] == c["id"] and s["date"] == dd] for dd in days},
                    "lessons": sum(1 for s in found if s["coach_id"] == c["id"]),
                    "people": sum(s["people"] for s in found if s["coach_id"] == c["id"])})
    known = {c["id"] for c in team}
    loose = [] if only_coach else [_public(s) for s in found if s["coach_id"] not in known]
    return {"week": monday.isoformat(), "days": days, "coaches": out, "no_coach": loose}


def check(tenant_id: str, coach_id: str, starts_at: datetime, item_id: str, people: int, request_id: str = "") -> dict:
    """What confirming this lesson with this coach at this time would run into (see the module's
    words): {"clash", "full", "outside"} — each "" when there is nothing to say."""
    L = _lessons()
    st = L.settings(tenant_id)
    name = next((c["name"].split(" ")[0] for c in L.coaches(tenant_id) if c["id"] == coach_id), "This coach")
    start = starts_at if starts_at.tzinfo else starts_at.replace(tzinfo=L.SAST)
    end = start + timedelta(minutes=int((L.lesson(st, item_id) or {}).get("minutes") or 90))
    day_lo = datetime.combine(start.astimezone(L.SAST).date(), time(0, 0), tzinfo=L.SAST) - timedelta(days=1)
    rows = [r for r in _rows(tenant_id, day_lo, day_lo + timedelta(days=3), coach_id) if str(r["id"]) != str(request_id)]
    out = {"clash": "", "full": "", "outside": ""}
    for s in sessions(st, rows):
        same = s["item_id"] == item_id and s["start"] == start
        if same:
            total = s["people"] + int(people or 1)
            if total > s["max"]:
                out["full"] = (f"That would make {total} people in {name}'s {s['time'][:5]} {str(s['item_name']).lower()}; "
                               f"the limit is {s['max']} per coach.")
        elif s["start"] < end and start < s["end"]:
            out["clash"] = f"{name} is already teaching {str(s['item_name']).lower()} {s['time']} that day."
    desk, avail = _availability(tenant_id)
    if desk is not None:
        try:
            why = desk._fits(avail, coach_id, start, end)
            out["outside"] = f"{name} {why}." if why else ""
        except Exception as exc:  # noqa: BLE001 — the warning is an extra
            log.debug("availability check failed: %s", exc)
    return out
