"""FNB statements read exactly, and a verified re-upload replaces misread lines (2 Oct 2026).

Ian: "let's reimport and compare". Against DIGG's real weekly FNB statements the LLM read had
put 50 amounts in the books a factor of ten too small (R82,000 as R8,200) and booked the
accrued-charge column as separate R3/R8 debits. Its upsert key includes the amount, so a
correct re-upload would have sat next to the wrong line. The fixture below uses the real
layout with made-up figures.
"""
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from vula.commerce import bank_rec
from vula.ingestion import fnb_statement

HEAD = """Branch Number
Account Number
PLATINUM BUSINESS ACCOUNT
Page {page} of 2
Delivery Method F1 R02
9 fnb.co.za
Statement Period : 21 September 2026 to 28 September 2026
Statement Date : 28 September 2026
Statement Balances
Opening Balance
1,000.00 Cr
Closing Balance
3,889.50 Cr
Transactions in RAND (ZAR)
Date
Description
Amount
Balance
Accrued
Bank
Charges
"""

PAGE1 = HEAD.format(page=1) + """22 Sep Payshap Account Off-Us Hpc Paint
300.00
700.00Cr
3.00
22 Sep FNB App Payment To Nelitho Wages
Digg Wage
820.00
120.00Dr
8.00
23 Sep Magtape Credit Hpc-PC9 Digg
5,000.00Cr
4,880.00Cr
"""

PAGE2 = """Branch Number
Account Number
PLATINUM BUSINESS ACCOUNT
Page 2 of 2
Transactions in RAND (ZAR) : 1234
Date
Description
Amount
Balance
Accrued
Bank
Charges
25 Sep POS Purchase Build It City Bi
491050*9369 23 Sep
990.50
3,889.50Cr
4.00
Closing Balance
3,889.50Cr
Turnover for Statement Period
"""

TEXT = PAGE1 + PAGE2


def test_reads_every_line_exactly():
    r = fnb_statement.parse(TEXT)
    t = r["transactions"]
    assert [(x["date"], x["amount_cents"], x["direction"]) for x in t] == [
        ("2026-09-22", 30000, "out"), ("2026-09-22", 82000, "out"),
        ("2026-09-23", 500000, "in"), ("2026-09-25", 99050, "out")]
    assert t[1]["description"] == "FNB App Payment To Nelitho Wages Digg Wage"
    assert t[1]["balance_cents"] == -12000            # overdrawn: no "Cr"
    assert t[3]["description"] == "POS Purchase Build It City Bi 491050*9369 23 Sep"
    assert r["closing_cents"] == 388950


def test_the_charge_column_is_not_a_transaction():
    assert len(fnb_statement.parse(TEXT)["transactions"]) == 4    # no R3 / R8 / R4 lines


def test_identical_lines_and_unnamed_charges_are_kept_apart():
    """2 Oct, after the re-upload: two R120 Dez Wood swipes on 22 Sep came out as one line (the
    books key on date+amount+description), and FNB's own charges print with no description."""
    text = TEXT.replace("22 Sep Payshap Account Off-Us Hpc Paint\n300.00\n700.00Cr\n3.00\n",
                        "22 Sep POS Purchase Yoco *Dez Wood\n150.00\n850.00Cr\n4.00\n"
                        "22 Sep POS Purchase Yoco *Dez Wood\n150.00\n700.00Cr\n4.00\n")
    text = text.replace("25 Sep POS Purchase Build It City Bi\n491050*9369 23 Sep\n990.50\n3,889.50Cr\n4.00\n",
                        "25 Sep POS Purchase Build It City Bi\n491050*9369 23 Sep\n978.50\n3,901.50Cr\n4.00\n"
                        "25 Sep\n12.00\n3,889.50Cr\n")
    t = fnb_statement.parse(text)["transactions"]
    assert [x["description"] for x in t[:2]] == ["POS Purchase Yoco *Dez Wood",
                                                 "POS Purchase Yoco *Dez Wood (2)"]
    assert t[-1]["description"] == "FNB bank charges" and t[-1]["amount_cents"] == 1200


def test_one_wrong_figure_and_it_is_not_trusted():
    assert fnb_statement.parse(TEXT.replace("820.00\n", "82.00\n", 1)) is None
    assert fnb_statement.parse("Capitec statement Opening Balance") is None


class _Q:
    def __init__(self, db):
        self.db, self.f, self.op, self.payload = db, [], "select", None

    def select(self, *_a, **_k): return self
    def order(self, *_a, **_k): return self
    def limit(self, *_a, **_k): return self
    def eq(self, k, v): self.f.append(lambda r, k=k, v=v: r.get(k) == v); return self
    def gte(self, k, v): self.f.append(lambda r, k=k, v=v: str(r.get(k)) >= v); return self
    def lte(self, k, v): self.f.append(lambda r, k=k, v=v: str(r.get(k)) <= v); return self
    def in_(self, k, vs): self.f.append(lambda r, k=k, vs=vs: r.get(k) in vs); return self
    def is_(self, k, _v): self.f.append(lambda r, k=k: r.get(k) is None); return self
    def update(self, p): self.op, self.payload = "update", p; return self

    def execute(self):
        hits = [r for r in self.db if all(f(r) for f in self.f)]
        if self.op == "update":
            for r in hits:
                r.update(self.payload)
        return type("R", (), {"data": [dict(r) for r in hits]})()


class _DB:
    def __init__(self, rows): self.rows = rows
    def table(self, _name): return _Q(self.rows)


def _row(i, d, desc, cents, direction="out", **kw):
    return {"id": i, "tenant_id": "digg-demo", "txn_date": d, "description": desc,
            "amount_cents": cents, "direction": direction, "match_status": "unmatched",
            "project": None, "trade": None, "matched_invoice_id": None,
            "matched_order_id": None, "matched_expense_id": None, **kw}


def test_a_verified_statement_sets_aside_what_was_misread(monkeypatch):
    txns = fnb_statement.parse(TEXT)["transactions"]
    rows = [
        # what the verified upload just saved
        *[_row(f"v{n}", t["date"], t["description"], t["amount_cents"], t["direction"])
          for n, t in enumerate(txns)],
        # the old LLM read of the same week
        _row("x10", "2026-09-22", "FNB App Payment To Nelitho Wages Digg Wage", 8200,
             project="HPC Bokaap", trade="Labour"),
        _row("fee", "2026-09-22", "Payshap Account Off-Us Hpc Paint bank charge", 300),
        _row("dup", "2026-09-22", "Payshap Account Off-Us Hpc Paint ", 30000),
        # left alone: another account's line, and one already matched to a supplier bill
        _row("other", "2026-09-22", "Capitec Transfer Groceries", 5000),
        _row("matched", "2026-09-25", "POS Purchase Build It", 9905, matched_expense_id="e1"),
    ]
    monkeypatch.setattr(bank_rec, "_client", lambda: _DB(rows))
    res = bank_rec.supersede_misread("digg-demo", txns)
    ignored = {r["id"] for r in rows if r["match_status"] == "ignored"}
    assert ignored == {"x10", "fee", "dup"}
    assert res == {"superseded": 3, "allocations_carried": 1}
    nelitho = next(r for r in rows if r["id"] == "v1")
    assert (nelitho["project"], nelitho["trade"]) == ("HPC Bokaap", "Labour")   # owner's work kept


def test_an_old_fee_line_gives_way_to_the_statements_own_charge(monkeypatch):
    txns = [{"date": "2026-06-30", "description": "FNB bank charges", "amount_cents": 12479,
             "direction": "out", "balance_cents": 0},
            {"date": "2026-06-30", "description": "FNB bank charges (2)", "amount_cents": 1200,
             "direction": "out", "balance_cents": 0}]
    rows = [_row("v1", "2026-06-30", "FNB bank charges", 12479),
            _row("v2", "2026-06-30", "FNB bank charges (2)", 1200),
            _row("old", "2026-06-30", "Service Fees", 13679),
            _row("blank", "2026-06-30", "", 1200)]          # the first upload, before the name
    monkeypatch.setattr(bank_rec, "_client", lambda: _DB(rows))
    assert bank_rec.supersede_misread("digg-demo", txns)["superseded"] == 2
    assert [r["id"] for r in rows if r["match_status"] == "ignored"] == ["old", "blank"]


def test_the_review_never_asks_about_a_line_set_aside(monkeypatch):
    from vula.commerce import bank_review
    rows = [_row("supplier", "2026-07-17", "INV05118 Sales Order", 439999, "in",
                 categorized_by="asked", match_status="ignored"),
            _row("real", "2026-07-18", "FNB App Payment To Wet And Dry", 4379, categorized_by="default")]
    monkeypatch.setattr(bank_review, "_client", lambda: _DB(rows))
    assert [r["id"] for r in bank_review.pending_txns("digg-demo")] == ["real"]


@pytest.mark.asyncio
async def test_ingest_reads_fnb_exactly_and_skips_the_llm():
    llm = AsyncMock(return_value=[])
    with (
        patch.object(fnb_statement, "pdf_text", return_value=TEXT),
        patch.object(bank_rec, "get_statement_password", return_value=None),
        patch.object(bank_rec, "extract_transactions", new=llm),
        patch.object(bank_rec, "reconcile", new=AsyncMock(return_value={"parsed": 4, "saved": 4})) as rec,
        patch.object(bank_rec, "supersede_misread", return_value={"superseded": 0}),
    ):
        res = await bank_rec.ingest_statement("digg-demo", Path("28 Sep.pdf"))
    assert res["parser"] == "fnb" and res["extraction_reconciled"] is True
    assert len(rec.await_args.args[1]) == 4 and rec.await_args.kwargs["auto_settle"] is True
    llm.assert_not_awaited()


# ── WhatsApp: a statement is imported before (and whatever happens to) its filing ──────────

@pytest.mark.asyncio
async def test_a_statement_by_whatsapp_imports_even_when_filing_fails(tmp_path):
    """2 Oct: of 11 statements sent at once, 17 Aug's knowledge-base filing failed under load
    and its bank lines were never imported — the import waited on the filing."""
    import vula.api.whatsapp as wa
    pdf = tmp_path / "17 Aug 2026.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    sent = []

    async def send(to, text, tenant_id="", idem_key=None):
        sent.append(text)
        return True
    failing = type("P", (), {"__init__": lambda self, **k: None,
                             "ingest_file": AsyncMock(side_effect=RuntimeError("busy"))})
    with (
        patch.object(wa, "_statement_text", return_value=TEXT),
        patch.object(bank_rec, "ingest_statement",
                     new=AsyncMock(return_value={"parsed": 4, "superseded": 2, "needs_input": 0})) as ing,
        patch.object(wa, "_send_reply", new=send),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline", failing),
    ):
        handled = await wa._bank_statement_first("digg-demo", "27820000000", pdf)
    assert handled is True and ing.await_count == 1
    assert "*4 transactions*" in sent[0] and "2 earlier misread line(s) replaced" in sent[0]


@pytest.mark.asyncio
async def test_a_suppliers_statement_of_account_is_left_to_the_document_path(tmp_path):
    import vula.api.whatsapp as wa
    pdf = tmp_path / "Accounts Receivable Statement.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    with (
        patch.object(wa, "_statement_text", return_value="Statement of account Opening balance"),
        patch.object(bank_rec, "ingest_statement",
                     new=AsyncMock(return_value={"error": "x", "not_bank_statement": True})),
    ):
        assert await wa._bank_statement_first("digg-demo", "27820000000", pdf) is False
