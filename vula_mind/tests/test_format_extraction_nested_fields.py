"""Tests for _format_extraction's 2026-08-27 fix: a list-of-dicts field (e.g. a project
billboard's "team" array — architect/engineer/contractor/H&S consultant, each with contact
details) used to be silently dropped entirely, confirmed live on a real gerflor billboard
photo — the reply showed only project_name/property_address, losing every contact. Financial
line_items stay a count-only summary; every other list/dict field now renders as compact
sub-bullets instead of vanishing."""
from vula.api.whatsapp import _format_extraction, _flatten_extraction_item

# The real (redacted-none) extraction from the live incident.
BILLBOARD_FIELDS = {
    "team": [
        {"role": "ARCHITECT & PRINCIPAL AGENT", "company": "Forte Architetti",
         "contact_details": {"email": "studio@fortearchitetti.it", "phone": "079 406 1889",
                             "website": "www.fortearch.lt"}},
        {"role": "STRUCTURAL ENGINEER", "company": "MH & A Consulting Engineers",
         "contact_details": {"email": "admin@mha-engineers.co.za", "phone": "021 762 6290"}},
        {"role": "PRINCIPAL CONTRACTOR", "company": "Orcon Projects",
         "contact_details": {"email": "info@orconprojects.co.za", "phone": "081 770 2246",
                             "website": "www.orconprojects.co.za"}},
        {"role": "OCCUPATIONAL HEALTH & SAFETY", "company": "SMIT Health & Safety Consulting",
         "contact_details": {"email": "henning20smit@gmail.com", "phone": "083 788 0249"}},
    ],
    "project_name": "Additions & Alterations",
    "property_address": "ERF 1375, 4 Ludlow Road, Vredehoek",
}


def test_billboard_team_no_longer_silently_dropped():
    out = _format_extraction({"fields": BILLBOARD_FIELDS})
    assert "• Project Name: Additions & Alterations" in out
    assert "• Property Address: ERF 1375, 4 Ludlow Road, Vredehoek" in out
    # every real contact from the incident must survive into the reply
    assert "Forte Architetti" in out
    assert "studio@fortearchitetti.it" in out
    assert "079 406 1889" in out
    assert "MH & A Consulting Engineers" in out
    assert "021 762 6290" in out
    assert "Orcon Projects" in out
    assert "081 770 2246" in out
    assert "SMIT Health & Safety Consulting" in out
    assert "083 788 0249" in out


def test_team_label_and_role_present():
    out = _format_extraction({"fields": BILLBOARD_FIELDS})
    assert "• Team:" in out
    assert "ARCHITECT & PRINCIPAL AGENT" in out


def test_line_items_stays_count_only_not_full_breakdown():
    """Financial line_items must NOT get the full sub-bullet treatment — a real invoice/quote
    with many items would make the reply unreadable. Confirmed the previous "items" key check
    never actually matched the real extraction shape (which uses "line_items") — fixed here too."""
    fields = {
        "supplier": "ACME Hardware",
        "total_cents": 500000,
        "line_items": [
            {"description": "Door hinge", "quantity": 12, "total_cents": 12000},
            {"description": "Bolt lock", "quantity": 4, "total_cents": 8000},
        ],
    }
    out = _format_extraction({"fields": fields})
    assert "• Line items: 2" in out
    assert "Door hinge" not in out
    assert "Bolt lock" not in out


def test_single_nested_dict_field_flattened_not_dropped():
    fields = {"classification": {"fire_behaviour": "Bfl", "smoke_production": "S1"}}
    out = _format_extraction({"fields": fields})
    assert "Bfl" in out
    assert "S1" in out


def test_list_of_plain_strings_still_renders():
    fields = {"attendees": ["Judy", "Solucent rep"]}
    out = _format_extraction({"fields": fields})
    assert "Judy" in out
    assert "Solucent rep" in out


def test_empty_list_field_omitted_cleanly():
    fields = {"team": [], "project_name": "Test"}
    out = _format_extraction({"fields": fields})
    assert "• Project Name: Test" in out
    assert "Team" not in out


def test_flatten_extraction_item_recurses_one_level():
    item = {"role": "ENGINEER", "contact_details": {"phone": "021 555 1234"}}
    assert _flatten_extraction_item(item) == "ENGINEER, 021 555 1234"


def test_flatten_extraction_item_skips_nested_lists():
    item = {"role": "ENGINEER", "certifications": ["ECSA", "SAICE"]}
    # nested list inside a list-of-dicts item is skipped, not recursed into — bounded depth
    assert _flatten_extraction_item(item) == "ENGINEER"
