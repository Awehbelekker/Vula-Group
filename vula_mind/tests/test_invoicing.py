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


# ── Supplier tiered auto-detection ────────────────────────────────────────────

def _seed_supplier(fake_db, tenant=TENANT, **fields):
    row = {"id": fields.get("id") or f"sup-{len(fake_db.store.get('commerce_suppliers', []))}",
           "tenant_id": tenant, "name": fields["name"], "aliases": fields.get("aliases", []),
           "payment_terms_days": fields.get("payment_terms_days", 30),
           "tax_id": fields.get("tax_id"), "layout_signature": fields.get("layout_signature")}
    fake_db.store.setdefault("commerce_suppliers", []).append(row)
    return row


@pytest.mark.asyncio
async def test_match_supplier_returns_none_without_suppliers(fake_db):
    assert await service.match_supplier(TENANT, name="Anyone") is None


@pytest.mark.asyncio
async def test_match_supplier_tax_id_exact_is_tier1(fake_db):
    _seed_supplier(fake_db, name="Ocean Fresh", tax_id="4123456789", payment_terms_days=45)
    # Name differs entirely; tax id (formatted differently) still wins.
    m = await service.match_supplier(TENANT, name="Totally Different Co", tax_id="4123 456 789")
    assert m["tier"] == "tax_id"
    assert m["auto_apply"] is True
    assert m["supplier"]["payment_terms_days"] == 45


@pytest.mark.asyncio
async def test_match_supplier_exact_name_ignores_suffix_and_case(fake_db):
    _seed_supplier(fake_db, name="Ocean Fresh Seafoods (Pty) Ltd", payment_terms_days=30)
    m = await service.match_supplier(TENANT, name="ocean fresh seafoods")
    assert m["tier"] == "exact_name"
    assert m["confidence"] == 1.0
    assert m["auto_apply"] is True


@pytest.mark.asyncio
async def test_match_supplier_alias_exact(fake_db):
    _seed_supplier(fake_db, name="Ocean Fresh Seafoods", aliases=["OFS Wholesale"])
    m = await service.match_supplier(TENANT, name="OFS Wholesale")
    assert m["tier"] == "exact_name"


@pytest.mark.asyncio
async def test_match_supplier_fuzzy_auto_applies_when_near_identical(fake_db):
    _seed_supplier(fake_db, name="Ocean Fresh Seafoods")
    m = await service.match_supplier(TENANT, name="Ocean Fresh Seafood")  # missing trailing s
    assert m["tier"] == "fuzzy_name"
    assert m["confidence"] >= service.FUZZY_AUTO
    assert m["auto_apply"] is True


@pytest.mark.asyncio
async def test_match_supplier_no_match_below_threshold(fake_db):
    _seed_supplier(fake_db, name="Ocean Fresh Seafoods")
    assert await service.match_supplier(TENANT, name="Highveld Packaging") is None


@pytest.mark.asyncio
async def test_match_supplier_layout_signature_is_surfaced_not_auto(fake_db):
    sig = service.compute_layout_signature({"line_items": _items()})
    _seed_supplier(fake_db, name="Mystery Supplier", layout_signature=sig)
    # No name/tax id provided → only the layout signal can match.
    m = await service.match_supplier(TENANT, layout_signature=sig)
    assert m["tier"] == "layout"
    assert m["auto_apply"] is False


@pytest.mark.asyncio
async def test_match_supplier_is_tenant_scoped(fake_db):
    _seed_supplier(fake_db, tenant=OTHER, name="Ocean Fresh Seafoods", tax_id="4123456789")
    assert await service.match_supplier(TENANT, name="Ocean Fresh Seafoods", tax_id="4123456789") is None


def test_compute_layout_signature_is_order_independent_and_optional():
    a = service.compute_layout_signature({"line_items": [
        {"description": "Fresh snoek"}, {"description": "Delivery"}]})
    b = service.compute_layout_signature({"line_items": [
        {"description": "delivery"}, {"description": "FRESH  Snoek"}]})
    assert a == b and a is not None
    assert service.compute_layout_signature({"line_items": []}) is None


# ── match-supplier endpoint ───────────────────────────────────────────────────

def _seed_invoice(fake_db, tenant=TENANT, **fields):
    row = {"id": fields.get("id") or "inv-1", "tenant_id": tenant,
           "supplier": fields.get("supplier"), "tax_id": fields.get("tax_id"),
           "line_items": fields.get("line_items", [])}
    fake_db.store.setdefault("commerce_invoices", []).append(row)
    return row


@pytest.mark.asyncio
async def test_match_supplier_endpoint_404_when_invoice_missing(fake_db):
    from fastapi import HTTPException
    from vula.api.commerce import admin_match_invoice_supplier
    with pytest.raises(HTTPException) as exc:
        await admin_match_invoice_supplier(TENANT, "nope")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_match_supplier_endpoint_matches_by_tax_id(fake_db):
    from vula.api.commerce import admin_match_invoice_supplier
    _seed_supplier(fake_db, name="Ocean Fresh", tax_id="4123456789", payment_terms_days=45)
    _seed_invoice(fake_db, supplier="Totally Different Co", tax_id="4123 456 789")
    out = await admin_match_invoice_supplier(TENANT, "inv-1")
    assert out["ok"] is True and out["matched"] is True
    assert out["tier"] == "tax_id"
    assert out["auto_apply"] is True
    assert out["supplier"]["payment_terms_days"] == 45


@pytest.mark.asyncio
async def test_match_supplier_endpoint_no_match_returns_false(fake_db):
    from vula.api.commerce import admin_match_invoice_supplier
    _seed_supplier(fake_db, name="Ocean Fresh Seafoods")
    _seed_invoice(fake_db, supplier="Highveld Packaging")
    out = await admin_match_invoice_supplier(TENANT, "inv-1")
    assert out["matched"] is False and out["supplier"] is None


@pytest.mark.asyncio
async def test_match_supplier_endpoint_parses_json_string_line_items(fake_db):
    import json
    from vula.api.commerce import admin_match_invoice_supplier
    sig = service.compute_layout_signature({"line_items": _items()})
    _seed_supplier(fake_db, name="Mystery Supplier", layout_signature=sig)
    # line_items stored as a JSON string (as Supabase may return jsonb) and no name/tax id.
    _seed_invoice(fake_db, supplier=None, line_items=json.dumps(_items()))
    out = await admin_match_invoice_supplier(TENANT, "inv-1")
    assert out["matched"] is True
    assert out["tier"] == "layout"
    assert out["auto_apply"] is False
