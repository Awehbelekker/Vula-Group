"""Tests for the configure_expense_sheet tool added to commerce_admin.py (migration 140)."""
import uuid

import pytest

import core.skills.commerce_admin as ca
from core.skills.commerce_admin import CommerceAdminSkill

TID = "test-tenant"
CTX = {"phone": "27821234567", "caller_name": "Richard", "tenant_id": TID}


class _Result:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table):
        self.table = table
        self.filters = []
        self._like = None
        self._limit = None
        self._patch = None

    def select(self, *_a, **_kw):
        return self

    def eq(self, key, val):
        self.filters.append((key, val))
        return self

    def ilike(self, key, pattern):
        self._like = (key, pattern.strip("%").lower())
        return self

    def limit(self, n):
        self._limit = n
        return self

    def update(self, patch_dict):
        self._patch = patch_dict
        return self

    def _matches(self, row):
        if not all(row.get(k) == v for k, v in self.filters):
            return False
        if self._like:
            key, needle = self._like
            if needle not in (row.get(key) or "").lower():
                return False
        return True

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
    monkeypatch.setattr(ca.service, "_client", lambda: client)
    return client


@pytest.fixture
def skill():
    return CommerceAdminSkill()


def _seed_rep(client, **over):
    row = {"id": str(uuid.uuid4()), "tenant_id": TID, "whatsapp": "27821234567", "name": "Richard",
           "role": "sales_rep", "active": True}
    row.update(over)
    client.store.setdefault("vula_team_members", []).append(row)
    return row


@pytest.mark.asyncio
async def test_configure_expense_sheet_sets_recipient_email(skill, fake_client):
    _seed_rep(fake_client)
    res = await skill._configure_expense_sheet(TID, {"recipient_email": "accounts@gerflor.co.za"}, CTX)
    assert res["saved"] is True
    assert "accounts@gerflor.co.za" in res["message"]
    rep = fake_client.store["vula_team_members"][0]
    assert rep["expense_sheet_recipient_email"] == "accounts@gerflor.co.za"


@pytest.mark.asyncio
async def test_configure_expense_sheet_rejects_malformed_email(skill, fake_client):
    _seed_rep(fake_client)
    res = await skill._configure_expense_sheet(TID, {"recipient_email": "not-an-email"}, CTX)
    assert "error" in res


@pytest.mark.asyncio
async def test_configure_expense_sheet_sets_day_of_month(skill, fake_client):
    _seed_rep(fake_client)
    res = await skill._configure_expense_sheet(TID, {"day_of_month": 5}, CTX)
    assert res["saved"] is True
    rep = fake_client.store["vula_team_members"][0]
    assert rep["expense_sheet_day_of_month"] == 5


@pytest.mark.asyncio
async def test_configure_expense_sheet_rejects_out_of_range_day(skill, fake_client):
    _seed_rep(fake_client)
    res = await skill._configure_expense_sheet(TID, {"day_of_month": 31}, CTX)
    assert "error" in res


@pytest.mark.asyncio
async def test_configure_expense_sheet_sets_budget(skill, fake_client):
    _seed_rep(fake_client)
    res = await skill._configure_expense_sheet(TID, {"budget_rands": 2000}, CTX)
    assert res["saved"] is True
    assert "R2,000.00" in res["message"]
    rep = fake_client.store["vula_team_members"][0]
    assert rep["expense_budget_cents"] == 200000


@pytest.mark.asyncio
async def test_configure_expense_sheet_clears_budget_with_zero(skill, fake_client):
    _seed_rep(fake_client, expense_budget_cents=200000)
    res = await skill._configure_expense_sheet(TID, {"budget_rands": 0}, CTX)
    assert res["saved"] is True
    rep = fake_client.store["vula_team_members"][0]
    assert rep["expense_budget_cents"] is None


@pytest.mark.asyncio
async def test_configure_expense_sheet_resolves_recipient_by_contact_name(skill, fake_client):
    _seed_rep(fake_client)
    fake_client.store.setdefault("commerce_contacts", []).append(
        {"tenant_id": TID, "name": "Sarah Accounts", "phone": "", "email": "sarah@gerflor.co.za"}
    )
    res = await skill._configure_expense_sheet(TID, {"recipient_name_or_phone": "Sarah"}, CTX)
    assert res["saved"] is True
    rep = fake_client.store["vula_team_members"][0]
    assert rep["expense_sheet_recipient_email"] == "sarah@gerflor.co.za"


@pytest.mark.asyncio
async def test_configure_expense_sheet_unknown_contact_errors(skill, fake_client):
    _seed_rep(fake_client)
    res = await skill._configure_expense_sheet(TID, {"recipient_name_or_phone": "Nobody"}, CTX)
    assert "error" in res


@pytest.mark.asyncio
async def test_configure_expense_sheet_no_fields_errors(skill, fake_client):
    _seed_rep(fake_client)
    res = await skill._configure_expense_sheet(TID, {}, CTX)
    assert "error" in res


@pytest.mark.asyncio
async def test_configure_expense_sheet_requires_phone_in_context(skill, fake_client):
    res = await skill._configure_expense_sheet(TID, {"recipient_email": "a@b.com"}, {})
    assert "error" in res


@pytest.mark.asyncio
async def test_configure_expense_sheet_dispatch_routes_correctly(skill, fake_client):
    _seed_rep(fake_client)
    res = await skill._dispatch_tool("configure_expense_sheet",
                                      {"day_of_month": 3}, CTX)
    assert res["saved"] is True
