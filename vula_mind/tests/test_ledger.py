"""Tests for the general ledger posting layer (migration 121).

vula/commerce/ledger.py builds debit/credit lines and calls the post_journal_entry RPC — the
RPC itself (balance check, idempotency) lives in Postgres and isn't exercised here; these tests
prove the Python side builds correct, balanced lines per event type, never raises even when the
RPC call fails, and that trial_balance() aggregates journal_lines correctly.
"""
import uuid

import pytest

import vula.commerce.ledger as ledger


class _FakeRpcCall:
    def __init__(self, name, params, sink, raise_on_execute=False):
        self.name = name
        self.params = params
        self.sink = sink
        self.raise_on_execute = raise_on_execute

    def execute(self):
        if self.raise_on_execute:
            raise RuntimeError("rpc boom")
        self.sink.append((self.name, self.params))
        return _Result(None)


class _Result:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table):
        self.table = table
        self.filters = []
        self._in_filter = None
        self._gte = None
        self._lte = None

    def select(self, *_a, **_kw):
        return self

    def eq(self, key, val):
        self.filters.append((key, val))
        return self

    def gte(self, key, val):
        self._gte = (key, val)
        return self

    def lte(self, key, val):
        self._lte = (key, val)
        return self

    def in_(self, key, values):
        self._in_filter = (key, set(values))
        return self

    def _matches(self, row):
        if not all(row.get(k) == v for k, v in self.filters):
            return False
        if self._in_filter:
            key, values = self._in_filter
            if row.get(key) not in values:
                return False
        if self._gte and row.get(self._gte[0]) < self._gte[1]:
            return False
        if self._lte and row.get(self._lte[0]) > self._lte[1]:
            return False
        return True

    def execute(self):
        rows = [r for r in self.table.rows if self._matches(r)]
        return _Result(rows)


class _FakeTable:
    def __init__(self, name, store):
        self.name = name
        self.rows = store.setdefault(name, [])


class _FakeClient:
    def __init__(self, raise_on_rpc=False):
        self.store = {}
        self.rpc_calls = []
        self.raise_on_rpc = raise_on_rpc

    def table(self, name):
        return _FakeQuery(_FakeTable(name, self.store))

    def rpc(self, name, params):
        return _FakeRpcCall(name, params, self.rpc_calls, raise_on_execute=self.raise_on_rpc)


@pytest.fixture
def fake_client(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(ledger, "_client", lambda: client)
    return client


TID = "test-tenant"


def _lines_for(rpc_calls):
    assert len(rpc_calls) == 1
    return rpc_calls[0]


# ── post_order_paid ─────────────────────────────────────────────────────────────

def test_post_order_paid_builds_balanced_lines(fake_client):
    order = {"id": "order-1", "display_id": "OTH-0001", "total_cents": 15000, "updated_at": "2026-08-01T10:00:00Z"}
    ledger.post_order_paid(TID, order)

    name, params = _lines_for(fake_client.rpc_calls)
    assert name == "post_journal_entry"
    assert params["p_source_type"] == "order_paid"
    assert params["p_source_id"] == "order-1"
    lines = params["p_lines"]
    assert sum(l["debit_cents"] for l in lines) == sum(l["credit_cents"] for l in lines) == 15000
    codes = {l["account_code"] for l in lines}
    assert codes == {"bank_cash", "sales"}


def test_post_order_paid_skips_zero_total(fake_client):
    ledger.post_order_paid(TID, {"id": "order-1", "total_cents": 0})
    assert fake_client.rpc_calls == []


def test_post_order_refund_reverses_the_lines(fake_client):
    order = {"id": "order-1", "display_id": "OTH-0001", "total_cents": 15000, "updated_at": "2026-08-01"}
    ledger.post_order_refund(TID, order)
    name, params = _lines_for(fake_client.rpc_calls)
    assert params["p_source_type"] == "order_refund"
    lines = {l["account_code"]: l for l in params["p_lines"]}
    assert lines["sales"]["debit_cents"] == 15000
    assert lines["bank_cash"]["credit_cents"] == 15000


# ── post_invoice_paid ────────────────────────────────────────────────────────────

def test_post_invoice_paid_splits_vat(fake_client):
    invoice = {"id": "inv-1", "invoice_number": "OTH-0001", "total_cents": 11500,
               "vat_cents": 1500, "paid_at": "2026-08-01"}
    ledger.post_invoice_paid(TID, invoice)
    name, params = _lines_for(fake_client.rpc_calls)
    assert params["p_source_type"] == "invoice_paid"
    lines = {l["account_code"]: l for l in params["p_lines"]}
    assert lines["bank_cash"]["debit_cents"] == 11500
    assert lines["sales"]["credit_cents"] == 10000
    assert lines["vat_output"]["credit_cents"] == 1500
    total_debit = sum(l["debit_cents"] for l in params["p_lines"])
    total_credit = sum(l["credit_cents"] for l in params["p_lines"])
    assert total_debit == total_credit == 11500


def test_post_invoice_paid_no_vat_single_sales_line(fake_client):
    invoice = {"id": "inv-1", "total_cents": 5000, "vat_cents": 0}
    ledger.post_invoice_paid(TID, invoice)
    _, params = _lines_for(fake_client.rpc_calls)
    codes = {l["account_code"] for l in params["p_lines"]}
    assert codes == {"bank_cash", "sales"}


# ── post_invoice_payment (partial payments, migration 130) ─────────────────────────

def test_post_invoice_payment_splits_vat_proportionally(fake_client):
    # Invoice: R1150 total (R1000 + R150 VAT). A R575 instalment (exactly half) should carry
    # exactly half the VAT (R75), not the full invoice's VAT.
    invoice = {"id": "inv-1", "invoice_number": "OTH-0001", "total_cents": 11500, "vat_cents": 1500}
    payment = {"id": "pay-1", "amount_cents": 5750, "paid_at": "2026-08-10"}
    ledger.post_invoice_payment(TID, invoice, payment)
    name, params = _lines_for(fake_client.rpc_calls)
    assert params["p_source_type"] == "invoice_payment"
    assert params["p_source_id"] == "pay-1"  # the PAYMENT's id, not the invoice's
    lines = {l["account_code"]: l for l in params["p_lines"]}
    assert lines["bank_cash"]["debit_cents"] == 5750
    assert lines["vat_output"]["credit_cents"] == 750
    assert lines["sales"]["credit_cents"] == 5000
    total_debit = sum(l["debit_cents"] for l in params["p_lines"])
    total_credit = sum(l["credit_cents"] for l in params["p_lines"])
    assert total_debit == total_credit == 5750


def test_post_invoice_payment_no_vat_single_sales_line(fake_client):
    invoice = {"id": "inv-1", "total_cents": 5000, "vat_cents": 0}
    payment = {"id": "pay-1", "amount_cents": 2000}
    ledger.post_invoice_payment(TID, invoice, payment)
    _, params = _lines_for(fake_client.rpc_calls)
    codes = {l["account_code"] for l in params["p_lines"]}
    assert codes == {"bank_cash", "sales"}


def test_post_invoice_payment_two_instalments_use_distinct_source_ids(fake_client):
    # Each instalment must get its own idempotency key (the payment's id) — using the
    # invoice's id for both would collide under post_journal_entry's unique constraint and
    # silently drop the second payment as "already posted".
    invoice = {"id": "inv-1", "total_cents": 10000, "vat_cents": 0}
    ledger.post_invoice_payment(TID, invoice, {"id": "pay-1", "amount_cents": 4000})
    ledger.post_invoice_payment(TID, invoice, {"id": "pay-2", "amount_cents": 6000})
    assert len(fake_client.rpc_calls) == 2
    source_ids = {params["p_source_id"] for _, params in fake_client.rpc_calls}
    assert source_ids == {"pay-1", "pay-2"}


def test_post_invoice_payment_skips_zero_amount(fake_client):
    invoice = {"id": "inv-1", "total_cents": 5000, "vat_cents": 0}
    ledger.post_invoice_payment(TID, invoice, {"id": "pay-1", "amount_cents": 0})
    assert fake_client.rpc_calls == []


# ── post_expense ─────────────────────────────────────────────────────────────────

def test_post_expense_splits_vat_and_uses_account_code(fake_client):
    expense = {"id": "exp-1", "description": "Packaging", "amount_cents": 1150,
               "vat_cents": 150, "account_code": "packaging", "date": "2026-08-01"}
    ledger.post_expense(TID, expense)
    _, params = _lines_for(fake_client.rpc_calls)
    assert params["p_source_type"] == "expense"
    lines = {l["account_code"]: l for l in params["p_lines"]}
    assert lines["bank_cash"]["credit_cents"] == 1150
    assert lines["packaging"]["debit_cents"] == 1000
    assert lines["vat_input"]["debit_cents"] == 150


def test_post_expense_falls_back_to_other_expense_when_account_code_missing(fake_client):
    expense = {"id": "exp-1", "amount_cents": 500, "date": "2026-08-01"}
    ledger.post_expense(TID, expense)
    _, params = _lines_for(fake_client.rpc_calls)
    codes = {l["account_code"] for l in params["p_lines"]}
    assert "other_expense" in codes


def test_post_expense_skips_zero_amount(fake_client):
    ledger.post_expense(TID, {"id": "exp-1", "amount_cents": 0})
    assert fake_client.rpc_calls == []


# ── Never raises ─────────────────────────────────────────────────────────────────

def test_posting_never_raises_when_rpc_fails(monkeypatch):
    client = _FakeClient(raise_on_rpc=True)
    monkeypatch.setattr(ledger, "_client", lambda: client)
    # Must not raise — a ledger failure can never block the real business action.
    ledger.post_order_paid(TID, {"id": "order-1", "total_cents": 1000})


# ── trial_balance ──────────────────────────────────────────────────────────────

def _seed_ledger(client):
    acct_bank = str(uuid.uuid4())
    acct_sales = str(uuid.uuid4())
    client.store.setdefault("commerce_accounts", []).extend([
        {"id": acct_bank, "tenant_id": TID, "code": "bank_cash", "name": "Bank / cash", "type": "asset"},
        {"id": acct_sales, "tenant_id": TID, "code": "sales", "name": "Sales", "type": "income"},
    ])
    entry_id = str(uuid.uuid4())
    client.store.setdefault("journal_entries", []).append(
        {"id": entry_id, "tenant_id": TID, "entry_date": "2026-08-01"})
    client.store.setdefault("journal_lines", []).extend([
        {"journal_entry_id": entry_id, "account_id": acct_bank, "debit_cents": 15000, "credit_cents": 0},
        {"journal_entry_id": entry_id, "account_id": acct_sales, "debit_cents": 0, "credit_cents": 15000},
    ])


def test_trial_balance_aggregates_and_balances(fake_client):
    _seed_ledger(fake_client)
    result = ledger.trial_balance(TID)
    assert result["total_debit_cents"] == result["total_credit_cents"] == 15000
    codes = {a["code"] for a in result["accounts"]}
    assert codes == {"bank_cash", "sales"}


def test_trial_balance_empty_when_no_entries(fake_client):
    result = ledger.trial_balance(TID)
    assert result["accounts"] == []
    assert result["total_debit_cents"] == 0
