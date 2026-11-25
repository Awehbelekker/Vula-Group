"""Service at the table (vula/tap/tableside.py, 24 Nov 2026): a customer calls the waiter or orders
from the table's tag; the waiter accepts and confirms; a confirmed order's lines become the table's
bill, which is paid through the flow that already exists. Made-up names."""
from datetime import timedelta

import pytest

from tests.tap_fakes import Clock, FakeGateway, FakeMessenger, MemoryRepo
from vula.tap import express_page
from vula.tap.service import TapConfig, TapService
from vula.tap.tableside import TableError

T = "the-yard"
THANDI, PIETER, BOSS = "w-thandi", "w-pieter", "m-boss"


class Pusher:
    def __init__(self):
        self.sent = []

    async def send(self, sub, payload):
        self.sent.append((sub["member_id"], payload["title"], payload["body"]))
        return "ok"


@pytest.fixture
def env():
    clock = Clock()
    repo = MemoryRepo(clock)
    msg, gw, push = FakeMessenger(), FakeGateway(), Pusher()
    svc = TapService(repo, msg, gw, TapConfig(pepper="pep", public_base_url="https://api.test",
                                              encrypt=lambda s: "enc:" + s, decrypt=lambda s: s[4:], clock=clock))
    svc.pusher = push
    repo.members[T] = [{"id": THANDI, "name": "Thandi Mokoena", "role": "staff"},
                       {"id": PIETER, "name": "Pieter Botha", "role": "staff"},
                       {"id": BOSS, "name": "Aisha Davids", "role": "owner"}]
    for m in (THANDI, PIETER, BOSS):
        repo.subs[f"https://push/{m}"] = {"endpoint": f"https://push/{m}", "member_id": m, "tenant_id": T,
                                          "p256dh": "k", "auth": "a"}
    repo.team[(T, THANDI)] = ["27800000001"]
    repo.team[(T, None)] = ["27800000009"]
    repo.products = [
        {"id": "p-fish", "tenant_id": T, "name": "Line fish", "price_cents": 16500, "category": "mains", "in_stock": True},
        {"id": "p-coke", "tenant_id": T, "name": "Coke", "price_cents": 3000, "sale_price_cents": 2500, "category": "drinks", "in_stock": True},
        {"id": "p-kg", "tenant_id": T, "name": "Biltong", "price_cents": 40000, "sold_by": "kg", "in_stock": True},
        {"id": "p-draft", "tenant_id": T, "name": "Not yet", "price_cents": 100, "status": "draft", "in_stock": True},
        {"id": "p-tkt", "tenant_id": T, "name": "A ticket", "price_cents": 100, "product_type": "ticket", "in_stock": True},
        {"id": "p-other", "tenant_id": "someone-else", "name": "Theirs", "price_cents": 100, "in_stock": True}]
    svc.tables.save_settings(T, calls=True, orders=True)
    table = svc.tables.create_table(T, "Table 7", THANDI)
    return svc, repo, msg, push, table, clock


def code_of(table):
    return table["url"].rsplit("/", 1)[1]


CUST, OTHER = "browser-one", "browser-two"


# ── set-up ────────────────────────────────────────────────────────────────────────────────────
def test_tables_are_tags_with_a_name_and_a_waiter(env):
    svc, repo, *_ , table, _ = env
    assert table["label"] == "Table 7" and table["waiter"] == "Thandi" and table["url"].startswith("https://api.test/t/")
    svc.tables.create_table(T, "Table 10")
    svc.tables.create_table(T, "Table 2")
    assert [t["label"] for t in svc.tables.tables(T)] == ["Table 2", "Table 7", "Table 10"]     # by number, not by letter
    for bad, says in ((" ", "name"), ("table 7", "already")):
        with pytest.raises(TableError, match=says):
            svc.tables.create_table(T, bad)
    with pytest.raises(TableError, match="Unknown"):
        svc.tables.create_table(T, "Table 99", "nobody")
    assert svc.tables.assign(T, table["id"], PIETER)["waiter"] == "Pieter"
    with pytest.raises(TableError):
        svc.tables.assign("someone-else", table["id"], PIETER)                              # not their table
    with pytest.raises(TableError):
        svc.tables.save_settings(T, escalate_minutes=0)


# ── calls ─────────────────────────────────────────────────────────────────────────────────────
async def test_a_call_reaches_the_tables_waiter_once(env):
    svc, repo, msg, push, table, _ = env
    c = await svc.tables.call(code_of(table), CUST, "bill")
    assert c["status"] == "open" and c["kind"] == "bill"
    assert push.sent == [(THANDI, "Table 7", "Table 7 is asking for the bill.")]
    assert msg.to("27800000001", "text")[-1][3] == "Table 7: Table 7 is asking for the bill."
    again = await svc.tables.call(code_of(table), OTHER, "bill")                             # the table asks again
    assert again["id"] == c["id"] and len(push.sent) == 1
    await svc.tables.call(code_of(table), CUST, "waiter")                                     # a different thing is a new call
    assert len(push.sent) == 2
    for bad_kind in ("", "champagne"):
        with pytest.raises(TableError):
            await svc.tables.call(code_of(table), CUST, bad_kind)
    with pytest.raises(TableError):
        await svc.tables.call("no-such-tag", CUST, "bill")


async def test_calls_are_off_unless_the_business_switched_them_on(env):
    svc, repo, msg, push, table, _ = env
    svc.tables.save_settings(T, calls=False)
    with pytest.raises(TableError, match="wave someone"):
        await svc.tables.call(code_of(table), CUST, "waiter")
    assert push.sent == [] and svc.tables.calls_for(T, THANDI, False) == []
    svc.tables.save_settings(T, orders=False)
    assert svc.tables.page(code_of(table), CUST) is None                                      # nothing to show


async def test_accepting_has_one_winner_and_only_the_right_people_see_a_call(env):
    svc, repo, msg, push, table, _ = env
    c = await svc.tables.call(code_of(table), CUST, "waiter")
    assert [x["table"] for x in svc.tables.calls_for(T, THANDI, False)] == ["Table 7"]
    assert svc.tables.calls_for(T, PIETER, False) == []                                       # not Pieter's table
    assert len(svc.tables.calls_for(T, BOSS, True)) == 1
    got = svc.tables.accept(T, c["id"], THANDI)
    assert got["status"] == "accepted" and got["accepted_by"] == "Thandi"
    with pytest.raises(TableError, match="Thandi already has this one"):
        svc.tables.accept(T, c["id"], PIETER)
    page = svc.tables.page(code_of(table), CUST)
    assert page["calls"] == [{"kind": "waiter", "status": "accepted", "by": "Thandi"}]
    svc.tables.done(T, c["id"], THANDI)
    assert svc.tables.calls_for(T, BOSS, True) == []
    with pytest.raises(TableError):
        svc.tables.done(T, c["id"], THANDI)
    with pytest.raises(TableError):
        svc.tables.accept("someone-else", c["id"], THANDI)


async def test_a_call_nobody_answers_goes_to_the_managers_once(env):
    svc, repo, msg, push, table, clock = env
    await svc.tables.call(code_of(table), CUST, "bill")
    clock.t += timedelta(minutes=2)
    assert await svc.tables.sweep(T) == 0                                                     # not yet
    clock.t += timedelta(minutes=2)
    assert await svc.tables.sweep(T) == 1
    assert push.sent[-1][0] == BOSS and "waited 4 minutes" in push.sent[-1][2] and "Table 7" in push.sent[-1][2]
    assert "Nobody has answered" in msg.to("27800000009", "text")[-1][3]
    assert await svc.tables.sweep(T) == 0 and len(push.sent) == 2                             # never twice
    fresh = await svc.tables.call(code_of(table), CUST, "waiter")
    svc.tables.accept(T, fresh["id"], THANDI)
    clock.t += timedelta(minutes=10)
    assert await svc.tables.sweep(T) == 0                                                     # an accepted call isn't passed up


async def test_a_table_nobody_is_serving_calls_the_managers(env):
    svc, repo, msg, push, table, _ = env
    svc.tables.assign(T, table["id"], None)
    await svc.tables.call(code_of(table), CUST, "waiter")
    assert [p[0] for p in push.sent] == [BOSS]
    assert len(svc.tables.calls_for(T, PIETER, False)) == 1                                   # any waiter can pick it up


# ── the menu and orders ───────────────────────────────────────────────────────────────────────
def test_the_menu_is_the_business_own_sellable_products_at_todays_price(env):
    svc, *_ = env
    assert [(m["name"], m["price_cents"], m["category"]) for m in svc.tables.menu(T)] == [
        ("Line fish", 16500, "Mains"), ("Coke", 2500, "Drinks")]                              # the sale price; no kg, draft, ticket


async def test_an_order_is_priced_by_the_server_and_owes_nothing_until_confirmed(env):
    svc, repo, msg, push, table, _ = env
    o = await svc.tables.order(code_of(table), CUST, [{"product_id": "p-fish", "quantity": 2, "unit_cents": 1},
                                                      {"product_id": "p-coke", "quantity": 3}], "  No onions\n please ")
    assert o["total_cents"] == 2 * 16500 + 3 * 2500 and o["status"] == "pending" and o["note"] == "No onions please"
    assert push.sent[-1] == (THANDI, "New order, Table 7", "2 x Line fish, 3 x Coke (R 405.00). Confirm it in the app.")
    assert svc.tables.bill_view(T, repo.get_tag(T, table["id"])) is None                       # no bill yet
    assert svc.express.view(code_of(table), "cookie")["kind"] == "no_bill"
    page = svc.tables.page(code_of(table), CUST)
    assert page["orders"] == [{"status": "pending", "total_cents": 40500, "summary": "2 x Line fish, 3 x Coke", "reason": ""}]
    assert svc.tables.page(code_of(table), OTHER)["orders"] == []                             # another phone's orders aren't shown
    assert [x["id"] for x in svc.tables.orders_for(T, THANDI, False)] == [o["id"]]
    assert svc.tables.orders_for(T, PIETER, False) == []


@pytest.mark.parametrize("items, says", [
    ([], "Choose something"), ([{"product_id": "p-fish", "quantity": 0}], "Choose something"),
    ([{"product_id": "p-kg", "quantity": 1}], "isn't on the menu"), ([{"product_id": "p-other", "quantity": 1}], "isn't on the menu"),
    ([{"product_id": "p-fish", "quantity": 1.5}], "whole number"), ([{"product_id": "p-fish", "quantity": 21}], "big order"),
    ("junk", "Choose something"),
])
async def test_refused_orders(env, items, says):
    svc, repo, msg, push, table, _ = env
    with pytest.raises(TableError, match=says):
        await svc.tables.order(code_of(table), CUST, items)
    assert svc.tables.orders_for(T, BOSS, True) == [] and push.sent == []


async def test_confirming_puts_the_lines_on_the_tables_bill_once(env):
    svc, repo, msg, push, table, _ = env
    o = await svc.tables.order(code_of(table), CUST, [{"product_id": "p-fish", "quantity": 2}])
    repo.products[0]["price_cents"] = 99900                                                   # the price changes after ordering
    bill = svc.tables.confirm(T, o["id"], THANDI)
    assert bill["total_cents"] == 33000 and bill["items"][0]["description"] == "Line fish" and bill["items"][0]["quantity"] == 2
    with pytest.raises(TableError, match="already been dealt with"):
        svc.tables.confirm(T, o["id"], PIETER)
    assert svc.tables.bill_view(T, repo.get_tag(T, table["id"]))["total_cents"] == 33000        # not doubled
    assert repo.get_row("kb_table_orders", T, o["id"])["bill_id"] == bill["id"]
    second = await svc.tables.order(code_of(table), CUST, [{"product_id": "p-coke", "quantity": 1}])
    assert svc.tables.confirm(T, second["id"], THANDI)["total_cents"] == 35500                 # the same bill grows
    third = await svc.tables.order(code_of(table), CUST, [{"product_id": "p-coke", "quantity": 1}])
    svc.tables.reject(T, third["id"], THANDI, "Kitchen is closed")
    with pytest.raises(TableError):
        svc.tables.confirm(T, third["id"], THANDI)
    assert svc.tables.page(code_of(table), CUST)["orders"][-1] == {
        "status": "rejected", "total_cents": 2500, "summary": "1 x Coke", "reason": "Kitchen is closed"}
    with pytest.raises(TableError):
        svc.tables.confirm("someone-else", o["id"], THANDI)


async def test_the_waiter_adds_and_removes_lines(env):
    svc, repo, msg, push, table, _ = env
    bill = svc.tables.add_item(T, table["id"], THANDI, product_id="p-coke", quantity=2)
    assert bill["total_cents"] == 5000
    bill = svc.tables.add_item(T, table["id"], THANDI, description="Corkage", unit_cents=6000)
    assert bill["total_cents"] == 11000 and [i["description"] for i in bill["items"]] == ["Coke", "Corkage"]
    bill = svc.tables.void_item(T, table["id"], bill["items"][0]["id"], THANDI)
    assert bill["total_cents"] == 6000
    noted = svc.tables.add_item(T, table["id"], THANDI, product_id="p-fish", note="  no onions,\n sauce on the side ")
    assert noted["items"][-1]["description"] == "Line fish (no onions, sauce on the side)" and noted["total_cents"] == 22500
    svc.tables.void_item(T, table["id"], noted["items"][-1]["id"], THANDI)
    for kw, says in (({"product_id": "p-kg"}, "isn't on the menu"), ({"description": ""}, "description"),
                     ({"description": "x", "unit_cents": -1}, "price"), ({"product_id": "p-coke", "quantity": 0}, "between 1 and 99")):
        with pytest.raises(TableError, match=says):
            svc.tables.add_item(T, table["id"], THANDI, **kw)
    with pytest.raises(TableError):
        svc.tables.void_item(T, table["id"], "no-such-item", THANDI)
    assert [a["action"] for a in repo.audit_log(T)][:3] == ["table.item_void", "table.items_add", "table.item_void"]


# ── paying ────────────────────────────────────────────────────────────────────────────────────
async def test_the_customer_pays_the_tables_bill_and_nothing_is_added_while_they_do(env):
    svc, repo, msg, push, table, _ = env
    svc.tables.add_item(T, table["id"], THANDI, product_id="p-fish", quantity=1)
    svc.tables.add_item(T, table["id"], THANDI, product_id="p-coke", quantity=2)
    code = code_of(table)
    v = svc.express.view(code, "cookie-1")
    assert v["kind"] == "bill" and v["bill_cents"] == 21500 and v["description"] == "Table 7"
    assert [(i["quantity"], i["description"], i["line_cents"]) for i in v["items"]] == [(1, "Line fish", 16500), (2, "Coke", 5000)]
    html = express_page.render(code, {**v, "table": svc.tables.page(code, svc.express.payer("cookie-1"))})
    assert "R 215.00" in html and "1 x Line fish" in html and "Ask for the bill" in html and "See the menu and order" in html
    # the stored figure is only for show: what is charged is the lines, added up when payment starts
    bill_id = svc.tables.bill_view(T, repo.get_tag(T, table["id"]))["id"]
    repo.bills[bill_id]["subtotal_cents"] = 1
    await svc.express.checkout(code, v["token"], "cookie-1", tip="0")
    session = next(s for s in repo.sessions.values() if s["bill_id"] == bill_id)
    assert session["bill_cents"] == 21500 and repo.bills[bill_id]["status"] == "claimed"
    with pytest.raises(TableError, match="paying this table's bill"):
        svc.tables.add_item(T, table["id"], THANDI, product_id="p-coke")
    o = await svc.tables.order(code, CUST, [{"product_id": "p-coke", "quantity": 1}])
    with pytest.raises(TableError):
        svc.tables.confirm(T, o["id"], THANDI)
    assert repo.get_row("kb_table_orders", T, o["id"])["status"] == "pending"                  # still there to confirm after
    assert svc.tables.bill_view(T, repo.get_tag(T, table["id"]))["total_cents"] == 21500


async def test_an_add_that_races_a_payment_is_undone(env):
    svc, repo, msg, push, table, _ = env
    svc.tables.add_item(T, table["id"], THANDI, product_id="p-coke")
    bill_id = svc.tables.bill_view(T, repo.get_tag(T, table["id"]))["id"]
    real = repo.add_tab_item

    def add_then_customer_claims(row):
        out = real(row)
        repo.bills[bill_id]["status"] = "claimed"            # the customer pressed Pay in between
        return out

    repo.add_tab_item = add_then_customer_claims
    with pytest.raises(TableError, match="started paying just now"):
        svc.tables.add_item(T, table["id"], THANDI, product_id="p-fish")
    assert svc.tables.total(T, bill_id) == 2500                                               # the fish isn't on the bill


async def test_a_table_with_another_bill_open_takes_no_lines_and_a_used_table_cant_be_removed(env):
    svc, repo, msg, push, table, _ = env
    svc.create_bill(tenant_id=T, tag_id=table["id"], amount_cents=5000, description="Typed bill", staff_id=THANDI, created_by=THANDI)
    with pytest.raises(TableError, match="already has a bill open"):
        svc.tables.add_item(T, table["id"], THANDI, product_id="p-coke")
    other = svc.tables.create_table(T, "Table 8", THANDI)
    svc.tables.add_item(T, other["id"], THANDI, product_id="p-coke")
    with pytest.raises(TableError, match="bill open"):
        svc.tables.remove_table(T, other["id"])
    empty = svc.tables.create_table(T, "Table 9")
    svc.tables.remove_table(T, empty["id"])
    assert "Table 9" not in [t["label"] for t in svc.tables.tables(T)]


# ── the pages ─────────────────────────────────────────────────────────────────────────────────
async def test_the_pages_escape_and_show_what_was_asked(env):
    svc, repo, msg, push, table, _ = env
    repo.products.append({"id": "p-x", "tenant_id": T, "name": "Soup <script>x</script>", "price_cents": 6500,
                          "category": "starters", "in_stock": True, "description": "Hot & fresh"})
    menu = express_page.menu(code_of(table), "The Yard", "Table 7", svc.tables.menu(T), nonce="n1")
    assert "Soup &lt;script&gt;x&lt;/script&gt;" in menu and "<script>x</script>" not in menu
    assert "name='q_p-x'" in menu and "data-c=6500" in menu and "Hot &amp; fresh" in menu and "nonce='n1'" in menu
    assert "<h2 class=cat>Starters</h2>" in menu
    await svc.tables.call(code_of(table), CUST, "waiter")
    v = svc.express.view(code_of(table), "cookie-1")
    html = express_page.render(code_of(table), {**v, "table": svc.tables.page(code_of(table), CUST)})
    assert "Nothing to pay yet" in html and "We've asked for a waiter" in html
    assert "value=waiter" not in html and "value=bill" in html                                 # no second button for what's asked


# ── with a roster: calls go to whoever is working ─────────────────────────────────────────────
def _on_shift(svc, clock, member, start_h=-1, end_h=4):
    """A published shift around now for `member`."""
    from vula.tap.shifts import SHIFTS
    now = clock.t
    svc.repo.add_row(SHIFTS, {"tenant_id": T, "member_id": member, "status": "published",
                              "starts_at": (now + timedelta(hours=start_h)).isoformat(),
                              "ends_at": (now + timedelta(hours=end_h)).isoformat()})


async def test_a_table_nobody_serves_calls_the_people_on_shift_not_the_owner(env):
    svc, repo, msg, push, table, clock = env
    free = svc.tables.create_table(T, "Table 9")
    repo.team[(T, PIETER)] = ["27800000002"]
    _on_shift(svc, clock, PIETER)
    await svc.tables.call(code_of(free), CUST, "waiter")
    assert [p[0] for p in push.sent] == [PIETER]
    assert msg.to("27800000002", "text") and not msg.to("27800000009", "text")
    assert [c["table"] for c in svc.tables.calls_for(T, PIETER, False)] == ["Table 9"]


async def test_a_waiter_who_is_off_shift_does_not_get_their_table_while_others_are_working(env):
    svc, repo, msg, push, table, clock = env
    _on_shift(svc, clock, PIETER)                                        # Thandi serves Table 7 but isn't on
    await svc.tables.call(code_of(table), CUST, "bill")
    assert [p[0] for p in push.sent] == [PIETER] and not msg.to("27800000001", "text")
    assert svc.tables.calls_for(T, PIETER, False)                        # he can see and accept it
    await svc.tables.order(code_of(table), CUST, [{"product_id": "p-fish", "quantity": 1}])
    assert svc.tables.orders_for(T, PIETER, False)


async def test_the_tables_own_waiter_still_gets_it_when_on_shift_and_alone_when_nobody_is_planned(env):
    svc, repo, msg, push, table, clock = env
    await svc.tables.call(code_of(table), CUST, "waiter")                # no roster at all: as before
    assert [p[0] for p in push.sent] == [THANDI]
    push.sent.clear()
    _on_shift(svc, clock, THANDI)
    _on_shift(svc, clock, PIETER)
    await svc.tables.call(code_of(table), CUST, "bill")
    assert [p[0] for p in push.sent] == [THANDI]
    assert not svc.tables.calls_for(T, PIETER, False)                    # her table, not his


async def test_a_shift_that_has_ended_or_was_declined_counts_for_nothing(env):
    svc, repo, msg, push, table, clock = env
    from vula.tap.shifts import SHIFTS
    free = svc.tables.create_table(T, "Table 9")
    _on_shift(svc, clock, PIETER, start_h=-6, end_h=-1)                  # over
    repo.add_row(SHIFTS, {"tenant_id": T, "member_id": THANDI, "status": "sick",
                          "starts_at": (clock.t - timedelta(hours=1)).isoformat(),
                          "ends_at": (clock.t + timedelta(hours=3)).isoformat()})
    await svc.tables.call(code_of(free), CUST, "waiter")
    assert [p[0] for p in push.sent] == [BOSS]                           # nobody working: owners and managers


async def test_a_roster_that_cannot_be_read_never_loses_the_call(env):
    svc, repo, msg, push, table, clock = env

    def broken(*a, **k):
        raise RuntimeError("database away")
    repo.shifts_between = broken
    await svc.tables.call(code_of(table), CUST, "waiter")
    assert [p[0] for p in push.sent] == [THANDI]
