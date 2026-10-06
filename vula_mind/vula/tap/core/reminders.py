"""Unpaid-bill reminder schedule (pure). Fair-use rules from the spec:

  * at most 3 reminders per bill, at most ONE per SAST calendar day;
  * only between 08:00 and 20:00 SAST (anything due outside waits for 08:00);
  * #1 about 10 minutes after the customer left, #2 the next morning (08:00), #3 three days after;
  * the last one says it is the last (`is_last`).
Africa/Johannesburg has no daylight saving, so a fixed +02:00 offset is exact.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Optional, Sequence

SAST = timezone(timedelta(hours=2))
OPEN_HOUR, CLOSE_HOUR = 8, 20
FIRST_DELAY = timedelta(minutes=10)
THIRD_DELAY = timedelta(days=3)
MAX_REMINDERS = 3


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def in_window(d: datetime) -> bool:
    return OPEN_HOUR <= _aware(d).astimezone(SAST).hour < CLOSE_HOUR


def next_window_start(d: datetime) -> datetime:
    """`d` itself when inside the sending window, else the next 08:00 SAST."""
    d = _aware(d)
    local = d.astimezone(SAST)
    if in_window(d):
        return d
    day = local.date() if local.hour < OPEN_HOUR else local.date() + timedelta(days=1)
    return datetime.combine(day, time(OPEN_HOUR), tzinfo=SAST).astimezone(timezone.utc)


def _morning_after(d: datetime) -> datetime:
    local = _aware(d).astimezone(SAST)
    return datetime.combine(local.date() + timedelta(days=1), time(OPEN_HOUR), tzinfo=SAST).astimezone(timezone.utc)


def target(abandoned_at: datetime, index: int) -> datetime:
    """Unadjusted target for reminder `index` (0-based)."""
    a = _aware(abandoned_at)
    return [a + FIRST_DELAY, _morning_after(a), a + THIRD_DELAY][index]


def next_due(abandoned_at: datetime, sent: Sequence[datetime], max_n: int, now: Optional[datetime] = None) -> Optional[datetime]:
    """When the next automatic reminder may go out, or None when the sequence is complete.
    `sent` = send times of the automatic reminders already sent (oldest first). If `now` is given the
    result is never earlier than the first allowed moment at/after `now`."""
    max_n = max(0, min(max_n, MAX_REMINDERS))
    n = len(sent)
    if n >= max_n:
        return None
    due = target(abandoned_at, n)
    if now is not None:
        due = max(due, _aware(now))
    due = next_window_start(due)
    if sent:                                           # one per calendar day
        last_day = _aware(sent[-1]).astimezone(SAST).date()
        while due.astimezone(SAST).date() <= last_day:
            due = next_window_start(_morning_after(due))
    return due


def is_last(seq: int, max_n: int) -> bool:
    return seq >= max(1, min(max_n, MAX_REMINDERS))


def sent_today(sent: Sequence[datetime], now: datetime) -> bool:
    today = _aware(now).astimezone(SAST).date()
    return any(_aware(s).astimezone(SAST).date() == today for s in sent)
