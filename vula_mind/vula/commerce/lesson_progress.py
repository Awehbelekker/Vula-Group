"""A customer's own progress, from the lessons a school marked done (25 Nov 2026).

Nothing is entered for this: it is counted from the customer's finished lessons — how many, the
level the latest one was at, the badges those lessons earn, and how many weeks in a row they came.
Every rule is fixed here in code and shown to the customer beside the badge. It is the customer's
own: there is no board, and nobody is compared with anyone.

`next_good_day` reads the forecast for the next few days at a school's spot and names the first
part of a day rated good for that customer's sport and level — the same rating the school sees.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable, List, Optional

LEVEL_ORDER = ("first_timer", "beginner", "intermediate", "advanced")
# (id, name, how it is earned) — the order they are shown in
BADGES = (("first", "First lesson", "Finish your first lesson"),
          ("five", "Five lessons", "Finish five lessons"),
          ("ten", "Ten lessons", "Finish ten lessons"),
          ("dawn", "Dawn patrol", "Finish a lesson that starts before 8 in the morning"),
          ("weeks", "Three weeks running", "Finish a lesson three weeks in a row"),
          ("sports", "Tried another sport", "Finish lessons in two different sports"))


def _start(r: dict) -> Optional[datetime]:
    from vula.commerce import lessons
    try:
        return datetime.fromisoformat(str(r.get("starts_at")).replace("Z", "+00:00")).astimezone(lessons.SAST)
    except (TypeError, ValueError):
        return None


def _weeks_running(weeks: List[tuple]) -> int:
    """The longest run of weeks one after the other (ISO weeks, across a new year)."""
    mondays = sorted({datetime.fromisocalendar(y, w, 1).date() for y, w in weeks})
    best = run = 0
    last = None
    for m in mondays:
        run = run + 1 if last is not None and (m - last).days == 7 else 1
        best, last = max(best, run), m
    return best


def progress(rows: List[dict], sport_of: Callable[[dict], str] = lambda r: "") -> dict:
    """What a customer has done, from their own request rows (any status, any school)."""
    from vula.commerce import lessons
    done = [r for r in rows if r.get("kind") == "lesson" and r.get("status") == "completed"]
    done.sort(key=lambda r: str(r.get("starts_at") or ""))
    starts = [s for s in (_start(r) for r in done) if s]
    sports = {s for s in (sport_of(r) for r in done) if s and s != "other"}
    weeks = _weeks_running([s.isocalendar()[:2] for s in starts])
    earned = {"first": len(done) >= 1, "five": len(done) >= 5, "ten": len(done) >= 10,
              "dawn": any(s.hour < 8 for s in starts), "weeks": weeks >= 3, "sports": len(sports) >= 2}
    level = next((r["level"] for r in reversed(done) if r.get("level") in LEVEL_ORDER), None)
    at = LEVEL_ORDER.index(level) if level else -1
    return {"lessons": len(done), "weeks_running": weeks,
            "level": lessons.LEVELS[level].split(" (")[0] if level else None,
            "next_level": lessons.LEVELS[LEVEL_ORDER[at + 1]].split(" (")[0] if 0 <= at < len(LEVEL_ORDER) - 1 else None,
            "last": lessons.when_text(done[-1]["starts_at"]) if done and done[-1].get("starts_at") else None,
            "badges": [{"id": i, "name": n, "how": h, "earned": earned[i]} for i, n, h in BADGES]}


async def next_good_day(spot: dict, sport: str, level: str = "", days: int = 5, today: Any = None) -> Optional[dict]:
    """The first part of a day, from tomorrow, rated good at this spot for this sport and level:
    {"date", "day", "part", "text"} — or None when none of the next days is, or no forecast."""
    from vula.commerce import conditions, lessons
    start = today or lessons._now().astimezone(lessons.SAST).date()
    for n in range(1, days + 1):
        day = start + timedelta(days=n)
        try:
            hours = conditions.with_shore(await conditions.forecast(spot["lat"], spot["lng"], day), spot.get("faces"))
        except Exception:  # noqa: BLE001 — the forecast is an extra
            return None
        for part in conditions.brief(hours, {}, [sport], level)["parts"]:
            if list(part["ratings"].values()) == ["good"]:
                return {"date": day.isoformat(), "day": lessons.day_text(day.isoformat()),
                        "part": part["part"].lower(), "text": part["text"]}
    return None
