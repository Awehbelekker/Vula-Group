"""A project's documents by folder and supplier, 3 Nov 2026 (vula/commerce/project_file.py).

Ian: "I want to share all the invoices for <project> by folder and sorted by supplier … something we
would like to be able to do for other clients … a dashboard with a quick view." By hand it showed the
same supplier under three spellings, invoices filed under another project and payments in the bank
with no invoice. Made-up businesses, suppliers and figures throughout.
"""
import io
import zipfile
from types import SimpleNamespace

import pytest

from vula.commerce import project_file as pf


class _Q:
    def __init__(self, db, name):
        self.db, self.name, self.filters = db, name, []

    def select(self, *a): return self
    def order(self, *a, **k): return self
    def range(self, *a): return self
    def limit(self, *a): return self
    def eq(self, k, v): self.filters.append(lambda r, k=k, v=v: r.get(k) == v); return self

    def execute(self):
        rows = self.db.tables.get(self.name, [])
        return SimpleNamespace(data=[dict(r) for r in rows if all(f(r) for f in self.filters)])


class _DB:
    def __init__(self, **t): self.tables = t
    def table(self, name): return _Q(self, name)


T = "thandi-demo"


def _doc(i, category, supplier=None, number=None, total=None, date=None, project="Seaview", tenant=T,
         supplier_id=None, filename=None, url=True, status="filed", **fields):
    f = {k: v for k, v in (("supplier", supplier), ("document_number", number), ("total_cents", total),
                           ("date", date)) if v is not None}
    f.update(fields)
    return {"id": i, "tenant_id": tenant, "project": project, "category": category, "status": status,
            "supplier_id": supplier_id, "fields": f, "filename": filename or f"{i}.pdf",
            "file_url": f"https://files/{i}" if url is True else url, "mime": "application/pdf",
            "created_at": f"2026-10-{10 + len(i):02d}T08:00:00", "summary": ""}


def _world():
    return _DB(
        commerce_suppliers=[
            {"id": "s-oak", "tenant_id": T, "name": "OAK HARDWARE CENTRE", "aliases": ["Oakies"]},
            {"id": "s-tile", "tenant_id": T, "name": "Tile Co (Pty) Ltd", "aliases": []},
            {"id": "s-x", "tenant_id": "other", "name": "Secret Supplier", "aliases": []},
        ],
        vula_filed_documents=[
            # three spellings of the same hardware store
            _doc("a1", "Invoice", "OAK HARDWARE CENTRE", "1001", 50000, "2026-09-02", supplier_id="s-oak"),
            _doc("a2", "Invoice", "Oak Hardware Centre", "1002", 20000, "2026-09-05"),
            _doc("a3", "Invoice", "Oakies", "1003", 9550, "2026-09-07"),
            _doc("a4", "Invoice", "OAK HARDWARE CENTRE", "1004", 4500, "2026-09-08", supplier_id="s-oak",
                 filename="POS Account Refund 1004.pdf"),
            # a second copy of the same invoice (sent twice) and a page 2
            _doc("t1", "Invoice", "Tile Co (Pty) Ltd", "IN-77", 300000, "2026-09-10", supplier_id="s-tile"),
            _doc("t1b", "Invoice", "TILE CO", "IN 77", 300000, "2026-09-10"),
            _doc("t1p2", "Invoice", project="Seaview", page_of="t1", page=2),
            _doc("q1", "Quote / Estimate", "Tile Co (Pty) Ltd", "Q-5", 450000, "2026-08-20", supplier_id="s-tile"),
            _doc("st", "Account Statement", "Tile Co (Pty) Ltd", supplier_id="s-tile"),
            _doc("pop", "Proof of Payment"),
            _doc("dr", "Drawing / Plan", filename="Seaview kitchen.pdf"),
            _doc("rep", "Report", "Thandi Builders", filename="Seaview cost statement.pdf"),
            _doc("own", "Invoice", "Thandi Builders (Pty) Ltd", "TB-INV-9", 1000000, "2026-10-20"),
            _doc("nofile", "Invoice", "Tile Co (Pty) Ltd", "IN-78", 1000, "2026-09-11", supplier_id="s-tile", url=None),
            # two slips photographed together: two documents, one file
            _doc("s1", "Invoice", "Oakies", "2001", 1500, "2026-09-12", url="https://files/shared"),
            _doc("s2", "Invoice", "Oakies", "2002", 2500, "2026-09-12", url="https://files/shared"),
            # elsewhere
            _doc("dup", "Invoice", "Tile Co", "IN-99", 7, project="Seaview", status="duplicate"),
            _doc("hpc", "Invoice", "Tile Co", "IN-50", 9999, project="Main Road"),
            _doc("oth", "Invoice", "Secret Supplier", "S-1", 123, tenant="other"),
            _doc("un1", "Invoice", "Paint Place", "P-3", 80000, project=None, status="pending_project",
                 delivery_address="12 Sea View Road"),
            _doc("un2", "Invoice", "Paint Place", "P-4", 80000, project=None, status="pending_project",
                 delivery_address="3 Oak Street"),
        ],
        commerce_bank_transactions=[
            {"id": "b1", "tenant_id": T, "txn_date": "2026-09-02", "description": "POS Oak Hardware",
             "amount_cents": 50000, "direction": "out", "account_code": "cost_of_sales", "project": "Seaview"},
            {"id": "b2", "tenant_id": T, "txn_date": "2026-09-20", "description": "Payment Tile Co IN-77",
             "amount_cents": 290000, "direction": "out", "account_code": "cost_of_sales", "project": "Seaview"},
            {"id": "b3", "tenant_id": T, "txn_date": "2026-09-21", "description": "Skip Hire Seaview",
             "amount_cents": 415000, "direction": "out", "account_code": "cost_of_sales", "project": "Seaview"},
            {"id": "b4", "tenant_id": T, "txn_date": "2026-09-22", "description": "POS Oakies Tableview",
             "amount_cents": 42499, "direction": "out", "account_code": "cost_of_sales", "project": "Seaview"},
            {"id": "b8", "tenant_id": T, "txn_date": "2026-09-24", "description": "POS Paint Shop",
             "reference": "Paid from Thandi Builders' bank account", "amount_cents": 11100, "direction": "out",
             "account_code": "cost_of_sales", "project": "Seaview"},
            {"id": "b5", "tenant_id": T, "txn_date": "2026-09-23", "description": "Seaview wages",
             "amount_cents": 760000, "direction": "out", "account_code": "casual_labour", "project": "Seaview"},
            {"id": "b6", "tenant_id": T, "txn_date": "2026-09-23", "description": "Sam deposit",
             "amount_cents": 999, "direction": "in", "account_code": None, "project": "Seaview"},
            {"id": "b7", "tenant_id": "other", "txn_date": "2026-09-23", "description": "Other skip",
             "amount_cents": 1, "direction": "out", "account_code": "cost_of_sales", "project": "Seaview"},
        ],
    )


@pytest.fixture
def world(monkeypatch):
    db = _world()
    monkeypatch.setattr(pf, "_client", lambda: db)
    from vula.commerce import service
    monkeypatch.setattr(service, "canonical_project", lambda tid, name: name)
    return db


def _ov():
    return pf.overview(T, "Seaview", "Thandi Builders")


def test_one_supplier_whatever_the_spelling_and_credit_notes_count_negative(world):
    ov = _ov()
    oak = next(s for s in ov["suppliers"] if s["supplier"].startswith("Oak"))
    assert oak["supplier"] == "Oak Hardware Centre (Oakies)"
    assert oak["invoices"] == 6
    assert oak["invoices_cents"] == 50000 + 20000 + 9550 - 4500 + 1500 + 2500
    names = {s["supplier"] for s in ov["suppliers"]}
    assert "Thandi Builders (our documents)" in names                 # the business's own, not a "supplier"
    assert not any("Secret" in n for n in names)                # another business's register never shows


def test_a_second_copy_is_left_out_and_a_later_page_rides_with_its_first(world):
    ov = _ov()
    tile = [d for d in ov["documents_list"] if d["number"] in ("IN-77", "IN 77")]
    assert len(tile) == 1 and ov["duplicates_skipped"] == 1
    assert not any(d["id"] == "t1p2" for d in ov["documents_list"])
    assert all(d["id"] not in ("dup", "hpc", "oth") for d in ov["documents_list"])


def test_folders_and_quotes_are_kept_apart_from_invoices(world):
    ov = _ov()
    assert ov["folders"]["01 Invoices"] == 9
    assert ov["folders"]["02 Quotes"] == 1 and ov["folders"]["03 Statements"] == 1
    tile = next(s for s in ov["suppliers"] if s["supplier"].startswith("Tile"))
    assert tile["quotes_cents"] == 450000 and tile["invoices_cents"] == 300000 + 1000
    own = next(s for s in ov["suppliers"] if s["supplier"].endswith("(our documents)"))
    assert own["invoices_cents"] == 1000000                      # the business's own invoice to the client
    assert ov["invoices_cents"] == sum(s["invoices_cents"] for s in ov["suppliers"]) - 1000000


def test_what_looks_missing(world):
    m = _ov()["missing"]
    gaps = {g["description"]: g for g in m["payments_without_document"]}
    # covered by amount (b1) and by the invoice number in the line (b2); wages are counted apart
    assert set(gaps) == {"Skip Hire Seaview", "POS Oakies Tableview", "POS Paint Shop"}
    assert gaps["POS Paint Shop"]["supplier_on_file"] is None        # the business itself is never the hint
    assert gaps["POS Oakies Tableview"]["supplier_on_file"] == "Oak Hardware Centre (Oakies)"
    assert gaps["Skip Hire Seaview"]["supplier_on_file"] is None
    assert m["labour_payments"] == {"count": 1, "total_cents": 760000}
    assert [d["id"] for d in m["documents_naming_project_not_filed"]] == ["un1"]     # "Sea View Road"
    assert [d["id"] for d in m["documents_without_file"]] == ["nofile"]


def test_project_name_with_a_typo_or_split_still_matches():
    assert pf.mentions_project("Villa Sea View, Bakoven", "Seaview")
    assert pf.mentions_project("Payment To Seavieww tiles", "Seaview")
    assert not pf.mentions_project("Atlantis Paarden Eiland", "Atlantic")
    assert not pf.mentions_project("Oak Street", "Seaview")


def _pack(world, audience="internal", fetch=None):
    return pf.build_pack(T, "Seaview", audience, "Thandi Builders",
                         fetch=fetch or (lambda url: f"bytes of {url}".encode()))


def test_the_pack_is_folders_by_supplier_with_an_index_that_adds_up(world):
    data, man = _pack(world)
    z = zipfile.ZipFile(io.BytesIO(data))
    names = z.namelist()
    root = "Seaview - project file"
    assert f"{root}/Index.xlsx" in names
    assert f"{root}/01 Invoices/Oak Hardware Centre (Oakies)/2026-09-02 1001 R500.00.pdf" in names
    assert f"{root}/01 Invoices/Oak Hardware Centre (Oakies)/2026-09-08 1004 -R45.00.pdf" in names
    assert f"{root}/01 Invoices/Tile Co (Pty) Ltd/2026-09-10 IN-77 R3,000.00 page 2.pdf" in names
    assert f"{root}/02 Quotes/Tile Co (Pty) Ltd/2026-08-20 Q-5 R4,500.00.pdf" in names
    assert any(n.startswith(f"{root}/06 Drawings, BOQ and specifications/") for n in names)
    assert any(n.startswith(f"{root}/04 Proofs of payment/") for n in names)
    # the shared photo is in the zip once; the document with no file isn't pretended
    assert sum(1 for n in names if "2026-09-12" in n) == 1
    assert man["files"] == len(names) - 1 and man["documents"] == len(_ov()["documents_list"])
    from openpyxl import load_workbook
    from vula.commerce.xlsx_check import evaluate
    ws = load_workbook(io.BytesIO(z.read(f"{root}/Index.xlsx"))).active
    labels = {ws.cell(r, 2).value: r for r in range(1, ws.max_row + 1)}
    assert round(evaluate(ws, f"F{labels['Total supplier invoices']}") * 100) == _ov()["invoices_cents"]
    assert round(evaluate(ws, f"F{labels['Subtotal — Oak Hardware Centre (Oakies)']}") * 100) == 79050
    files = [ws.cell(r, 7).value for r in range(5, ws.max_row + 1)]
    assert "(no file on record)" in files


def test_the_client_pack_leaves_out_the_internal_papers(world):
    data, man = _pack(world, "client")
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert all("/03 Statements/" not in n and "/04 Proofs of payment/" not in n and "/07 " not in n for n in names)
    assert any("/01 Invoices/" in n for n in names) and any("/06 Drawings" in n for n in names)
    assert names[0].startswith("Seaview - project file (client)/") or any(
        n.startswith("Seaview - project file (client)/") for n in names)


def test_an_unreadable_file_is_marked_not_dropped(world):
    def fetch(url):
        if url.endswith("/a2"):
            raise OSError("storage said no")
        return b"x"
    data, man = _pack(world, fetch=fetch)
    assert len(man["files_failed"]) == 1
    z = zipfile.ZipFile(io.BytesIO(data))
    assert not any("1002" in n for n in z.namelist())


def test_nothing_is_handed_out_when_the_index_does_not_read_back(world, monkeypatch):
    from vula.commerce import xlsx_check
    monkeypatch.setattr(xlsx_check, "mismatches", lambda *a, **k: ["F9: file 1 vs books 2"])
    with pytest.raises(RuntimeError):
        _pack(world)


def test_a_misread_year_is_flagged_not_used(world):
    world.tables["vula_filed_documents"].append(
        _doc("old", "Invoice", "Tile Co (Pty) Ltd", "IN-80", 500, "2022-09-26", supplier_id="s-tile"))
    row = next(d for d in _ov()["documents_list"] if d["id"] == "old")
    assert row["date"] is None and row["date_note"] == "date read as 2022-09-26 — check"


def test_an_empty_project_says_so(world):
    with pytest.raises(LookupError):
        pf.build_pack(T, "Nowhere", "internal", "Thandi Builders", fetch=lambda u: b"")


def test_formula_looking_text_stays_text(world):
    world.tables["vula_filed_documents"].append(
        _doc("evil", "Invoice", "=HYPERLINK(\"http://x\")", "=1+1", 100, "2026-09-30"))
    data, _ = _pack(world)
    from openpyxl import load_workbook
    z = zipfile.ZipFile(io.BytesIO(data))
    ws = load_workbook(io.BytesIO(z.read("Seaview - project file/Index.xlsx"))).active
    cells = [c for row in ws.iter_rows() for c in row if isinstance(c.value, str) and c.value.startswith("=HYPER")]
    assert cells and all(c.data_type == "s" for c in cells)


def test_only_owners_and_managers_get_it():
    from core.skills import commerce_admin as ca
    tool = [t for t in ca.PROJECT_TOOLS if t["function"]["name"] == "project_file"]
    assert tool and ca._TOOL_SCOPE["project_file"] == "owner_only"
    assert ca._restrict_to_access(tool, ["finances", "projects", "invoices"]) == []
    assert ca._restrict_to_access(tool, None) == tool


def test_the_api_is_full_members_only(monkeypatch):
    import asyncio

    from vula.api import server, tenant_auth

    async def member(*a): return True

    async def restricted(*a): return {"full": False, "access": ["finances", "projects"]}

    async def full(*a): return {"full": True, "access": []}

    monkeypatch.setattr(tenant_auth, "is_tenant_member", member)
    for path in ("/v1/commerce/t1/admin/projects/file", "/v1/commerce/t1/admin/projects/file.zip"):
        monkeypatch.setattr(tenant_auth, "member_scope", restricted)
        blocked = asyncio.run(server._guard_check("GET", path, "Bearer x"))
        assert blocked is not None and blocked.status_code == 403
        monkeypatch.setattr(tenant_auth, "member_scope", full)
        assert asyncio.run(server._guard_check("GET", path, "Bearer x")) is None
