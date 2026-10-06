"""Debtors and WhatsApp reminders (finance brief, capability 3).

- "Who owes me what?" grouped by customer, owed after part-payments, aged — server-computed.
- Reminders follow the tenant's reminder_mode (migration 198): 'propose' (default) asks the owner
  first; nothing reaches a customer before the Confirm tap. Tone and the pay-page link ride along.
- Outside the 24-hour window the approved reminder template is used, or the failure is logged.
- The weekday digest's figures are DB sums; a customer's typed "I paid" tells the owner and
  never marks anything paid.
"""
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest

import vula.api.commerce as commerce
from tests.test_overdue_reminders import _FakeClient, _inv
from vula.commerce import service

TID = "off-the-hook"


@pytest.fixture(autouse=True)
def _now(monkeypatch):
    monkeypatch.setattr(service, "_now", lambda: "2026-10-06T05:00:00Z")


# ── who owes me ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_who_owes_me_groups_by_customer_after_part_payments(monkeypatch):
    from core.skills.commerce_admin import CommerceAdminSkill
    due = lambda n: (date.today() - timedelta(days=n)).isoformat()  # noqa: E731
    rows = {"sent": [{"invoice_number": "OTH-INV-1", "customer_name": "Thabo", "status": "sent",
                      "total_cents": 150000, "total_paid_cents": 0, "due_date": due(3)}],
            "overdue": [{"invoice_number": "OTH-INV-2", "customer_name": "Thabo", "status": "overdue",
                         "total_cents": 50000, "total_paid_cents": 0, "due_date": due(20)}],
            "part_paid": [{"invoice_number": "OTH-INV-3", "customer_name": "Lerato",
                           "status": "part_paid", "total_cents": 100000, "total_paid_cents": 60000,
                           "due_date": due(1)}]}

    async def list_invoices(tid, doc_type=None, status=None, direction=None, limit=100):
        assert doc_type == "invoice" and direction == "outbound"
        return rows.get(status, [])
    monkeypatch.setattr(service, "list_invoices", list_invoices)
    out = await CommerceAdminSkill()._outstanding_invoices(TID)
    assert out["outstanding_total"] == "R2,400.00"
    assert out["by_customer"][0] == {"customer": "Thabo", "owes": "R2,000.00", "invoices": 2,
                                     "oldest_days_overdue": 20}
    assert out["by_customer"][1]["owes"] == "R400.00"          # R1,000 less R600 paid


# ── reminder modes ─────────────────────────────────────────────────────────────

@pytest.fixture
def sends(monkeypatch):
    calls = []

    async def fake(phone, message, tenant_id="", idem_key=None):
        calls.append((phone, message))
        return True
    monkeypatch.setattr("vula.api.whatsapp._send_reply", fake)
    monkeypatch.setattr(commerce, "_suppressed_phones", lambda t: set())
    return calls


@pytest.mark.asyncio
async def test_propose_mode_asks_the_owner_and_messages_no_customer(monkeypatch, sends):
    store = [_inv("i1", days_over=8), _inv("i2", days_over=1, phone="27829999999")]
    monkeypatch.setattr(service, "_client", lambda: _FakeClient(store))
    monkeypatch.setattr(commerce, "_reminder_settings", lambda t: ("propose", "friendly"))
    asked = []

    async def ask(phone, tenant_id, tool, args, preview, caller_role=None):
        asked.append((phone, tool, args, preview))
    monkeypatch.setattr("vula.api.whatsapp._ask_admin_confirm", ask)
    monkeypatch.setattr("vula.commerce.approvals.tenant_admin_approvers",
                        AsyncMock(return_value=[{"phone": "27645755210"}]))
    assert await commerce._process_overdue_invoices(TID) == 0
    assert sends == []                                           # no customer messaged
    [(owner, tool, args, preview)] = asked
    assert owner == "27645755210" and tool == "send_payment_reminders"
    assert set(args["invoice_ids"]) == {"i1", "i2"} and preview["reminders"] == 2
    assert all(r["reminder_stage"] is None for r in store)        # not claimed until confirmed
    assert {r["reminder_proposed_stage"] for r in store} == {"firm", "due"}
    asked.clear()
    await commerce._process_overdue_invoices(TID)                # next morning: not re-proposed
    assert asked == []


@pytest.mark.asyncio
async def test_confirm_sends_with_the_tone_and_the_pay_page(monkeypatch, sends):
    store = [_inv("i1", days_over=8)]
    monkeypatch.setattr(service, "_client", lambda: _FakeClient(store))
    monkeypatch.setattr(commerce, "_reminder_settings", lambda t: ("propose", "firm"))
    assert await commerce._process_overdue_invoices(TID, only_ids=["i1"], force_send=True) == 1
    msg = sends[0][1]
    assert "Please pay immediately." in msg and f"/v1/commerce/{TID}/pay/i1" in msg
    assert store[0]["reminder_stage"] == "firm"


@pytest.mark.asyncio
async def test_off_mode_never_messages_customers_but_still_escalates(monkeypatch, sends):
    store = [_inv("i1", days_over=20, status="overdue", reminder_stage="firm")]
    monkeypatch.setattr(service, "_client", lambda: _FakeClient(store))
    monkeypatch.setattr(commerce, "_reminder_settings", lambda t: ("off", "friendly"))
    alerts = []
    monkeypatch.setattr("vula.integrations.notify.notify_team",
                        AsyncMock(side_effect=lambda *a, **k: alerts.append(a) or 1))
    assert await commerce._process_overdue_invoices(TID) == 0
    assert sends == [] and alerts and store[0]["reminder_stage"] == "escalated"


def test_unreadable_settings_mean_propose(monkeypatch):
    monkeypatch.setattr(service, "_client", lambda: (_ for _ in ()).throw(RuntimeError("db")))
    assert commerce._reminder_settings(TID) == ("propose", "friendly")


def test_reminder_amount_is_what_is_still_owed():
    iv = dict(_inv("i1", 8), total_paid_cents=5000)
    assert "R100.00" in commerce._reminder_message("firm", iv, 8)          # R150 − R50


@pytest.mark.asyncio
async def test_outside_the_window_the_template_is_used_or_the_failure_logged(monkeypatch, caplog):
    from config import settings
    monkeypatch.setattr("vula.api.whatsapp._send_reply", AsyncMock(return_value=False))
    tpl = AsyncMock(return_value=True)
    monkeypatch.setattr("vula.api.whatsapp.send_template", tpl)
    iv = _inv("i1", 8)
    monkeypatch.setattr(settings, "whatsapp_reminder_template", "payment_reminder")
    assert await commerce._send_reminder(TID, "27821234567", iv, "firm", 8, "friendly")
    assert tpl.await_args.args[3][:3] == ["Thabo", "OTH-i1", "R150.00"]
    monkeypatch.setattr(settings, "whatsapp_reminder_template", "")
    assert not await commerce._send_reminder(TID, "27821234567", iv, "firm", 8, "friendly")
    assert "WHATSAPP_REMINDER_TEMPLATE" in caplog.text


@pytest.mark.asyncio
async def test_the_reminder_tool_previews_then_sends(monkeypatch):
    from core.skills.commerce_admin import CommerceAdminSkill
    run = AsyncMock(return_value=2)
    monkeypatch.setattr(commerce, "_process_overdue_invoices", run)
    skill = CommerceAdminSkill()
    out = await skill._send_payment_reminders(TID, {})
    assert out["preview"] and run.await_count == 0
    out = await skill._send_payment_reminders(TID, {"invoice_ids": ["i1"], "confirm": True})
    assert out["reminders_sent"] == 2
    assert run.await_args.kwargs == {"only_ids": ["i1"], "force_send": True}


# ── morning digest ──────────────────────────────────────────────────────────────

def test_digest_figures_are_sums_from_the_rows():
    from vula import owner_digest
    d = {"paid": [{"customer": "Sam", "invoice": "INV-1", "cents": 120000},
                  {"customer": "Thabo", "invoice": None, "cents": 5050}],
         "overdue": [("Lerato", 40000), ("Thabo", 15000)], "upcoming_cents": 99900,
         "upcoming_n": 2}
    text = owner_digest.render(d)
    assert "Paid yesterday:* R1,250.50" in text and "R550.00 from 2 customers" in text
    assert "R999.00 (2 invoices)" in text
    assert owner_digest.render({"paid": [], "overdue": [], "upcoming_cents": 0, "upcoming_n": 0}) is None


@pytest.mark.asyncio
async def test_digest_is_off_until_enabled(monkeypatch):
    from config import settings
    from vula import owner_digest
    monkeypatch.setattr(settings, "owner_digest_enabled", False)
    assert (await owner_digest.send_all())["sent"] == 0


def test_digest_runs_weekday_mornings_only():
    from datetime import datetime
    from vula import owner_digest
    assert owner_digest.due(datetime(2026, 10, 6, 7, 30, tzinfo=owner_digest.SAST))        # Tue
    assert not owner_digest.due(datetime(2026, 10, 10, 7, 30, tzinfo=owner_digest.SAST))   # Sat
    assert not owner_digest.due(datetime(2026, 10, 6, 10, 0, tzinfo=owner_digest.SAST))


# ── "I paid" ────────────────────────────────────────────────────────────────────

class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def table(self, _t):
        return self

    def __getattr__(self, _n):
        return lambda *a, **k: self

    def execute(self):
        return type("R", (), {"data": self.rows})()


@pytest.mark.asyncio
async def test_i_paid_tells_the_owner_and_marks_nothing(monkeypatch):
    from vula.api import whatsapp as wa
    rows = [{"id": "i1", "invoice_number": "OTH-INV-9", "customer_name": "Sam Botha",
             "total_cents": 120000, "total_paid_cents": 0}]
    monkeypatch.setattr(service, "_client", lambda: _Rows(rows))
    monkeypatch.setattr("vula.commerce.approvals.tenant_admin_approvers",
                        AsyncMock(return_value=[{"phone": "27645755210"}]))
    upd = AsyncMock()
    monkeypatch.setattr(service, "update_invoice_status", upd)
    with patch.object(wa, "_send_reply", AsyncMock(return_value=True)) as send:
        assert await wa._maybe_customer_says_paid("0821112222", "I've paid", TID)
    owner, customer = send.await_args_list
    assert owner.args[0] == "27645755210" and "OTH-INV-9 — R1,200.00" in owner.args[1]
    assert "record payment OTH-INV-9" in owner.args[1]
    assert customer.args[0] == "0821112222" and "proof of payment" in customer.args[1]
    upd.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["Can I pay by card?", "I paid, when will it be delivered",
                                  "I will pay tomorrow"])
async def test_other_messages_go_to_the_assistant(monkeypatch, text):
    from vula.api import whatsapp as wa
    monkeypatch.setattr(service, "_client", lambda: _Rows([{"total_cents": 1, "total_paid_cents": 0}]))
    assert not await wa._maybe_customer_says_paid("0821112222", text, TID)


@pytest.mark.asyncio
async def test_i_paid_with_no_open_invoice_goes_to_the_assistant(monkeypatch):
    from vula.api import whatsapp as wa
    monkeypatch.setattr(service, "_client", lambda: _Rows([]))
    assert not await wa._maybe_customer_says_paid("0821112222", "I paid", TID)
