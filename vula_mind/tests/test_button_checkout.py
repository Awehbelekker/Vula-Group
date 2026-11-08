"""Checkout after a WhatsApp cart, with buttons (8 Nov 2026): the cart → delivery or collect →
address (same as last time, or typed) → slot → summary → a pay button places the order through
the assistant's own place_order. Every total comes from the cart rows; no model chooses a
step. Made-up shop, products and customer; no network.
"""
import asyncio
from types import SimpleNamespace

import pytest

from vula.commerce import button_checkout as bc

T, PHONE = "seaview-fish", "27820000005"


class _Q:
    def __init__(self, w, name):
        self.w, self.name, self.f, self.ins, self.patch = w, name, [], [], None

    def select(self, *a, **k): return self
    def eq(self, k, v): self.f.append((k, v)); return self
    def in_(self, k, vs): self.ins.append((k, set(vs))); return self
    def update(self, p): self.patch = p; return self

    def execute(self):
        if self.name == "commerce_carts":
            assert ("tenant_id", T) in self.f                     # always scoped to the business
            if self.patch is not None and ("id", self.w.cart["id"]) in self.f:
                self.w.cart.update(self.patch)
            return SimpleNamespace(data=[])
        rows = [p for p in self.w.products if all(p.get(k) == v for k, v in self.f)
                and all(p.get(k) in vs for k, vs in self.ins)]
        return SimpleNamespace(data=rows)


class World:
    def __init__(self, *, collection=True, last_address=None, stock=None, fee=8000):
        self.products = [{"id": "p1", "tenant_id": T, "name": "Hake Fillet", "stock_quantity": stock},
                         {"id": "p2", "tenant_id": T, "name": "Prawns 1kg", "stock_quantity": None}]
        self.cart = {"id": "cart-1", "tenant_id": T, "delivery_address": None, "delivery_slot": None,
                     "customer_name": "Sam", "commerce_cart_items": [
                         {"id": "ci1", "product_id": "p1", "quantity": 2, "unit_price_cents": 12950,
                          "commerce_products": {"name": "Hake Fillet"}},
                         {"id": "ci2", "product_id": "p2", "quantity": 1, "unit_price_cents": 24900,
                          "commerce_products": {"name": "Prawns 1kg"}}]}
        self.cfg = {"collection_enabled": collection, "collection_note": "Seaview Harbour, 8–4", "min_order_cents": None}
        self.last_address, self.fee = last_address, fee
        self.sent, self.placed, self.asked, self.closed, self.team = [], [], [], [], []

    # the bits of vula.commerce.service the checkout uses
    def _client(self): return SimpleNamespace(table=lambda n: _Q(self, n))
    async def get_or_create_cart(self, tid, session, phone):
        assert tid == T and session == PHONE
        return self.cart
    async def get_customer_profile(self, tid, phone):
        return {"delivery_address": self.last_address} if self.last_address else None
    def delivery_fee_cents(self, tid, cart, subtotal): return self.fee
    def delivery_fee_known(self, tid): return True
    async def remove_from_cart(self, cart_id, item_id):
        self.cart["commerce_cart_items"] = [i for i in self.cart["commerce_cart_items"] if i["id"] != item_id]
    async def get_or_create_session(self, *a, **k): return {"id": "s1"}
    async def append_message(self, *a, **k): return None

    def last(self):
        return self.sent[-1]

    def ids(self):
        return [b["id"] for b in self.sent[-1][1]]


class FakeSkill:
    def __init__(self, w): self.w = w

    async def _exec_place_order(self, tid, session, phone, args, ctx=None):
        self.w.placed.append(args)
        self.w.cart["commerce_cart_items"] = []
        return {"confirmation": "✅ *Order OTH-1001 confirmed*"}

    async def _exec_ask_team(self, tid, phone, args):
        self.w.team.append(args["question"])
        return {"escalated": True}


@pytest.fixture()
def make(monkeypatch):
    def build(**kw):
        w = World(**kw)
        monkeypatch.setattr(bc, "_svc", lambda: w)
        monkeypatch.setattr(bc, "_settings", lambda tid: w.cfg)

        async def buttons(phone, tid, body, buttons):
            w.sent.append((body, list(buttons)))
            return True

        async def text(phone, tid, body):
            w.sent.append((body, []))
        monkeypatch.setattr(bc, "_buttons", buttons)
        monkeypatch.setattr(bc, "_text", text)
        monkeypatch.setattr("vula.commerce.order_workflow.usable_payment_methods", lambda tid, cfg=None: ["eft", "cod"])
        monkeypatch.setattr("vula.open_questions.ask", lambda tid, phone, kind, ref, prompt="": w.asked.append((kind, ref)) or "q1")
        monkeypatch.setattr("vula.open_questions.close", lambda qid, answer="", status="answered": w.closed.append((qid, status)))
        monkeypatch.setattr("core.skills.loader.get_skill", lambda name: FakeSkill(w))
        return w
    return build


def tap(w, rid):
    assert asyncio.run(bc.handle(PHONE, T, rid))


def test_delivery_with_a_typed_address_then_slot_then_pay(make):
    w = make(collection=False)
    assert asyncio.run(bc.start(PHONE, T, intro="Got your order ✅"))
    body, _ = w.last()
    assert "2 × Hake Fillet — R259.00" in body and "Subtotal: R508.00" in body and w.ids() == ["chk_go", "chk_edit"]
    tap(w, "chk_go")                                                    # no collection → straight to the address
    assert w.asked == [("checkout_address", "cart-1")] and "delivery address" in w.last()[0]
    assert asyncio.run(bc.take_address(PHONE, T, "12 Main Road, Seaview, Cape Town", {"id": "q1", "ref_id": "cart-1"}))
    assert w.cart["delivery_address"] == "12 Main Road, Seaview, Cape Town" and w.closed == [("q1", "answered")]
    assert w.ids() == ["chk_slot_morning", "chk_slot_afternoon"]
    tap(w, "chk_slot_afternoon")
    body, _ = w.last()
    assert "Delivery: R80.00" in body and "*Total: R588.00*" in body                     # 508.00 + 80.00 from the rows
    assert "12 Main Road, Seaview, Cape Town (afternoon)" in body and w.ids() == ["chk_pay_eft", "chk_pay_cod"]
    tap(w, "chk_pay_eft")
    assert w.placed == [{"payment_method": "eft", "fulfilment": "delivery", "delivery_address": "12 Main Road, Seaview, Cape Town",
                         "delivery_slot": "afternoon", "customer_name": "Sam"}]
    assert "Order OTH-1001 confirmed" in w.last()[0]


def test_collection_skips_the_fee_address_and_slot(make):
    w = make()
    tap(w, "chk_go")
    assert w.ids() == ["chk_del", "chk_col"] and "Seaview Harbour" in w.last()[0]
    tap(w, "chk_col")
    body, _ = w.last()
    assert w.cart["delivery_address"] == "Collection" and "Delivery" not in body and "*Total: R508.00*" in body
    tap(w, "chk_pay_cod")
    assert w.placed[0]["fulfilment"] == "collection" and w.placed[0]["delivery_address"] == ""


def test_a_returning_customer_is_offered_the_last_address(make):
    w = make(last_address="7 Oak Street, Seaview")
    tap(w, "chk_del")
    assert "7 Oak Street, Seaview" in w.last()[0] and w.ids() == ["chk_addr_last", "chk_addr_new"]
    tap(w, "chk_addr_last")
    assert w.cart["delivery_address"] == "7 Oak Street, Seaview" and w.ids()[0] == "chk_slot_morning"
    tap(w, "chk_addr_new")
    assert w.asked == [("checkout_address", "cart-1")]


def test_an_out_of_stock_line_is_named_with_remove_and_ask_the_team(make):
    w = make(stock=1)                                                  # 2 wanted, 1 left
    assert asyncio.run(bc.start(PHONE, T))
    assert "We only have 1 of Hake Fillet left" in w.last()[0]
    assert w.ids() == ["chk_rm_ci1", "chk_team", "chk_edit"]
    tap(w, "chk_rm_ci1")
    assert [i["id"] for i in w.cart["commerce_cart_items"]] == ["ci2"]
    assert "Removed ✅" in w.last()[0] and "Subtotal: R249.00" in w.last()[0] and w.ids()[0] == "chk_go"


def test_ask_the_team_keeps_the_cart(make):
    w = make(stock=1)
    tap(w, "chk_team")
    assert w.team and "Hake Fillet" in w.team[0] and len(w.cart["commerce_cart_items"]) == 2
    assert "Your cart is kept" in w.last()[0]


def test_unmatched_catalogue_items_are_said_and_offer_the_team(make):
    w = make()
    assert asyncio.run(bc.start(PHONE, T, intro="Got your order ✅", unmatched=["old-sku-9"]))
    assert "1 item from the catalogue couldn't be matched" in w.last()[0]
    assert w.ids() == ["chk_go", "chk_edit", "chk_team"]


def test_a_removal_id_from_someone_elses_cart_does_nothing(make):
    w = make()
    tap(w, "chk_rm_not-my-item")
    assert len(w.cart["commerce_cart_items"]) == 2


def test_typed_questions_mid_checkout_go_to_the_assistant_and_the_cart_stays(make):
    w = make(collection=False)
    tap(w, "chk_go")
    q = {"id": "q1", "ref_id": "cart-1"}
    for text in ("Do you deliver to Hout Bay?", "wait", "add another hake", "ok"):
        assert not asyncio.run(bc.take_address(PHONE, T, text, q))
    assert w.cart["delivery_address"] is None and len(w.cart["commerce_cart_items"]) == 2 and w.closed == []


def test_minimum_order_is_said_before_any_address(make):
    w = make()
    w.cfg["min_order_cents"] = 60000
    tap(w, "chk_go")
    assert "minimum order is R600.00" in w.last()[0] and w.ids() == ["cart_more"]


def test_a_second_pay_tap_never_places_twice_and_unknown_ids_are_not_taken(make):
    w = make()
    tap(w, "chk_col")
    tap(w, "chk_pay_eft")
    tap(w, "chk_pay_eft")
    assert len(w.placed) == 1 and "already been placed" in w.last()[0]
    assert not asyncio.run(bc.handle(PHONE, T, "rate_x_5"))
    w2 = make()
    tap(w2, "chk_col")
    tap(w2, "chk_pay_online")                                          # not offered here → the summary again
    assert w2.placed == [] and w2.ids() == ["chk_pay_eft", "chk_pay_cod"]


def test_an_empty_cart_says_so(make):
    w = make()
    w.cart["commerce_cart_items"] = []
    assert not asyncio.run(bc.start(PHONE, T))
    tap(w, "chk_go")
    assert "Your cart is empty" in w.last()[0]


def test_the_webhook_hands_checkout_ids_to_this_module():
    import inspect
    from vula.api import whatsapp
    src = inspect.getsource(whatsapp._handle_commerce_interactive)
    assert 'reply_id.startswith("chk_")' in src and "button_checkout.handle" in src
    assert "button_checkout.start" in inspect.getsource(whatsapp._handle_native_order)
    assert 'kind == "checkout_address"' in inspect.getsource(whatsapp._try_open_question)


def test_a_collection_only_shop_goes_straight_to_the_summary(make):
    w = make()
    w.cfg["delivery_enabled"] = False
    tap(w, "chk_go")
    assert w.cart["delivery_address"] == "Collection" and "Seaview Harbour" in w.last()[0]
    assert w.ids() == ["chk_pay_eft", "chk_pay_cod"]
    tap(w, "chk_del")                                                  # an old Delivery button
    assert w.cart["delivery_address"] == "Collection"
