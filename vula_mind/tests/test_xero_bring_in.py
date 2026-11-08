"""Move from Xero, step 2 (5 Nov 2026): the one-off move. Everything at the cut-over comes in once,
is checked to the cent against Xero, and then Vula disconnects. Made-up business and figures; a
fake database with the ledger's balance check; no network.
"""
import asyncio
import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from tests.test_xero_move import ACCOUNTS, CONTACTS, CREDIT_NOTES, CUT, INVOICES, TB, VULA_CUSTOMERS, VULA_SUPPLIERS
from vula.commerce import ledger
from vula.migrate import commit, plan, runs

T = "seaview-demo"


class _Q:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.f, self.ins, self.lte_, self.patch, self.row, self.up, self.n, self.sort, self.dele = [], [], [], None, None, None, None, None, False

    def select(self, *a, **k): return self
    def eq(self, k, v): self.f.append((k, v)); return self
    def in_(self, k, vs): self.ins.append((k, set(vs))); return self
    def lte(self, k, v): self.lte_.append((k, v)); return self
    def order(self, col, desc=False): self.sort = (col, desc); return self
    def limit(self, n): self.n = n; return self
    def insert(self, row): self.row = row; return self
    def update(self, patch): self.patch = patch; return self
    def delete(self): self.dele = True; return self
    def upsert(self, rows, on_conflict="id"): self.up = (rows, on_conflict.split(",")); return self

    def _hit(self, r):
        return (all(r.get(k) == v for k, v in self.f) and all(r.get(k) in vs for k, vs in self.ins)
                and all(str(r.get(k) or "") <= v for k, v in self.lte_))

    def execute(self):
        rows = self.db.t.setdefault(self.name, [])
        if self.row is not None:
            new = [self.row] if isinstance(self.row, dict) else self.row
            out = []
            for r in new:
                r = {"id": str(uuid.uuid4()), "created_at": datetime.now(timezone.utc).isoformat(), **r}
                rows.append(r)
                out.append(dict(r))
            return SimpleNamespace(data=out)
        if self.up:
            new, keys = self.up
            for r in ([new] if isinstance(new, dict) else new):
                hit = next((x for x in rows if all(x.get(k) == r.get(k) for k in keys)), None)
                hit.update(r) if hit else rows.append({"id": str(uuid.uuid4()),
                                                       "created_at": datetime.now(timezone.utc).isoformat(), **r})
            return SimpleNamespace(data=[])
        hit = [r for r in rows if self._hit(r)]
        if self.dele:
            self.db.t[self.name] = [r for r in rows if r not in hit]
            return SimpleNamespace(data=hit)
        if self.patch is not None:
            for r in hit:
                r.update(self.patch)
        if self.sort:
            hit = sorted(hit, key=lambda r: str(r.get(self.sort[0]) or ""), reverse=self.sort[1])
        return SimpleNamespace(data=[dict(r) for r in hit][: self.n or None])


class _DB:
    def __init__(self, **t):
        self.t = t

    def table(self, name):
        return _Q(self, name)

    def rpc(self, name, p):
        """post_journal_entry: balanced, known accounts, once per (tenant, source)."""
        def run():
            assert name == "post_journal_entry"
            lines = p["p_lines"]
            assert sum(l["debit_cents"] for l in lines) == sum(l["credit_cents"] for l in lines), "unbalanced"
            ents = self.t.setdefault("journal_entries", [])
            if any(e["tenant_id"] == p["p_tenant_id"] and e["source_type"] == p["p_source_type"]
                   and e["source_id"] == p["p_source_id"] for e in ents):
                return SimpleNamespace(data=None)
            accts = {a["code"]: a["id"] for a in self.t.get("commerce_accounts", []) if a["tenant_id"] == p["p_tenant_id"]}
            for l in lines:
                assert l["account_code"] in accts, f"unknown account {l['account_code']}"
            eid = str(uuid.uuid4())
            ents.append({"id": eid, "tenant_id": p["p_tenant_id"], "entry_date": p["p_entry_date"],
                         "description": p["p_description"], "source_type": p["p_source_type"], "source_id": p["p_source_id"]})
            jl = self.t.setdefault("journal_lines", [])
            for l in lines:
                jl.append({"id": str(uuid.uuid4()), "journal_entry_id": eid, "account_id": accts[l["account_code"]],
                           "debit_cents": l["debit_cents"], "credit_cents": l["credit_cents"]})
            return SimpleNamespace(data=eid)
        return SimpleNamespace(execute=run)


class FakeXero:
    calls = 9

    def __init__(self, invoices=None):
        self._inv = invoices if invoices is not None else INVOICES

    async def organisation(self): return {"Name": "Seaview Builders"}
    async def accounts(self): return ACCOUNTS
    async def trial_balance(self, on): return TB
    async def contacts(self): return [dict(c) for c in CONTACTS]
    async def items(self): return [{"ItemID": "it1", "Name": "Grout 20kg", "SalesDetails": {"UnitPrice": "189.90"},
                                    "IsTrackedAsInventory": True}]
    async def invoices(self): return self._inv
    async def credit_notes(self): return CREDIT_NOTES
    async def payments(self): return [{"PaymentID": "p1", "Date": "2026-10-16T00:00:00", "Amount": "2300.00",
                                       "Invoice": {"InvoiceID": "i2"}}]
    async def bank_transactions(self): return [{"BankTransactionID": "bt1", "DateString": "2026-10-03T00:00:00",
                                                "Total": "500.00", "Reference": "Fuel"}]
    async def manual_journals(self): return [{"ManualJournalID": "mj1", "DateString": "2026-09-30T00:00:00",
                                              "Narration": "Depreciation"}]


@pytest.fixture()
def world(monkeypatch):
    db = _DB(vula_imports=[], commerce_accounts=[], commerce_products=[], commerce_invoices=[],
             commerce_suppliers=[dict(s, tenant_id=T) for s in VULA_SUPPLIERS],
             commerce_invoice_clients=[dict(c, tenant_id=T) for c in VULA_CUSTOMERS],
             journal_entries=[], journal_lines=[], vula_import_map=[], vula_import_records=[],
             commerce_invoice_payments=[])
    monkeypatch.setattr("vula.commerce.service._client", lambda: db)

    async def suppliers(t): return [s for s in db.t["commerce_suppliers"] if s["tenant_id"] == t]
    async def clients(t, kind=None): return [c for c in db.t["commerce_invoice_clients"] if c["tenant_id"] == t]
    monkeypatch.setattr("vula.commerce.service.list_suppliers", suppliers)
    monkeypatch.setattr("vula.commerce.service.list_invoice_clients", clients)
    told, gone = [], []

    async def reply(to, text, tenant_id=None, idem_key=None, **k):
        told.append((to, text))
        return True

    async def disconnect(tid):
        gone.append(tid)
    monkeypatch.setattr("vula.api.whatsapp._send_reply", reply)
    monkeypatch.setattr("vula.xero.credentials.disconnect", disconnect)
    monkeypatch.setattr("vula.connections._owner_phones", lambda t: ["27820000001"])
    monkeypatch.setattr("vula.api.tenants.dashboard_link", lambda t, p: f"https://seaview.example/#/go/{p}")
    return SimpleNamespace(db=db, told=told, gone=gone)


def _previewed(world, client=None):
    run = world.db.table("vula_imports").insert({"tenant_id": T, "source": "xero", "cutover_date": CUT.isoformat(),
                                                 "status": "reading"}).execute().data[0]
    asyncio.run(runs.run_preview(T, run["id"], CUT, client=client or FakeXero()))
    assert runs.get(T, run["id"])["status"] == "previewed"
    return run["id"]


def _docs(world):
    return {r["invoice_number"]: r for r in world.db.t["commerce_invoices"]}


def test_everything_comes_in_agrees_to_the_cent_and_vula_disconnects(world):
    rid = _previewed(world)
    res = asyncio.run(commit.run_commit(T, rid, client=FakeXero()))
    assert res["ok"], res["problems"]
    assert runs.get(T, rid)["status"] == "done"
    assert res["receivables_cents"] == 1265000 and res["payables_cents"] == 230000
    d = _docs(world)
    assert d["INV-0101"]["status"] == "part_paid" and d["INV-0101"]["source"] == commit.OPEN   # R1,000 credited
    assert d["INV-0101"]["total_cents"] - d["INV-0101"]["total_paid_cents"] == 1050000
    assert d["INV-0102"]["status"] == "part_paid" and d["INV-0102"]["total_paid_cents"] == 230000
    assert d["INV-0090"]["status"] == "paid" and d["INV-0090"]["source"] == commit.HISTORY
    assert d["INV-0090"]["paid_at"] == "2026-09-15"
    assert "INV-0110" not in d                                       # after the cut-over
    assert d["TC-7781"]["direction"] == "inbound" and d["TC-7781"]["supplier_id"] == "s1"   # Tile Co by VAT number
    assert d["CN-0007"]["doc_type"] == "credit_note"
    # the opening journal, balanced, per account
    entry = next(e for e in world.db.t["journal_entries"] if e["source_type"] == "opening_balance")
    assert entry["entry_date"] == CUT.isoformat()
    codes = {a["id"]: a["code"] for a in world.db.t["commerce_accounts"]}
    net = {}
    for l in world.db.t["journal_lines"]:
        net[codes[l["account_id"]]] = net.get(codes[l["account_id"]], 0) + l["debit_cents"] - l["credit_cents"]
    assert net["bank_cash"] == 5000000 and net["accounts_receivable"] == 1265000 and net["sales"] == -9000000
    assert {"x_480", "accounts_receivable"} <= set(codes.values())
    # contacts, items, archive
    names = {s["name"] for s in world.db.t["commerce_suppliers"]}
    assert {"New Plumbing Supplies", "Seaview Hardwre"} <= names and "Tile Co (Pty) Ltd" not in names
    assert {c["name"] for c in world.db.t["commerce_invoice_clients"]} == {"Oak St Trust", "Main Road Cafe"}
    assert world.db.t["commerce_invoice_items"][0]["unit_price_cents"] == 18990
    kinds = {r["kind"] for r in world.db.t["vula_import_records"]}
    assert {"invoice", "payment", "bank_transaction", "manual_journal", "contact", "account"} <= kinds
    # disconnected, owner told
    assert world.gone == [T] and "agree with Xero to the cent" in world.told[0][1]


def test_running_it_again_adds_nothing_twice(world):
    rid = _previewed(world)
    asyncio.run(commit.run_commit(T, rid, client=FakeXero(), disconnect=False))
    before = {k: len(v) for k, v in world.db.t.items()}
    res = asyncio.run(commit.run_commit(T, rid, client=FakeXero(), disconnect=False))
    assert res["ok"] and {k: len(v) for k, v in world.db.t.items() if k != "vula_imports"} == \
        {k: v for k, v in before.items() if k != "vula_imports"}


def test_a_number_vula_already_uses_is_kept_distinct(world):
    world.db.t["commerce_invoices"].append({"id": "own-1", "tenant_id": T, "invoice_number": "INV-0101", "source": None})
    rid = _previewed(world)
    asyncio.run(commit.run_commit(T, rid, client=FakeXero(), disconnect=False))
    assert "INV-0101 (Xero)" in _docs(world)


def test_it_refuses_when_vula_already_has_books_before_the_cut_over(world):
    rid = _previewed(world)
    world.db.t["journal_entries"].append({"id": "e0", "tenant_id": T, "entry_date": "2026-10-05", "source_type": "expense"})
    asyncio.run(commit.run_commit(T, rid, client=FakeXero()))
    run = runs.get(T, rid)
    assert run["status"] == "failed" and "already has books" in run["error"]
    assert not world.db.t["commerce_invoices"] and not world.gone


def test_it_refuses_when_xero_changed_since_the_preview(world):
    rid = _previewed(world)
    changed = [dict(i) for i in INVOICES]
    changed[0]["Total"] = "12000.00"
    asyncio.run(commit.run_commit(T, rid, client=FakeXero(changed)))
    assert "changed in Xero since the preview" in runs.get(T, rid)["error"]
    assert not world.db.t["commerce_invoices"]


def test_a_payment_later_clears_what_was_owed_never_sales_again(world):
    rid = _previewed(world)
    asyncio.run(commit.run_commit(T, rid, client=FakeXero(), disconnect=False))
    inv = dict(_docs(world)["INV-0101"])
    ledger.post_invoice_payment(T, inv, {"id": "pay-1", "amount_cents": 500000, "paid_at": "2026-11-10"})
    bill = dict(_docs(world)["TC-7781"])
    ledger.post_supplier_payment(T, bill, {"id": "pay-2", "amount_cents": 230000, "paid_at": "2026-11-11"})
    codes = {a["id"]: a["code"] for a in world.db.t["commerce_accounts"]}
    for src, want in (("pay-1", {"bank_cash": 500000, "accounts_receivable": -500000}),
                      ("pay-2", {"accounts_payable": 230000, "bank_cash": -230000})):
        eid = next(e["id"] for e in world.db.t["journal_entries"] if e["source_id"] == src)
        got = {codes[l["account_id"]]: l["debit_cents"] - l["credit_cents"]
               for l in world.db.t["journal_lines"] if l["journal_entry_id"] == eid}
        assert got == want


def test_undo_cancels_and_reverses_but_not_after_a_payment(world):
    rid = _previewed(world)
    asyncio.run(commit.run_commit(T, rid, client=FakeXero(), disconnect=False))
    first = _docs(world)["INV-0101"]["id"]
    world.db.t["commerce_invoice_payments"].append({"id": "p", "tenant_id": T, "invoice_id": first})
    with pytest.raises(ValueError, match="can't be undone"):
        commit.undo(T, rid, "Sam")
    world.db.t["commerce_invoice_payments"].clear()
    out = commit.undo(T, rid, "Sam")
    assert out["reversed"] and all(r["status"] == "cancelled" for r in world.db.t["commerce_invoices"])
    assert runs.get(T, rid)["status"] == "undone"
    codes = {a["id"]: a["code"] for a in world.db.t["commerce_accounts"]}
    net = {}
    for l in world.db.t["journal_lines"]:
        net[codes[l["account_id"]]] = net.get(codes[l["account_id"]], 0) + l["debit_cents"] - l["credit_cents"]
    assert all(v == 0 for v in net.values())


def test_the_bring_in_blockers_name_what_disagrees():
    fresh = {"trial_balance": {"balanced": False}, "receivables": {"agrees": False}, "payables": {"agrees": True},
             "opening_balances": []}
    assert commit.blockers(fresh) == ["Xero's trial balance doesn't balance at the cut-over date",
                                      "what customers owe doesn't agree with Xero's debtors"]
    assert plan.norm_name("Tile Co (Pty) Ltd") == "tile co"
    assert date.fromisoformat(CUT.isoformat()) == CUT


# ── 8 Nov 2026: the files attached in Xero come across with the move ──────────────────────────

class FakeXeroFiles(FakeXero):
    """INV-0101 and the Tile Co bill have files; INV-0110 (after the cut-over) has one too, which
    never comes across. One file is too big."""
    FILES = {"i1": [{"AttachmentID": "a1", "FileName": "INV-0101 signed.pdf", "MimeType": "application/pdf",
                     "ContentLength": 1200}],
             "b1": [{"AttachmentID": "a2", "FileName": "tileco bill.jpg", "MimeType": "image/jpeg", "ContentLength": 900},
                    {"AttachmentID": "a3", "FileName": "huge scan.pdf", "MimeType": "application/pdf",
                     "ContentLength": 50 * 1024 * 1024}],
             "i4": [{"AttachmentID": "a4", "FileName": "later.pdf", "MimeType": "application/pdf", "ContentLength": 10}]}

    def __init__(self):
        super().__init__([dict(d, HasAttachments=d["InvoiceID"] in self.FILES) for d in INVOICES])
        self.downloads = []

    async def attachments(self, endpoint, rid):
        assert endpoint == "Invoices"
        return self.FILES.get(rid, [])

    async def attachment(self, endpoint, rid, att, max_bytes):
        self.downloads.append(att["AttachmentID"])
        return b"%PDF-1.4 made up " + att["AttachmentID"].encode()


@pytest.fixture()
def stored(monkeypatch):
    up = []

    def upload(bucket, path, data, ct):
        up.append((bucket, path, ct))
        return f"https://store.example/{bucket}/{path}"
    monkeypatch.setattr("vula.api.whatsapp._upload_to_storage", upload)
    return up


def test_attached_files_come_across_linked_to_their_document(world, stored):
    rid = _previewed(world)
    x = FakeXeroFiles()
    res = asyncio.run(commit.run_commit(T, rid, client=x))
    assert res["ok"] and res["attachments"] == 2
    assert x.downloads == ["a1", "a2"]                                   # not the big one, not after the cut-over
    assert res["attachments_skipped"] == ["TC-7781 huge scan.pdf: bigger than 20 MB"]
    d = _docs(world)
    files = {f["filename"]: f for f in world.db.t["vula_filed_documents"]}
    assert files["INV-0101 signed.pdf"]["commerce_invoice_id"] == d["INV-0101"]["id"]
    assert files["tileco bill.jpg"]["commerce_invoice_id"] == d["TC-7781"]["id"]
    assert files["tileco bill.jpg"]["category"] == "supplier_invoice" and files["tileco bill.jpg"]["supplier_id"] == "s1"
    assert all(f["tenant_id"] == T and f["source"] == "xero" for f in files.values())
    assert all(b == "documents" and p.startswith(f"{T}/xero/") for b, p, _ in stored)   # the private bucket
    assert "2 attached files" in world.told[0][1]


def test_files_brought_in_once_are_not_fetched_again(world, stored):
    rid = _previewed(world)
    asyncio.run(commit.run_commit(T, rid, client=FakeXeroFiles(), disconnect=False))
    x = FakeXeroFiles()
    res = asyncio.run(commit.run_commit(T, rid, client=x, disconnect=False))
    assert x.downloads == [] and res["attachments"] == 0 and len(world.db.t["vula_filed_documents"]) == 2


def test_a_file_xero_refuses_is_listed_and_the_move_still_finishes(world, stored):
    class Refusing(FakeXeroFiles):
        async def attachment(self, endpoint, rid, att, max_bytes):
            if att["AttachmentID"] == "a1":
                raise RuntimeError("Xero Invoices: error 500")
            return await super().attachment(endpoint, rid, att, max_bytes)
    rid = _previewed(world)
    res = asyncio.run(commit.run_commit(T, rid, client=Refusing()))
    assert res["ok"] and runs.get(T, rid)["status"] == "done" and res["attachments"] == 1
    assert "INV-0101 INV-0101 signed.pdf: RuntimeError" in res["attachments_skipped"]
    assert world.gone == [T]
