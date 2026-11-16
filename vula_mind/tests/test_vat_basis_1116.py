"""16 Nov 2026, Ian: "we need to be accurate". Learned prices didn't say whether VAT was in them, and
estimates mixed invoices written with VAT in, written with VAT out, and web prices (always incl) —
up to 15% off without a word. Migration 284 records the basis; an estimate is on ONE basis: excl
VAT for a VAT-registered business (it claims supplier VAT back), incl for one that isn't."""
import io
from unittest.mock import patch

import openpyxl
import pytest

from tests.fake_supabase import FakeSupabase
from tests.test_estimate import ITEMS, T, _own_rates, prices, wall, db  # noqa: F401 — fixtures
from vula.commerce import estimate, estimate_sheet, price_book, service, xlsx_check


def _doc(lines_cents, total, vat, **extra):
    return {"total_cents": total, "vat_cents": vat,
            "line_items": [{"description": f"Item {i}", "quantity": 1, "unit_price_cents": c, "total_cents": c}
                           for i, c in enumerate(lines_cents)], **extra}


def test_a_documents_basis_comes_from_its_own_figures():
    assert price_book.document_vat_basis(_doc([11500], 11500, 1500)) == "incl"      # lines = total
    assert price_book.document_vat_basis(_doc([10000], 11500, 1500)) == "excl"      # lines + VAT = total
    assert price_book.document_vat_basis(_doc([10000], 10000, 0)) == "no_vat"
    assert price_book.document_vat_basis({"amounts_exclude_vat": True}) == "excl"   # the document says so
    assert price_book.document_vat_basis({}) == "unknown"


def test_each_observation_carries_its_documents_basis():
    row = {"id": "d1", "tenant_id": T, "category": "Invoice",
           "fields": {**_doc([10000], 11500, 1500), "supplier_name": "Cashbuild", "date": "2026-11-01"}}
    with patch.object(service, "_is_refund_row", return_value=False), \
         patch.object(service, "_party_of", return_value="Cashbuild"):
        obs = price_book.observations_for(row)
    assert obs and all(o["vat_basis"] == "excl" for o in obs)


def _obs(cents, basis, when="2026-11-01"):
    return {"norm_key": "clay stock brick", "unit": "each", "description": "Clay stock brick", "kind": "material",
            "source_kind": "invoice", "unit_price_cents": cents, "observed_on": when, "vat_basis": basis}


def test_prices_written_either_way_become_one_cost():
    rows = [_obs(115, "incl"), _obs(100, "excl", "2026-10-01")]
    registered = price_book.rollup(rows, vat_registered=True)[0]
    not_registered = price_book.rollup(rows, vat_registered=False)[0]
    as_written = price_book.rollup(rows)[0]
    assert (registered["low_cents"], registered["high_cents"], registered["vat"]) == (100, 100, "excl")
    assert (not_registered["low_cents"], not_registered["high_cents"], not_registered["vat"]) == (115, 115, "incl")
    assert (as_written["low_cents"], as_written["high_cents"], as_written["vat"]) == (100, 115, None)


def test_a_price_that_didnt_say_is_used_as_written_and_counted():
    r = price_book.rollup([_obs(120, "unknown")], vat_registered=True)[0]
    assert r["rate_cents"] == 120 and r["vat_unknown"] == 1


@pytest.mark.asyncio
async def test_a_registered_business_estimate_is_excl_vat_with_market_prices_converted(wall, prices, monkeypatch):
    monkeypatch.setattr(estimate, "vat_registered", lambda t: True)
    est = await estimate.estimate(T, ITEMS, save=False)
    assert est["vat"] == "excl"
    sand = next(ln for it in est["items"] for ln in it["lines"] if ln["component"] == "mortar_sand")
    assert sand["source"] == "market" and sand["unit_price_cents"] == 56522     # R650.00 incl → R565.22
    text = estimate.summary(est)
    assert "excl VAT" in text and "Prices and totals exclude VAT" in text


@pytest.mark.asyncio
async def test_a_business_that_isnt_registered_keeps_vat_in(wall, prices, monkeypatch):
    monkeypatch.setattr(estimate, "vat_registered", lambda t: False)
    est = await estimate.estimate(T, ITEMS, save=False)
    sand = next(ln for it in est["items"] for ln in it["lines"] if ln["component"] == "mortar_sand")
    assert est["vat"] == "incl" and sand["unit_price_cents"] == 65000
    assert "include VAT" in estimate.summary(est)


@pytest.mark.asyncio
async def test_lines_whose_basis_was_unknown_are_named(wall, prices, monkeypatch):
    monkeypatch.setattr(estimate, "vat_registered", lambda t: True)

    def rates(tenant, term, kind=None, **kw):
        return [{**r, "vat_unknown": 1} if term == "brick" else r for r in _own_rates(tenant, term, kind)]

    monkeypatch.setattr("vula.commerce.price_book.rates", rates)
    est = await estimate.estimate(T, ITEMS, save=False)
    assert est["vat_unknown"] and "Couldn't tell whether VAT was included on" in estimate.summary(est)
    data, want = estimate_sheet.workbook("DIGG", est)
    assert xlsx_check.mismatches(data, want) == []                 # still adds up to the cent
    ws = openpyxl.load_workbook(io.BytesIO(data)).active
    assert "exclude VAT" in str(ws["A3"].value)
    assert any("(VAT basis unknown)" in str(c.value) for row in ws.iter_rows() for c in row if c.value)


def test_the_backfill_fills_older_rows_from_their_documents(monkeypatch):
    fdb = FakeSupabase({
        price_book._TABLE: [{"tenant_id": T, "doc_id": "d1", "vat_basis": "unknown", "unit_price_cents": 100},
                            {"tenant_id": T, "doc_id": "d2", "vat_basis": "unknown", "unit_price_cents": 100},
                            {"tenant_id": T, "doc_id": "d3", "vat_basis": "incl", "unit_price_cents": 100}],
        "vula_filed_documents": [{"tenant_id": T, "id": "d1", "fields": _doc([10000], 11500, 1500)},
                                 {"tenant_id": T, "id": "d2", "fields": {}}],
    })
    monkeypatch.setattr(price_book, "_client", lambda: fdb)
    assert price_book.backfill_vat_basis() == 1
    got = {r["doc_id"]: r["vat_basis"] for r in fdb.rows[price_book._TABLE]}
    assert got == {"d1": "excl", "d2": "unknown", "d3": "incl"}
    assert price_book.backfill_vat_basis() == 0                    # idempotent
