"""Finding a street address for the courier forms (vula/commerce/address_search.py, 24 Nov 2026):
what the map lookup answers becomes the form's own fields; asked on a button press, spaced, kept."""
import asyncio

import httpx
import pytest

from vula.commerce import address_search as finder

HIT = {"lat": "-33.9180", "lon": "18.4233", "display_name": "x",
       "address": {"house_number": "12", "road": "Main Road", "suburb": "Green Point", "city": "Cape Town",
                   "state": "Western Cape", "ISO3166-2-lvl4": "ZA-WC", "postcode": "8005", "country": "South Africa"}}


def test_a_result_becomes_the_forms_fields():
    assert finder.address_parts(HIT) == {
        "label": "12 Main Road, Green Point, Cape Town, 8005", "place": "", "street_address": "12 Main Road",
        "local_area": "Green Point", "city": "Cape Town", "zone": "WC", "code": "8005",
        "lat": -33.918, "lng": 18.4233, "has_number": True}


def test_provinces_by_code_old_code_or_name_and_odd_rows():
    with_code = lambda c: {**HIT, "address": {**HIT["address"], "ISO3166-2-lvl4": c}}  # noqa: E731
    assert finder.address_parts(with_code("ZA-NL"))["zone"] == "KZN"
    assert finder.address_parts(with_code("ZA-GT"))["zone"] == "GP"
    by_name = {k: v for k, v in HIT["address"].items() if k != "ISO3166-2-lvl4"}
    assert finder.address_parts({**HIT, "address": {**by_name, "state": "KwaZulu-Natal"}})["zone"] == "KZN"
    no_number = {k: v for k, v in HIT["address"].items() if k != "house_number"}
    p = finder.address_parts({**HIT, "address": no_number})
    assert p["street_address"] == "Main Road" and p["has_number"] is False
    town = finder.address_parts({"lat": "-33.6", "lon": "19.4",
                                 "address": {"town": "Worcester", "state": "Western Cape", "postcode": "6850 "}})
    assert town["city"] == "Worcester" and town["street_address"] == "" and town["code"] == "6850"
    assert finder.address_parts({"lat": "-30", "lon": "25", "address": {"state": "Free State"}}) is None
    assert finder.address_parts({"address": HIT["address"]}) is None                    # no position
    assert finder.address_parts({"lat": "x", "lon": "y", "address": HIT["address"]}) is None


class _Resp:
    def __init__(self, rows, status):
        self._rows, self.status_code = rows, status

    def json(self):
        return self._rows


def _client(calls, rows=None, status=200, boom=False):
    class C:
        def __init__(self, *a, **k):
            calls.append(("agent", (k.get("headers") or {}).get("User-Agent")))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            calls.append(params)
            if boom:
                raise httpx.ConnectError("down")
            return _Resp(rows if rows is not None else [HIT, HIT, {"lat": "1", "lon": "2", "address": {}}], status)
    return C


def _asked(calls):
    return [c for c in calls if isinstance(c, dict)]


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    finder._cache.clear()
    finder._suggest_cache.clear()
    monkeypatch.setattr(finder, "_lock", None)
    monkeypatch.setattr(finder, "_last", 0.0)


@pytest.mark.asyncio
async def test_search_asks_south_africa_once_per_query_drops_doubles_and_sends_no_contact(monkeypatch):
    calls = []
    monkeypatch.setattr(finder.httpx, "AsyncClient", _client(calls))
    out = await finder.search("Main Road  Green Point")
    assert [r["label"] for r in out] == ["12 Main Road, Green Point, Cape Town, 8005"]
    asked = _asked(calls)
    assert asked[0]["countrycodes"] == "za" and asked[0]["addressdetails"] == 1 and asked[0]["q"] == "Main Road Green Point"
    assert "@" not in calls[0][1]                                    # who asks is the product, never a person
    assert await finder.search("main road green point") == out and len(_asked(calls)) == 1      # kept


@pytest.mark.asyncio
async def test_too_short_asks_nobody_and_a_lookup_that_is_down_says_so_and_is_not_kept(monkeypatch):
    calls = []
    monkeypatch.setattr(finder.httpx, "AsyncClient", _client(calls, boom=True))
    assert await finder.search("12 M") == [] and not calls
    with pytest.raises(finder.LookupDown):
        await finder.search("12 Main Road Green Point")
    assert not finder._cache
    monkeypatch.setattr(finder.httpx, "AsyncClient", _client(calls, status=429))
    with pytest.raises(finder.LookupDown):
        await finder.search("9 Long Street Cape Town")
    assert not finder._cache


@pytest.mark.asyncio
async def test_two_searches_are_spaced_a_second_apart(monkeypatch):
    calls, slept = [], []
    monkeypatch.setattr(finder.httpx, "AsyncClient", _client(calls))

    async def nap(s):
        slept.append(s)
    monkeypatch.setattr(asyncio, "sleep", nap)
    await finder.search("12 Main Road Green Point")
    await finder.search("9 Long Street Cape Town")
    assert len(_asked(calls)) == 2 and slept and 0 < slept[-1] <= 1.1


# ── as you type, and a pin on the map (25 Nov 2026) — shapes taken from the services' real answers ──
STREET = {"geometry": {"coordinates": [18.409199, -33.9075972]},
          "properties": {"name": "Main Road", "district": "Green Point", "city": "Cape Town", "state": "Western Cape",
                         "postcode": "8005", "countrycode": "ZA", "osm_key": "highway", "type": "street"}}
HOUSE = {"geometry": {"coordinates": [28.2812269, -25.7863098]},
         "properties": {"name": "The Capital Hotel", "housenumber": "194", "street": "Bancor Avenue",
                        "district": "Tshwane Ward 46", "locality": "Menlyn", "city": "Pretoria", "state": "Gauteng",
                        "postcode": "0010", "countrycode": "ZA", "osm_key": "tourism", "type": "house"}}
ABROAD = {"geometry": {"coordinates": [27.8, -29.6]}, "properties": {"name": "long stream", "city": "Ha Ramosebo", "countrycode": "LS"}}


def test_a_suggestion_keeps_the_typed_number_when_the_map_only_knows_the_street():
    p = finder.suggestion_parts(STREET, "12")
    assert p["street_address"] == "12 Main Road" and p["has_number"] and p["number_typed"]
    assert p["label"] == "12 Main Road, Green Point, Cape Town, 8005" and p["zone"] == "WC" and p["lat"] == -33.9075972
    bare = finder.suggestion_parts(STREET)
    assert bare["street_address"] == "Main Road" and not bare["has_number"] and not bare["number_typed"]


def test_a_named_place_keeps_its_own_number_a_ward_is_never_a_suburb_and_abroad_is_left_out():
    p = finder.suggestion_parts(HOUSE, "12")
    assert p["street_address"] == "194 Bancor Avenue" and p["place"] == "The Capital Hotel" and not p["number_typed"]
    assert p["local_area"] == "Menlyn" and p["zone"] == "GP"                       # "Tshwane Ward 46" passed over
    assert finder.suggestion_parts(ABROAD) is None
    assert finder.suggestion_parts({"properties": {"countrycode": "ZA", "name": "x"}}) is None      # no position
    ward = {**HIT, "address": {**HIT["address"], "suburb": "Cape Town Ward 115"}}
    assert finder.address_parts(ward)["local_area"] == ""
    assert finder._typed_number("12A Main Rd") == "12A" and finder._typed_number("Main Road 12") == "" and finder._typed_number("8005") == ""


class _Json:
    def __init__(self, body, status=200):
        self._b, self.status_code = body, status

    def json(self):
        return self._b


def _one(calls, body, status=200, boom=False):
    class C:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            calls.append((url, params))
            if boom:
                raise httpx.ConnectError("down")
            return _Json(body, status)
    return C


@pytest.mark.asyncio
async def test_suggestions_are_south_african_kept_and_never_an_error(monkeypatch):
    calls = []
    monkeypatch.setattr(finder.httpx, "AsyncClient", _one(calls, {"features": [STREET, ABROAD, STREET, HOUSE]}))
    out = await finder.suggest("12 main road green point")
    assert [r["label"] for r in out] == ["12 Main Road, Green Point, Cape Town, 8005",
                                         "The Capital Hotel, 194 Bancor Avenue, Menlyn, Pretoria, 0010"]
    assert "photon" in calls[0][0] and calls[0][1]["q"] == "12 main road green point"
    assert await finder.suggest("12 Main Road Green Point") == out and len(calls) == 1          # kept
    assert await finder.suggest("12 ") == [] and len(calls) == 1                                  # too little typed
    monkeypatch.setattr(finder.httpx, "AsyncClient", _one(calls, None, boom=True))
    assert await finder.suggest("9 long street") == []                                            # down: just nothing
    monkeypatch.setattr(finder.httpx, "AsyncClient", _one(calls, {"message": "slow down"}, status=429))
    assert await finder.suggest("7 kloof street") == []


@pytest.mark.asyncio
async def test_a_pin_gives_the_address_there_at_the_pins_own_position(monkeypatch):
    calls = []
    row = {"lat": "-33.9069340", "lon": "18.4091957",
           "address": {"road": "Helen Suzman Boulevard", "suburb": "Cape Town Ward 115", "city": "Cape Town",
                       "ISO3166-2-lvl4": "ZA-WC", "postcode": "8005", "country_code": "za"}}
    monkeypatch.setattr(finder.httpx, "AsyncClient", _one(calls, row))
    p = await finder.at(-33.9070, 18.4093)
    assert p["street_address"] == "Helen Suzman Boulevard" and p["local_area"] == "" and p["zone"] == "WC"
    assert (p["lat"], p["lng"]) == (-33.9070, 18.4093)                              # the pin, not the nearest road
    assert "reverse" in calls[0][0]
    assert await finder.at(51.5, -0.12) is None and len(calls) == 1                 # not in South Africa: nobody asked
    monkeypatch.setattr(finder.httpx, "AsyncClient", _one(calls, {"error": "Unable to geocode"}))
    assert await finder.at(-34.4, 17.5) is None                                      # the sea
    monkeypatch.setattr(finder.httpx, "AsyncClient", _one(calls, None, boom=True))
    with pytest.raises(finder.LookupDown):
        await finder.at(-33.9, 18.4)


@pytest.mark.asyncio
async def test_find_keeps_the_typed_number_too(monkeypatch):
    calls = []
    no_number = {**HIT, "address": {k: v for k, v in HIT["address"].items() if k != "house_number"}}
    monkeypatch.setattr(finder.httpx, "AsyncClient", _client(calls, rows=[no_number]))
    out = await finder.search("12 Main Road Green Point")
    assert out[0]["street_address"] == "12 Main Road" and out[0]["number_typed"] and out[0]["label"].startswith("12 Main Road")
