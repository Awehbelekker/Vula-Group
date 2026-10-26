"""Moving a bill or document filed under the wrong project, 26 Oct.

Ian: "Can we not add that the tenant can change the allocated invoice to a project if Vula filed
it incorrectly or even the tenant mistakenly allocated?" Pudlo's Belladonna tile invoices were
filed under HPC Bokaap; nothing moved an already-filed bill or document, and the invoice edit
form's project change was dropped without a word.
"""
from types import SimpleNamespace

import pytest

from vula.integrations import doc_filing


class _Q:
    def __init__(self, db, name):
        self.db, self.name, self.filters, self.patch, self.kill = db, name, [], None, False

    def select(self, *a): return self
    def order(self, *a, **k): return self
    def limit(self, *a): return self
    def eq(self, k, v): self.filters.append(lambda r, k=k, v=v: _get(r, k) == v); return self
    def neq(self, k, v): self.filters.append(lambda r, k=k, v=v: _get(r, k) != v); return self
    def in_(self, k, vs): self.filters.append(lambda r, k=k, vs=vs: _get(r, k) in vs); return self
    def ilike(self, k, v): self.filters.append(lambda r, k=k, v=v: str(_get(r, k)).lower() == v.lower()); return self
    def update(self, row): self.patch = row; return self
    def delete(self): self.kill = True; return self
    def insert(self, row): self.db.tables.setdefault(self.name, []).append(dict(row)); return self

    def execute(self):
        rows = self.db.tables.setdefault(self.name, [])
        hit = [r for r in rows if all(f(r) for f in self.filters)]
        if self.patch is not None:
            for r in hit:
                r.update(self.patch)
        if self.kill:
            self.db.tables[self.name] = [r for r in rows if r not in hit]
        return SimpleNamespace(data=hit)


def _get(row, key):
    if "->>" in key:
        col, sub = key.split("->>")
        return (row.get(col) or {}).get(sub)
    return row.get(key)


class _DB:
    def __init__(self, **t): self.tables = t
    def table(self, name): return _Q(self, name)


def _world():
    return _DB(
        vula_filed_documents=[
            {"id": "d1", "tenant_id": "digg-demo", "project": "HPC Bokaap", "status": "filed",
             "category": "Invoice", "filename": "Pudlo IN411214.pdf", "file_url": "https://f/d1",
             "commerce_invoice_id": "b1", "doc_id": "k1",
             "fields": {"supplier": "Pudlo S.A (Pty) Ltd", "document_number": "IN411214",
                        "total_cents": 1422845}},
            {"id": "d2", "tenant_id": "digg-demo", "project": "HPC Bokaap", "status": "filed",
             "fields": {"page_of": "d1", "page": 2}},
            {"id": "x1", "tenant_id": "other", "project": "HPC Bokaap", "commerce_invoice_id": "b1",
             "fields": {"page_of": "d1"}},
        ],
        commerce_invoices=[
            {"id": "b1", "tenant_id": "digg-demo", "invoice_number": "DIG-BILL-00139",
             "project": "HPC Bokaap", "supplier": "Pudlo", "total_cents": 1422845},
            {"id": "b1", "tenant_id": "other", "invoice_number": "OTH-BILL-1", "project": "HPC Bokaap"},
        ],
        commerce_expenses=[
            {"id": "e1", "tenant_id": "digg-demo", "receipt_url": "https://f/d1", "status": "pending",
             "project": "HPC Bokaap"},
            {"id": "e2", "tenant_id": "digg-demo", "receipt_url": "https://f/d1", "status": "cancelled",
             "project": "HPC Bokaap"},
        ],
        vula_filing_rules=[
            {"id": "r1", "tenant_id": "digg-demo", "signal": "pudlo s.a (pty) ltd", "project": "HPC Bokaap"},
            {"id": "r2", "tenant_id": "other", "signal": "pudlo s.a (pty) ltd", "project": "HPC Bokaap"},
        ],
        vula_project_boq=[],
    )


@pytest.fixture
def world(monkeypatch):
    db = _world()
    monkeypatch.setattr(doc_filing, "_client", lambda: db)
    monkeypatch.setattr("vula.commerce.service._client", lambda: db)
    monkeypatch.setattr("vula.commerce.expenses.known_projects", lambda t: ["Belladonna", "HPC Bokaap"])
    monkeypatch.setattr("vula.commerce.service.canonical_project",
                        lambda t, p: {"belladona": "Belladonna", "belladonna": "Belladonna"}.get(p.lower(), p))
    monkeypatch.setattr(doc_filing, "_signals_from", lambda f, t: [("supplier", "pudlo s.a (pty) ltd")])
    moved_prices = []
    monkeypatch.setattr("vula.commerce.price_book.set_project",
                        lambda t, d, p: moved_prices.append((t, d, p)))
    closed = []
    monkeypatch.setattr("vula.open_questions.close_for", lambda t, ref: closed.append((t, ref)))
    monkeypatch.setattr("vula.integrations.finances.post_finance_from_doc", lambda *a, **k: None)
    db.prices, db.closed = moved_prices, closed
    return db


def _project(db, table, rid, tenant="digg-demo"):
    return next(r["project"] for r in db.tables[table] if r["id"] == rid and r["tenant_id"] == tenant)


def test_moving_a_document_moves_its_bill_expense_pages_and_prices(world):
    res = doc_filing.move_to_project("digg-demo", "Belladonna", doc_id="d1")
    assert res["ok"] and (res["from"], res["to"]) == ("HPC Bokaap", "Belladonna")
    assert res["moved"] == {"vula_filed_documents": 2, "commerce_invoices": 1, "commerce_expenses": 1}
    for table, rid in (("vula_filed_documents", "d1"), ("vula_filed_documents", "d2"),
                       ("commerce_invoices", "b1"), ("commerce_expenses", "e1")):
        assert _project(world, table, rid) == "Belladonna"
    assert _project(world, "commerce_expenses", "e2") == "HPC Bokaap"       # cancelled stays put
    assert {d for _, d, _ in world.prices} == {"d1", "d2"}
    assert world.closed == [("digg-demo", "d1")]
    assert doc_filing.move_summary(res) == ("Moved to Belladonna (from HPC Bokaap): bill DIG-BILL-00139, "
                                            "the document (2 pages) and 1 expense.")


def test_moving_the_bill_moves_its_document(world):
    res = doc_filing.move_to_project("digg-demo", "belladona", invoice_id="b1")
    assert res["ok"] and res["to"] == "Belladonna"
    assert _project(world, "vula_filed_documents", "d1") == "Belladonna"


def test_a_later_page_moves_the_whole_document(world):
    assert doc_filing.move_to_project("digg-demo", "Belladonna", doc_id="d2")["ok"]
    assert _project(world, "commerce_invoices", "b1") == "Belladonna"


def test_another_tenants_rows_are_never_touched(world):
    doc_filing.move_to_project("digg-demo", "Belladonna", doc_id="d1")
    assert _project(world, "vula_filed_documents", "x1", "other") == "HPC Bokaap"
    assert _project(world, "commerce_invoices", "b1", "other") == "HPC Bokaap"
    assert [r["id"] for r in world.tables["vula_filing_rules"] if r["project"] == "HPC Bokaap"] == ["r2"]
    assert not doc_filing.move_to_project("other", "Belladonna", doc_id="d1")["ok"]


def test_the_old_filing_rule_is_dropped_and_the_new_one_learned(world):
    doc_filing.move_to_project("digg-demo", "Belladonna", doc_id="d1")
    mine = [(r["signal"], r["project"]) for r in world.tables["vula_filing_rules"]
            if r["tenant_id"] == "digg-demo"]
    assert mine == [("pudlo s.a (pty) ltd", "Belladonna")]


def test_an_unknown_project_is_refused(world):
    res = doc_filing.move_to_project("digg-demo", "Atlantis", doc_id="d1")
    assert not res["ok"] and "no project called" in res["error"]
    assert _project(world, "vula_filed_documents", "d1") == "HPC Bokaap"


def test_a_pending_document_becomes_filed(world):
    world.tables["vula_filed_documents"][0]["status"] = "pending_project"
    doc_filing.move_to_project("digg-demo", "Belladonna", doc_id="d1")
    assert world.tables["vula_filed_documents"][0]["status"] == "filed"


def test_whatsapp_previews_first_and_moves_only_on_confirm(world):
    from core.skills.commerce_admin import CommerceAdminSkill
    skill = CommerceAdminSkill()
    pre = skill._move_to_project("digg-demo", {"reference": "dig-bill-00139", "project": "Belladonna"})
    assert pre["preview"] and "DIG-BILL-00139 (Pudlo R14,228.45)" in pre["message"]
    assert "from HPC Bokaap to *Belladonna*" in pre["message"]
    assert _project(world, "commerce_invoices", "b1") == "HPC Bokaap"
    done = skill._move_to_project("digg-demo", {"reference": "DIG-BILL-00139", "project": "Belladonna",
                                                "confirm": True})
    assert done["verified"] and done["reply_verbatim"].startswith("✅ Moved to Belladonna")
    assert _project(world, "vula_filed_documents", "d1") == "Belladonna"


def test_a_supplier_invoice_number_finds_the_document(world):
    from core.skills.commerce_admin import CommerceAdminSkill
    pre = CommerceAdminSkill()._move_to_project("digg-demo", {"reference": "IN411214", "project": "Belladonna"})
    assert pre["preview"] and "Pudlo IN411214.pdf" in pre["message"]


def test_move_is_a_preview_tool_and_routes_to_the_admin_agent():
    from core import dry_run
    from core.skills.base import _OWNER_ADMIN_RE
    assert "move_to_project" in dry_run.PREVIEW_UNLESS_CONFIRMED
    assert _OWNER_ADMIN_RE.search("Move DIG-BILL-00141 to Belladonna".lower())
    assert _OWNER_ADMIN_RE.search("the Pudlo invoice should move to Belladonna, put the invoice under Belladonna")
    assert not _OWNER_ADMIN_RE.search("move the site meeting to Friday")
