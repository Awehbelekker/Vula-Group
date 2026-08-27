"""Tests for core/time_fmt.py — the shared relative-age labelling used by both
vula/commerce/service.py::format_history and vula/chat/history.py::format_for_prompt to fix a
real incident (2026-08-27, gerflor): conversation history had zero timing signal, so a 7-hour-old
message got echoed back as if it were fresh context."""
from datetime import datetime, timedelta, timezone

from core.time_fmt import cutoff_iso, relative_age_label

NOW = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)


def test_just_now_under_a_minute():
    assert relative_age_label((NOW - timedelta(seconds=10)).isoformat(), NOW) == "just now"


def test_minutes_ago():
    assert relative_age_label((NOW - timedelta(minutes=12)).isoformat(), NOW) == "12 min ago"


def test_hours_ago():
    assert relative_age_label((NOW - timedelta(hours=3)).isoformat(), NOW) == "3 hr ago"


def test_boundary_just_under_an_hour_is_minutes():
    assert relative_age_label((NOW - timedelta(minutes=59)).isoformat(), NOW) == "59 min ago"


def test_boundary_just_under_a_day_is_hours():
    assert relative_age_label((NOW - timedelta(hours=23, minutes=59)).isoformat(), NOW) == "23 hr ago"


def test_yesterday():
    assert relative_age_label((NOW - timedelta(hours=30)).isoformat(), NOW) == "yesterday"


def test_several_days_ago():
    assert relative_age_label((NOW - timedelta(days=4)).isoformat(), NOW) == "4 days ago"


def test_future_timestamp_never_shows_negative():
    # clock skew — never show a nonsensical "future" age
    assert relative_age_label((NOW + timedelta(minutes=5)).isoformat(), NOW) == "just now"


def test_unparsable_input_returns_empty_string_not_raise():
    assert relative_age_label("not-a-real-date") == ""
    assert relative_age_label("") == ""
    assert relative_age_label(None) == ""


def test_z_suffix_utc_parses():
    ts = (NOW - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    assert relative_age_label(ts, NOW) == "2 hr ago"


def test_cutoff_iso_is_hours_before_now():
    c = cutoff_iso(24, NOW)
    assert c == (NOW - timedelta(hours=24)).isoformat()
