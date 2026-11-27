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


# ── paid in person: the coach charges the lesson with Tap to Pay ───────────────────────────────
def test_a_coach_sees_their_own_unpaid_lessons_and_never_the_customers_number(money):
    a, b = _lesson(), _lesson(phone="083 000 1111")
    run(lessons.confirm(TID, a["id"], starts_at=_at(), coach_id="m1"))
    run(lessons.confirm(TID, b["id"], starts_at=_at()))                                    # no coach
    mine = lessons.tap_due(TID, "m1")
    assert [(x["ref"], x["customer"], x["coach"], x["billed"]) for x in mine] == [(a["ref"], "Thandi", "Shane", False)]
    assert mine[0]["amount_cents"] == a["amount_cents"] and "082" not in str(mine) and "2782" not in str(mine)
    assert len(lessons.tap_due(TID)) == 2 and lessons.tap_due(TID, "someone_else") == []   # a manager sees both
    far = _lesson(phone="084 000 2222")
    run(lessons.confirm(TID, far["id"], starts_at=_at(5), coach_id="m1"))                  # five days off: not yet
    assert len(lessons.tap_due(TID, "m1")) == 1


def test_a_tap_payment_marks_the_lesson_paid_once_and_cancels_the_unpaid_invoice(money):
    a = _lesson()
    done = run(lessons.confirm(TID, a["id"], starts_at=_at(), coach_id="m1", pay_link=True))
    inv = money.db.rows["commerce_invoices"][0]
    ready = lessons.tap_ready(TID, a["id"])
    assert ready["owed_cents"] == done["amount_cents"]
    lessons.tap_billed(TID, a["id"], "bill-1")
    assert lessons.tap_due(TID, "m1")[0]["billed"] is True
    assert run(lessons.tap_paid(TID, {"id": "bill-other", "subtotal_cents": 999999})) is None          # not a lesson's bill
    assert run(lessons.tap_paid(TID, {"id": "bill-1", "subtotal_cents": done["amount_cents"] - 1})) is None   # less than it costs
    assert money.db.rows["commerce_lesson_requests"][0].get("paid_at") is None
    paid = run(lessons.tap_paid(TID, {"id": "bill-1", "subtotal_cents": done["amount_cents"]}))
    assert paid["paid_at"] and paid["paid_via"] == "tap" and inv["status"] == "cancelled"   # never paid twice
    row = lessons.list_requests(TID)[0]
    assert row["paid"] is True and row["pay_url"] is None and row["paid_via"] == "tap"
    mine = lessons.by_tokens(TID, [lessons.token_for(a["id"])])[0]
    assert mine["paid"] is True and mine["pay_url"] is None
    assert lessons.tap_due(TID, "m1") == []
    assert run(lessons.tap_paid(TID, {"id": "bill-1", "subtotal_cents": done["amount_cents"]})) is None  # a second notice changes nothing
    with pytest.raises(lessons.LessonError, match="already paid"):
        lessons.tap_ready(TID, a["id"])


def test_a_lesson_paid_online_or_not_confirmed_cant_be_charged_by_tap(money):
    a = _lesson()
    with pytest.raises(lessons.LessonError, match="Only a confirmed lesson"):
        lessons.tap_ready(TID, a["id"])
    run(lessons.confirm(TID, a["id"], starts_at=_at(), coach_id="m1", pay_link=True))
    inv = money.db.rows["commerce_invoices"][0]
    inv.update(status="paid", total_paid_cents=inv["total_cents"])                          # paid online first
    assert lessons.tap_due(TID, "m1") == []
    with pytest.raises(lessons.LessonError, match="already paid"):
        lessons.tap_ready(TID, a["id"])
    lessons.tap_billed(TID, a["id"], "bill-9")                                            # an old bill paid anyway
    run(lessons.tap_paid(TID, {"id": "bill-9", "subtotal_cents": inv["total_cents"]}))
    assert inv["status"] == "paid"                                                        # a paid invoice is never cancelled


def test_the_staff_app_charges_the_lessons_own_amount_on_the_coachs_tag(money, monkeypatch):
    from types import SimpleNamespace as NS
    from fastapi import HTTPException
    from vula.tap import api as tap_api, appapi
    a = _lesson()
    done = run(lessons.confirm(TID, a["id"], starts_at=_at(), coach_id="m1"))
    made, logged = [], []

    class Svc:
        repo = NS(list_tags=lambda tid: [{"id": "tag-m1", "bound_id": "m1", "status": "active", "bound_type": "person"},
                                         {"id": "tag-t", "bound_id": "m1", "status": "active", "bound_type": "table"}])

        def create_bill(self, **kw):
            made.append(kw)
            return {"id": "bill-7", "bill_type": "fixed", "status": "open", "description": kw["description"],
                    "subtotal_cents": kw["amount_cents"], "staff_id": kw["staff_id"]}
    monkeypatch.setattr(tap_api, "tenant_enabled", lambda tid: True)
    monkeypatch.setattr(tap_api, "get_service", lambda: Svc())
    monkeypatch.setattr(appapi, "_audit", lambda *x, **k: logged.append(k))
    coach = NS(tenant_id=TID, member_id="m1", device_id="d", role="staff", name="Shane", sees_all=False)
    other = NS(tenant_id=TID, member_id="m9", device_id="d", role="staff", name="Zed", sees_all=False)
    assert [x["ref"] for x in run(appapi.lessons_due(coach))["lessons"]] == [a["ref"]]
    assert run(appapi.lessons_due(other))["lessons"] == []
    with pytest.raises(HTTPException) as e:
        run(appapi.lesson_bill(a["id"], other))                                           # not their lesson
    assert e.value.status_code == 404 and made == []
    bill = run(appapi.lesson_bill(a["id"], coach))
    assert made[0]["amount_cents"] == done["amount_cents"] and made[0]["tag_id"] == "tag-m1" and made[0]["staff_id"] == "m1"
    assert bill["description"] == f"Surf lesson · {a['ref']}" and logged[0]["lesson"] == a["ref"]
    assert money.db.rows["commerce_lesson_requests"][0]["tap_bill_id"] == "bill-7"
    owner = NS(tenant_id=TID, member_id="boss", device_id="d", role="owner", name="Zoe", sees_all=True)
    run(appapi.lesson_bill(a["id"], owner))                                               # a manager: still the coach's tag
    assert made[1]["staff_id"] == "m1" and made[1]["created_by"] == "boss"
