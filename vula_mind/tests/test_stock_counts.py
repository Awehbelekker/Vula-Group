"""Stock-takes, receiving and quick adjust from the dashboard Stock tab (migration 183).

Staff scan items into an open count; the owner or a manager applies it, which posts one
'count' movement per line. A phone that was offline re-sends its queued scans — a scan_id
already recorded changes nothing. The SQL of stock_count_scan was checked against production
inside a rolled-back transaction; FakeCountDB mirrors it.
"""
import pytest
from fastapi import HTTPException

from tests.test_stock_movements import TID, FakeDB, _moves, _Req
from vula.api import commerce as api
from vula.commerce import service


class FakeCountDB(FakeDB):
    def _rpc_stock_count_scan(self, p):
        count = [c for c in self.tables.get("commerce_stock_counts", [])
                 if c["id"] == p["p_count_id"] and c["tenant_id"] == p["p_tenant_id"] and c["status"] == "open"]
        prod = [r for r in self.tables["commerce_products"]
                if r["id"] == p["p_product_id"] and r["tenant_id"] == p["p_tenant_id"]]
        if not count or not prod:
            return None
        lines = self.tables.setdefault("commerce_stock_count_lines", [])
        line = next((ln for ln in lines if ln["count_id"] == p["p_count_id"]
                     and ln["product_id"] == p["p_product_id"] and ln.get("variant_id") == p["p_variant_id"]), None)
        seen = self.tables.setdefault("commerce_stock_count_scans", [])
        if p["p_scan_id"] is not None:
            if (p["p_count_id"], p["p_scan_id"]) in seen:
                return line["counted"] if line else 0
            seen.append((p["p_count_id"], p["p_scan_id"]))
        if line is None:
            line = {"id": f"line-{len(lines) + 1}", "count_id": p["p_count_id"], "tenant_id": p["p_tenant_id"],
                    "product_id": p["p_product_id"], "variant_id": p["p_variant_id"], "counted": 0}
            lines.append(line)
            base = 0
        else:
            base = line["counted"]
        line["counted"] = max(0, p["p_set"] if p["p_set"] is not None else base + (p["p_add"] or 1))
        line["counted_by"] = p["p_actor"]
        return line["counted"]


@pytest.fixture()
def db(monkeypatch):
    fake = FakeCountDB({
        "commerce_products": [
            {"id": "p1", "tenant_id": TID, "name": "Hake", "stock_quantity": 10, "in_stock": True, "cost_cents": 5000},
            {"id": "p2", "tenant_id": TID, "name": "Mackerel", "stock_quantity": 4, "in_stock": True},
            {"id": "p3", "tenant_id": TID, "name": "Prawns", "stock_quantity": 6, "in_stock": True},
            {"id": "px", "tenant_id": "other", "name": "Theirs", "stock_quantity": 3, "in_stock": True},
        ],
    })
    monkeypatch.setattr(service, "_client", lambda: fake)

    async def list_products(tid, **kw):
        return [p for p in fake.tables["commerce_products"] if p["tenant_id"] == tid]
    monkeypatch.setattr(service, "list_products", list_products)
    return fake


@pytest.fixture()
def owner(monkeypatch):
    async def yes(request, tenant_id):
        return True
    monkeypatch.setattr(api, "_may_apply_stock", yes)


async def _count(db):
    return (await api.admin_start_stock_count(TID, {"note": "Month end"}, _Req()))["id"]


def _scan(sid, pid, **kw):
    return {"scan_id": sid, "product_id": pid, **kw}


@pytest.mark.asyncio
async def test_scans_add_up_and_a_resent_batch_counts_once(db):
    cid = await _count(db)
    batch = {"scans": [_scan("a", "p1"), _scan("b", "p1"), _scan("c", "p1", add=5)]}
    first = await api.admin_stock_count_scans(TID, cid, batch, _Req())
    assert [r["counted"] for r in first["results"]] == [1, 2, 7]
    again = await api.admin_stock_count_scans(TID, cid, batch, _Req())   # offline retry
    assert [r["counted"] for r in again["results"]] == [7, 7, 7]          # totals, nothing added
    line = db.tables["commerce_stock_count_lines"][0]
    assert line["counted"] == 7
    assert db.tables["commerce_products"][0]["stock_quantity"] == 10     # nothing moves before apply


@pytest.mark.asyncio
async def test_a_typed_quantity_sets_the_count(db):
    cid = await _count(db)
    await api.admin_stock_count_scans(TID, cid, {"scans": [_scan("a", "p1"), _scan("b", "p1", set=12)]}, _Req())
    assert db.tables["commerce_stock_count_lines"][0]["counted"] == 12


@pytest.mark.asyncio
async def test_another_tenants_product_cannot_be_counted(db):
    cid = await _count(db)
    res = await api.admin_stock_count_scans(TID, cid, {"scans": [_scan("a", "px")]}, _Req())
    assert res["results"][0]["counted"] is None


@pytest.mark.asyncio
async def test_review_shows_variance_valued_at_cost_and_uncounted_items(db):
    cid = await _count(db)
    await api.admin_stock_count_scans(TID, cid, {"scans": [_scan("a", "p1", set=8), _scan("b", "p2", set=4)]}, _Req())
    rv = await api.admin_review_stock_count(TID, cid)
    hake = next(ln for ln in rv["lines"] if ln["product_id"] == "p1")
    assert (hake["expected"], hake["counted"], hake["variance"], hake["variance_cents"]) == (10, 8, -2, -10000)
    assert rv["variance_cents"] == -10000
    assert rv["lines"][0]["product_id"] == "p1"                           # biggest difference first
    assert [u["product_id"] for u in rv["uncounted"]] == ["p3"]


@pytest.mark.asyncio
async def test_apply_sets_stock_records_count_movements_and_only_once(db, owner):
    cid = await _count(db)
    await api.admin_stock_count_scans(TID, cid, {"scans": [_scan("a", "p1", set=8), _scan("b", "p2", set=9)]}, _Req())
    out = await api.admin_apply_stock_count(TID, cid, _Req())
    assert out == {"applied": 2, "failed": []}
    assert db.tables["commerce_products"][0]["stock_quantity"] == 8
    assert db.tables["commerce_products"][1]["stock_quantity"] == 9
    assert db.tables["commerce_products"][2]["stock_quantity"] == 6      # uncounted: untouched
    assert {(m["product_id"], m["delta"], m["reason"], m["ref_id"]) for m in _moves(db)} == {
        ("p1", -2, "count", cid), ("p2", 5, "count", cid)}
    assert db.tables["commerce_stock_count_lines"][0]["expected"] == 10
    with pytest.raises(HTTPException) as e:
        await api.admin_apply_stock_count(TID, cid, _Req())
    assert e.value.status_code == 409 and len(_moves(db)) == 2
    # A closed count takes no more scans.
    res = await api.admin_stock_count_scans(TID, cid, {"scans": [_scan("z", "p1")]}, _Req())
    assert res["results"][0]["counted"] is None


@pytest.mark.asyncio
async def test_staff_cannot_apply(db, monkeypatch):
    async def no(request, tenant_id):
        return False
    monkeypatch.setattr(api, "_may_apply_stock", no)
    cid = await _count(db)
    with pytest.raises(HTTPException) as e:
        await api.admin_apply_stock_count(TID, cid, _Req())
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_may_apply_rule(db, monkeypatch):
    from vula.api import master_auth
    monkeypatch.setattr("config.settings.api_key", "k")

    async def verify(token):
        return {"id": "u1", "email": token}
    monkeypatch.setattr(master_auth, "_verify_jwt", verify)
    monkeypatch.setattr(master_auth, "_role_for_user", lambda uid: "owner")
    db.tables["vula_team_members"] = [
        {"tenant_id": TID, "email": "staff@x", "role": "staff"},
        {"tenant_id": TID, "email": "mgr@x", "role": "manager"}]

    class R:
        def __init__(self, h):
            self.headers = h
    assert await api._may_apply_stock(R({"x-api-key": "k"}), TID) is True
    assert await api._may_apply_stock(R({"authorization": "Bearer mgr@x"}), TID) is True
    assert await api._may_apply_stock(R({"authorization": "Bearer owner@x"}), TID) is True
    assert await api._may_apply_stock(R({"authorization": "Bearer staff@x"}), TID) is False
    assert await api._may_apply_stock(R({}), TID) is False


@pytest.mark.asyncio
async def test_cancel_and_other_tenants_count(db):
    cid = await _count(db)
    with pytest.raises(HTTPException) as e:
        await api.admin_review_stock_count("other", cid)
    assert e.value.status_code == 404
    assert (await api.admin_cancel_stock_count(TID, cid)) == {"ok": True}
    with pytest.raises(HTTPException) as e:
        await api.admin_cancel_stock_count(TID, cid)
    assert e.value.status_code == 409


# ── receive / adjust / barcode link ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_receive_without_a_po_books_stock_and_cost(db):
    out = await api.admin_receive_stock(TID, {"reference": "DN-88", "lines": [
        {"product_id": "p2", "quantity": 6, "unit_cost_cents": 4200},
        {"product_id": "px", "quantity": 99}]}, _Req())
    assert out == {"received": 1, "failed": ["px"]}
    p2 = db.tables["commerce_products"][1]
    assert p2["stock_quantity"] == 10 and p2["cost_cents"] == 4200
    assert _moves(db)[0]["reason"] == "receive" and _moves(db)[0]["ref_id"] == "DN-88"
    assert db.tables["commerce_products"][3]["stock_quantity"] == 3


@pytest.mark.asyncio
async def test_receive_against_a_po_marks_it_received_once(db):
    db.tables["commerce_purchase_orders"] = [{"id": "po1", "tenant_id": TID, "status": "sent",
                                              "items": [{"product_id": "p1", "quantity": 10}]}]
    await api.admin_receive_stock(TID, {"po_id": "po1", "lines": [{"product_id": "p1", "quantity": 7}]}, _Req())
    assert db.tables["commerce_products"][0]["stock_quantity"] == 17
    assert db.tables["commerce_purchase_orders"][0]["status"] == "received"
    with pytest.raises(HTTPException) as e:
        await api.admin_receive_stock(TID, {"po_id": "po1", "lines": [{"product_id": "p1", "quantity": 7}]}, _Req())
    assert e.value.status_code == 409
    assert db.tables["commerce_products"][0]["stock_quantity"] == 17


@pytest.mark.asyncio
async def test_quick_adjust_add_and_set(db):
    assert (await api.admin_adjust_stock(TID, {"product_id": "p1", "add": -3}, _Req()))["stock_quantity"] == 7
    assert (await api.admin_adjust_stock(TID, {"product_id": "p1", "set": 20}, _Req()))["stock_quantity"] == 20
    assert [m["ref_type"] for m in _moves(db)] == ["scan", "scan"]
    with pytest.raises(HTTPException) as e:
        await api.admin_adjust_stock(TID, {"product_id": "px", "set": 1}, _Req())
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_link_an_unknown_barcode_then_it_scans(db):
    got = await api.admin_link_barcode(TID, "p2", {"barcode": "6009876543210"})
    assert got["product"]["id"] == "p2"
    assert (await service.find_by_barcode(TID, "6009876543210"))["product"]["id"] == "p2"
    with pytest.raises(HTTPException) as e:                         # already on Mackerel
        await api.admin_link_barcode(TID, "p1", {"barcode": "6009876543210"})
    assert e.value.status_code == 409
    with pytest.raises(HTTPException) as e:                         # not this tenant's product
        await api.admin_link_barcode(TID, "px", {"barcode": "111"})
    assert e.value.status_code == 404
