"""Tests for the invoices/quotes service layer (commerce.service).

Uses an in-memory fake Supabase so the create → numbering → convert → list
flows are exercised end-to-end. All money is integer cents (ZAR); every query
is asserted tenant-scoped.
"""
from unittest.mock import patch

import pytest

from vula.commerce import service

TENANT = "off-the-hook"
OTHER = "kalk-bay"


# ── In-memory fake Supabase ───────────────────────────────────────────────────

class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, store, table):
        self._store = store
        self._table = table
        self._op = "select"
        self._filters = []
        self._payload = None
        self._patch = None
        self._order = None
        self._desc = False
        self._limit = None
        self._range = None
        self._single = False

    def select(self, *_a, **_k):
        self._op = "select"
        return self

    def insert(self, payload):
        self._op = "insert"
        self._payload = payload
        return self

    def update(self, patch):
        self._op = "update"
        self._patch = patch
        return self

    def delete(self):
        self._op = "delete"
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def order(self, col, desc=False):
        self._order, self._desc = col, desc
        return self

    def limit(self, n):
        self._limit = n
        return self

    def range(self, a, b):
        self._range = (a, b)
        return self

    def single(self):
        self._single = True
        return self

    def maybe_single(self):
        self._single = True
        return self

    def _match(self, row):
        return all(row.get(c) == v for c, v in self._filters)

    def execute(self):
        rows = self._store.setdefault(self._table, [])
        if self._op == "insert":
            payload = self._payload if isinstance(self._payload, list) else [self._payload]
            for p in payload:
                p = dict(p)
                p["__seq__"] = len(rows)
                rows.append(p)
            return _Result([_clean(p) for p in payload])
        if self._op == "update":
            hit = [r for r in rows if self._match(r)]
            for r in hit:
                r.update(self._patch)
            return _Result([_clean(r) for r in hit])
        if self._op == "delete":
            self._store[self._table] = [r for r in rows if not self._match(r)]
            return _Result([])
        data = [r for r in rows if self._match(r)]
        if self._order:
            data.sort(key=lambda r: (r.get(self._order), r.get("__seq__", 0)), reverse=self._desc)
        if self._range:
            a, b = self._range
            data = data[a:b + 1]
        elif self._limit is not None:
            data = data[:self._limit]
        cleaned = [_clean(r) for r in data]
        if self._single:
            return _Result(cleaned[0] if cleaned else None)
        return _Result(cleaned)


def _clean(row):
    return {k: v for k, v in row.items() if k != "__seq__"}


class _FakeSupabase:
    def __init__(self):
        self.store = {}

    def table(self, name):
        return _Query(self.store, name)


@pytest.fixture
def fake_db():
    fake = _FakeSupabase()
    with patch("vula.commerce.service._client", return_value=fake):
        yield fake


def _items():
    return [
        {"description": "Fresh snoek", "quantity": 2, "unit_price_cents": 18500},
        {"description": "Delivery", "quantity": 1, "unit_price_cents": 3000},
    ]


# ── Creation + totals math ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_invoice_computes_totals_server_side(fake_db):
    out = await service.create_invoice(
        TENANT, {"customer_name": "Thabo", "line_items": _items(), "vat_rate": 15.0}
    )
    # subtotal = 2*18500 + 3000 = 40000; vat = 6000; total = 46000
    assert out["subtotal_cents"] == 40000
    assert out["vat_cents"] == 6000
    assert out["total_cents"] == 46000
    # per-line totals recomputed
    assert out["line_items"][0]["total_cents"] == 37000
    assert out["doc_type"] == "invoice"
    assert out["invoice_number"] == "OFF-INV-00001"
    assert out["status"] == "draft"


@pytest.mark.asyncio
async def test_numbering_increments_per_doc_type(fake_db):
    a = await service.create_invoice(TENANT, {"customer_name": "A", "line_items": _items()})
    b = await service.create_invoice(TENANT, {"customer_name": "B", "line_items": _items()})
    q = await service.create_invoice(
        TENANT, {"doc_type": "quote", "customer_name": "C", "line_items": _items()}
    )
    assert a["invoice_number"] == "OFF-INV-00001"
    assert b["invoice_number"] == "OFF-INV-00002"
    assert q["invoice_number"] == "OFF-QTE-00001"


# ── Listing: doc_type filter + tenant scoping ─────────────────────────────────

@pytest.mark.asyncio
async def test_list_invoices_filters_doc_type_and_scopes_tenant(fake_db):
    await service.create_invoice(TENANT, {"customer_name": "A", "line_items": _items()})
    await service.create_invoice(
        TENANT, {"doc_type": "quote", "customer_name": "B", "line_items": _items()}
    )
    await service.create_invoice(OTHER, {"customer_name": "Z", "line_items": _items()})

    invoices = await service.list_invoices(TENANT, doc_type="invoice")
    quotes = await service.list_invoices(TENANT, doc_type="quote")
    other = await service.list_invoices(OTHER)

    assert [i["customer_name"] for i in invoices] == ["A"]
    assert [q["customer_name"] for q in quotes] == ["B"]
    assert [o["customer_name"] for o in other] == ["Z"]


# ── Status transitions ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_status_paid_stamps_paid_at(fake_db):
    inv = await service.create_invoice(TENANT, {"customer_name": "A", "line_items": _items()})
    updated = await service.update_invoice_status(TENANT, inv["id"], "paid")
    assert updated["status"] == "paid"
    assert updated["paid_at"]


# ── Quote → invoice conversion ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_convert_quote_links_both_directions(fake_db):
    quote = await service.create_invoice(
        TENANT, {"doc_type": "quote", "customer_name": "Thabo", "line_items": _items()}
    )
    invoice = await service.convert_quote_to_invoice(TENANT, quote["id"])

    assert invoice["doc_type"] == "invoice"
    assert invoice["invoice_number"] == "OFF-INV-00001"
    assert invoice["source_quote_id"] == quote["id"]
    # totals carried over verbatim
    assert invoice["total_cents"] == quote["total_cents"]

    refreshed = await service.get_invoice(TENANT, quote["id"])
    assert refreshed["status"] == "accepted"
    assert refreshed["converted_invoice_id"] == invoice["id"]


@pytest.mark.asyncio
async def test_convert_rejects_non_quote(fake_db):
    inv = await service.create_invoice(TENANT, {"customer_name": "A", "line_items": _items()})
    with pytest.raises(ValueError):
        await service.convert_quote_to_invoice(TENANT, inv["id"])


@pytest.mark.asyncio
async def test_convert_rejects_missing_quote(fake_db):
    with pytest.raises(ValueError):
        await service.convert_quote_to_invoice(TENANT, "does-not-exist")


@pytest.mark.asyncio
async def test_get_invoice_scoped_to_tenant(fake_db):
    inv = await service.create_invoice(TENANT, {"customer_name": "A", "line_items": _items()})
    assert await service.get_invoice(OTHER, inv["id"]) is None
