"""Tests for vula/commerce/recurring_bills.py — pure date-math (the most bug-prone piece:
month-end clamping, year rollover). CRUD/scheduler behavior was verified live against prod
during development (create/list/patch/status/jobs-run, plus idempotent process_due)."""
from datetime import date

from vula.commerce.recurring_bills import _advance


def test_advance_weekly():
    assert _advance(date(2026, 7, 1), "weekly") == date(2026, 7, 8)


def test_advance_biweekly():
    assert _advance(date(2026, 7, 1), "biweekly") == date(2026, 7, 15)


def test_advance_monthly_same_day():
    assert _advance(date(2026, 7, 15), "monthly") == date(2026, 8, 15)


def test_advance_monthly_clamps_to_shorter_month():
    # Jan 31 -> Feb has no 31st, should clamp to the 28th (2026 is not a leap year).
    assert _advance(date(2026, 1, 31), "monthly") == date(2026, 2, 28)


def test_advance_monthly_year_rollover():
    assert _advance(date(2026, 12, 5), "monthly") == date(2027, 1, 5)
