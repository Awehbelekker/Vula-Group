"""Gap audit 2.3: recurring invoices can be set up, listed and stopped on WhatsApp, behind a
preview, and read back before Vula says it's done. Made-up customers and amounts."""
import pytest

from core.skills import commerce_admin as ca
from core.skills.commerce_admin import CommerceAdminSkill
from vula.commerce import service

INV = {"id": "i1", "invoice_number": "ACM-INV-00012", "doc_type": "invoice", "direction": "outbound",
       "customer_name": "Bokaap Holdings CC", "customer_phone": "27820000009", "issue_date": "2026-10-05",
       "line_items": [{"description": "Retainer", "quantity": 1, "unit_price_cents": 500000, "total_cents": 500000}],
       "vat_rate": 15, "total_cents": 575000}


@pytest.fixture
def store(monkeypatch):
    rows = []

    async def listing(tid):
        return [dict(r) for r in rows]

    async def upsert(tid, data):
        if data.get("id"):
            r = next(r for r in rows if r["id"] == data["id"])
            r.update({k: v for k, v in data.items() if k != "id"})
            return dict(r)
        r = {"id": f"r{len(rows) + 1}", "tenant_id": tid, **data}
        rows.append(r)
        return dict(r)

    async def find(self, tid, number):
        return dict(INV) if number == INV["invoice_number"] else None

    monkeypatch.setattr(service, "list_recurring", listing)
    monkeypatch.setattr(service, "upsert_recurring", upsert)
    monkeypatch.setattr(CommerceAdminSkill, "_find_invoice_by_number", find)
    monkeypatch.setattr(ca, "_readback_gate", lambda *a, **k: None)
    return rows


async def _call(args):
    return await CommerceAdminSkill()._recurring_invoice("acme", args)


@pytest.mark.asyncio
async def test_set_up_previews_then_writes_and_reads_back(store, monkeypatch):
    import datetime as dt

    class D(dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 27)

    monkeypatch.setattr(dt, "date", D)
    prev = await _call({"action": "set_up", "invoice_number": "ACM-INV-00012"})
    assert prev["preview"] and prev["first_new_invoice"] == "2026-11-05" and prev["amount"] == "R5,750.00"
    assert "draft" in prev["message"] and store == []          # nothing written yet
    done = await _call({"action": "set_up", "invoice_number": "ACM-INV-00012", "confirm": True})
    assert done["verified"] and "2026-11-05" in done["reply_verbatim"]
    assert store[0]["cadence"] == "monthly" and store[0]["line_items"] == INV["line_items"]
    listed = await _call({"action": "list"})
    assert listed["recurring"][0]["amount_excl_vat"] == "R5,000.00"


@pytest.mark.asyncio
async def test_an_unknown_invoice_asks_which_one(store):
    out = await _call({"action": "set_up", "invoice_number": "nope"})
    assert out["status"] == "need_info" and store == []


@pytest.mark.asyncio
async def test_stop_previews_then_deactivates_without_deleting(store):
    await _call({"action": "set_up", "invoice_number": "ACM-INV-00012", "cadence": "weekly",
                 "start_date": "2026-11-02", "confirm": True})
    prev = await _call({"action": "stop", "customer_name": "bokaap"})
    assert prev["preview"] and store[0]["active"] is True
    done = await _call({"action": "stop", "customer_name": "bokaap", "confirm": True})
    assert done["verified"] and store[0]["active"] is False and len(store) == 1
    assert (await _call({"action": "list"}))["recurring"] == []


@pytest.mark.asyncio
async def test_a_failed_read_back_is_said_plainly(store, monkeypatch):
    async def lost(tid):
        return []
    monkeypatch.setattr(service, "list_recurring", lost)
    out = await _call({"action": "set_up", "invoice_number": "ACM-INV-00012", "confirm": True,
                       "start_date": "2026-11-05"})
    assert out["reply_verbatim"].startswith("⚠️")


def test_the_tool_is_offered_with_the_invoice_tools():
    names = [t["function"]["name"] for t in ca.INVOICE_TOOLS]
    assert "recurring_invoice" in names and ca._TOOL_SCOPE["recurring_invoice"] == "invoices"
