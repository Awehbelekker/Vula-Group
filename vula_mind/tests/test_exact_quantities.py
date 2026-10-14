"""Stock in exact amounts (Manufacturing Core phase 1, migration 210). Before: stock was whole
numbers and the code rounded to fit, so a 0.5 kg sale took 0 off stock and 1.5 kg took 2."""
import pytest

from vula.commerce import service, stock_counts


class _RPC:
    def __init__(self, log, result=True):
        self.log, self.result = log, result

    def rpc(self, name, params):
        self.log.append((name, params))
        res = self.result(name, params) if callable(self.result) else self.result
        return type("Q", (), {"execute": lambda s: type("R", (), {"data": res})()})()

    def table(self, name):
        log = self.log

        class T:
            def insert(self, row):
                log.append((name, row))
                return self

            def update(self, row):
                log.append((name + ":update", row))
                return self

            def eq(self, *a):
                return self

            def execute(self):
                return type("R", (), {"data": []})()
        return T()


def test_qty_is_exact_and_reads_naturally():
    assert service.qty("2.5") == 2.5 and service.qty(0.1 + 0.2) == 0.3
    assert service.qty(None) == 0.0 and service.qty("abc") == 0.0
    assert service.qty_text(5.0) == "5" and service.qty_text(2.25) == "2.25" and service.qty_text("12") == "12"


@pytest.mark.asyncio
async def test_two_and_a_half_kilos_round_trip_exactly(monkeypatch):
    log = []
    monkeypatch.setattr(service, "_client", lambda: _RPC(log, result=lambda n, p: p.get("p_set") or p.get("p_delta")))
    after = await service.adjust_stock("off-the-hook", "p-1", set_to="2.5", reason="count")
    assert after == 2.5
    assert log[0] == ("apply_stock_change", {**log[0][1], "p_set": 2.5, "p_delta": None})
    await service.adjust_stock("off-the-hook", "p-1", delta=-0.75, reason="adjust")
    assert log[1][1]["p_delta"] == -0.75


@pytest.mark.asyncio
async def test_half_kilo_reservation_reserves_half_a_kilo(monkeypatch):
    log = []
    monkeypatch.setattr(service, "_client", lambda: _RPC(log))
    await service._reserve_cart_stock("off-the-hook", [
        {"product_id": "hake", "quantity": 0.5}, {"product_id": "kob", "quantity": "1.5"}])
    assert [p["p_qty"] for n, p in log if n == "reserve_product_stock"] == [0.5, 1.5]


@pytest.mark.asyncio
async def test_failed_reservation_gives_back_the_exact_amount(monkeypatch):
    log = []
    monkeypatch.setattr(service, "_client", lambda: _RPC(log, result=lambda n, p: p.get("p_product_id") != "kob"))
    with pytest.raises(service.OutOfStockError):
        await service._reserve_cart_stock("off-the-hook", [
            {"product_id": "hake", "quantity": 0.5}, {"product_id": "kob", "quantity": 1.5}])
    assert ("decrement_product_stock", {"p_tenant_id": "off-the-hook", "p_product_id": "hake", "p_delta": -0.5}) in log


@pytest.mark.asyncio
async def test_sale_and_refund_move_the_exact_amount(monkeypatch):
    log = []
    order = {"id": "o-1", "tenant_id": "off-the-hook", "stock_adjusted": False,
             "commerce_order_items": [{"product_id": "hake", "quantity": 0.5}]}

    async def get_order(oid):
        return dict(order)

    monkeypatch.setattr(service, "_client", lambda: _RPC(log))
    monkeypatch.setattr(service, "get_order", get_order)
    assert await service.apply_order_stock("o-1") is True
    assert ("decrement_product_stock", {"p_tenant_id": "off-the-hook", "p_product_id": "hake", "p_delta": 0.5}) in log
    movement = next(r for n, r in log if n == "commerce_stock_movements")
    assert movement["delta"] == -0.5 and movement["reason"] == "sale"
    order["stock_adjusted"] = True
    log.clear()
    assert await service.apply_order_stock("o-1", restore=True) is True
    assert ("decrement_product_stock", {"p_tenant_id": "off-the-hook", "p_product_id": "hake", "p_delta": -0.5}) in log


def test_count_variance_in_exact_kilos_and_whole_cents():
    v = stock_counts._variance(2.25, 2.5, 12000)          # R120/kg
    assert v == {"variance": -0.25, "variance_cents": -3000}
    assert stock_counts._variance(12, None, None)["variance"] == 12


@pytest.mark.asyncio
async def test_whatsapp_stock_update_keeps_the_half(monkeypatch):
    import core.skills.commerce_admin as ca
    monkeypatch.setattr(ca, "_emit", lambda **kw: None)

    async def get_product_by_slug(tid, slug):
        return None

    async def list_products(tid, **kw):
        return [{"id": "p1", "name": "Hake Fillets", "stock_quantity": 3.0}]

    calls = []

    async def adjust_stock(tid, pid, **kw):
        calls.append(kw)
        return 4.5

    async def get_product(tid, pid):
        return {"id": "p1", "stock_quantity": 4.5}

    monkeypatch.setattr(ca.service, "get_product_by_slug", get_product_by_slug)
    monkeypatch.setattr(ca.service, "list_products", list_products)
    monkeypatch.setattr(ca.service, "adjust_stock", adjust_stock)
    monkeypatch.setattr(ca.service, "get_product", get_product)
    skill = ca.CommerceAdminSkill()
    prev = await skill._update_stock("off-the-hook", "hake", None, add=1.5)
    assert prev["new_stock"] == 4.5 and prev["change"] == "+1.5"
    res = await skill._update_stock("off-the-hook", "hake", None, confirm=True, add=1.5)
    assert calls[0]["delta"] == 1.5
    assert res["reply_verbatim"] == "✅ Hake Fillets: stock is now *4.5*."


def test_storefront_cart_takes_a_kilo_and_a_half():
    from vula.commerce.models import AddToCartRequest
    assert AddToCartRequest(product_id="00000000-0000-0000-0000-000000000001", quantity=1.5).quantity == 1.5
    with pytest.raises(ValueError):
        AddToCartRequest(product_id="00000000-0000-0000-0000-000000000001", quantity=0)


def test_migration_210_moves_every_stock_column_and_function_to_numeric():
    import pathlib
    sql = (pathlib.Path(__file__).resolve().parents[1] / "migrations" / "210_exact_quantities.sql").read_text()
    for col in ("stock_quantity", "reorder_threshold", "reorder_qty", "delta", "qty_after", "counted", "expected"):
        assert f"alter column {col}" in sql
    for fn in ("reserve_product_stock", "reserve_variant_stock", "decrement_product_stock",
               "decrement_variant_stock", "apply_stock_change", "stock_count_scan"):
        assert f"drop function if exists {fn}(" in sql
    body = sql.split("drop function if exists stock_count_scan")[1].split(";", 1)[1].lower()
    assert " integer" not in body.replace("v_affected integer", "")
