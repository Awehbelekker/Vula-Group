"""Corrections to the books drafted for an approver, made only on APPROVE (3 Nov 2026).

The owner wanted a payment split and two duplicate records cancelled, but approved by the
business's bookkeeper on WhatsApp rather than done straight away. A drafted approval is stored as
'staged'; the scheduler sends the question; APPROVE makes the change and reads it back; REJECT
changes nothing. Made-up business, people and figures throughout.
"""
from datetime import datetime, timezone

import pytest

from vula.commerce import approvals

T = "thandi-demo"
OWNER, BOOKKEEPER = "27820000001", "27820000002"


class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self.payload, self.rows_in = db, table, [], None, None

    def select(self, *_a, **_k): return self
    def order(self, *_a, **_k): return self
    def limit(self, *_a): return self
    def eq(self, col, val): self.filters.append(lambda r, c=col, v=val: r.get(c) == v); return self
    def in_(self, col, vals): self.filters.append(lambda r, c=col, v=set(vals): r.get(c) in v); return self
    def update(self, payload): self.payload = payload; return self
    def insert(self, rows): self.rows_in = rows if isinstance(rows, list) else [rows]; return self

    def execute(self):
        table = self.db.setdefault(self.table, [])
        if self.rows_in is not None:
            out = []
            for r in self.rows_in:
                row = {"id": f"{self.table}-{len(table) + 1}", **r}
                table.append(row)
                out.append(row)
            return type("R", (), {"data": out, "count": len(out)})()
        rows = [r for r in table if all(f(r) for f in self.filters)]
        if self.payload is not None:
            for r in rows:
                r.update(self.payload)
        return type("R", (), {"data": [dict(r) for r in rows], "count": len(rows)})()


def _staged(i, entity_type, entity_id, title, meta):
    return {"id": i, "tenant_id": T, "status": "staged", "entity_type": entity_type, "entity_id": entity_id,
            "title": title, "requested_by": OWNER, "meta": meta,
            "created_at": datetime.now(timezone.utc).isoformat()}


@pytest.fixture()
def env(monkeypatch):
    db = {
        "vula_approvals": [
            _staged("a1", "bank_split", "t1", "Split the R19,000.00 payment of 25 Sep",
                    {"txn_id": "t1", "parts": [{"project": "Seaview", "amount_cents": 1505000}]}),
            _staged("a2", "cancel_record", "e1", "Cancel the R55,400.00 expense (duplicate)",
                    {"table": "commerce_expenses", "id": "e1", "reason": "Duplicate of a bank payment"}),
            _staged("a3", "cancel_record", "b1", "Cancel draft bill BILL-00149",
                    {"table": "commerce_invoices", "id": "b1", "reason": "Day sheet, not a bill"}),
        ],
        "vula_approval_steps": [{"id": f"s{i}", "approval_id": f"a{i}", "approver_phone": BOOKKEEPER,
                                 "approver_name": "Thandi", "status": "pending"} for i in (1, 2, 3)],
        "commerce_expenses": [{"id": "e1", "tenant_id": T, "status": "submitted", "amount_cents": 5540000}],
        "commerce_invoices": [{"id": "b1", "tenant_id": T, "status": "draft", "total_cents": 2710000},
                              {"id": "b2", "tenant_id": T, "status": "paid", "total_cents": 100}],
    }
    client = type("C", (), {"table": lambda self, t: _Q(db, t)})()
    monkeypatch.setattr(approvals, "_client", lambda: client)
    sent, asked, splits = [], [], []

    async def fake_send(phone, text, tenant_id=None, **_k):
        sent.append((phone, text))
    monkeypatch.setattr("vula.api.whatsapp._send_reply", fake_send)

    async def ask_or_queue(tenant_id, phone, kind, ref, prompt, msg):
        asked.append((phone, kind, ref))
        await fake_send(phone, msg)
    monkeypatch.setattr("vula.open_questions.ask_or_queue", ask_or_queue)
    monkeypatch.setattr("vula.open_questions.ask", lambda *a, **k: None)
    monkeypatch.setattr("vula.open_questions.close_mine", lambda *a, **k: None)
    monkeypatch.setattr("vula.open_questions.close_for", lambda *a, **k: None)
    monkeypatch.setattr("vula.commerce.decisions.record", lambda *a, **k: None)

    def fake_split(tenant_id, txn_id, parts, by=None, source="whatsapp"):
        splits.append((tenant_id, txn_id, parts, by))
        line = {"txn_date": "2026-09-25", "description": "Wages", "amount_cents": 1900000, "project": "Oak Street"}
        return {"line": line, "parts": [{"project": p["project"], "amount_cents": p["amount_cents"]} for p in parts],
                "remainder_cents": 395000}
    monkeypatch.setattr("vula.commerce.bank_splits.split", fake_split)
    return db, sent, asked, splits


@pytest.mark.asyncio
async def test_staged_approvals_are_sent_once(env):
    db, sent, asked, _ = env
    assert await approvals.send_staged() == 3
    assert [a["status"] for a in db["vula_approvals"]] == ["pending"] * 3
    assert len(asked) == 3 and all(p == BOOKKEEPER and k == "approval" for p, k, _ in asked)
    assert all("Reply *APPROVE*" in t for _, t in sent)
    assert await approvals.send_staged() == 0                      # never sent twice


@pytest.mark.asyncio
async def test_nothing_changes_before_approve(env):
    db, *_ = env
    await approvals.send_staged()
    assert db["commerce_expenses"][0]["status"] == "submitted"
    assert db["commerce_invoices"][0]["status"] == "draft"


@pytest.mark.asyncio
async def test_approve_splits_the_payment(env):
    db, sent, _, splits = env
    await approvals.send_staged()
    await approvals.record_decision(BOOKKEEPER, "approved", approval_id="a1")
    assert splits == [(T, "t1", [{"project": "Seaview", "amount_cents": 1505000}], "Thandi")]
    assert any("R15,050.00 to Seaview" in t for p, t in sent if p == BOOKKEEPER)
    assert any("Thandi approved" in t for p, t in sent if p == OWNER)          # the owner hears too


@pytest.mark.asyncio
async def test_approve_cancels_and_reads_back(env):
    db, sent, *_ = env
    await approvals.send_staged()
    await approvals.record_decision(BOOKKEEPER, "approved", approval_id="a2")
    await approvals.record_decision(BOOKKEEPER, "approved", approval_id="a3")
    assert db["commerce_expenses"][0]["status"] == "cancelled"
    bill = db["commerce_invoices"][0]
    assert bill["status"] == "cancelled" and bill["cancel_reason"] == "Day sheet, not a bill"
    assert sum("is cancelled (kept on record, not deleted)" in t for _, t in sent) >= 2


@pytest.mark.asyncio
async def test_reject_changes_nothing(env):
    db, *_ = env
    await approvals.send_staged()
    await approvals.record_decision(BOOKKEEPER, "rejected", "keep it", approval_id="a2")
    assert db["commerce_expenses"][0]["status"] == "submitted"
    assert db["vula_approvals"][1]["status"] == "rejected"


@pytest.mark.asyncio
async def test_a_paid_record_is_never_cancelled(env):
    db, sent, *_ = env
    db["vula_approvals"][2]["meta"]["id"] = "b2"
    await approvals.send_staged()
    await approvals.record_decision(BOOKKEEPER, "approved", approval_id="a3")
    assert db["commerce_invoices"][1]["status"] == "paid"
    assert any("already paid" in t for _, t in sent)


@pytest.mark.asyncio
async def test_only_expenses_and_bills_can_be_cancelled(env):
    db, sent, *_ = env
    db["vula_approvals"][1]["meta"]["table"] = "commerce_orders"
    await approvals.send_staged()
    await approvals.record_decision(BOOKKEEPER, "approved", approval_id="a2")
    assert db["commerce_expenses"][0]["status"] == "submitted"
    assert any("can't be cancelled this way" in t for _, t in sent)


@pytest.mark.asyncio
async def test_another_business_record_is_not_touched(env):
    db, sent, *_ = env
    db["commerce_expenses"][0]["tenant_id"] = "other"
    await approvals.send_staged()
    await approvals.record_decision(BOOKKEEPER, "approved", approval_id="a2")
    assert db["commerce_expenses"][0]["status"] == "submitted"
    assert any("wasn't found" in t for _, t in sent)
