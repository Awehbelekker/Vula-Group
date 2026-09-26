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


def test_render_builds_a_real_readable_workbook():
    xlsx_bytes = render_supplier_history_xlsx(_RESULT, "GARDENS HANDIMAN CENTRE")
    assert xlsx_bytes is not None
    wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes))
    assert wb.sheetnames == ["Invoices", "Materials"]

    inv = wb["Invoices"]
    assert inv.cell(row=1, column=1).value == "GARDENS HANDIMAN CENTRE"
    rows = [inv.cell(row=r, column=2).value for r in range(5, 5 + len(_RESULT["matches"]))]
    assert "POS Account Sale 24-225537" in rows
    assert "POS Account Refund 21-366230" in rows
    # Every amount cell in the invoice rows is a real number, not a formatted string.
    amounts = [inv.cell(row=r, column=3).value for r in range(5, 5 + len(_RESULT["matches"]))]
    assert all(isinstance(a, (int, float)) for a in amounts)
    # A total row is present with the server-computed cents total.
    total_col = [inv.cell(row=r, column=3).value for r in range(1, inv.max_row + 1)]
    assert 1084.0 in total_col

    mat = wb["Materials"]
    descriptions = [mat.cell(row=r, column=1).value for r in range(2, mat.max_row + 1)]
    assert "SAND PER BAG ACC" in descriptions
    assert "CEMENT 50KG" in descriptions


def test_render_omits_the_materials_sheet_when_there_are_no_materials():
    result = dict(_RESULT, materials=[])
    xlsx_bytes = render_supplier_history_xlsx(result, "GARDENS HANDIMAN CENTRE")
    wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes))
    assert wb.sheetnames == ["Invoices"]


def test_render_falls_back_to_a_generic_supplier_name():
    result = {k: v for k, v in _RESULT.items() if k != "resolved_supplier"}
    result["matches"] = [{"filename": "x.pdf", "amount": 100.0}]
    xlsx_bytes = render_supplier_history_xlsx(result)
    wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes))
    assert wb["Invoices"].cell(row=1, column=1).value == "Supplier"


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
    assert filename == "GARDENS_HANDIMAN_CENTRE_history.xlsx"
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
