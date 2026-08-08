"""Tests for resolve_party_name()'s nested-`{key}_details.name` fallback (2026-08-08 fix).

Real bug this closes: a "General Document" extraction (no enforced fields shape beyond
Invoice/Quote/BOQ/Business Card) nested a bank payment notification's payee under
`payee_details: {"name": "Josphine Gondo", ...}` instead of a flat `payee` string. The flat-only
lookup returned None, so the payee was silently unidentifiable downstream (no learned filing
rule, no reimbursement-balance match).
"""
from vula.commerce.party import resolve_party_name


def test_resolves_flat_field_first():
    assert resolve_party_name({"supplier": "Bauxite Extrusions"}) == "Bauxite Extrusions"


def test_falls_back_to_nested_details_name():
    fields = {"payee_details": {"name": "Josphine Gondo", "bank": "BANKZERO"}}
    assert resolve_party_name(fields) == "Josphine Gondo"


def test_flat_field_wins_over_nested_when_both_present():
    fields = {"payee": "Flat Name", "payee_details": {"name": "Nested Name"}}
    assert resolve_party_name(fields) == "Flat Name"


def test_excluded_key_skips_both_flat_and_nested():
    fields = {"payer": "Should Skip", "payer_details": {"name": "Also Should Skip"},
              "payee_details": {"name": "Real Payee"}}
    assert resolve_party_name(fields, exclude=("payer",)) == "Real Payee"


def test_nested_without_name_key_falls_through():
    fields = {"payee_details": {"bank": "BANKZERO"}, "beneficiary": "Fallback Name"}
    assert resolve_party_name(fields) == "Fallback Name"


def test_no_match_returns_none():
    assert resolve_party_name({"unrelated": "value"}) is None
