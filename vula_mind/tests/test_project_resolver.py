"""Which project a document belongs to, from the evidence (2026-09-28, Ian: "it should see this
payment was for HPC to XYZ supplier and allocate accordingly").

digg-demo had 338 documents waiting on "which project?"; 22 named HPC in their own text.
"""
import pytest

from tests.test_job_costing import FakeDB as _Base, _Q as _BaseQ
from vula.commerce import service
from vula.integrations import project_resolver as pr

TID = "digg-demo"


class _Q(_BaseQ):
    def ilike(self, col, pat):
        if "->>" in col:
            base, key = col.split("->>")
            self.json_likes = getattr(self, "json_likes", []) + [(base, key, pat.strip("%").lower())]
            return self
        return super().ilike(col, pat)

    def _hit(self, r):
        for base, key, p in getattr(self, "json_likes", []):
            if p not in str((r.get(base) or {}).get(key) or "").lower():
                return False
        return super()._hit(r)


class FakeDB(_Base):
    def table(self, name):
        return _Q(self, name)


def _doc(i, supplier, project, status="filed"):
    return {"id": f"h{i}", "tenant_id": TID, "status": status, "project": project,
            "fields": {"supplier": supplier}}


@pytest.fixture()
def db(monkeypatch):
    fake = FakeDB({
        "vula_projects": [{"tenant_id": TID, "name": "HPC Bokaap", "number": "HPC001"},
                          {"tenant_id": TID, "name": "Porterfield", "number": None}],
        "vula_filed_documents": (
            [_doc(i, "SOLID CAPE (PTY) LTD", "HPC Bokaap") for i in range(4)]
            + [_doc(9, "Solid Cape", "HPC_Bokaap")]
            + [_doc(10, "Gardens Handiman", "HPC Bokaap"), _doc(11, "Gardens Handiman", "Porterfield"),
               _doc(12, "Gardens Handiman", "Porterfield")]),
        "commerce_bank_transactions": [
            {"id": "t1", "tenant_id": TID, "direction": "out", "txn_date": "2026-07-20",
             "description": "HPC DOORS", "amount_cents": 2729248, "project": "HPC Bokaap",
             "match_status": "unmatched"},
        ],
    })
    monkeypatch.setattr(service, "_client", lambda: fake)
    monkeypatch.setattr("vula.commerce.expenses.known_projects",
                        lambda tid: ["HPC Bokaap", "HPC_Bokaap", "Porterfield", "Sporty – Phase 2"])
    return fake


def test_a_project_named_in_the_document_files_it(db):
    for text in ("Tax invoice — site: HPC, Bo-Kaap", "Ref HPC001 PC7", "22 Porterfield Rd plumbing"):
        r = pr.resolve(TID, {}, text)
        assert r["confidence"] == 0.9 and r["kind"] == "named", text
    assert pr.resolve(TID, {}, "HPC Bokaap")["project"] == "HPC Bokaap"
    assert pr.resolve(TID, {}, "Sporty ceilings")["project"] == "Sporty – Phase 2"
    assert pr.resolve(TID, {}, "Invoice for a mixer tap") is None


def test_two_projects_named_asks_with_both(db):
    r = pr.resolve(TID, {}, "HPC and Porterfield deliveries")
    assert r["ambiguous"] and set(r["candidates"]) == {"HPC Bokaap", "Porterfield"}


def test_the_bank_payment_that_paid_it_gives_the_project(db):
    r = pr.resolve(TID, {"supplier": "GELMAR", "date": "2026-07-18", "total_cents": 2729248}, "Invoice GEL-88")
    assert (r["project"], r["kind"]) == ("HPC Bokaap", "paid") and "HPC DOORS" in r["reason"]


def test_the_suppliers_usual_project(db):
    r = pr.resolve(TID, {"supplier": "Solid Cape (Pty) Ltd", "total_cents": 99}, "Invoice 51934")
    assert (r["project"], r["kind"], r["confidence"]) == ("HPC Bokaap", "usual_supplier", 0.7)
    assert "5 of 5" in r["reason"]                                 # HPC_Bokaap counted as HPC Bokaap
    split = pr.resolve(TID, {"supplier": "Gardens Handiman"}, "Invoice 7")
    assert split["ambiguous"] and set(split["candidates"]) == {"HPC Bokaap", "Porterfield"}


def test_sort_pending_reports_then_files(db):
    db.tables["vula_filed_documents"] += [
        {"id": "w1", "tenant_id": TID, "status": "pending_project", "filename": "HPC-PC7.pdf",
         "summary": "", "fields": {}, "commerce_invoice_id": None},
        {"id": "w2", "tenant_id": TID, "status": "pending_project", "filename": "Inv_51934.pdf",
         "summary": "", "fields": {"supplier": "SOLID CAPE"}, "commerce_invoice_id": "ci1"},
        {"id": "w3", "tenant_id": TID, "status": "pending_project", "filename": "scan.jpg",
         "summary": "a receipt", "fields": {}, "commerce_invoice_id": None},
    ]
    db.tables["commerce_invoices"] = [{"id": "ci1", "tenant_id": TID, "project": None}]
    dry = pr.sort_pending(TID)
    assert (dry["waiting"], dry["would_file"], dry["still_ask"], dry["filed"]) == (3, 2, 1, 0)
    assert dry["by_reason"] == {"named": 1, "usual_supplier": 1}
    assert next(d for d in db.tables["vula_filed_documents"] if d["id"] == "w1")["status"] == "pending_project"
    done = pr.sort_pending(TID, apply=True)
    docs = {d["id"]: d for d in db.tables["vula_filed_documents"]}
    assert done["filed"] == 2 and docs["w1"]["project"] == "HPC Bokaap" and docs["w1"]["status"] == "filed"
    assert docs["w3"]["status"] == "pending_project"
    assert db.tables["commerce_invoices"][0]["project"] == "HPC Bokaap"


def test_the_months_main_project_is_only_a_suggestion(db):
    db.tables["commerce_bank_transactions"] += [
        {"id": f"m{i}", "tenant_id": TID, "direction": "out", "txn_date": "2026-08-1{i}", "description": "x",
         "amount_cents": c, "project": p, "match_status": "unmatched"}
        for i, (p, c) in enumerate([("HPC Bokaap", 900000), ("HPC Bokaap", 500000), ("Porterfield", 100000)])]
    r = pr.resolve(TID, {"date": "2026-08-20"}, "a receipt with no clues")
    assert (r["project"], r["kind"], r["confidence"]) == ("HPC Bokaap", "month", 0.5)   # 93% of August
    db.tables["vula_filed_documents"].append(
        {"id": "w9", "tenant_id": TID, "status": "pending_project", "filename": "scan.jpg", "summary": "",
         "fields": {"date": "2026-08-20"}, "commerce_invoice_id": None})
    dry = pr.sort_pending(TID)
    assert dry["would_file"] == 0 and dry["suggested_by_month"] == {"HPC Bokaap": 1}
    assert pr.sort_pending(TID, apply=True)["filed"] == 0                    # never on its own
    assert pr.sort_pending(TID, apply=True, include_suggested=True)["filed"] == 1
