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

import pytest

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


AGREED = {"Sample Flat": {"pricing": "cost_plus", "fee_pct": 10.0, "fee_cents": None}}


def _position(db=None, agreed=None):
    db = db or _db()
    with (patch.object(pt, "_client", return_value=db),
          patch("vula.commerce.job_costing.agreed_terms", return_value=AGREED if agreed is None else agreed),
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
          patch("vula.commerce.job_costing.agreed_terms", return_value=AGREED),
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
        res = asyncio.run(project_page("not-a-real-token", None))
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


def _reconciled_db():
    db = _db()
    db.tables["vula_project_boq"][0]["sections"] = [
        {"section": "Bathroom", "budget_cents": 30_000_00, "actual_cents": 36_000_00, "paid_cents": 30_000_00},
        {"section": "Kitchen", "budget_cents": 20_000_00, "actual_cents": 19_000_00, "paid_cents": 19_000_00}]
    return db


def test_the_reconciled_project_bill_shows_actual_and_paid_per_section():
    # 28 Oct (Ian): "we did a whole recon on the Belladonna but it's not reflecting in the project
    # tables" — the project bill's Actual / Paid columns were stored on the sections but dropped
    p = _position(_reconciled_db())
    assert p["sections"][0] == {"section": "Bathroom", "budget_cents": 30_000_00,
                                "actual_cents": 36_000_00, "paid_cents": 30_000_00}
    s = p["sheet"]
    assert (s["actual_cents"], s["paid_cents"], s["to_pay_cents"]) == (55_000_00, 49_000_00, 6_000_00)
    assert (s["over_budget_cents"], s["over_sections"]) == (5_000_00, ["Bathroom"])
    assert s["not_in_books_cents"] == 55_000_00 - p["spent_cents"]
    assert _position()["sheet"] is None                    # a bill without those columns: nothing new


def test_a_price_or_qs_link_never_shows_what_the_sections_really_cost():
    p = _position(_reconciled_db())
    for view in ("price", "qs"):
        v = pt.client_view(p, view)
        assert "sheet" not in v and all("actual_cents" not in s and "paid_cents" not in s for s in v["sections"])
        assert "R36 000.00" not in pt.render({"name": "Example Builders"}, v)
    ob = pt.client_view(p, "open_book")
    assert ob["sheet"] == {"paid_cents": 49_000_00}
    page = pt.render({"name": "Example Builders"}, ob)
    assert "By section" in page and "Forecast" in page and "Paid so far" in page and "R49 000.00" in page


def test_the_client_never_sees_the_expected_final_cost():
    # 10 Nov 2026 (Ian): "drop the actual column … but we need to show what we forecasted as well"
    p = _position(_billed_db())
    ob = pt.client_view(p, "open_book")
    assert all("actual_cents" not in s for s in ob["sections"] + ob["bill_lines"])
    assert "actual_cents" not in ob["sheet"] and "to_pay_cents" not in ob["sheet"]
    page = pt.render({"name": "Example Builders"}, ob)
    assert "Actual" not in page and "R36 000.00" not in page and "R55 000.00" not in page and "R21 000.00" not in page
    assert "R50 000.00" in page and "R18 000.00" in page            # the forecast, total and per item
    assert p["sheet"]["actual_cents"] == 55_000_00                   # the business still has it


def test_the_page_fits_a_phone_and_uses_a_wide_screen():
    """10 Nov 2026: the client's page was a 560px strip on a desktop, and on a phone its multi-column
    tables and document rows ran off the right edge. Now: a wider card with the account blocks side by
    side, wide tables scrolling inside their own box, and document buttons on their own line."""
    v = {"view": "open_book", "project": "Belladonna", "as_of": "2026-11-10", "budget_cents": 100000,
         "contract_cents": 100000, "trades": [], "business_cost_cents": 0, "sheet": {"actual_cents": 1, "paid_cents": 1},
         "sections": [{"section": "Kitchen", "budget_cents": 1, "actual_cents": 1, "paid_cents": 1}]}
    page = pt.render({"name": "Example"}, v, extra_html="<div class='docs'></div>")
    assert "max-width:1040px" in page and "max-width:560px" not in page
    assert "<div class=\"grid\">" in page and "class='blk wide'" in page and "class='scroll'" in page
    assert "data-label='Forecast'" in page and "content:attr(data-label)" in page and "@media print" in page


# ── the agreed final account (migration 254) ───────────────────────────────────────────────

_FA = {"as_of": "2026-11-02", "charged_cents": 23924738, "total_cents": 24424738,
       "adjustments": [{"label": "Correction — Jack Hammer account", "cents": 500000}],
       "received_cents": 21140798, "balance_cents": 3283940}


def _ob(**kw):
    return {"view": "open_book", "project": "Belladonna", "as_of": "2026-11-10", "trades": [],
            "business_cost_cents": 13953701, "fee_basis": "cost_plus", "fee_pct": 10.0, "fee_cents": 1395370,
            "open_book_total_cents": 15349071, "received_cents": 21140798,
            "open_book_balance_cents": -5791727, **kw}


def test_the_final_account_is_the_account_on_the_client_page():
    page = pt.render({"name": "DIGG"}, _ob(final_account=_FA))
    assert "Costs and fee (final account, 2026-11-02)" in page and "R239 247.38" in page
    assert "Correction — Jack Hammer account" in page and "R244 247.38" in page
    assert "Balance due to us" in page and "R32 839.40" in page
    assert "Fee (10%)" not in page and "57 917.27" not in page          # never the cost + fee estimate


def test_without_a_final_account_the_estimate_stays():
    page = pt.render({"name": "DIGG"}, _ob(final_account=None))
    assert "Fee (10%)" in page and "Balance due to us" not in page


def test_an_overpaid_final_account_says_so():
    fa = {**_FA, "received_cents": 25000000, "balance_cents": 24424738 - 25000000}
    page = pt.render({"name": "DIGG"}, _ob(final_account=fa))
    assert "Balance due to you" in page and "R5 752.62" in page


def test_only_the_open_book_view_carries_the_final_account():
    pos = {**_ob(final_account=_FA), "overhead_share_cents": 1, "profit_cents": 2}
    assert pt.client_view(pos, "open_book")["final_account"] == _FA
    for view in ("price", "qs"):
        assert "final_account" not in pt.client_view(pos, view)


def test_a_later_payment_lowers_the_balance(monkeypatch):
    rows = [{"project": "Belladonna", "as_of": "2026-11-02", "charged_cents": 23924738,
             "adjustments": [{"label": "Correction", "cents": 500000}]}]
    monkeypatch.setattr(pt, "_rows", lambda make: rows)
    fa = pt.final_account("digg-demo", pt._key("belladonna"))
    assert fa["total_cents"] == 24424738
    assert pt.final_account("digg-demo", pt._key("HPC Bokaap")) is None


def test_saving_a_final_account_is_read_back(monkeypatch):
    saved = []

    class _T:
        def upsert(self, row, **kw): saved.append(row); return self
        def execute(self): return self

    monkeypatch.setattr(pt, "_client", lambda: type("C", (), {"table": lambda self, n: _T()})())
    monkeypatch.setattr("vula.commerce.service.canonical_project", lambda t, p: "Belladonna")
    monkeypatch.setattr(pt, "final_account", lambda t, k: {"as_of": "2026-11-02", "charged_cents": 100,
                                                           "adjustments": [], "total_cents": 100} if saved else None)
    out = pt.set_final_account("digg-demo", "belladonna", "2026-11-02", 100)
    assert out["project"] == "Belladonna" and saved[0]["tenant_id"] == "digg-demo"
    monkeypatch.setattr(pt, "final_account", lambda t, k: None)
    with pytest.raises(RuntimeError):
        pt.set_final_account("digg-demo", "belladonna", "2026-11-02", 100)


# ── the fee as agreed, per project (migration 255) ─────────────────────────────────────────

def test_no_agreed_fee_means_no_balance_for_the_client():
    p = _position(agreed={})
    assert p["fee_basis"] is None and p["fee_cents"] is None and p["open_book_balance_cents"] is None
    assert any("No fee is agreed" in w for w in p["warnings"])
    page = pt.render({"name": "Example"}, pt.client_view(p, "open_book"))
    assert "Not agreed yet" in page and "once the fee for this project is agreed" in page
    assert "Balance due" not in page and "Fee (10%)" not in page


def test_an_agreed_fixed_fee_is_used_as_agreed():
    p = _position(agreed={"sample flat": {"pricing": "fixed_fee", "fee_pct": None, "fee_cents": 48_000_00}})
    assert (p["fee_basis"], p["fee_source"], p["fee_cents"]) == ("fixed_fee", "project", 48_000_00)
    assert p["open_book_balance_cents"] == 40_000_00 + 48_000_00 - 30_000_00
    page = pt.render({"name": "Example"}, pt.client_view(p, "open_book"))
    assert "Fee (agreed amount)" in page and "R48 000.00" in page and "Balance due to us" in page


def test_the_businesss_standard_fee_applies_with_a_warning():
    p = _position(agreed={"*": {"pricing": "cost_plus", "fee_pct": 12.5, "fee_cents": None}})
    assert (p["fee_source"], p["fee_pct"], p["fee_cents"]) == ("standard", 12.5, 5_000_00)
    assert any("standard fee (12.5%)" in w for w in p["warnings"])


def test_a_project_fee_beats_the_standard_one():
    p = _position(agreed={"*": {"pricing": "cost_plus", "fee_pct": 12.5, "fee_cents": None},
                          "Sample Flat": {"pricing": "cost_plus", "fee_pct": 8.0, "fee_cents": None}})
    assert (p["fee_source"], p["fee_cents"]) == ("project", 3_200_00)


def test_the_owner_is_warned_when_the_link_and_the_invoices_disagree():
    # cost R40,000 + 10% − R30,000 received = R14,000 due; unpaid invoices R10,000 (INV-2)
    p = _position()
    assert any("R14,000.00 due" in w and "R10,000.00 unpaid" in w for w in p["warnings"])
    assert all("standard fee" not in w for w in p["warnings"])          # this project's own fee


def test_the_owner_is_warned_when_the_link_says_the_business_owes_the_client():
    w = pt._warnings(None, {"pricing": "cost_plus", "fee_pct": 10.0, "fee_cents": None}, "project", 10.0,
                     -5_791_727, [], 0, 21_140_798)
    assert any("owing the client R57,917.27" in x for x in w)


def test_a_final_account_matching_the_invoice_raises_no_warning():
    fa = {"total_cents": 24424738, "balance_cents": 3283940}
    assert pt._warnings(fa, None, None, None, None, [], 3283940, 21140798) == []


def test_an_open_book_link_needs_an_agreed_fee():
    db = _db()
    with (patch.object(pt, "_client", return_value=db),
          patch("vula.commerce.job_costing.agreed_terms", return_value={}),
          patch("vula.commerce.service.canonical_project", return_value="Sample Flat")):
        with pytest.raises(ValueError, match="Agree this project's fee first"):
            pt.create_share(TID, "sample flat", "open_book")
        assert pt.create_share(TID, "sample flat", "price")["view"] == "price"     # price only is fine


def test_the_owners_warnings_never_reach_the_client():
    p = _position(agreed={})
    for view in pt.VIEWS:
        assert "warnings" not in pt.client_view(p, view) and "fee_source" not in pt.client_view(p, view)


def test_a_project_priced_as_per_the_bill_shows_the_contract_and_invoices():
    p = _position(agreed={"Sample Flat": {"pricing": "per_bill", "fee_pct": None, "fee_cents": None}})
    assert (p["fee_basis"], p["fee_cents"], p["open_book_balance_cents"]) == ("per_bill", None, None)
    assert any("as per the bill" in w for w in p["warnings"]) and not any("No fee" in w for w in p["warnings"])
    page = pt.render({"name": "Example"}, pt.client_view(p, "open_book"))
    assert "Contract (as per the bill)" in page and "R53 000.00" in page and "Owed now" in page
    assert "Fee (" not in page and "Not agreed yet" not in page


# ── what the client still owes, at the top ─────────────────────────────────────────────────

def test_the_client_sees_where_the_money_stands_today_at_the_top():
    v = _ob(final_account=_FA, to_pay_directly_cents=6770200, paid_directly_cents=28381608)
    page = pt.render({"name": "DIGG"}, v)
    top = page[:page.index("<div class=\"grid\">")]
    assert "Where the money stands today" in top
    assert "Paid to us so far" in top and "R211 407.98" in top
    assert "Paid by you directly to suppliers" in top and "R283 816.08" in top
    assert "Balance due to us" in top and "R32 839.40" in top and "per the final account of 2026-11-02" in top
    # today's position only: no other suppliers' balances, nothing projected
    assert "R67 702.00" not in top and "Total still to pay" not in top
    assert "Open book · final account of 2026-11-02" in page and "cost-plus" not in page


def test_no_owed_summary_without_an_agreed_fee_or_final_account():
    page = pt.render({"name": "DIGG"}, _ob(final_account=None, fee_basis=None, fee_cents=None,
                                           open_book_balance_cents=None))
    assert "Where the money stands today" not in page


def test_the_heading_says_how_the_account_works():
    assert pt._basis_label({"view": "open_book", "fee_basis": "per_bill"}) == "Open book · priced as per the bill"
    assert pt._basis_label({"view": "open_book", "fee_basis": "cost_plus", "fee_pct": 10.0}) == "Open book · cost plus 10%"
    assert pt._basis_label({"view": "price"}) == "Price only"


# ── every project at a glance ───────────────────────────────────────────────────────────────

def test_what_is_owed_rests_on_the_same_rule_as_the_client_page():
    assert pt.owed_to_business({"final_account": {"balance_cents": 3283940, "as_of": "2026-11-02"}}) == \
        {"cents": 3283940, "basis": "final account 2026-11-02"}
    assert pt.owed_to_business({"open_book_balance_cents": 1400000, "fee_basis": "cost_plus"}) == \
        {"cents": 1400000, "basis": "cost + agreed fee"}
    assert pt.owed_to_business({"open_book_balance_cents": None, "invoices": [{"owed_cents": 5}], "invoices_owed_cents": 5}) == \
        {"cents": 5, "basis": "unpaid invoices"}
    assert pt.owed_to_business({"open_book_balance_cents": None, "invoices": [], "fee_basis": None}) == \
        {"cents": None, "basis": "fee not agreed"}


def test_the_overview_reads_the_business_once_and_lists_every_project():
    calls = {"costing": 0}

    def costing(tid):
        calls["costing"] += 1
        return {"projects": [COSTED, {**COSTED, "project": "Other Job", "cost_cents": 0, "received_cents": 5_000_00}]}

    with (patch.object(pt, "_client", return_value=_db()),
          patch("vula.commerce.job_costing.costing", side_effect=costing),
          patch("vula.commerce.job_costing.direct_costs", return_value={}),
          patch("vula.commerce.job_costing.agreed_terms", return_value=AGREED),
          patch("vula.commerce.service.registered_projects", return_value=[{"name": "Sample Flat", "status": "active"}])):
        o = pt.overview(TID)
    assert calls["costing"] == 1                       # once for the whole business, not once a project
    assert [r["project"] for r in o["projects"]] == ["Sample Flat", "Other Job"]
    flat = o["projects"][0]
    assert (flat["status"], flat["owed_cents"], flat["owed_basis"]) == ("active", 14_000_00, "cost + agreed fee")
    assert o["owed_cents"] == 14_000_00 and o["received_cents"] == 35_000_00
    assert o["projects"][1]["owed_cents"] is None       # Other Job: no fee agreed — nothing guessed


def _billed_db(total=50_000_00):
    db = _reconciled_db()
    items = [{"section": "Bathroom", "description": "Tiling", "total_cents": 18_000_00, "actual_cents": 21_000_00,
              "paid_cents": 18_000_00},
             {"section": "Bathroom", "description": "Plumbing", "total_cents": 12_000_00},
             {"section": "Kitchen", "description": "Units", "total_cents": 20_000_00},
             {"section": "Garden", "description": "Paving", "total_cents": 1_000_00},
             {"section": "Kitchen", "description": "  "}]
    db.tables["vula_filed_documents"] = [
        {"tenant_id": TID, "project": "Sample Flat", "created_at": "2026-10-26", "status": "filed",
         "fields": {"total_cents": total, "line_items_source": "sheet", "line_items": items}},
        {"tenant_id": TID, "project": "Sample Flat", "created_at": "2026-10-27", "status": "duplicate",
         "fields": {"total_cents": 50_000_00, "line_items": [{"description": "Copy", "total_cents": 1}]}},
        {"tenant_id": "other-tenant", "project": "Sample Flat", "created_at": "2026-10-28",
         "fields": {"total_cents": 50_000_00, "line_items": [{"description": "Theirs", "total_cents": 1}]}}]
    return db


def test_the_open_book_page_shows_the_project_bill_item_by_item():
    # 10 Nov 2026 (Ian): "why is it not showing the full project as per Judy's bill?"
    p = _position(_billed_db())
    assert [li["description"] for li in p["bill_lines"]] == ["Tiling", "Plumbing", "Units", "Paving"]
    assert p["bill_lines"][0] == {"section": "Bathroom", "description": "Tiling", "budget_cents": 18_000_00,
                                  "actual_cents": 21_000_00, "paid_cents": 18_000_00}
    page = pt.render({"name": "Example Builders"}, pt.client_view(p, "open_book"))
    assert "The project bill" in page
    assert page.index("Bathroom") < page.index("Tiling") < page.index("Plumbing") < page.index("Kitchen") \
        < page.index("Units") < page.index("Paving")             # items under their section; the unmatched last
    assert "Copy" not in page and "Theirs" not in page


def test_a_bill_that_is_not_the_budget_never_lends_its_items():
    assert _position(_billed_db(total=12_345_00))["bill_lines"] == []


def test_price_and_qs_links_never_show_the_bill_items():
    p = _position(_billed_db())
    for view in ("price", "qs"):
        v = pt.client_view(p, view)
        assert "bill_lines" not in v and "Tiling" not in pt.render({"name": "Example Builders"}, v)
