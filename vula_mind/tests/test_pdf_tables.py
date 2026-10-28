"""PDF tables read as tables (vula/ingestion/pdf_tables.py), 28 Oct.

Ian: "Why is Vula struggling to read PDFs?" Judy's project bill as a PDF read P&G as R4,800 for
R48,000 (columns run together); the same bill as a spreadsheet read exactly. A table-shaped PDF is
now read through Docling first, its bill tables go through the spreadsheet reader, and a long
document's middle is no longer skipped. Docling itself is stubbed here. All figures made up.
"""
from unittest.mock import AsyncMock, patch

import pytest

from vula.ingestion import pdf_tables

PLAIN = """Sample Flat - Project Bill
Item Unit Qty Budget Actual Paid
SITE MANAGEMENT
P&G sum 1 48 000,00 48 000,00 48 000,00 Site supervision wk 6
12 000.00 10 000.00 5 000.00
BUILDING LABOUR
Brickwork sum 1 38 000.00 82 650.00 41 650.00
Plaster m2 120 5 000.00 6 000.00 6 000.00
Geyser no 1 9 000.00 9 500.00 0.00
Paint l 40 3 200.00 3 400.00 3 400.00
TOTAL 115 200.00 159 550.00 104 050.00
"""

MD = """Sample Flat - Project Bill

| Item | Unit | Qty | Budget | Actual | Paid to Date |
|---|---|---|---|---|---|
| SITE MANAGEMENT | | | | | |
| P&G | sum | 1 | R 48 000,00 | R 48 000,00 | R 48 000,00 |
| Site supervision | wk | 6 | 12 000.00 | 10 000.00 | 5 000.00 |
| BUILDING LABOUR | | | | | |
| Brickwork | sum | 1 | 38 000.00 | 82 650.00 | 41 650.00 |

Page 2

| Plaster | m2 | 120 | 5 000.00 | 6 000.00 | 6 000.00 |
| PLUMBING | | | | | |
| Geyser | no | 1 | 9 000.00 | 9 500.00 | 0.00 |
| TOTAL | | | 112 000.00 | 156 150.00 | 100 650.00 |
"""


def test_a_page_of_figures_is_tabular_and_prose_is_not():
    assert pdf_tables.looks_tabular(PLAIN)
    assert not pdf_tables.looks_tabular("Dear Judy,\nThank you for the meeting on 12 October 2026.\nRegards")
    assert pdf_tables.has_tables(MD) and not pdf_tables.has_tables(PLAIN)


def test_bill_tables_are_read_cell_by_cell_across_a_page_break():
    lines = pdf_tables.boq_lines(MD)
    got = [(li["section"], li["description"], li["total_cents"], li.get("actual_cents"), li.get("paid_cents"))
           for li in lines]
    assert got == [("SITE MANAGEMENT", "P&G", 48_000_00, 48_000_00, 48_000_00),
                   ("SITE MANAGEMENT", "Site supervision", 12_000_00, 10_000_00, 5_000_00),
                   ("BUILDING LABOUR", "Brickwork", 38_000_00, 82_650_00, 41_650_00),
                   ("BUILDING LABOUR", "Plaster", 5_000_00, 6_000_00, 6_000_00),   # page 2, same section
                   ("PLUMBING", "Geyser", 9_000_00, 9_500_00, 0)]                  # TOTAL row skipped
    assert pdf_tables.boq_lines("no tables here") == []


async def test_docling_runs_once_per_file(tmp_path):
    pdf = tmp_path / "bill.pdf"
    pdf.write_bytes(b"%PDF-1.4 sample")
    with patch("vula.ingestion.docling_extract.extract_markdown", new=AsyncMock(return_value=MD)) as conv:
        assert await pdf_tables.structured_markdown(pdf) == MD
        assert await pdf_tables.structured_markdown(pdf) == MD
    assert conv.await_count == 1
    assert await pdf_tables.structured_markdown(tmp_path / "sheet.xlsx") is None


def _resp(content):
    return type("R", (), {"choices": [type("C", (), {"message": type("M", (), {"content": content})()})()]})()


BOQ_JSON = ('{"category": "Bill of Quantities (BOQ)", "summary": "Project bill.", "fields": '
            '{"supplier": null, "total_cents": 11200000, "line_items": ['
            '{"description": "P&G", "total_cents": 480000}, {"description": "Brickwork", "total_cents": 3800000},'
            '{"description": "Plaster", "total_cents": 500000}, {"description": "Geyser", "total_cents": 900000},'
            '{"description": "Paint", "total_cents": 1}]}}')


async def test_a_table_pdf_is_read_as_tables_before_the_model(tmp_path):
    from vula.api.whatsapp import _analyze_document
    pdf = tmp_path / "Project Bill.pdf"
    pdf.write_bytes(b"%PDF-1.4 sample")
    seen = []

    async def llm(**kw):
        seen.append(kw["messages"][-1]["content"])
        return _resp(BOQ_JSON)
    with (patch("core.llm_router.resolve_cheap_route", new=AsyncMock(return_value=("m", "k", "b"))),
          patch("core.llm_router.resolve_cloud_route", return_value=None),
          patch("litellm.acompletion", new=AsyncMock(side_effect=llm)),
          patch("vula.ingestion.docling_extract.extract_markdown", new=AsyncMock(return_value=MD))):
        r = await _analyze_document("t1", "Project Bill.pdf", pdf, text=PLAIN)
    assert "| P&G | sum | 1 | R 48 000,00 |" in seen[0]               # the model saw the table
    f = r["fields"]
    # the bill's lines then came from its cells, not the model's (scrambled R4,800 P&G replaced)
    assert f["line_items_read_from"] == "pdf_tables" and f["line_items_source"] == "sheet"
    assert [li["total_cents"] for li in f["line_items"]][:1] == [48_000_00]
    assert [s["section"] for s in f["sections"]] == ["SITE MANAGEMENT", "BUILDING LABOUR", "PLUMBING"]
    assert f["sections"][1]["actual_cents"] == 88_650_00 and f["sections"][1]["paid_cents"] == 47_650_00


async def test_a_scanned_pdf_keeps_its_plain_text(tmp_path):
    from vula.api.whatsapp import _analyze_document
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4 sample")
    seen = []

    async def llm(**kw):
        seen.append(kw["messages"][-1]["content"])
        return _resp('{"category": "General Document", "summary": "x", "fields": {}}')
    with (patch("core.llm_router.resolve_cheap_route", new=AsyncMock(return_value=("m", "k", "b"))),
          patch("core.llm_router.resolve_cloud_route", return_value=None),
          patch("litellm.acompletion", new=AsyncMock(side_effect=llm)),
          patch("vula.ingestion.docling_extract.extract_markdown", new=AsyncMock(return_value="| a |\n|---|\n"))):
        await _analyze_document("t1", "scan.pdf", pdf, text=PLAIN)
    assert "Brickwork sum 1 38 000.00" in seen[0]                       # Docling lost the text: plain kept


def test_the_middle_of_a_long_document_is_no_longer_skipped():
    from vula.api.whatsapp import _unread_middle
    text = "H" * 4600 + "M" * 3000 + "T" * 1400
    assert _unread_middle(text) == "M" * 3000
    assert _unread_middle("short") == ""


async def test_an_invoice_short_of_its_total_reads_its_middle_pages():
    from vula.api import whatsapp as wa
    long_text = "TAX INVOICE\n" + "x" * 4600 + "\nGrout 2 x R 454.00 = R 908.00\n" + "y" * 1500
    result = {"category": "Invoice", "fields": {"total_cents": 158_000, "line_items": [
        {"description": "Tiles", "total_cents": 67_200}]}}
    extra = AsyncMock(return_value=[{"description": "Grout", "quantity": 2, "total_cents": 90_800}])
    with patch.object(wa, "_boq_lines_from_text", new=extra):
        out = await wa._complete_invoice_lines(result, long_text, "inv.pdf")
    assert [li["description"] for li in out["fields"]["line_items"]] == ["Tiles", "Grout"]
    assert extra.await_args.kwargs["doc_kind"] == "invoice"
    # lines that already add up: nothing more is read
    done = {"category": "Invoice", "fields": {"total_cents": 67_200, "line_items": [{"description": "Tiles", "total_cents": 67_200}]}}
    extra.reset_mock()
    with patch.object(wa, "_boq_lines_from_text", new=extra):
        await wa._complete_invoice_lines(done, long_text, "inv.pdf")
    extra.assert_not_awaited()
