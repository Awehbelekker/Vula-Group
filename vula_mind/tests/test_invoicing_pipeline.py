"""Quote → invoice → paid, corrected only by credit notes (finance brief, capability 2).

- A sent invoice is never edited or deleted: amounts are locked, corrections are credit notes.
- A credit reduces what is still owed first (no ledger entry — the cash-basis ledger never
  booked the unpaid part); any part already paid needs the refund confirmed, and is posted as a
  refund (sales + VAT debited, bank credited).
- An invoice part-paid or part-credited, then cleared by the gateway, books only the remainder.
- Recurring invoices are drafted and put to the owner; they never go to the customer by themselves.
- A VAT-registered tenant's PDF is a "Tax Invoice" with VAT number, rate and amount.
"""
from unittest.mock import AsyncMock, patch

import pytest

from vula.commerce import ledger, service

TID = "off-the-hook"
INV = {"id": "inv-1", "tenant_id": TID, "doc_type": "invoice", "direction": "outbound",
       "status": "sent", "invoice_number": "OTH-INV-00012", "customer_name": "Thabo",
       "customer_phone": "27821234567", "total_cents": 115000, "vat_cents": 15000,
       "total_paid_cents": 0, "vat_rate": 15}


class _DB:
    def __init__(self):
        self.inserts, self.updates = [], []

    def table(self, t):
        self.t = t
        return self

    def insert(self, row):
        self.inserts.append((self.t, row))
        return self

    def update(self, patch):
        self.updates.append((self.t, patch))
        return self

    def eq(self, *_a):
        return self

    def execute(self):
        return type("R", (), {"data": [{}]})()


@pytest.fixture
def env(monkeypatch):
    db = _DB()
    monkeypatch.setattr(service, "_client", lambda: db)
    made = {}

    async def create(tid, data):
        made.update(data)
        return {"id": "cn-1", "invoice_number": "OTH-CN-00003", **data}
    monkeypatch.setattr(service, "create_invoice", create)
    posted = []
    monkeypatch.setattr(ledger, "_post", lambda tid, **kw: posted.append(kw))
    return db, made, posted


def _src(monkeypatch, **over):
    monkeypatch.setattr(service, "get_invoice", AsyncMock(return_value={**INV, **over}))


@pytest.mark.asyncio
async def test_a_partial_credit_on_an_unpaid_invoice_reduces_what_is_owed(env, monkeypatch):
    db, made, posted = env
    _src(monkeypatch)
    res = await service.credit_invoice(TID, "inv-1", 20000, "Two crates short")
    assert made["doc_type"] == "credit_note" and made["prices_include_vat"] is True
    assert made["line_items"][0]["unit_price_cents"] == 20000
    [(t, pay)] = db.inserts
    assert t == "commerce_invoice_payments" and pay["payment_method"] == "credit_note"
    assert pay["amount_cents"] == 20000
    [patch_] = [p for t, p in db.updates if t == "commerce_invoices" and "total_paid_cents" in p]
    assert patch_["total_paid_cents"] == 20000 and "status" not in patch_     # still owed R950
    assert posted == []                                   # nothing was ever booked for it
    assert res["refund_cents"] == 0


@pytest.mark.asyncio
async def test_a_full_credit_on_an_unpaid_invoice_cancels_it(env, monkeypatch):
    db, made, posted = env
    _src(monkeypatch)
    await service.credit_invoice(TID, "inv-1")
    patch_ = [p for t, p in db.updates if "total_paid_cents" in p][0]
    assert patch_["status"] == "cancelled" and "OTH-CN-00003" in patch_["cancel_reason"]


@pytest.mark.asyncio
async def test_crediting_a_paid_invoice_needs_the_refund_confirmed(env, monkeypatch):
    db, made, posted = env
    _src(monkeypatch, status="paid", total_paid_cents=115000)
    with pytest.raises(service.RefundNeeded):
        await service.credit_invoice(TID, "inv-1", 23000)
    assert made == {} and posted == []
    await service.credit_invoice(TID, "inv-1", 23000, refund_made=True)
    [entry] = posted
    assert entry["source_type"] == "invoice_refund" and entry["source_id"] == "cn-1"
    lines = {l["account_code"]: l for l in entry["lines"]}
    assert lines["bank_cash"]["credit_cents"] == 23000
    assert lines["vat_output"]["debit_cents"] == 3000          # 23000 × 15000/115000
    assert lines["sales"]["debit_cents"] == 20000
    assert sum(l["debit_cents"] for l in entry["lines"]) == sum(l["credit_cents"] for l in entry["lines"])


@pytest.mark.asyncio
async def test_drafts_and_supplier_bills_are_not_credited(env, monkeypatch):
    _src(monkeypatch, status="draft")
    with pytest.raises(ValueError):
        await service.credit_invoice(TID, "inv-1")
    _src(monkeypatch, direction="inbound")
    with pytest.raises(ValueError):
        await service.credit_invoice(TID, "inv-1")


def test_gateway_payment_after_a_part_payment_books_only_the_remainder(monkeypatch):
    posted = []
    monkeypatch.setattr(ledger, "_post", lambda tid, **kw: posted.append(kw))
    ledger.post_invoice_paid(TID, {**INV, "status": "paid", "total_paid_cents": 15000})
    lines = {l["account_code"]: l for l in posted[0]["lines"]}
    assert lines["bank_cash"]["debit_cents"] == 100000
    assert lines["vat_output"]["credit_cents"] == 13043         # 100000 × 15000/115000
    ledger.post_invoice_paid(TID, {**INV, "total_paid_cents": 115000})
    assert len(posted) == 1                                      # nothing left to book


# ── the WhatsApp tool ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_credit_tool_previews_and_flags_a_refund(monkeypatch):
    from core.skills.commerce_admin import CommerceAdminSkill
    skill = CommerceAdminSkill()
    monkeypatch.setattr(skill, "_find_invoice_by_number",
                        AsyncMock(return_value={**INV, "status": "paid", "total_paid_cents": 115000}))
    run = AsyncMock()
    monkeypatch.setattr(service, "credit_invoice", run)
    out = await skill._credit_note(TID, {"invoice_number": "OTH-INV-00012", "amount_rands": 230})
    assert out["preview"] and out["refund_needed"] == "R230.00" and "refunded" in out["message"]
    run.assert_not_awaited()


# ── sent invoices are locked ────────────────────────────────────────────────────

def _client_app():
    from fastapi.testclient import TestClient
    from vula.api.server import app
    return TestClient(app)


def test_a_sent_invoice_cannot_be_edited_or_deleted(monkeypatch):
    from vula.api import commerce
    monkeypatch.setattr(commerce.service, "get_invoice", AsyncMock(return_value=INV))
    db = _DB()
    monkeypatch.setattr(commerce.service, "_client", lambda: db)
    c = _client_app()
    r = c.patch(f"/v1/commerce/{TID}/admin/invoices/inv-1", json={"total_cents": 99})
    assert r.status_code == 409 and "credit note" in r.json()["detail"]
    r = c.delete(f"/v1/commerce/{TID}/admin/invoices/inv-1")
    assert r.status_code == 409
    assert db.updates == []                                       # nothing written


# ── recurring invoices go to the owner first ────────────────────────────────────

@pytest.mark.asyncio
async def test_a_recurring_invoice_is_proposed_not_sent(monkeypatch):
    asked = []

    async def ask(phone, tenant_id, tool, args, preview, caller_role=None):
        asked.append((phone, tool, args))
    monkeypatch.setattr("vula.api.whatsapp._ask_admin_confirm", ask)
    monkeypatch.setattr("vula.commerce.approvals.tenant_admin_approvers",
                        AsyncMock(return_value=[{"phone": "27645755210"}]))
    with patch("vula.api.whatsapp._send_reply", AsyncMock()) as send:
        await service._propose_recurring_send(TID, {**INV, "status": "draft"}, {"label": "Monthly retainer"})
    send.assert_not_awaited()
    assert asked == [("27645755210", "send_invoice", {"invoice_number": "OTH-INV-00012"})]


# ── SARS tax invoice fields ─────────────────────────────────────────────────────

def _pdf_text(invoice, settings):
    import fitz
    from vula.commerce import pdf
    data = pdf.render_invoice_pdf(invoice, pdf.merge_branding(TID, settings))
    return "\n".join(p.get_text() for p in fitz.open(stream=data, filetype="pdf"))


def test_a_vat_registered_tenants_invoice_is_a_tax_invoice():
    inv = {**INV, "subtotal_cents": 100000, "issue_date": "2026-10-06",
           "line_items": [{"description": "Hake", "quantity": 1, "unit_price_cents": 100000,
                           "total_cents": 100000}]}
    text = _pdf_text(inv, {"company_name": "Off the Hook", "vat_number": "4123456789",
                           "vat_registered": True})
    assert "tax invoice" in text.lower() and "VAT No: 4123456789" in text
    assert "150.00" in text and "15%" in text.replace(" %", "%")


def test_a_non_vat_registered_tenant_issues_a_plain_invoice():
    inv = {**INV, "vat_cents": 0, "vat_rate": 0, "subtotal_cents": 115000, "issue_date": "2026-10-06",
           "line_items": [{"description": "Hake", "quantity": 1, "unit_price_cents": 115000,
                           "total_cents": 115000}]}
    text = _pdf_text(inv, {"company_name": "Off the Hook", "vat_registered": False})
    assert "tax invoice" not in text.lower() and "invoice" in text.lower()
