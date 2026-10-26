"""Tests for whatsapp.py's _format_extraction — confirmed live 2026-08-27 that a real
digg-demo WhatsApp reply literally said "Amount Cents: 280000" / "Opening Balance Cents: 0"
instead of "Amount: R2,800.00" / "Opening Balance: R0.00", because every field was printed
verbatim with no special-casing for *_cents fields."""
from vula.api.whatsapp import _format_extraction


def test_cents_field_converted_to_rand_and_cents_dropped_from_label():
    analysis = {"fields": {"amount_cents": 280000}}
    out = _format_extraction(analysis)
    assert "Amount: R2,800.00" in out
    assert "Cents" not in out
    assert "280000" not in out


def test_multiple_cents_fields_all_converted():
    analysis = {"fields": {
        "opening_balance_cents": 0,
        "closing_balance_cents": 280000,
    }}
    out = _format_extraction(analysis)
    assert "Opening Balance: R0.00" in out
    assert "Closing Balance: R2,800.00" in out
    assert "Cents" not in out


def test_non_cents_fields_unaffected():
    analysis = {"fields": {"supplier": "Safe Point", "reference": "INV-0196"}}
    out = _format_extraction(analysis)
    assert "• Supplier: Safe Point" in out
    assert "• Reference: INV-0196" in out


def test_non_numeric_cents_value_is_dropped_not_shown_as_bogus_rand():
    analysis = {"fields": {"amount_cents": "not-a-number", "supplier": "Safe Point"}}
    out = _format_extraction(analysis)
    assert "Amount" not in out
    assert "• Supplier: Safe Point" in out


def test_real_payment_notification_breakdown_matches_expected_shape():
    """The exact real payload from the live incident, minus dict/list fields (already filtered
    elsewhere) — confirms the whole breakdown reads as real Rand values, not raw cents."""
    analysis = {"fields": {
        "payer": "AWEH BE LEKKER (PTY) LTD",
        "payee_name": "Safe Point",
        "payee_bank": "FIRST NATIONAL BANK",
        "amount_cents": 280000,
        "reference": "INV-0196/INV-0217",
        "trace_id": "Y3QQ3STQ",
    }}
    out = _format_extraction(analysis)
    assert "• Amount: R2,800.00" in out
    assert "Cents" not in out
    assert "• Payer: AWEH BE LEKKER (PTY) LTD" in out


def test_a_reply_never_shows_vulas_bookkeeping_or_raw_cents():
    """2026-10-26, DIGG: Judy's Belladonna BOQ was answered with "Unverified Figures:
    line_items[0].unit_price_cents, 480000, 4800.0", room budgets in cents ("Main Bathroom,
    990000"), "Confidence: medium" and "Total Budget: 432195"."""
    out = _format_extraction({"fields": {
        "supplier": "Bathroom Bizarre (Pty) Ltd", "tax_id": "4060154616",
        "total_cents": 2050831, "confidence": "medium", "total_budget": 432195,
        "_unverified_figures": [{"rand": 4800.0, "cents": 480000, "field": "line_items[0].total_cents"}],
        "sections": [{"section": "Main Bathroom", "budget_cents": 990000}],
        "line_items": [{"description": "Butler sink"}]}})
    assert "Unverified" not in out and "line_items[0]" not in out and "480000" not in out
    assert "Confidence" not in out and "432195" not in out and "990000" not in out
    assert "Main Bathroom, R9,900.00" in out and "• Total: R20,508.31" in out
    assert "Tax Id: 4060154616" in out and "• Line items: 1" in out
