"""Tests for the dashboard-facing expense-sheet config endpoints (2026-08-28) —
GET/POST /v1/commerce/{tenant_id}/admin/expense-sheet[/configure]. Mirrors the call-sheet
endpoints' shape; no direct HTTP-level precedent exists for those either, so this file follows
test_expense_sheet.py's in-memory fake-Supabase-client style, driven through a real
fastapi.testclient.TestClient (matching tests/test_api.py's convention).
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from vula.api.server import app
from vula.commerce import service

client = TestClient(app)

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
    fc = _FakeClient()
    monkeypatch.setattr(service, "_client", lambda: fc)
    monkeypatch.setattr(service, "_now", lambda: "2026-08-15T12:00:00+00:00")
    return fc


def _rep(**over):
    row = {"id": str(uuid.uuid4()), "tenant_id": TID, "whatsapp": REP_PHONE, "name": "Richard",
           "role": "sales_rep", "expense_sheet_recipient_email": "accounts@gerflor.co.za",
           "expense_sheet_day_of_month": 1, "expense_sheet_last_sent_at": None,
           "expense_budget_cents": 200000}
    row.update(over)
    return row


def _claim(amount_cents, date="2026-08-10", **over):
    row = {"id": str(uuid.uuid4()), "tenant_id": TID, "paid_by": REP_PHONE,
           "date": date, "amount_cents": amount_cents}
    row.update(over)
    return row


def test_get_no_matching_row_returns_empty_config_and_zero_spend(fake_client):
    resp = client.get(f"/v1/commerce/{TID}/admin/expense-sheet", params={"rep_phone": REP_PHONE})
    assert resp.status_code == 200
    body = resp.json()
    assert body["config"] == {}
    assert body["mtd_spend_cents"] == 0


def test_get_sums_only_this_rep_this_month(fake_client):
    fake_client.store["vula_team_members"] = [_rep()]
    fake_client.store["commerce_expenses"] = [
        _claim(50000, date="2026-08-10"),
        _claim(30000, date="2026-08-05"),
        _claim(999999, date="2026-07-31"),          # prior month — excluded
        _claim(999999, date="2026-08-10", paid_by="other"),  # other rep — excluded
    ]
    resp = client.get(f"/v1/commerce/{TID}/admin/expense-sheet", params={"rep_phone": REP_PHONE})
    body = resp.json()
    assert body["mtd_spend_cents"] == 80000
    assert body["config"]["expense_sheet_recipient_email"] == "accounts@gerflor.co.za"


def test_get_requires_rep_phone(fake_client):
    resp = client.get(f"/v1/commerce/{TID}/admin/expense-sheet")
    assert resp.status_code == 422  # missing required query param


def test_post_updates_recipient_email(fake_client):
    fake_client.store["vula_team_members"] = [_rep()]
    resp = client.post(f"/v1/commerce/{TID}/admin/expense-sheet/configure",
                        json={"rep_phone": REP_PHONE, "recipient_email": "new@gerflor.co.za"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["saved"] is True
    assert body["config"]["expense_sheet_recipient_email"] == "new@gerflor.co.za"


def test_post_day_of_month_out_of_range_returns_400(fake_client):
    fake_client.store["vula_team_members"] = [_rep()]
    resp = client.post(f"/v1/commerce/{TID}/admin/expense-sheet/configure",
                        json={"rep_phone": REP_PHONE, "day_of_month": 29})
    assert resp.status_code == 400


def test_post_budget_zero_clears_budget(fake_client):
    fake_client.store["vula_team_members"] = [_rep()]
    resp = client.post(f"/v1/commerce/{TID}/admin/expense-sheet/configure",
                        json={"rep_phone": REP_PHONE, "budget_rands": 0})
    assert resp.status_code == 200
    assert resp.json()["config"]["expense_budget_cents"] is None


def test_post_budget_rands_converts_to_cents(fake_client):
    fake_client.store["vula_team_members"] = [_rep()]
    resp = client.post(f"/v1/commerce/{TID}/admin/expense-sheet/configure",
                        json={"rep_phone": REP_PHONE, "budget_rands": 1500})
    assert resp.status_code == 200
    assert resp.json()["config"]["expense_budget_cents"] == 150000


def test_post_unknown_rep_returns_404(fake_client):
    resp = client.post(f"/v1/commerce/{TID}/admin/expense-sheet/configure",
                        json={"rep_phone": "27820000000", "recipient_email": "x@y.com"})
    assert resp.status_code == 404


def test_post_no_fields_returns_400(fake_client):
    fake_client.store["vula_team_members"] = [_rep()]
    resp = client.post(f"/v1/commerce/{TID}/admin/expense-sheet/configure",
                        json={"rep_phone": REP_PHONE})
    assert resp.status_code == 400
