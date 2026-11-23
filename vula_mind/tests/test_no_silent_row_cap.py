"""22 Nov 2026: the database hands back at most 1 000 rows a request, whatever `.limit(N)` asks for,
and says nothing when it cuts a list short. The year-end pack's add-up check found DIGG's year of
1 346 bank lines read as 998; 96 places asked for more than 1 000 rows in one request. They now read
page by page (vula/db_pages.all_rows). This file keeps it that way, and checks real sites with more
than 1 000 rows against the cap (FakeSupabase.max_rows). Made-up data."""
import ast
import asyncio
from pathlib import Path

import pytest

from vula import db_pages
from tests.fake_supabase import FakeSupabase

ROOT = Path(__file__).resolve().parent.parent
T = "t1"


def _big_limits():
    """Every `.limit(<1000 or more>)` in the code that runs (vula/, core/): a request returns at most 1 000 rows,
    so a limit that high means "all" — and gets 1 000. A real "first N" stays below 1 000."""
    out = []
    for path in sorted(list((ROOT / "vula").rglob("*.py")) + list((ROOT / "core").rglob("*.py"))):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "limit"
                    and node.args and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, int) and node.args[0].value >= 1000):
                out.append(f"{path.relative_to(ROOT)}:{node.lineno} .limit({node.args[0].value})")
    return out


def test_no_query_asks_for_a_thousand_rows_or_more_in_one_request():
    found = _big_limits()
    assert found == [], ("A request returns at most 1 000 rows — read these with vula.db_pages.all_rows(…, cap=N):\n"
                         + "\n".join(found))


def test_the_guard_finds_one(tmp_path, monkeypatch):
    bad = tmp_path / "vula" / "x.py"
    bad.parent.mkdir()
    bad.write_text('rows = db.table("t").select("*").limit(1000).execute().data\n')
    (tmp_path / "core").mkdir()
    monkeypatch.setattr("tests.test_no_silent_row_cap.ROOT", tmp_path)
    assert _big_limits() == ["vula/x.py:1 .limit(1000)"]


# ── the helper ───────────────────────────────────────────────────────────────

@pytest.fixture
def db():
    d = FakeSupabase({"rows": [{"id": f"r{i:05d}", "tenant_id": T, "n": i} for i in range(2500)]
                      + [{"id": "x1", "tenant_id": "other", "n": 0}]})
    d.max_rows = 1000
    return d


def test_every_row_comes_back_once_in_order(db):
    one = db.table("rows").select("*").eq("tenant_id", T).execute().data
    assert len(one) == 1000                                       # what a single request gives
    rows = db_pages.all_rows(lambda: db.table("rows").select("*").eq("tenant_id", T))
    assert len(rows) == 2500 and len({r["id"] for r in rows}) == 2500
    assert [r["id"] for r in rows] == sorted(r["id"] for r in rows)
    assert all(r["tenant_id"] == T for r in rows)


def test_a_cap_stops_there(db):
    assert len(db_pages.all_rows(lambda: db.table("rows").select("*").eq("tenant_id", T), cap=1500)) == 1500
    assert len(db_pages.all_rows(lambda: db.table("rows").select("*").eq("tenant_id", T), cap=10)) == 10


def test_a_client_that_cannot_page_reads_once():
    class Q:
        def __init__(self): self.limited = None
        def select(self, *_a): return self
        def limit(self, n): self.limited = n; return self
        def execute(self):
            return type("R", (), {"data": [{"id": i} for i in range(5)]})()
    assert len(db_pages.all_rows(lambda: Q(), cap=3)) == 3
    assert len(db_pages.all_rows(lambda: Q())) == 5


# ── real sites past 1 000 rows ───────────────────────────────────────────────

def _lines(n, **kw):
    return [{"id": f"b{i:05d}", "tenant_id": T, "txn_date": f"2026-{3 + i % 9:02d}-15", "direction": "in",
             "amount_cents": 100, "match_status": "unmatched", "bank_account": "62012345678",
             "source_file": "stmt.pdf", **kw} for i in range(n)]


def test_money_in_and_out_reads_every_bank_line(monkeypatch):
    from core.skills import finance_admin
    from vula.commerce import service
    d = FakeSupabase({"commerce_bank_transactions": _lines(1300)})
    d.max_rows = 1000
    monkeypatch.setattr(service, "_client", lambda: d)
    out = finance_admin._bank_in_out(T, "2026-03-01", "2027-03-01")
    assert out["transactions"] == 1300 and out["money_in"] == 1300.0


def test_every_line_of_an_account_is_counted(monkeypatch):
    from vula.commerce import bank_accounts
    d = FakeSupabase({"commerce_bank_transactions": _lines(1300),
                      "commerce_bank_accounts": [{"id": "ba1", "tenant_id": T, "account_number": "62012345678",
                                                  "bank": "FNB", "last_seen": "2026-11-01"}]})
    d.max_rows = 1000
    monkeypatch.setattr(bank_accounts, "_client", lambda: d)
    monkeypatch.setattr(bank_accounts, "available", lambda: True)
    assert bank_accounts.list_accounts(T)[0]["lines"] == 1300


def test_re_posting_a_statement_reaches_every_line(monkeypatch):
    from vula.commerce import ledger_events as ev
    d = FakeSupabase({"commerce_bank_transactions": _lines(1300)})
    d.max_rows = 1000
    monkeypatch.setattr(ev, "_client", lambda: d)
    monkeypatch.setattr(ev, "on", lambda tid: True)
    seen = []
    monkeypatch.setattr(ev, "sync_bank_lines", lambda tid, ids: seen.extend(ids) or {"posted": len(ids)})
    ev.sync_bank_lines_where(T, source_file="stmt.pdf")
    assert len(seen) == 1300 and len(set(seen)) == 1300


def test_what_customers_owe_reads_every_invoice(monkeypatch):
    from vula.commerce import finances, ledger_v2, service
    invs = [{"id": f"i{i:05d}", "tenant_id": T, "direction": "outbound", "doc_type": "invoice", "status": "sent",
             "total_cents": 1000, "total_paid_cents": 0, "created_at": "2026-10-01T00:00:00",
             "due_date": "2099-01-01"} for i in range(1200)]
    d = FakeSupabase({"commerce_invoices": invs, "commerce_orders": [], "commerce_expenses": []})
    d.max_rows = 1000
    for mod in (finances, service):
        monkeypatch.setattr(mod, "_client", lambda: d)
    monkeypatch.setattr(ledger_v2, "is_on", lambda tid: False)
    out = asyncio.run(finances.insights(T, days=30))
    assert out["receivables"]["outstanding_cents"] == 1200 * 1000


def _open_invoices(n):
    return [{"id": f"i{i:05d}", "tenant_id": T, "invoice_number": f"INV-{i:05d}", "customer_name": "Sam",
             "direction": "outbound", "doc_type": "invoice", "status": "sent", "total_cents": 1000,
             "total_paid_cents": 0, "issue_date": "2026-10-01", "due_date": "2026-10-15"} for i in range(n)]


def test_a_bank_line_s_candidates_include_an_invoice_past_the_thousandth(monkeypatch):
    from vula.api import commerce
    from vula.commerce import service
    invs = _open_invoices(1200)
    invs[-1]["total_cents"] = 4321                                 # the one that matches, last by id
    d = FakeSupabase({"commerce_invoices": invs, "commerce_bank_transactions": [
        {"id": "t1", "tenant_id": T, "direction": "in", "amount_cents": 4321, "txn_date": "2026-10-20"}]})
    d.max_rows = 1000
    monkeypatch.setattr(service, "_client", lambda: d)
    out = asyncio.run(commerce.admin_bank_match_candidates(T, "t1"))
    assert out["candidates"][0]["number"] == "INV-01199" and out["candidates"][0]["exact"]


def test_the_owner_digest_counts_every_open_invoice(monkeypatch):
    from datetime import date
    from vula import owner_digest
    d = FakeSupabase({"commerce_invoices": _open_invoices(1200), "commerce_invoice_payments": []})
    d.max_rows = 1000
    monkeypatch.setattr(owner_digest, "_client", lambda: d)
    out = owner_digest.build(T, date(2026, 11, 1))
    assert out["overdue"] == [("Sam", 1200 * 1000)]                  # 1 200 × R10 overdue from Sam
