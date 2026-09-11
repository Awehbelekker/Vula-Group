"""ungrounded_figures: the number that gets booked must be legible in the document it came
from. A misread/invented total is often internally consistent (scan_quality_ok passes) but
still isn't on the page. vula/commerce/extraction_quality.py, 2026-09-11."""
from vula.commerce.extraction_quality import ungrounded_figures

INVOICE_TEXT = """ACME BUILDING SUPPLIES
Tax Invoice INV-4471
2 x Cement 42.5N            R 1 250.00
1 x Rebar Y12 (6m)           R  480.00
Subtotal                     R 1 730.00
VAT 15%                       R  259.50
Total Due                     R 1 989.50
"""


def test_all_figures_present_returns_empty():
    ex = {"total_cents": 198950, "vat_cents": 25950,
          "line_items": [{"description": "Cement", "total_cents": 125000},
                         {"description": "Rebar", "total_cents": 48000}]}
    assert ungrounded_figures(ex, INVOICE_TEXT) == []


def test_a_total_not_in_the_text_is_flagged():
    # Model read R1,989.50 as R7,989.50 — the line items it also grabbed still "reconcile"
    # against its wrong total closely enough, but 7989.50 is nowhere on the page.
    ex = {"total_cents": 798950,
          "line_items": [{"total_cents": 125000}, {"total_cents": 48000}]}
    bad = ungrounded_figures(ex, INVOICE_TEXT)
    assert [b["cents"] for b in bad] == [798950]
    assert bad[0]["rand"] == 7989.50


def test_no_source_text_flags_nothing():
    ex = {"total_cents": 798950}
    assert ungrounded_figures(ex, "") == []
    assert ungrounded_figures(ex, "  ") == []


def test_small_amounts_below_floor_are_ignored():
    # A R2.00 line item collides with digits everywhere — not worth checking.
    ex = {"total_cents": 173000, "line_items": [{"total_cents": 200}]}
    assert ungrounded_figures(ex, INVOICE_TEXT) == []


def test_whole_rand_amount_without_cents_in_source_still_grounds():
    ex = {"total_cents": 4400000}
    assert ungrounded_figures(ex, "Amount paid: R44 000 to the supplier") == []
