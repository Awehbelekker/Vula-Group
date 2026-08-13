"""Test for project_financials() including quotes as a distinct figure (2026-08-12 fix).

Real bug this closes: quotes (doc_type='quote', including every BoQ — there's no separate
doc_type for a BoQ) were silently invisible to project_financials()'s money picture entirely,
not just excluded from the contract-value figure. Confirmed live: a real project had R2.9M in
quotes sitting in commerce_invoices with zero visibility here. Kept as a distinct `quoted`
figure rather than merged into `invoiced` — a quote isn't a commitment the way a sent invoice is.
"""
from unittest.mock import MagicMock, patch

from vula.integrations.finances import project_financials

TID = "digg-demo"
PROJECT = "Porterfield"

DOCS = [
    {"total_cents": 100000, "status": "paid", "project": PROJECT, "doc_type": "invoice"},
    {"total_cents": 50000, "status": "draft", "project": PROJECT, "doc_type": "invoice"},
    {"total_cents": 24055353, "status": "draft", "project": PROJECT, "doc_type": "quote"},
    {"total_cents": 30000, "status": "draft", "project": "SomeOtherProject", "doc_type": "quote"},
]


def _empty_execute():
    m = MagicMock()
    m.execute.return_value = MagicMock(data=[])
    return m


def _mock_db():
    def table(name):
        t = MagicMock()
        if name == "commerce_invoices":
            t.select.return_value.eq.return_value.limit.return_value.execute.return_value = \
                MagicMock(data=DOCS)
        else:
            t.select.return_value = _empty_execute()
            t.select.return_value.eq.return_value = _empty_execute()
            t.select.return_value.eq.return_value.eq.return_value = _empty_execute()
            t.select.return_value.eq.return_value.limit.return_value = _empty_execute()
        return t

    mock_db = MagicMock()
    mock_db.table.side_effect = table
    return mock_db


def test_quotes_counted_separately_from_invoiced():
    mock_db = _mock_db()
    with patch("vula.integrations.finances._client", return_value=mock_db):
        result = project_financials(TID, PROJECT)

    assert result["invoiced"] == 1500.00     # only the two real invoices (R1,000 + R500)
    assert result["invoice_count"] == 2
    assert result["quoted"] == 240553.53      # only this project's quote, not the other project's
    assert result["quote_count"] == 1
