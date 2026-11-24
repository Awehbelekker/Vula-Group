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
    monkeypatch.setattr(finder, "_lock", None)
    monkeypatch.setattr(finder, "_last", 0.0)


@pytest.mark.asyncio
async def test_search_asks_south_africa_once_per_query_drops_doubles_and_sends_no_contact(monkeypatch):
    calls = []
    monkeypatch.setattr(finder.httpx, "AsyncClient", _client(calls))
    out = await finder.search("12 Main Road  Green Point")
    assert [r["label"] for r in out] == ["12 Main Road, Green Point, Cape Town, 8005"]
    asked = _asked(calls)
    assert asked[0]["countrycodes"] == "za" and asked[0]["addressdetails"] == 1 and asked[0]["q"] == "12 Main Road Green Point"
    assert "@" not in calls[0][1]                                    # who asks is the product, never a person
    assert await finder.search("12 main road green point") == out and len(_asked(calls)) == 1      # kept


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
