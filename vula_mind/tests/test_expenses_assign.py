"""Tests for expenses.assign()'s 2026-08-12 fixes:

1. `project or None` used to silently convert an explicit "" (a real, deliberate "no project"
   answer) back into NULL (still pending) — confirmed live: digg-demo had 29 expenses stuck at
   project IS NULL and zero at project='', meaning "no project" had never once actually stuck.
2. A real (non-empty) project choice now also teaches a supplier->project rule for next time,
   via the same learned-rules mechanism (vula_filing_rules) the document-filing path already
   uses — previously only account_code/category choices were learned, not project ones.
"""
from unittest.mock import MagicMock, patch

from vula.commerce.expenses import assign

TID = "digg-demo"
EID = "exp1"


def _mock_db(row):
    mock_db = MagicMock()
    mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value \
        .limit.return_value.execute.return_value = MagicMock(data=[row] if row else [])
    mock_db.table.return_value.update.return_value.eq.return_value.eq.return_value \
        .execute.return_value = MagicMock(data=[{**(row or {}), "id": EID}])
    return mock_db


def test_explicit_empty_project_is_saved_as_empty_string_not_none():
    row = {"id": EID, "supplier": "Bauxite Extrusions"}
    mock_db = _mock_db(row)
    with (
        patch("vula.commerce.expenses._client", return_value=mock_db),
        patch("vula.integrations.doc_filing.learn_filing_rule") as mock_learn,
    ):
        assign(TID, EID, project="")

    patch_arg = mock_db.table.return_value.update.call_args[0][0]
    assert patch_arg["project"] == ""
    mock_learn.assert_not_called()


def test_project_not_provided_leaves_it_untouched():
    row = {"id": EID, "supplier": "Bauxite Extrusions"}
    mock_db = _mock_db(row)
    with patch("vula.commerce.expenses._client", return_value=mock_db):
        assign(TID, EID, category="Stock")

    patch_arg = mock_db.table.return_value.update.call_args[0][0]
    assert "project" not in patch_arg


def test_real_project_choice_learns_supplier_rule():
    row = {"id": EID, "supplier": "Bauxite Extrusions"}
    mock_db = _mock_db(row)
    with (
        patch("vula.commerce.expenses._client", return_value=mock_db),
        patch("vula.integrations.doc_filing.learn_filing_rule") as mock_learn,
    ):
        assign(TID, EID, project="HPC_Bokaap")

    patch_arg = mock_db.table.return_value.update.call_args[0][0]
    assert patch_arg["project"] == "HPC_Bokaap"
    mock_learn.assert_called_once_with(TID, {"supplier": "Bauxite Extrusions"}, "HPC_Bokaap")


def test_no_supplier_on_row_skips_learning():
    row = {"id": EID, "supplier": None}
    mock_db = _mock_db(row)
    with (
        patch("vula.commerce.expenses._client", return_value=mock_db),
        patch("vula.integrations.doc_filing.learn_filing_rule") as mock_learn,
    ):
        assign(TID, EID, project="HPC_Bokaap")
    mock_learn.assert_not_called()


def test_notes_are_saved():
    row = {"id": EID, "supplier": "Bauxite Extrusions"}
    mock_db = _mock_db(row)
    with patch("vula.commerce.expenses._client", return_value=mock_db):
        assign(TID, EID, notes="Ask the accountant about this one")

    patch_arg = mock_db.table.return_value.update.call_args[0][0]
    assert patch_arg["notes"] == "Ask the accountant about this one"
