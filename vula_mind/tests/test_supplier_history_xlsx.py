"""Real .xlsx generation/delivery for supplier-history export requests, added 2026-09-26 after
the user asked for an actual spreadsheet capability rather than the disclosure-only fix shipped
in #74 ("I can't generate an actual spreadsheet file yet"). Vula can now build a real workbook
(vula/commerce/xlsx.py::render_supplier_history_xlsx) and WhatsApp it via the same Meta-media
mechanism already used for invoice PDFs (vula/api/whatsapp.py::_send_invoice_document, no public
URL needed) — see vula/commerce/service.py::send_supplier_history_xlsx.
"""
import io
from unittest.mock import AsyncMock, patch

import openpyxl
import pytest

from vula.commerce.service import send_supplier_history_xlsx
from vula.commerce.xlsx import render_supplier_history_xlsx

TID = "digg-demo"

_RESULT = {
    "status": "found", "match_type": "resolved_via_knowledge_base",
    "resolved_supplier": "GARDENS HANDIMAN CENTRE", "total_matches": 3,
    "total_amount": "R1,084.00", "total_amount_cents": 108400, "matches_with_amount": 3,
    "materials": [{"description": "SAND PER BAG ACC", "quantity": 40, "spend": "R1,240.00",
                   "spend_cents": 124000, "documents": 2, "unit_price_varies": True},
                  {"description": "CEMENT 50KG", "quantity": -3, "spend": "-R477.00",
                   "spend_cents": -47700, "documents": 1}],
    "materials_distinct": 2,
    "matches": [
        {"filename": "POS Account Sale 24-225537.pdf", "amount": 942.0, "filed_at": "2026-09-22T11:40:03"},
        {"filename": "POS Account Refund 21-366230.pdf", "amount": 954.0, "is_refund": True,
         "filed_at": "2026-09-22T10:36:10"},
        {"filename": "POS Account Sale 23-244976.pdf", "amount": 1252.0, "filed_at": "2026-09-12T09:10:52"},
    ],
}


# ── render_supplier_history_xlsx ────────────────────────────────────────────────

def test_render_returns_none_for_an_incomplete_result():
    assert render_supplier_history_xlsx({"status": "not_found_filed"}) is None
    assert render_supplier_history_xlsx({"status": "found", "match_type": "knowledge_base",
                                         "matches": [{"excerpt": "x"}]}) is None


# Real-shaped vula_filed_documents rows (digg-demo, 2026-09), run through the real search-result
# builder so the workbook is tested on exactly what production hands it.
_ROWS = [
    {"id": "d1", "filename": "POS Account Sale 24-225537.pdf", "category": "Invoice",
     "summary": "Tax invoice from Gardens Handiman Centre for sand and cement",
     "created_at": "2026-09-22T11:40:03",
     "fields": {"supplier": "Gardens Handiman Centre", "date": "2026-09-22", "total_cents": 94200,
                "vat_cents": 12287, "line_items": [
                    {"description": "SAND PER BAG ACC", "quantity": 15, "unit_price_cents": 3100, "total_cents": 46500},
                    {"description": "CEMENT 50KG PPC", "quantity": 3, "unit_price_cents": 15900, "total_cents": 47700}]}},
    {"id": "d2", "filename": "POS Account Refund 21-366230.pdf", "category": "Invoice",
     "summary": "Account refund from Gardens Handiman Centre for 6 bags of cement",
     "created_at": "2026-09-22T10:36:10",
     "fields": {"supplier": "GARDENS HANDIMAN CENTRE", "date": "2026-09-22", "total_cents": 95400,
                "vat_cents": 12443, "line_items": [
                    {"description": "CEMENT 50KG PPC", "quantity": 6, "unit_price_cents": 15900, "total_cents": 95400}]}},
    {"id": "d3", "filename": "POS Account Sale 23-244976.pdf", "category": "Invoice",
     "summary": "Tax invoice from Gardens Handiman Centre", "created_at": "2026-09-12T09:10:52",
     "fields": {"supplier": "GARDENS HANDIMAN CENTRE", "date": "2026-09-12", "total_cents": 125200,
                "vat_cents": 16330, "line_items": []}},
    {"id": "d4", "filename": "image-wamid.HBgLMjc4MjcwNzcwODAVAgASGCBBQ0NDNjVGMkY3NjYwMEU0NjA2MDMwN0E4MEJDOENFMgA=",
     "category": "Invoice", "summary": "Handwritten slip", "created_at": "2026-09-10T08:00:00",
     "fields": {"supplier": "GARDENS HANDIMAN CENTRE"}},
]


def _result():
    from vula.commerce.service import _filed_rows_result
    return dict(_filed_rows_result(_ROWS), resolved_supplier="GARDENS HANDIMAN CENTRE")


def _sheet_rows(ws):
    return [list(r) for r in ws.iter_rows(values_only=True)]


def test_render_builds_summary_invoices_line_items_and_materials():
    """The 2026-09-26 workbook: title was the question, the Document column held raw filenames
    and a WhatsApp media id, dates were text, no invoice no./VAT/line items, no Materials."""
    wb = openpyxl.load_workbook(io.BytesIO(render_supplier_history_xlsx(_result(), "GARDENS HANDIMAN CENTRE")))
    assert wb.sheetnames == ["Summary", "Invoices", "Line items", "Materials"]
    assert wb["Summary"]["A1"].value == "GARDENS HANDIMAN CENTRE"

    inv = wb["Invoices"]
    assert _sheet_rows(inv)[0] == ["Date", "Invoice no.", "Supplier", "Type", "Excl. VAT", "VAT",
                                   "Total", "Lines", "Note"]
    body = _sheet_rows(inv)[1:5]
    refs = [r[1] for r in body]
    assert refs == ["24-225537", "21-366230", "23-244976", "WhatsApp photo"]
    assert not any("wamid" in str(c) for r in _sheet_rows(inv) for c in r)
    import datetime
    assert all(isinstance(r[0], datetime.datetime) for r in body)          # real dates, not text
    refund = body[1]
    assert refund[3] == "Refund" and refund[6] == -954.0                   # refunds are negative
    assert body[3][6] is None and "No amount on file" in body[3][8]
    total_row = _sheet_rows(inv)[5]
    assert total_row[3] == "Total" and total_row[6] == "=SUM(G2:G5)"
    # the SUM of the column is the server total the text reply quotes
    assert sum(r[6] or 0 for r in body) * 100 == _result()["total_amount_cents"] == 124000

    lines = _sheet_rows(wb["Line items"])
    assert lines[0][:4] == ["Date", "Invoice no.", "Supplier", "Description"]
    assert ["21-366230", "CEMENT 50KG PPC", 6, 159.0, -954.0] == [lines[3][1], *lines[3][3:7]]

    mats = {r[0]: r for r in _sheet_rows(wb["Materials"])[1:] if r[0] and r[0] != "Total"}
    assert mats["SAND PER BAG ACC"][1] == 15 and mats["SAND PER BAG ACC"][2] == 465.0


def test_render_omits_the_materials_sheet_when_there_are_no_materials():
    result = {k: v for k, v in _result().items() if k not in ("materials", "_materials_all")}
    wb = openpyxl.load_workbook(io.BytesIO(render_supplier_history_xlsx(result, "GARDENS HANDIMAN CENTRE")))
    assert "Materials" not in wb.sheetnames


def test_render_without_export_rows_still_builds_from_the_listed_matches():
    result = {k: v for k, v in _RESULT.items() if k != "resolved_supplier"}
    result["matches"] = [{"filename": "x 12345.pdf", "amount": 100.0, "party": "ACME (PTY) LTD"}]
    wb = openpyxl.load_workbook(io.BytesIO(render_supplier_history_xlsx(result)))
    assert wb["Summary"]["A1"].value == "Supplier"
    assert _sheet_rows(wb["Invoices"])[1][1:3] == ["12345", "ACME"]


def test_all_suppliers_workbook_groups_spelling_variants_and_ranks_suppliers():
    from vula.commerce.service import _export_row
    from vula.commerce.xlsx import render_invoices_xlsx
    rows = [_export_row(r) for r in _ROWS] + [_export_row({
        "filename": "00090614.pdf", "category": "Invoice", "created_at": "2026-07-20T09:00:00",
        "summary": "This is a tax invoice from SOLID CAPE (PTY) LTD to AWEH BELEKKER T/A DIGG for construction",
        "fields": {}})]
    wb = openpyxl.load_workbook(io.BytesIO(render_invoices_xlsx(rows, "All suppliers", by_supplier=True)))
    summary = _sheet_rows(wb["Summary"])
    table = [r for r in summary if r[0] in ("GARDENS HANDIMAN CENTRE", "SOLID CAPE")]
    assert [r[0] for r in table] == ["GARDENS HANDIMAN CENTRE", "SOLID CAPE"]   # one row per supplier
    assert table[0][1] == 4
    solid = [r for r in _sheet_rows(wb["Invoices"]) if r[2] == "SOLID CAPE"][0]
    assert "Supplier read from the document summary" in solid[8] and "No amount on file" in solid[8]


# ── send_supplier_history_xlsx ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_returns_false_without_a_phone():
    send_doc = AsyncMock()
    with patch("vula.api.whatsapp._send_invoice_document", new=send_doc):
        ok = await send_supplier_history_xlsx(TID, "", "in excel please", _RESULT, "Gardens")
    assert ok is False
    send_doc.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_returns_false_without_export_wording():
    send_doc = AsyncMock()
    with patch("vula.api.whatsapp._send_invoice_document", new=send_doc):
        ok = await send_supplier_history_xlsx(
            TID, "+27821234567", "just the total please", _RESULT, "Gardens")
    assert ok is False
    send_doc.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_builds_and_sends_the_workbook_with_the_right_content_type():
    send_doc = AsyncMock(return_value=True)
    with patch("vula.api.whatsapp._send_invoice_document", new=send_doc):
        ok = await send_supplier_history_xlsx(
            TID, "+27821234567", "please send as .xlsx", _RESULT, "GARDENS HANDIMAN CENTRE")
    assert ok is True
    send_doc.assert_awaited_once()
    (phone, xlsx_bytes, filename, *_rest), kwargs = send_doc.call_args
    assert phone == "+27821234567"
    assert filename.startswith("Gardens_Handiman_Centre_invoices_") and filename.endswith(".xlsx")
    assert kwargs["content_type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    # Bytes are a real xlsx, not a placeholder.
    openpyxl.load_workbook(io.BytesIO(xlsx_bytes))


@pytest.mark.asyncio
async def test_send_returns_false_when_the_result_has_nothing_to_export():
    send_doc = AsyncMock()
    with patch("vula.api.whatsapp._send_invoice_document", new=send_doc):
        ok = await send_supplier_history_xlsx(
            TID, "+27821234567", "in excel please", {"status": "not_found_filed"}, "Gardens")
    assert ok is False
    send_doc.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_fails_closed_when_whatsapp_send_raises():
    with patch("vula.api.whatsapp._send_invoice_document",
               new=AsyncMock(side_effect=RuntimeError("meta down"))):
        ok = await send_supplier_history_xlsx(
            TID, "+27821234567", "in excel please", _RESULT, "Gardens")
    assert ok is False
