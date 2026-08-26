"""Tests for the weekly-call-sheet tools added to commerce_admin.py: configure_call_sheet,
view_call_sheet, update_call_sheet (migration 138)."""
import uuid

import pytest

import core.skills.commerce_admin as ca
from core.skills.commerce_admin import CommerceAdminSkill

TID = "test-tenant"
CTX = {"phone": "27821234567", "caller_name": "Ian", "tenant_id": TID}


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
    row = {"id": str(uuid.uuid4()), "tenant_id": TID, "whatsapp": "27821234567", "name": "Ian",
           "role": "sales_rep", "active": True}
    row.update(over)
    client.store.setdefault("vula_team_members", []).append(row)
    return row


# ── configure_call_sheet ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_configure_call_sheet_sets_recipient_email(skill, fake_client):
    _seed_rep(fake_client)
    res = await skill._configure_call_sheet(TID, {"recipient_email": "sarah@gerflor.co.za"}, CTX)
    assert res["saved"] is True
    assert "sarah@gerflor.co.za" in res["message"]
    rep = fake_client.store["vula_team_members"][0]
    assert rep["call_sheet_recipient_email"] == "sarah@gerflor.co.za"


@pytest.mark.asyncio
async def test_configure_call_sheet_rejects_malformed_email(skill, fake_client):
    _seed_rep(fake_client)
    res = await skill._configure_call_sheet(TID, {"recipient_email": "not-an-email"}, CTX)
    assert "error" in res


@pytest.mark.asyncio
async def test_configure_call_sheet_clears_recipient_with_empty_string(skill, fake_client):
    _seed_rep(fake_client, call_sheet_recipient_email="old@example.com")
    res = await skill._configure_call_sheet(TID, {"recipient_email": ""}, CTX)
    assert res["saved"] is True
    rep = fake_client.store["vula_team_members"][0]
    assert rep["call_sheet_recipient_email"] is None


@pytest.mark.asyncio
async def test_configure_call_sheet_sets_day_and_time(skill, fake_client):
    _seed_rep(fake_client)
    res = await skill._configure_call_sheet(TID, {"day_of_week": "Monday", "time": "08:00"}, CTX)
    assert res["saved"] is True
    rep = fake_client.store["vula_team_members"][0]
    assert rep["call_sheet_day_of_week"] == 0
    assert rep["call_sheet_hour"] == 8
    assert rep["call_sheet_minute"] == 0


@pytest.mark.asyncio
async def test_configure_call_sheet_rejects_bad_time_format(skill, fake_client):
    _seed_rep(fake_client)
    res = await skill._configure_call_sheet(TID, {"time": "5pm"}, CTX)
    assert "error" in res


@pytest.mark.asyncio
async def test_configure_call_sheet_rejects_bad_channel(skill, fake_client):
    _seed_rep(fake_client)
    res = await skill._configure_call_sheet(TID, {"channel": "sms"}, CTX)
    assert "error" in res


@pytest.mark.asyncio
async def test_configure_call_sheet_resolves_recipient_by_contact_name(skill, fake_client):
    _seed_rep(fake_client)
    fake_client.store.setdefault("commerce_contacts", []).append(
        {"tenant_id": TID, "name": "Sarah Manager", "phone": "27829998888", "email": "sarah@gerflor.co.za"})
    res = await skill._configure_call_sheet(TID, {"recipient_name_or_phone": "Sarah"}, CTX)
    assert res["saved"] is True
    rep = fake_client.store["vula_team_members"][0]
    assert rep["call_sheet_recipient_email"] == "sarah@gerflor.co.za"


@pytest.mark.asyncio
async def test_configure_call_sheet_unknown_contact_name_errors(skill, fake_client):
    _seed_rep(fake_client)
    res = await skill._configure_call_sheet(TID, {"recipient_name_or_phone": "Nobody"}, CTX)
    assert "error" in res


@pytest.mark.asyncio
async def test_configure_call_sheet_requires_at_least_one_field(skill, fake_client):
    _seed_rep(fake_client)
    res = await skill._configure_call_sheet(TID, {}, CTX)
    assert "error" in res


# ── view_call_sheet / update_call_sheet (delegate to vula.commerce.call_sheet) ─────

@pytest.mark.asyncio
async def test_view_call_sheet_formats_entries(skill, monkeypatch):
    def fake_get_or_create(tid, phone):
        return {"entries": [{"id": "e1", "text": "Met Dick", "created_at": "2026-08-20T10:00:00+00:00"}]}
    monkeypatch.setattr("vula.commerce.call_sheet.get_or_create_open_call_sheet", fake_get_or_create)
    res = await skill._view_call_sheet(TID, CTX)
    assert res["count"] == 1
    assert "Met Dick" in res["formatted"]


@pytest.mark.asyncio
async def test_update_call_sheet_previews_without_confirm(skill, monkeypatch):
    monkeypatch.setattr("vula.commerce.call_sheet.get_or_create_open_call_sheet",
                         lambda tid, phone: {"entries": []})

    async def fake_parse(entries, instruction):
        return {"action": "add", "entry_id": None, "text": "Sarah wants a Q4 review"}
    monkeypatch.setattr("vula.commerce.call_sheet.parse_update_instruction", fake_parse)

    applied = {"n": 0}
    monkeypatch.setattr("vula.commerce.call_sheet.apply_edit",
                        lambda *a, **kw: applied.__setitem__("n", applied["n"] + 1))

    res = await skill._update_call_sheet(TID, {"instruction": "add a note about Q4"}, CTX)
    assert res.get("preview") is True
    assert applied["n"] == 0  # no write without confirm=true


@pytest.mark.asyncio
async def test_update_call_sheet_applies_with_confirm(skill, monkeypatch):
    monkeypatch.setattr("vula.commerce.call_sheet.get_or_create_open_call_sheet",
                         lambda tid, phone: {"entries": []})

    async def fake_parse(entries, instruction):
        return {"action": "add", "entry_id": None, "text": "Sarah wants a Q4 review"}
    monkeypatch.setattr("vula.commerce.call_sheet.parse_update_instruction", fake_parse)
    monkeypatch.setattr("vula.commerce.call_sheet.apply_edit",
                        lambda *a, **kw: {"entries": [{"id": "e1", "text": "Sarah wants a Q4 review"}]})

    res = await skill._update_call_sheet(TID, {"instruction": "add a note", "confirm": True}, CTX)
    assert res["applied"] is True
    assert res["count"] == 1


@pytest.mark.asyncio
async def test_update_call_sheet_passes_through_parse_error(skill, monkeypatch):
    monkeypatch.setattr("vula.commerce.call_sheet.get_or_create_open_call_sheet",
                         lambda tid, phone: {"entries": []})

    async def fake_parse(entries, instruction):
        return {"error": "Couldn't tell which entry you meant."}
    monkeypatch.setattr("vula.commerce.call_sheet.parse_update_instruction", fake_parse)

    res = await skill._update_call_sheet(TID, {"instruction": "fix it"}, CTX)
    assert "error" in res


# ── dispatch routing smoke tests ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_routes_configure_call_sheet(skill, fake_client):
    _seed_rep(fake_client)
    res = await skill._dispatch_tool("configure_call_sheet", {"recipient_email": "x@y.com"}, CTX)
    assert res["saved"] is True


@pytest.mark.asyncio
async def test_dispatch_routes_view_call_sheet(skill, monkeypatch):
    monkeypatch.setattr("vula.commerce.call_sheet.get_or_create_open_call_sheet",
                         lambda tid, phone: {"entries": []})
    res = await skill._dispatch_tool("view_call_sheet", {}, CTX)
    assert res["count"] == 0


@pytest.mark.asyncio
async def test_dispatch_routes_update_call_sheet(skill, monkeypatch):
    monkeypatch.setattr("vula.commerce.call_sheet.get_or_create_open_call_sheet",
                         lambda tid, phone: {"entries": []})

    async def fake_parse(entries, instruction):
        return {"error": "too vague"}
    monkeypatch.setattr("vula.commerce.call_sheet.parse_update_instruction", fake_parse)
    res = await skill._dispatch_tool("update_call_sheet", {"instruction": "do something"}, CTX)
    assert "error" in res
