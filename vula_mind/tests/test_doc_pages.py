"""Bathroom Bizarre's two-page invoice, 26 Oct: a later page sent on its own joins the first.

Ian sent tax invoice 1013/00037437 as two photos. Page 1 had five lines and no total Vula could
find on the page; page 2 had the other four lines and the totals. Vula filed page 1 alone.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from vula.api import whatsapp as wa
from vula.integrations import doc_pages

PAGE1 = {"supplier": "Bathroom Bizarre (Pty) Ltd", "document_number": "1013/00037437",
         "date": "2026-10-22", "total_cents": 1568740, "amounts_exclude_vat": True,
         "_unverified_figures": [{"rand": 15687.40}],
         "line_items": [{"description": "Butler sink", "quantity": 1, "total_cents": 224815},
                        {"description": "Shataff bidet set", "quantity": 2, "total_cents": 57110},
                        {"description": "Shower trap", "quantity": 2, "total_cents": 127890},
                        {"description": "Geberit Kombifix cistern", "quantity": 2, "total_cents": 481802},
                        {"description": "Geberit Selnova WC", "quantity": 2, "total_cents": 707020}]}
PAGE2 = {"supplier": "Bathroom Bizarre", "document_number": "1013 / 00037437",
         "total_cents": 2050831, "vat_cents": 267500, "subtotal_cents": 1783331,
         "amounts_exclude_vat": False, "discount_cents": 189860,
         "line_items": [{"description": "Shataff cradle", "quantity": 2, "total_cents": 55968},
                        {"description": "Alpha20 plate matt chrome", "quantity": 1, "total_cents": 56521},
                        {"description": "Alpha20 plate white", "quantity": 1, "total_cents": 56521},
                        {"description": "90mm basket waste", "quantity": 1, "total_cents": 15684}]}


def test_page_two_continues_page_one():
    assert doc_pages.continues(PAGE1, PAGE2)


def test_the_joined_invoice_has_every_line_and_the_last_pages_totals():
    m = doc_pages.merge(PAGE1, PAGE2)
    assert len(m["line_items"]) == 9
    assert sum(li["total_cents"] for li in m["line_items"]) == 1783331
    assert (m["total_cents"], m["vat_cents"], m["amounts_exclude_vat"]) == (2050831, 267500, False)
    assert "_unverified_figures" not in m and m["pages"] == 2
    assert m["supplier"] == "Bathroom Bizarre (Pty) Ltd" and m["date"] == "2026-10-22"


def test_a_resent_page_one_is_not_a_second_page():
    assert not doc_pages.continues(PAGE1, dict(PAGE1, _unverified_figures=None))


def test_a_complete_invoice_takes_no_more_pages():
    whole = doc_pages.merge(PAGE1, PAGE2)
    assert not doc_pages.continues(whole, dict(PAGE2, line_items=[{"description": "Extra", "total_cents": 100}]))


def test_another_supplier_or_number_is_never_joined():
    assert not doc_pages.continues(PAGE1, dict(PAGE2, document_number="1013/00037438"))
    assert not doc_pages.continues(PAGE1, dict(PAGE2, supplier="Flush Bathrooms"))
    assert not doc_pages.continues(PAGE1, dict(PAGE2, document_number=None))


class _Q:
    def __init__(self, db, name):
        self.db, self.name, self.filters, self.patch = db, name, [], None

    def select(self, *a): return self
    def order(self, *a, **k): return self
    def limit(self, *a): return self
    def gte(self, *a): return self
    def eq(self, k, v): self.filters.append((k, v, True)); return self
    def neq(self, k, v): self.filters.append((k, v, False)); return self
    def update(self, row): self.patch = row; return self

    def execute(self):
        rows = [r for r in self.db.tables.setdefault(self.name, [])
                if all((r.get(k) == v) == same for k, v, same in self.filters)]
        if self.patch is not None:
            self.db.writes.append((self.name, dict(self.patch)))
            for r in rows:
                r.update(self.patch)
        return SimpleNamespace(data=rows)


class _DB:
    def __init__(self, **tables): self.tables, self.writes = tables, []
    def table(self, name): return _Q(self, name)


def _first(**extra):
    return {"id": "doc1", "tenant_id": "digg-demo", "filename": "Invoice - Bathroom Bizarre.jpg",
            "project": "Belladonna", "status": "filed", "fields": dict(PAGE1),
            "file_url": "https://files/doc1.jpg", "commerce_invoice_id": None, **extra}


def test_the_filed_first_page_is_found(monkeypatch):
    db = _DB(vula_filed_documents=[_first(), {"id": "p", "tenant_id": "digg-demo",
                                              "fields": {"page_of": "doc1", **PAGE1}}])
    assert doc_pages.find_first_page("digg-demo", PAGE2, db=db)["id"] == "doc1"
    assert doc_pages.find_first_page("other-tenant", PAGE2, db=db) is None


async def _join(monkeypatch, tmp_path, db, first):
    filed = []

    async def file_document(tenant_id, **kw):
        filed.append(kw)
        return {"id": "doc2", **kw}

    monkeypatch.setattr("vula.commerce.service._client", lambda: db)
    monkeypatch.setattr("vula.integrations.doc_filing.file_document", file_document)
    commit = AsyncMock(return_value={"committed": True, "record": {"invoice_number": "DIG-BILL-00150"}})
    monkeypatch.setattr("vula.commerce.service.commit_inbound_document", commit)
    page = Path(tmp_path) / "p2.jpg"
    page.write_bytes(b"page two")
    note, row = await wa._join_to_first_page(
        "digg-demo", "27645755210", SimpleNamespace(filename="Invoice p2.jpg", doc_id="d2"),
        page, "image/jpeg", "Invoice", PAGE2, first)
    return note, row, filed, commit


@pytest.mark.asyncio
async def test_page_two_is_filed_with_page_one_and_the_whole_invoice_is_booked(monkeypatch, tmp_path):
    first = _first()
    db = _DB(vula_filed_documents=[first], commerce_expenses=[])
    note, row, filed, commit = await _join(monkeypatch, tmp_path, db, first)
    assert filed[0]["project"] == "Belladonna" and filed[0]["fields"]["page_of"] == "doc1"
    assert len(first["fields"]["line_items"]) == 9 and first["fields"]["total_cents"] == 2050831
    sent = commit.call_args
    assert sent.args[1]["total_cents"] == 2050831 and sent.kwargs["filed_document_id"] == "doc1"
    assert sent.kwargs["project"] == "Belladonna"
    assert "page 2 of Bathroom Bizarre (Pty) Ltd 1013/00037437" in note
    assert "9 lines now, total R20,508.31" in note and "Booked as DIG-BILL-00150" in note


@pytest.mark.asyncio
async def test_a_bill_booked_from_page_one_is_brought_up_to_the_whole_invoice(monkeypatch, tmp_path):
    first = _first(commerce_invoice_id="inv1")
    bill = {"id": "inv1", "tenant_id": "digg-demo", "invoice_number": "DIG-BILL-00149",
            "status": "draft", "total_cents": 1568740}
    db = _DB(vula_filed_documents=[first], commerce_invoices=[bill])
    note, _, _, commit = await _join(monkeypatch, tmp_path, db, first)
    commit.assert_not_awaited()
    assert (bill["total_cents"], bill["vat_cents"], len(bill["line_items"])) == (2050831, 267500, 9)
    assert "Bill DIG-BILL-00149 updated to the full R20,508.31" in note


@pytest.mark.asyncio
async def test_a_paid_bill_is_never_changed_only_flagged(monkeypatch, tmp_path):
    first = _first(commerce_invoice_id="inv1")
    bill = {"id": "inv1", "tenant_id": "digg-demo", "invoice_number": "DIG-BILL-00149",
            "status": "paid", "total_cents": 1568740}
    db = _DB(vula_filed_documents=[first], commerce_invoices=[bill])
    note, *_ = await _join(monkeypatch, tmp_path, db, first)
    assert bill["total_cents"] == 1568740 and "already paid at R15,687.40" in note


@pytest.mark.asyncio
async def test_an_expense_from_the_first_photo_is_updated_not_booked_twice(monkeypatch, tmp_path):
    first = _first()
    exp = {"id": "e1", "tenant_id": "digg-demo", "receipt_url": "https://files/doc1.jpg",
           "status": "pending", "amount_cents": 1568740}
    db = _DB(vula_filed_documents=[first], commerce_expenses=[exp])
    note, _, _, commit = await _join(monkeypatch, tmp_path, db, first)
    commit.assert_not_awaited()
    assert exp["amount_cents"] == 2050831 and "expense is updated to the full R20,508.31" in note


@pytest.mark.asyncio
async def test_a_page_whose_total_is_not_on_it_is_joined_but_not_booked(monkeypatch, tmp_path):
    first = _first()
    db = _DB(vula_filed_documents=[first], commerce_expenses=[])
    global PAGE2
    saved, PAGE2 = PAGE2, dict(PAGE2, _unverified_figures=[{"rand": 20508.31}])
    try:
        note, _, _, commit = await _join(monkeypatch, tmp_path, db, first)
    finally:
        PAGE2 = saved
    commit.assert_not_awaited()
    assert "not booked the total" in note
