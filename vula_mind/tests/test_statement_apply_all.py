"""25 Nov 2026 (Ian: clear DIGG's books review faster). Every waiting official-statement comparison
checked again against the bank lines as they are now, and only the changes that need no person applied
in one go (statement_compare.apply_all). DIGG had 35 waiting from 17–20 Nov, some the same period twice,
some out of date. Made-up statement and lines (the September set from test_statement_compare)."""
from datetime import date

import pytest

from vula.commerce import ledger_v2, management_pack, statement_compare as sc
from vula.ingestion import fnb_statement
from tests.fake_supabase import FakeSupabase
from tests.test_statement_compare import NUMBER, OFFICIAL, _rows

T = "t1"


@pytest.fixture
def db(monkeypatch):
    d = FakeSupabase({"commerce_bank_transactions": _rows(), "vula_statement_checks": [], "vula_merchant_audit": [],
                      "commerce_bank_accounts": [{"id": "ba-1", "tenant_id": T, "account_number": NUMBER}],
                      "vula_filed_documents": [], "vula_email_accounts": []})
    monkeypatch.setattr(sc, "_client", lambda: d)
    monkeypatch.setattr("vula.api.merchant_audit._client", lambda: d)
    monkeypatch.setattr(fnb_statement, "pdf_text", lambda path, password=None: OFFICIAL)
    monkeypatch.setattr(management_pack, "first_trading_day", lambda tid, one_book: date(2026, 1, 2))
    monkeypatch.setattr(ledger_v2, "is_on", lambda tid: False)
    return d


def _propose(db, filename="FNB official Sep.pdf", created="2026-11-18T08:00:00", keep=True):
    cid = sc.compare(T, filename=filename, text=OFFICIAL, created_by="owner")["check_id"]
    check = next(c for c in db.rows["vula_statement_checks"] if c["id"] == cid)
    check["created_at"] = created
    if keep:
        db.rows["vula_filed_documents"].append({
            "id": f"doc-{cid}", "tenant_id": T, "category": "Bank Statement", "filename": filename, "status": "filed",
            "file_url": f"https://x.supabase.co/storage/v1/object/public/documents/{T}/{filename}",
            "fields": {"official": True, "bank_account_id": "ba-1", "period_from": check["period_from"],
                       "period_to": check["period_to"]}})
    return check


def _row(db, i):
    return next(r for r in db.rows["commerce_bank_transactions"] if r["id"] == i)


def _run(preview=False, person="owner", full=True):
    return sc.apply_all(T, person=person, full_access=full, actor={"email": "owner@example.com"},
                        preview=preview, fetch=lambda url: b"%PDF-1.4")


def test_the_preview_writes_nothing(db):
    _propose(db)
    rows = [dict(r) for r in db.rows["commerce_bank_transactions"]]
    out = _run(preview=True)
    assert out["preview"] and out["statements"][0]["status"] == "would_apply"
    assert out["totals"]["restore"] == 1 and out["totals"]["add"] == 2 and out["totals"]["review"] == 1
    assert db.rows["commerce_bank_transactions"] == rows
    assert [c["status"] for c in db.rows["vula_statement_checks"]] == ["proposed"]


def test_applying_does_the_safe_changes_and_leaves_the_tied_line_for_a_person(db):
    old = _propose(db)
    out = _run()
    assert out["applied"] == 1 and out["failed"] == 0
    assert _row(db, "r3")["match_status"] == "unmatched"                   # restored
    assert _row(db, "r2")["match_status"] == "ignored"                     # the misread copy set aside
    assert _row(db, "r5")["match_status"] == "ignored"                     # not on the statement
    assert _row(db, "r6")["match_status"] == "unmatched"                   # tied to a worker: a person decides
    assert _row(db, "r7")["match_status"] == "unmatched"                   # another account's line left alone
    added = sorted(r["amount_cents"] for r in db.rows["commerce_bank_transactions"] if not r["id"].startswith("r"))
    assert added == [82000, 99050]
    statuses = {c["id"]: c["status"] for c in db.rows["vula_statement_checks"]}
    assert statuses[old["id"]] == "discarded" and list(statuses.values()).count("applied") == 1
    assert any(a["action"] == "statement_checks_applied_all" for a in db.rows["vula_merchant_audit"])


def test_an_out_of_date_action_is_not_applied(db):
    _propose(db)                                                           # proposed: restore r3
    _row(db, "r3")["match_status"] = "unmatched"                           # someone restored it since
    out = _run(preview=True)
    assert out["statements"][0]["counts"]["restore"] == 0


def test_the_same_statement_uploaded_twice_is_applied_once(db):
    a = _propose(db, created="2026-11-17T08:00:00")
    b = _propose(db, filename="FNB official Sep (1).pdf", created="2026-11-19T08:00:00")
    out = _run()
    assert len(out["statements"]) == 1 and out["statements"][0]["copies"] == 2
    added = [r for r in db.rows["commerce_bank_transactions"] if not r["id"].startswith("r")]
    assert len(added) == 2                                                 # not four
    statuses = {c["id"]: c["status"] for c in db.rows["vula_statement_checks"]}
    assert statuses[a["id"]] == statuses[b["id"]] == "discarded"


def test_a_statement_from_before_the_books_start_is_left(db, monkeypatch):
    _propose(db)
    monkeypatch.setattr(management_pack, "first_trading_day", lambda tid, one_book: date(2027, 1, 1))
    rows = [dict(r) for r in db.rows["commerce_bank_transactions"]]
    out = _run()
    assert out["left"] == 1 and "Before the books start" in out["statements"][0]["why"]
    assert db.rows["commerce_bank_transactions"] == rows
    assert [c["status"] for c in db.rows["vula_statement_checks"]] == ["proposed"]


def test_a_statement_whose_file_is_not_kept_is_left(db):
    _propose(db, keep=False)
    out = _run()
    assert out["left"] == 1 and "isn't kept" in out["statements"][0]["why"]
    assert _row(db, "r3")["match_status"] == "ignored"


def test_only_an_owner_or_manager(db):
    _propose(db)
    assert "owner or manager" in _run(full=False)["error"]


def test_another_business_is_never_touched(db):
    db.rows["vula_statement_checks"].append({"id": "x1", "tenant_id": "other", "status": "proposed",
                                             "filename": "theirs.pdf", "account_number": NUMBER,
                                             "period_from": "2026-09-01", "period_to": "2026-09-30",
                                             "created_at": "2026-11-18T08:00:00"})
    _propose(db)
    out = _run()
    assert len(out["statements"]) == 1
    assert next(c for c in db.rows["vula_statement_checks"] if c["id"] == "x1")["status"] == "proposed"
