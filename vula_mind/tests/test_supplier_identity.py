"""One supplier, one record (vula/commerce/supplier_identity.py, 10 Nov 2026).

A client's list held the same business two or three times — each added automatically from a slip,
proof of payment or quote whose name didn't quite match. The pairs below are the real ones."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from vula.commerce import service
from vula.commerce import supplier_identity as si


def S(name, **kw):
    return {"id": kw.pop("id", name), "name": name, "aliases": kw.pop("aliases", []), **kw}


# ── the real duplicates ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("a,b,why", [
    (S("Pudlo", account_number="..812444"), S("Pudlo S.A (Pty) Ltd", bank_account_number="62491812444"), "bank_account"),
    (S("Pudlo"), S("Pudlo S.A (Pty) Ltd"), "name"),
    (S("Isoboard"), S("Isoboard SA (Pty) Ltd"), "name"),
    (S("Dyi", account_number="020322"), S("DO IT YOURSELF HARDWARE", bank_account_number="1092020322"), "bank_account"),
    (S("DIY"), S("DO IT YOURSELF HARDWARE"), "name"),
    (S("ICES ELIGHTING ELECTRICAL", account_number="..288969"),
     S("Elighting Electrical Services", bank_account_number="62804288969"), "bank_account"),
    (S("elighting01@gmail.com"), S("Elighting Electrical Services", contact_email="elighting01@gmail.com"), "email"),
    (S("ITALTILE"), S("ITALTILE CAPE TOWN"), "name"),
    (S("Solid Cape", tax_id="4560123456"), S("SOLID CAPE (PTY) LTD", tax_id="456 012 3456"), "tax_id"),
    (S("JACKhammer's"), S("GARDENS HANDIMAN CENTRE", aliases=["JACKhammer's"]), "name"),
])
def test_the_same_business_is_recognised(a, b, why):
    assert si.same_supplier(a, b) == why
    assert si.same_supplier(b, a) == why


# ── and what must stay apart ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("a,b", [
    (S("Builders"), S("Builders Warehouse")),                  # a trade word is not a name
    (S("Cape Glass"), S("Cape Steel")),
    (S("Engen Winelands North"), S("Engen Rietvlei S/S")),
    (S("Weavers Ware House"), S("Weavers World (Pty) Ltd")),
    (S("ITALTILE"), S("Italian Tiles Direct")),
    (S("Glass Co", bank_account_number="62001234567"), S("Steel Co", bank_account_number="62009234567")),
    (S("A", account_number="1234"), S("B", account_number="991234")),      # too short to mean anything
    (S("Big Supplier", bank_account_number="1112812444"), S("Other One", bank_account_number="2222812444")),
    (S("ABC"), S("African Bank")),                             # initials need every letter
    (S("Solucent (Pty) Ltd."), S("Solid Cape (PTY) LTD")),
])
def test_different_businesses_stay_apart(a, b):
    assert si.same_supplier(a, b) is None


def test_an_email_is_never_a_name():
    assert si.name_from_email("elighting01@gmail.com") == "Elighting"
    assert si.name_from_email("accounts@solidcape.co.za") == "Solidcape"
    assert si.name_from_email("rudi.scheepers@outlook.com") == "Rudi Scheepers"


def test_duplicate_groups_keep_the_fullest_record():
    rows = [S("Pudlo", id="p2", account_number="..812444", created_at="2026-09-10"),
            S("Pudlo S.A (Pty) Ltd", id="p1", bank_account_number="62491812444", tax_id="4950103772",
              created_at="2026-08-11"),
            S("Dyi", id="d3", account_number="020322"), S("DIY", id="d2"),
            S("DO IT YOURSELF HARDWARE", id="d1", bank_account_number="1092020322", tax_id="4150249060"),
            S("Builders", id="b1"), S("Builders Warehouse", id="b2"), S("Gelmar", id="g1")]
    groups = {g["keep"]["id"]: sorted(d["id"] for d in g["duplicates"]) for g in si.find_duplicates(rows)}
    assert groups == {"d1": ["d2", "d3"], "p1": ["p2"]}


def test_merged_fields_add_aliases_and_fill_blanks_only():
    keep = S("Pudlo S.A (Pty) Ltd", tax_id="4950103772", category="general")
    up = si.merged_fields(keep, [S("Pudlo", tax_id="999", contact_phone="021 448 0607", category="cost_of_sales",
                                   aliases=["PUDLO SA"])])
    assert up == {"aliases": ["Pudlo", "PUDLO SA"], "contact_phone": "021 448 0607", "category": "cost_of_sales"}


# ── upsert_supplier: the one door every new supplier comes through ─────────────────────────

class _Table:
    def __init__(self, log):
        self.log, self.op = log, None

    def update(self, row): self.op = ("update", row); return self
    def upsert(self, row, **kw): self.op = ("upsert", row); return self
    def eq(self, *a): return self

    def execute(self):
        self.log.append(self.op)
        return SimpleNamespace(data=[self.op[1]])


def _db(log):
    return SimpleNamespace(table=lambda name: _Table(log))


@pytest.mark.asyncio
async def test_a_near_name_is_not_created_again():
    on_file = [S("Isoboard SA (Pty) Ltd", id="iso1", category="cost_of_sales")]
    log = []
    with patch.object(service, "list_suppliers", AsyncMock(return_value=on_file)), \
         patch.object(service, "_client", return_value=_db(log)):
        out = await service.upsert_supplier("t1", {"name": "Isoboard", "notes": "from a quote"})
    assert out["id"] == "iso1" and out["matched_existing"] == "name"
    assert log == [("update", {"aliases": ["Isoboard"]})]          # no upsert: nothing new created


@pytest.mark.asyncio
async def test_an_email_name_finds_the_supplier_by_its_email():
    on_file = [S("Elighting Electrical Services", id="el1", contact_email="elighting01@gmail.com")]
    log = []
    with patch.object(service, "list_suppliers", AsyncMock(return_value=on_file)), \
         patch.object(service, "_client", return_value=_db(log)):
        out = await service.upsert_supplier("t1", {"name": "elighting01@gmail.com"})
    assert out["id"] == "el1" and out["matched_existing"] == "email"
    assert not any(op == "upsert" for op, _ in log)


@pytest.mark.asyncio
async def test_a_new_business_is_still_created():
    log = []
    with patch.object(service, "list_suppliers", AsyncMock(return_value=[S("Gelmar", id="g1")])), \
         patch.object(service, "_client", return_value=_db(log)):
        out = await service.upsert_supplier("t1", {"name": "Kell Aluminium Profiles"})
    assert [op for op, _ in log] == ["upsert"] and out["name"] == "Kell Aluminium Profiles"
    assert "matched_existing" not in out


@pytest.mark.asyncio
async def test_the_same_name_still_updates_that_record():
    log = []
    with patch.object(service, "list_suppliers", AsyncMock(return_value=[S("Gelmar", id="g1")])), \
         patch.object(service, "_client", return_value=_db(log)):
        await service.upsert_supplier("t1", {"name": "Gelmar", "payment_terms_days": 7})
    assert [op for op, _ in log] == ["upsert"] and log[0][1]["payment_terms_days"] == 7


@pytest.mark.asyncio
async def test_the_duplicate_check_never_blocks_a_filing():
    log = []
    with patch.object(service, "list_suppliers", AsyncMock(side_effect=RuntimeError("db down"))), \
         patch.object(service, "_client", return_value=_db(log)):
        out = await service.upsert_supplier("t1", {"name": "Pudlo"})
    assert [op for op, _ in log] == ["upsert"] and out["name"] == "Pudlo"


# ── merge ───────────────────────────────────────────────────────────────────────────────────

class _MemQ:
    def __init__(self, db, name):
        self.db, self.name, self.f, self.op = db, name, [], ("select", None)

    def select(self, *a): return self
    def limit(self, *a): return self
    def eq(self, k, v): self.f.append(lambda r: str(r.get(k)) == str(v)); return self
    def in_(self, k, vs): self.f.append(lambda r: str(r.get(k)) in {str(v) for v in vs}); return self
    def update(self, row): self.op = ("update", row); return self
    def delete(self): self.op = ("delete", None); return self

    def execute(self):
        rows = self.db.setdefault(self.name, [])
        hit = [r for r in rows if all(f(r) for f in self.f)]
        if self.op[0] == "update":
            for r in hit:
                r.update(self.op[1])
        elif self.op[0] == "delete":
            self.db[self.name] = [r for r in rows if r not in hit]
        return SimpleNamespace(data=[dict(r) for r in hit])


def test_merge_moves_everything_then_deletes_the_duplicates():
    db = {
        "commerce_suppliers": [
            {"id": "p1", "tenant_id": "t1", "name": "Pudlo S.A (Pty) Ltd", "aliases": [], "category": "cost_of_sales"},
            {"id": "p2", "tenant_id": "t1", "name": "Pudlo", "aliases": [], "account_number": "..812444"},
            {"id": "x1", "tenant_id": "t2", "name": "Pudlo", "aliases": []},          # another business's
        ],
        "commerce_expenses": [{"id": f"e{i}", "tenant_id": "t1", "supplier_id": "p2"} for i in range(4)],
        "commerce_invoices": [{"id": "b1", "tenant_id": "t1", "supplier_id": "p2"},
                              {"id": "b9", "tenant_id": "t2", "supplier_id": "x1"}],
    }
    with patch.object(si, "_client", return_value=SimpleNamespace(table=lambda n: _MemQ(db, n))):
        res = si.merge("t1", "p1", ["p2"])
    assert res["ok"] and res["merged"] == ["Pudlo"] and res["moved"] == {"commerce_expenses": 4, "commerce_invoices": 1}
    assert {r["id"] for r in db["commerce_suppliers"]} == {"p1", "x1"}
    kept = next(r for r in db["commerce_suppliers"] if r["id"] == "p1")
    assert kept["aliases"] == ["Pudlo"] and kept["account_number"] == "..812444"
    assert all(r["supplier_id"] == "p1" for r in db["commerce_expenses"])
    assert next(r for r in db["commerce_invoices"] if r["id"] == "b9")["supplier_id"] == "x1"   # untouched


def test_merge_refuses_another_business_supplier():
    db = {"commerce_suppliers": [{"id": "p1", "tenant_id": "t1", "name": "Pudlo S.A"},
                                 {"id": "x1", "tenant_id": "t2", "name": "Pudlo"}]}
    with patch.object(si, "_client", return_value=SimpleNamespace(table=lambda n: _MemQ(db, n))):
        res = si.merge("t1", "p1", ["x1"])
    assert res == {"ok": False, "error": "Supplier not found."}
    assert len(db["commerce_suppliers"]) == 2


def test_the_business_own_account_never_joins_two_suppliers():
    # A proof of payment carried the payer's (the business's own) account where the payee's belongs.
    rows = [S("OfftheHook", id="o1", bank_account_number="1052925286"),
            S("StaciBritsOth", id="s1", account_number="925286"),
            S("Fish Hoek Fish Market", id="f1", account_number="..925286")]
    others, own = si.split_own(rows, ["off-the-hook", "Off the Hook", "OfftheHook"])
    assert [r["id"] for r in others] == ["s1", "f1"] and own == frozenset({"1052925286"})
    assert si.find_duplicates(others, own) == []
    assert si.find_existing(others, S("Someone New", account_number="925286"), own) is None
