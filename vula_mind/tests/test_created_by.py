"""Who made each quote, invoice and order (vula/acting.py, migration 260), and sales per person."""
import asyncio
from datetime import datetime

import pytest

from vula import acting
from vula.when import SAST

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=SAST)


# ── who is acting ─────────────────────────────────────────────────────────────────────────

def test_acting_as_is_scoped_and_cleaned():
    assert acting.current() is None
    with acting.acting_as("+27 82 000 0002"):
        assert acting.current() == "27820000002"
        with acting.acting_as("Thandi@Biz.co.za"):
            assert acting.current() == "thandi@biz.co.za"
        assert acting.current() == "27820000002"
    assert acting.current() is None


def test_from_dashboard_prefers_the_members_number(monkeypatch):
    import vula.api.tenant_auth as ta

    async def scope(auth, tid):
        return {"member": {"whatsapp": "082 000 0003"}, "email": "x@y.co"}
    monkeypatch.setattr(ta, "member_scope", scope)
    assert asyncio.run(acting.from_dashboard("Bearer t", "t1")) == "0820000003"

    async def scope2(auth, tid):
        return {"member": None, "email": "Owner@Biz.co"}
    monkeypatch.setattr(ta, "member_scope", scope2)
    assert asyncio.run(acting.from_dashboard("Bearer t", "t1")) == "owner@biz.co"


def test_every_whatsapp_tool_call_runs_as_the_sender(monkeypatch):
    from core.skills.commerce_admin import CommerceAdminSkill
    seen = []

    async def inner(self, name, args, ctx):
        seen.append(acting.current())
        return {}
    monkeypatch.setattr(CommerceAdminSkill, "_dispatch_tool_as", inner)
    asyncio.run(CommerceAdminSkill()._dispatch_tool("x", {}, {"tenant_id": "t1", "phone": "+27820000002"}))
    assert seen == ["27820000002"] and acting.current() is None


# ── stamped on create ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def invoices(monkeypatch):
    from vula.commerce import service
    inserted, fail_first = [], {"on": False}

    class _T:
        def insert(self, row):
            self.row = dict(row)
            return self

        def execute(self):
            if fail_first["on"] and "created_by" in self.row:
                fail_first["on"] = False
                raise RuntimeError('column "created_by" does not exist')
            inserted.append(self.row)
            return type("R", (), {"data": [self.row]})()

    async def settings(tid):
        return {"vat_registered": False}

    async def number(tid, doc_type, **k):
        return "Q-1"
    monkeypatch.setattr(service, "_client", lambda: type("C", (), {"table": lambda s, n: _T()})())
    monkeypatch.setattr(service, "get_invoice_settings", settings)
    monkeypatch.setattr(service, "_next_invoice_number", number)
    return inserted, fail_first


DATA = {"doc_type": "quote", "customer_name": "Acme", "line_items": [{"description": "Tiles", "quantity": 1, "unit_price_cents": 100000}]}


def test_create_invoice_records_who_made_it(invoices):
    from vula.commerce import service
    inserted, _ = invoices
    with acting.acting_as("27820000002"):
        asyncio.run(service.create_invoice("t1", dict(DATA)))
    assert inserted[-1]["created_by"] == "27820000002"
    asyncio.run(service.create_invoice("t1", dict(DATA)))                 # no actor (e.g. a job)
    assert "created_by" not in inserted[-1]


def test_an_unmigrated_database_still_invoices(invoices):
    from vula.commerce import service
    inserted, fail_first = invoices
    fail_first["on"] = True
    with acting.acting_as("27820000002"):
        out = asyncio.run(service.create_invoice("t1", dict(DATA)))
    assert out["invoice_number"] == "Q-1" and "created_by" not in inserted[-1]


# ── counted per person ────────────────────────────────────────────────────────────────────

class _Q:
    def __init__(self, store, name):
        self.store, self.name, self.f = store, name, []

    def __getattr__(self, n):
        return lambda *a, **k: self

    def eq(self, k, v): self.f.append(lambda r: r.get(k) == v); return self
    def in_(self, k, vs): self.f.append(lambda r: r.get(k) in vs); return self

    def gte(self, k, v):
        self.f.append(lambda r: r.get(k) is not None and str(r[k]) >= str(v)[:10]); return self

    def lt(self, k, v):
        self.f.append(lambda r: r.get(k) is not None and str(r[k]) < str(v)[:10]); return self

    def execute(self):
        return type("R", (), {"data": [r for r in self.store.get(self.name, []) if all(f(r) for f in self.f)]})()


@pytest.fixture
def team(monkeypatch):
    from vula import team_activity as ta
    import vula.team_index as team_index
    store = {
        "commerce_invoices": [
            {"tenant_id": "t1", "created_by": "27820000002", "doc_type": "quote", "direction": "outbound",
             "total_cents": 1_250_000, "status": "sent", "issue_date": "2026-11-10"},
            {"tenant_id": "t1", "created_by": "27820000002", "doc_type": "quote", "direction": "outbound",
             "total_cents": 50_000, "status": "cancelled", "issue_date": "2026-11-10"},     # not counted
            {"tenant_id": "t1", "created_by": "lindi@biz.co.za", "doc_type": "invoice", "direction": "outbound",
             "total_cents": 400_000, "status": "sent", "issue_date": "2026-11-11"},       # made on the dashboard
            {"tenant_id": "t1", "created_by": "27820000002", "doc_type": "invoice", "direction": "inbound",
             "total_cents": 99_900, "status": "sent", "issue_date": "2026-11-11"},        # a supplier bill: never
        ],
        "commerce_orders": [
            {"tenant_id": "t1", "created_by": "27820000002", "total_cents": 125_000, "status": "paid",
             "created_at": "2026-11-11"},
            {"tenant_id": "t1", "created_by": None, "total_cents": 999_900, "status": "paid",
             "created_at": "2026-11-11"}],                                                # a customer's checkout
    }
    client = type("C", (), {"table": lambda s, n: _Q(store, n)})()
    monkeypatch.setattr(ta, "_client", lambda: client)
    import vula.commerce.service as service
    monkeypatch.setattr(service, "_client", lambda: client)
    members = [{"id": "t", "name": "Thabo", "whatsapp": "27820000002", "role": "sales_rep"},
               {"id": "l", "name": "Lindi", "whatsapp": "27820000003", "email": "Lindi@biz.co.za", "role": "sales_rep"}]
    monkeypatch.setattr(team_index, "active_members", lambda tid: members)
    monkeypatch.setattr(ta, "now_sast", lambda: NOW)
    return ta, members


def test_sales_per_person(team):
    ta, _ = team
    a, b, _ = ta.period("this week", NOW)
    rows = {r["name"]: r for r in ta.team_summary("t1", a, b, now=NOW)}
    assert (rows["Thabo"]["quotes"], rows["Thabo"]["quotes_cents"]) == (1, 1_250_000)
    assert (rows["Thabo"]["orders"], rows["Thabo"]["orders_cents"]) == (1, 125_000)
    assert rows["Thabo"]["invoices"] == 0                                   # the supplier bill isn't a sale
    assert (rows["Lindi"]["invoices"], rows["Lindi"]["invoices_cents"]) == (1, 400_000)   # matched by email
    text = ta.team_text(list(rows.values()), "this week")
    assert "1 quote (R12,500.00)" in text and "1 invoice (R4,000.00)" in text


def test_person_week_shows_what_they_made(team):
    ta, members = team
    a, b, _ = ta.period("this week", NOW)
    text = ta.person_text(ta.person_week("t1", members[0], a, b, NOW), "this week", NOW)
    assert "💰 Made: 1 quote (R12,500.00), 1 order (R1,250.00)" in text


def test_sheets_have_made_by(team):
    from vula.commerce import sheet_builder as sb
    spec = sb.validate({"source": "customer_invoices", "columns": ["number", "customer", "total", "person"],
                        "group_by": "person", "filters": {"period": "this_week", "person": "lindi"}})
    assert spec["filters"]["person"] == "lindi" and spec["group_by"] == "person"
    assert "person" in sb.SOURCES["orders"].fields
