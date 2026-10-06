"""A reply goes to the question the person was actually asked (migration 194).

Real replies, digg-demo 5–6 Oct 2026, owner's phone:
- "Approve" (to the STE Scaffolding supplier question) approved a June test invoice instead.
- "Atlantis Paarden Eiland" (to "which project?" for the POP just sent) went to a Bauxite expense
  claim from 28 August.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from vula import open_questions as oq
from vula.api import whatsapp as wa

TID, PHONE = "digg-demo", "27645755210"
NOW = datetime.now(timezone.utc)


class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.f, self.payload, self.ins, self.order_key = db, table, [], None, None, None

    def select(self, *_a, **_k):
        return self

    def insert(self, row):
        rows = row if isinstance(row, list) else [row]
        self.ins = [dict(r, id=f"q{len(self.db[self.table]) + i + 1}") for i, r in enumerate(rows)]
        return self

    def update(self, payload):
        self.payload = payload
        return self

    def eq(self, col, val):
        self.f.append(lambda r: r.get(col) == val)
        return self

    def gte(self, col, val):
        self.f.append(lambda r: (r.get(col) or "") >= val)
        return self

    def in_(self, col, vals):
        self.f.append(lambda r: r.get(col) in vals)
        return self

    def order(self, col, desc=False):
        self.order_key = (col, desc)
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        if self.ins is not None:
            self.db[self.table].extend(self.ins)
            return type("R", (), {"data": self.ins})()
        rows = [r for r in self.db[self.table] if all(f(r) for f in self.f)]
        if self.order_key:
            rows.sort(key=lambda r: r.get(self.order_key[0]) or "", reverse=self.order_key[1])
        if self.payload is not None:
            for r in rows:
                r.update(self.payload)
        return type("R", (), {"data": rows})()


@pytest.fixture()
def db(monkeypatch):
    data = {"vula_open_questions": [], "commerce_bank_transactions": []}
    client = type("C", (), {"table": lambda self, t: _Q(data, t)})()
    monkeypatch.setattr(oq, "_client", lambda: client)
    monkeypatch.setattr("vula.commerce.service._client", lambda: client)
    return data


def _asked(db, kind, ref, minutes_ago):
    t = NOW - timedelta(minutes=minutes_ago)
    db["vula_open_questions"].append({
        "id": f"{kind}-{ref}", "tenant_id": TID, "phone": PHONE, "kind": kind, "ref_id": ref,
        "status": "open", "asked_at": t.isoformat(), "expires_at": (t + timedelta(days=3)).isoformat()})


def test_ask_then_current_then_close(db):
    qid = oq.ask(TID, "0645755210", "doc_project", "doc-pop", "Which project: Payment Notification (16).pdf")
    assert qid and oq.current(TID, PHONE)["ref_id"] == "doc-pop"      # 0… → 27… normalised
    oq.close_for(TID, "doc-pop")
    assert oq.current(TID, PHONE) is None


def test_lookup_failure_falls_back_to_the_old_handlers(monkeypatch):
    monkeypatch.setattr(oq, "_client", lambda: (_ for _ in ()).throw(RuntimeError("no table")))
    assert oq.open_for(TID, PHONE) == [] and oq.ask(TID, PHONE, "approval", "a1") is None


@pytest.mark.asyncio
async def test_approve_goes_to_the_approval_that_was_asked(db):
    _asked(db, "approval", "appr-ste", 25)
    decide = AsyncMock(return_value={"id": "appr-ste"})
    with patch("vula.commerce.approvals.record_decision", decide):
        assert await wa._answer_open_question(TID, PHONE, "Approve")
    decide.assert_awaited_once_with(PHONE, "approved", "", approval_id="appr-ste")


@pytest.mark.asyncio
async def test_atlantis_files_the_pop_it_was_asked_about(db):
    _asked(db, "approval", "appr-ste", 600)       # older, still open
    _asked(db, "doc_project", "doc-pop", 1)       # the question just asked
    resolve = AsyncMock(return_value={"filed": True, "project": "Atlantis Paarden Eiland",
                                      "filename": "Proof of Payment 20261006-0152.pdf"})
    decide = AsyncMock()
    with patch("vula.integrations.doc_filing.resolve_pending_document", resolve), \
         patch("vula.commerce.approvals.record_decision", decide), \
         patch.object(wa, "_send_reply", AsyncMock()) as reply:
        assert await wa._answer_open_question(TID, PHONE, "Atlantis Paarden Eiland")
    resolve.assert_awaited_once_with(TID, PHONE, "Atlantis Paarden Eiland", doc_id="doc-pop")
    decide.assert_not_called()
    assert "under *Atlantis Paarden Eiland*" in reply.await_args.args[1]


@pytest.mark.asyncio
async def test_a_project_name_skips_a_newer_approval_and_reaches_the_project_question(db):
    _asked(db, "doc_project", "doc-pop", 30)
    _asked(db, "approval", "appr-ste", 1)          # newer, but "Atlantis" isn't an approval answer
    resolve = AsyncMock(return_value={"filed": True, "project": "Atlantis Paarden Eiland", "filename": "pop.pdf"})
    with patch("vula.integrations.doc_filing.resolve_pending_document", resolve), \
         patch.object(wa, "_send_reply", AsyncMock()):
        assert await wa._answer_open_question(TID, PHONE, "Atlantis Paarden Eiland")
    assert resolve.await_args.kwargs["doc_id"] == "doc-pop"


@pytest.mark.asyncio
async def test_yes_goes_to_the_newest_question(db):
    _asked(db, "doc_project", "doc-pop", 30)
    _asked(db, "pop_match", "txn-ste", 1)
    db["commerce_bank_transactions"].append({"id": "txn-ste", "tenant_id": TID, "match_status": "asked",
                                             "direction": "out", "amount_cents": 239717})
    answer = AsyncMock(return_value="✅ R2,397.17 → bill *DIG-BILL-00092* (STE Scaffolding) — marked paid.")
    resolve = AsyncMock()
    with patch("vula.commerce.bank_review._handle_supplier_pop_answer", answer), \
         patch("vula.integrations.doc_filing.resolve_pending_document", resolve), \
         patch.object(wa, "_send_reply", AsyncMock()) as reply:
        assert await wa._answer_open_question(TID, PHONE, "yes")
    resolve.assert_not_called()
    assert "marked paid" in reply.await_args.args[1]


@pytest.mark.asyncio
async def test_a_real_request_is_not_taken_as_an_answer(db):
    _asked(db, "doc_project", "doc-pop", 1)
    resolve = AsyncMock(return_value=None)          # resolve_pending_document: not answer-shaped
    with patch("vula.integrations.doc_filing.resolve_pending_document", resolve):
        assert not await wa._answer_open_question(TID, PHONE, "Please give me all invoices for jack hammer?")
    assert db["vula_open_questions"][0]["status"] == "open"        # still waiting


@pytest.mark.asyncio
async def test_an_approval_decided_elsewhere_stops_claiming_replies(db):
    _asked(db, "approval", "appr-old", 5)
    with patch("vula.commerce.approvals.record_decision", AsyncMock(return_value=None)):
        assert not await wa._answer_open_question(TID, PHONE, "Approve")
    assert db["vula_open_questions"][0]["status"] == "expired"


@pytest.mark.asyncio
async def test_no_open_question_leaves_the_old_flow_alone(db):
    assert not await wa._answer_open_question(TID, PHONE, "Atlantis Paarden Eiland")


# ── every question records itself ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_asking_which_project_records_the_question(db, monkeypatch):
    from vula.integrations import doc_filing
    monkeypatch.setattr(doc_filing, "project_question", lambda t, d: "📂 Which project?")
    monkeypatch.setattr(doc_filing, "mark_asked", lambda *a: None)
    with patch.object(wa, "_send_reply", AsyncMock(return_value=True)):
        await doc_filing.ask_project(TID, {"id": "doc-ste", "filename": "Tax Invoice STE00866.PDF"}, [PHONE])
    q = oq.current(TID, PHONE)
    assert (q["kind"], q["ref_id"]) == ("doc_project", "doc-ste")


@pytest.mark.asyncio
async def test_an_approval_request_records_a_question_per_approver(db, monkeypatch):
    from vula.commerce import approvals
    db.update({"vula_approvals": [], "vula_approval_steps": []})
    client = type("C", (), {"table": lambda self, t: _Q(db, t)})()
    monkeypatch.setattr(approvals, "_client", lambda: client)
    with patch.object(wa, "_send_reply", AsyncMock(return_value=True)):
        await approvals.create_approval(TID, "inbound_invoice", "inv-ste",
                                        "Supplier match: is *STE SCAFFOLDING SA*?",
                                        [{"phone": "0645755210"}, {"phone": "27827077080"}])
    asked = {(q["phone"], q["kind"]) for q in db["vula_open_questions"]}
    assert asked == {(PHONE, "approval"), ("27827077080", "approval")}


def test_a_supplier_pop_records_its_question(db, monkeypatch):
    from vula.commerce import bank_rec
    monkeypatch.setattr(bank_rec, "_client", lambda: type("C", (), {"table": lambda self, t: _Q(db, t)})())
    monkeypatch.setattr(bank_rec, "_open_supplier_bills", lambda t: [
        {"id": "bill-ste", "invoice_number": "DIG-BILL-00092", "total_cents": 239717,
         "supplier": "STE Scaffolding S A (Pty) Ltd (Cape)"}])
    bank_rec._stage_supplier_pop(TID, 239717, "2026-10-05", "Digg", "Ste", sender_phone=PHONE)
    q = oq.current(TID, PHONE)
    assert q["kind"] == "pop_match" and q["ref_id"] == db["commerce_bank_transactions"][0]["id"]


# ── step 1b: "which project?" and "company card or own money?" on a receipt ───────
def _claims(db):
    db["commerce_expenses"] = [
        {"id": "bauxite", "tenant_id": TID, "amount_cents": 1807674, "project": None, "paid_with": "personal",
         "status": "submitted", "created_at": "2026-08-28T11:46:33Z"},
        {"id": "fuel", "tenant_id": TID, "amount_cents": 87150, "project": None, "paid_with": None,
         "status": "submitted", "created_at": NOW.isoformat()},
    ]


@pytest.mark.asyncio
async def test_project_answer_goes_to_the_receipt_that_was_asked_about(db, monkeypatch):
    _claims(db)
    _asked(db, "expense_project", "fuel", 1)
    assigned = []
    monkeypatch.setattr("vula.commerce.expenses.match_project", lambda t, x: "Atlantis Paarden Eiland")
    monkeypatch.setattr("vula.commerce.expenses.assign", lambda t, cid, project: assigned.append((cid, project)))
    with patch.object(wa, "_send_reply", AsyncMock()) as reply:
        assert await wa._answer_open_question(TID, PHONE, "Atlantis Paarden Eiland")
    assert assigned == [("fuel", "Atlantis Paarden Eiland")]          # never the Bauxite claim
    assert "R871.50" in reply.await_args.args[1]
    assert db["vula_open_questions"][0]["status"] == "answered"


@pytest.mark.asyncio
async def test_own_money_answers_the_paid_with_question_not_the_project_one(db, monkeypatch):
    _claims(db)
    _asked(db, "expense_project", "fuel", 1)
    _asked(db, "expense_paid_with", "fuel", 1)
    monkeypatch.setattr("vula.commerce.expenses.match_project", lambda t, x: None)
    monkeypatch.setattr("vula.commerce.service._now", lambda: NOW.isoformat())
    with patch.object(wa, "_send_reply", AsyncMock()) as reply:
        assert await wa._answer_open_question(TID, PHONE, "own money")
    assert db["commerce_expenses"][1]["paid_with"] == "personal"
    assert "paid back to you" in reply.await_args.args[1]
    still_open = [q["kind"] for q in db["vula_open_questions"] if q["status"] == "open"]
    assert still_open == ["expense_project"]


@pytest.mark.asyncio
async def test_a_request_is_not_taken_as_a_receipt_answer(db, monkeypatch):
    _claims(db)
    _asked(db, "expense_project", "fuel", 1)
    monkeypatch.setattr("vula.commerce.expenses.match_project", lambda t, x: "HPC Bokaap")
    assert not await wa._answer_open_question(TID, PHONE, "Can you send me the HPC invoices?")
