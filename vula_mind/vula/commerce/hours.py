"""
vula/commerce/hours.py — opening hours (migration 158).

Same shape as vula/commerce/geo.py's delivery-coverage verdict: a deterministic answer the
commerce assistant calls via check_business_hours instead of guessing, or computing SAST time
itself. Before this, EVERY "are you open?" question routed to ask_team — a fact that should be
configured once, not a staff escalation every time a customer asks it.

business_hours shape: {"mon": {"open": "08:00", "close": "17:00"}, ..., "sun": null} — a day
mapped to null/missing means closed. All times SAST (UTC+2, no DST) — every current tenant is
South African, same assumption vula/commerce/job_config.py already makes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

SAST = timezone(timedelta(hours=2))
_DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _parse_hm(s: Any) -> Optional[tuple[int, int]]:
    try:
        h, m = str(s).split(":")
        return int(h), int(m)
    except (ValueError, AttributeError):
        return None


def _is_open_at(day_cfg: Optional[dict], hm: tuple[int, int]) -> bool:
    if not day_cfg:
        return False
    o, c = _parse_hm(day_cfg.get("open")), _parse_hm(day_cfg.get("close"))
    if not o or not c:
        return False
    return o <= hm < c


def hours_verdict(business_hours: Optional[dict], now: Optional[datetime] = None) -> Optional[dict]:
    """{"open": bool, "today": {"open","close"}|None, "next_open": {"day","time"}|None} — or
    None if business_hours isn't configured at all (caller must escalate, never guess, same
    convention as geo.area_verdict when delivery_areas is unset)."""
    if not business_hours:
        return None
    now = (now or datetime.now(SAST)).astimezone(SAST)
    today_key = _DAY_KEYS[now.weekday()]
    today = business_hours.get(today_key)
    cur = (now.hour, now.minute)

    if _is_open_at(today, cur):
        return {"open": True, "today": today, "next_open": None}

    # Closed right now — scan forward up to a week for the next configured opening slot.
    for offset in range(8):
        d = now + timedelta(days=offset)
        cfg = business_hours.get(_DAY_KEYS[d.weekday()])
        o = _parse_hm((cfg or {}).get("open"))
        if not o:
            continue
        if offset == 0 and cur >= o:
            continue  # today's slot (if any) already passed
        label = {0: "today", 1: "tomorrow"}.get(offset, d.strftime("%A"))
        return {"open": False, "today": today, "next_open": {"day": label, "time": cfg["open"]}}
    return {"open": False, "today": today, "next_open": None}


_DAY_NAMES = {"mon": "Mon", "tue": "Tue", "wed": "Wed", "thu": "Thu", "fri": "Fri", "sat": "Sat",
              "sun": "Sun"}


def describe(business_hours: Optional[dict]) -> str:
    """The week in one line — "Mon–Fri 08:00–17:00 · Sat 09:00–13:00 · Sun closed" — or "" when
    hours aren't set. 2026-10-22: the business profile's free-text "hours" answer could disagree
    with these structured hours; the profile now shows this line instead when they're set."""
    if not business_hours:
        return ""

    def slot(day: str) -> str:
        cfg = business_hours.get(day) or {}
        o, c = _parse_hm(cfg.get("open")), _parse_hm(cfg.get("close"))
        return f"{o[0]:02d}:{o[1]:02d}–{c[0]:02d}:{c[1]:02d}" if o and c else "closed"

    runs: list[list] = []          # [first_day, last_day, slot]
    for day in _DAY_KEYS:
        s = slot(day)
        if runs and runs[-1][2] == s:
            runs[-1][1] = day
        else:
            runs.append([day, day, s])
    parts = []
    for first, last, s in runs:
        days = _DAY_NAMES[first] if first == last else f"{_DAY_NAMES[first]}–{_DAY_NAMES[last]}"
        parts.append(f"{days} {s}")
    return " · ".join(parts)
