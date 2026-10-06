"""APPROVE/REJECT answers the approver's most recent question (2026-10-05, digg-demo).

The owner's "Approve" to that afternoon's STE Scaffolding supplier question approved a June test
invoice instead and tried to send it to the client: steps were picked by a random UUID order and
never aged out. The send itself then failed (wrong port) while the owner was told "Delivering now".
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from vula.commerce import approvals

NOW = datetime.now(timezone.utc)
PHONE = "27645755210"


class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self.payload = db, table, [], None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self.filters.append(lambda r, c=col, v=val: r.get(c) == v)
        return self

    def in_(self, col, vals):
        self.filters.append(lambda r, c=col, v=set(vals): r.get(c) in v)
        return self

    def limit(self, *_a):
        return self

    def update(self, payload):
        self.payload = payload
        return self

    def execute(self):
        rows = [r for r in self.db[self.table] if all(f(r) for f in self.filters)]
        if self.payload is not None:
            for r in rows:
                r.update(self.payload)
        return type("R", (), {"data": rows, "count": len(rows)})()


def _db():
    june = {"id": "a-june", "tenant_id": "digg-demo", "status": "pending", "entity_type": "invoice",
            "entity_id": "inv-test", "deliver_via": "whatsapp", "requested_by": PHONE,
            "title": "Invoice DIG-INV-00001 for BuildCo (Test) — R163875.00",
            "created_at": (NOW - timedelta(days=101)).isoformat()}
    noon = {"id": "a-noon", "tenant_id": "digg-demo", "status": "pending", "entity_type": "inbound_invoice",
            "entity_id": "f048", "title": "Supplier match: is *STE SCAFFOLDING SA* who sent invoice for R2,397.17?",
            "meta": {"candidate_supplier_name": "STE SCAFFOLDING SA", "filed_document_id": "doc-ste"},
            "created_at": (NOW - timedelta(hours=2, minutes=26)).isoformat()}
    later = dict(noon, id="a-later", entity_id="a781", created_at=(NOW - timedelta(minutes=25)).isoformat())
    steps = [  # ids sort the June step first, as the real UUIDs did
        {"id": "ffff", "approval_id": "a-june", "approver_phone": PHONE, "status": "pending"},
        {"id": "8ab1", "approval_id": "a-noon", "approver_phone": PHONE, "status": "pending"},
        {"id": "7a1c", "approval_id": "a-later", "approver_phone": PHONE, "status": "pending"},
    ]
    return {"vula_approvals": [june, noon, later], "vula_approval_steps": steps,
            "commerce_invoices": [], "commerce_suppliers": [],
            "vula_filed_documents": [{"id": "doc-ste", "tenant_id": "digg-demo", "status": "pending_project",
                                      "filename": "Tax Invoice STE00866.PDF", "fields": {}}]}


@pytest.fixture()
def env(monkeypatch):
    db = _db()
    client = type("C", (), {"table": lambda self, t: _Q(db, t)})()
    monkeypatch.setattr(approvals, "_client", lambda: client)
    sent = []

    async def fake_send(phone, text, tenant_id=None, **_k):
        sent.append(text)
    monkeypatch.setattr("vula.api.whatsapp._send_reply", fake_send)
    deliver = AsyncMock(return_value=False)
    monkeypatch.setattr(approvals, "_deliver_invoice", deliver)
    asked = AsyncMock(return_value=1)
    monkeypatch.setattr("vula.integrations.doc_filing.ask_project", asked)
    monkeypatch.setattr(approvals, "_apply_supplier_match", AsyncMock())
    return db, sent, deliver, asked


@pytest.mark.asyncio
async def test_approve_answers_the_newest_question_not_a_stale_one(env):
    db, sent, deliver, asked = env
    out = await approvals.record_decision(PHONE, "approved")
    assert out["id"] == "a-later"
    june = next(a for a in db["vula_approvals"] if a["id"] == "a-june")
    assert june["status"] == "pending"            # the June test invoice is untouched
    deliver.assert_not_called()
    assert sent[-1] == "✅ Linked to *STE SCAFFOLDING SA*."
    # …and straight on to "which project?" for that same document
    asked.assert_awaited_once()
    assert asked.await_args.args[1]["id"] == "doc-ste" and asked.await_args.args[2] == [PHONE]


@pytest.mark.asyncio
async def test_reject_on_a_supplier_question_still_asks_the_project(env):
    _db, sent, _deliver, asked = env
    await approvals.record_decision(PHONE, "rejected")
    assert "keep it under the supplier name" in sent[-1]
    asked.assert_awaited_once()


@pytest.mark.asyncio
async def test_only_stale_approvals_left_means_not_an_approval_reply(env):
    db, _sent, _deliver, _asked = env
    db["vula_approvals"] = [a for a in db["vula_approvals"] if a["id"] == "a-june"]
    assert await approvals.record_decision(PHONE, "approved") is None


@pytest.mark.asyncio
async def test_a_failed_invoice_send_is_reported_not_claimed(env):
    db, sent, deliver, asked = env
    june = next(a for a in db["vula_approvals"] if a["id"] == "a-june")
    june["created_at"] = NOW.isoformat()          # a fresh invoice approval
    db["vula_approvals"] = [june]
    await approvals.record_decision(PHONE, "approved")
    deliver.assert_awaited_once()
    assert "couldn't send it" in sent[-1] and "Delivering now" not in sent[-1]


@pytest.mark.asyncio
async def test_delivery_uses_the_port_the_server_listens_on(monkeypatch):
    posted = []

    class _Resp:
        is_success = True
        status_code = 200

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **_k):
            posted.append(url)
            return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setenv("PORT", "8080")
    ok = await approvals._deliver_invoice({"tenant_id": "digg-demo", "entity_id": "inv1",
                                           "deliver_via": "whatsapp"})
    assert ok and posted == ["http://localhost:8080/v1/commerce/digg-demo/admin/invoices/inv1/send-whatsapp"]
