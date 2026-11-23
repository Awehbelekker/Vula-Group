"""Booking a courier from an order (vula/couriers, vula/commerce/shipping.py, 23 Nov 2026).

The request bodies are checked against the couriers' own published examples; a booking is only
made at the price shown, once per order, and read back; tracking moves the order forward only;
a webhook is a pointer, never believed."""
import pytest

from tests.fake_supabase import FakeSupabase
from vula import couriers
from vula.commerce import order_updates, service, shipping

TID = "seedleme"
OID = "0a000000-0000-0000-0000-000000000001"
ORDER = {"id": OID, "tenant_id": TID, "display_id": "SEE-00042", "status": "confirmed",
         "customer_name": "Thandi Mokoena", "customer_phone": "0821234567", "customer_email": "",
         "delivery_address": "12 Main Road, Sea Point, Cape Town, 8005", "delivery_notes": "Gate code 4411",
         "commerce_order_items": [{"product_id": "p-1", "quantity": 2}]}
SETTINGS = {"tenant_id": TID,
            "collection_address": {"company": "SeedleMe", "street_address": "5 Kloof Street", "local_area": "Gardens",
                                   "city": "Cape Town", "zone": "WC", "code": "8001", "lat": -33.93, "lng": 18.41,
                                   "type": "business"},
            "collection_contact": {"name": "Sipho", "mobile_number": "+27825550000", "email": ""},
            "default_parcel": {"length_cm": 30, "width_cm": 20, "height_cm": 10, "weight_kg": 2}}


class FakeCourier:
    name, lockers = "tcg", True

    def __init__(self):
        self.calls, self.price, self.status, self.fail_create, self.lost = [], "105.80", "submitted", None, False

    async def rates(self, creds, mode, **kw):
        self.calls.append(("rates", kw))
        return [couriers.Rate(code="ECO", name="Economy", cents=couriers.rand_to_cents(self.price)),
                couriers.Rate(code="OVN", name="Overnight", cents=18950)]

    async def create(self, creds, mode, **kw):
        self.calls.append(("create", kw))
        if self.lost:
            raise RuntimeError("connection reset")
        if self.fail_create:
            raise couriers.CourierError(self.fail_create)
        return couriers.Booked(shipment_id="14135", tracking_reference="G9G", status="submitted",
                               cents=10580, service_code=kw["service_code"], service_name="Economy")

    async def track(self, creds, mode, **kw):
        self.calls.append(("track", kw))
        return couriers.Tracking(status=self.status, events=[{"status": self.status, "at": "2026-11-23T10:00:00Z",
                                                              "message": "", "location": ""}])

    async def cancel(self, creds, mode, **kw):
        self.calls.append(("cancel", kw))
        self.status = "cancelled"

    def made(self, what):
        return [kw for name, kw in self.calls if name == what]


@pytest.fixture
def world(monkeypatch):
    db = FakeSupabase({"commerce_courier_settings": [dict(SETTINGS)],
                       "commerce_products": [{"id": "p-1", "tenant_id": TID, "weight_grams": 750}],
                       "commerce_orders": [dict(ORDER)],
                       "vula_courier_providers": [{"tenant_id": TID, "provider": "tcg", "active": True,
                                                   "webhook_token": "tok-123", "mode": "live"}]})
    fake = FakeCourier()
    out = {"db": db, "courier": fake, "moves": [], "told": [], "team": []}
    monkeypatch.setattr(service, "_client", lambda: db)
    monkeypatch.setattr(couriers, "provider_row", lambda tid, provider=None: {
        "provider": "tcg", "mode": "live", "credentials": {"api_key": "k"}} if tid == TID else None)
    monkeypatch.setattr(couriers, "get_courier", lambda name: fake)

    async def get_order(order_id):
        rows = [r for r in db.rows["commerce_orders"] if r["id"] == order_id]
        return dict(rows[0]) if rows else None

    async def update_status(order_id, status, **kw):
        out["moves"].append(status)
        for r in db.rows["commerce_orders"]:
            if r["id"] == order_id:
                r["status"] = status

    async def stock(order_id, restore=False):
        return True

    async def notify(tenant_id, order, status, note=""):
        out["told"].append((status, note))
        return True

    async def notify_team(tenant_id, event, message, idem_key=None):
        out["team"].append((event, message, idem_key))
        return 1

    monkeypatch.setattr(service, "get_order", get_order)
    monkeypatch.setattr(service, "update_order_status", update_status)
    monkeypatch.setattr(service, "apply_order_stock", stock)
    monkeypatch.setattr(order_updates, "notify", notify)
    monkeypatch.setattr("vula.integrations.notify.notify_team", notify_team)
    monkeypatch.setattr("vula.api.merchant_audit.audit", lambda *a, **k: None)
    return out


# ── reading addresses and money in code ──────────────────────────────────────

def test_an_address_is_split_and_what_cannot_be_told_is_left_empty():
    a = shipping.parse_address("12 Main Road, Sea Point, Cape Town, 8005, South Africa")
    assert (a["street_address"], a["local_area"], a["city"], a["code"]) == ("12 Main Road", "Sea Point", "Cape Town", "8005")
    b = shipping.parse_address("4 Vlei St\nParow\nWestern Cape 7500")
    assert (b["street_address"], b["city"], b["zone"], b["code"]) == ("4 Vlei St", "Parow", "WC", "7500")
    assert shipping.address_gaps(shipping.parse_address("the blue house by the shop")) == ["town or city", "postal code"]
    assert shipping.address_gaps(shipping.parse_address("")) == ["street address", "town or city", "postal code"]


def test_rand_becomes_cents_half_up_and_never_a_guess():
    assert couriers.rand_to_cents("705.56") == 70556
    assert couriers.rand_to_cents(205.5625) == 20556
    assert couriers.rand_to_cents(82.5) == 8250
    assert couriers.rand_to_cents("1,250.005") == 125001
    assert couriers.rand_to_cents(None) is None and couriers.rand_to_cents("n/a") is None
    assert couriers.rand_to_cents(-5) is None


def test_phone_numbers_go_out_the_way_the_courier_wants():
    assert shipping.mobile("082 123 4567") == "+27821234567"
    assert shipping.mobile("27821234567") == "+27821234567"
    assert shipping.mobile("call me") == ""


# ── quote ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_quote_prices_the_order_and_books_nothing(world):
    q = await shipping.quote(TID, OID)
    assert [r["code"] for r in q["rates"]] == ["ECO", "OVN"] and q["rates"][0]["cents"] == 10580
    assert q["missing"] == [] and q["address"]["city"] == "Cape Town"
    assert q["parcel"]["weight_kg"] == 1.5 and q["weight_from"] == "products"     # 2 × 750 g
    assert world["db"].rows.get("commerce_shipments", []) == [] and not world["courier"].made("create")


@pytest.mark.asyncio
async def test_a_quote_says_what_is_missing_instead_of_asking_the_courier(world):
    world["db"].rows["commerce_orders"][0]["delivery_address"] = "the blue house"
    world["db"].rows["commerce_courier_settings"][0]["default_parcel"] = None
    q = await shipping.quote(TID, OID)
    assert q["rates"] == [] and not world["courier"].made("rates")
    assert any("town or city" in m for m in q["missing"]) and any("usual parcel" in m for m in q["missing"])


@pytest.mark.asyncio
async def test_no_courier_connected_is_said_plainly(world, monkeypatch):
    monkeypatch.setattr(couriers, "provider_row", lambda tid, provider=None: None)
    with pytest.raises(shipping.ShippingError, match="No courier is connected"):
        await shipping.quote(TID, OID)


# ── book ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_booking_at_the_price_shown_is_read_back(world):
    s = await shipping.book(TID, OID, service_code="ECO", expected_cents=10580, actor={"email": "sipho@x.co.za"})
    assert s["tracking_reference"] == "G9G" and s["verified"] is True and s["status"] == "submitted"
    assert s["quoted_cents"] == 10580 and s["booked_by"] == "sipho@x.co.za"
    sent = world["courier"].made("create")[0]
    assert sent["reference"] == "SEE-00042" and sent["instructions"] == "Gate code 4411"
    assert sent["delivery_contact"]["mobile_number"] == "+27821234567"
    assert world["courier"].made("track")                    # the read-back happened
    assert world["moves"] == []                              # booked isn't dispatched


@pytest.mark.asyncio
async def test_a_price_that_moved_is_refused_and_nothing_is_booked(world):
    world["courier"].price = "119.00"
    with pytest.raises(shipping.ShippingError) as exc:
        await shipping.book(TID, OID, service_code="ECO", expected_cents=10580)
    assert exc.value.detail["price_cents"] == 11900 and "Nothing was booked" in str(exc.value)
    assert not world["courier"].made("create") and world["db"].rows.get("commerce_shipments", []) == []


@pytest.mark.asyncio
async def test_an_order_is_booked_once(world):
    await shipping.book(TID, OID, service_code="ECO", expected_cents=10580)
    with pytest.raises(shipping.ShippingError, match="already has a courier booked"):
        await shipping.book(TID, OID, service_code="ECO", expected_cents=10580)
    assert len(world["courier"].made("create")) == 1


@pytest.mark.asyncio
async def test_a_refused_booking_is_kept_as_failed_and_can_be_tried_again(world):
    world["courier"].fail_create = "The courier refused this: insufficient account balance"
    with pytest.raises(shipping.ShippingError, match="insufficient account balance"):
        await shipping.book(TID, OID, service_code="ECO", expected_cents=10580)
    row = world["db"].rows["commerce_shipments"][0]
    assert row["status"] == "failed" and row["open"] is False
    world["courier"].fail_create = None
    assert (await shipping.book(TID, OID, service_code="ECO", expected_cents=10580))["tracking_reference"] == "G9G"


@pytest.mark.asyncio
async def test_a_lost_answer_blocks_a_second_booking_until_someone_has_looked(world):
    world["courier"].lost = True
    with pytest.raises(shipping.ShippingError, match="can't tell whether it was booked"):
        await shipping.book(TID, OID, service_code="ECO", expected_cents=10580)
    row = world["db"].rows["commerce_shipments"][0]
    assert row["status"] == "booking"
    world["courier"].lost = False
    with pytest.raises(shipping.ShippingError, match="already has a courier booked"):
        await shipping.book(TID, OID, service_code="ECO", expected_cents=10580)
    cleared = await shipping.cancel(TID, row["id"])          # the person checked: it never went through
    assert cleared["status"] == "failed" and not world["courier"].made("cancel")


@pytest.mark.asyncio
async def test_a_cancelled_or_refunded_order_is_never_sent(world):
    world["db"].rows["commerce_orders"][0]["status"] = "refunded"
    with pytest.raises(shipping.ShippingError, match="nothing to send"):
        await shipping.book(TID, OID, service_code="ECO", expected_cents=10580)


@pytest.mark.asyncio
async def test_another_business_cannot_touch_the_order_or_the_booking(world):
    with pytest.raises(shipping.ShippingError, match="Order not found"):
        await shipping.quote("digg", OID)
    s = await shipping.book(TID, OID, service_code="ECO", expected_cents=10580)
    with pytest.raises(shipping.ShippingError, match="wasn't found"):
        await shipping.cancel("digg", s["id"])


@pytest.mark.asyncio
async def test_to_a_locker_needs_no_street_address(world):
    world["db"].rows["commerce_orders"][0]["delivery_address"] = ""
    s = await shipping.book(TID, OID, service_code="ECO", expected_cents=10580,
                            pickup_point={"id": "CG54", "name": "Sasol Rivonia"})
    assert s["delivery_kind"] == "pickup_point" and s["pickup_point_id"] == "CG54"
    assert world["courier"].made("create")[0]["pickup_point_id"] == "CG54"


# ── afterwards ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_cancellation_is_read_back(world):
    s = await shipping.book(TID, OID, service_code="ECO", expected_cents=10580)
    done = await shipping.cancel(TID, s["id"])
    assert done["status"] == "cancelled" and done["live"] is False
    assert world["courier"].made("cancel")[0]["tracking_reference"] == "G9G"


@pytest.mark.asyncio
async def test_collected_moves_the_order_on_and_the_customer_gets_the_tracking_number_once(world):
    s = await shipping.book(TID, OID, service_code="ECO", expected_cents=10580)
    world["courier"].status = "collected"
    await shipping.refresh(TID, s["id"])
    world["courier"].status = "in-transit"
    await shipping.refresh(TID, s["id"])
    assert world["moves"] == ["dispatched"]
    assert world["told"] == [("dispatched", "Sent with The Courier Guy. Tracking number: G9G")]
    world["courier"].status = "delivered"
    out = await shipping.refresh(TID, s["id"])
    assert world["moves"] == ["dispatched", "delivered"] and out["status_text"] == "Delivered"
    assert world["db"].rows["commerce_shipments"][0]["open"] is False


@pytest.mark.asyncio
async def test_tracking_never_moves_an_order_backwards_or_an_unpaid_or_cancelled_one(world):
    s = await shipping.book(TID, OID, service_code="ECO", expected_cents=10580)
    for status in ("delivered", "cancelled", "pending_payment"):
        world["db"].rows["commerce_orders"][0]["status"] = status
        world["courier"].status = "collected"
        await shipping.refresh(TID, s["id"])
    assert world["moves"] == [] and world["told"] == []


@pytest.mark.asyncio
async def test_a_failed_delivery_reaches_the_team_once(world):
    s = await shipping.book(TID, OID, service_code="ECO", expected_cents=10580)
    world["courier"].status = "delivery-failed-attempt"
    await shipping.refresh(TID, s["id"])
    await shipping.refresh(TID, s["id"])
    assert len(world["team"]) == 1 and world["team"][0][0] == "delivery_problem"
    assert "SEE-00042" in world["team"][0][1] and "Delivery attempt failed" in world["team"][0][1]


@pytest.mark.asyncio
async def test_a_webhook_is_only_a_pointer(world):
    s = await shipping.book(TID, OID, service_code="ECO", expected_cents=10580)
    payload = {"short_tracking_reference": "G9G", "status": "delivered"}            # a forged "delivered"
    assert await shipping.handle_webhook(TID, "tcg", "wrong", payload) == 0
    assert await shipping.handle_webhook(TID, "tcg", "", payload) == 0
    assert await shipping.handle_webhook("digg", "tcg", "tok-123", payload) == 0
    assert world["moves"] == []
    assert await shipping.handle_webhook(TID, "tcg", "tok-123", payload) == 1
    assert world["moves"] == []                              # the courier itself still says "submitted"
    world["courier"].status = "collected"
    assert await shipping.handle_webhook(TID, "tcg", "tok-123", [payload]) == 1
    assert world["moves"] == ["dispatched"] and s["id"]


@pytest.mark.asyncio
async def test_the_hourly_check_reads_only_bookings_still_on_their_way(world):
    s = await shipping.book(TID, OID, service_code="ECO", expected_cents=10580)
    world["db"].rows["commerce_shipments"].append(
        {"id": "x", "tenant_id": TID, "status": "delivered", "open": False, "tracking_reference": "OLD",
         "created_at": "2099-01-01T00:00:00+00:00"})
    world["db"].rows["commerce_shipments"][0].update(created_at="2099-01-01T00:00:00+00:00", last_checked_at=None)
    world["courier"].calls.clear()
    assert await shipping.refresh_open() == {"checked": 1, "failed": 0}
    assert [kw["tracking_reference"] for kw in world["courier"].made("track")] == ["G9G"] and s["id"]


# ── what is sent to each courier (their own published examples) ──────────────

@pytest.fixture
def wire(monkeypatch):
    sent = []

    async def call(method, url, key, *, params=None, body=None):
        sent.append({"method": method, "url": url, "key": key, "params": params, "body": body})
        if url.endswith("/rates"):
            return {"rates": [{"rate": "705.56", "rate_excluding_vat": 613.53,
                               "service_level": {"code": "OVN", "name": "Overnight",
                                                 "delivery_date_from": "25 Apr, 2024 12:00 AM"}},
                              {"rate": "82.50", "service_level": {
                                  "code": "D2LXS - ECO", "name": "Door to Locker Extra Small", "box_type_name": "V4-XS",
                                  "dimensions": {"width": 17, "height": 8, "length": 60, "weight": 2}}}]}
        if url.endswith("/shipments") and method == "POST":
            return {"id": 6870623, "custom_tracking_reference": "TCGD000500", "short_tracking_reference": "S7GL",
                    "status": "submitted", "rate": 205.5625, "service_level_name": "Overnight"}
        if "/tracking/" in url:
            return {"shipment_id": 984, "status": False, "tracking_events": [
                {"date": "2024-07-15 07:02:23.495234", "status": "deposit-pending"},
                {"date": "2024-07-15 07:02:23.495157", "status": "submitted"}]}
        if url.endswith("/lockers-data"):
            return [{"code": "CG54", "name": "Sasol Rivonia Uplifted", "latitude": "-26.049703",
                     "longitude": "28.059084", "address": "375 Rivonia Rd", "type": {"id": 2, "name": "Locker"},
                     "lstTypesBoxes": [{"name": "V4-XS", "width": 17, "height": 8, "length": 60, "weight": 2}]},
                    {"code": "CG10", "name": "Far away", "latitude": "-33.9", "longitude": "18.4"}]
        return {"url": "https://s3.example/label.pdf"}

    monkeypatch.setattr(couriers, "_call", call)
    return sent


ADDR = {"street_address": "10 Midas Ave", "local_area": "Olympus AH", "city": "Pretoria", "zone": "GP",
        "code": "0081", "lat": -25.8, "lng": 28.33, "type": "residential", "company": ""}
PARCEL = [{"length_cm": 20, "width_cm": 20, "height_cm": 10, "weight_kg": 2}]
WHO = {"name": "A", "mobile_number": "+27820000000", "email": ""}


@pytest.mark.asyncio
async def test_the_courier_guy_live_and_test_hosts_and_bodies(wire):
    tcg = couriers.CourierGuy()
    rates = await tcg.rates({"api_key": "k"}, "live", collection=ADDR, delivery=ADDR, parcels=PARCEL)
    assert wire[0]["url"] == "https://api.portal.thecourierguy.co.za/rates" and wire[0]["key"] == "k"
    assert wire[0]["body"]["delivery_address"]["country"] == "ZA"
    assert wire[0]["body"]["parcels"] == [{"submitted_length_cm": 20.0, "submitted_width_cm": 20.0,
                                           "submitted_height_cm": 10.0, "submitted_weight_kg": 2.0}]
    assert [(r.code, r.cents) for r in rates] == [("D2LXS - ECO", 8250), ("OVN", 70556)]      # cheapest first
    assert rates[0].box.startswith("V4-XS") and rates[1].excl_vat_cents == 61353

    b = await tcg.create({"api_key": "k"}, "test", collection=ADDR, collection_contact=WHO, delivery=ADDR,
                         delivery_contact=WHO, parcels=PARCEL, service_code="OVN", reference="SEE-00042",
                         pickup_point_id="CG01")
    body = wire[1]["body"]
    assert wire[1]["url"] == "https://api.shiplogic.com/shipments"
    assert body["delivery_pickup_point_id"] == "CG01" and "delivery_address" not in body
    assert body["customer_reference"] == "SEE-00042" and body["service_level_code"] == "OVN"
    assert (b.shipment_id, b.tracking_reference, b.cents) == ("6870623", "S7GL", 20556)

    await tcg.cancel({"api_key": "k"}, "live", tracking_reference="S7GL")
    assert wire[2]["url"].endswith("/shipments/cancel") and wire[2]["body"] == {"tracking_reference": "S7GL"}
    assert await tcg.label({"api_key": "k"}, "live", shipment_id="6870623") == "https://s3.example/label.pdf"
    assert wire[3]["params"] == {"id": "6870623"}


@pytest.mark.asyncio
async def test_pudo_names_a_locker_by_its_terminal_and_prices_by_box(wire):
    pudo = couriers.Pudo()
    await pudo.rates({"api_key": "k"}, "live", collection=ADDR, delivery={}, parcels=PARCEL, pickup_point_id="CG10")
    assert wire[0]["url"] == "https://api-tcg.co.za/rates"
    assert wire[0]["body"]["delivery_address"] == {"terminal_id": "CG10"} and "parcels" not in wire[0]["body"]
    assert wire[0]["body"]["collection_address"]["country"] == "South Africa"

    b = await pudo.create({"api_key": "k"}, "live", collection={}, collection_contact=WHO, delivery={},
                          delivery_contact=WHO, parcels=PARCEL, service_code="L2LXS - ECO", reference="SEE-1",
                          pickup_point_id="CG10", collection_point_id="K067")
    assert wire[1]["body"]["collection_address"] == {"terminal_id": "K067"} and b.tracking_reference == "TCGD000500"

    t = await pudo.track({"api_key": "k"}, "live", tracking_reference="TCGD000500")
    assert t.status == "deposit-pending" and [e["status"] for e in t.events] == ["submitted", "deposit-pending"]

    await pudo.cancel({"api_key": "k"}, "live", tracking_reference="TCGD000500", shipment_id="6870623")
    assert (wire[3]["method"], wire[3]["url"]) == ("PUT", "https://api-tcg.co.za/shipments/6870623")
    assert wire[3]["body"] == {"status": "cancelled"}

    near = await pudo.pickup_points({"api_key": "k"}, "live", lat=-26.05, lng=28.06)
    assert near[0].id == "CG54" and near[0].km < 1 and near[0].boxes[0]["name"] == "V4-XS" and near[1].km > 1000
    with pytest.raises(couriers.CourierError, match="live account"):
        pudo.base("test")


def test_what_a_courier_status_means_for_the_order():
    assert couriers.order_status_for("submitted") is None and couriers.order_status_for("collection-assigned") is None
    assert couriers.order_status_for("collected") == "dispatched" and couriers.order_status_for("in-locker") == "dispatched"
    assert couriers.order_status_for("delivered") == "delivered"
    assert couriers.order_status_for("collected-from-locker") == "delivered"
    assert couriers.order_status_for("delivery-failed-attempt") is None
    assert couriers.plain_status("at-destination-hub") == "At the depot nearest the customer"


def test_a_courier_key_is_stored_wrapped_and_never_listed(monkeypatch):
    db = FakeSupabase()
    monkeypatch.setattr(service, "_client", lambda: db)
    first = couriers.upsert_provider(TID, "tcg", "sk-live-123", "live")
    assert first["is_default"] is True and len(first["webhook_token"]) == 48 and "credentials" not in first
    stored = db.rows["vula_courier_providers"][0]["credentials"]
    assert "sk-live-123" not in str(stored) and "_enc" in stored
    listed = couriers.list_providers(TID)
    assert listed[0]["connected"] is True and "sk-live-123" not in str(listed)
    assert couriers.provider_row(TID)["credentials"]["api_key"] == "sk-live-123"
    assert couriers.provider_row("digg") is None
    again = couriers.upsert_provider(TID, "tcg", "sk-live-456", "test")          # a new key keeps the address
    assert again["webhook_token"] == first["webhook_token"] and len(db.rows["vula_courier_providers"]) == 1
    with pytest.raises(ValueError):
        couriers.upsert_provider(TID, "pudo", "k", "test")                       # PUDO has no test mode
    with pytest.raises(ValueError):
        couriers.upsert_provider(TID, "dhl", "k")


# ── the business assistant: price first, then exactly that price ─────────────

@pytest.fixture
def assistant(world, monkeypatch):
    from core.skills.commerce_admin import CommerceAdminSkill
    skill = CommerceAdminSkill()

    async def find(tid, display_id):
        return {"id": OID, "display_id": "SEE-00042", "status": "confirmed"} if display_id == "SEE-00042" else None

    monkeypatch.setattr(skill, "_find_order_by_display_id", find)
    return skill


@pytest.mark.asyncio
async def test_the_assistant_shows_the_price_and_the_yes_books_that_price(world, assistant):
    from core.skills import commerce_admin as ca
    assert "book_courier" in ca._PREVIEW_FIRST
    args = {"order_id": "SEE-00042", "confirm": True}        # a model that says yes for the owner still gets the preview…
    shown = await assistant._book_courier(TID, args, {})
    assert shown["preview"] is True and shown["price"].startswith("R105.80") and shown["service"] == "Economy"
    assert shown["deliver_to"] == "Thandi Mokoena, 12 Main Road, Sea Point, Cape Town, 8005"
    assert shown["other_services"] == "Overnight R189.50" and "1.5 kg" in shown["parcel"]
    assert (args["service"], args["expected_cents"]) == ("ECO", 10580)           # …what the yes will run
    assert not world["courier"].made("create")

    done = await assistant._book_courier(TID, {**args, "confirm": True}, {"caller_name": "Sipho"})
    assert done["verified"] is True and done["waybill"] == "G9G"
    assert done["reply_verbatim"].startswith("✅ Courier booked for SEE-00042: The Courier Guy Economy, R105.80.")
    assert len(world["courier"].made("create")) == 1


@pytest.mark.asyncio
async def test_the_assistant_never_books_at_a_price_that_moved(world, assistant):
    args = {"order_id": "SEE-00042"}
    await assistant._book_courier(TID, args, {})
    world["courier"].price = "140.00"
    out = await assistant._book_courier(TID, {**args, "confirm": True}, {})
    assert out["reply_verbatim"].startswith("⚠️ The price changed to R140.00. Nothing was booked")
    assert not world["courier"].made("create")


@pytest.mark.asyncio
async def test_the_assistant_says_what_is_missing_or_already_booked(world, assistant):
    assert "No order" in (await assistant._book_courier(TID, {"order_id": "NOPE-1"}, {}))["error"]
    asked = await assistant._book_courier(TID, {"order_id": "SEE-00042", "service": "same day"}, {})
    assert asked["status"] == "need_info" and "Economy R105.80; Overnight R189.50" in asked["message"]
    world["db"].rows["commerce_orders"][0]["delivery_address"] = "the blue house"
    missing = await assistant._book_courier(TID, {"order_id": "SEE-00042"}, {})
    assert missing["status"] == "need_info" and "town or city" in missing["message"]
    world["db"].rows["commerce_orders"][0]["delivery_address"] = ORDER["delivery_address"]
    await shipping.book(TID, OID, service_code="ECO", expected_cents=10580)
    again = await assistant._book_courier(TID, {"order_id": "SEE-00042"}, {})
    assert again["status"] == "need_info" and "already has a courier booked" in again["message"] and "G9G" in again["message"]

    where = await assistant._courier_status(TID, "SEE-00042")
    assert where["courier_booked"] and where["waybill"] == "G9G" and where["status"] == "Booked"
    assert where["checked_with_courier_now"] is True
