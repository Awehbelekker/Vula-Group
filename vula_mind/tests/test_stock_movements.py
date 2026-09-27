"""Stock foundations (migration 182, 2026-09-27).

Every stock change outside checkout reservations goes through service.adjust_stock → the
apply_stock_change RPC, which updates the quantity and in_stock and records a movement in one
statement. Before this, PO receive was a read-modify-write on a product looked up by id alone
(not tenant-scoped, ignoring variants), the dashboard PATCH overwrote the number, and nothing
recorded who changed a count or why. Products can now carry their own barcode.
"""
import base64
import json

import pytest
from fastapi import HTTPException

from vula.api import commerce as api
from vula.commerce import service

TID = "off-the-hook"


class _Res:
    def __init__(self, data):
        self.data = data


class _In:
    def __init__(self, vals):
        self.vals = set(vals)

    def __eq__(self, other):
        return other in self.vals


class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.op, self.payload, self.filters = db, table, "select", None, []

    def select(self, *_a, **_k):
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def gt(self, *_a):
        return self

    def in_(self, col, vals):
        self.filters.append((col, _In(vals)))
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def single(self):
        self.one = True
        return self

    def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        hit = [r for r in rows if all(v == r.get(c) for c, v in self.filters)]
        if self.op == "insert":
            row = {"id": f"{self.table}-{len(rows) + 1}", **self.payload}
            rows.append(row)
            return _Res([dict(row)])
        if self.op == "update":
            for r in hit:
                r.update(self.payload)
            return _Res(hit)
        if getattr(self, "one", False):
            return _Res(dict(hit[0]) if hit else None)
        return _Res([dict(r) for r in hit])


class FakeDB:
    """Just enough postgrest: tables plus an apply_stock_change that behaves like migration 182."""

    def __init__(self, tables=None):
        self.tables = tables or {}
        self.rpcs = []

    def table(self, name):
        return _Q(self, name)

    def rpc(self, name, params):
        self.rpcs.append((name, params))
        db = self

        class _Call:
            def execute(self_inner):
                return _Res(getattr(db, "_rpc_" + name)(params))
        return _Call()

    def _rpc_apply_stock_change(self, p):
        if p["p_variant_id"]:
            rows = [r for r in self.tables.get("commerce_product_variants", [])
                    if r["id"] == p["p_variant_id"] and r["tenant_id"] == p["p_tenant_id"]
                    and r["product_id"] == p["p_product_id"]]
        else:
            rows = [r for r in self.tables.get("commerce_products", [])
                    if r["id"] == p["p_product_id"] and r["tenant_id"] == p["p_tenant_id"]]
        if not rows:
            return None
        row = rows[0]
        old = row.get("stock_quantity")
        new = p["p_set"] if p["p_set"] is not None else (old or 0) + (p["p_delta"] or 0)
        new = max(0, new)
        row.update(stock_quantity=new, in_stock=new > 0)
        self.tables.setdefault("commerce_stock_movements", []).append({
            "tenant_id": p["p_tenant_id"], "product_id": p["p_product_id"],
            "variant_id": p["p_variant_id"], "delta": new - (old or 0), "qty_after": new,
            "reason": p["p_reason"], "ref_type": p["p_ref_type"], "ref_id": p["p_ref_id"],
            "actor": p["p_actor"]})
        return new


@pytest.fixture()
def db(monkeypatch):
    fake = FakeDB({
        "commerce_products": [
            {"id": "p1", "tenant_id": TID, "name": "Hake", "stock_quantity": 10, "in_stock": True},
            {"id": "p2", "tenant_id": TID, "name": "Mackerel", "stock_quantity": None, "in_stock": True,
             "barcode": "6001234567890"},
            {"id": "px", "tenant_id": "other", "name": "Theirs", "stock_quantity": 3, "in_stock": True},
        ],
        "commerce_product_variants": [
            {"id": "v1", "tenant_id": TID, "product_id": "p1", "stock_quantity": 4, "in_stock": True,
             "barcode": "999", "commerce_products": {"id": "p1", "name": "Hake"}},
        ],
    })
    monkeypatch.setattr(service, "_client", lambda: fake)
    return fake


class _Req:
    def __init__(self, token=None):
        self.headers = {"authorization": f"Bearer {token}"} if token else {}


def _jwt(claims):
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"xx.{body}.sig"


def _moves(db):
    return db.tables.get("commerce_stock_movements", [])


# ── service.adjust_stock ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_adjust_stock_passes_every_field_to_the_rpc(db):
    after = await service.adjust_stock(TID, "p1", delta=5, reason="receive", ref_type="purchase_order",
                                       ref_id="po1", actor="ian@x")
    assert after == 15
    name, params = db.rpcs[0]
    assert name == "apply_stock_change"
    assert params == {"p_tenant_id": TID, "p_product_id": "p1", "p_variant_id": None, "p_delta": 5,
                      "p_set": None, "p_reason": "receive", "p_ref_type": "purchase_order",
                      "p_ref_id": "po1", "p_actor": "ian@x", "p_note": None}
    assert _moves(db)[0]["delta"] == 5 and _moves(db)[0]["qty_after"] == 15


@pytest.mark.asyncio
async def test_counting_an_untracked_product_starts_tracking_it(db):
    assert await service.adjust_stock(TID, "p2", set_to=7, reason="count") == 7
    p2 = db.tables["commerce_products"][1]
    assert p2["stock_quantity"] == 7 and p2["in_stock"] is True
    assert _moves(db)[0]["delta"] == 7


@pytest.mark.asyncio
async def test_another_tenants_product_is_not_touched(db):
    assert await service.adjust_stock(TID, "px", set_to=0) is None
    assert db.tables["commerce_products"][2]["stock_quantity"] == 3
    assert _moves(db) == []


@pytest.mark.asyncio
async def test_adjust_stock_needs_a_number(db):
    with pytest.raises(ValueError):
        await service.adjust_stock(TID, "p1")


# ── barcode lookup ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_barcode_finds_the_product_then_variants(db):
    hit = await service.find_by_barcode(TID, " 6001234567890 ")
    assert hit["product"]["id"] == "p2" and hit["variant"] is None
    hit = await service.find_by_barcode(TID, "999")
    assert hit["variant"]["id"] == "v1" and hit["product"] == {"id": "p1", "name": "Hake"}
    assert "commerce_products" not in hit["variant"]
    assert await service.find_by_barcode(TID, "nope") is None
    assert await service.find_by_barcode("other", "6001234567890") is None


@pytest.mark.asyncio
async def test_lookup_endpoint_404s_an_unknown_barcode(db):
    with pytest.raises(HTTPException) as e:
        await api.admin_lookup_barcode(TID, barcode="123")
    assert e.value.status_code == 404


def test_migration_182_adds_product_barcode_and_the_rpc():
    from pathlib import Path
    sql = (Path(__file__).parent.parent / "migrations" / "182_stock_movements.sql").read_text()
    assert "create unique index if not exists idx_products_tenant_barcode" in sql
    assert "enable row level security" in sql
    assert "for update" in sql                          # row lock: no lost concurrent update
    assert "and tenant_id = p_tenant_id" in sql


# ── dashboard PATCH ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dashboard_stock_edit_is_recorded_with_the_user(db):
    out = await api.admin_update_product(TID, "p1", {"stock_quantity": 3, "in_stock": True},
                                         _Req(_jwt({"email": "ian@vula.co.za"})))
    assert out["stock_quantity"] == 3
    m = _moves(db)[0]
    assert (m["delta"], m["reason"], m["ref_type"], m["actor"]) == (-7, "adjust", "dashboard", "ian@vula.co.za")


@pytest.mark.asyncio
async def test_variant_stock_edit_goes_through_the_rpc(db):
    out = await api.admin_update_variant(TID, "p1", "v1", {"stock_quantity": 0}, _Req())
    assert out == {"id": "v1", "stock_quantity": 0, "in_stock": False}
    assert _moves(db)[0]["variant_id"] == "v1" and _moves(db)[0]["actor"] == "api"


# ── purchase-order receive ────────────────────────────────────────────────────

def _po(db, **kw):
    po = {"id": "po1", "tenant_id": TID, "status": "sent",
          "items": [{"product_id": "p1", "quantity": 5}, {"product_id": "p1", "variant_id": "v1", "quantity": 2}]}
    po.update(kw)
    db.tables["commerce_purchase_orders"] = [po]
    return po


@pytest.mark.asyncio
async def test_receiving_a_po_adds_stock_per_line_including_variants(db):
    _po(db)
    await api.admin_update_po_status(TID, "po1", {"status": "received"}, _Req())
    assert db.tables["commerce_products"][0]["stock_quantity"] == 15
    assert db.tables["commerce_product_variants"][0]["stock_quantity"] == 6
    assert {m["reason"] for m in _moves(db)} == {"receive"}
    assert {m["ref_id"] for m in _moves(db)} == {"po1"}


@pytest.mark.asyncio
async def test_partial_delivery_books_what_arrived(db):
    _po(db)
    await api.admin_update_po_status(TID, "po1", {"status": "received",
                                                  "received": [{"product_id": "p1", "quantity": 3}]}, _Req())
    assert db.tables["commerce_products"][0]["stock_quantity"] == 13
    assert db.tables["commerce_product_variants"][0]["stock_quantity"] == 4


@pytest.mark.asyncio
async def test_a_po_is_received_once(db):
    _po(db, status="received")
    await api.admin_update_po_status(TID, "po1", {"status": "received"}, _Req())
    assert db.tables["commerce_products"][0]["stock_quantity"] == 10 and _moves(db) == []


@pytest.mark.asyncio
async def test_a_po_line_cannot_bump_another_tenants_product(db):
    _po(db, items=[{"product_id": "px", "quantity": 50}])
    await api.admin_update_po_status(TID, "po1", {"status": "received"}, _Req())
    assert db.tables["commerce_products"][2]["stock_quantity"] == 3


@pytest.mark.asyncio
async def test_another_tenants_po_is_not_found(db):
    _po(db, tenant_id="other")
    with pytest.raises(HTTPException) as e:
        await api.admin_update_po_status(TID, "po1", {"status": "received"}, _Req())
    assert e.value.status_code == 404


# ── order sales and refunds leave a record ────────────────────────────────────

@pytest.mark.asyncio
async def test_order_sale_and_refund_are_recorded(db, monkeypatch):
    order = {"id": "o1", "tenant_id": TID, "stock_adjusted": False,
             "commerce_order_items": [{"product_id": "p1", "quantity": 2}]}

    async def get_order(oid):
        return order

    async def noop(*_a, **_k):
        return None
    monkeypatch.setattr(service, "get_order", get_order)
    monkeypatch.setattr(service, "update_product_stock", noop)
    assert await service.apply_order_stock("o1") is True
    order["stock_adjusted"] = True
    assert await service.apply_order_stock("o1", restore=True) is True
    got = [(m["delta"], m["reason"], m["ref_id"]) for m in _moves(db)]
    assert got == [(-2, "sale", "o1"), (2, "refund", "o1")]
