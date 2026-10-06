"""Reminder schedule: fair-use rules as properties (never > 3, never > 1 per day, never outside 08:00-20:00 SAST)."""
from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import given, strategies as st

from vula.tap.core import matching as m
from vula.tap.core import reminders as r
from vula.tap.core import states as s

UTC = timezone.utc


def sast(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=r.SAST).astimezone(UTC)


def run_sequence(abandoned, max_n, lateness_minutes=()):
    """Simulate the sweeper: it wakes up some (random) time after each reminder becomes due."""
    sent, guard = [], 0
    while True:
        due = r.next_due(abandoned, sent, max_n)
        if due is None:
            return sent
        late = timedelta(minutes=lateness_minutes[len(sent) % len(lateness_minutes)] if lateness_minutes else 0)
        now = due + late
        due2 = r.next_due(abandoned, sent, max_n, now=now)
        sent.append(due2)
        guard += 1
        assert guard <= 5, "sequence did not terminate"


def test_the_spec_example_afternoon():
    a = sast(2026, 10, 6, 14, 0)
    seq = run_sequence(a, 3)
    assert seq[0] == a + timedelta(minutes=10)                                 # 14:10
    assert seq[1] == sast(2026, 10, 7, 8, 0)                                   # next morning
    assert seq[2] == a + timedelta(days=3)                                     # day 3, 14:00
    assert [x.astimezone(r.SAST).hour for x in seq] == [14, 8, 14]


def test_evening_abandon_waits_for_morning():
    a = sast(2026, 10, 6, 21, 30)                                              # after 20:00
    seq = run_sequence(a, 3)
    assert seq[0] == sast(2026, 10, 7, 8, 0)                                   # held until 08:00
    assert seq[1] == sast(2026, 10, 8, 8, 0)                                   # not the same day as #1
    # the third is three days after the abandon (21:30 Fri), which is after hours, so it waits for 08:00 Sat
    assert seq[2] == sast(2026, 10, 10, 8, 0) and r.in_window(seq[2])


def test_early_morning_abandon():
    a = sast(2026, 10, 6, 3, 0)
    assert run_sequence(a, 3)[0] == sast(2026, 10, 6, 8, 0)


def test_window_edges():
    assert r.in_window(sast(2026, 10, 6, 8, 0)) and r.in_window(sast(2026, 10, 6, 19, 59))
    assert not r.in_window(sast(2026, 10, 6, 7, 59)) and not r.in_window(sast(2026, 10, 6, 20, 0))
    assert r.next_window_start(sast(2026, 10, 6, 20, 0)) == sast(2026, 10, 7, 8, 0)
    assert r.next_window_start(sast(2026, 10, 6, 7, 0)) == sast(2026, 10, 6, 8, 0)


def test_zero_reminders_means_none():
    assert r.next_due(sast(2026, 10, 6, 14), [], 0) is None


def test_fewer_reminders_stop_early():
    a = sast(2026, 10, 6, 14)
    assert len(run_sequence(a, 1)) == 1 and len(run_sequence(a, 2)) == 2
    assert r.is_last(1, 1) and r.is_last(2, 2) and not r.is_last(1, 3) and r.is_last(3, 3)


@given(st.datetimes(min_value=datetime(2026, 1, 1), max_value=datetime(2027, 12, 31)),
       st.integers(0, 3), st.lists(st.integers(0, 3000), min_size=0, max_size=3))
def test_fair_use_properties(naive, max_n, lateness):
    a = naive.replace(tzinfo=UTC)
    seq = run_sequence(a, max_n, lateness)
    assert len(seq) == max_n                                                   # never more than the cap
    assert all(r.in_window(t) for t in seq)                                    # never outside 08:00-20:00 SAST
    days = [t.astimezone(r.SAST).date() for t in seq]
    assert len(set(days)) == len(days)                                         # at most one per calendar day
    assert seq == sorted(seq)
    if seq:
        assert seq[0] >= a + r.FIRST_DELAY
    if len(seq) > 1:
        assert seq[1] >= r._morning_after(a)
    if len(seq) > 2:
        assert seq[2] >= a + r.THIRD_DELAY


@given(st.datetimes(min_value=datetime(2026, 1, 1), max_value=datetime(2027, 12, 31)), st.integers(0, 40))
def test_next_due_never_in_the_past_or_outside_window(naive, days_later):
    now = naive.replace(tzinfo=UTC) + timedelta(days=days_later)
    a = naive.replace(tzinfo=UTC)
    due = r.next_due(a, [], 3, now=now)
    assert due >= now and r.in_window(due)


def test_sent_today():
    now = sast(2026, 10, 6, 15)
    assert r.sent_today([sast(2026, 10, 6, 9)], now) and not r.sent_today([sast(2026, 10, 5, 19)], now)


# ── the abandoned states and matching ────────────────────────────────────────────────────────
def test_abandoned_bill_state_transitions():
    assert s.bill_next("claimed", "abandon") == "abandoned"
    assert s.bill_next("abandoned", "reminders_exhausted") == "needs_follow_up"
    for st_ in ("abandoned", "needs_follow_up"):
        assert s.bill_next(st_, "pay") == "paid"
        assert s.bill_next(st_, "reclaim") == "claimed"
        assert s.bill_next(st_, "release") == "open"
        assert s.bill_next(st_, "mark_paid_other") == "paid_other"
        assert s.bill_next(st_, "write_off") == "written_off"
        assert s.bill_next(st_, "cancel") == "cancelled"
    for dead in ("paid", "paid_other", "written_off", "cancelled", "expired"):
        for ev in ("reclaim", "release", "pay"):
            with pytest.raises(s.IllegalTransition):
                s.bill_next(dead, ev)


@pytest.mark.parametrize("status", ["abandoned", "needs_follow_up"])
def test_customer_who_left_picks_the_bill_up_again(status):
    bills = [m.BillView("1", status, None, "me")]
    d = m.resolve_tap(tag_mode="appointment", payer_hash="me", bills=bills)
    assert (d.kind, d.bill_id) == (m.CLAIM, "1")
    other = m.resolve_tap(tag_mode="appointment", payer_hash="someone", bills=bills)
    assert other.kind == m.LOCKED
