"""Tests for BOQ Excel export."""
import io
import pytest


SAMPLE_BOQ = {
    "project_name": "Test House",
    "net_total": 100000.0,
    "markup_pct": 15,
    "markup_amount": 15000.0,
    "grand_total": 115000.0,
    "items": [
        {
            "trade": "Concrete",
            "description": "Strip foundations",
            "unit": "m³",
            "qty": 12.0,
            "rate_used": 1800.0,
            "line_total": 21600.0,
            "source": "scraped",
            "confidence": 85,
        },
        {
            "trade": "Brickwork",
            "description": "215mm brick walls",
            "unit": "m²",
            "qty": 80.0,
            "rate_used": 450.0,
            "line_total": 36000.0,
            "source": "estimate",
            "confidence": 70,
        },
    ],
    "by_trade": {
        "Concrete": [
            {"line_total": 21600.0, "description": "Strip foundations"}
        ],
        "Brickwork": [
            {"line_total": 36000.0, "description": "215mm brick walls"}
        ],
    },
}


def test_generate_returns_bytesio():
    pytest.importorskip("openpyxl")
    from vula.takeoff.boq_export import generate_boq_excel
    buf = generate_boq_excel(SAMPLE_BOQ, "Test Project")
    assert isinstance(buf, io.BytesIO)
    assert buf.read(4) == b"PK\x03\x04"  # xlsx is a zip file


def test_three_sheets():
    openpyxl = pytest.importorskip("openpyxl")
    from vula.takeoff.boq_export import generate_boq_excel
    buf = generate_boq_excel(SAMPLE_BOQ)
    wb = openpyxl.load_workbook(buf)
    assert set(wb.sheetnames) == {"Summary", "Full BOQ", "Trade Summary"}


def test_summary_sheet_has_grand_total():
    openpyxl = pytest.importorskip("openpyxl")
    from vula.takeoff.boq_export import generate_boq_excel
    buf = generate_boq_excel(SAMPLE_BOQ, "Test")
    wb = openpyxl.load_workbook(buf)
    ws = wb["Summary"]
    values = [ws.cell(row=r, column=1).value for r in range(1, 15)]
    assert "Grand Total" in values


def test_full_boq_sheet_item_count():
    openpyxl = pytest.importorskip("openpyxl")
    from vula.takeoff.boq_export import generate_boq_excel
    buf = generate_boq_excel(SAMPLE_BOQ)
    wb = openpyxl.load_workbook(buf)
    ws = wb["Full BOQ"]
    # Row 1 = header, rows 2+ = items, last row = totals
    item_rows = [ws.cell(row=r, column=3).value for r in range(2, 4)]
    assert "Strip foundations" in item_rows
    assert "215mm brick walls" in item_rows


def test_trade_summary_sheet_percentages():
    openpyxl = pytest.importorskip("openpyxl")
    from vula.takeoff.boq_export import generate_boq_excel
    buf = generate_boq_excel(SAMPLE_BOQ)
    wb = openpyxl.load_workbook(buf)
    ws = wb["Trade Summary"]
    trades = [ws.cell(row=r, column=1).value for r in range(2, 4)]
    assert "Concrete" in trades
    assert "Brickwork" in trades


def test_empty_boq_does_not_crash():
    pytest.importorskip("openpyxl")
    from vula.takeoff.boq_export import generate_boq_excel
    buf = generate_boq_excel({})
    assert isinstance(buf, io.BytesIO)


def test_no_openpyxl_raises_import_error(monkeypatch):
    import vula.takeoff.boq_export as mod
    monkeypatch.setattr(mod, "HAS_OPENPYXL", False)
    with pytest.raises(ImportError):
        mod.generate_boq_excel({})
