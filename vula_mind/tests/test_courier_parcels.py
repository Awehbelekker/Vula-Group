"""Sending a parcel that belongs to no order, and the courier account's balance
(vula/commerce/shipping.py quote_parcel / book_parcel / account, 25 Nov 2026)."""
import pytest

from tests.test_courier_booking import TID, world  # noqa: F401 — the same business, courier and database
from vula import couriers
from vula.commerce import shipping

TO = {"street_address": "10 Long Street", "local_area": "City Centre", "city": "Cape Town", "zone": "WC",
      "code": "8001", "lat": -33.92, "lng": 18.42}
WHO = {"name": "Lerato Dube", "mobile_number": "082 555 1234"}
REQ = "req-0001-abcd"


@pytest.mark.asyncio
async def test_a_parcel_is_priced_without_an_order_and_books_nothing(world):  # noqa: F811
    q = await shipping.quote_parcel(TID, address=TO, recipient=WHO)
    assert [r["code"] for r in q["rates"]] == ["ECO", "OVN"] and q["missing"] == [] and q["order"] is None
    assert q["recipient"]["mobile_number"] == "+27825551234" and q["weight_from"] == "usual"
    assert not world["courier"].made("create") and not world["db"].rows.get("commerce_shipments")


@pytest.mark.asyncio
async def test_what_a_parcel_still_needs_is_named_and_the_courier_is_not_asked(world):  # noqa: F811
    q = await shipping.quote_parcel(TID, address={"city": "Cape Town"}, recipient={"name": ""})
    assert q["rates"] == [] and not world["courier"].calls
    assert q["missing"][0] == "the receiver's name"
    assert "the receiver's phone number or email" in q["missing"]
    assert any(m.startswith("the receiver's street address") for m in q["missing"])
    with pytest.raises(shipping.ShippingError, match="still needed"):
        await shipping.book_parcel(TID, request_id=REQ, service_code="ECO", expected_cents=10580,
                                   address={"city": "Cape Town"}, recipient=WHO)
    assert not world["courier"].made("create")


@pytest.mark.asyncio
async def test_a_parcel_is_booked_at_the_price_shown_read_back_and_only_once(world):  # noqa: F811
    s = await shipping.book_parcel(TID, request_id=REQ, service_code="ECO", expected_cents=10580, address=TO,
                                   recipient=WHO, reference="  Samples for  Lerato ", instructions="Ring the bell",
                                   actor={"email": "owner@example.com"})
    assert s["tracking_reference"] == "G9G" and s["verified"] and s["order_id"] is None
    assert s["recipient"]["name"] == "Lerato Dube" and s["reference"] == "Samples for Lerato"
    sent = world["courier"].made("create")[0]
    assert sent["reference"] == "Samples for Lerato" and sent["reference_name"] == "Reference"
    assert sent["delivery_contact"]["mobile_number"] == "+27825551234" and sent["instructions"] == "Ring the bell"
    again = await shipping.book_parcel(TID, request_id=REQ, service_code="ECO", expected_cents=10580, address=TO,
                                       recipient=WHO)                       # a second tap
    assert again["id"] == s["id"] and len(world["courier"].made("create")) == 1
    assert [p["id"] for p in shipping.parcels(TID)] == [s["id"]] and shipping.parcels("another") == []
    assert shipping.shipments_for(TID, "0a000000-0000-0000-0000-000000000001") == []


@pytest.mark.asyncio
async def test_a_parcel_is_never_booked_at_a_price_that_moved_or_without_its_request_id(world):  # noqa: F811
    world["courier"].price = "119.00"
    with pytest.raises(shipping.ShippingError) as e:
        await shipping.book_parcel(TID, request_id=REQ, service_code="ECO", expected_cents=10580, address=TO, recipient=WHO)
    assert e.value.detail == {"price_cents": 11900} and not world["courier"].made("create")
    with pytest.raises(shipping.ShippingError, match="Get the prices first"):
        await shipping.book_parcel(TID, request_id="", service_code="ECO", expected_cents=11900, address=TO, recipient=WHO)


@pytest.mark.asyncio
async def test_a_parcels_news_moves_no_order_and_a_problem_names_the_receiver(world):  # noqa: F811
    s = await shipping.book_parcel(TID, request_id=REQ, service_code="ECO", expected_cents=10580, address=TO, recipient=WHO)
    world["courier"].status = "collected"
    assert (await shipping.refresh(TID, s["id"]))["status"] == "collected"
    assert world["moves"] == [] and world["told"] == []
    world["courier"].status = "delivery-failed-attempt"
    await shipping.refresh(TID, s["id"])
    assert len(world["team"]) == 1 and "the parcel to Lerato Dube" in world["team"][0][1]
    world["courier"].status = "submitted"
    out = await shipping.cancel(TID, s["id"])
    assert out["status"] == "cancelled"


# ── the account with the courier ──────────────────────────────────────────────

def test_a_balance_keeps_its_sign():
    assert couriers.signed_cents(-129.95) == -12995 and couriers.signed_cents("1,250.5") == 125050
    assert couriers.signed_cents(0) == 0 and couriers.signed_cents(None) is None and couriers.signed_cents("x") is None
    assert couriers.signed_cents(True) is None and couriers.signed_cents(float("nan")) is None


@pytest.mark.asyncio
async def test_the_courier_guy_account_as_the_sandbox_answers(monkeypatch):
    seen = []

    async def call(method, url, key, *, params=None, body=None):
        seen.append((method, url))
        return {"accounts": [{"name": "Demo", "account_type": "postpaid", "credit_limit": 10000000, "payment_terms": 30,
                              "balance": -129.95, "is_on_hold": False, "status": "open"}]}
    monkeypatch.setattr(couriers, "_call", call)
    a = await couriers.CourierGuy().account({"api_key": "k"}, "test")
    assert seen == [("GET", "https://api.shiplogic.com/accounts")]
    assert a == {"name": "Demo", "kind": "postpaid", "balance_cents": -12995, "credit_limit_cents": 1000000000,
                 "payment_terms_days": 30, "on_hold": False, "status": "open"}


def _acct(world, answer):
    async def account(creds, mode):
        if isinstance(answer, Exception):
            raise answer
        return answer
    world["courier"].account = account


@pytest.mark.asyncio
async def test_what_is_owed_and_what_is_left(world, monkeypatch):  # noqa: F811
    _acct(world, {"name": "Demo", "kind": "postpaid", "balance_cents": -12995, "credit_limit_cents": 500000,
                  "payment_terms_days": 30, "on_hold": False})
    a = await shipping.account(TID)
    assert (a["owed_cents"], a["available_cents"], a["low"], a["on_hold"]) == (12995, 487005, False, False)
    assert a["portal_url"] == "https://portal.thecourierguy.co.za" and a["known"]

    _acct(world, {"kind": "prepaid", "balance_cents": 15000, "credit_limit_cents": None, "on_hold": True})
    a = await shipping.account(TID)
    assert (a["owed_cents"], a["available_cents"], a["low"], a["on_hold"]) == (0, 15000, True, True)

    monkeypatch.setattr(couriers, "provider_row", lambda tid, provider=None: {
        "provider": "tcg", "mode": "test", "credentials": {"api_key": "k"}})
    assert (await shipping.account(TID))["portal_url"] is None              # a test account has no money to add


@pytest.mark.asyncio
async def test_an_account_the_courier_does_not_describe_is_unknown_never_a_guess(world):  # noqa: F811
    _acct(world, None)
    a = await shipping.account(TID)
    assert a["known"] is False and "balance_cents" not in a
    _acct(world, {"kind": "", "balance_cents": 5})
    assert (await shipping.account(TID))["known"] is False
    _acct(world, couriers.CourierError("The courier couldn't be reached. Nothing was booked or changed."))
    with pytest.raises(shipping.ShippingError, match="couldn't be reached"):
        await shipping.account(TID)
