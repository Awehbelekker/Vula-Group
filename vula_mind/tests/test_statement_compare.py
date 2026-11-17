"""17 Nov 2026 (Ian, DIGG): each official FNB statement is compared line by line with Vula's rows,
and nothing changes until an owner or manager confirms (vula/commerce/statement_compare.py)."""
from vula.commerce import statement_compare as sc
from tests.fake_supabase import FakeSupabase
from tests.test_fnb_statement import TEXT

NUMBER = "62012345678"
OFFICIAL = TEXT.replace("PLATINUM BUSINESS ACCOUNT\nPage 1", f"Platinum Business Account : {NUMBER}\nPage 1", 1)


def _row(i, d, direction, cents, bal, status="unmatched", f="week.pdf", desc="", **kw):
    return {"id": f"r{i}", "tenant_id": "t1", "source_file": f, "txn_date": d, "direction": direction,
            "amount_cents": cents, "balance_cents": bal, "match_status": status, "description": desc,
            "bank_account": None, **kw}


def _rows():
    return [
        _row(1, "2026-09-22", "out", 30000, 70000, desc="Payshap Account Off-Us Hpc Paint"),        # right
        _row(2, "2026-09-22", "out", 8200, -12000, desc="FNB App Payment To Nelitho Wages"),         # misread ÷10
        _row(3, "2026-09-23", "in", 500000, 488000, status="ignored", desc="Magtape Credit Hpc-PC9"),  # set aside by mistake
        _row(5, "2026-09-24", "out", 5000, 483000, desc="POS Purchase Ghost"),                        # not on it
        _row(6, "2026-09-24", "out", 7000, 476000, desc="Wages Sipho", worker_id="w1"),               # not on it, linked
        _row(7, "2026-09-23", "out", 12345, 900000, f="other.pdf", desc="Another account"),           # other account
    ]


def _db():
    return FakeSupabase({"commerce_bank_transactions": _rows(), "vula_statement_checks": [],
                         "vula_merchant_audit": []})


def test_every_kind_of_difference_is_proposed(monkeypatch):
    db = _db()
    monkeypatch.setattr(sc, "_client", lambda: db)
    out = sc.compare("t1", filename="FNB official Sep.pdf", text=OFFICIAL, created_by="owner")
    kinds = {(a["kind"], a.get("row_id")) for a in out["actions"]}
    assert ("set_aside", "r2") in kinds                       # the misread copy
    assert ("restore", "r3") in kinds                         # the right line, set aside by mistake
    assert ("set_aside", "r5") in kinds                       # not on the official statement
    assert ("review", "r6") in kinds                          # not on it but tied to a worker: a person decides
    adds = [a["line"] for a in out["actions"] if a["kind"] == "add"]
    assert sorted(x["amount_cents"] for x in adds) == [82000, 99050]   # the right amount + the missing line
    assert not any(a.get("row_id") == "r7" for a in out["actions"])    # another account's row left alone
    tag = next(a for a in out["actions"] if a["kind"] == "tag")
    assert set(tag["row_ids"]) == {"r1", "r3"} and tag["account"] == NUMBER
    assert out["summary"]["already_right"] == 1 and out["summary"]["other_accounts_rows_left_alone"] == 1
    assert out["account"] == "…5678" and out["closing_cents"] == 388950


def test_comparing_writes_only_the_check(monkeypatch):
    db = _db()
    before = [dict(r) for r in db.rows["commerce_bank_transactions"]]
    monkeypatch.setattr(sc, "_client", lambda: db)
    out = sc.compare("t1", filename="FNB official Sep.pdf", text=OFFICIAL)
    assert db.rows["commerce_bank_transactions"] == before
    assert len(db.rows["vula_statement_checks"]) == 1 and out["check_id"]
    assert db.rows["vula_statement_checks"][0]["status"] == "proposed"


def test_a_statement_that_does_not_reconcile_proposes_nothing(monkeypatch):
    db = _db()
    monkeypatch.setattr(sc, "_client", lambda: db)
    bad = OFFICIAL.replace("4,880.00Cr", "4,881.00Cr")
    out = sc.compare("t1", filename="x.pdf", text=bad)
    assert out == {"error": sc.UNREADABLE} and db.rows["vula_statement_checks"] == []


def test_apply_needs_an_owner_or_manager(monkeypatch):
    db = _db()
    monkeypatch.setattr(sc, "_client", lambda: db)
    cid = sc.compare("t1", filename="FNB official Sep.pdf", text=OFFICIAL)["check_id"]
    got = sc.apply("t1", cid, person="staff", full_access=False)
    assert "owner or manager" in got["error"]
    assert db.rows["vula_statement_checks"][0]["status"] == "proposed"


def test_apply_does_only_what_was_accepted_reads_it_back_and_keeps_the_record(monkeypatch):
    db = _db()
    monkeypatch.setattr(sc, "_client", lambda: db)
    monkeypatch.setattr("vula.api.merchant_audit._client", lambda: db)
    out = sc.compare("t1", filename="FNB official Sep.pdf", text=OFFICIAL)
    by = {(a["kind"], a.get("row_id")): a["id"] for a in out["actions"]}
    accept = [by[("restore", "r3")], by[("set_aside", "r2")]] + \
             [a["id"] for a in out["actions"] if a["kind"] in ("add", "tag", "review")]
    got = sc.apply("t1", out["check_id"], person="owner", full_access=True, accept=accept)
    assert got["ok"] and got["not_saved"] == []
    rows = {r["id"]: r for r in db.rows["commerce_bank_transactions"]}
    assert rows["r3"]["match_status"] == "unmatched" and rows["r3"]["bank_account"] == NUMBER
    assert rows["r2"]["match_status"] == "ignored" and "Misread" in rows["r2"]["excluded_reason"]
    assert rows["r5"]["match_status"] == "unmatched"           # not accepted: left as it was
    assert rows["r6"]["match_status"] == "unmatched"           # review is never done automatically
    added = [r for r in rows.values() if r.get("source_file") == "FNB official Sep.pdf"]
    assert sorted(r["amount_cents"] for r in added) == [82000, 99050]
    assert all(r["bank_account"] == NUMBER for r in added)
    check = db.rows["vula_statement_checks"][0]
    assert check["status"] == "applied" and check["applied_by"] == "owner"
    assert any(a["action"] == "statement_check_applied" for a in db.rows["vula_merchant_audit"])
    assert "already applied" in sc.apply("t1", out["check_id"], person="owner", full_access=True)["error"]


def test_another_business_rows_are_never_read(monkeypatch):
    db = _db()
    db.rows["commerce_bank_transactions"].append(
        {**_row(9, "2026-09-25", "out", 99050, 388950, desc="POS Purchase Build It City Bi"), "tenant_id": "t2"})
    monkeypatch.setattr(sc, "_client", lambda: db)
    out = sc.compare("t1", filename="FNB official Sep.pdf", text=OFFICIAL)
    assert any(a["kind"] == "add" and a["line"]["amount_cents"] == 99050 for a in out["actions"])
    assert not any(a.get("row_id") == "r9" for a in out["actions"])


def test_the_routes_are_for_an_owner_or_manager_only(monkeypatch):
    import asyncio
    import pytest
    from fastapi import HTTPException
    from vula.api import books_audit as api

    class Req:
        headers = {"authorization": "Bearer x"}

    async def staff(auth, tenant):
        return {"full": False, "access": ["bank"], "role": "staff"}

    monkeypatch.setattr("vula.api.tenant_auth.member_scope", staff)
    with pytest.raises(HTTPException) as e:
        asyncio.run(api._full("t1", Req()))
    assert e.value.status_code == 403
