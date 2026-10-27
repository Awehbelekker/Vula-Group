"""The project tracker and its client link, 27 Oct.

Ian (DIGG): a project tracker — budget vs actual, what the client paid suppliers directly vs what
they paid the business, cost by trade — shared with the client by link, the view chosen per
project ("price only" or "open book"). Belladonna's final account was built by hand; this builds
it from the books. All figures here are made up.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from vula.commerce import project_tracker as pt

TID = "test-tenant"


class _Q:
    def __init__(self, db, name):
        self.db, self.name, self.filters, self.patch = db, name, [], None

    def select(self, *a): return self
    def order(self, *a, **k): return self
    def limit(self, *a): return self
    def range(self, *a): return self
    def eq(self, k, v): self.filters.append(lambda r, k=k, v=v: r.get(k) == v); return self
    def update(self, row): self.patch = row; return self
    def insert(self, row): self.db.tables.setdefault(self.name, []).append(dict(row, id=f"s{len(self.db.tables.get(self.name, []))}")); return self

    def execute(self):
        hit = [r for r in self.db.tables.setdefault(self.name, []) if all(f(r) for f in self.filters)]
        if self.patch is not None:
            for r in hit:
                r.update(self.patch)
        return SimpleNamespace(data=hit)


class _DB:
    def __init__(self, **t): self.tables = t
    def table(self, name): return _Q(self, name)


COSTED = {"project": "Sample Flat", "cost_cents": 40_000_00, "received_cents": 30_000_00, "fee_pct": 10.0,
          "trades": [{"trade": "Labour", "cents": 25_000_00}, {"trade": "Materials", "cents": 15_000_00}],
          "overhead_share_cents": 2_000_00, "profit_cents": 5_000_00, "unallocated_trade_cents": 0,
          "variations": {"claimed_cents": 3_000_00, "extra_cost_cents": 0, "documents": 1}}


def _db():
    return _DB(
        vula_project_boq=[{"tenant_id": TID, "project": "Sample Flat", "title": "Project bill", "total_cents": 50_000_00,
                           "sections": [{"section": "Bathroom", "budget_cents": 30_000_00},
                                        {"section": "Kitchen", "budget_cents": 20_000_00}]},
                          {"tenant_id": "other-tenant", "project": "Sample Flat", "total_cents": 99_999_00}],
        commerce_expenses=[{"tenant_id": TID, "paid_with": "client", "project": "Sample flat", "amount_cents": 2_000_00},
                           {"tenant_id": TID, "paid_with": "client", "project": "Other job", "amount_cents": 9_000_00}],
        commerce_invoices=[
            {"tenant_id": TID, "doc_type": "invoice", "project": "Sample Flat", "invoice_number": "INV-1",
             "issue_date": "2026-09-01", "total_cents": 20_000_00, "total_paid_cents": 20_000_00, "status": "paid"},
            {"tenant_id": TID, "doc_type": "invoice", "project": "Sample Flat", "invoice_number": "INV-2",
             "issue_date": "2026-10-01", "total_cents": 15_000_00, "total_paid_cents": 5_000_00, "status": "sent"},
            {"tenant_id": TID, "doc_type": "invoice", "project": "Sample Flat", "invoice_number": "INV-3",
             "total_cents": 7_000_00, "status": "cancelled"},
            {"tenant_id": TID, "doc_type": "invoice", "project": "Sample Flat", "direction": "inbound",
             "invoice_number": "BILL-9", "total_cents": 1_000_00, "status": "sent"}])


def _position(db=None):
    db = db or _db()
    with (patch.object(pt, "_client", return_value=db),
          patch("vula.commerce.job_costing.costing", return_value={"projects": [COSTED]}),
          patch("vula.commerce.job_costing.direct_costs", return_value={"sample flat": {"cents": 8_000_00}})):
        return pt.position(TID, "sample flat")


def test_the_projects_money_comes_from_its_own_books():
    p = _position()
    assert (p["project"], p["budget_cents"], p["contract_cents"]) == ("Sample Flat", 50_000_00, 53_000_00)
    # spent = through the business + paid directly by the client (supplier invoices + slips)
    assert (p["business_cost_cents"], p["paid_directly_cents"], p["spent_cents"]) == (40_000_00, 10_000_00, 50_000_00)
    assert (p["budget_left_cents"], p["spent_pct"]) == (0, 100)
    # open book: cost + 10% fee − received
    assert (p["fee_cents"], p["open_book_total_cents"], p["open_book_balance_cents"]) == (4_000_00, 44_000_00, 14_000_00)
    # price: the project's own outbound invoices (cancelled and supplier bills left out)
    assert [i["number"] for i in p["invoices"]] == ["INV-1", "INV-2"]
    assert (p["invoiced_cents"], p["invoices_paid_cents"], p["invoices_owed_cents"], p["to_invoice_cents"]) == \
        (35_000_00, 25_000_00, 10_000_00, 18_000_00)


def test_the_price_view_keeps_the_business_costs_and_margin_private():
    v = pt.client_view(_position(), "price")
    for private in ("trades", "business_cost_cents", "fee_cents", "fee_pct", "profit_cents",
                    "overhead_share_cents", "open_book_balance_cents", "received_cents"):
        assert private not in v, private
    page = pt.render({"name": "Example Builders"}, v)
    assert "R40 000.00" not in page and "Labour" not in page and "R10 000.00" in page   # owed now


def test_the_open_book_view_shows_cost_fee_and_balance_but_never_profit():
    v = pt.client_view(_position(), "open_book")
    assert v["business_cost_cents"] == 40_000_00 and v["fee_cents"] == 4_000_00
    assert "profit_cents" not in v and "overhead_share_cents" not in v
    page = pt.render({"name": "Example <Builders>"}, v)
    assert "R14 000.00" in page and "Labour" in page and "Example &lt;Builders&gt;" in page


def test_a_share_link_stores_only_a_hash_and_ends_when_revoked_or_expired():
    db = _db()
    with (patch.object(pt, "_client", return_value=db),
          patch("vula.commerce.service.canonical_project", return_value="Sample Flat"),
          patch("config.settings.public_base_url", "https://api.example.test")):
        share = pt.create_share(TID, "sample flat", "open_book", by="27820000000")
        token = share["url"].rsplit("/", 1)[1]
        row = db.tables["vula_project_shares"][0]
        assert share["url"].startswith("https://api.example.test/track/") and token not in str(row)
        assert pt.share_for_token(token)["view"] == "open_book"
        assert pt.share_for_token(token + "x") is None
        assert pt.revoke_share(TID, row["id"]) and pt.share_for_token(token) is None
        row["revoked_at"] = None
        row["expires_at"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        assert pt.share_for_token(token) is None


def test_a_view_that_is_not_one_of_the_two_is_refused():
    import pytest
    with patch.object(pt, "_client", return_value=_db()), pytest.raises(ValueError):
        pt.create_share(TID, "Sample Flat", "everything")


def test_whatsapp_asks_which_view_then_previews_then_makes_the_link():
    from core.skills.commerce_admin import CommerceAdminSkill
    skill = CommerceAdminSkill()
    reg = [{"name": "Sample Flat"}]
    with (patch("vula.commerce.service.canonical_project", return_value="Sample Flat"),
          patch("vula.commerce.service.registered_projects", return_value=reg),
          patch.object(pt, "position", return_value=_position()),
          patch.object(pt, "create_share", return_value={"id": "s1", "url": "https://api.example.test/track/abc",
                                                         "expires_at": "2027-04-25T00:00:00+00:00"}) as made):
        ask = asyncio.run(skill._share_project(TID, {"project": "sample flat"}))
        assert "Price only" in ask["reply_verbatim"] and "Open book" in ask["reply_verbatim"]
        pre = asyncio.run(skill._share_project(TID, {"project": "sample flat", "view": "price"}))
        assert pre["preview"] and "Owed now: R10 000.00" in pre["reply_verbatim"] and not made.called
        done = asyncio.run(skill._share_project(TID, {"project": "sample flat", "view": "price", "confirm": True},
                                                {"phone": "27820000000"}))
    made.assert_called_once_with(TID, "Sample Flat", "price", "27820000000")
    assert "https://api.example.test/track/abc" in done["reply_verbatim"] and "](" not in done["reply_verbatim"]


def test_an_unknown_project_is_asked_about_not_shared():
    from core.skills.commerce_admin import CommerceAdminSkill
    with (patch("vula.commerce.service.canonical_project", return_value="Nowhere"),
          patch("vula.commerce.service.registered_projects", return_value=[{"name": "Sample Flat"}])):
        out = asyncio.run(CommerceAdminSkill()._share_project(TID, {"project": "Nowhere", "view": "price"}))
    assert out["status"] == "need_info"


def test_the_public_page_refuses_an_unknown_link():
    from vula.api.project_track import project_page
    with patch.object(pt, "share_for_token", return_value=None):
        res = asyncio.run(project_page("not-a-real-token"))
    assert res.status_code == 404 and b"isn't valid" in res.body


# ── what the client paid suppliers directly (migration 229) ──────────────────

def _with_direct(db):
    db.tables["vula_project_direct_payments"] = [
        {"id": "d1", "tenant_id": TID, "project": "Sample Flat", "supplier": "Flush Bathrooms", "amount_cents": 5_000_00,
         "paid_on": "2026-09-10", "status": "paid"},
        {"id": "d2", "tenant_id": TID, "project": "Sample Flat", "supplier": "Edward (joinery)", "amount_cents": 4_500_00,
         "description": "balance", "paid_on": None, "status": "to_pay"},
        {"id": "d3", "tenant_id": TID, "project": "Sample Flat", "supplier": "Typo Co", "amount_cents": 9_999_00,
         "status": "removed"},
        {"id": "d4", "tenant_id": "other-tenant", "project": "Sample Flat", "supplier": "Not ours", "amount_cents": 1_00,
         "status": "paid"}]
    return db


def test_the_clients_own_payments_count_in_the_spend_never_in_the_business_cost():
    p = _position(_with_direct(_db()))
    assert p["paid_directly_recorded_cents"] == 5_000_00 and p["to_pay_directly_cents"] == 4_500_00
    assert p["paid_directly_cents"] == 8_000_00 + 2_000_00 + 5_000_00        # invoices + slips + recorded
    assert p["business_cost_cents"] == 40_000_00 and p["fee_cents"] == 4_000_00   # unchanged
    assert [d["supplier"] for d in p["direct_payments"]] == ["Flush Bathrooms", "Edward (joinery)"]
    v = pt.client_view(p, "open_book")
    page = pt.render({"name": "Example"}, v)
    assert "Still to be paid by you directly" in page and "Edward (joinery) — balance" in page and "Typo Co" not in page
    assert "direct_payments" not in pt.client_view(p, "price")


def test_a_direct_payment_is_checked_saved_and_read_back():
    import pytest
    db = _db()
    with (patch.object(pt, "_client", return_value=db),
          patch("vula.commerce.service.canonical_project", return_value="Sample Flat"),
          patch("vula.commerce.ledger._all_pages", side_effect=lambda make: make().execute().data)):
        for bad in ({"supplier": "", "amount_cents": 100}, {"supplier": "X", "amount_cents": 0},
                    {"supplier": "X", "amount_cents": 100, "status": "maybe"}):
            with pytest.raises(ValueError):
                pt.add_direct_payment(TID, "sample flat", bad["supplier"], bad["amount_cents"],
                                      status=bad.get("status", "paid"))
        row = pt.add_direct_payment(TID, "sample flat", "  Flush   Bathrooms ", 5_826_830, paid_on="2026-09-10")
        assert (row["project"], row["supplier"], row["amount_cents"], row["status"]) == \
            ("Sample Flat", "Flush Bathrooms", 5_826_830, "paid")
        assert pt.remove_direct_payment(TID, row["id"])
        assert pt.direct_payments(TID, "sample flat") == []


def test_whatsapp_previews_then_records_a_client_payment():
    from core.skills.commerce_admin import CommerceAdminSkill
    skill = CommerceAdminSkill()
    with (patch("vula.commerce.service.canonical_project", return_value="Sample Flat"),
          patch("vula.commerce.service.registered_projects", return_value=[{"name": "Sample Flat"}]),
          patch.object(pt, "add_direct_payment", return_value={"id": "d9", "project": "Sample Flat",
                                                               "supplier": "Flush", "amount_cents": 5_826_830}) as add):
        args = {"project": "sample flat", "supplier": "Flush", "amount": 58268.30}
        pre = asyncio.run(skill._client_paid_direct(TID, args))
        assert pre["preview"] and "R58,268.30" in pre["reply_verbatim"] and not add.called
        done = asyncio.run(skill._client_paid_direct(TID, {**args, "confirm": True}, {"phone": "27820000000"}))
    assert add.call_args.args[:4] == (TID, "Sample Flat", "Flush", 5_826_830)
    assert "Recorded on *Sample Flat*" in done["reply_verbatim"]
