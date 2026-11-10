"""One invoice for several jobs, split per project (vula/commerce/site_split.py, 10 Nov 2026).

The case: skip invoice 1091 (R4,150) — R1,400 for "07 Belladonna Drive", R2,750 for two skips at
"8 Wessex, Paarden Island" — whose whole payment had been booked on Belladonna."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from vula.commerce import data_checks
from vula.commerce import site_split as ss

T = "test-tenant"
PROJECTS = [{"name": "Belladonna", "aliases": []},
            {"name": "Atlantis Paarden Eiland", "aliases": ["ATLANTIS FOODS"]},
            {"name": "HPC Bokaap", "aliases": []},
            {"name": "Porterfield", "aliases": []},
            {"name": "Sporty TV", "aliases": ["SPORTY.TV"]},
            {"name": "Sporty TV – Phase 2", "aliases": []}]

INV_1091 = {"supplier": "R Engelbrecht", "date": "2026-10-22", "total_cents": 415000, "line_items": [
    {"description": "16th Oct 2026 1 x Skip Rental @ R1250 - 8 Wessex Paarden Island Road", "total_cents": 125000},
    {"description": "23rd Oct 2026 1 x Skip Rental with glass removal @ R1500.00 - 8 Wessex Paarden Island Road",
     "total_cents": 150000},
    {"description": "23rd Oct 2026 1 x Skip Rental @ R1400.00 - 07 Belladonna Drive, Vredehoek", "total_cents": 140000}]}


@pytest.mark.parametrize("text,want", [
    ("07 Belladonna Drive, Vredehoek", "Belladonna"),
    ("Skip - 8 Wessex Paarden Island Road", "Atlantis Paarden Eiland"),
    ("Bella dona site", "Belladonna"),                     # handwritten, split word
    ("skip at Belladona", "Belladonna"),                   # misspelt
    ("HPC Bokaap snag list", "HPC Bokaap"),
    ("Skip rental — road works, new site", None),          # only generic words
    ("Sporty TV", None),                                   # two projects named equally: never a guess
    ("Belladonna and HPC", None),
])
def test_a_line_names_its_project_by_the_projects_own_words(text, want):
    assert ss.match_project(text, PROJECTS) == want


def test_invoice_1091_splits_into_belladonna_and_paarden_island():
    p = ss.propose(INV_1091, PROJECTS, "Belladonna")
    assert [(x["project"], x["amount_cents"]) for x in p["parts"]] == [
        ("Belladonna", 140000), ("Atlantis Paarden Eiland", 275000)]
    assert p["home"] == "Belladonna" and p["total_cents"] == 415000 and not p["scaled"] and p["unmatched"] == []


def test_an_invoice_for_one_job_is_left_alone():
    one = {"total_cents": 140000, "line_items": [INV_1091["line_items"][2]]}
    assert ss.propose(one, PROJECTS, "Belladonna") is None
    assert ss.propose({"total_cents": 5, "line_items": []}, PROJECTS) is None


def test_lines_before_vat_are_scaled_to_the_total_and_still_add_up():
    inv = {"total_cents": 115000, "line_items": [
        {"description": "Hire - Belladonna", "total_cents": 33333},
        {"description": "Hire - HPC", "total_cents": 66667}]}
    p = ss.propose(inv, PROJECTS)
    assert p["scaled"] and sum(x["amount_cents"] for x in p["parts"]) == 115000
    assert {x["project"]: x["amount_cents"] for x in p["parts"]} == {"HPC Bokaap": 76668, "Belladonna": 38332}


def test_a_line_that_names_no_job_stays_with_the_job_it_was_filed_to():
    inv = {"total_cents": 300000, "line_items": [
        {"description": "Skip - Belladonna Drive", "total_cents": 100000},
        {"description": "Skip - HPC", "total_cents": 100000},
        {"description": "Delivery fee", "total_cents": 100000}]}
    p = ss.propose(inv, PROJECTS, "HPC Bokaap")
    assert {x["project"]: x["amount_cents"] for x in p["parts"]} == {"HPC Bokaap": 200000, "Belladonna": 100000}
    assert p["unmatched"][0]["description"] == "Delivery fee"


def test_filing_records_the_proposal_keeps_it_off_client_links_and_tells_the_sender():
    with patch.object(ss, "_projects", return_value=PROJECTS):
        fields, issues = ss.at_filing(T, "Invoice", INV_1091, "Belladonna")
    assert fields["_site_split"]["status"] == "proposed" and fields["_hidden_from_client"] is True
    assert [i["code"] for i in issues] == ["multi_site"]
    assert "Belladonna R1,400.00; Atlantis Paarden Eiland R2,750.00" in issues[0]["message"]
    # a drawing or a quote with site names is never split
    with patch.object(ss, "_projects", return_value=PROJECTS):
        assert ss.at_filing(T, "Drawing / Plan", INV_1091, "Belladonna") == (dict(INV_1091), [])


def test_filing_never_fails_over_the_check():
    with patch.object(ss, "_projects", side_effect=RuntimeError("db down")):
        fields, issues = ss.at_filing(T, "Invoice", INV_1091, "Belladonna")
    assert issues == [] and "_site_split" not in fields


# ── applying it: the bank payment is split, read back ──────────────────────────────────────────

class _Q:
    def __init__(self, db, name):
        self.db, self.name, self.f, self.patch, self.rows = db, name, [], None, None

    def select(self, *a): return self
    def order(self, *a, **k): return self
    def limit(self, *a): return self
    def eq(self, k, v): self.f.append(lambda r, k=k, v=v: r.get(k) == v); return self
    def neq(self, k, v): self.f.append(lambda r, k=k, v=v: r.get(k) != v); return self
    def gte(self, k, v): self.f.append(lambda r, k=k, v=v: str(r.get(k)) >= v); return self
    def lte(self, k, v): self.f.append(lambda r, k=k, v=v: str(r.get(k)) <= v); return self
    def update(self, row): self.patch = row; return self

    def insert(self, rows):
        for r in rows if isinstance(rows, list) else [rows]:
            self.db.t.setdefault(self.name, []).append(dict(r, id=f"{self.name}-{len(self.db.t.get(self.name, []))}"))
        return self

    def execute(self):
        hit = [r for r in self.db.t.setdefault(self.name, []) if all(f(r) for f in self.f)]
        if self.patch is not None:
            for r in hit:
                r.update(self.patch)
        return SimpleNamespace(data=hit)


class _DB:
    def __init__(self, **t): self.t = t
    def table(self, n): return _Q(self, n)


def _db(*payments):
    with patch.object(ss, "_projects", return_value=PROJECTS):
        fields, _ = ss.at_filing(T, "Invoice", INV_1091, "Belladonna")
    return _DB(vula_filed_documents=[{"id": "doc1", "tenant_id": T, "project": "Belladonna", "category": "Invoice",
                                      "filename": "Invoice_1091.pdf", "fields": fields, "created_at": "2026-11-10"}],
               commerce_bank_transactions=[dict(p, tenant_id=T, direction="out", account_code="cost_of_sales")
                                           for p in payments],
               commerce_bank_splits=[])


def _run(db, fn, *a):
    from vula.commerce import bank_splits, expenses
    with (patch.object(ss, "_client", return_value=db), patch.object(bank_splits, "_client", return_value=db),
          patch.object(expenses, "known_projects", return_value=[p["name"] for p in PROJECTS]),
          patch("vula.commerce.decisions.record", return_value=None)):
        return fn(*a)


PAY = {"id": "t1", "txn_date": "2026-10-23", "description": "FNB App Payment To Skipp Rubble Removal",
       "amount_cents": 415000, "project": "Belladonna"}


def test_confirming_splits_the_payment_and_marks_the_invoice():
    db = _db(PAY)
    out = _run(db, ss.apply, T, "doc1", None, "judy@example.com")
    assert out["parts"] == [{"project": "Belladonna", "amount_cents": 140000},
                            {"project": "Atlantis Paarden Eiland", "amount_cents": 275000}]
    split = [r for r in db.t["commerce_bank_splits"] if r["status"] == "active"]
    assert [(r["project"], r["amount_cents"], r["txn_id"]) for r in split] == [("Atlantis Paarden Eiland", 275000, "t1")]
    s = db.t["vula_filed_documents"][0]["fields"]["_site_split"]
    assert s["status"] == "applied" and s["txn_id"] == "t1" and s["by"] == "judy@example.com"
    assert _run(db, ss.pending, T, db.t["vula_filed_documents"]) == []      # off the to-check list


def test_a_payment_on_no_project_goes_to_the_invoices_job_first():
    db = _db(dict(PAY, project=None))
    _run(db, ss.apply, T, "doc1", None, "x")
    assert db.t["commerce_bank_transactions"][0]["project"] == "Belladonna"


def test_no_payment_yet_or_two_that_could_be_it_ask_the_owner():
    with pytest.raises(LookupError, match="R4,150.00 payment"):
        _run(_db(), ss.apply, T, "doc1", None, "x")
    two = _db(PAY, dict(PAY, id="t2", txn_date="2026-11-02"))
    with pytest.raises(ValueError, match="pick one"):
        _run(two, ss.apply, T, "doc1", None, "x")
    out = _run(two, ss.apply, T, "doc1", "t2", "x")
    assert out["payment"]["id"] == "t2"
    # a payment months off the invoice's date is never offered
    far = _db(dict(PAY, txn_date="2027-06-01"))
    with pytest.raises(LookupError):
        _run(far, ss.apply, T, "doc1", None, "x")


def test_the_health_list_shows_invoices_waiting_for_a_split():
    db = _db(PAY)
    items = _run(db, ss.pending, T, db.t["vula_filed_documents"])
    assert items[0]["action"] == "site_split" and items[0]["id"] == "doc1"
    assert "R4,150.00 across 2 jobs" in items[0]["detail"]
    assert "multi_site" in data_checks._TITLES


def test_a_bank_statement_filed_as_an_invoice_is_never_split():
    # DIGG's FNB statement (31 Aug – 7 Sep) was filed as an "Invoice": its lines are transactions that
    # mention HPC and Belladonna, with no invoice total
    statement = {"supplier": "First National Bank", "line_items": [
        {"description": "Int-Banking Pmt Frm Hpc-PC09", "total_cents": 14210145},
        {"description": "Magtape Credit ABSA Bank 7 Belladonna Ave", "total_cents": 1200000},
        {"description": "POS Purchase Kwikspar Blouberg", "total_cents": 21002}]}
    assert ss.propose(statement, PROJECTS) is None
    # nor an invoice whose lines don't add up to its total, or that is mostly lines naming no job
    assert ss.propose(dict(INV_1091, total_cents=999999), PROJECTS) is None
    mostly_unnamed = {"total_cents": 1000000, "line_items": [
        {"description": "Belladonna", "total_cents": 100000}, {"description": "HPC", "total_cents": 100000},
        {"description": "Materials", "total_cents": 800000}]}
    assert ss.propose(mostly_unnamed, PROJECTS) is None
