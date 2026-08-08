"""Tests for the commerce_admin system-prompt guardrails and the create_invoice price gate,
added 2026-08-08 after a real-transcript review of this week's DIGG + OTH WhatsApp chats found:
an off-topic non-answer to a how-to question, a leaked internal tool name, silent R0.00 draft
invoices, and a hallucinated "exported to Xero" claim. See core/skills/commerce_admin.py's
_GUARDRAILS constant and _create_invoice's price-completeness gate.
"""
from unittest.mock import MagicMock, patch

import pytest

from core.skills.commerce_admin import CommerceAdminSkill

TID = "test-tenant"


@pytest.fixture
def skill():
    return CommerceAdminSkill()


# ── system-prompt guardrails (owner/staff + sales_rep) ──────────────────────────

@pytest.mark.parametrize("role", [None, "owner", "staff", "sales_rep"])
def test_system_prompt_has_procedural_question_guardrail(skill, role):
    prompt = skill._system_prompt(TID, role=role, name="Test")
    assert "how-to/procedural question" in prompt


@pytest.mark.parametrize("role", [None, "sales_rep"])
def test_system_prompt_has_no_tool_name_leak_guardrail(skill, role):
    prompt = skill._system_prompt(TID, role=role, name="Test")
    assert "internal tool/function names" in prompt


@pytest.mark.parametrize("role", [None, "sales_rep"])
def test_system_prompt_has_no_fabricated_success_guardrail(skill, role):
    prompt = skill._system_prompt(TID, role=role, name="Test")
    assert "unless a tool call" in prompt


@pytest.mark.parametrize("role", [None, "sales_rep"])
def test_system_prompt_has_need_info_guardrail(skill, role):
    prompt = skill._system_prompt(TID, role=role, name="Test")
    assert "need_info" in prompt


# ── create_invoice price-completeness gate ──────────────────────────────────────

@pytest.mark.asyncio
async def test_create_invoice_need_info_when_price_missing(skill):
    args = {"line_items": [{"description": "Hake fillets", "quantity": 3}],
            "customer_name": "Paola"}
    res = await skill._create_invoice(TID, args)
    assert res["status"] == "need_info"
    assert "the price for Hake fillets" in res["missing"]


@pytest.mark.asyncio
async def test_create_invoice_need_info_lists_only_items_missing_a_price(skill):
    args = {"line_items": [
        {"description": "Hake fillets", "quantity": 3, "unit_price_rands": 90},
        {"description": "Calamari", "quantity": 2},
    ], "customer_name": "Paola"}
    res = await skill._create_invoice(TID, args)
    assert res["status"] == "need_info"
    assert res["missing"] == ["the price for Calamari"]


@pytest.mark.asyncio
async def test_create_invoice_allows_explicit_zero_price(skill, monkeypatch):
    import core.skills.commerce_admin as ca

    async def create_invoice(tid, data):
        assert data["line_items"][0]["unit_price_cents"] == 0
        return {"id": "i1", "invoice_number": "INV-001", "total_cents": 0, "status": "draft"}

    async def get_invoice(tid, invoice_id):
        return {"id": "i1", "invoice_number": "INV-001", "status": "draft"}

    monkeypatch.setattr(ca.service, "create_invoice", create_invoice)
    monkeypatch.setattr(ca.service, "get_invoice", get_invoice)

    args = {"line_items": [{"description": "Free sample", "quantity": 1, "unit_price_rands": 0}],
            "customer_name": "Paola"}
    res = await skill._create_invoice(TID, args)
    assert res.get("status") != "need_info"
    assert res["invoice_number"] == "INV-001"


@pytest.mark.asyncio
async def test_create_invoice_proceeds_normally_when_all_prices_given(skill, monkeypatch):
    import core.skills.commerce_admin as ca

    async def create_invoice(tid, data):
        return {"id": "i1", "invoice_number": "INV-002", "total_cents": 27000, "status": "draft"}

    async def get_invoice(tid, invoice_id):
        return {"id": "i1", "invoice_number": "INV-002", "status": "draft"}

    monkeypatch.setattr(ca.service, "create_invoice", create_invoice)
    monkeypatch.setattr(ca.service, "get_invoice", get_invoice)

    args = {"line_items": [{"description": "Hake fillets", "quantity": 3, "unit_price_rands": 90}],
            "customer_name": "Paola"}
    res = await skill._create_invoice(TID, args)
    assert res.get("status") != "need_info"
    assert res["invoice_number"] == "INV-002"


# ── reimbursement_balance ────────────────────────────────────────────────────────

def _mock_reimbursable_rows(rows):
    """Builds a chainable mock matching commerce_expenses' select().eq()...().execute() shape,
    accepting either the paid_by(phone) or paid_by_name(ilike) branch of the query."""
    m = MagicMock()
    chain = m.table.return_value.select.return_value.eq.return_value.eq.return_value.is_.return_value
    chain.eq.return_value.execute.return_value = MagicMock(data=rows)
    chain.ilike.return_value.execute.return_value = MagicMock(data=rows)
    return m


@pytest.mark.asyncio
async def test_reimbursement_balance_sums_outstanding_items(skill):
    import core.skills.commerce_admin as ca

    rows = [
        {"date": "2026-08-05", "description": "Hardware", "amount_cents": 852348,
         "supplier": "Bauxite Extrusions", "project": "HPC_Bokaap", "paid_by": None,
         "paid_by_name": "Nelethu"},
        {"date": "2026-07-24", "description": "Extrusions", "amount_cents": 200000,
         "supplier": "Bauxite Extrusions", "project": "HPC_Bokaap", "paid_by": None,
         "paid_by_name": "Nelethu"},
    ]
    with patch.object(ca, "service") as mock_service:
        mock_service._client.return_value = _mock_reimbursable_rows(rows)
        res = await skill._reimbursement_balance(TID, "Nelethu")

    assert res["owed"] == "R10,523.48"
    assert res["item_count"] == 2


@pytest.mark.asyncio
async def test_reimbursement_balance_no_outstanding_items(skill):
    import core.skills.commerce_admin as ca

    with patch.object(ca, "service") as mock_service:
        mock_service._client.return_value = _mock_reimbursable_rows([])
        res = await skill._reimbursement_balance(TID, "Josaphin")

    assert res["owed"] == "R0.00"
    assert res["items"] == []


@pytest.mark.asyncio
async def test_reimbursement_balance_requires_payee(skill):
    res = await skill._reimbursement_balance(TID, "")
    assert "error" in res
