"""An order that needs a deposit, paid from the WhatsApp chat (vula/commerce/order_deposit.py,
Ian 13 Oct: "can admin send an order number by Vula and the client pays by WhatsApp?")."""
import pytest

import vula.api.whatsapp as wa
from vula.commerce import order_deposit, pay_page, service, web_basket

TID = "seedleme"


def _order(**kw):
    return {"id": "o-7", "tenant_id": TID, "display_id": "SEE-00007", "customer_name": "Thandi Mokoena",
            "customer_phone": "27821234567", "status": "pending_payment", "total_cents": 640000,
            "commerce_order_items": [{"product_id": "p-1", "product_name": "Round Drop-In Basin",
                                      "quantity": 2}], **kw}


class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.op, self.payload, self.filters = db, table, "select", None, {}

    def select(self, *_a, **_k):
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def eq(self, k, v):
        self.filters[k] = v
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        rows = [r for r in self.db.rows.setdefault(self.table, [])
                if all(r.get(k) == v for k, v in self.filters.items())]
        self.db.calls.append((self.table, self.op, self.payload, dict(self.filters)))
        if self.op == "update":
            for r in rows:
                r.update(self.payload)
        return type("R", (), {"data": [dict(r) for r in rows]})()


class _DB:
    def __init__(self):
        self.rows, self.calls = {"commerce_products": [{"id": "p-1", "image_url": "https://x/basin.jpg"}]}, []

    def table(self, name):
        return _Q(self, name)


@pytest.fixture
def env(monkeypatch):
    db = _DB()
    db.rows["commerce_orders"] = [_order()]
    out = {"db": db, "cta": [], "templates": [], "replies": [], "team": [], "ok": True, "card": True,
           "eft": "FNB 123", "invoices": []}

    async def create_invoice(tid, data):
        inv = {"id": f"inv-{len(out['invoices']) + 1}", "tenant_id": tid, "invoice_number": f"SEE-INV-{len(out['invoices']) + 1:05d}",
               "status": data.get("status", "draft"), "order_id": data.get("order_id"), "notes": data.get("notes"),
               "direction": "outbound", "total_cents": data["line_items"][0]["unit_price_cents"],
               "total_paid_cents": 0, "_data": data}
        out["invoices"].append(inv)
        db.rows.setdefault("commerce_invoices", []).append(inv)
        return dict(inv)

    async def get_invoice(tid, iid):
        return next((dict(i) for i in out["invoices"] if i["id"] == iid), None)

    async def update_invoice_status(tid, iid, status):
        for i in out["invoices"]:
            if i["id"] == iid:
                i["status"] = status
        return {}

    async def get_order(oid):
        return next((dict(o) for o in db.rows["commerce_orders"] if o["id"] == oid), None)

    async def apply_order_stock(oid, restore=False):
        out.setdefault("stock", []).append(oid)
        return True

    async def has_gateway(tid):
        return out["card"]

    async def gateway_link(tid, inv):
        return "https://pay.yoco.com/x", "yoco"

    async def resolve(tid):
        return {"phone_id": "1387582947771270", "token": "t"}

    async def cta(creds, number, body, label, url, image_url=None):
        out["cta"].append({"body": body, "label": label, "url": url, "image": image_url})
        return out["ok"]

    async def template(tid, phone, name, params, **kw):
        out["templates"].append((name, params))
        return True

    async def reply(phone, text, tid=None, **kw):
        out["replies"].append(text)
        return True

    async def notify_team(tid, kind, text, idem_key=None):
        out["team"].append((kind, text))
        return 1

    monkeypatch.setattr(service, "_client", lambda: db)
    monkeypatch.setattr(service, "create_invoice", create_invoice)
    monkeypatch.setattr(service, "get_invoice", get_invoice)
    monkeypatch.setattr(service, "update_invoice_status", update_invoice_status)
    monkeypatch.setattr(service, "get_order", get_order)
    monkeypatch.setattr(service, "apply_order_stock", apply_order_stock)
    monkeypatch.setattr(pay_page, "has_gateway", has_gateway)
    monkeypatch.setattr(pay_page, "gateway_link", gateway_link)
    monkeypatch.setattr(pay_page, "eft_details", lambda tid: out["eft"])
    monkeypatch.setattr(wa, "_resolve_wa", resolve)
    monkeypatch.setattr(wa, "_send_wa_cta_url", cta)
    monkeypatch.setattr(wa, "send_template", template)
    monkeypatch.setattr(wa, "_send_reply", reply)
    monkeypatch.setattr("vula.integrations.notify.notify_team", notify_team)
    monkeypatch.setattr(web_basket, "public_base", lambda tid: "https://shop.seedleme.com")
    monkeypatch.setattr("vula.api.tenants.display_name", lambda tid: "SeedleMe")
    return out


def test_deposit_amounts():
    assert order_deposit.deposit_cents(640000, amount_rands=1500) == 150000
    assert order_deposit.deposit_cents(640050, pct=50) == 320000          # to the rand
    assert order_deposit.deposit_cents(640000, amount_rands=6400) == 0     # not less than the total
    assert order_deposit.deposit_cents(640000) == 0


@pytest.mark.asyncio
async def test_deposit_request_sends_order_number_and_pay_button(env):
    res = await order_deposit.request(TID, _order(), "deposit", 150000)
    assert res["sent"] and res["amount_cents"] == 150000 and not res["via_template"]
    msg = env["cta"][0]
    assert msg["label"] == "Pay deposit"
    assert msg["url"] == "https://shop.seedleme.com/v1/commerce/seedleme/pay/inv-1"
    assert "*SEE-00007*" in msg["body"] and "2 × Round Drop-In Basin" in msg["body"]
    assert "Deposit to confirm it: *R1,500.00*" in msg["body"] and "Balance later: R4,900.00" in msg["body"]
    inv = env["invoices"][0]
    assert inv["order_id"] == "o-7" and inv["notes"] == "Deposit for order SEE-00007"
    assert inv["_data"]["prices_include_vat"] is True and inv["status"] == "sent"
    # the 2-hour chase for the full total is switched off for this order
    assert env["db"].rows["commerce_orders"][0].get("followup_sent_at")


@pytest.mark.asyncio
async def test_resending_reuses_the_open_deposit_invoice(env):
    await order_deposit.request(TID, _order(), "deposit", 150000)
    await order_deposit.request(TID, _order(), "deposit", 150000)
    assert len(env["invoices"]) == 1 and len(env["cta"]) == 2


@pytest.mark.asyncio
async def test_outside_24h_the_reminder_template_goes_instead(env):
    env["ok"] = False
    res = await order_deposit.request(TID, _order(), "deposit", 150000)
    assert res["via_template"] and env["templates"] == [
        ("seedleme_unpaid_order_chase", ["Thandi Mokoena", "SEE-00007", "R1,500.00"])]


@pytest.mark.asyncio
async def test_no_way_to_pay_creates_nothing(env):
    env["card"], env["eft"] = False, None
    res = await order_deposit.request(TID, _order(), "deposit", 150000)
    assert "No way to take payment" in res["error"] and not env["invoices"] and not env["cta"]


@pytest.mark.asyncio
async def test_paid_deposit_confirms_the_order_not_paid_in_full(env):
    await order_deposit.request(TID, _order(), "deposit", 150000)
    inv = env["invoices"][0]
    inv["status"] = "paid"
    await service._settle_linked_order(TID, dict(inv))
    order = env["db"].rows["commerce_orders"][0]
    assert order["status"] == "confirmed" and env["stock"] == ["o-7"]
    assert "deposit of *R1,500.00*" in env["cta"][-1]["body"] and "Balance: R4,900.00" in env["cta"][-1]["body"]
    assert env["team"][0][0] == "payment_received" and "Balance R4,900.00" in env["team"][0][1]
    # a repeat of the same payment event tells nobody again
    await service._settle_linked_order(TID, dict(inv))
    assert len(env["team"]) == 1


@pytest.mark.asyncio
async def test_balance_after_deposit(env):
    await order_deposit.request(TID, _order(), "deposit", 150000)
    env["invoices"][0]["status"] = "paid"
    env["db"].rows["commerce_orders"][0]["status"] = "packing"
    res = await order_deposit.request(TID, _order(status="packing"), "balance")
    assert res["amount_cents"] == 490000 and env["cta"][-1]["label"] == "Pay balance"
    assert "Already paid: R1,500.00" in env["cta"][-1]["body"]
    bal = env["invoices"][1]
    bal["status"] = "paid"
    await service._settle_linked_order(TID, dict(bal))
    assert env["db"].rows["commerce_orders"][0]["status"] == "packing"      # status left alone
    assert "Paid in full" in env["cta"][-1]["body"]
    assert (await order_deposit.request(TID, _order(), "balance"))["error"].endswith("already paid in full.")


@pytest.mark.asyncio
async def test_admin_order_with_deposit_on_whatsapp(env, monkeypatch):
    import core.skills.commerce_admin as ca
    monkeypatch.setattr(ca, "_emit", lambda **kw: None)

    async def list_products(tid, **kw):
        return [{"id": "p-1", "name": "Round Drop-In Basin", "price_cents": 320000}]

    async def get_or_create_cart(tid, session, customer_phone=None):
        return {"id": "c-1", "items": []}

    async def add_to_cart(*a, **k):
        return None

    async def create_order(tid, cart, data):
        assert data["payment_method"] == "eft"          # never expires as an online checkout
        return {"id": "o-7", "display_id": "SEE-00007", "total_cents": 640000}

    monkeypatch.setattr(ca.service, "list_products", list_products)
    monkeypatch.setattr(ca.service, "get_or_create_cart", get_or_create_cart)
    monkeypatch.setattr(ca.service, "add_to_cart", add_to_cart)
    monkeypatch.setattr(ca.service, "create_order", create_order)
    args = {"customer_name": "Thandi Mokoena", "customer_phone": "0821234567",
            "items": [{"product": "round drop-in basin", "quantity": 2}], "deposit_pct": 50}
    skill = ca.CommerceAdminSkill()
    preview = await skill._create_manual_order(TID, args)
    assert preview["preview"] and preview["deposit"] == "R3,200.00" and not env["invoices"]
    res = await skill._create_manual_order(TID, {**args, "confirm": True})
    assert "✅ Order *SEE-00007* created" in res["reply_verbatim"]
    assert "*Pay deposit* button for R3,200.00" in res["reply_verbatim"]
    assert env["cta"][0]["label"] == "Pay deposit"


@pytest.mark.asyncio
async def test_order_payment_tool_previews_then_sends(env, monkeypatch):
    import core.skills.commerce_admin as ca
    skill = ca.CommerceAdminSkill()
    prev = await skill._order_payment(TID, {"order_number": "see-00007", "deposit_rands": 2000})
    assert prev["preview"] and prev["deposit"] == "R2,000.00" and not env["cta"]
    res = await skill._order_payment(TID, {"order_number": "SEE-00007", "deposit_rands": 2000, "confirm": True})
    assert res["sent"] and "Pay deposit" in res["reply_verbatim"]
    bad = await skill._order_payment(TID, {"order_number": "SEE-00007", "deposit_rands": 9000})
    assert "less than the order total" in bad["error"]
