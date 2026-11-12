"""A bank statement is the whole business's, unless the owner ties that account to one project
(11 Nov 2026, Ian: "6574 is Porterfield's project bank account"). Migration 261. Figures made up."""
from unittest.mock import AsyncMock, patch

import pytest

from tests.test_job_costing import FakeDB
from vula.commerce import bank_accounts, bank_rec, service
from vula.integrations import doc_filing

TID = "digg-demo"
MAIN, PORTERFIELD = "63214254607", "62009876574"
REGISTER = [{"tenant_id": TID, "name": "Porterfield", "number": "2604", "aliases": []},
            {"tenant_id": TID, "name": "Belladonna", "number": None, "aliases": []},
            {"tenant_id": TID, "name": "HPC Bokaap", "number": None, "aliases": ["HPC"]}]


@pytest.fixture()
def db(monkeypatch):
    fake = FakeDB({"vula_projects": [dict(r) for r in REGISTER], "commerce_bank_accounts": [],
                   "commerce_bank_transactions": []})
    monkeypatch.setattr(service, "_client", lambda: fake)
    monkeypatch.setattr(bank_accounts, "available", lambda: True)
    monkeypatch.setattr("vula.integrations.doc_filing._own_names", lambda tid: ["DIGG"])
    monkeypatch.setattr("vula.api.tenants.uses_projects", lambda tid: True)
    monkeypatch.setattr(service, "canonical_project", lambda tid, p: p)
    return fake


# ── which account a statement is for ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, want", [
    ("Platinum Business Account : 63214254607\nStatement Period : 1 Oct 2026 to 31 Oct 2026", MAIN),
    ("Account Number: 6200 987 6574\nOpening Balance", PORTERFIELD),
    ("Account no. 62-0098-76574", PORTERFIELD),
    ("Gold Business Account 62009876574", PORTERFIELD),
    ("Tel 0860 123 456\nStatement Period : 1 Oct", None),          # a phone number is not an account
    ("", None),
])
def test_the_account_is_read_off_the_statement(text, want):
    assert bank_accounts.account_number(text) == want


def test_the_bank_is_named():
    assert bank_accounts.bank_name("First National Bank\nPlatinum Business Account") == "FNB"
    assert bank_accounts.bank_name("nothing here") is None


# ── a statement is never filed on a project ──────────────────────────────────────────────────

@pytest.mark.parametrize("category, summary, filename, want", [
    ("General Document", "This document is a bank statement for AWEH BE LEKKER (PTY) LTD from FNB, "
     "covering 8 to 17 August 2026.", "17 Aug 2026 - (Free)..CVF4.pdf", True),
    ("Account Statement", "An account statement from First National Bank for account 63160575256.",
     "Account Statement - First National Bank 20261111-1236.pdf", True),
    ("Bank Statement", "Statement", "x.pdf", True),
    ("Account Statement", "Statement of account from Hout Bay Handiman Centre CC: amount due R4,200. "
     "Pay into FNB account 62...", "Account Statement - Hout Bay Handiman Centre CC.pdf", False),
    ("Report", "Accounts Receivable statement of Solid Cape for DIGG's account", "Accounts Receivable Statements.pdf", False),
    ("Invoice", "Tax invoice from FNB for bank charges", "inv.pdf", False),
    ("Activity Statement", "Activity statement for Enziglo (Pty) Ltd", "Activity Statement for Enziglo.pdf", False),
])
def test_a_bank_statement_is_told_apart_from_a_suppliers(category, summary, filename, want):
    assert doc_filing.is_bank_statement(category, summary, filename) is want


@pytest.mark.asyncio
async def test_a_bank_statement_naming_porterfield_is_filed_for_the_whole_business(monkeypatch):
    """Twelve of DIGG's FNB statements were filed under Porterfield because a line read "22 Porterfield"."""
    saved = {}

    class _T:
        def upsert(self, row, **k):
            saved.update(row)
            return self

        def insert(self, row):
            saved.update(row)
            return self

        def select(self, *a):
            return self

        def eq(self, *a):
            return self

        def is_(self, *a):
            return self

        def limit(self, *a):
            return self

        def execute(self):
            from types import SimpleNamespace
            return SimpleNamespace(data=[dict(saved, id="d1")])
    monkeypatch.setattr(doc_filing, "_client", lambda: type("C", (), {"table": lambda s, n: _T()})())
    monkeypatch.setattr("vula.commerce.plan_limits.check_document_quota", lambda t: None)
    monkeypatch.setattr(doc_filing, "_store_copy", AsyncMock(return_value=None), raising=False)
    await doc_filing.file_document(
        TID, filename="17 Aug 2026 - (Free)..CVF4.pdf", data=b"%PDF", content_type="application/pdf",
        category="General Document", summary="A bank statement for AWEH BE LEKKER (PTY) LTD from FNB.",
        project="Porterfield", status="filed")
    assert saved.get("project") is None and saved.get("status") == "filed"


def test_whatsapp_and_email_never_ask_which_project_for_a_statement():
    import inspect
    from vula.api import whatsapp
    from vula.email_imap import sync
    assert "or is_bank_statement(category, summary, result.filename)" in inspect.getsource(whatsapp)
    src = inspect.getsource(sync._file_attachment)
    assert "not bank_statement and uses_projects(tenant_id)" in src


# ── an account kept for one project ──────────────────────────────────────────────────────────

def _txns():
    return [
        {"date": "2026-10-20", "description": "FNB App Payment To Sporty Plumming 22 Belladonna",
         "amount_cents": 405000, "direction": "out", "balance_cents": None, "reference": None},
        {"date": "2026-10-21", "description": "POS Purchase Bwh Tableview", "amount_cents": 10620,
         "direction": "out", "balance_cents": None, "reference": None},
    ]


async def _reconcile(db, number):
    cats = [{"account_code": "cost_of_sales", "source": "ai"}] * 2
    with (patch("vula.commerce.accounting.ensure_chart", return_value=[{"code": "cost_of_sales"}]),
          patch("vula.commerce.accounting.categorize_batch", new=AsyncMock(return_value=cats)),
          patch("vula.commerce.accounting.vat_for", return_value=0),
          patch.object(service, "get_invoice_settings", new=AsyncMock(return_value={})),
          patch("vula.commerce.labour.list_workers", return_value=[], create=True),
          patch.object(bank_rec, "_client", return_value=db)):
        return await bank_rec.reconcile(TID, _txns(), "oct.pdf", auto_settle=False, bank_account=number)


@pytest.mark.asyncio
async def test_every_line_from_the_porterfield_account_is_porterfields(db):
    db.tables["commerce_bank_accounts"] = [{"id": "a2", "tenant_id": TID, "account_number": PORTERFIELD,
                                            "project": "Porterfield"}]
    await _reconcile(db, PORTERFIELD)
    rows = {r["description"]: (r["project"], r["project_source"], r["bank_account"])
            for r in db.tables["commerce_bank_transactions"]}
    assert rows == {"FNB App Payment To Sporty Plumming 22 Belladonna": ("Porterfield", "account", PORTERFIELD),
                    "POS Purchase Bwh Tableview": ("Porterfield", "account", PORTERFIELD)}


@pytest.mark.asyncio
async def test_the_business_account_puts_lines_on_projects_one_by_one(db):
    db.tables["commerce_bank_accounts"] = [{"id": "a1", "tenant_id": TID, "account_number": MAIN, "project": None}]
    await _reconcile(db, MAIN)
    rows = {r["description"]: (r["project"], r["project_source"]) for r in db.tables["commerce_bank_transactions"]}
    assert rows == {"FNB App Payment To Sporty Plumming 22 Belladonna": ("Belladonna", "named"),
                    "POS Purchase Bwh Tableview": (None, None)}


@pytest.mark.asyncio
async def test_the_owners_own_choice_still_wins_over_the_account(db):
    db.tables["commerce_bank_accounts"] = [{"id": "a2", "tenant_id": TID, "account_number": PORTERFIELD,
                                            "project": "Porterfield"}]
    db.tables["commerce_bank_transactions"] = [
        {"tenant_id": TID, "txn_date": "2026-10-21", "amount_cents": 10620, "description": "POS Purchase Bwh Tableview",
         "project": "HPC Bokaap", "categorized_by": "owner", "account_code": "cost_of_sales"}]
    await _reconcile(db, PORTERFIELD)
    got = {r["description"]: (r["project"], r.get("project_source")) for r in db.tables["commerce_bank_transactions"]}
    assert got["POS Purchase Bwh Tableview"] == ("HPC Bokaap", "owner")


def test_tying_an_account_moves_its_lines_and_untying_moves_them_back(db):
    db.tables["commerce_bank_accounts"] = [{"id": "a2", "tenant_id": TID, "account_number": PORTERFIELD, "project": None}]
    db.tables["commerce_bank_transactions"] = [
        {"id": "t1", "tenant_id": TID, "bank_account": PORTERFIELD, "project": None, "project_source": None},
        {"id": "t2", "tenant_id": TID, "bank_account": PORTERFIELD, "project": "Belladonna", "project_source": "named"},
        {"id": "t3", "tenant_id": TID, "bank_account": PORTERFIELD, "project": "HPC Bokaap", "project_source": "owner"},
        {"id": "t4", "tenant_id": TID, "bank_account": MAIN, "project": None, "project_source": None},
    ]
    res = bank_accounts.set_project(TID, "a2", "Porterfield")
    assert res == {"ok": True, "account": "…6574", "project": "Porterfield", "lines_moved": 2}
    got = {r["id"]: (r["project"], r["project_source"]) for r in db.tables["commerce_bank_transactions"]}
    assert got == {"t1": ("Porterfield", "account"), "t2": ("Porterfield", "account"),
                   "t3": ("HPC Bokaap", "owner"), "t4": (None, None)}
    res = bank_accounts.set_project(TID, "a2", "")
    got = {r["id"]: (r["project"], r["project_source"]) for r in db.tables["commerce_bank_transactions"]}
    assert res["project"] is None and got["t1"] == (None, None) and got["t3"] == ("HPC Bokaap", "owner")
    assert bank_accounts.set_project(TID, "nope", "Porterfield") == {"error": "account not found"}


def test_lines_read_before_the_migration_get_their_account(db):
    db.tables["commerce_bank_accounts"] = [{"id": "a2", "tenant_id": TID, "account_number": PORTERFIELD,
                                            "project": "Porterfield"}]
    db.tables["commerce_bank_transactions"] = [
        {"id": "t1", "tenant_id": TID, "source_file": "31 Aug 2026 - (Free).pdf", "bank_account": None, "project": None},
        {"id": "t2", "tenant_id": TID, "source_file": "31 Aug 2026 - (Free).pdf", "bank_account": None,
         "project": "HPC Bokaap", "project_source": "owner"},
        {"id": "t3", "tenant_id": TID, "source_file": "other.pdf", "bank_account": None, "project": None},
    ]
    assert bank_accounts.tag_lines(TID, "31 Aug 2026 - (Free).pdf", PORTERFIELD) == 2
    got = {r["id"]: (r["bank_account"], r["project"]) for r in db.tables["commerce_bank_transactions"]}
    assert got == {"t1": (PORTERFIELD, "Porterfield"), "t2": (PORTERFIELD, "HPC Bokaap"), "t3": (None, None)}


def test_an_account_is_remembered_once_with_its_dates(db):
    bank_accounts.remember(TID, MAIN, bank="FNB", seen="2026-10-05")
    bank_accounts.remember(TID, MAIN, bank="FNB", seen="2026-10-31")
    bank_accounts.remember(TID, MAIN, seen="2026-09-30")
    rows = db.tables["commerce_bank_accounts"]
    assert len(rows) == 1 and rows[0]["bank"] == "FNB"
    assert (str(rows[0]["first_seen"]), str(rows[0]["last_seen"])) == ("2026-09-30", "2026-10-31")
    assert bank_accounts.remember(TID, None) is None


def test_before_the_migration_nothing_changes(monkeypatch):
    monkeypatch.setattr(bank_accounts, "available", lambda: False)
    assert bank_accounts.remember(TID, MAIN) is None
    assert bank_accounts.project_for(TID, MAIN) is None
    assert bank_accounts.list_accounts(TID) == []
    assert bank_accounts.set_project(TID, "a1", "Porterfield") == {"error": "Bank accounts need migration 261."}
