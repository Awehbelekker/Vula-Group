"""vula/when.py: people's words → a real South African time, never the model's arithmetic."""
from datetime import datetime

import pytest

from vula.when import SAST, reminder_due, resolve, say

NOW = datetime(2026, 11, 12, 9, 0, tzinfo=SAST)          # Thursday 09:00


@pytest.mark.parametrize("phrase,at,has_date,has_time", [
    ("Friday at 10am", "2026-11-13T10:00", True, True),
    ("tomorrow 9:30", "2026-11-13T09:30", True, True),       # dateutil read this as *today*
    ("Thursday at 10", "2026-11-12T10:00", True, True),
    ("thursday 7am", "2026-11-19T07:00", True, True),        # passed today → next Thursday
    ("next thursday 14:00", "2026-11-19T14:00", True, True),
    ("15 Nov 2pm", "2026-11-15T14:00", True, True),
    ("15/11 at 3", "2026-11-15T15:00", True, True),          # SA day-first; "at 3" = 15:00
    ("on the 15th of December at 11h30", "2026-12-15T11:30", True, True),
    ("Jan 5 at 9", "2027-01-05T09:00", True, True),          # January is next year
    ("2026-12-01 08:15", "2026-12-01T08:15", True, True),
    ("noon tomorrow", "2026-11-13T12:00", True, True),
    ("in 2 hours", "2026-11-12T11:00", True, True),
    ("10am", "2026-11-12T10:00", False, True),
    ("8am", "2026-11-13T08:00", False, True),                # passed today → tomorrow
    ("Monday", "2026-11-16T09:00", True, False),             # no time: the caller asks
])
def test_resolve(phrase, at, has_date, has_time):
    r = resolve(phrase, NOW)
    assert r["at"].strftime("%Y-%m-%dT%H:%M") == at and r["at"].utcoffset().total_seconds() == 7200
    assert (r["has_date"], r["has_time"]) == (has_date, has_time)


def test_nothing_to_read():
    assert resolve("hello there", NOW) is None and resolve("", NOW) is None


def test_reminder_day_without_time_is_8am():
    assert reminder_due("Friday", NOW) == "2026-11-13T08:00:00+02:00"
    assert reminder_due("Friday at 10am", NOW) == "2026-11-13T10:00:00+02:00"


def test_say():
    assert say(resolve("tomorrow 9:30", NOW)["at"], NOW) == "tomorrow at 09:30"
    assert say(resolve("15 Nov 2pm", NOW)["at"], NOW) == "Sun 15 Nov at 14:00"


def test_the_reminder_tool_now_uses_sa_time():
    from core.skills.commerce_admin import _resolve_due_at
    out = _resolve_due_at("tomorrow at 10am")
    assert out.endswith("T10:00:00+02:00")
