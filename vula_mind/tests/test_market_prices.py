"""Trade costing, phase 1 (14 Nov 2026): market prices Vula can trust, looked up first.

The weekly scrape kept Cashbuild face bricks at R0.35–R0.55 each, a different figure every run,
and re-read every source on every deploy. A price is now kept only when its figures are printed
on the page and it sits in a plausible band; a source is read about weekly; and a market price is
a lookup unless it is missing or stale, when one web search refreshes it and saves the result."""
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from vula.takeoff import market, market_checks
from vula.takeoff.construction_rates_scraper import (ConstructionRatesScraper, MaterialRate,
                                                     RateExtractor, RatesDatabase, _parse_json_array)

PAGE = ("Cashbuild Brickwork. Face Brick Travertine R5.49 each. Clay Stock Brick R2.99 each. "
        "PPC Surebuild Cement 50kg R109.99. Plaster Sand per cube R650.00. Brick Trowel 280mm R104.00")


# ── the checks ───────────────────────────────────────────────────────────────

def test_a_price_not_printed_on_the_page_is_dropped():
    assert market_checks.check("Face Brick Travertine", "each", 0.50, 0.50, PAGE) == (False, "not_on_page", "brick")
    assert market_checks.check("Face Brick Travertine", "each", 5.49, 5.49, PAGE) == (True, "", "brick")


def test_a_printed_price_that_cant_be_a_brick_is_dropped():
    page = "Special: face brick R0.45 each when you buy 10 000"
    assert market_checks.check("Face brick", "no", 0.45, 0.45, page) == (False, "out_of_band", "brick")


def test_amounts_are_compared_whole_not_as_digits():
    assert not market_checks.grounded(72.50, 72.50, "Labour R 7 250 per day")


def test_families_and_units():
    assert market_checks.family_of("Plaster Sand per cube") == "sand"
    assert market_checks.family_of("Brick Trowel 280mm") == "tool"        # no band: a trowel isn't a brick
    assert market_checks.check("Brick Trowel 280mm", "each", 104, 104, PAGE)[0]
    assert market_checks.family_of("PPC Surebuild Cement 50kg") == "cement"
    assert market_checks.norm_unit("m²") == "m2" and market_checks.norm_unit("No") == "each"
    assert market_checks.check("Plaster Sand per cube", "m³", 650, 650, PAGE)[0]


def test_an_unknown_item_is_kept_on_grounding_alone():
    assert market_checks.check("Geotextile membrane", "roll", 5.49, 5.49, PAGE) == (True, "", None)


def test_a_cut_off_array_keeps_its_complete_rows():
    raw = ('[{"label": "Cement 50kg", "unit": "bag", "low": 109.99, "high": 109.99},\n'
           ' {"label": "Clay stock", "unit": "each", "low": 2.99, "high": 2.99},\n'
           ' {"label": "Porcelanosa Porcelanosa Porcelanosa Porc')
    rows = _parse_json_array(raw)
    assert [r["label"] for r in rows] == ["Cement 50kg", "Clay stock"]


# ── extraction ───────────────────────────────────────────────────────────────

def _answer(*rows):
    return json.dumps([{"label": label, "unit": unit, "low": p, "high": p, "notes": ""} for label, unit, p in rows])


@pytest.mark.asyncio
async def test_only_prices_that_check_out_are_kept(monkeypatch):
    seen = {}

    async def complete(task, messages, **kw):
        seen["task"], seen["accept"] = task, kw["accept"]
        return _answer(("Face Brick Travertine", "each", 5.49), ("Face Brick Travertine x", "each", 0.50),
                       ("PPC Surebuild Cement 50kg", "bag", 109.99))
    monkeypatch.setattr("vula.research._complete", complete)
    cat = await RateExtractor()._extract_one("https://shop.test/bricks", "Cashbuild", "prices", PAGE)
    assert seen["task"] == "rates_extract"
    assert cat.status == "ok" and sorted(r.label for r in cat.rates) == ["Face Brick Travertine",
                                                                          "PPC Surebuild Cement 50kg"]
    assert all(r.checked and r.url == "https://shop.test/bricks" for r in cat.rates)
    assert {r.family for r in cat.rates} == {"brick", "cement"}
    # Vula's own model answering with only made-up prices is sent to the cloud instead
    assert seen["accept"](_answer(("Face brick", "each", 0.50))) is False
    assert seen["accept"](_answer(("Face Brick Travertine", "each", 5.49))) is True
    assert seen["accept"]("[]") is True                       # an honest "no prices here"


@pytest.mark.asyncio
async def test_a_page_whose_prices_all_fail_is_unchecked_not_ok(monkeypatch):
    monkeypatch.setattr("vula.research._complete",
                        AsyncMock(return_value=_answer(("Face brick", "each", 0.50), ("Face brick 2", "each", 0.35))))
    cat = await RateExtractor()._extract_one("https://shop.test", "Cashbuild", "prices", PAGE)
    assert cat.status == "unchecked" and not cat.rates and "none checked out" in cat.error


# ── the store ────────────────────────────────────────────────────────────────

def test_a_checked_price_replaces_an_unchecked_one_and_is_mirrored(tmp_path, monkeypatch):
    mirrored = []
    monkeypatch.setattr("vula.durable.upsert", lambda table, row, **kw: mirrored.append(row) or True)
    db = RatesDatabase(db_path=tmp_path / "r.db")
    assert db.upsert(MaterialRate(key="face_brick", label="Face brick", unit="each", low=5.49, high=5.49)) == "new"
    good = MaterialRate(key="face_brick", label="Face brick", unit="each", low=5.49, high=5.49,
                        url="https://shop.test", family="brick", checked=True)
    assert db.upsert(good) == "updated"
    row = db.get_all()[0]
    assert row["checked"] is True and row["url"] == "https://shop.test" and row["family"] == "brick"
    assert mirrored[-1]["checked"] is True and mirrored[-1]["url"] == "https://shop.test"


# ── the schedule ─────────────────────────────────────────────────────────────

def _log(days_ago, status="ok", rates=10, url=None):
    return {"url": url, "status": status, "rates_extracted": rates,
            "scraped_at": (datetime.now() - timedelta(days=days_ago)).isoformat()}


@pytest.mark.parametrize("log,fresh", [
    ([_log(3)], True),                                   # read well 3 days ago: up to date for a week
    ([_log(8)], False),                                  # over a week: read again
    ([_log(0.1, "parse_failed", 0)], True),              # tried two hours ago: not again today
    ([_log(2, "parse_failed", 0), _log(9)], False),      # failed two days ago, last good read 9 days ago
    ([], False),
])
def test_a_source_is_read_about_weekly_not_on_every_deploy(monkeypatch, log, fresh):
    scraper = ConstructionRatesScraper.__new__(ConstructionRatesScraper)
    source = next(s for s in ConstructionRatesScraper.SOURCES if not s.get("refresh_days"))
    rows = [{**r, "url": r["url"] or source["url"]} for r in log]
    monkeypatch.setattr("vula.durable.select", lambda *a, **k: rows)
    assert scraper._fresh(source) is fresh


# ── looking up first ─────────────────────────────────────────────────────────

def _db(tmp_path, *rows):
    db = RatesDatabase(db_path=tmp_path / "m.db")
    for days_ago, label, unit, price, checked in rows:
        db.upsert(MaterialRate(key=label.lower().replace(" ", "_"), label=label, unit=unit, low=price,
                               high=price, source="cashbuild.co.za", url="https://cashbuild.test",
                               checked=checked,
                               scraped_at=(datetime.now() - timedelta(days=days_ago)).isoformat()))
    return db


@pytest.fixture(autouse=True)
def _no_mirror(monkeypatch):
    monkeypatch.setattr("vula.durable.upsert", lambda *a, **k: True)
    monkeypatch.setattr("vula.durable.select", lambda *a, **k: [])


def test_lookup_uses_checked_prices_only(tmp_path):
    db = _db(tmp_path, (2, "PPC Surebuild Cement 50kg", "bag", 109.99, True),
             (1, "Cement 50kg special", "bag", 5.00, False))
    found = market.lookup("cement 50kg", "bag", db=db)
    assert found["price_cents"] == 10999 and found["stale"] is False and found["refreshed"] is False
    assert found["where"].startswith("cashbuild.co.za's website")
    assert market.lookup("cement", "m3", db=db) is None              # wrong unit
    assert market.lookup("roof sheet", db=db) is None


def test_stale_after_30_days_for_materials_90_for_labour(tmp_path):
    db = _db(tmp_path, (45, "Plaster sand", "m3", 650.0, True), (45, "General worker labour", "day", 350.0, True))
    assert market.lookup("plaster sand", db=db)["stale"] is True
    assert market.lookup("general worker labour", kind="labour", db=db)["stale"] is False


@pytest.mark.asyncio
async def test_a_fresh_price_is_a_lookup_with_no_web(tmp_path):
    db = _db(tmp_path, (5, "PPC Surebuild Cement 50kg", "bag", 109.99, True))
    with patch.object(market, "research_price", new=AsyncMock()) as research:
        found = await market.price_for("cement 50kg", "bag", db=db)
    assert found["price_cents"] == 10999 and not research.called


@pytest.mark.asyncio
async def test_a_stale_price_is_refreshed_once_and_saved(tmp_path):
    db = _db(tmp_path, (45, "PPC Surebuild Cement 50kg", "bag", 99.99, True))

    async def research(description, unit=None, db=None):
        db.upsert(MaterialRate(key="ppc_cement_new", label="PPC Surebuild Cement 50kg (new)", unit="bag",
                               low=114.99, high=114.99, source="builders.co.za", checked=True))
        return 1
    with patch.object(market, "research_price", new=research):
        found = await market.price_for("cement 50kg", "bag", db=db)
    assert found["price_cents"] == 11499 and found["refreshed"] is True and found["stale"] is False
    # the next ask is a lookup again
    with patch.object(market, "research_price", new=AsyncMock()) as again:
        assert (await market.price_for("cement 50kg", "bag", db=db))["price_cents"] == 11499
    assert not again.called


@pytest.mark.asyncio
async def test_a_failed_refresh_returns_the_old_price_marked_stale(tmp_path):
    db = _db(tmp_path, (45, "PPC Surebuild Cement 50kg", "bag", 99.99, True))
    with patch.object(market, "research_price", new=AsyncMock(side_effect=RuntimeError("offline"))):
        found = await market.price_for("cement 50kg", "bag", db=db)
    assert found["price_cents"] == 9999 and found["stale"] is True and found["age_days"] >= 44


@pytest.mark.asyncio
async def test_research_searches_for_the_material_only_and_saves_checked_prices(tmp_path, monkeypatch):
    db = _db(tmp_path)
    searched = []

    async def search(queries):
        searched.extend(queries)
        return [{"title": "Cement", "url": "https://www.builders.co.za/cement"}]
    monkeypatch.setattr("vula.research._search", search)
    monkeypatch.setattr("vula.research.read_pages", AsyncMock(return_value=[
        {"title": "Cement", "url": "https://www.builders.co.za/cement",
         "text": "PPC Surebuild Cement 50kg R114.99 in stock. Afrisam 50kg R4.00 deposit"}]))
    monkeypatch.setattr("vula.research._complete", AsyncMock(return_value=_answer(
        ("PPC Surebuild Cement 50kg", "bag", 114.99), ("Afrisam Cement 50kg", "bag", 4.00))))
    saved = await market.research_price("Cement 50kg bag", "bag", db=db)
    assert saved == 1                                         # R4.00 can't be a bag of cement
    assert searched == ["cement 50kg bag price South Africa", "buy cement 50kg bag South Africa"]
    row = market.lookup("cement 50kg", "bag", db=db)
    assert row["price_cents"] == 11499 and row["supplier"] == "builders.co.za"
