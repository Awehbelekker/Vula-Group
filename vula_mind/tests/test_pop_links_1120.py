"""20 Nov 2026 (Ian, DIGG): "when the tenant uploads a POP, is it linked to the supplier? One payment can
pay several invoices." DIGG had 65 proofs of payment and none was linked. vula/commerce/pop_links.py links
each one the business sent to its supplier (by the account paid into, else the payee's name) and the
bill(s) adding up to it. Made-up names and figures."""
import pytest

from vula.commerce import books_review as br, doc_audit, pop_links as pl, service
from tests.fake_supabase import FakeSupabase

T = "t1"


def _pop(i, amount, day, payee, account="", payer="Mandla Designs (Pty) Ltd", **kw):
    return {"id": i, "tenant_id": kw.pop("tenant_id", T), "category": "Proof of Payment", "filename": f"{i}.pdf",
            "summary": "", "status": "filed", "content_hash": f"h-{i}", "file_url": f"u/{i}.pdf", "mime": "application/pdf",
            "commerce_invoice_id": None, "supplier_id": None, "created_at": f"{day}T10:00:00Z", "project": None,
            "fields": {"amount_cents": amount, "date": day, "payee_name": payee, "payee_account_number": account,
                       "payer": payer, "reference": "SKIPS", **kw.pop("fields", {})}}


def _bill(i, number, supplier, day, total, status="draft", paid=0, supplier_id=None, tenant_id=T):
    return {"id": i, "tenant_id": tenant_id, "invoice_number": number, "supplier": supplier, "supplier_id": supplier_id,
            "direction": "inbound", "doc_type": "invoice", "status": status, "total_cents": total,
            "total_paid_cents": paid, "issue_date": day, "project": "Bokaap"}


@pytest.fixture
def db(monkeypatch):
    d = FakeSupabase({
        "vula_filed_documents": [
            _pop("p1", 840000, "2026-09-25", "R Engelbrecht", "..1234561576"),     # two skips bills, one paid
            _pop("p2", 280000, "2026-10-14", "Skip Rubble", ""),                     # by name (alias)
            _pop("p3", 405000, "2026-07-24", "E Mahlangu", "..891598"),              # nobody on file
            _pop("p4", 50000, "2026-07-20", "Mandla Designs", "", payer="Atlantis Foods"),   # a customer's POP
            _pop("x1", 840000, "2026-09-25", "R Engelbrecht", "..1234561576", tenant_id="other")],
        "commerce_suppliers": [
            {"id": "s1", "tenant_id": T, "name": "Belekkie Skips", "aliases": ["R Engelbrecht", "Skip Rubble"],
             "bank_account_name": "R Engelbrecht", "bank_account_number": "1234561576"},
            {"id": "s2", "tenant_id": T, "name": "Steel Supplies", "aliases": [], "bank_account_name": None,
             "bank_account_number": "62000009999"}],
        "commerce_invoices": [
            _bill("b1", "DIG-BILL-00196", "Belekkie Skips", "2026-09-10", 560000, supplier_id="s1"),
            _bill("b2", "DIG-BILL-00197", "R Engelbrecht", "2026-09-12", 280000, status="paid", paid=280000),
            _bill("b3", "DIG-BILL-00198", "Belekkie Skips", "2026-10-01", 280000, supplier_id="s1"),
            _bill("b9", "STEEL-9", "Steel Supplies", "2026-09-10", 840000, supplier_id="s2"),
            _bill("bx", "X-1", "Belekkie Skips", "2026-09-10", 560000, supplier_id="s1", tenant_id="other")],
        "commerce_invoice_payments": [], "vula_merchant_audit": [], "commerce_bank_transactions": [],
        "commerce_bank_line_documents": []})
    for mod in (pl, br, doc_audit, service):
        monkeypatch.setattr(mod, "_client", lambda: d)
    monkeypatch.setattr("vula.api.merchant_audit._client", lambda: d)
    monkeypatch.setattr("vula.commerce.bank_rec._own_names_now", lambda tid: ["Mandla Designs (Pty) Ltd"])
    monkeypatch.setattr("vula.commerce.bank_rec._team_member_paying", lambda tid, payer: None)
    paid = []

    async def record(tid, inv_id, cents, payment_method=None, note=None, paid_at=None):
        paid.append((inv_id, cents, paid_at, note))
        for r in d.rows["commerce_invoices"]:
            if r["id"] == inv_id:
                r["total_paid_cents"] = (r.get("total_paid_cents") or 0) + cents
                r["status"] = "paid" if r["total_paid_cents"] >= r["total_cents"] else "part_paid"
        return {"ok": True}
    monkeypatch.setattr(service, "record_invoice_payment", record)
    d.paid = paid
    return d


def test_the_supplier_is_found_by_the_account_paid_into_then_by_name(db):
    docs = {r["id"]: r for r in db.rows["vula_filed_documents"]}
    assert pl.supplier_for(T, pl.read(docs["p1"]))["how"] == "the account paid into"
    by_name = pl.supplier_for(T, pl.read(docs["p2"]))
    assert by_name["id"] == "s1" and by_name["how"] == "the payee's name"
    assert pl.supplier_for(T, pl.read(docs["p3"])) is None
    assert not pl.outgoing(T, pl.read(docs["p4"]))                 # paid to the business: a customer's proof


def test_the_review_offers_the_bills_that_add_up_and_never_another_business_s(db):
    r = pl.review(T)
    items = {x["id"]: x for x in r["items"]}
    assert set(items) == {"p1", "p2", "p3"} and r["no_supplier"] == 1
    assert [[b["number"] for b in o["bills"]] for o in items["p1"]["options"]] == [["DIG-BILL-00196", "DIG-BILL-00197"],
                                                                                 ["DIG-BILL-00196", "DIG-BILL-00198"]]
    assert items["p1"]["options"][0]["bills"][1]["already_paid"]
    assert [[b["number"] for b in o["bills"]] for o in items["p2"]["options"]] == [["DIG-BILL-00197"], ["DIG-BILL-00198"]] \
        or [[b["number"] for b in o["bills"]] for o in items["p2"]["options"]] == [["DIG-BILL-00198"], ["DIG-BILL-00197"]]
    assert items["p1"]["account"] == "…1576" and items["p3"]["options"] == []


@pytest.mark.asyncio
async def test_linking_records_each_open_bill_once_and_ties_a_paid_one(db):
    out = await pl.link(T, "p1", ["b1", "b2"], actor={"email": "judy@example.com"})
    assert out["ok"] and out["recorded"] == 1
    assert db.paid == [("b1", 560000, "2026-09-25", "Proof of payment 2026-09-25 (SKIPS)")]
    doc = next(r for r in db.rows["vula_filed_documents"] if r["id"] == "p1")
    link = doc["fields"]["pop_link"]
    assert link["bill_ids"] == ["b1", "b2"] and link["supplier_id"] == "s1" and link["by"] == "judy@example.com"
    assert doc["supplier_id"] == "s1"
    assert any(a["action"] == "pop_linked" for a in db.rows["vula_merchant_audit"])
    assert "p1" not in {x["id"] for x in pl.review(T)["items"]}
    p2 = {x["id"]: x for x in pl.review(T)["items"]}["p2"]
    assert [[b["number"] for b in o["bills"]] for o in p2["options"]] == [["DIG-BILL-00198"]]   # b2 is taken now
    assert pl.proofs_for_bill(T, "b2")[0]["doc_id"] == "p1"
    with pytest.raises(pl.Refused, match="already linked"):
        await pl.link(T, "p1", ["b3"])


@pytest.mark.asyncio
async def test_what_is_refused(db):
    with pytest.raises(pl.Refused, match="isn't what's still owed"):
        await pl.link(T, "p2", ["b1"])
    with pytest.raises(pl.Refused, match="aren't Belekkie Skips's"):
        await pl.link(T, "p1", ["b9"])
    with pytest.raises(pl.Refused, match="isn't there"):
        await pl.link(T, "p1", ["bx"])                                # another business's bill
    with pytest.raises(pl.Refused, match="isn't on file"):
        await pl.link(T, "x1", ["b1", "b2"])                          # another business's proof
    assert db.paid == []


def test_not_a_supplier_s_bill_is_kept_with_what_it_was(db):
    with pytest.raises(pl.Refused, match="Say what it was"):
        pl.dismiss(T, "p3", " ")
    assert pl.dismiss(T, "p3", "Wages for the week")["ok"]
    assert "p3" not in {x["id"] for x in pl.review(T)["items"]}


def test_the_documents_worklist_counts_them(db):
    assert "pops" in br.DOC_GROUPS and {x["id"] for x in doc_audit.worklist(T)["pops"]} == {"p1", "p2", "p3"}
