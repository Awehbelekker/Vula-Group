"""Selling over the counter (vula/commerce/till.py, 24 Nov 2026): the till sends ids and
quantities, the server prices; one tap is one sale; cash short of the total sells nothing; the
sale is read back as paid; and the day's takings are sums of what was stored."""
import asyncio
import uuid

import pytest

from tests.fake_supabase import FakeSupabase
from vula.commerce import order_slip, service, till

TID = "seedleme"
PRICES = {"p-basin": 125050, "p-tap": 34900, "p-kg": 18000}


@pytest.fixture
def world(monkeypatch):
    db = FakeSupabase({"commerce_carts": [], "commerce_orders": [], "commerce_order_items": []})
    out = {"db": db, "paid": [], "moved": [], "restocked": [], "fail_paid": False, "gone": set(), "n": 0}
    monkeypatch.setattr(service, "_client", lambda: db)
    monkeypatch.setattr(order_slip, "urls", lambda oid, tid="": {"page": f"https://seedleme.shop.vula-ai.com/receipt/{oid}"})

    async def get_or_create_cart(tenant_id, session_id, customer_phone=None):
        for c in db.rows["commerce_carts"]:
            if c["tenant_id"] == tenant_id and c["session_id"] == session_id and c["status"] == "active":
                return c
        cart = {"id": str(uuid.uuid4()), "tenant_id": tenant_id, "session_id": session_id, "status": "active",
                "commerce_cart_items": []}
        db.rows["commerce_carts"].append(cart)
        return cart

    async def add_to_cart(tenant_id, cart_id, product_id, quantity, variant_id=None, **kw):
        if product_id in out["gone"] or product_id not in PRICES:
            raise ValueError("Product not found")
        cart = next(c for c in db.rows["commerce_carts"] if c["id"] == cart_id)
        cart["commerce_cart_items"].append({"product_id": product_id, "variant_id": variant_id,
                                            "quantity": quantity, "unit_price_cents": PRICES[product_id]})

    async def clear_cart(cart_id):
        for c in db.rows["commerce_carts"]:
            if c["id"] == cart_id:
                c.update(status="converted", commerce_cart_items=[])

    async def create_order(tenant_id, cart, data, clear_cart_after=True):
        out["n"] += 1
        items = cart["commerce_cart_items"]
        subtotal = sum(int(round(i["quantity"] * i["unit_price_cents"])) for i in items)
        discount = 5000 if data.get("discount_code") == "FIVER" else 0
        order = {"id": str(uuid.uuid4()), "display_id": f"SEE-{out['n']:05d}", "tenant_id": tenant_id,
                 "cart_id": cart["id"], "status": "pending_payment", "subtotal_cents": subtotal,
                 "discount_cents": discount, "delivery_cents": 0 if data.get("fulfilment") == "collection" else 8000,
                 "channel": data["channel"], "payment_method": data["payment_method"],
                 "customer_name": data["customer_name"], "customer_phone": data["customer_phone"],
                 "created_by": service_actor(), "created_at": "2026-11-24T09:00:00+00:00"}
        order["total_cents"] = subtotal - discount + order["delivery_cents"]
        db.rows["commerce_orders"].append(order)
        for i in items:
            db.rows["commerce_order_items"].append({
                "order_id": order["id"], "product_name": i["product_id"], "quantity": i["quantity"],
                "unit_price_cents": i["unit_price_cents"],
                "total_cents": int(round(i["quantity"] * i["unit_price_cents"]))})
        await clear_cart(cart["id"])
        return dict(order)

    def service_actor():
        from vula import acting
        return acting.current()

    async def mark_order_paid(tenant_id, order_id, method, **kw):
        if out["fail_paid"]:
            return None
        for o in db.rows["commerce_orders"]:
            if o["id"] == order_id and o["tenant_id"] == tenant_id and o["status"] == "pending_payment":
                o.update(status="paid", payment_method=method)
                out["paid"].append((o["display_id"], method))
                return dict(o)
        return None

    async def update_order_status(order_id, status, **kw):
        out["moved"].append(status)
        for o in db.rows["commerce_orders"]:
            if o["id"] == order_id:
                o["status"] = status

    async def apply_order_stock(order_id, restore=False):
        out["restocked"].append((order_id, restore))
        return True

    for name, fn in (("get_or_create_cart", get_or_create_cart), ("add_to_cart", add_to_cart),
                     ("clear_cart", clear_cart), ("create_order", create_order),
                     ("mark_order_paid", mark_order_paid), ("update_order_status", update_order_status),
                     ("apply_order_stock", apply_order_stock)):
        monkeypatch.setattr(service, name, fn)
    return out


BASKET = [{"product_id": "p-basin", "quantity": 1}, {"product_id": "p-tap", "quantity": 2}]


def ring(**kw):
    args = {"items": BASKET, "method": "cash", "sale_id": "sale-0001-abcdef", "tendered_cents": 200000}
    return asyncio.run(till.sale(TID, **{**args, **kw}))


def test_a_cash_sale_is_priced_on_the_server_paid_handed_over_and_read_back(world):
    r = ring()
    assert r["total_cents"] == 125050 + 2 * 34900 == 194850 and r["change_cents"] == 5150
    assert r["paid"] is True and r["status"] == "delivered" and r["method_label"] == "Cash"
    assert [(l["name"], l["quantity"], l["total_cents"]) for l in r["lines"]] == [("p-basin", 1, 125050), ("p-tap", 2, 69800)]
    assert r["customer_name"] == "Walk-in customer" and r["receipt_url"].endswith(r["order_id"])
    order = world["db"].rows["commerce_orders"][0]
    assert order["channel"] == "till" and order["delivery_cents"] == 0          # nobody pays delivery at a counter
    assert world["paid"] == [("SEE-00001", "cash")] and world["moved"] == ["delivered"]


def test_a_price_sent_by_the_till_is_never_used(world):
    r = ring(items=[{"product_id": "p-basin", "quantity": 1, "price_cents": 1, "unit_price_cents": 1}])
    assert r["total_cents"] == 125050


def test_the_same_basket_rung_up_twice_is_one_sale(world):
    first = ring()
    again = ring()
    assert again["repeat"] is True and again["number"] == first["number"] == "SEE-00001"
    assert len(world["db"].rows["commerce_orders"]) == 1 and len(world["paid"]) == 1
    assert ring(sale_id="sale-0002-abcdef")["number"] == "SEE-00002"            # a new basket is a new sale


def test_cash_short_of_the_total_sells_nothing(world):
    with pytest.raises(till.TillError, match=r"R1,000.00 is less than the total of R1,948.50") as exc:
        ring(tendered_cents=100000)
    assert exc.value.detail["total_cents"] == 194850
    assert world["db"].rows["commerce_orders"] == [] and world["paid"] == []
    assert ring(tendered_cents=194850)["change_cents"] == 0                     # exact money, same basket id


def test_card_eft_and_wallets_need_no_cash_amount_and_give_no_change(world):
    for i, m in enumerate(("card", "eft", "snapscan", "zapper")):
        r = ring(method=m, tendered_cents=None, sale_id=f"sale-{i:04d}-abcdef")
        assert r["method"] == m and r["change_cents"] is None and r["tendered_cents"] is None and r["paid"]
    for bad in ("", "bitcoin", None):
        with pytest.raises(till.TillError, match="how the customer paid"):
            ring(method=bad, sale_id="sale-9999-abcdef")


def test_a_basket_that_changed_under_the_till_is_not_sold_short(world):
    world["gone"].add("p-tap")
    with pytest.raises(till.TillError, match="couldn't be added") as exc:
        ring()
    assert exc.value.status == 409 and world["db"].rows["commerce_orders"] == []
    for empty in ([], None, [{"product_id": "p-basin", "quantity": 0}], [{"quantity": 2}]):
        with pytest.raises(till.TillError, match="basket is empty"):
            ring(items=empty, sale_id=f"sale-{uuid.uuid4().hex[:12]}")


def test_a_sale_that_cannot_be_recorded_as_paid_is_undone(world):
    world["fail_paid"] = True
    with pytest.raises(till.TillError, match="nothing was sold"):
        ring()
    order = world["db"].rows["commerce_orders"][0]
    assert order["status"] == "cancelled" and world["restocked"] == [(order["id"], True)]


def test_a_discount_code_is_applied_by_the_server_and_cash_is_checked_against_the_real_total(world):
    r = ring(items=[{"product_id": "p-tap", "quantity": 1}], discount_code="FIVER", tendered_cents=30000)
    assert r["discount_cents"] == 5000 and r["total_cents"] == 29900 and r["change_cents"] == 100
    with pytest.raises(till.TillError, match="less than the total"):
        ring(items=[{"product_id": "p-tap", "quantity": 1}], discount_code="NOPE", tendered_cents=30000,
             sale_id="sale-0003-abcdef")
    assert [o["status"] for o in world["db"].rows["commerce_orders"]] == ["delivered", "cancelled"]


def test_by_weight_quantities_and_who_rang_it_up(world):
    from vula import acting
    with acting.acting_as("27825550000"):
        r = ring(items=[{"product_id": "p-kg", "quantity": 1.25}], method="card", tendered_cents=None)
    assert r["total_cents"] == 22500 and world["db"].rows["commerce_orders"][0]["created_by"] == "27825550000"


def test_a_sale_needs_a_reference_from_the_till(world):
    for bad in ("", "short", "has spaces in it!", "x" * 80):
        with pytest.raises(till.TillError, match="no reference"):
            ring(sale_id=bad)


def test_the_days_takings_are_sums_of_what_was_stored(world):
    db = world["db"]
    def row(n, cents, method, status="delivered", who="27825550000", at="2026-11-24T08:00:00+00:00", **kw):
        return {"id": f"o{n}", "display_id": f"SEE-{n:05d}", "tenant_id": TID, "channel": "till",
                "total_cents": cents, "payment_method": method, "status": status, "created_by": who,
                "created_at": at, **kw}
    db.rows["commerce_orders"] = [
        row(1, 10000, "cash"), row(2, 25050, "cash", who="27821110000"), row(3, 40000, "card"),
        row(4, 9900, "cash", status="cancelled"),                                  # never happened
        row(5, 30000, "card", status="refunded", refunded_amount_cents=30000),     # sold, then refunded
        row(6, 5000, "cash", at="2026-11-23T21:30:00+00:00"),                      # 23:30 SAST the day before
        row(7, 7000, "cash", at="2026-11-23T22:30:00+00:00"),                      # 00:30 SAST: today
        {**row(8, 99900, "cash"), "channel": "whatsapp"},                           # not a till sale
        {**row(9, 88800, "cash"), "tenant_id": "digg"},                             # another business
    ]
    s = till.summary(TID, "2026-11-24")
    assert s["sales"] == 5 and s["total_cents"] == 10000 + 25050 + 40000 + 30000 + 7000 == 112050
    assert s["refunded_cents"] == 30000 and s["net_cents"] == 82050 and s["not_completed"] == 1
    assert {m["method"]: (m["count"], m["cents"]) for m in s["by_method"]} == {"card": (2, 70000), "cash": (3, 42050)}
    assert {p["who"]: p["cents"] for p in s["by_person"]} == {"27825550000": 87000, "27821110000": 25050}
    assert s["by_method"][0]["label"] == "Card machine" and len(s["recent"]) == 5
    assert till.summary(TID, "2026-11-23")["total_cents"] == 5000
    with pytest.raises(till.TillError, match="date like"):
        till.summary(TID, "yesterday")


def test_more_than_a_thousand_sales_in_a_day_are_all_counted(world):
    world["db"].max_rows = 1000
    world["db"].rows["commerce_orders"] = [
        {"id": f"{i:05d}", "tenant_id": TID, "channel": "till", "total_cents": 100, "payment_method": "cash",
         "status": "delivered", "created_at": "2026-11-24T08:00:00+00:00"} for i in range(1300)]
    s = till.summary(TID, "2026-11-24")
    assert s["sales"] == 1300 and s["total_cents"] == 130000


def test_the_till_shows_the_price_a_sale_would_charge(world, monkeypatch):
    async def list_products(tenant_id, **kw):
        assert kw.get("statuses") == {"active"}
        return [{"id": "p1", "name": "Tap", "price_cents": 40000, "sale_price_cents": 34900, "barcode": "600123",
                 "stock_quantity": 4, "in_stock": True, "sold_by": "each"},
                {"id": "p2", "name": "Basin", "price_cents": 125050, "in_stock": False},
                {"id": "p3", "name": "Old", "price_cents": 100, "archived": True}]
    monkeypatch.setattr(service, "list_products", list_products)
    world["db"].rows["commerce_product_variants"] = [
        {"id": "v1", "tenant_id": TID, "product_id": "p2", "option_values": {"colour": "White"}, "price_cents": 130000,
         "barcode": "600999", "stock_quantity": 2},
        {"id": "v2", "tenant_id": TID, "product_id": "p2", "option_values": {"colour": "Black"}, "archived": True},
        {"id": "v3", "tenant_id": "digg", "product_id": "p2", "option_values": {"colour": "Red"}}]
    rows = asyncio.run(till.products(TID))
    assert [r["name"] for r in rows] == ["Basin", "Tap"]
    tap = rows[1]
    assert tap["price_cents"] == 34900 and tap["on_sale"] is True and tap["barcode"] == "600123"
    assert rows[0]["in_stock"] is False and [v["label"] for v in rows[0]["variants"]] == ["White"]
    assert rows[0]["variants"][0]["price_cents"] == 130000


def test_the_till_routes_are_inside_the_tenant_guard():
    from vula.api import server
    for p in ("/v1/commerce/x/admin/till/products", "/v1/commerce/x/admin/till/sales",
              "/v1/commerce/x/admin/till/summary"):
        assert any(rx.match(p) for rx in server._TENANT_GUARD_RES), p
