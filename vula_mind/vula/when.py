"""
vula/when.py — "Thursday at 10", "tomorrow 9:30", "15 Nov 2pm", "in 2 hours" → a real SAST time.

Deterministic, never the model's own date arithmetic (it doesn't know today's date). Built
12 Nov 2026 for meetings (vula/meetings.py) after the reminder resolver turned out to read
"Friday at 10am" as 10:25 UTC (12:25 in South Africa: dateutil kept the current minutes and
used UTC) and "tomorrow 9:30" as today (dateutil ignores "tomorrow").

resolve() returns {"at": aware datetime (Africa/Johannesburg), "has_date", "has_time"} or None
when the phrase names no day and no time. It never guesses a missing part silently: the caller
sees has_date / has_time and asks for what's missing.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

SAST = ZoneInfo("Africa/Johannesburg")

_MONTHS = {m: i + 1 for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))}
_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"

_REL_RE = re.compile(r"\bin\s+(\d{1,3}|an?|one|half an?)\s*(min(?:ute)?s?|hours?|hrs?|days?)\b", re.I)
_ISO_RE = re.compile(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b")
_DMY_RE = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?\b")          # SA: day first
_D_MON_RE = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MON}(?:\s+(20\d{{2}}))?", re.I)
_MON_D_RE = re.compile(rf"\b{_MON}\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(20\d{{2}}))?\b", re.I)
_WEEKDAY_RE = re.compile(r"\b(next\s+|this\s+)?(mon|tue|wed|thu|fri|sat|sun)[a-z]*\b", re.I)
_DAYWORD_RE = re.compile(r"\b(day after tomorrow|tomorrow|tmrw|tmr|today|tonight)\b", re.I)

_HM_RE = re.compile(r"\b(\d{1,2})[:h](\d{2})\s*(am|pm|a\.m\.|p\.m\.)?", re.I)
_H_AMPM_RE = re.compile(r"\b(\d{1,2})\s*(am|pm|a\.m\.|p\.m\.)", re.I)
_AT_H_RE = re.compile(r"\b(?:at|@|from)\s*(\d{1,2})(?![\d:/.-])(?!\s*(?:st|nd|rd|th)\b)", re.I)
_NOON_RE = re.compile(r"\b(noon|midday|lunch ?time)\b", re.I)


def now_sast() -> datetime:
    return datetime.now(SAST)


def _cut(text: str, m: "re.Match") -> str:
    return text[:m.start()] + " " + text[m.end():]


def _date(text: str, today: date) -> Tuple[Optional[date], str]:
    """The day named in the text (and the text with it cut out, so its numbers aren't read as a
    time), or (None, text)."""
    m = _DAYWORD_RE.search(text)
    if m:
        w = m.group(1).lower()
        add = 2 if w.startswith("day after") else 1 if w.startswith("t") and w not in ("today", "tonight") else 0
        return today + timedelta(days=add), _cut(text, m)
    m = _ISO_RE.search(text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))), _cut(text, m)
        except ValueError:
            return None, text
    for rx, d_i, m_i, y_i in ((_D_MON_RE, 1, 2, 3), (_MON_D_RE, 2, 1, 3)):
        m = rx.search(text)
        if m:
            month = _MONTHS.get(m.group(m_i).lower()[:3])
            return _year(today, int(m.group(d_i)), month, m.group(y_i)), _cut(text, m)
    m = _DMY_RE.search(text)
    if m and not re.match(r"\s*(?:am|pm)", text[m.end():], re.I):
        return _year(today, int(m.group(1)), int(m.group(2)), m.group(3)), _cut(text, m)
    m = _WEEKDAY_RE.search(text)
    if m:
        want = _WEEKDAYS.index(m.group(2).lower()[:3])
        ahead = (want - today.weekday()) % 7
        if ahead == 0 and (m.group(1) or "").strip().lower() == "next":
            ahead = 7
        return today + timedelta(days=ahead), _cut(text, m)
    return None, text


def _year(today: date, day: int, month: Optional[int], year: Optional[str]) -> Optional[date]:
    if not month:
        return None
    try:
        if year:
            y = int(year)
            return date(y + 2000 if y < 100 else y, month, day)
        d = date(today.year, month, day)
        return d if d >= today else date(today.year + 1, month, day)   # "15 Jan" in November → next year
    except ValueError:
        return None


def _time(text: str) -> Optional[Tuple[int, int]]:
    def ampm(h: int, flag: Optional[str]) -> int:
        f = (flag or "").lower().replace(".", "")
        if f == "pm" and h < 12:
            return h + 12
        if f == "am" and h == 12:
            return 0
        return h
    m = _HM_RE.search(text)
    if m:
        h, mins = ampm(int(m.group(1)), m.group(3)), int(m.group(2))
        return (h, mins) if h < 24 and mins < 60 else None
    m = _H_AMPM_RE.search(text)
    if m:
        h = ampm(int(m.group(1)), m.group(2))
        return (h, 0) if 0 < int(m.group(1)) <= 12 else None
    if _NOON_RE.search(text):
        return 12, 0
    m = _AT_H_RE.search(text)
    if m:
        h = int(m.group(1))
        if 1 <= h <= 6:                 # "at 3" in a working day is 15:00
            h += 12
        return (h, 0) if h < 24 else None
    return None


def resolve(phrase: Optional[str], now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    text = (phrase or "").strip()
    if not text:
        return None
    now = (now or now_sast()).astimezone(SAST)
    rel = _REL_RE.search(text)
    if rel:
        n_raw, unit = rel.group(1).lower(), rel.group(2).lower()
        n = 0.5 if n_raw.startswith("half") else 1 if n_raw in ("a", "an", "one") else float(n_raw)
        minutes = n * (1 if unit.startswith("min") else 1440 if unit.startswith("day") else 60)
        return {"at": (now + timedelta(minutes=minutes)).replace(second=0, microsecond=0),
                "has_date": True, "has_time": True}
    day, rest = _date(text, now.date())
    hm = _time(rest)
    if day is None and hm is None:
        return None
    on = day or now.date()
    h, m = hm if hm else (now.hour, now.minute)
    at = datetime(on.year, on.month, on.day, h, m, tzinfo=SAST)
    if day is None and at < now:             # "at 3pm" when 3pm has passed → tomorrow
        at += timedelta(days=1)
    elif (at < now and day == now.date() and _WEEKDAY_RE.search(text) and not _DAYWORD_RE.search(text)):
        at += timedelta(days=7)              # "Thursday 7am" said on Thursday at 9 → next Thursday
    return {"at": at, "has_date": day is not None, "has_time": hm is not None}


def reminder_due(phrase: Optional[str], now: Optional[datetime] = None) -> Optional[str]:
    """For a reminder: a day with no time means 08:00 that day; a time with no day means the next
    time it comes round. ISO string (UTC offset) or None."""
    r = resolve(phrase, now)
    if not r:
        return None
    at = r["at"]
    if r["has_date"] and not r["has_time"]:
        at = at.replace(hour=8, minute=0)
    return at.isoformat()


def say(at: datetime, now: Optional[datetime] = None) -> str:
    """'Thu 13 Nov at 10:00', or 'today at 10:00' / 'tomorrow at 10:00'."""
    at = at.astimezone(SAST)
    today = (now or now_sast()).astimezone(SAST).date()
    if at.date() == today:
        day = "today"
    elif at.date() == today + timedelta(days=1):
        day = "tomorrow"
    else:
        day = at.strftime("%a %-d %b") + (f" {at.year}" if at.year != today.year else "")
    return f"{day} at {at.strftime('%H:%M')}"
