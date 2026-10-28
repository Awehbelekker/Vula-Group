"""A bank line goes on the project it names (vula/commerce/allocation.py), 28 Oct.

Ian, importing Porterfield's statements: "FNB App Payment To Sporty Plumming 22 Porterfield"
stayed off Porterfield because no worker, bill or learned rule matched it, though the line names
the project. Names are matched more strictly than on documents: registered projects only, no
project numbers, and a lead word only from 6 letters. All figures made up.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.test_job_costing import FakeDB
from vula.commerce import allocation, bank_rec, bank_review, service

TID = "digg-demo"
REGISTER = [
    {"tenant_id": TID, "name": "Porterfield", "number": "2604", "aliases": []},
    {"tenant_id": TID, "name": "Belladonna", "number": None, "aliases": []},
    {"tenant_id": TID, "name": "Life Changers - Tableview", "number": None, "aliases": []},
    {"tenant_id": TID, "name": "HPC Bokaap", "number": "DIGG-2024-017", "aliases": []},
    {"tenant_id": TID, "name": "Sporty TV", "number": None, "aliases": ["SPORTY.TV"]},
    {"tenant_id": TID, "name": "Sporty TV – Phase 2", "number": None, "aliases": []},
]


@pytest.fixture()
def db(monkeypatch):
    fake = FakeDB({"vula_projects": [dict(r) for r in REGISTER]})
    monkeypatch.setattr(service, "_client", lambda: fake)
    monkeypatch.setattr("vula.commerce.expenses.known_projects", lambda tid: ["Some Old Expense Job"])
    monkeypatch.setattr("vula.integrations.doc_filing._own_names", lambda tid: ["DIGG"])
    monkeypatch.setattr("vula.api.tenants.uses_projects", lambda tid: True)
    return fake


@pytest.mark.parametrize("line, want", [
    ("FNB App Payment To Sporty Plumming 22 Porterfield", "Porterfield"),
    ("Send Money App Dr Send Belladona Tiles", "Belladonna"),          # a typo of a long name
    ("FNB App Payment To 22 Porterfield Wages Digg Wage", "Porterfield"),
    ("POS Purchase Bwh Tableview 491050*9369 11 Jul", None),           # a suburb, not the job
    ("Debit Order Discovery Life 2604", None),                         # no lead word, no number
    ("POS Purchase Bokaap Mini Market", None),
    ("FNB App Payment To HPC Doors", None),                            # HPC stays with its rule
    ("Payment To Sporty Ceilings", None),                              # two Sporty projects
    ("Payment To Some Old Expense Job", None),                         # not in the register
    ("Belladonna and Porterfield tiles", None),                        # two projects: not guessed
])
def test_a_line_names_one_registered_project(db, line, want):
    assert allocation.named_project(allocation.naming_projects(TID), line) == want


def test_a_shop_has_no_projects_to_name(db, monkeypatch):
    monkeypatch.setattr("vula.api.tenants.uses_projects", lambda tid: False)
    assert allocation.naming_projects(TID) == []


def test_lines_already_saved_go_on_the_project_they_name(db):
    db.tables["commerce_bank_transactions"] = [
        {"id": "b1", "tenant_id": TID, "description": "FNB App Payment To Sporty Plumming 22 Porterfield",
         "project": None, "match_status": "unmatched"},
        {"id": "b2", "tenant_id": TID, "description": "POS Purchase Bwh Tableview", "project": None,
         "match_status": "unmatched"},
        {"id": "b3", "tenant_id": TID, "description": "Payment To Porterfield", "project": None,
         "match_status": "ignored"},                                   # set aside: left alone
    ]
    with patch("vula.commerce.ledger._all_pages",
               side_effect=lambda make: [r for r in db.tables["commerce_bank_transactions"]]):
        assert allocation.apply_to_existing(TID) == 1
    got = {r["id"]: r["project"] for r in db.tables["commerce_bank_transactions"]}
    assert got == {"b1": "Porterfield", "b2": None, "b3": None}


async def test_a_statement_puts_named_lines_on_their_project(db):
    txns = [
        {"date": "2026-10-20", "description": "FNB App Payment To Sporty Plumming 22 Porterfield",
         "amount_cents": 405000, "direction": "out", "balance_cents": None, "reference": None},
        {"date": "2026-10-21", "description": "POS Purchase Bwh Tableview", "amount_cents": 10620,
         "direction": "out", "balance_cents": None, "reference": None},
    ]
    cats = [{"account_code": "cost_of_sales", "source": "ai"}] * 2
    with (patch("vula.commerce.accounting.ensure_chart", return_value=[{"code": "cost_of_sales"}]),
          patch("vula.commerce.accounting.categorize_batch", new=AsyncMock(return_value=cats)),
          patch("vula.commerce.accounting.vat_for", return_value=0),
          patch.object(service, "get_invoice_settings", new=AsyncMock(return_value={})),
          patch("vula.commerce.labour.list_workers", return_value=[], create=True),
          patch.object(bank_rec, "_client", return_value=db)):
        rec = await bank_rec.reconcile(TID, txns, "oct.pdf", auto_settle=False)
    rows = {r["description"]: r.get("project") for r in db.tables["commerce_bank_transactions"]}
    assert rows == {"FNB App Payment To Sporty Plumming 22 Porterfield": "Porterfield",
                    "POS Purchase Bwh Tableview": None}
    assert rec["named_projects"] == {"Porterfield": 1}
    assert "On the project they name: Porterfield 1" in bank_review.named_line(rec)
    assert bank_review.named_line({"named_projects": {}}) == ""


async def test_an_owners_earlier_project_still_wins(db):
    db.tables["commerce_bank_transactions"] = [
        {"tenant_id": TID, "txn_date": "2026-10-20", "amount_cents": 405000,
         "description": "FNB App Payment To Sporty Plumming 22 Porterfield", "project": "Belladonna",
         "categorized_by": "owner", "account_code": "cost_of_sales"}]
    txns = [{"date": "2026-10-20", "description": "FNB App Payment To Sporty Plumming 22 Porterfield",
             "amount_cents": 405000, "direction": "out", "balance_cents": None, "reference": None}]
    with (patch("vula.commerce.accounting.ensure_chart", return_value=[{"code": "cost_of_sales"}]),
          patch("vula.commerce.accounting.categorize_batch",
                new=AsyncMock(return_value=[{"account_code": "cost_of_sales", "source": "ai"}])),
          patch("vula.commerce.accounting.vat_for", return_value=0),
          patch.object(service, "get_invoice_settings", new=AsyncMock(return_value={})),
          patch.object(bank_rec, "_client", return_value=db)):
        rec = await bank_rec.reconcile(TID, txns, "oct.pdf", auto_settle=False)
    assert db.tables["commerce_bank_transactions"][0]["project"] == "Belladonna"
    assert rec["named_projects"] == {}


def test_naming_never_blocks_an_import(monkeypatch):
    monkeypatch.setattr("vula.api.tenants.uses_projects", MagicMock(side_effect=RuntimeError("down")))
    assert allocation.naming_projects(TID) == []
    assert allocation.named_project([], "anything") is None
