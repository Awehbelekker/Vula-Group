"""Events and tickets (vula/commerce/events.py, 24 Nov 2026): a ticket sale is an ordinary order
priced by the server; seats are held atomically; a paid order's tickets are issued once; a ticket
gets one person in, once; a cancelled or fully refunded order's tickets stop working."""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from config import settings
from tests.fake_supabase import FakeSupabase
from vula.api import commerce as commerce_api, events as events_api, tenants
from vula.commerce import events, order_slip, service, web_basket

TID = "seedleme"


class DB(FakeSupabase):
    def _product(self, tenant, pid):
        return next((p for p in self.rows["commerce_products"] if p["id"] == pid and p["tenant_id"] == tenant), None)

    def _rpc_reserve_product_stock_exact(self, p_tenant_id, p_product_id, p_qty):
        p = self._product(p_tenant_id, p_product_id)
        if not p or p["stock_quantity"] < p_qty:
            return False
        p["stock_quantity"] -= p_qty
        p["in_stock"] = p["stock_quantity"] > 0
        return True

    def _rpc_decrement_product_stock_exact(self, p_tenant_id, p_product_id, p_delta):
        p = self._product(p_tenant_id, p_product_id)
        p["stock_quantity"] -= p_delta
        p["in_stock"] = p["stock_quantity"] > 0
        return True


def run(c):
    return asyncio.run(c)


def _soon(days=10, hours=0):
    return (datetime.now(timezone.utc) + timedelta(days=days, hours=hours)).isoformat()


@pytest.fixture
def world(monkeypatch):
    db = DB({t: [] for t in ("commerce_events", "commerce_ticket_types", "commerce_tickets", "commerce_products",
                             "commerce_carts", "commerce_cart_items", "commerce_orders", "commerce_order_items")})
    monkeypatch.setattr(service, "_client", lambda: db)
    monkeypatch.setattr(tenants, "is_active", lambda tid: tid != "paused")
    monkeypatch.setattr(web_basket, "public_base", lambda tid: f"https://{tid}.shop.vula-ai.com")
    told = []

    async def tell(tenant_id, order, count):
        told.append((order["id"], count))
        return True

    monkeypatch.setattr(events, "_tell_buyer", tell)
    out = SimpleNamespace(db=db, told=told, orders=[], returns=[], fail=None)

    async def checkout_for(tenant_id, body, *, extra=None, return_to=None):
        """The storefront checkout, reduced to what matters here: the order is made from the cart
        at the product rows' prices, and its seats are held with the same atomic call."""
        from fastapi import HTTPException
        if out.fail:
            raise HTTPException(status_code=out.fail, detail="Payment gateway error — please try again")
        cart = next(c for c in db.rows["commerce_carts"] if c["session_id"] == body.session_id)
        items = [i for i in db.rows["commerce_cart_items"] if i["cart_id"] == cart["id"]]
        for i in items:
            if not db._rpc_reserve_product_stock_exact(tenant_id, i["product_id"], i["quantity"]):
                raise HTTPException(status_code=409, detail="out of stock")
        order = {"id": str(uuid.uuid4()), "display_id": f"SEE-{len(out.orders) + 1:05d}", "tenant_id": tenant_id,
                 "status": "pending_payment", "customer_name": body.customer_name, "channel": body.channel,
                 "customer_phone": body.customer_phone, "refunded_amount_cents": 0,
                 "total_cents": sum(int(i["quantity"] * i["unit_price_cents"]) for i in items), **(extra or {})}
        db.rows["commerce_orders"].append(order)
        for i in items:
            db.rows["commerce_order_items"].append({"id": str(uuid.uuid4()), "order_id": order["id"],
                                                    "product_id": i["product_id"], "quantity": i["quantity"],
                                                    "product_name": "x"})
        out.orders.append(order)
        out.returns.append(return_to(order))
        return {"redirect_url": "https://pay.example/c/1", "display_id": order["display_id"],
                "total_cents": order["total_cents"], "order_id": order["id"]}

    monkeypatch.setattr(commerce_api, "checkout_for", checkout_for)

    async def get_order(order_id):
        return next((o for o in db.rows["commerce_orders"] if o["id"] == order_id), None)

    monkeypatch.setattr(service, "get_order", get_order)
    return out


def _event(**kw):
    data = {"title": "Summer Market", "starts_at": _soon(), "ends_at": _soon(hours=4), "venue": "The Yard",
            "types": [{"name": "General", "price_cents": 15000, "capacity": 5, "max_per_order": 4},
                      {"name": "VIP", "price_cents": 45000, "capacity": 2, "max_per_order": 2}], **kw}
    ev = run(events.create(TID, data, "owner@x.co.za"))
    return ev, {t["name"]: t for t in ev["types"]}


def _on_sale():
    ev, types = _event()
    run(events.set_status(TID, ev["id"], "on_sale"))
    return ev, types


def _buy(ev, items, **kw):
    return run(events.buy(ev["id"], items=items, name=kw.get("name", "Thandi Mokoena"),
                          phone=kw.get("phone", "082 123 4567"), email=kw.get("email", "")))


def _pay(world, n=-1):
    order = world.orders[n]
    order["status"] = "paid"
    return run(events.issue_for_order(TID, order))


# ── setting an event up ───────────────────────────────────────────────────────────────────────
def test_a_new_event_is_a_draft_whose_ticket_types_are_products_never_listed_as_goods(world):
    ev, types = _event()
    assert ev["status"] == "draft" and ev["capacity"] == 7 and ev["sold"] == 0 and not ev["selling"]
    assert types["General"]["price_cents"] == 15000 and types["General"]["left"] == 5
    prods = world.db.rows["commerce_products"]
    assert {p["product_type"] for p in prods} == {"ticket"} and {p["status"] for p in prods} == {"unlisted"}
    assert prods[0]["name"] == "Summer Market — General"
    assert run(service.list_products(TID)) == []                         # not in the shop, the till, the assistant
    assert len(run(service.list_products(TID, include_tickets=True))) == 2
    assert events.public_event(ev["id"]) is None                         # a draft has no public page


@pytest.mark.parametrize("bad, says", [
    ({"title": " "}, "name"), ({"starts_at": "next week"}, "date"), ({"starts_at": ""}, "starts"),
    ({"ends_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}, "end before"),
    ({"image_url": "http://plain.example/a.jpg"}, "https"),
    ({"types": [{"name": "Free", "price_cents": 0, "capacity": 5}]}, "price"),
    ({"types": [{"name": "A", "price_cents": 150.5, "capacity": 5}]}, "whole number"),
    ({"types": [{"name": "A", "price_cents": 100, "capacity": 5}, {"name": "a", "price_cents": 100, "capacity": 5}]}, "same name"),
])
def test_refused_events(world, bad, says):
    with pytest.raises(events.EventError) as e:
        _event(**bad)
    assert says in str(e.value)


def test_going_on_sale_needs_tickets_and_a_future_date(world):
    ev, _ = _event(types=[])
    with pytest.raises(events.EventError, match="ticket type"):
        run(events.set_status(TID, ev["id"], "on_sale"))
    past, _ = _event(starts_at=(datetime.now(timezone.utc) - timedelta(days=2)).isoformat(), ends_at=None)
    with pytest.raises(events.EventError, match="past"):
        run(events.set_status(TID, past["id"], "on_sale"))
    with pytest.raises(events.EventError):
        run(events.set_status("someone_else", ev["id"], "closed"))           # not this business's event


def test_capacity_goes_up_freely_and_down_only_to_what_is_unsold(world):
    ev, types = _on_sale()
    _buy(ev, [{"type_id": types["General"]["id"], "quantity": 3}])
    _pay(world)
    g = {"id": types["General"]["id"], "name": "General", "price_cents": 18000, "max_per_order": 4}
    vip = {"id": types["VIP"]["id"], "name": "VIP", "price_cents": 45000, "capacity": 2, "max_per_order": 2}
    after = run(events.update(TID, ev["id"], {"types": [{**g, "capacity": 9}, vip]}))
    general = next(t for t in after["types"] if t["name"] == "General")
    assert general["capacity"] == 9 and general["left"] == 6 and general["sold"] == 3 and general["price_cents"] == 18000
    with pytest.raises(events.EventError, match="already sold"):
        run(events.update(TID, ev["id"], {"types": [{**g, "capacity": 2}, vip]}))
    assert events.get(TID, ev["id"])["types"][0]["left"] == 6                  # nothing changed
    with pytest.raises(events.EventError, match="can't be removed"):
        run(events.update(TID, ev["id"], {"types": [vip]}))
    only_general = run(events.update(TID, ev["id"], {"types": [{**g, "capacity": 9}]}))   # VIP unsold: withdrawn
    assert [t["name"] for t in only_general["types"]] == ["General"]


# ── buying ────────────────────────────────────────────────────────────────────────────────────
def test_buying_makes_an_ordinary_order_priced_by_the_server_and_holds_the_seats(world):
    ev, types = _on_sale()
    out = _buy(ev, [{"type_id": types["General"]["id"], "quantity": 2, "price_cents": 1},
                    {"type_id": types["VIP"]["id"], "quantity": 1}])
    assert out == {"redirect_url": "https://pay.example/c/1", "order": "SEE-00001", "total_cents": 75000}
    order = world.orders[0]
    assert order["channel"] == "tickets" and order["fulfilment"] == "collection" and order["customer_phone"] == "27821234567"
    success, cancel = world.returns[0]
    assert success == f"https://seedleme.shop.vula-ai.com/tickets/{order_slip.token_for(order['id'])}"
    assert cancel == f"https://seedleme.shop.vula-ai.com/events/{ev['id']}?cancelled=1"
    now = events.get(TID, ev["id"])
    general = now["types"][0]
    assert general["left"] == 3 and general["sold"] == 0 and general["held"] == 2      # held, not yet paid
    assert world.db.rows["commerce_tickets"] == []                                    # no ticket before payment


@pytest.mark.parametrize("items, kw, status", [
    ([], {}, 400), ([{"type_id": "nope", "quantity": 1}], {}, 400),
    ([{"type_id": "G", "quantity": 5}], {}, 400),                       # more than 4 per order
    ([{"type_id": "G", "quantity": 1.5}], {}, 400), ([{"type_id": "G", "quantity": -1}], {}, 400),
    ([{"type_id": "V", "quantity": 1}], {"name": "T"}, 400), ([{"type_id": "V", "quantity": 1}], {"phone": "12"}, 400),
    ([{"type_id": "V", "quantity": 1}], {"email": "not-an-email"}, 400),
])
def test_refused_purchases_make_no_order(world, items, kw, status):
    ev, types = _on_sale()
    ids = {"G": types["General"]["id"], "V": types["VIP"]["id"]}
    items = [{**i, "type_id": ids.get(i["type_id"], i["type_id"])} for i in items]
    with pytest.raises(events.EventError) as e:
        _buy(ev, items, **kw)
    assert e.value.status == status and world.orders == []


def test_sold_out_and_closed_sales(world):
    ev, types = _on_sale()
    vip = [{"type_id": types["VIP"]["id"], "quantity": 2}]
    _buy(ev, vip)
    with pytest.raises(events.EventError) as e:
        _buy(ev, [{"type_id": types["VIP"]["id"], "quantity": 1}])
    assert e.value.status == 409 and "sold out" in str(e.value)
    run(events.set_status(TID, ev["id"], "closed"))
    with pytest.raises(events.EventError) as e:
        _buy(ev, [{"type_id": types["General"]["id"], "quantity": 1}])
    assert e.value.status == 409 and "closed" in str(e.value)
    draft, dtypes = _event()
    with pytest.raises(events.EventError) as e:
        _buy(draft, [{"type_id": dtypes["General"]["id"], "quantity": 1}])
    assert e.value.status == 404


def test_a_gateway_failure_is_passed_on_in_plain_words(world):
    ev, types = _on_sale()
    world.fail = 502
    with pytest.raises(events.EventError) as e:
        _buy(ev, [{"type_id": types["General"]["id"], "quantity": 1}])
    assert e.value.status == 502 and "try again" in str(e.value)


# ── tickets ───────────────────────────────────────────────────────────────────────────────────
def test_a_paid_order_gets_one_ticket_per_seat_once(world):
    ev, types = _on_sale()
    _buy(ev, [{"type_id": types["General"]["id"], "quantity": 3}, {"type_id": types["VIP"]["id"], "quantity": 1}])
    made = _pay(world)
    assert len(made) == 4 and len({k["code"] for k in made}) == 4
    assert all(events.clean_code(k["code"]) == k["code"] and k["holder_name"] == "Thandi Mokoena" for k in made)
    assert world.told == [(world.orders[0]["id"], 4)]
    assert _pay(world) == [] and len(world.db.rows["commerce_tickets"]) == 4 and len(world.told) == 1   # again: nothing new
    now = events.get(TID, ev["id"])
    assert now["sold"] == 4 and now["types"][0]["sold"] == 3 and now["types"][0]["held"] == 0


def test_the_order_hooks_issue_and_void(world, monkeypatch):
    ev, types = _on_sale()
    _buy(ev, [{"type_id": types["General"]["id"], "quantity": 2}])
    order = world.orders[0]
    monkeypatch.setattr("vula.commerce.ledger.post_order_paid", lambda *a, **k: None)
    monkeypatch.setattr(service, "_settle_order_invoice", lambda *a, **k: None)
    assert run(service.mark_order_paid(TID, order["id"], "online"))["status"] == "paid"
    assert len(world.db.rows["commerce_tickets"]) == 2
    assert run(service.mark_order_paid(TID, order["id"], "online")) is None            # a second notification
    assert len(world.db.rows["commerce_tickets"]) == 2
    run(service._tickets_after({**order, "status": "refunded", "refunded_amount_cents": 10000}, "refunded"))
    assert {k["status"] for k in world.db.rows["commerce_tickets"]} == {"valid"}       # part refund: still good
    run(service._tickets_after({**order, "refunded_amount_cents": order["total_cents"]}, "refunded"))
    assert {k["status"] for k in world.db.rows["commerce_tickets"]} == {"void"}
    assert events.get(TID, ev["id"])["sold"] == 0


def test_the_tickets_page(world):
    ev, types = _on_sale()
    _buy(ev, [{"type_id": types["General"]["id"], "quantity": 2}])
    order = world.orders[0]
    token = order_slip.token_for(order["id"])
    waiting = run(events.order_tickets(token))
    assert waiting["paid"] is False and waiting["tickets"] == []
    page = run(events_api.tickets_page(token)).body.decode()
    assert "Waiting for your payment" in page and 'http-equiv="refresh"' in page
    order["status"] = "paid"                                    # the hook didn't run: the page issues them
    data = run(events.order_tickets(token))
    assert len(data["tickets"]) == 2 and data["tickets"][0]["event"]["title"] == "Summer Market"
    page = run(events_api.tickets_page(token)).body.decode()
    assert page.count("<svg") >= 2 and "ticket 1 of 2" in page and "noindex" in page
    assert "ticket 1 of 2" in page and page.count("Send this ticket on WhatsApp") == 2     # one each, to pass on
    code = data["tickets"][0]["code"]
    assert data["tickets"][0]["url"] == f"https://seedleme.shop.vula-ai.com/ticket/{code}"
    one = run(events_api.ticket_page(code)).body.decode()
    assert one.count("<svg") >= 1 and "Send this ticket" not in one and "Thandi" not in one and "noindex" in one
    events.check_in(TID, data["tickets"][0]["event"]["id"], code)
    assert "Already used" in run(events_api.ticket_page(code)).body.decode()
    with pytest.raises(Exception):
        run(events_api.ticket_page("VT-" + "z" * 22))
    assert run(events.order_tickets("not-a-token")) is None
    with pytest.raises(Exception):
        run(events_api.tickets_page("not-a-token"))


# ── the door ──────────────────────────────────────────────────────────────────────────────────
def test_a_ticket_gets_one_person_in_once(world):
    ev, types = _on_sale()
    _buy(ev, [{"type_id": types["General"]["id"], "quantity": 2}])
    a, b = _pay(world)
    first = events.check_in(TID, ev["id"], a["code"], "door@x.co.za")
    assert first["result"] == "ok" and first["ticket"]["type"] == "General" and first["ticket"]["holder_name"] == "Thandi Mokoena"
    again = events.check_in(TID, ev["id"], f"https://x/t/{a['code']}", "other@x.co.za")
    assert again["result"] == "already" and again["ticket"]["checked_in_by"] == "door@x.co.za"
    assert events.check_in(TID, ev["id"], "VT-" + "x" * 22)["result"] == "unknown"
    assert events.check_in(TID, ev["id"], "'; drop table")["result"] == "unknown"
    other, _ = _event(title="Other night")
    assert events.check_in(TID, other["id"], b["code"])["result"] == "wrong_event"
    with pytest.raises(events.EventError):
        events.check_in("someone_else", ev["id"], b["code"])                          # not their event
    events.void_for_order(TID, world.orders[0]["id"], "refunded")
    assert events.check_in(TID, ev["id"], b["code"])["result"] == "void"


def test_undo_and_the_door_list(world):
    ev, types = _on_sale()
    _buy(ev, [{"type_id": types["General"]["id"], "quantity": 2}])
    a, b = _pay(world)
    events.check_in(TID, ev["id"], a["code"], "door@x.co.za")
    lst = events.attendees(TID, ev["id"])
    assert lst["event"]["sold"] == 2 and lst["event"]["checked_in"] == 1 and lst["event"]["takings_cents"] == 30000
    assert all("code" not in r for r in lst["attendees"]) and {r["order"] for r in lst["attendees"]} == {"SEE-00001"}
    assert events.undo_check_in(TID, ev["id"], a["id"])["status"] == "valid"
    with pytest.raises(events.EventError):
        events.undo_check_in(TID, ev["id"], a["id"])
    assert events.check_in(TID, ev["id"], a["code"])["result"] == "ok"


# ── the public page ───────────────────────────────────────────────────────────────────────────
def test_the_event_page_escapes_and_shows_what_is_left(world, monkeypatch):
    async def inv(tid):
        return {"trading_as": "SeedleMe", "accent_color": "0a7d5c"}
    monkeypatch.setattr(service, "get_invoice_settings", inv)
    ev, types = _event(title="Jazz <b>Night</b> {{BODY}}", description="Bring a chair.\n<script>x</script>")
    with pytest.raises(Exception):
        run(events_api.event_page(ev["id"]))                                          # draft: 404
    run(events.set_status(TID, ev["id"], "on_sale"))
    _buy(ev, [{"type_id": types["VIP"]["id"], "quantity": 2}])
    page = run(events_api.event_page(ev["id"])).body.decode()
    assert "Jazz &lt;b&gt;Night&lt;/b&gt;" in page and "<script>x</script>" not in page and "{{" not in page
    assert "Sold out" in page and "5 left" in page and "R150.00" in page and "#0a7d5c" in page
    assert '"@type": "Event"' in page and 'id="buy"' in page
    run(events.set_status(TID, ev["id"], "cancelled"))
    page = run(events_api.event_page(ev["id"])).body.decode()
    assert "has been cancelled" in page and 'id="buy"' not in page
    assert events_api.when_text("2026-12-05T16:00:00+00:00", "2026-12-05T20:00:00+00:00") == "Sat 5 Dec 2026, 18:00 – 22:00"
