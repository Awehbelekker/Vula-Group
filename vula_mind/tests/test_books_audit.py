"""17 Nov 2026, Ian: DIGG will give its accountant access, so a full audit of its bank data before
any history goes into the ledger. vula/commerce/books_audit.py reads only and names the rows behind
every finding."""
from datetime import date

from vula.commerce import books_audit as ba
from tests.fake_supabase import FakeSupabase


def _ln(i, d, direction, cents, bal, status="unmatched", f="stmt-A.pdf", **kw):
    return {"id": f"t{i}", "tenant_id": "t1", "source_file": f, "txn_date": d, "direction": direction,
            "amount_cents": cents, "balance_cents": bal, "match_status": status, "description": kw.pop("desc", f"line {i}"),
            "created_at": f"2026-07-01T00:00:{i:02d}", **kw}


def test_a_clean_statement_chains_into_one_run():
    lines = [_ln(1, "2026-07-01", "in", 10000, 110000), _ln(2, "2026-07-02", "out", 3000, 107000),
             _ln(3, "2026-07-02", "out", 2000, 105000)]
    c = ba.chain(lines[::-1])                     # order in doesn't matter
    assert c["segments"] == [["t1", "t2", "t3"]] and c["opening"] == 100000 and c["closing"] == 105000


def test_the_right_copy_set_aside_by_mistake_is_found_and_restores_the_chain():
    """DIGG 18 Jul: the misread copy (amount ÷10) and the right copy were both set aside; the
    books were missing the purchases. The running balance proves which lines belong."""
    lines = [
        _ln(1, "2026-07-18", "in", 17591613, 18062480),                    # live
        _ln(2, "2026-07-18", "out", 920, 1805328, status="ignored", desc="Yoco"),     # misread copy
        _ln(3, "2026-07-18", "out", 9200, 18053280, status="ignored", desc="Yoco"),   # the right copy, set aside
        _ln(4, "2026-07-18", "out", 10180, 18043100, status="ignored", desc="Mcd"),
        _ln(5, "2026-07-20", "out", 250000, 17793100),                     # live again
    ]
    s = ba.statements(lines)["stmt-A.pdf"]
    assert [x["id"] for x in s["restore"]] == ["t3", "t4"]
    assert s["breaks"] == 0
    rep_lines = {r["id"] for r in s["restore"]}
    assert "t2" not in rep_lines                                          # the misread stays set aside
    assert any(set(p["rows"]) == {"t2", "t3"} for p in s["misreads"])      # read twice, decimal point moved


def test_a_break_nothing_fills_is_reported():
    lines = [_ln(1, "2026-07-01", "in", 10000, 110000), _ln(2, "2026-07-03", "out", 3000, 90000),
             _ln(3, "2026-07-04", "out", 1000, 89000)]
    s = ba.statements(lines)["stmt-A.pdf"]
    assert s["breaks"] == 1 and s["restore"] == []


def test_two_statements_of_one_account_join_and_a_fully_superseded_read_is_not_an_account():
    lines = [_ln(1, "2026-07-01", "in", 10000, 110000, f="june.pdf"), _ln(2, "2026-07-02", "out", 3000, 107000, f="june.pdf"),
             _ln(3, "2026-07-02", "out", 1000, 106000, f="june.pdf"),
             _ln(4, "2026-07-05", "out", 6000, 100000, f="july.pdf"), _ln(5, "2026-07-06", "in", 500, 100500, f="july.pdf"),
             _ln(6, "2026-07-06", "out", 100, 100400, f="july.pdf"),
             _ln(7, "2026-07-01", "in", 1000, 11000, status="ignored", f="misread.pdf"),
             _ln(8, "2026-07-02", "out", 300, 10700, status="ignored", f="misread.pdf"),
             _ln(9, "2026-07-02", "out", 100, 10600, status="ignored", f="misread.pdf")]
    stmts = ba.statements(lines)
    accts = ba.accounts(lines, stmts)
    assert len(accts) == 1 and accts[0]["files"] == ["june.pdf", "july.pdf"]
    assert stmts["misread.pdf"]["superseded"]


def test_a_probable_continuation_is_reported_not_joined():
    a = {"key": "A1", "first": date(2026, 7, 1), "last": date(2026, 7, 31), "opening": 0, "closing": 3789055}
    b = {"key": "A2", "first": date(2026, 8, 1), "last": date(2026, 8, 31), "opening": 3705748, "closing": 232586}
    got = ba.continuations([a, b])
    assert got == [{"from": "A1", "to": "A2", "a_closed": 3789055, "b_opened": 3705748, "difference": -83307,
                    "a_last": "2026-07-31", "b_first": "2026-08-01"}]


def test_money_between_own_accounts_is_a_transfer_not_income_and_a_cost():
    lines = [_ln(1, "2026-04-02", "out", 1500000, 100000, f="a.pdf", account_code="casual_labour"),
             _ln(2, "2026-04-03", "in", 1500000, 1600000, f="b.pdf", account_code="other_income"),
             _ln(3, "2026-04-03", "in", 1500000, 1700000, f="a.pdf", account_code="sales")]   # same account: not one
    trs = ba.transfers(lines, {"a.pdf": "A1", "b.pdf": "A2"})
    assert [(t["out"], t["in"]) for t in trs] == [("t1", "t2")]


def _db(**tables):
    return FakeSupabase({k: v for k, v in tables.items()})


def test_the_whole_audit_reads_only_and_names_rows(monkeypatch):
    lines = [_ln(1, "2026-07-18", "in", 17591613, 18062480, account_code="sales", categorized_by="ai"),
             _ln(2, "2026-07-18", "out", 9200, 18053280, status="ignored"),
             _ln(3, "2026-07-20", "out", 250000, 17803280, account_code="cost_of_sales", categorized_by="owner"),
             _ln(4, "2026-07-21", "out", 4000, 17799280, account_code="owner_drawings", categorized_by="ai")]
    db = _db(commerce_bank_transactions=lines, commerce_bank_accounts=[],
             commerce_invoices=[{"id": "b1", "tenant_id": "t1", "direction": "inbound", "doc_type": "invoice",
                                 "status": "draft", "total_cents": 50000}],
             commerce_expenses=[], journal_entries=[{"id": "j1", "tenant_id": "t1", "entry_date": "2015-10-26",
                                                      "source_type": "expense", "source_id": "e1"}],
             commerce_invoice_settings=[{"tenant_id": "t1", "vat_registered": False}])
    before = {k: [dict(r) for r in v] for k, v in db.rows.items()}
    monkeypatch.setattr(ba, "_client", lambda: db)
    rep = ba.run("t1", today=date(2026, 11, 17))
    assert db.rows == before                                             # nothing written
    by = {s["key"]: s for s in rep["sections"]}
    assert by["restore"]["status"] == "problem" and by["restore"]["findings"][0]["rows"] == ["t2"]
    assert by["dates"]["findings"][0]["rows"] == ["j1"]
    assert by["paperwork"]["findings"][0]["rows"] == ["b1"]
    assert any(f["kind"] == "owner_drawings" for f in by["categories"]["findings"])
    assert by["documents"]["findings"][0]["rows"] == ["t3"]              # a R2,500 payment with no document
    assert not rep["summary"]["clean"]
    assert "Books audit" in ba.text_report(rep)


def test_vat_question_raised_only_when_income_passes_the_line_unregistered(monkeypatch):
    big = [_ln(i, f"2026-0{1 + i % 8}-10", "in", 20_000_000, 20_000_000 * (i + 1), account_code="sales",
               categorized_by="owner", matched_invoice_id="x") for i in range(6)]          # R1.2m in 8 months
    for registered, expect in ((False, "problem"), (True, "ok")):
        db = _db(commerce_bank_transactions=big, commerce_bank_accounts=[], commerce_invoices=[],
                 commerce_expenses=[], journal_entries=[],
                 commerce_invoice_settings=[{"tenant_id": "t1", "vat_registered": registered}])
        monkeypatch.setattr(ba, "_client", lambda: db)
        vat = {s["key"]: s for s in ba.run("t1", today=date(2026, 11, 17))["sections"]}["vat"]
        assert vat["status"] == expect
