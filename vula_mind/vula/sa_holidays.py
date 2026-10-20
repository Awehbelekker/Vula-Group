"""South African public holidays (Public Holidays Act 36 of 1994), worked out for any year.

The ten fixed dates, Good Friday and Family Day (from Easter), and the Act's rule that a holiday
falling on a Sunday is also observed on the Monday. One-off days the President declares (an
election day, an extra day at year end) go in EXTRA as they're announced.

Used by the staff notices (vula/staff_notices.py: "tomorrow is a public holiday") and by tax
due dates (vula/commerce/tax.py: the last business day before a deadline skips them).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, Optional

_FIXED = [
    (1, 1, "New Year's Day"),
    (3, 21, "Human Rights Day"),
    (4, 27, "Freedom Day"),
    (5, 1, "Workers' Day"),
    (6, 16, "Youth Day"),
    (8, 9, "National Women's Day"),
    (9, 24, "Heritage Day"),
    (12, 16, "Day of Reconciliation"),
    (12, 25, "Christmas Day"),
    (12, 26, "Day of Goodwill"),
]

# One-off public holidays declared for a particular year: {date: name}.
EXTRA: Dict[date, str] = {}


def easter_sunday(year: int) -> date:
    """Western Easter (anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    lv = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * lv) // 451
    month = (h + lv - 7 * m + 114) // 31
    day = (h + lv - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def holidays(year: int) -> Dict[date, str]:
    """Every public holiday in `year` (including the Monday after a Sunday holiday)."""
    out: Dict[date, str] = {date(year, m, d): name for m, d, name in _FIXED}
    easter = easter_sunday(year)
    out[easter - timedelta(days=2)] = "Good Friday"
    out[easter + timedelta(days=1)] = "Family Day"
    out.update({d: n for d, n in EXTRA.items() if d.year == year})
    for d, name in list(out.items()):
        if d.weekday() == 6:                      # Sunday → the Monday is a holiday too
            monday = d + timedelta(days=1)
            out.setdefault(monday, f"{name} (observed)")
    return dict(sorted(out.items()))


def holiday_name(d: date) -> Optional[str]:
    return holidays(d.year).get(d)


def is_business_day(d: date) -> bool:
    return d.weekday() < 5 and holiday_name(d) is None


# Days worth a message that aren't public holidays — each one off until a business switches it on
# (vula/staff_notices.py). (id, name, default line); the date is worked out per year below.
OBSERVANCES = [
    ("valentines", "Valentine's Day", "happy Valentine's Day! ❤️"),
    ("mothers_day", "Mother's Day", "happy Mother's Day to all the moms on the team! 💐"),
    ("fathers_day", "Father's Day", "happy Father's Day to all the dads on the team! 👔"),
    ("mandela_day", "Mandela Day", "it's Mandela Day — 67 minutes for a good cause. 🧡"),
    ("spring_day", "Spring Day", "happy Spring Day! 🌸"),
    ("christmas_eve", "Christmas Eve", "Merry Christmas — thank you for everything this year! 🎄"),
    ("new_years_eve", "New Year's Eve", "happy New Year when it comes — see you in the new year! 🎉"),
]


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def observances(year: int) -> Dict[date, tuple]:
    """{date: (id, name, default line)} for `year`."""
    when = {
        "valentines": date(year, 2, 14),
        "mothers_day": _nth_weekday(year, 5, 6, 2),     # 2nd Sunday of May
        "fathers_day": _nth_weekday(year, 6, 6, 3),     # 3rd Sunday of June
        "mandela_day": date(year, 7, 18),
        "spring_day": date(year, 9, 1),
        "christmas_eve": date(year, 12, 24),
        "new_years_eve": date(year, 12, 31),
    }
    return {when[i]: (i, name, line) for i, name, line in OBSERVANCES}
