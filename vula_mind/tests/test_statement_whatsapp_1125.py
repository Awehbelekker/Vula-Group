"""25 Nov 2026 (Ian: "can I add them on WhatsApp?" — DIGG's missing bank statements; "other tenants
might want to upload through WhatsApp"). Every business can still send a bank statement on WhatsApp or
by email; while its books are under review (a books check run, not yet signed off) the statement is
compared like the Books review upload — kept as official, proposed, nothing written to the bank lines —
and waits for "Check again and apply all". Made-up statement (the September set of test_statement_compare)."""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from vula.commerce import bank_rec, books_review, statement_compare as sc, statement_files
from vula.ingestion import fnb_statement
from tests.fake_supabase import FakeSupabase
from tests.test_statement_compare import NUMBER, OFFICIAL, _rows

T = "t1"


@pytest.fixture
def db(monkeypatch, tmp_path):
    d = FakeSupabase({"commerce_bank_transactions": _rows(), "vula_statement_checks": [], "vula_merchant_audit": []})
    monkeypatch.setattr(sc, "_client", lambda: d)
    monkeypatch.setattr(bank_rec, "get_statement_password", lambda tid: None)
    monkeypatch.setattr(bank_rec, "_statement_account", lambda tid, text, txns: NUMBER)
    monkeypatch.setattr(bank_rec, "_private_owner", lambda tid, acct: None)
    monkeypatch.setattr(fnb_statement, "pdf_text", lambda path, password=None: OFFICIAL)
    keep = AsyncMock(return_value={"id": "doc-1"})
    monkeypatch.setattr(statement_files, "keep", keep)
    rec = AsyncMock(return_value={"parsed": 4})
    monkeypatch.setattr(bank_rec, "reconcile", rec)
    monkeypatch.setattr(bank_rec, "supersede_misread", lambda tid, txns: {})
    monkeypatch.setattr(bank_rec, "_keep_file", AsyncMock())
    d.keep, d.reconcile = keep, rec
    pdf = tmp_path / "FNB Sep.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    d.pdf = pdf
    return d


def _ingest(db, source):
    return asyncio.run(bank_rec.ingest_statement(T, db.pdf, source_file="FNB Sep.pdf", source=source))


def test_under_review_a_whatsapp_statement_is_compared_not_written(db, monkeypatch):
    monkeypatch.setattr(books_review, "under_review", lambda tid: True)
    rows = [dict(r) for r in db.rows["commerce_bank_transactions"]]
    out = _ingest(db, "whatsapp")
    assert out["compared"] and out["check_id"] and out["account"] == "…5678"
    assert out["summary"]["restore"] == 1 and out["summary"]["add"] == 2
    assert db.rows["commerce_bank_transactions"] == rows                      # nothing written
    assert not db.reconcile.await_count
    assert [c["status"] for c in db.rows["vula_statement_checks"]] == ["proposed"]
    assert db.keep.await_args.kwargs["official"] is True                      # kept, so "apply all" re-reads it


def test_under_review_an_email_statement_is_compared_too(db, monkeypatch):
    monkeypatch.setattr(books_review, "under_review", lambda tid: True)
    assert _ingest(db, "email")["compared"] and not db.reconcile.await_count


def test_not_under_review_whatsapp_works_as_before(db, monkeypatch):
    monkeypatch.setattr(books_review, "under_review", lambda tid: False)
    out = _ingest(db, "whatsapp")
    assert "compared" not in out and db.reconcile.await_count == 1             # other businesses: unchanged
    assert db.rows["vula_statement_checks"] == []


def test_the_dashboard_bank_upload_is_unchanged_under_review(db, monkeypatch):
    monkeypatch.setattr(books_review, "under_review", lambda tid: True)
    out = _ingest(db, "dashboard")
    assert "compared" not in out and db.reconcile.await_count == 1


def test_a_statement_not_read_to_the_cent_is_held_under_review(db, monkeypatch):
    monkeypatch.setattr(books_review, "under_review", lambda tid: True)
    monkeypatch.setattr(fnb_statement, "pdf_text", lambda path, password=None: "a scanned statement")
    model = AsyncMock(side_effect=AssertionError("never read by the model while under review"))
    monkeypatch.setattr(bank_rec, "extract_transactions", model)
    out = _ingest(db, "whatsapp")
    assert out["held"] and "Books review › Statements" in out["error"]
    assert not db.reconcile.await_count and db.rows["vula_statement_checks"] == []


def test_a_private_account_still_goes_to_its_owner(db, monkeypatch):
    monkeypatch.setattr(books_review, "under_review", lambda tid: True)
    monkeypatch.setattr(bank_rec, "_private_owner", lambda tid, acct: "m9")
    out = _ingest(db, "whatsapp")
    assert out["private"] and out["private_to"] == "m9" and db.rows["vula_statement_checks"] == []


def test_the_reply_says_what_it_found_and_that_nothing_changed():
    text = bank_rec.compared_text({"account": "…5678", "period": ["2026-09-01", "2026-09-30"],
                                   "summary": {"already_right": 40, "restore": 3, "add": 1, "review": 0}})
    assert text == ("🏦 Official statement …5678, 1 Sep 2026 – 30 Sep 2026, read to the cent: 40 lines already right, "
                    "3 lines to put back, 1 line to add. Nothing has changed yet — your books are being checked, so "
                    "apply it in Money › Books review › Statements › Check again and apply all.")


@pytest.mark.parametrize("audit,expected", [(None, False), ({"id": "a1", "signed_off_by": None}, True),
                                            ({"id": "a1", "signed_off_by": "master"}, False)])
def test_under_review_means_a_check_run_and_not_signed_off(monkeypatch, audit, expected):
    monkeypatch.setattr(books_review, "latest", lambda tid: audit)
    assert books_review.under_review(T) is expected


def test_under_review_fails_open_to_the_old_path(monkeypatch):
    def boom(tid):
        raise RuntimeError("db down")
    monkeypatch.setattr(books_review, "latest", boom)
    assert books_review.under_review(T) is False


def test_whatsapp_replies_with_the_totals(monkeypatch, tmp_path):
    from vula.api import whatsapp
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr(whatsapp, "_send_reply", sent)
    monkeypatch.setattr(whatsapp, "_statement_text", lambda tid, p: "Statement opening balance closing balance")
    monkeypatch.setattr(bank_rec, "ingest_statement", AsyncMock(return_value={
        "compared": True, "account": "…5678", "period": ["2026-09-01", "2026-09-30"],
        "summary": {"already_right": 40, "add": 2}}))
    p = tmp_path / "s.pdf"
    p.write_bytes(b"%PDF")
    assert asyncio.run(whatsapp._bank_statement_first(T, "27820000001", Path(p))) is True
    assert "Nothing has changed yet" in sent.await_args.args[1] and "2 lines to add" in sent.await_args.args[1]


# ── what's still missing (25 Nov 2026, Ian: "let tenants know if something is missing") ──────────

def _missing_db(monkeypatch):
    from datetime import date
    from vula.commerce import management_pack
    d = FakeSupabase({
        "commerce_bank_accounts": [{"id": "ba-1", "tenant_id": T, "account_number": NUMBER, "bank": "FNB", "name": "Cheque"},
                                   {"id": "ba-p", "tenant_id": T, "account_number": "62099990000", "bank": "FNB",
                                    "private_to": "m9"}],
        "vula_filed_documents": [
            {"tenant_id": T, "category": "Bank Statement", "status": "filed",
             "fields": {"bank_account_id": "ba-1", "official": True, "period_from": "2026-09-01", "period_to": "2026-09-30"}},
            {"tenant_id": T, "category": "Bank Statement", "status": "filed",
             "fields": {"bank_account_id": "ba-1", "official": True, "period_from": "2026-10-05", "period_to": "2026-10-10"}},
            {"tenant_id": T, "category": "Bank Statement", "status": "filed",           # Mon 12 – Fri 16 Oct
             "fields": {"bank_account_id": "ba-1", "official": True, "period_from": "2026-10-12", "period_to": "2026-10-16"}},
            {"tenant_id": T, "category": "Bank Statement", "status": "filed",           # not official: doesn't count
             "fields": {"bank_account_id": "ba-1", "official": False, "period_from": "2026-10-01", "period_to": "2026-10-04"}},
            {"tenant_id": "other", "category": "Bank Statement", "status": "filed",
             "fields": {"bank_account_id": "ba-1", "official": True, "period_from": "2026-10-17", "period_to": "2026-10-31"}}],
        "commerce_bank_transactions": [{"id": "l1", "tenant_id": T, "bank_account": NUMBER, "txn_date": "2026-08-20"}]})
    monkeypatch.setattr(statement_files, "_client", lambda: d)
    monkeypatch.setattr(management_pack, "first_trading_day", lambda tid, one_book: date(2026, 8, 25))
    return d


def test_the_days_with_no_statement_are_named_per_account(monkeypatch):
    from datetime import date
    _missing_db(monkeypatch)
    out = statement_files.missing(T, today=date(2026, 10, 21))
    assert len(out) == 1 and out[0]["last4"] == "5678"                        # never the private account
    assert out[0]["ranges"] == [(date(2026, 8, 25), date(2026, 8, 31)),       # from the books' start
                                (date(2026, 10, 1), date(2026, 10, 4)),       # only an unofficial copy
                                (date(2026, 10, 17), date(2026, 10, 20))]     # not the lone weekend of 11 Oct
    assert statement_files.ranges_text(out[0]["ranges"]) == "25 Aug – 31 Aug 2026; 1 Oct – 4 Oct 2026; 17 Oct – 20 Oct 2026"


def test_the_reply_names_what_is_still_missing():
    text = bank_rec.compared_text({"account": "…5678", "period": ["2026-09-01", "2026-09-30"],
                                   "summary": {"already_right": 40}, "still_missing": "17 Oct – 20 Oct 2026"})
    assert text.endswith("📭 Still missing for …5678: 17 Oct – 20 Oct 2026. Send those too.")


def test_a_compared_statement_reports_its_account_s_gaps(db, monkeypatch):
    from datetime import date
    monkeypatch.setattr(books_review, "under_review", lambda tid: True)
    monkeypatch.setattr(statement_files, "missing", lambda tid: [
        {"account": "Cheque …5678", "last4": "5678", "ranges": [(date(2026, 10, 1), date(2026, 10, 4))]},
        {"account": "Savings …1111", "last4": "1111", "ranges": [(date(2026, 1, 1), date(2026, 1, 2))]}])
    out = _ingest(db, "whatsapp")
    assert out["still_missing"] == "1 Oct – 4 Oct 2026"                       # its own account only
