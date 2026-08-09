"""Tests for invoice BoQ trade sections/subtotals (backlog item, 2026-08-08).

Construction invoices/quotes often need line items grouped into trade sections
(Demolition, Structure, Finishes...) each with its own subtotal — commerce_invoices'
line_items was previously a flat list with no grouping concept at all. This is purely
additive: an invoice that never sets `section` on any line renders and totals exactly as
before (verified explicitly below).
"""
from vula.commerce.pdf import _group_sections
from vula.commerce.service import _compute_totals


# ── _compute_totals: section pass-through ───────────────────────────────────────

def test_compute_totals_passes_through_section():
    items = [
        {"description": "Strip out partitions", "quantity": 1, "unit_price_cents": 500000, "section": "Demolition"},
        {"description": "Mezzanine slab", "quantity": 1, "unit_price_cents": 850000, "section": "Structure"},
    ]
    _, _, _, _, normalised = _compute_totals(items, vat_rate=15.0)
    assert normalised[0]["section"] == "Demolition"
    assert normalised[1]["section"] == "Structure"


def test_compute_totals_section_none_when_not_given():
    items = [{"description": "Consulting", "quantity": 1, "unit_price_cents": 100000}]
    _, _, _, _, normalised = _compute_totals(items, vat_rate=15.0)
    assert normalised[0]["section"] is None


def test_compute_totals_blank_section_normalised_to_none():
    items = [{"description": "Consulting", "quantity": 1, "unit_price_cents": 100000, "section": "   "}]
    _, _, _, _, normalised = _compute_totals(items, vat_rate=15.0)
    assert normalised[0]["section"] is None


def test_compute_totals_math_unaffected_by_sections():
    """The actual invoice math (subtotal/VAT/total) must be identical whether or not lines
    carry a section — sections are a display/grouping concept only, never a pricing one."""
    items = [
        {"description": "A", "quantity": 2, "unit_price_cents": 10000, "section": "Structure"},
        {"description": "B", "quantity": 1, "unit_price_cents": 5000, "section": "Finishes"},
    ]
    plain = [{"description": "A", "quantity": 2, "unit_price_cents": 10000},
             {"description": "B", "quantity": 1, "unit_price_cents": 5000}]
    sectioned = _compute_totals(items, vat_rate=15.0)
    unsectioned = _compute_totals(plain, vat_rate=15.0)
    assert sectioned[:4] == unsectioned[:4]  # subtotal, discount, vat, total all identical


# ── _group_sections (pdf.py) ─────────────────────────────────────────────────────

def test_group_sections_single_unnamed_group_for_flat_invoice():
    """Every existing invoice: no line has a section — must produce exactly one group with
    name=None, which the template renders as the original flat table (no header/subtotal
    rows) — this is the "zero visual change" guarantee."""
    items = [
        {"description": "A", "total_cents": 1000, "section": None},
        {"description": "B", "total_cents": 2000, "section": None},
    ]
    sections = _group_sections(items)
    assert len(sections) == 1
    assert sections[0]["name"] is None
    assert len(sections[0]["lines"]) == 2
    assert sections[0]["subtotal_cents"] == 3000


def test_group_sections_groups_by_name_preserving_first_seen_order():
    items = [
        {"description": "Strip out", "total_cents": 5000, "section": "Demolition"},
        {"description": "Slab", "total_cents": 8500, "section": "Structure"},
        {"description": "Steel", "total_cents": 3000, "section": "Structure"},
        {"description": "Debris removal", "total_cents": 1200, "section": "Demolition"},
    ]
    sections = _group_sections(items)
    assert [s["name"] for s in sections] == ["Demolition", "Structure"]
    assert sections[0]["subtotal_cents"] == 6200
    assert sections[1]["subtotal_cents"] == 11500
    assert len(sections[0]["lines"]) == 2
    assert len(sections[1]["lines"]) == 2


def test_group_sections_mixed_sectioned_and_unsectioned():
    """A line with no section lands in its own unnamed group — still rendered (with no
    header, per the template logic), not silently dropped."""
    items = [
        {"description": "Consulting fee", "total_cents": 2000, "section": None},
        {"description": "Demo", "total_cents": 5000, "section": "Demolition"},
    ]
    sections = _group_sections(items)
    assert len(sections) == 2
    names = [s["name"] for s in sections]
    assert None in names and "Demolition" in names
