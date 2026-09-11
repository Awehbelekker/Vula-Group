"""Tests for construction rates scraper and API endpoints."""
from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from vula.api.server import app
from vula.takeoff.construction_rates_scraper import (
    MaterialRate,
    RatesDatabase,
    ScrapedCatalogue,
    UpdateResult,
    _parse_json_array,
)

client = TestClient(app)


# ── MaterialRate dataclass ────────────────────────────────────────────────────

def test_material_rate_mid_auto_calculated():
    rate = MaterialRate(key="tiles_x", label="Tiles", unit="m²", low=300.0, high=500.0)
    assert rate.mid == 400.0


def test_material_rate_scraped_at_auto_set():
    rate = MaterialRate(key="tiles_x", label="Tiles", unit="m²", low=300.0, high=500.0)
    assert rate.scraped_at != ""


def test_material_rate_manual_mid_preserved():
    rate = MaterialRate(key="tiles_x", label="Tiles", unit="m²", low=300.0, high=500.0, mid=380.0)
    assert rate.mid == 380.0


# ── RatesDatabase ─────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    return RatesDatabase(db_path=tmp_path / "test_rates.db")


def test_db_creates_tables(tmp_path):
    db_path = tmp_path / "rates.db"
    RatesDatabase(db_path=db_path)
    conn = sqlite3.connect(db_path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert "material_rates" in tables
    assert "scrape_log" in tables


def test_db_upsert_new_rate(db):
    rate = MaterialRate(key="test_tile", label="Test Tile", unit="m²", low=280.0, high=520.0)
    status = db.upsert(rate)
    assert status == "new"


def test_db_upsert_unchanged_rate(db):
    rate = MaterialRate(key="test_tile", label="Test Tile", unit="m²", low=280.0, high=520.0)
    db.upsert(rate)
    status = db.upsert(rate)  # Same mid — no change
    assert status == "unchanged"


def test_db_upsert_updated_rate(db):
    rate1 = MaterialRate(key="test_tile", label="Test Tile", unit="m²", low=280.0, high=520.0)
    db.upsert(rate1)
    # Price jumps 20%
    rate2 = MaterialRate(key="test_tile", label="Test Tile", unit="m²", low=340.0, high=620.0)
    status = db.upsert(rate2)
    assert status == "updated"


def test_db_get_all(db):
    for i in range(5):
        db.upsert(MaterialRate(key=f"item_{i}", label=f"Item {i}", unit="m²", low=100.0*i+100, high=200.0*i+200))
    rates = db.get_all()
    assert len(rates) == 5


def test_db_get_changes_filters_by_threshold(db):
    r1 = MaterialRate(key="item_a", label="Item A", unit="m²", low=100.0, high=200.0)
    db.upsert(r1)
    # 50% jump
    r2 = MaterialRate(key="item_a", label="Item A", unit="m²", low=150.0, high=300.0)
    db.upsert(r2)

    r3 = MaterialRate(key="item_b", label="Item B", unit="m²", low=100.0, high=200.0)
    db.upsert(r3)
    # 2% jump — below default 5% threshold
    r4 = MaterialRate(key="item_b", label="Item B", unit="m²", low=101.0, high=203.0)
    db.upsert(r4)

    changes = db.get_changes(threshold_pct=5.0)
    keys = {c["key"] for c in changes}
    assert "item_a" in keys
    assert "item_b" not in keys


def test_db_log_scrape(db):
    cat = ScrapedCatalogue(
        source_name="Test Source",
        source_url="https://example.com",
        rates=[MaterialRate(key="x", label="X", unit="m²", low=100.0, high=200.0)],
        status="ok",
    )
    db.log_scrape(cat)
    conn = sqlite3.connect(db.db_path)
    rows = conn.execute("SELECT * FROM scrape_log").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][1] == "Test Source"


def test_db_log_scrape_persists_error(db):
    cat = ScrapedCatalogue(source_name="Broken Source", source_url="https://example.com/404",
                           status="fetch_failed", error="404 Not Found")
    db.log_scrape(cat)
    conn = sqlite3.connect(db.db_path)
    row = conn.execute("SELECT source, status, error FROM scrape_log").fetchone()
    conn.close()
    assert row == ("Broken Source", "fetch_failed", "404 Not Found")


def test_get_source_status_returns_latest_per_source(db):
    db.log_scrape(ScrapedCatalogue(source_name="A", source_url="https://a", status="ok",
                                   rates=[MaterialRate(key="k", label="K", unit="m²", low=1, high=2)]))
    db.log_scrape(ScrapedCatalogue(source_name="B", source_url="https://b",
                                   status="fetch_failed", error="404 Not Found"))
    status = {s["source"]: s for s in db.get_source_status()}
    assert status["A"]["status"] == "ok"
    assert status["B"]["status"] == "fetch_failed"
    assert status["B"]["error"] == "404 Not Found"


# ── _parse_json_array (2026-09-11: json.loads("Extra data" fix) ────────────────

def test_parse_json_array_handles_clean_array():
    assert _parse_json_array('[{"label": "Cement", "low": 100}]') == [{"label": "Cement", "low": 100}]


def test_parse_json_array_tolerates_trailing_commentary():
    # This is the exact failure mode reported: json.loads on the whole string raises
    # "Extra data: line 15 column 1" the moment the model adds so much as a trailing word.
    raw = '[{"label": "Tile", "low": 300, "high": 500}]\n\nLet me know if you need more items.'
    assert _parse_json_array(raw) == [{"label": "Tile", "low": 300, "high": 500}]


def test_parse_json_array_no_brackets_returns_none():
    assert _parse_json_array("Sorry, I couldn't find any prices on this page.") is None


def test_parse_json_array_malformed_returns_none():
    assert _parse_json_array("[{\"label\": unquoted_value}]") is None


def test_parse_json_array_rejects_non_list_json():
    assert _parse_json_array('{"label": "not a list"}') is None


# ── run_full_update: failed sources are tracked, not silently absorbed ─────────

@pytest.mark.asyncio
async def test_run_full_update_tracks_failed_sources(tmp_path):
    from vula.takeoff.construction_rates_scraper import ConstructionRatesScraper

    scraper = ConstructionRatesScraper()
    scraper.db = RatesDatabase(db_path=tmp_path / "rates.db")

    async def fake_extract(url, source_name, prompt):
        if source_name == scraper.SOURCES[0]["name"]:
            return ScrapedCatalogue(
                source_name=source_name, source_url=url,
                rates=[MaterialRate(key="ok_item", label="OK Item", unit="m²", low=100, high=200)],
            )
        return ScrapedCatalogue(source_name=source_name, source_url=url,
                                status="fetch_failed", error="404 Not Found")

    with patch.object(scraper.extractor, "extract_rates", new=AsyncMock(side_effect=fake_extract)):
        result = await scraper.run_full_update()

    assert result.sources_scraped == [scraper.SOURCES[0]["name"]]
    assert len(result.sources_failed) == len(scraper.SOURCES) - 1
    assert all(f["reason"] == "404 Not Found" for f in result.sources_failed)
    assert "produced nothing" in result.summary


# ── ScrapedCatalogue ──────────────────────────────────────────────────────────

def test_scraped_catalogue_defaults():
    cat = ScrapedCatalogue(source_name="Test", source_url="https://example.com")
    assert cat.rates == []
    assert cat.status == "ok"
    assert cat.error is None


# ── ConstructionRatesScraper ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_full_update_returns_result(tmp_path):
    from vula.takeoff.construction_rates_scraper import ConstructionRatesScraper

    with patch.object(
        ConstructionRatesScraper,
        "run_full_update",
        new=AsyncMock(return_value=UpdateResult(
            total_rates=2, updated=0, new=2, unchanged=0,
            sources_scraped=["Test"],
            duration_s=1.5,
            summary="2 rates scraped",
        )),
    ):
        scraper = ConstructionRatesScraper()
        result = await scraper.run_full_update()

    assert result.total_rates == 2
    assert result.new == 2
    assert result.duration_s == 1.5


@pytest.mark.asyncio
async def test_whatsapp_digest_no_changes(tmp_path):
    from vula.takeoff.construction_rates_scraper import ConstructionRatesScraper
    scraper = ConstructionRatesScraper()
    scraper.db = RatesDatabase(db_path=tmp_path / "rates.db")
    digest = scraper.whatsapp_digest()
    assert "Vula QS" in digest
    assert "No significant rate" in digest


@pytest.mark.asyncio
async def test_whatsapp_digest_with_changes(tmp_path):
    from vula.takeoff.construction_rates_scraper import ConstructionRatesScraper
    scraper = ConstructionRatesScraper()
    scraper.db = RatesDatabase(db_path=tmp_path / "rates.db")

    # Insert a rate then update it with a big jump
    r1 = MaterialRate(key="tiles_p", label="Porcelain Tiles", unit="m²", low=300.0, high=500.0)
    scraper.db.upsert(r1)
    r2 = MaterialRate(key="tiles_p", label="Porcelain Tiles", unit="m²", low=370.0, high=610.0)
    scraper.db.upsert(r2)

    digest = scraper.whatsapp_digest()
    assert "Porcelain Tiles" in digest


# ── Rates API endpoints ───────────────────────────────────────────────────────

def test_rates_endpoint_returns_list():
    resp = client.get("/takeoff/rates")
    assert resp.status_code == 200
    data = resp.json()
    assert "rates" in data
    assert "count" in data
    assert isinstance(data["rates"], list)
    assert "failed_sources" in data
    assert isinstance(data["failed_sources"], list)


def test_rates_update_endpoint_queues_job():
    resp = client.post("/takeoff/rates/update")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "started"
    assert "background" in data["message"].lower()


def test_rates_changed_only_filter():
    resp = client.get("/takeoff/rates?changed_only=true&threshold_pct=5.0")
    assert resp.status_code == 200
    data = resp.json()
    assert "rates" in data
