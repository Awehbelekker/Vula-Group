"""Tests for BoQ trade-section persistence (migration 129): upsert_project_boq's `sections`
param and expenses.known_sections()."""
from unittest.mock import MagicMock, patch

from vula.commerce.expenses import known_sections
from vula.commerce.service import upsert_project_boq

TID = "digg-demo"


def test_upsert_project_boq_includes_sections_when_given():
    mock_db = MagicMock()
    with patch("vula.commerce.service._client", return_value=mock_db):
        upsert_project_boq(TID, "Porterfield", 100000,
                           sections=[{"section": "Demolition", "budget_cents": 20000}])

    row = mock_db.table.return_value.upsert.call_args[0][0]
    assert row["sections"] == [{"section": "Demolition", "budget_cents": 20000}]


def test_upsert_project_boq_omits_sections_key_when_not_given():
    """Omitted (not an empty list) so a repeat upsert from the auto-BoQ-bridge (no reliable
    per-section signal in a scanned document) never clobbers a real breakdown entered manually —
    PostgREST upsert only touches columns present in the payload."""
    mock_db = MagicMock()
    with patch("vula.commerce.service._client", return_value=mock_db):
        upsert_project_boq(TID, "Porterfield", 100000)

    row = mock_db.table.return_value.upsert.call_args[0][0]
    assert "sections" not in row


def test_known_sections_unions_boq_and_expense_sections():
    def table(name):
        t = MagicMock()
        if name == "vula_project_boq":
            t.select.return_value.eq.return_value.limit.return_value.execute.return_value = \
                MagicMock(data=[{"project": "Porterfield",
                                 "sections": [{"section": "Demolition"}, {"section": "Structure"}]}])
        elif name == "commerce_expenses":
            (t.select.return_value.eq.return_value.not_.is_.return_value
             .limit.return_value.execute.return_value) = MagicMock(data=[{"section": "Plumbing"}])
        return t

    mock_db = MagicMock()
    mock_db.table.side_effect = table
    with patch("vula.commerce.expenses._client", return_value=mock_db):
        result = known_sections(TID)

    assert result == ["Demolition", "Plumbing", "Structure"]
