"""Paying a lesson online (vula/commerce/lessons.py): the request's invoice is what is paid, on
the business's own pay page; "paid" is only ever read from the invoice."""
from __future__ import annotations

import pytest

from tests.test_lessons import TID, _at, run, world  # noqa: F401 — the fixture
from vula.commerce import lessons, pay_page, service


@pytest.fixture
def money(world, monkeypatch):
    db = world.db
    changes = []

    async def create_invoice(tenant_id, data):
        row = {"id": f"00000000-0000-0000-0000-{len(db.rows['commerce_invoices']) + 1:012d}", "tenant_id": tenant_id,
               "status": data["status"], "total_cents": sum(x["quantity"] * x["unit_price_cents"] for x in data["line_items"]),
               "total_paid_cents": 0, "lines": data["line_items"]}
        db.rows["commerce_invoices"].append(row)
        return row

    async def get_invoice(tenant_id, invoice_id):
        return next((dict(i) for i in db.rows["commerce_invoices"] if i["id"] == invoice_id and i["tenant_id"] == tenant_id), None)

    async def update_invoice_status(tenant_id, invoice_id, status, **k):
        changes.append((invoice_id, status))
        for i in db.rows["commerce_invoices"]:
            if i["id"] == invoice_id and i["tenant_id"] == tenant_id:
                i["status"] = status
                if status == "paid":
                    i["total_paid_cents"] = i["total_cents"]
    monkeypatch.setattr(service, "create_invoice", create_invoice)
    monkeypatch.setattr(service, "get_invoice", get_invoice)
    monkeypatch.setattr(service, "update_invoice_status", update_invoice_status)
    monkeypatch.setattr(pay_page, "page_url", lambda tid, inv: f"https://pay.example/{tid}/{inv}")
    world.changes = changes
    return world


def _lesson(people=2, phone="082 123 4567"):
    return run(lessons.request(TID, kind="lesson", item_id="surf", wanted_date=_at()[:10], name="Thandi Mokoena",
                               phone=phone, people=people, fmt="group", level="first_timer"))


def test_a_confirmation_with_a_link_sends_the_invoice_and_says_where_to_pay(money):
    a = _lesson()
    done = run(lessons.confirm(TID, a["id"], starts_at=_at(), pay_link=True))
    inv = money.db.rows["commerce_invoices"][0]
    assert inv["status"] == "sent" and inv["total_cents"] == done["amount_cents"]
    assert done["paid"] is False and done["pay_url"] == f"https://pay.example/{TID}/{inv['id']}"
    assert f"Pay here: {done['pay_url']}" in money.told[-1] and "Pay at the shop" not in money.told[-1]
    again = run(lessons.confirm(TID, a["id"], starts_at=_at(2), pay_link=True))          # a new time: the same invoice
    assert len(money.db.rows["commerce_invoices"]) == 1 and again["pay_url"] == done["pay_url"]
    plain = _lesson(phone="083 000 1111")                                                # no link asked: as before
    out = run(lessons.confirm(TID, plain["id"], starts_at=_at()))
    assert out["pay_url"] is None and "Pay at the shop or by EFT." in money.told[-1] and len(money.db.rows["commerce_invoices"]) == 1


def test_paid_is_read_from_the_invoice_and_the_customer_sees_it(money, monkeypatch):
    a = _lesson()
    done = run(lessons.confirm(TID, a["id"], starts_at=_at(), pay_link=True))
    token = lessons.token_for(a["id"])
    mine = lessons.by_tokens(TID, [token])[0]
    assert mine["paid"] is False and mine["pay_url"] == done["pay_url"]
    assert lessons.list_requests(TID)[0]["pay_url"] == done["pay_url"]
    sent = []

    async def reply(phone, text, tenant_id="", idem_key=None):
        sent.append((phone, text, idem_key))
        return True
    monkeypatch.setattr("vula.api.whatsapp._send_reply", reply)
    inv = money.db.rows["commerce_invoices"][0]
    assert run(lessons.invoice_paid(TID, inv["id"])) is True                              # the gateway said so
    assert "Payment received for" in sent[0][1] and sent[0][2] == f"lesson_paid:{inv['id']}"
    inv.update(status="paid", total_paid_cents=inv["total_cents"])
    mine = lessons.by_tokens(TID, [token])[0]
    assert mine["paid"] is True and mine["pay_url"] is None
    row = lessons.list_requests(TID)[0]
    assert row["paid"] is True and row["pay_url"] is None
    later = run(lessons.confirm(TID, a["id"], starts_at=_at(3), pay_link=True))           # moved after paying
    assert "paid, thank you" in money.told[-1] and "Pay here" not in money.told[-1] and later["paid"] is True
    assert len(money.db.rows["commerce_invoices"]) == 1
    assert run(lessons.invoice_paid("another_school", inv["id"])) is False                # another business's id opens nothing


def test_a_new_price_makes_a_new_invoice_and_a_draft_is_sent(money):
    a = _lesson()
    run(lessons.confirm(TID, a["id"], starts_at=_at(), invoice=True))                      # a draft, for the record
    first = money.db.rows["commerce_invoices"][0]
    assert first["status"] == "draft" and lessons.list_requests(TID)[0]["pay_url"] is None   # a draft has no link
    sent = run(lessons.confirm(TID, a["id"], starts_at=_at(), pay_link=True))
    assert first["status"] == "sent" and sent["pay_url"].endswith(first["id"]) and len(money.db.rows["commerce_invoices"]) == 1
    moved = run(lessons.confirm(TID, a["id"], starts_at=_at(), price_cents=50000, pay_link=True))
    second = money.db.rows["commerce_invoices"][1]
    assert first["status"] == "cancelled" and second["total_cents"] == 50000 and moved["pay_url"].endswith(second["id"])
    second["total_paid_cents"] = 10000                                                     # part paid: a person sorts it out
    kept = run(lessons.confirm(TID, a["id"], starts_at=_at(), price_cents=60000, pay_link=True))
    assert len(money.db.rows["commerce_invoices"]) == 2 and second["status"] == "sent" and kept["invoice_id"] == second["id"]


def test_a_cancelled_or_finished_request_offers_no_link(money):
    a = _lesson()
    run(lessons.confirm(TID, a["id"], starts_at=_at(), pay_link=True))
    lessons.set_status(TID, a["id"], "cancelled")
    assert lessons.list_requests(TID)[0]["pay_url"] is None
    assert lessons.by_tokens(TID, [lessons.token_for(a["id"])])[0]["pay_url"] is None
