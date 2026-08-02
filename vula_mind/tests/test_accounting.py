"""Tests for the chart-of-accounts seeding change (migration 121): ensure_chart() went from
"seed only if the tenant has zero accounts" to "backfill any DEFAULT_CHART code the tenant
doesn't already have" — needed so the new balance-sheet accounts (bank_cash, vat_output, etc.)
reach tenants who already had an expense-only chart, not just brand-new tenants.
"""
import vula.commerce.accounting as accounting


class _Result:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table):
        self.table = table
        self.filters = []

    def select(self, *_a, **_kw):
        return self

    def eq(self, key, val):
        self.filters.append((key, val))
        return self

    def order(self, *_a, **_kw):
        return self

    def execute(self):
        rows = [r for r in self.table.rows if all(r.get(k) == v for k, v in self.filters)]
        return _Result(rows)

    def insert(self, rows):
        rows = [rows] if isinstance(rows, dict) else rows
        self.table.rows.extend(rows)
        return _ExecWrapper(rows)


class _ExecWrapper:
    def __init__(self, rows):
        self._rows = rows

    def execute(self):
        return _Result(self._rows)


class _FakeTable:
    def __init__(self, name, store):
        self.rows = store.setdefault(name, [])


class _FakeClient:
    def __init__(self):
        self.store = {}

    def table(self, name):
        return _FakeQuery(_FakeTable(name, self.store))


TID = "test-tenant"


def test_ensure_chart_seeds_full_default_for_new_tenant(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(accounting, "_client", lambda: client)
    rows = accounting.ensure_chart(TID)
    codes = {r["code"] for r in rows}
    assert "bank_cash" in codes
    assert "vat_output" in codes
    assert len(rows) == len(accounting.DEFAULT_CHART)


def test_ensure_chart_backfills_only_missing_codes_for_existing_tenant(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(accounting, "_client", lambda: client)
    # Simulate a tenant who already has the OLD chart (pre-migration-121), missing the 4 new
    # balance-sheet accounts.
    client.store["commerce_accounts"] = [
        {"tenant_id": TID, "code": c, "name": n, "type": t, "vat_treatment": v,
         "deductible": d, "sort": s}
        for (c, n, t, v, d, s) in accounting.DEFAULT_CHART
        if c not in {"bank_cash", "accounts_payable", "vat_output", "vat_input"}
    ]
    before_count = len(client.store["commerce_accounts"])

    rows = accounting.ensure_chart(TID)
    codes = {r["code"] for r in rows}
    assert {"bank_cash", "accounts_payable", "vat_output", "vat_input"} <= codes
    assert len(client.store["commerce_accounts"]) == before_count + 4
    # A second call must be a no-op (nothing new to add), not a duplicate insert.
    accounting.ensure_chart(TID)
    assert len(client.store["commerce_accounts"]) == before_count + 4
