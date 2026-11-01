"""Takeoff measurement and pricing defects found in the 2026-10-31 audit
(docs/golive-and-takeoff-audit.md, NEW-T1..T4). Each test failed before its fix."""
from __future__ import annotations

import asyncio
import sqlite3
from decimal import Decimal

import pytest

from vula.takeoff.boq_generator import BOQ, BOQGenerator, BOQItem, RateLookup
from vula.takeoff.plan_reader import DXFReader, ExtractedProject, Room, TitleBlock


def _dxf(path, units: int, w: float, h: float, extra_blocks=()):
    import ezdxf
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = units
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (w, 0), (w, h), (0, h)], close=True)
    for name in extra_blocks:
        doc.blocks.new(name=name)
        msp.add_blockref(name, (1, 1))
    doc.saveas(path)
    return path


# ── NEW-T1: DXF drawing units ────────────────────────────────────────────────

def test_dxf_in_millimetres_reports_square_metres(tmp_path):
    p = _dxf(tmp_path / "mm.dxf", units=4, w=5000, h=4000)          # 4 = millimetres
    rooms, _ = asyncio.run(DXFReader().read(p))
    assert len(rooms) == 1
    assert rooms[0].area == pytest.approx(20.0)
    assert rooms[0].perimeter == pytest.approx(18.0)


def test_dxf_in_metres_is_unchanged(tmp_path):
    p = _dxf(tmp_path / "m.dxf", units=6, w=5, h=4)                 # 6 = metres
    rooms, _ = asyncio.run(DXFReader().read(p))
    assert rooms[0].area == pytest.approx(20.0)


def test_dxf_in_centimetres(tmp_path):
    p = _dxf(tmp_path / "cm.dxf", units=5, w=500, h=400)
    rooms, _ = asyncio.run(DXFReader().read(p))
    assert rooms[0].area == pytest.approx(20.0)


def test_unitless_dxf_is_inferred_and_marked_unverified(tmp_path):
    p = _dxf(tmp_path / "u.dxf", units=0, w=5000, h=4000)
    reader = DXFReader()
    rooms, _ = asyncio.run(reader.read(p))
    assert rooms[0].area == pytest.approx(20.0)
    assert reader.last_units["method"] == "inferred_from_extent"
    assert reader.last_units["verified"] is False


def test_dxf_units_are_recorded_as_verified_when_declared(tmp_path):
    p = _dxf(tmp_path / "mm.dxf", units=4, w=5000, h=4000)
    reader = DXFReader()
    asyncio.run(reader.read(p))
    assert reader.last_units == {"units": "mm", "to_metres": 0.001, "method": "dxf_units",
                                 "verified": True}


def test_dxf_drain_block_is_not_a_door(tmp_path):
    p = _dxf(tmp_path / "b.dxf", units=6, w=5, h=4, extra_blocks=("DRAIN_POINT", "DOOR_900"))
    _, openings = asyncio.run(DXFReader().read(p))
    assert [o.ref for o in openings] == ["D1"]


# ── NEW-T3: mezzanine slab priced at a per-m³ rate ───────────────────────────

def _project(rooms, gfa=200.0):
    return ExtractedProject(title_block=TitleBlock(project_name="T"), rooms=rooms,
                            gross_floor_area=gfa, confidence_overall=0.9)


def test_mezzanine_slab_is_measured_in_cubic_metres():
    boq = asyncio.run(BOQGenerator(_project([Room(name="Mezzanine", area=40.0)])).generate())
    slab = next(i for i in boq.items if "slab" in i.description.lower())
    assert slab.unit == "m³"
    assert slab.qty == pytest.approx(40.0 * 0.15)


# ── NEW-T2: scraped market rates actually price BOQ lines ────────────────────

def _rates_db(path, rows):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE material_rates (key TEXT PRIMARY KEY, label TEXT, unit TEXT, low REAL,"
                 " high REAL, mid REAL, source TEXT, scraped_at TEXT, prev_mid REAL, change_pct REAL,"
                 " notes TEXT)")
    for key, label, unit, low, high in rows:
        conn.execute("INSERT INTO material_rates (key,label,unit,low,high,mid,source) VALUES (?,?,?,?,?,?,?)",
                     (key, label, unit, low, high, (low + high) / 2, "Scraped"))
    conn.commit()
    conn.close()
    return path


def test_scraped_rate_with_a_label_key_prices_the_boq_line(tmp_path):
    db = _rates_db(tmp_path / "r.db", [("porcelain_tiles_600x600__laid_",
                                         "Porcelain Tiles 600x600 (laid)", "m²", 400, 600)])
    low, high, unit, source = RateLookup(db).get("tiles_porcelain_floor", "m²")
    assert (low, high, source) == (400, 600, "Scraped")


def test_a_supply_only_retail_price_never_prices_a_laid_line(tmp_path):
    # Real production rows (2026-10-31): retail supply prices, not laid rates. The Italtile one sits
    # inside the sanity band, so only the "laid/installed" rule keeps it out.
    db = _rates_db(tmp_path / "r.db", [
        ("italtile_floor_tiles_porcelain", "Italtile - Floor Tiles - Porcelain", "m²", 39.99, 399.99),
        ("porcelain_tile_600x600", "Porcelain Tile - 600x600mm", "m²", 99.99, 99.99)])
    assert RateLookup(db).get("tiles_porcelain_floor", "m²")[3] != "Scraped"


def test_a_row_with_no_source_is_never_used(tmp_path):
    db = _rates_db(tmp_path / "r.db", [("porcelain_tiles", "Porcelain Tiles (laid)", "m²", 400, 600)])
    conn = sqlite3.connect(db)
    conn.execute("UPDATE material_rates SET source=''")
    conn.commit()
    conn.close()
    assert RateLookup(db).get("tiles_porcelain_floor", "m²")[3] != ""


def test_scraped_rate_in_a_different_unit_is_never_used(tmp_path):
    db = _rates_db(tmp_path / "r.db", [("paint_interior_5l", "Interior paint 5L installed", "no", 900, 1100)])
    low, high, unit, source = RateLookup(db).get("paint_interior", "m²")
    assert source != "Scraped"
    assert unit == "m²"


def test_scraped_rate_far_outside_the_reference_band_is_ignored(tmp_path):
    # A mis-parsed price (R65,000 per m² of paint) must not replace the reference rate.
    db = _rates_db(tmp_path / "r.db", [("interior_paint", "Interior paint 2 coats applied", "m²", 65000, 70000)])
    assert RateLookup(db).get("paint_interior", "m²")[3] != "Scraped"


def test_loose_substring_never_matches_another_item(tmp_path):
    # Old lookup: LIKE '%door_solid%' — and 'door_solid_core_fire_rated…' would match a fire door.
    db = _rates_db(tmp_path / "r.db", [("door_solid_fire_60", "Fire door 60 min solid installed", "no", 14000, 20000)])
    assert RateLookup(db).get("door_solid", "no")[3] != "Scraped"


# ── NEW-T4: money in integer cents ───────────────────────────────────────────

def test_boq_line_amounts_are_integer_cents_rounded_half_up():
    item = BOQItem(id="X", trade="t", description="d", unit="m²", qty=3.0, rate_low=1.0,
                   rate_high=1.01, rate_used=1.005, line_total=0, source="s", confidence=90)
    assert isinstance(item.rate_cents, int) and item.rate_cents == 101     # R1.005 → 101c half-up
    assert item.line_total_cents == 303
    assert item.line_total == pytest.approx(3.03)


def test_boq_totals_are_summed_in_cents():
    items = [BOQItem(id=str(i), trade="t", description="d", unit="m²", qty=1, rate_low=0.1,
                     rate_high=0.1, rate_used=0.1, line_total=0, source="s", confidence=90)
             for i in range(3)]
    boq = BOQ(project_name="p", tenant_id="t", items=items, markup_pct=15)
    assert boq.net_total_cents == 30
    assert boq.markup_cents == 5            # 4.5c → 5c half-up
    assert boq.grand_total_cents == 35
    assert Decimal(str(boq.net_total)) == Decimal("0.3")


def test_old_style_polyline_is_measured_too(tmp_path):
    import ezdxf
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 4
    doc.modelspace().add_polyline2d([(0, 0), (3000, 0), (3000, 3000), (0, 3000)], close=True)
    doc.saveas(tmp_path / "p.dxf")
    rooms, _ = asyncio.run(DXFReader().read(tmp_path / "p.dxf"))
    assert rooms[0].area == pytest.approx(9.0)
