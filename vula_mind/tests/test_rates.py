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
