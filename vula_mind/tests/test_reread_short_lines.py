"""Invoices whose extracted line items fall short of the total (6 Oct 2026).

Two Gardens Handiman ("Jack Hammer") invoices were filed with a line missing: 23-247062
(R5,284.50, lines R465 short) and 22-191406 (R697.00, lines R180 short). scan_quality_ok accepts
lines within 70–130% of the total, so neither read was escalated, and the re-read only picked up
documents missing a supplier, date or total. Now a shortfall escalates the read, the re-read
picks such invoices up, and a re-read replaces only their line items.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.test_stock_movements import FakeDB
from vula.api import whatsapp as wa
from vula.commerce import reread, service
from vula.commerce.extraction_quality import lines_short_cents

TID = "digg-demo"

# 22-191406 as filed: R697.00 incl. R90.91 VAT, lines summing to R517.00
SHORT_191406 = {"supplier": "GARDENS HANDIMAN CENTRE", "date": "2026-09-14", "total_cents": 69700,
                "vat_cents": 9091, "confidence": "high",
                "line_items": [{"description": "DRILL BIT ALPEN PRO HSS 6.0MM", "quantity": 1, "total_cents": 23000},
                               {"description": "WALL PLUG BOX", "quantity": 1, "total_cents": 28700}]}
COMPLETE_191406 = {**SHORT_191406, "line_items": SHORT_191406["line_items"] + [
    {"description": "MASONRY BIT 8MM", "quantity": 1, "total_cents": 18000}]}
# 23-247062 as filed: R5,284.50, lines R465 short
SHORT_247062 = {"supplier": "GARDENS HANDIMAN CENTRE", "date": "2026-10-01", "total_cents": 528450,
                "vat_cents": 68928, "line_items": [{"description": "SLIDING DOOR TRACK", "quantity": 1,
                                                    "total_cents": 481950}]}


def test_shortfall_of_the_two_real_invoices():
    assert lines_short_cents(SHORT_191406) == 18000
    assert lines_short_cents(SHORT_247062) == 46500
    assert lines_short_cents(COMPLETE_191406) == 0


def test_no_shortfall_for_excl_vat_lines_rounding_or_nothing_to_compare():
    excl = {"total_cents": 115000, "vat_cents": 15000, "line_items": [{"total_cents": 100000}]}
    assert lines_short_cents(excl) == 0
    assert lines_short_cents({"total_cents": 115000, "line_items": [{"total_cents": 100000}]}) == 0
    assert lines_short_cents({"total_cents": 1000, "line_items": [{"quantity": 2, "unit_price_cents": 450}]}) == 0
    assert lines_short_cents({"total_cents": 1000, "line_items": []}) == 0
    assert lines_short_cents({"line_items": [{"total_cents": 500}]}) == 0
    # more than the total is the ratio check's business, not a shortfall
    assert lines_short_cents({"total_cents": 1000, "line_items": [{"total_cents": 1200}]}) == 0


# ── first read: a shortfall escalates, and the complete read is kept ─────────

TEXT_191406 = ("TAX INVOICE GARDENS HANDIMAN CENTRE POS Account Sale 22-191406\n"
               "DRILL BIT ALPEN PRO HSS 6.0MM 230.00\nWALL PLUG BOX 287.00\nMASONRY BIT 8MM 180.00\n"
               "VAT 90.91  TOTAL 697.00")


async def _analyze(reads, tmp_path):
    import json
    calls = []

    async def fake_completion(**kw):
        calls.append(kw)
        msg = MagicMock()
        msg.content = json.dumps(reads[min(len(calls), len(reads)) - 1])
        return MagicMock(choices=[MagicMock(message=msg)])

    with patch("litellm.acompletion", new=fake_completion), \
            patch("core.llm_router.resolve_cheap_route", new=AsyncMock(return_value=("cheap", None, None))), \
            patch("core.llm_router.resolve_cloud_route", return_value=("cloud", None, None)):
        out = await wa._analyze_document(TID, "POS Account Sale 22-191406.txt", tmp_path / "x.txt",
                                         text=TEXT_191406)
    return out, calls


@pytest.mark.asyncio
async def test_a_short_read_is_escalated_and_the_complete_one_kept(tmp_path):
    out, calls = await _analyze([{"category": "Invoice", "summary": "s", "fields": SHORT_191406},
                                 {"category": "Invoice", "summary": "s", "fields": COMPLETE_191406}], tmp_path)
    assert [c["model"] for c in calls] == ["cheap", "cloud"]
    assert len(out["fields"]["line_items"]) == 3


@pytest.mark.asyncio
async def test_escalation_never_swaps_a_grounded_total_for_a_different_one(tmp_path):
    wrong = {**COMPLETE_191406, "total_cents": 51700}
    out, calls = await _analyze([{"category": "Invoice", "summary": "s", "fields": SHORT_191406},
                                 {"category": "Invoice", "summary": "s", "fields": wrong}], tmp_path)
    assert len(calls) == 2
    assert out["fields"]["total_cents"] == 69700 and len(out["fields"]["line_items"]) == 2


# ── re-read: short invoices are candidates; only their lines are replaced ────

@pytest.fixture()
def db(monkeypatch):
    fake = FakeDB({"vula_filed_documents": [
        {"id": "short", "tenant_id": TID, "category": "Invoice", "filename": "POS Account Sale 22-191406.pdf",
         "file_url": "https://s/short.pdf", "fields": SHORT_191406, "summary": "Gardens Handiman", "project": "Belladonna"},
        {"id": "ok", "tenant_id": TID, "category": "Invoice", "filename": "POS Account Sale 23-247517.pdf",
         "file_url": "https://s/ok.pdf", "fields": {**COMPLETE_191406}},
    ]})
    monkeypatch.setattr(service, "_client", lambda: fake)
    return fake


def test_short_invoice_is_a_reread_candidate(db):
    assert [r["id"] for r in reread.candidates(TID)] == ["short"]


def test_lines_only_accepts_a_complete_read_and_rejects_a_changed_total(db):
    row = db.tables["vula_filed_documents"][0]
    assert reread._lines_only(row, {"category": "Invoice", "fields": COMPLETE_191406})
    assert not reread._lines_only(row, {"category": "Invoice", "fields": {**COMPLETE_191406, "total_cents": 70000}})
    assert not reread._lines_only(row, {"category": "Quote / Estimate", "fields": COMPLETE_191406})
    assert not reread._lines_only(row, {"category": "Invoice", "fields": SHORT_191406})   # no better


@pytest.mark.asyncio
async def test_reread_replaces_only_the_line_items(db, monkeypatch):
    reread_fields = {**COMPLETE_191406, "supplier": "Gardens Handiman", "date": "2026-09-15"}

    async def analyze(tid, filename, path, text=None):
        return {"category": "Invoice", "summary": "new summary", "fields": reread_fields}
    monkeypatch.setattr("vula.api.whatsapp._analyze_document", analyze)
    monkeypatch.setattr(reread, "_download", AsyncMock(return_value=b"%PDF-1.4"))

    class _Pipe:
        def __init__(self, tenant_id):
            self.parser = MagicMock(parse=AsyncMock(return_value=[(1, TEXT_191406)]))
    monkeypatch.setattr("vula.ingestion.pipeline.VulaIngestionPipeline", _Pipe)
    monkeypatch.setattr("vula.commerce.stock_sheet.persist_if_stock_sheet", lambda *a: None)
    recorded = []
    monkeypatch.setattr("vula.commerce.price_book.record_from_document", lambda t, r: recorded.append(r))

    out = await reread.reread_missing(TID)
    assert (out["total"], out["fixed"]) == (1, 1)
    row = db.tables["vula_filed_documents"][0]
    f = row["fields"]
    assert len(f["line_items"]) == 3 and lines_short_cents(f) == 0
    # everything else exactly as first read and booked
    assert (f["supplier"], f["date"], f["total_cents"], f["vat_cents"]) == \
        ("GARDENS HANDIMAN CENTRE", "2026-09-14", 69700, 9091)
    assert row["summary"] == "Gardens Handiman" and f["_reread_at"]
    assert len(recorded[0]["fields"]["line_items"]) == 3
