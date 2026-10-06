"""Receipts that wait for the owner's tap (finance brief, capability 4; migration 198).

expense_mode 'book' (default) keeps today's flow: a receipt sent on WhatsApp is booked and posted
at once. 'confirm' reads and stages it as 'unconfirmed' — not in the ledger — and sends Confirm /
Cancel; only the tap books it (posted once, with any project/paid-with answers given meanwhile).
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.commerce import expenses, ledger

TID, PHONE = "digg-demo", "27645755210"


class _DB:
    def __init__(self, rows=None, update_hits=True):
        self.inserted, self.rows, self.hits, self._ins = [], rows or [], update_hits, None

    def table(self, _t):
        return self

    def insert(self, row):
        self.inserted.append(row)
        self._ins = row
        return self

    def __getattr__(self, _n):
        return lambda *a, **k: self

    def execute(self):
        if self._ins is not None:
            row, self._ins = self._ins, None
            return type("R", (), {"data": [row]})()
        return type("R", (), {"data": self.rows if self.hits else []})()


@pytest.mark.asyncio
@pytest.mark.parametrize("confirmed,status,posts", [(True, "submitted", 1), (False, "unconfirmed", 0)])
async def test_an_unconfirmed_claim_stays_out_of_the_ledger(monkeypatch, confirmed, status, posts):
    db = _DB()
    monkeypatch.setattr("vula.commerce.service._client", lambda: db)
    monkeypatch.setattr(expenses, "known_projects", lambda t: [])
    post = MagicMock()
    monkeypatch.setattr(ledger, "post_expense", post)
    out = await expenses.create_claim(TID, amount_cents=45000, description="Cement",
                                      supplier="Builders", confirmed=confirmed, dedupe=False)
    assert out["status"] == status and post.call_count == posts


def test_confirm_books_once(monkeypatch):
    row = {"id": "e1", "status": "submitted", "amount_cents": 45000}
    monkeypatch.setattr(expenses, "_client", lambda: _DB(rows=[row]))
    post = MagicMock()
    monkeypatch.setattr(ledger, "post_expense", post)
    assert expenses.confirm_claim(TID, "e1")["status"] == "submitted"
    assert post.call_count == 1
    monkeypatch.setattr(expenses, "_client", lambda: _DB(rows=[row], update_hits=False))
    assert expenses.confirm_claim(TID, "e1") is None                 # second tap: nothing
    assert post.call_count == 1


def test_mode_defaults_to_book(monkeypatch):
    monkeypatch.setattr(expenses, "_client", lambda: (_ for _ in ()).throw(RuntimeError("db")))
    assert expenses.expense_mode(TID) == "book"


@pytest.mark.asyncio
async def test_a_receipt_in_confirm_mode_asks_before_booking(monkeypatch):
    from vula.api import whatsapp as wa
    create = AsyncMock(return_value={"id": "e1", "amount_cents": 45000, "category": "materials",
                                     "project": None, "needs_project": False,
                                     "purpose_category": "other"})
    ask = AsyncMock()
    with patch("vula.commerce.expenses.expense_mode", return_value="confirm"), \
            patch("vula.commerce.expenses.create_claim", new=create), \
            patch("vula.commerce.expenses.resolve_paid_with", return_value="company_card"), \
            patch("vula.commerce.expenses.match_project", return_value=None), \
            patch("vula.commerce.expenses.list_cards", return_value=[]), \
            patch("vula.commerce.expenses.classify_purpose_category", new=AsyncMock(return_value="other")), \
            patch("vula.commerce.expenses.set_purpose_category"), \
            patch("vula.models.tenants.get_tenant_db", side_effect=Exception("n/a")), \
            patch("vula.models.field_ops.get_field_ops_db", side_effect=Exception("n/a")), \
            patch.object(wa, "_ask_admin_confirm", new=ask):
        msg = await wa._log_expense_claim(TID, PHONE, {"total_cents": 45000, "supplier": "Builders"})
    assert create.await_args.kwargs["confirmed"] is False
    assert "Not booked yet" in msg and "Logged as an expense" not in msg
    assert ask.await_args.args[2] == "confirm_expense"
    assert ask.await_args.args[3] == {"expense_id": "e1"}


@pytest.mark.asyncio
async def test_the_confirm_tool_previews_then_books(monkeypatch):
    from core.skills.commerce_admin import CommerceAdminSkill
    from vula.commerce import service
    row = {"id": "e1", "amount_cents": 45000, "supplier": "Builders", "status": "unconfirmed",
           "project": "HPC Bokaap"}
    monkeypatch.setattr(service, "_client", lambda: _DB(rows=[row]))
    booked = MagicMock(return_value={**row, "status": "submitted"})
    monkeypatch.setattr(expenses, "confirm_claim", booked)
    skill = CommerceAdminSkill()
    out = await skill._confirm_expense(TID, {"expense_id": "e1"})
    assert out["preview"] and out["expense"] == "R450.00"
    booked.assert_not_called()
    out = await skill._confirm_expense(TID, {"expense_id": "e1", "confirm": True})
    assert out["verified"] and out["booked"] == "R450.00"
