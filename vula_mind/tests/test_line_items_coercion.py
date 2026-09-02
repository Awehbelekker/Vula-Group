"""line_items must always be stored as a real list of dicts.

2026-09-02, real DIGG incident: the document-scan commit path wrote json.dumps(line_items) while
every other write path stored the list itself. The column holds JSON, so the dumped string was
stored as a JSON *string* — and reading it back gives a string, which len() and iteration treat
CHARACTER BY CHARACTER. A R1,599.90 Caisson (Pty) Ltd invoice came back as 260 "line items":

    '[', '{', '"', 'd', 'e', 's', 'c', 'r', 'i', 'p', 't', 'i', ...

and the line totals summed to R0.00 against a real R1,599.90 invoice. Every email-scanned
invoice and expense since that path shipped was affected, and opening one in the UI showed
hundreds of single-character rows.
"""
import json

import pytest

from vula.commerce.service import _coerce_line_items

REAL_ITEMS = [
    {"description": "Excavator hire - 1 day", "quantity": 1, "unit_price_cents": 159990},
    {"description": "Delivery", "quantity": 1, "unit_price_cents": 0},
]


def test_the_real_bug_a_json_string_becomes_a_list():
    got = _coerce_line_items(json.dumps(REAL_ITEMS))
    assert got == REAL_ITEMS
    assert len(got) == 2, "not 260 characters"
    assert all(isinstance(i, dict) for i in got)


def test_character_iteration_no_longer_happens():
    """The specific symptom: len() over a stored string counted characters."""
    raw = json.dumps(REAL_ITEMS)
    assert len(raw) > 100, "the raw JSON really is long enough to look like many items"
    assert len(_coerce_line_items(raw)) == 2


def test_a_list_passes_through_unchanged():
    assert _coerce_line_items(REAL_ITEMS) == REAL_ITEMS


def test_double_encoding_is_unwrapped():
    assert _coerce_line_items(json.dumps(json.dumps(REAL_ITEMS))) == REAL_ITEMS


@pytest.mark.parametrize("value", [None, "", [], "not json at all", 5, True])
def test_junk_becomes_an_empty_list_never_a_string(value):
    got = _coerce_line_items(value)
    assert isinstance(got, list)
    assert all(isinstance(i, dict) for i in got)


def test_a_single_dict_is_wrapped():
    assert _coerce_line_items({"description": "x"}) == [{"description": "x"}]


def test_non_dict_entries_are_dropped():
    assert _coerce_line_items([{"a": 1}, "junk", 5, None]) == [{"a": 1}]


def test_the_scan_commit_path_no_longer_dumps_to_a_string():
    """Guards the exact regression at both write sites."""
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1] / "vula" / "commerce" / "service.py"
    text = src.read_text(encoding="utf-8")
    assert '"line_items": _j.dumps(' not in text, "line_items must not be json.dumps()'d on write"
    assert text.count('"line_items": _coerce_line_items(') == 2
