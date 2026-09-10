"""A distributor stock sheet must be readable as a SET of rows, not as prose.

gerflor, 2026-09-07: a rep uploaded "DT SOH and Planning 07.09.26.pdf" and asked "how's
Creation stock". Creation is not on that sheet at all, and similarity search can never say so —
it can rank what a document contains, never report what it omits. So the sheet is parsed to
rows and answered deterministically (vula/commerce/stock_sheet.py, migration 156).
"""
from unittest.mock import patch

import pytest

from vula.commerce import stock_sheet as ss

# A trimmed real-shape DT SOH sheet.
SHEET = """DT STOCK ON HAND (SOH m²) - 07.09.26

VIRTUO 30 COL: SUNNY WHITE 2.00MM 532
VIRTUO 30 COL: BAITA MEDIUM 2.00MM 581.4 534 Est. Mid Nov - TBC
MAC TILES-COL: 612 ST/GREY 1.60MM 6504.3
MAC TILES-COL: 656 BASIL 2.00MM 378 164m2 Reserved TVET College
AMBIANCE ULTRA TERRA COL: 0203 STEAM GREY 2.00W 540 2000 Est. Mid Oct - TBC
EL7 SD ROBUST COL: 0098 CONCRETE 2.00MM 0
"""


def test_recognises_a_real_stock_sheet():
    assert ss.looks_like_stock_sheet(SHEET) is True


def test_ignores_an_unrelated_document():
    assert ss.looks_like_stock_sheet("Tax Invoice\nTotal due R2.00mm\n" * 6) is False


def test_parses_rows_with_product_colour_soh_and_note():
    rows = ss.parse_stock_lines(SHEET)
    assert len(rows) == 6
    virtuo = next(r for r in rows if "SUNNY WHITE" in r["colour"])
    assert virtuo["product"] == "VIRTUO 30"
    assert virtuo["thickness"] == "2.00MM"
    assert virtuo["soh"] == 532.0
    baita = next(r for r in rows if "BAITA" in r["colour"])
    assert baita["note"] and "Nov" in baita["note"]


def test_as_at_date_from_header():
    assert ss.as_at_date(SHEET) == "07.09.26"


def test_absent_product_reports_what_IS_stocked():
    rows = ss.parse_stock_lines(SHEET)
    out = ss.summarise(rows, "Creation", as_at="07.09.26")
    assert out["found"] is False
    assert "not on this stock list" in out["answer"]
    assert "VIRTUO 30" in out["available_products"]


def test_present_product_totals_soh_and_singular_plural():
    rows = ss.parse_stock_lines(SHEET)
    out = ss.summarise(rows, "mac tiles", as_at="07.09.26")
    assert out["found"] is True
    assert out["line_count"] == 2
    # 6504.3 + 378
    assert out["total_soh"] == pytest.approx(6882.3)


def _sheet_row():
    return {"filename": "DT SOH 07.09.26.pdf", "as_at": "07.09.26",
            "rows": ss.parse_stock_lines(SHEET),
            "product_ranges": ss.product_names(ss.parse_stock_lines(SHEET))}


def test_answer_stock_query_returns_deterministic_hit():
    with patch.object(ss, "latest_rows_for_tenant", return_value=_sheet_row()):
        out = ss.answer_stock_query("gerflor", "how's virtuo stock")
    assert out["found"] is True
    assert out["source_file"] == "DT SOH 07.09.26.pdf"


def test_answer_stock_query_absent_product_still_answers_when_stock_intent():
    with patch.object(ss, "latest_rows_for_tenant", return_value=_sheet_row()):
        out = ss.answer_stock_query("gerflor", "do we have any Creation in stock")
    assert out is not None and out["found"] is False


def test_answer_stock_query_does_not_hijack_unrelated_lookups():
    # A stock sheet on file must not turn "what's our VAT number" into "VAT is not on the list".
    with patch.object(ss, "latest_rows_for_tenant", return_value=_sheet_row()):
        assert ss.answer_stock_query("gerflor", "what is our VAT registration number") is None


def test_answer_stock_query_none_when_no_sheet():
    with patch.object(ss, "latest_rows_for_tenant", return_value=None):
        assert ss.answer_stock_query("someone-else", "virtuo stock") is None
