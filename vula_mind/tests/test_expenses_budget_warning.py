"""Tests for vula/commerce/expenses.py::budget_warning_line — the month-to-date 90%/100%
budget-warning check (2026-08-28). Migration 139 added expense_budget_cents/
expense_budget_warned_month/expense_budget_warned_pct to vula_team_members, and
configure_expense_sheet's own tool description already promised this warning, but nothing
computed or fired it until now. Uses the same in-memory fake Supabase client pattern as
tests/test_expense_sheet.py/test_call_sheet.py (duplicated here rather than shared, matching
this codebase's established per-test-file fixture convention).
"""
import uuid

import pytest

from vula.commerce import expenses

TID = "test-tenant"
REP_PHONE = "27821234567"


class _Result:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table):
        self.table = table
        self.filters = []
        self._gte = []
        self._lte = []
        self._limit = None
        self._patch = None

    def select(self, *_a, **_kw):
        return self

    def eq(self, key, val):
        self.filters.append((key, val))
        return self

    def gte(self, key, val):
        self._gte.append((key, val))
        return self

    def lte(self, key, val):
        self._lte.append((key, val))
        return self

    def order(self, *_a, **_kw):
        return self

    def limit(self, n):
        self._limit = n
        return self

    def _matches(self, row):
        if not all(row.get(k) == v for k, v in self.filters):
            return False
        if not all((row.get(k) or "") >= v for k, v in self._gte):
            return False
        if not all((row.get(k) or "") <= v for k, v in self._lte):
            return False
        return True

    def update(self, patch_dict):
        self._patch = patch_dict
        return self

    def execute(self):
        rows = [r for r in self.table.rows if self._matches(r)]
        if self._patch is not None:
            for r in rows:
                r.update(self._patch)
        if self._limit:
            rows = rows[: self._limit]
        return _Result(rows)


class _FakeTable:
    def __init__(self, name, store):
        self.rows = store.setdefault(name, [])


class _FakeClient:
    def __init__(self):
        self.store = {}

    def table(self, name):
        return _FakeQuery(_FakeTable(name, self.store))


@pytest.fixture
def fake_client(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(expenses, "_client", lambda: client)
    monkeypatch.setattr(expenses, "_now", lambda: "2026-08-15T12:00:00+00:00")
    return client


def _rep(**over):
    row = {"id": str(uuid.uuid4()), "tenant_id": TID, "whatsapp": REP_PHONE,
           "role": "sales_rep", "expense_budget_cents": 200000,  # R2000
           "expense_budget_warned_month": None, "expense_budget_warned_pct": None}
    row.update(over)
    return row


def _claim(amount_cents, date="2026-08-10", **over):
    row = {"id": str(uuid.uuid4()), "tenant_id": TID, "paid_by": REP_PHONE,
           "date": date, "amount_cents": amount_cents}
    row.update(over)
    return row


def test_no_matching_team_member_row_returns_none(fake_client):
    assert expenses.budget_warning_line(TID, REP_PHONE) is None


def test_non_sales_rep_role_returns_none(fake_client):
    fake_client.store["vula_team_members"] = [_rep(role="owner")]
    fake_client.store["commerce_expenses"] = [_claim(190000)]
    assert expenses.budget_warning_line(TID, REP_PHONE) is None


def test_no_budget_set_returns_none(fake_client):
    fake_client.store["vula_team_members"] = [_rep(expense_budget_cents=None)]
    fake_client.store["commerce_expenses"] = [_claim(190000)]
    assert expenses.budget_warning_line(TID, REP_PHONE) is None


def test_below_90_percent_returns_none(fake_client):
    fake_client.store["vula_team_members"] = [_rep()]
    fake_client.store["commerce_expenses"] = [_claim(100000)]  # 50%
    assert expenses.budget_warning_line(TID, REP_PHONE) is None


def test_crossing_90_percent_first_time_warns_and_stamps(fake_client):
    fake_client.store["vula_team_members"] = [_rep()]
    fake_client.store["commerce_expenses"] = [_claim(190000)]  # 95%
    line = expenses.budget_warning_line(TID, REP_PHONE)
    assert line is not None
    assert "90%" in line
    row = fake_client.store["vula_team_members"][0]
    assert row["expense_budget_warned_month"] == "2026-08"
    assert row["expense_budget_warned_pct"] == 90


def test_crossing_100_percent_first_time_warns_at_100(fake_client):
    fake_client.store["vula_team_members"] = [_rep()]
    fake_client.store["commerce_expenses"] = [_claim(250000)]  # 125%
    line = expenses.budget_warning_line(TID, REP_PHONE)
    assert line is not None
    assert "100%" in line


def test_already_warned_at_90_this_month_does_not_repeat(fake_client):
    fake_client.store["vula_team_members"] = [
        _rep(expense_budget_warned_month="2026-08", expense_budget_warned_pct=90)]
    fake_client.store["commerce_expenses"] = [_claim(190000)]  # still 95%
    assert expenses.budget_warning_line(TID, REP_PHONE) is None


def test_crossing_from_90_to_100_same_month_warns_again(fake_client):
    fake_client.store["vula_team_members"] = [
        _rep(expense_budget_warned_month="2026-08", expense_budget_warned_pct=90)]
    fake_client.store["commerce_expenses"] = [_claim(250000)]  # now 125%
    line = expenses.budget_warning_line(TID, REP_PHONE)
    assert line is not None
    assert "100%" in line
    row = fake_client.store["vula_team_members"][0]
    assert row["expense_budget_warned_pct"] == 100


def test_stale_prior_month_warned_flag_re_warns_this_month(fake_client):
    fake_client.store["vula_team_members"] = [
        _rep(expense_budget_warned_month="2026-07", expense_budget_warned_pct=100)]
    fake_client.store["commerce_expenses"] = [_claim(190000)]  # 95% this month
    line = expenses.budget_warning_line(TID, REP_PHONE)
    assert line is not None
    assert "90%" in line


def test_month_to_date_excludes_prior_month_and_other_rep_claims(fake_client):
    fake_client.store["vula_team_members"] = [_rep()]
    fake_client.store["commerce_expenses"] = [
        _claim(190000, date="2026-08-10"),
        _claim(500000, date="2026-07-31"),          # prior month — excluded
        _claim(500000, date="2026-08-10", paid_by="27829999999"),  # different rep — excluded
    ]
    line = expenses.budget_warning_line(TID, REP_PHONE)
    assert line is not None
    assert "90%" in line  # would be way over 100% if the excluded rows leaked in


def test_db_error_returns_none_never_raises(fake_client, monkeypatch):
    def _boom(*_a, **_kw):
        raise RuntimeError("db down")
    monkeypatch.setattr(fake_client, "table", _boom)
    assert expenses.budget_warning_line(TID, REP_PHONE) is None
