"""The BOQ priced online, and the QS's view of a project, 27 Oct.

Ian: "can the tenant just do the BOQ online if needed and not download, also can you share access
to the QS and give a quick view dashboard on what a QS would like to view". All figures here are
made up.
"""
import io
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from openpyxl import load_workbook

from vula.commerce import boq_online as bo
from vula.commerce import boq_template as bt
from vula.commerce import project_tracker as pt

TID = "test-tenant"


class _Q:
    def __init__(self, db, name):
        self.db, self.name, self.filters, self.row = db, name, [], None

    def select(self, *a): return self
    def order(self, *a, **k): return self
    def limit(self, *a): return self
    def range(self, *a): return self
    def eq(self, k, v): self.filters.append(lambda r, k=k, v=v: r.get(k) == v); return self

    def upsert(self, row, on_conflict=None):
        keys = on_conflict.split(",")
        rows = self.db.tables.setdefault(self.name, [])
        rows[:] = [r for r in rows if any(r.get(k) != row.get(k) for k in keys)] + [dict(row)]
        return self

    def execute(self):
        return SimpleNamespace(data=[r for r in self.db.tables.setdefault(self.name, []) if all(f(r) for f in self.filters)])


class _DB:
    def __init__(self, **t): self.tables = t
    def table(self, name): return _Q(self, name)


def test_amounts_are_worked_out_by_the_server_not_taken_from_the_browser():
    lines = bo.clean_lines([
        {"section": "Tiling", "description": "Floor tiles", "unit": "m²", "qty": "2.5", "rate_cents": 333,
         "amount_cents": 999_999},
        {"description": "   ", "qty": 1, "rate_cents": 100},                    # no description: dropped
        {"section": "Masonry", "description": "Lintels", "qty": "-3", "rate_cents": 9500},   # negative qty: blank
        {"description": "Provisional item", "qty": "1", "rate_cents": None},
    ])
    assert [li["description"] for li in lines] == ["Floor tiles", "Lintels", "Provisional item"]
    assert lines[0]["amount_cents"] == 833                    # 2.5 × R3.33 = R8.325 → R8.33, half up
    assert lines[1]["qty"] is None and lines[1]["amount_cents"] is None
    assert lines[2]["amount_cents"] is None


def test_section_totals_follow_the_asaqs_trade_order():
    lines = bo.clean_lines([{"section": "Tiling", "description": "a", "qty": 1, "rate_cents": 100},
                            {"section": "Guest Bathroom", "description": "b", "qty": 1, "rate_cents": 50},
                            {"section": "Masonry", "description": "c", "qty": 2, "rate_cents": 100}])
    assert bo.section_totals(lines) == [{"section": "Masonry", "budget_cents": 200},
                                        {"section": "Tiling", "budget_cents": 100},
                                        {"section": "Guest Bathroom", "budget_cents": 50}]


def _patched(db):
    return (patch.object(bo, "_client", return_value=db),
            patch("vula.commerce.service.canonical_project", side_effect=lambda t, n: n),
            patch("vula.commerce.service._now", return_value="2026-10-27T12:00:00+00:00"),
            patch("vula.commerce.ledger._all_pages", side_effect=lambda make: make().execute().data))


def test_the_bill_is_saved_and_read_back_per_tenant():
    db = _DB(vula_boq_drafts=[{"tenant_id": "other-tenant", "project": "Sample Flat", "lines": [], "total_cents": 7}])
    p1, p2, p3, p4 = _patched(db)
    with p1, p2, p3, p4:
        out = bo.save(TID, "Sample Flat", [{"section": "Tiling", "description": "Floor tiles", "qty": "10",
                                            "rate_cents": 20000}], by="27820000000")
    assert (out["total_cents"], out["sections"]) == (200000, [{"section": "Tiling", "budget_cents": 200000}])
    assert [r["total_cents"] for r in db.tables["vula_boq_drafts"] if r["tenant_id"] == "other-tenant"] == [7]


def test_a_locked_baseline_is_never_replaced_by_the_online_bill():
    db = _DB(vula_project_boq=[{"tenant_id": TID, "project": "Sample Flat", "total_cents": 1, "baseline_locked": True}],
             vula_boq_drafts=[{"tenant_id": TID, "project": "Sample Flat", "total_cents": 500,
                               "lines": [{"description": "x", "amount_cents": 500}]}])
    p1, p2, p3, p4 = _patched(db)
    with p1, p2, p3, p4, patch("vula.commerce.service.upsert_project_boq") as up:
        out = bo.use_as_budget(TID, "Sample Flat")
    assert "locked" in out["error"] and not up.called


def test_the_online_bill_becomes_the_project_budget_and_is_read_back():
    db = _DB(vula_project_boq=[], vula_boq_drafts=[{"tenant_id": TID, "project": "Sample Flat", "total_cents": 500,
                                                    "lines": [{"section": "Tiling", "description": "x", "amount_cents": 500}]}])

    def upsert(tenant, project, total, **kw):
        db.tables["vula_project_boq"] = [{"tenant_id": tenant, "project": project, "total_cents": total, **kw}]
    p1, p2, p3, p4 = _patched(db)
    with p1, p2, p3, p4, patch("vula.commerce.service.upsert_project_boq", side_effect=upsert) as up:
        out = bo.use_as_budget(TID, "Sample Flat")
    assert out["budget_cents"] == 500
    assert up.call_args.kwargs["sections"] == [{"section": "Tiling", "budget_cents": 500}]


def test_starting_from_the_filed_qs_bill():
    db = _DB(vula_filed_documents=[{"tenant_id": TID, "project": "Sample Flat", "category": bo._BOQ_CATEGORY,
                                    "created_at": "2026-10-20", "filename": "Bill.xlsx", "fields": {
        "line_items_source": "sheet", "line_items": [
            {"section": "MASONRY", "description": "Half brick walls", "quantity": 10, "unit": "m2",
             "unit_price_cents": 18500, "total_cents": 185000},
            {"section": "Guest Bathroom", "description": "Wall tiles", "quantity": 12, "unit": "m2",
             "unit_price_cents": 22000},
            {"section": "MASONRY", "description": "Extra opening", "variation": True, "variation_cents": 5000}]}}])
    p1, p2, p3, p4 = _patched(db)
    with p1, p2, p3, p4:
        lines = bo.from_filed_bill(TID, "Sample Flat")
    assert [(li["section"], li["description"], li["unit"], li["amount_cents"]) for li in lines] == [
        ("Masonry", "Half brick walls", "m²", 185000), ("Guest Bathroom", "Wall tiles", "m²", 264000)]


BRAND = {"display_name": "Example Builders", "legal_name": "Example Builders", "reg_no": "", "address": "",
         "vat_registered": False, "vat_number": "", "accent": "2C5545", "ink": "1E1E1E", "logo_url": None}


def test_the_excel_download_is_the_template_pre_filled_with_the_bill():
    lib = [["Tiling: Floor tiles [m²] — Supplier A", "Tiling", "Floor tiles", "m²", 200.0, "Material", "Supplier A", "1 Oct 2026"]]
    lines = [{"section": "Tiling", "key": lib[0][0], "description": "Floor tiles", "qty": "10", "rate_cents": 21000},
             {"section": "Masonry", "key": None, "description": "Own lintel", "unit": "m", "qty": "3", "rate_cents": 9500,
              "area": "Kitchen"}]
    wb = load_workbook(io.BytesIO(bt.render(BRAND, lib, None, date(2026, 10, 27), lines=lines, project="Sample Flat")))
    bq = wb["BOQ"]
    assert (bq["A9"].value, bq["C9"].value, bq["F9"].value, bq["H9"].value) == ("Tiling", lib[0][0], 10, 210)
    assert (bq["D10"].value, bq["E10"].value, bq["H10"].value, bq["M10"].value) == ("Own lintel", "m", 95, "Kitchen")
    assert bq["N9"].value is None                               # no "example" note on a real bill
    assert wb["Cover"]["C21"].value == "Sample Flat"


def test_the_print_page_escapes_and_totals():
    with patch.object(bo, "load", return_value={"project": "Flat <1>", "total_cents": 1500, "lines": [
            {"section": "Tiling", "description": "Tiles & grout", "unit": "m²", "qty": "1", "rate_cents": 1500,
             "amount_cents": 1500, "area": None}]}):
        page = bo.print_html(TID, "Flat <1>", {"name": "Example"})
    assert "Flat &lt;1&gt;" in page and "Tiles &amp; grout" in page and "R15.00" in page


POS = {"project": "Sample Flat", "budget_cents": 100_000_00, "contract_cents": 105_000_00,
       "variations_claimed_cents": 5_000_00, "variations_extra_cost_cents": 2_000_00, "baseline_locked": True,
       "sections": [{"section": "Masonry", "budget_cents": 60_000_00, "certified_cents": 30_000_00,
                     "anticipated_cents": 62_000_00},
                    {"section": "Tiling", "budget_cents": 40_000_00, "certified_cents": 10_000_00,
                     "anticipated_cents": 40_000_00}],
       "claims": {"count": 2, "latest": {"number": 2, "status": "certified", "date": "2026-10-20",
                                         "cumulative_value_cents": 42_000_00, "certified_to_date_cents": 39_900_00,
                                         "retention_cents": 2_100_00, "this_payment_cents": 19_950_00}},
       "programme": {"tasks": 20, "done": 8, "done_pct": 40, "time_pct": 55, "start": "2026-09-01",
                     "finish": "2026-11-30"},
       "documents": [{"name": "PC No. 2.xlsx", "category": "Bill of Quantities (BOQ)", "filed": "2026-10-20"}],
       "invoices": [], "invoiced_cents": 39_900_00, "invoices_paid_cents": 19_950_00, "invoices_owed_cents": 19_950_00,
       "to_invoice_cents": 65_100_00, "as_of": "2026-10-27",
       "trades": [{"trade": "Labour", "cents": 30_000_00}], "business_cost_cents": 35_000_00, "fee_cents": 3_500_00,
       "fee_pct": 10, "profit_cents": 4_000_00, "overhead_share_cents": 1_000_00, "received_cents": 19_950_00,
       "open_book_balance_cents": 18_550_00}


def test_the_qs_view_shows_valuations_programme_and_register_but_never_the_business_costs():
    v = pt.client_view(POS, "qs")
    assert v["claims"]["latest"]["certified_to_date_cents"] == 39_900_00 and v["programme"]["time_pct"] == 55
    assert v["documents"][0]["name"] == "PC No. 2.xlsx" and v["sections"][0]["certified_cents"] == 30_000_00
    for private in ("trades", "business_cost_cents", "fee_cents", "fee_pct", "profit_cents",
                    "overhead_share_cents", "received_cents", "open_book_balance_cents"):
        assert private not in v, private
    page = pt.render({"name": "Example Builders"}, v)
    assert "Anticipated final" in page and "R102 000.00" in page        # anticipated total
    assert "Retention held" in page and "55% of the time gone" in page and "PC No. 2.xlsx" in page
    assert "Labour" not in page and "R35 000.00" not in page
    assert "Valuation 2" in pt.summary_text(POS, "qs")


# ── not for reuse: the Excel and the prices stay with the team; outsiders get PDFs ──

import pytest  # noqa: E402
from unittest.mock import AsyncMock  # noqa: E402

from vula.api import server  # noqa: E402

QS_LOGIN = {"full": False, "access": ["projects"]}


def _as(scope):
    return (patch("vula.api.tenant_auth.is_tenant_member", AsyncMock(return_value=True)),
            patch("vula.api.tenant_auth.member_scope", AsyncMock(return_value=scope)))


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["admin/boq-template.xlsx", "admin/boq-library", "admin/projects/boq-online.xlsx",
                                  "admin/projects/tracker", "admin/projects/share", "admin/projects/shares/s1/revoke",
                                  "admin/projects/costing"])
async def test_a_projects_only_login_never_gets_the_excel_the_prices_or_the_full_tracker(path):
    a, b = _as(QS_LOGIN)
    with a, b:
        res = await server._guard_check("GET", f"/v1/commerce/{TID}/admin/{path.split('admin/', 1)[1]}", "Bearer qs")
    assert res is not None and res.status_code == 403
    a, b = _as({"full": False, "access": ["finances"]})
    with a, b:
        assert await server._guard_check("GET", f"/v1/commerce/{TID}/{path}", "Bearer team") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["admin/projects/boq-online", "admin/projects/boq-online/print",
                                  "admin/projects/boq-online.pdf", "admin/projects/qs-view"])
async def test_a_projects_only_login_keeps_the_online_bill_its_pdf_and_the_qs_view(path):
    a, b = _as(QS_LOGIN)
    with a, b:
        assert await server._guard_check("GET", f"/v1/commerce/{TID}/{path}", "Bearer qs") is None


def test_the_excel_is_locked_with_a_password_nobody_is_given_and_says_who_it_is_licensed_to():
    data = bt.render(BRAND, [], None, date(2026, 10, 27))
    wb = load_workbook(io.BytesIO(data))
    assert wb.security.lockStructure and wb.security.workbookPassword
    for name in ("Cover", "BOQ", "Priced Bill", "Summary", "Rate Library", "Read me"):
        assert wb[name].protection.sheet and wb[name].protection.password, name
    assert wb["Rate Library"].protection.selectLockedCells          # prices can't be selected and copied
    assert wb["BOQ"]["F9"].protection.locked is False                # the inputs stay open
    assert "Licensed to Example Builders" in wb["BOQ"].oddFooter.center.text
    other = load_workbook(io.BytesIO(bt.render(BRAND, [], None, date(2026, 10, 27))))
    assert other.security.workbookPassword != wb.security.workbookPassword   # a new password per file
    readme = " ".join(str(c.value) for row in wb["Read me"].iter_rows() for c in row if c.value)
    assert "Unprotect" not in readme and "not for reuse" in readme


def test_the_priced_bill_pdf_is_a_pdf():
    with patch.object(bo, "load", return_value={"project": "Sample Flat", "total_cents": 1500, "lines": [
            {"section": "Tiling", "description": "Tiles", "unit": "m²", "qty": "1", "rate_cents": 1500,
             "amount_cents": 1500, "area": None}]}):
        data = bo.pdf(TID, "Sample Flat", {"name": "Example"})
    assert data[:4] == b"%PDF"


def test_the_shared_page_offers_pdfs_only():
    import asyncio
    from vula.api import project_track
    share = {"id": "s1", "tenant_id": TID, "project": "Sample Flat", "view": "qs", "views": 0}
    with (patch.object(pt, "share_for_token", return_value=share), patch.object(pt, "position", return_value=POS),
          patch.object(pt, "note_view"), patch("vula.api.commerce.public_brand", AsyncMock(return_value={"name": "Example"})),
          patch.object(bo, "load", return_value={"lines": [{"description": "x"}]})):
        page = asyncio.run(project_track.project_page("tok")).body.decode()
        pdf = asyncio.run(project_track.project_page_pdf("tok"))
    assert "href='/track/tok/pdf'" in page and "href='/track/tok/bill.pdf'" in page
    assert ".xlsx'" not in page                    # no link to an Excel — a file NAME in the register is fine
    assert pdf.media_type == "application/pdf" and pdf.body[:4] == b"%PDF"
