"""Tests for lookup_learned_project()'s multi-project ambiguity detection (2026-08-08 fix).

Real bug this closes: the same recurring payee ("NELETU"/"NELETU CELING"/"NELETHU" — spelling
drift aside, the same learned signal) had been confirmed against three different projects over
time in vula_filing_rules, and the old `.order("hits", desc=True).limit(1)` lookup silently
picked whichever won that round instead of recognizing the payee isn't single-project. See
vula/integrations/doc_filing.py's lookup_learned_project().
"""
from unittest.mock import MagicMock, patch

from vula.integrations.doc_filing import lookup_learned_project

FIELDS = {"reference": "inv-123"}


def _mock_rows(rows):
    mock_table = MagicMock()
    (mock_table.select.return_value.eq.return_value.in_.return_value
     .order.return_value.execute.return_value) = MagicMock(data=rows)
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    return mock_db


def test_single_project_history_is_confident():
    mock_db = _mock_rows([
        {"project": "HPC_Bokaap", "signal": "inv-123", "hits": 3},
    ])
    with patch("vula.integrations.doc_filing._client", return_value=mock_db):
        result = lookup_learned_project("digg-demo", FIELDS)
    assert result["ambiguous"] is False
    assert result["project"] == "HPC_Bokaap"
    assert result["confidence"] == 1.0


def test_multi_project_history_is_ambiguous_not_highest_hits():
    # Old behavior would have returned "HPC_Bokaap" (5 hits) silently. It must now recognize
    # the payee genuinely spans more than one project and ask instead of guessing.
    mock_db = _mock_rows([
        {"project": "HPC_Bokaap", "signal": "inv-123", "hits": 5},
        {"project": "SPORTY.TV", "signal": "inv-123", "hits": 2},
    ])
    with patch("vula.integrations.doc_filing._client", return_value=mock_db):
        result = lookup_learned_project("digg-demo", FIELDS)
    assert result["ambiguous"] is True
    assert result["project"] is None
    assert result["candidates"] == ["HPC_Bokaap", "SPORTY.TV"]


def test_no_matching_signal_returns_none():
    mock_db = _mock_rows([])
    with patch("vula.integrations.doc_filing._client", return_value=mock_db):
        result = lookup_learned_project("digg-demo", FIELDS)
    assert result is None


def test_no_signals_at_all_returns_none_without_querying():
    with patch("vula.integrations.doc_filing._client") as mock_client:
        result = lookup_learned_project("digg-demo", {})
    assert result is None
    mock_client.assert_not_called()
