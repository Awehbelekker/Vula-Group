"""Trade costing, phase 3 (14 Nov 2026): "how much to build a 6 x 4 m wall, plastered both sides,
not painted?" — quantities from confirmed build-ups, prices from what the business paid, then its
quotes/BOQs, then checked market prices; every line with its source and date; nothing missed
silently; an Excel breakdown that adds up to the cent."""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from tests.fake_supabase import FakeSupabase
from vula.commerce import buildups, estimate, estimate_sheet, service, xlsx_check

T = "digg-demo"
OLD = (date.today() - timedelta(days=60)).isoformat()
NEW = (date.today() - timedelta(days=5)).isoformat()


@pytest.fixture()
def db(monkeypatch):
    d = FakeSupabase({buildups.TABLE: [], buildups.LINES: [], estimate.TABLE: []})
    monkeypatch.setattr(service, "_client", lambda: d)
    return d


def _confirmed(trade, item, spec, lines, unknown=()):
    b = buildups.save_proposed(T, trade=trade, item=item, spec=spec, unit="m2", lines=lines,
                               sources=[{"n": 1, "title": "Guide", "url": "https://guide"}],
                               unknown=[{"key": k, "label": k.replace("_", " ").title()} for k in unknown])
    return buildups.confirm(T, b["id"], person="judy", full_access=True)


@pytest.fixture()
def wall(db):
    _confirmed("Masonry", "one-brick wall", {"thickness": "220 mm"}, [
        {"component": "units", "qty_per_unit": 104, "unit": "each", "waste_pct": 5},
        {"component": "mortar_cement", "qty_per_unit": "0.5", "unit": "bag"},
        {"component": "mortar_sand", "qty_per_unit": "0.06", "unit": "m3"},
        {"component": "labour", "qty_per_unit": "1.5", "unit": "hour"},
    ], unknown=("wall_ties",))
    _confirmed("Plastering", "plaster to brick walls", {}, [
        {"component": "cement", "qty_per_unit": "0.12", "unit": "bag"},
        {"component": "plaster_sand", "qty_per_unit": "0.02", "unit": "m3"},
        {"component": "labour", "qty_per_unit": "0.5", "unit": "hour"},
    ])


def _rate(desc, unit, cents, basis="paid", when=NEW, kind="material"):
    return {"description": desc, "unit": unit, "rate_cents": cents, "rate": cents / 100, "basis": basis,
            "latest_on": when, "learned": True, "kind": kind, "observations": 2,
            "source": f"Learned from 2 invoices, last {when}", "projects": ["HPC Bokaap"]}


OWN = {
    "brick": [_rate("Clay stock brick", "each", 299), _rate("Clay stock brick", "each", 350, basis="quoted")],
    "cement": [_rate("PPC cement 50kg", "bag", 9999, when=OLD)],
    "labour": [_rate("General worker", "day", 40000, kind="labour")],
}
MARKET = {"cement": 11499, "building sand": 65000, "plaster sand": 70000}


def _own_rates(tenant, term, kind=None, **kw):
    return [r for r in OWN.get(term, []) if kind in (None, r["kind"])]


async def _market(term, unit, kind="material", refresh=True, db=None):
    if term not in MARKET:
        return None
    return {"price_cents": MARKET[term], "label": term, "where": "builders.co.za's website, " + NEW,
            "scraped_at": NEW, "url": "https://builders.co.za/x", "stale": False, "refreshed": False}


@pytest.fixture()
def prices(monkeypatch):
    monkeypatch.setattr("vula.commerce.price_book.rates", _own_rates)
    monkeypatch.setattr("vula.takeoff.market.price_for", _market)
    monkeypatch.setattr("vula.api.qs.search_rates", lambda t, q, limit=25, include_learned=True: [
        {"description": "One brick walls", "unit": "m2", "rate": 554.56, "learned": True,
         "source": "Learned from 1 boq, last 2026-10-26", "projects": ["HPC Bokaap"],
         "latest_on": "2026-10-26", "observations": 1}] if "brick" in q else [])


ITEMS = [{"item": "One-brick wall", "spec": {"thickness": "220 mm"}, "quantity_expr": "6*4"},
         {"item": "Plaster to brick walls", "quantity_expr": "6 x 4 x 2"}]


def _line(est, item_no, comp):
    return next(ln for ln in est["items"][item_no]["lines"] if ln["component"] == comp)


@pytest.mark.asyncio
async def test_the_6_by_4_plastered_wall(wall, prices):
    est = await estimate.estimate(T, ITEMS, exclusions=["painting"], created_by="judy")
    w, p = est["items"]
    assert w["quantity"] == "24.000" and p["quantity"] == "48.000"
    # quantities: 24 m2 x 104 bricks x 1.05 waste
    bricks = _line(est, 0, "units")
    assert Decimal(bricks["quantity"]) == Decimal("2620.8")
    # what was paid beats what was quoted
    assert bricks["source"] == "paid" and bricks["unit_price_cents"] == 299
    assert bricks["cost_cents"] == 783619                              # 2620.8 x 299c = 783 619.2c
    # an old own price is still used, with today's market price beside it
    cement = _line(est, 0, "mortar_cement")
    assert cement["source"] == "paid" and cement["unit_price_cents"] == 9999
    assert cement["market_now_cents"] == 11499
    # no own price: the checked market price
    sand = _line(est, 0, "mortar_sand")
    assert sand["source"] == "market" and sand["unit_price_cents"] == 65000 and sand["date"] == NEW
    # a day rate becomes an hour rate at 8 hours a day
    labour = _line(est, 0, "labour")
    assert labour["unit_price_cents"] == 5000 and labour["converted"] and labour["cost_cents"] == 180000
    # totals are the sum of the lines, in cents
    assert est["total_cents"] == sum(ln["cost_cents"] for it in est["items"] for ln in it["lines"] if ln["priced"])
    assert est["totals_cents"]["labour"] == 180000 + 120000           # 36 h + 24 h at R50.00
    # nothing missed silently
    assert w["needs_figure"] == ["Wall ties"]
    assert "Brickforce" in w["not_in_buildup"] and "Damp-proof course" in w["not_in_buildup"]
    # compared with the business's own all-in rate
    c = w["compare"]
    assert c["rate_cents"] == 55456 and c["total_cents"] == 1330944 and "HPC Bokaap BOQ" in c["where"]
    assert est["id"]                                                   # kept for phase 4


@pytest.mark.asyncio
async def test_the_reply_is_built_from_the_figures(wall, prices):
    est = await estimate.estimate(T, ITEMS, exclusions=["painting"], save=False)
    text = estimate.summary(est)
    assert f"Total {estimate.rand(est['total_cents'])}" in text
    assert "Your own rate: R554.56/m2" in text and "R13 309.44" in text
    assert "your price is from " + OLD in text and "market now R114.99" in text
    assert "Needs a figure: Wall ties" in text
    assert "Not included, as asked: painting" in text
    assert "from what you paid" in text and "from market price" in text


@pytest.mark.asyncio
async def test_a_component_with_no_price_anywhere_is_listed(wall, prices, monkeypatch):
    monkeypatch.setattr("vula.takeoff.market.price_for", AsyncMock(return_value=None))
    est = await estimate.estimate(T, ITEMS[:1], save=False)
    assert "Mortar (building) sand" in est["unpriced"]
    assert _line(est, 0, "mortar_sand")["priced"] is False
    assert "Not priced (no price on file or checked online): Mortar (building) sand" in estimate.summary(est)


@pytest.mark.asyncio
async def test_no_build_up_says_so_or_researches(db, prices, monkeypatch):
    est = await estimate.estimate(T, [{"item": "One-brick wall", "quantity": 10}], save=False)
    assert est["items"][0]["status"] == "no_buildup" and est["total_cents"] == 0
    monkeypatch.setattr(buildups, "research", AsyncMock(side_effect=buildups.BuildupError("couldn't read any sources")))
    est = await estimate.estimate(T, [{"item": "One-brick wall", "quantity": 10}], research=True, save=False)
    assert est["items"][0]["message"] == "couldn't read any sources"


@pytest.mark.asyncio
async def test_an_unconfirmed_build_up_is_flagged(db, prices):
    buildups.save_proposed(T, trade="Masonry", item="half-brick wall", spec={}, unit="m2",
                           lines=[{"component": "units", "qty_per_unit": 52, "unit": "each"}], sources=[], unknown=[])
    est = await estimate.estimate(T, [{"item": "half-brick wall", "quantity": 10}], save=False)
    assert est["items"][0]["buildup_origin"] == "proposed"
    assert "not yet confirmed by an owner or manager" in estimate.summary(est)


def test_quantities_are_the_persons_own_figures():
    assert estimate.quantity_of({"quantity_expr": "6 x 4 x 2"}) == Decimal("48.000")
    assert estimate.quantity_of({"quantity": 12.5}) == Decimal("12.500")
    with pytest.raises(estimate.EstimateError, match="couldn't work out"):
        estimate.quantity_of({"quantity_expr": "six by four"})
    with pytest.raises(estimate.EstimateError, match="How much"):
        estimate.quantity_of({"item": "wall"})


@pytest.mark.asyncio
async def test_a_repeat_estimate_with_fresh_prices_makes_no_web_or_research_calls(wall, prices, monkeypatch):
    research = AsyncMock()
    monkeypatch.setattr(buildups, "research", research)
    est = await estimate.estimate(T, [{"item": "Plaster to brick walls", "quantity_expr": "3*2.4"}],
                                  research=True, save=False)
    assert est["sources"]["refreshed"] == 0 and not research.called


@pytest.mark.asyncio
async def test_the_spreadsheet_adds_up_to_the_cent(wall, prices):
    est = await estimate.estimate(T, ITEMS, exclusions=["painting"], save=False)
    data, want = estimate_sheet.workbook("DIGG", est)
    assert xlsx_check.mismatches(data, want) == []
    assert len(want) == sum(1 for it in est["items"] for ln in it["lines"] if ln["priced"]) + 3


# ── the business assistant and the routes ────────────────────────────────────

@pytest.mark.asyncio
async def test_estimate_cost_tool_replies_from_code_and_returns_the_file(wall, prices, monkeypatch):
    from core.skills import commerce_admin as ca
    monkeypatch.setattr("vula.features.on", lambda t, name: name == "trade_costing")
    assert "estimate_cost" in {t["function"]["name"] for t in ca._tools_for(T)}
    monkeypatch.setattr("vula.api.tenants.display_name", lambda t: "DIGG")
    monkeypatch.setattr("vula.api.whatsapp._upload_to_storage", lambda *a, **k: "https://storage/est.xlsx")
    monkeypatch.setattr("vula.api.whatsapp._send_invoice_document", AsyncMock(return_value=True))
    monkeypatch.setattr("vula.storage_links.signed", lambda url, ttl: url + "?signed")
    ctx = {"phone": "27820000000"}
    out = await ca.CommerceAdminSkill()._estimate_cost(T, {"items": ITEMS, "exclusions": ["painting"]}, ctx)
    assert out["reply_verbatim"].startswith("Estimate:") and "spreadsheet above" in out["reply_verbatim"]
    assert ctx["files"][0]["kind"] == "xlsx" and out["total_cents"] > 0
    need = await ca.CommerceAdminSkill()._estimate_cost(T, {"items": [{"item": "wall"}]}, {"phone": "1"})
    assert need["status"] == "need_info"


@pytest.mark.asyncio
async def test_estimate_route_and_excel_download(wall, prices, monkeypatch):
    from vula.api import trade_costing as api
    monkeypatch.setattr("vula.features.on", lambda t, name: True)
    monkeypatch.setattr(api, "_caller", AsyncMock(return_value=("judy", True)))
    monkeypatch.setattr("vula.api.tenants.display_name", lambda t: "DIGG")
    body = api.EstimateIn(items=[api.EstimateItem(**i) for i in ITEMS], exclusions=["painting"])
    got = await api.make_estimate(T, body, request=None)
    assert got["estimate"]["total_cents"] > 0 and got["text"].startswith("Estimate:")
    listed = await api.list_estimates(T)
    assert listed["estimates"][0]["id"] == got["estimate"]["id"]
    resp = await api.estimate_xlsx(T, got["estimate"]["id"])
    assert resp.body[:2] == b"PK" and "Estimate_" in resp.headers["content-disposition"]
