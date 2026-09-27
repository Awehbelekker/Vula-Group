"""Stock by WhatsApp photo (2026-09-27, barcode stock counting).

An owner/staff photo captioned about stock is read once by vision: a barcode or label → the
product and its stock, and a bare "12" / "+3" reply becomes an update_stock preview; a delivery
note → a receive_stock preview. Both go out with the Confirm button — nothing moves until it's
tapped, and a shelf photo is never turned into a guessed count. Before this, the photo was
filed as a document and a delivery note was filed "no money booked".
"""
from unittest.mock import AsyncMock

import pytest

from tests.test_stock_movements import TID, FakeDB, _moves
from vula.api import whatsapp as wa
from vula.commerce import service

PHONE = "27821234567"


@pytest.fixture()
def env(monkeypatch):
    db = FakeDB({
        "commerce_products": [
            {"id": "p1", "tenant_id": TID, "name": "Hake Fillet", "stock_quantity": 10, "in_stock": True,
             "barcode": "6001234567890"},
            {"id": "p2", "tenant_id": TID, "name": "Tiger Prawns 1kg", "stock_quantity": 4, "in_stock": True},
        ],
    })
    monkeypatch.setattr(service, "_client", lambda: db)

    async def list_products(tid, **kw):
        return [p for p in db.tables["commerce_products"] if p["tenant_id"] == tid]
    monkeypatch.setattr(service, "list_products", list_products)
    replies, buttons = [], []

    async def send_reply(phone, text, tenant_id=None, **kw):
        replies.append(text)

    async def send_confirm(phone, tenant_id, cr):
        buttons.append(cr)
    monkeypatch.setattr(wa, "_send_reply", send_reply)
    monkeypatch.setattr(wa, "_send_confirm_request", send_confirm)
    monkeypatch.setattr("vula.api.whatsapp._STOCK_FOCUS", {})

    async def no_llm(*a, **k):
        raise RuntimeError("no model in tests")
    monkeypatch.setattr("litellm.acompletion", no_llm)
    return {"db": db, "replies": replies, "buttons": buttons}


def _photo(monkeypatch, read):
    monkeypatch.setattr(wa, "_read_stock_photo", AsyncMock(return_value=read))


async def _tap(env, action="admin_confirm"):
    pending = env["db"].tables["commerce_pending_confirmations"][-1]
    pending.setdefault("status", "pending")
    await wa._handle_admin_confirm_reply(PHONE, f"{action}:{pending['id']}", TID)
    return pending


@pytest.mark.asyncio
async def test_delivery_note_photo_previews_then_books_on_confirm(env, monkeypatch):
    _photo(monkeypatch, {"kind": "delivery_note", "reference": "DN 4471", "lines": [
        {"description": "HAKE FILLETS", "quantity": 6, "unit_price_rands": 52.5},
        {"description": "Tiger prawns 1kg", "quantity": 2},
        {"description": "Squid tubes", "quantity": 3}]})
    assert await wa._handle_stock_photo(PHONE, "m1", "received", TID) is True
    summary = env["buttons"][0]["summary"]
    assert "6 × Hake Fillet" in summary and "2 × Tiger Prawns 1kg" in summary
    assert "Squid tubes" in summary and "not booked" in summary
    assert _moves(env["db"]) == []                                    # nothing moves on a photo
    pending = env["db"].tables["commerce_pending_confirmations"][0]
    assert pending["tool_name"] == "receive_stock" and pending["phone"] == PHONE

    await _tap(env)
    stock = {p["id"]: p["stock_quantity"] for p in env["db"].tables["commerce_products"]}
    assert stock == {"p1": 16, "p2": 6}
    assert {m["reason"] for m in _moves(env["db"])} == {"receive"}
    assert {m["ref_id"] for m in _moves(env["db"])} == {"DN 4471"}
    assert env["db"].tables["commerce_products"][0]["cost_cents"] == 5250


@pytest.mark.asyncio
async def test_cancel_books_nothing(env, monkeypatch):
    _photo(monkeypatch, {"kind": "delivery_note", "lines": [{"description": "hake fillet", "quantity": 6}]})
    await wa._handle_stock_photo(PHONE, "m1", "delivery", TID)
    await _tap(env, "admin_cancel")
    assert _moves(env["db"]) == [] and env["db"].tables["commerce_products"][0]["stock_quantity"] == 10


@pytest.mark.asyncio
async def test_a_delivery_note_with_nothing_matching_says_so(env, monkeypatch):
    _photo(monkeypatch, {"kind": "delivery_note", "lines": [{"description": "Cement 50kg", "quantity": 20}]})
    await wa._handle_stock_photo(PHONE, "m1", "received", TID)
    assert env["buttons"] == [] and "nothing was booked" in env["replies"][0]


@pytest.mark.asyncio
async def test_barcode_photo_then_a_count_reply(env, monkeypatch):
    _photo(monkeypatch, {"kind": "barcode_label", "barcodes": ["6001 234567890"]})
    await wa._handle_stock_photo(PHONE, "m1", "stock count", TID)
    assert "*Hake Fillet* — in stock: 10" in env["replies"][0]

    assert await wa._maybe_stock_followup(PHONE, "12", TID) is True
    assert "Current stock: 10" in env["buttons"][0]["summary"]
    assert "New stock: 12" in env["buttons"][0]["summary"]
    await _tap(env)
    assert env["db"].tables["commerce_products"][0]["stock_quantity"] == 12
    m = _moves(env["db"])[0]
    assert (m["delta"], m["reason"], m["actor"]) == (2, "adjust", PHONE)
    # The item is used once — a later number is just a message again.
    assert await wa._maybe_stock_followup(PHONE, "5", TID) is False


@pytest.mark.asyncio
async def test_plus_n_adds_to_whatever_is_there_at_confirm(env, monkeypatch):
    _photo(monkeypatch, {"kind": "barcode_label", "barcodes": [], "product_text": "Tiger prawns 1kg"})
    await wa._handle_stock_photo(PHONE, "m1", "count", TID)
    assert await wa._maybe_stock_followup(PHONE, "+3", TID) is True
    env["db"].tables["commerce_products"][1]["stock_quantity"] = 3     # a sale lands meanwhile
    await _tap(env)
    assert env["db"].tables["commerce_products"][1]["stock_quantity"] == 6


@pytest.mark.asyncio
async def test_unknown_barcode_points_to_linking_it(env, monkeypatch):
    _photo(monkeypatch, {"kind": "barcode_label", "barcodes": ["5000000000001"]})
    await wa._handle_stock_photo(PHONE, "m1", "stock", TID)
    assert "5000000000001" in env["replies"][0] and "Sell › Stock" in env["replies"][0]
    assert await wa._maybe_stock_followup(PHONE, "4", TID) is False


@pytest.mark.asyncio
async def test_a_shelf_photo_is_never_counted(env, monkeypatch):
    _photo(monkeypatch, {"kind": "shelf", "barcodes": [], "product_text": "", "lines": []})
    await wa._handle_stock_photo(PHONE, "m1", "how many hake do we have", TID)
    assert "won't guess" in env["replies"][0] and env["buttons"] == []


@pytest.mark.asyncio
async def test_staff_without_stock_access_get_normal_photo_handling(env, monkeypatch):
    import core.skills.commerce_admin as ca
    monkeypatch.setattr(ca, "_member_access", lambda tid, phone: ["orders"])
    _photo(monkeypatch, {"kind": "delivery_note", "lines": [{"description": "hake", "quantity": 1}]})
    assert await wa._handle_stock_photo(PHONE, "m1", "received", TID) is False


@pytest.mark.asyncio
async def test_only_staff_stock_captions_take_the_stock_path(env, monkeypatch):
    stock = AsyncMock(return_value=True)
    monkeypatch.setattr(wa, "_handle_stock_photo", stock)
    monkeypatch.setattr(wa, "_sender_is_sales_rep", AsyncMock(return_value=False))
    monkeypatch.setattr(wa, "_recently_asked_about_signature", lambda phone: False)
    monkeypatch.setattr(wa, "_handle_media", AsyncMock(return_value=True))

    monkeypatch.setattr(wa, "_is_tenant_owner", lambda tid, phone: True)
    await wa._handle_image_or_video(PHONE, "image", "m1", "Delivery from Sea Harvest", "image/jpeg", "w1",
                                    "commerce", TID, None)
    assert stock.await_count == 1
    await wa._handle_image_or_video(PHONE, "image", "m1", "lunch receipt", "image/jpeg", "w1",
                                    "commerce", TID, None)
    assert stock.await_count == 1                                     # not a stock caption

    monkeypatch.setattr(wa, "_is_tenant_owner", lambda tid, phone: False)   # a customer
    await wa._handle_image_or_video(PHONE, "image", "m1", "how many in stock?", "image/jpeg", "w1",
                                    "commerce", TID, None)
    assert stock.await_count == 1
