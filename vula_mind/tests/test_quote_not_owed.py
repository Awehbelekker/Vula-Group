"""A supplier's quote is a price, not money owed (2026-10-06, digg-demo).

Ian: "The sale R41752.47 what's this". It is STE Scaffolding's quote to SELL DIGG the
scaffolding set ("STESACA-DIGG-CT-SALE-SCAFFOLDING QUOTATION.pdf", R41,752.47 excl VAT) — the
alternative to the hire quote, R2,084.50 a week excl VAT, which is what DIGG took. What that
showed up:
- the quote was booked as a bill with a 30-day due date, like all 85 DIGG supplier quotes
  (R12.8M), so "money owed" and bills-due views could count it;
- "R41,752.47 excluding VAT" was booked as the full price with R0 VAT;
- asking about it by amount found nothing (the text search never matched "41752.47").
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.commerce import service
from vula.commerce.service import money_in_query, vat_inclusive

TID = "digg-demo"


@pytest.mark.parametrize("q,cents", [
    ("The sale R41752.47 what's this", 4175247),
    ("R41 752,47", 4175247),
    ("what is 41,752.47 for", 4175247),
    ("the R2,397.17 POP", 239717),
    ("invoice 00084", None),
    ("STE00866", None),
])
def test_amounts_in_a_question(q, cents):
    assert money_in_query(q) == cents


def test_vat_is_added_in_code_only_when_the_document_excludes_it():
    assert vat_inclusive(4175247, 0, True) == (4801534, 626287)      # R48,015.34 incl
    assert vat_inclusive(208450, 0, True) == (239717, 31267)         # = STE00866, R2,397.17
    assert vat_inclusive(239717, 31267, False) == (239717, 31267)
    assert vat_inclusive(239717, 31267, True) == (239717, 31267)     # VAT already shown


class _Capture:
    def __init__(self, rows):
        self.rows, self.clauses = rows, None

    def table(self, _t):
        return self

    def select(self, *_a):
        return self

    def eq(self, *_a):
        return self

    def or_(self, clauses):
        self.clauses = clauses
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        return type("R", (), {"data": self.rows})()


SALE_QUOTE = {"id": "8de5d678", "filename": "STESACA-DIGG-CT-SALE-SCAFFOLDING QUOTATION.pdf",
              "category": "Quote / Estimate", "status": "filed", "created_at": "2026-10-02T16:14:22",
              "summary": "Scaffolding quotation from STE SCAFFOLDING SA ... totaling R41,752.47 "
                         "excluding VAT.",
              "fields": {"supplier": "STE SCAFFOLDING SA", "total_cents": 4175247,
                         "date": "2026-10-02"}}


@pytest.mark.asyncio
async def test_asking_by_amount_finds_the_quote(monkeypatch):
    db = _Capture([SALE_QUOTE])
    monkeypatch.setattr(service, "_client", lambda: db)
    out = await service.find_filed_document(TID, "The sale R41752.47 what's this")
    assert "fields->>total_cents.eq.4175247" in db.clauses
    assert "fields->>total_cents.eq.4801534" in db.clauses            # same price incl VAT
    assert out["matched_by"].startswith("amount R41,752.47")
    assert "quote is a price" in out["matched_by"]
    assert out["total_matches"] == 1


@pytest.mark.asyncio
async def test_an_inbound_quote_has_no_due_date_and_carries_vat():
    captured = {}

    def table(name):
        t = MagicMock()
        if name == "commerce_invoices":
            def insert(row):
                captured["row"] = row
                m = MagicMock()
                m.execute.return_value = MagicMock(data=[row])
                return m
            t.insert.side_effect = insert
        return t
    db = MagicMock()
    db.table.side_effect = table
    db.rpc.return_value.execute.return_value = MagicMock(data=84)
    pipeline = MagicMock()
    pipeline.ingest_text = AsyncMock(return_value=MagicMock(chunks_stored=0))
    with patch("vula.commerce.service._client", return_value=db), \
            patch("vula.commerce.service.match_supplier", new=AsyncMock(return_value=None)), \
            patch("vula.commerce.service.upsert_supplier",
                  new=AsyncMock(return_value={"id": "s1", "name": "STE SCAFFOLDING SA",
                                              "payment_terms_days": 30})), \
            patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=pipeline):
        out = await service.commit_inbound_document(
            TID, {"doc_type": "quote", "supplier": "STE SCAFFOLDING SA", "date": "2026-10-02",
                  "total_cents": 4175247, "vat_cents": None, "amounts_exclude_vat": True,
                  "line_items": []}, auto_commit=True, source="email")
    row = captured["row"]
    assert row["due_date"] is None
    assert (row["subtotal_cents"], row["vat_cents"], row["total_cents"]) == (4175247, 626287, 4801534)
    assert "nothing is owed" in out["message"]


@pytest.mark.asyncio
async def test_cash_summary_leaves_quotes_and_cancelled_bills_out(monkeypatch):
    from core.skills import commerce_admin as ca
    rows = {"commerce_invoices": [
        {"direction": "inbound", "doc_type": "quote", "status": "draft", "total_cents": 4175247,
         "supplier": "STE SCAFFOLDING SA"},
        {"direction": "inbound", "doc_type": "invoice", "status": "paid", "total_cents": 239717,
         "supplier": "STE Scaffolding S A (Pty) Ltd (Cape)"},
        {"direction": "inbound", "doc_type": "invoice", "status": "cancelled", "total_cents": 239717,
         "supplier": "STE Scaffolding CC (Cape)"},
    ], "commerce_expenses": []}

    class Q:
        def __init__(self, t):
            self.t, self.f = t, []

        def select(self, *_a):
            return self

        def eq(self, c, v):
            self.f.append((c, v))
            return self

        def gte(self, *_a):
            return self

        def limit(self, *_a):
            return self

        def execute(self):
            data = [r for r in rows[self.t] if all(c == "tenant_id" or r.get(c) == v for c, v in self.f)]
            return type("R", (), {"data": data})()
    monkeypatch.setattr(service, "_client", lambda: type("C", (), {"table": lambda s, t: Q(t)})())
    out = await ca.CommerceAdminSkill()._cash_summary(TID, {})
    assert out["money_out"]["billed_by_suppliers"] == 2397.17
    assert out["money_out"]["still_owed_by_us"] == 0
