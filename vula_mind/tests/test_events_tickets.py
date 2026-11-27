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
                             "commerce_product_variants",
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


# ── the door link ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def door(world, monkeypatch):
    monkeypatch.setattr(tenants, "dashboard_url_for", lambda tid: f"https://{tid}.vula-ai.com")
    ev, types = _on_sale()
    _buy(ev, [{"type_id": types["General"]["id"], "quantity": 3}])
    tickets = _pay(world)
    link = events.door_link(TID, ev["id"])
    return SimpleNamespace(ev=ev, tickets=tickets, key=link["url"].split("#")[1], url=link["url"], world=world)


def test_the_door_link_shows_names_and_hashes_never_codes_phones_or_money(door):
    assert door.url.startswith("https://seedleme.vula-ai.com/door/#D") and events.door_status(TID, door.ev["id"])["on"]
    assert events.door_link(TID, door.ev["id"])["url"] == door.url                       # the same link again
    lst = events.door_list(door.key)
    assert lst["sold"] == 3 and lst["checked_in"] == 0 and lst["event"]["title"] == "Summer Market"
    text = str(lst)
    assert all(k["code"] not in text for k in door.tickets) and "cents" not in text
    # the phone number is looked for outside the random ids and hashes, whose hex can hold "2782" by chance
    plain = str({**lst, "tickets": [{k: v for k, v in r.items() if k not in ("id", "h")} for r in lst["tickets"]]})
    assert "2782" not in plain
    assert {r["h"] for r in lst["tickets"]} == {events._code_hash(k["code"]) for k in door.tickets}
    assert set(lst["tickets"][0]) == {"id", "h", "name", "type", "status", "at", "by"}


def test_the_door_link_lets_tickets_in_by_scan_or_from_the_list(door):
    a, b, c = door.tickets
    ok = events.door_check_in(door.key, {"code": a["code"]}, "Gate A")
    assert ok["result"] == "ok" and ok["ticket"]["checked_in_by"] == "Door: Gate A"
    assert events.door_check_in(door.key, {"code": a["code"]}, "Gate B")["result"] == "already"
    assert events.door_check_in(door.key, {"ticket_id": b["id"]}, "")["ticket"]["checked_in_by"] == "Door"
    assert events.door_check_in(door.key, {"ticket_id": "not-a-ticket"})["result"] == "unknown"
    assert events.door_check_in(door.key, {})["result"] == "unknown"
    assert events.door_list(door.key)["checked_in"] == 2


def test_check_ins_made_with_no_signal_are_sent_later_once_each(door):
    a, b, c = door.tickets
    events.check_in(TID, door.ev["id"], b["code"], "owner@x.co.za")                      # another door got there first
    when = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    out = events.door_sync(door.key, [{"ref": "1", "code": a["code"], "at": when},
                                      {"ref": "2", "code": b["code"], "at": when},
                                      {"ref": "3", "ticket_id": c["id"], "at": "2099-01-01T00:00:00+00:00"},
                                      {"ref": "4", "code": "VT-" + "q" * 22}, "junk"], "Gate A")
    assert [(r["ref"], r["result"]) for r in out["results"]] == [("1", "ok"), ("2", "already"), ("3", "ok"), ("4", "unknown")]
    assert out["results"][1]["ticket"]["checked_in_by"] == "owner@x.co.za"
    rows = {k["id"]: k for k in door.world.db.rows["commerce_tickets"]}
    assert rows[a["id"]]["checked_in_at"] == when                                    # when the phone scanned it
    assert rows[c["id"]]["checked_in_at"] != "2099-01-01T00:00:00+00:00"               # a time in the future isn't believed
    again = events.door_sync(door.key, [{"ref": "1", "code": a["code"], "at": when}], "Gate A")
    assert again["results"][0]["result"] == "already"                                 # sent twice: still one entry
    with pytest.raises(events.EventError):
        events.door_sync(door.key, [{}] * 501)


def test_a_door_link_stops_working(door):
    for bad in ("", "D" + "x" * 30, "'; drop", door.key[:-1]):
        with pytest.raises(events.EventError) as e:
            events.door_list(bad)
        assert e.value.status == 404
    renewed = events.door_link(TID, door.ev["id"], new=True)["url"].split("#")[1]
    assert renewed != door.key and events.door_list(renewed)["sold"] == 3
    with pytest.raises(events.EventError):
        events.door_list(door.key)                                                    # the old link is dead
    events.door_off(TID, door.ev["id"])
    with pytest.raises(events.EventError):
        events.door_check_in(renewed, {"code": door.tickets[0]["code"]})
    key = events.door_link(TID, door.ev["id"])["url"].split("#")[1]
    ev_row = door.world.db.rows["commerce_events"][0]
    ev_row["starts_at"] = ev_row["ends_at"] = (datetime.now(timezone.utc) - timedelta(hours=13)).isoformat()
    with pytest.raises(events.EventError):
        events.door_list(key)                                                         # 12 hours after it ended
    draft, _ = _event(title="Not yet")
    with pytest.raises(events.EventError):
        events.door_link(TID, draft["id"])
    with pytest.raises(events.EventError):
        events.door_link("someone_else", door.ev["id"])


# ── tickets in the WhatsApp chat ──────────────────────────────────────────────────────────────
@pytest.fixture
def chat(world, monkeypatch):
    from vula.api import whatsapp as wa
    from vula.commerce import event_chat
    sent, asked, closed = [], [], []

    async def resolve(tid):
        return {"phone_id": "1", "token": "t"}

    async def send_list(creds, number, header, body, footer, button, sections):
        sent.append(("list", body, [r for sec in sections for r in sec["rows"]]))
        return True

    async def cta(creds, number, body, label, url, image_url=None):
        sent.append(("cta", body, label, url))
        return True

    async def reply(phone, body, tenant_id=None, **k):
        sent.append(("text", body))

    monkeypatch.setattr(wa, "_resolve_wa", resolve)
    monkeypatch.setattr(wa, "_send_wa_list", send_list)
    monkeypatch.setattr(wa, "_send_wa_cta_url", cta)
    monkeypatch.setattr(wa, "_send_reply", reply)
    monkeypatch.setattr("vula.open_questions.ask", lambda t, p, kind, ref, prompt="": asked.append((kind, ref)) or "q1")
    monkeypatch.setattr("vula.open_questions.close", lambda qid, answer="", status="answered": closed.append((qid, answer)))
    known = {}

    async def profile(tid, phone):
        return known.get(phone)

    monkeypatch.setattr(service, "get_customer_profile", profile)
    return SimpleNamespace(sent=sent, asked=asked, closed=closed, known=known, ec=event_chat, world=world)


def test_what_counts_as_asking_for_tickets():
    from vula.commerce import event_chat as ec
    for yes in ("tickets", "Can I get 2 tickets please", "Kaartjies?", "ticket for saturday"):
        assert ec.is_ticket_request(yes)
    for no in ("", "what time do you open", "x" * 200 + " tickets"):
        assert not ec.is_ticket_request(no)
    assert ec.is_my_tickets("where are my tickets") and ec.is_my_tickets("I didn't get my ticket")
    assert not ec.is_my_tickets("2 tickets please")
    assert ec.clean_name("my name is Thandi Mokoena.") == "Thandi Mokoena" and ec.clean_name("Pieter van der Merwe")
    for bad in ("how much are they?", "2", "see https://x.co.za", "ok thanks I will pay tomorrow morning then", ""):
        assert ec.clean_name(bad) == ""


def test_nothing_on_sale_leaves_the_message_to_the_assistant(chat):
    assert run(chat.ec.start("27821234567", TID, "tickets")) is False and chat.sent == []
    assert run(chat.ec.start("27821234567", TID, "what time do you open")) is False


def test_buying_in_the_chat_step_by_step(chat):
    ev, types = _on_sale()
    gid = types["General"]["id"]
    assert run(chat.ec.start("27821234567", TID, "Hi, tickets please")) is True
    kind, body, rows = chat.sent[-1]
    assert kind == "list" and "Summer Market" in body                       # one event: straight to its tickets
    assert {r["id"] for r in rows} == {f"evt_t_{t['id']}" for t in types.values()}
    assert next(r for r in rows if r["id"] == f"evt_t_{gid}")["description"] == "R150.00 · 5 left"
    assert run(chat.ec.handle_tap("27821234567", TID, f"evt_t_{gid}")) is True
    rows = chat.sent[-1][2]
    assert [r["id"] for r in rows] == [f"evt_q_{gid}_{n}" for n in (1, 2, 3, 4)]     # 4 per order
    assert rows[2] == {"id": f"evt_q_{gid}_3", "title": "3 tickets", "description": "R450.00"}
    assert run(chat.ec.handle_tap("27821234567", TID, f"evt_q_{gid}_3")) is True     # a new customer: the name first
    assert chat.asked == [("ticket_name", f"{gid}:3")] and chat.world.orders == []
    q = {"id": "q1", "ref_id": f"{gid}:3"}
    assert run(chat.ec.take_name("27821234567", TID, "how much is that?", q)) is False and chat.world.orders == []
    assert run(chat.ec.take_name("27821234567", TID, "Thandi Mokoena", q)) is True
    order = chat.world.orders[0]
    assert order["customer_name"] == "Thandi Mokoena" and order["total_cents"] == 45000 and order["channel"] == "tickets"
    kind, body, label, url = chat.sent[-1]
    assert kind == "cta" and label == "Pay R450.00" and url == "https://pay.example/c/1" and "R450.00" in body
    assert chat.closed == [("q1", "Thandi Mokoena")]


def test_a_returning_customer_is_not_asked_their_name_and_several_events_are_listed(chat):
    ev, types = _on_sale()
    other, _ = _event(title="Winter Market", starts_at=_soon(20), ends_at=_soon(20, 4))
    run(events.set_status(TID, other["id"], "on_sale"))
    chat.known["27821234567"] = {"name": "Thandi Mokoena"}
    run(chat.ec.start("27821234567", TID, "tickets"))
    assert [r["id"] for r in chat.sent[-1][2]] == [f"evt_e_{ev['id']}", f"evt_e_{other['id']}"]     # soonest first
    run(chat.ec.handle_tap("27821234567", TID, f"evt_e_{other['id']}"))
    assert "Winter Market" in chat.sent[-1][1]
    run(chat.ec.handle_tap("27821234567", TID, f"evt_q_{types['VIP']['id']}_2"))
    assert chat.asked == [] and chat.world.orders[0]["total_cents"] == 90000


def test_refused_taps_in_the_chat(chat):
    ev, types = _on_sale()
    gid = types["General"]["id"]
    chat.known["27821234567"] = {"name": "Thandi Mokoena"}
    assert run(chat.ec.handle_tap("27821234567", TID, f"evt_q_{gid}_99")) is False             # not a number we offered
    run(chat.ec.handle_tap("27821234567", TID, f"evt_q_{gid}_10"))                             # more than 4 per order
    assert chat.world.orders == [] and "up to 4" in chat.sent[-1][1]
    run(chat.ec.handle_tap("27821234567", "someone_else", f"evt_t_{gid}"))                     # another business's line
    assert "aren't on sale" in chat.sent[-1][1]
    run(events.set_status(TID, ev["id"], "closed"))
    run(chat.ec.handle_tap("27821234567", TID, f"evt_q_{gid}_1"))
    assert chat.world.orders == [] and "aren't on sale" in chat.sent[-1][1]


def test_my_tickets_sends_back_what_this_number_paid_for(chat):
    ev, types = _on_sale()
    _buy(ev, [{"type_id": types["General"]["id"], "quantity": 2}])
    chat.world.orders[-1]["status"] = "paid"
    run(events.issue_for_order(TID, chat.world.orders[-1]))
    chat.sent.clear()
    assert run(chat.ec.start("0821234567", TID, "where are my tickets?")) is True
    kind, body, label, url = chat.sent[-1]
    assert kind == "cta" and label == "View tickets" and "2 tickets" in body and "/tickets/" in url
    chat.sent.clear()
    run(chat.ec.start("27839999999", TID, "where are my tickets?"))                            # someone else's number
    assert all("/tickets/" not in str(m) for m in chat.sent)


# ── the reminder and the thank-you ────────────────────────────────────────────────────────────
@pytest.fixture
def told(world, monkeypatch):
    out = []

    async def message(tenant_id, order, text, news, button, link):
        out.append({"order": order["display_id"], "text": text, "news": news, "button": button, "link": link})
        return True

    monkeypatch.setattr(events, "_message_buyer", message)
    monkeypatch.setattr(tenants, "display_name", lambda tid: "SeedleMe")
    monkeypatch.setattr("vula.consent.opted_out", lambda tid, phone: phone.endswith("9999"))
    return out


def _sold(world, start_in_hours, phones=("082 123 4567",), qty=2, **kw):
    ev, types = _event(title=kw.pop("title", "Summer Market"),
                       starts_at=(datetime.now(timezone.utc) + timedelta(hours=start_in_hours)).isoformat(),
                       ends_at=(datetime.now(timezone.utc) + timedelta(hours=start_in_hours + 3)).isoformat(), **kw)
    run(events.set_status(TID, ev["id"], "on_sale"))
    for ph in phones:
        _buy(ev, [{"type_id": types["General"]["id"], "quantity": qty}], phone=ph)
        _pay(world)
    return ev


def test_the_reminder_goes_once_the_day_before(world, told):
    ev = _sold(world, 20, phones=("082 123 4567", "083 555 0000"))
    far = _sold(world, 24 * 5, title="Far off")
    assert run(events.send_reminders()) == 2
    assert {m["order"] for m in told} == {"SEE-00001", "SEE-00002"}
    m = told[0]
    assert "Summer Market is on" in m["news"] and "The Yard" in m["news"] and "2 tickets" in m["text"]
    assert m["button"] == "View tickets" and "/tickets/" in m["link"] and m["text"].endswith("— SeedleMe")
    assert run(events.send_reminders()) == 0 and len(told) == 2                      # never twice
    assert all(k["reminded_at"] for k in world.db.rows["commerce_tickets"] if k["event_id"] == ev["id"])
    assert not any(k.get("reminded_at") for k in world.db.rows["commerce_tickets"] if k["event_id"] == far["id"])


def test_no_reminder_when_switched_off_or_the_ticket_is_void(world, told):
    _sold(world, 20, remind_buyers=False)
    ev = _sold(world, 20, title="Second")
    events.void_for_order(TID, world.orders[-1]["id"], "refunded")
    assert run(events.send_reminders()) == 0 and told == []


def test_the_thank_you_goes_to_those_who_came_with_the_next_event(world, told):
    nxt = _sold(world, 24 * 7, title="Winter Market", phones=())
    ev = _sold(world, 5, phones=("082 123 4567", "083 555 0000", "084 000 9999"), qty=1)
    came, stayed_home, opted_out = world.orders[-3:]
    for o in (came, opted_out):
        k = next(t for t in world.db.rows["commerce_tickets"] if t["order_id"] == o["id"])
        events.check_in(TID, ev["id"], k["code"], "door")
    after = datetime.now(timezone.utc) + timedelta(hours=5 + 3 + 6)                  # six hours after it ended
    assert run(events.send_thanks(after)) == 1
    m = told[0]
    assert m["order"] == came["display_id"] and "Thank you for coming to Summer Market" in m["text"]
    assert "Next up: Winter Market" in m["text"] and m["link"].endswith(f"/events/{nxt['id']}")
    assert run(events.send_thanks(after)) == 0 and len(told) == 1
    assert run(events.send_thanks(datetime.now(timezone.utc) + timedelta(hours=5 + 3 + 1))) == 0     # too soon


# ── photos and things sold with the tickets ───────────────────────────────────────────────────
def _product(world, name, price=25000, **kw):
    row = {"id": str(uuid.uuid4()), "tenant_id": kw.pop("tenant_id", TID), "name": name, "price_cents": price,
           "in_stock": True, "status": "active", "stock_quantity": kw.pop("stock", 8), **kw}
    world.db.rows["commerce_products"].append(row)
    return row


def test_photos_are_https_the_first_is_the_cover_and_six_is_the_most(world):
    ev, _ = _event(images=["https://cdn.example/a.jpg", "https://cdn.example/b.jpg", "https://cdn.example/a.jpg"])
    assert ev["images"] == ["https://cdn.example/a.jpg", "https://cdn.example/b.jpg"] and ev["image_url"] == "https://cdn.example/a.jpg"
    assert run(events.update(TID, ev["id"], {"images": []}))["image_url"] is None
    for bad in (["http://plain.example/a.jpg"], ["javascript:alert(1)"], ["https://x/%d.jpg" % n for n in range(7)], "x"):
        with pytest.raises(events.EventError):
            _event(images=bad)


def test_only_the_business_own_sellable_products_are_offered_with_tickets(world):
    tee = _product(world, "Market T-shirt", 25000, image_url="https://cdn.example/tee.jpg")
    parking = _product(world, "Parking", 5000, stock=None)                      # stock not counted
    sale = _product(world, "Tote bag", 12000, sale_price_cents=9000)
    gone = _product(world, "Sold out cap", 9000, stock=0, in_stock=False)
    draft = _product(world, "Not listed yet", 9000, status="draft")
    by_kg = _product(world, "Biltong", 40000, sold_by="kg")
    sized = _product(world, "Hoodie", 60000)
    world.db.rows["commerce_product_variants"].append({"id": "v1", "tenant_id": TID, "product_id": sized["id"], "archived": False})
    theirs = _product(world, "Someone else's", 100, tenant_id="other")
    with pytest.raises(events.EventError, match="product list"):
        _event(addon_product_ids=[theirs["id"]])
    ev, types = _event(addon_product_ids=[p["id"] for p in (tee, parking, sale, gone, draft, by_kg, sized)])
    with pytest.raises(events.EventError):                                         # a ticket is not merch
        run(events.update(TID, ev["id"], {"addon_product_ids": [world.db.rows["commerce_products"][-1]["id"]]}))
    run(events.set_status(TID, ev["id"], "on_sale"))
    offered = events.public_event(ev["id"])["addons"]
    assert [(a["name"], a["price_cents"], a["max"]) for a in offered] == [
        ("Market T-shirt", 25000, 8), ("Parking", 5000, 20), ("Tote bag", 9000, 8)]
    assert offered[0]["image"] == "https://cdn.example/tee.jpg"


def test_merch_goes_on_the_same_order_and_only_tickets_become_tickets(world, monkeypatch):
    async def inv(tid):
        return {"trading_as": "SeedleMe"}
    monkeypatch.setattr(service, "get_invoice_settings", inv)
    tee = _product(world, "Market <b>T-shirt</b>", 25000, stock=3)
    other = _product(world, "Not offered", 100)
    ev, types = _event(addon_product_ids=[tee["id"]], images=["https://cdn.example/a.jpg", "https://cdn.example/b.jpg"])
    run(events.set_status(TID, ev["id"], "on_sale"))
    gen = [{"type_id": types["General"]["id"], "quantity": 2}]
    page = run(events_api.event_page(ev["id"])).body.decode()
    assert "Add to your order" in page and "Market &lt;b&gt;T-shirt&lt;/b&gt;" in page and f'data-pid="{tee["id"]}"' in page
    assert page.count("<img src=") == 2 and 'class="gal"' in page                    # both photos
    for bad, status in (([{"product_id": other["id"], "quantity": 1}], 400), ([{"product_id": tee["id"], "quantity": 4}], 409),
                        ([{"product_id": tee["id"], "quantity": 1.5}], 400), ("x", 400)):
        with pytest.raises(events.EventError) as e:
            run(events.buy(ev["id"], items=gen, name="Thandi Mokoena", phone="0821234567", addons=bad))
        assert e.value.status == status and world.orders == []
    with pytest.raises(events.EventError):                                         # merch alone isn't a ticket sale
        run(events.buy(ev["id"], items=[], name="Thandi Mokoena", phone="0821234567",
                       addons=[{"product_id": tee["id"], "quantity": 1}]))
    out = run(events.buy(ev["id"], items=gen, name="Thandi Mokoena", phone="0821234567",
                         addons=[{"product_id": tee["id"], "quantity": 2}]))
    assert out["total_cents"] == 2 * 15000 + 2 * 25000
    assert next(p for p in world.db.rows["commerce_products"] if p["id"] == tee["id"])["stock_quantity"] == 1
    assert len(_pay(world)) == 2                                                    # two tickets; the shirts are not tickets
