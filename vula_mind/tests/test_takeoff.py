"""Tests for the Takeoff / BOQ engine — no file I/O or LLM calls required."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from vula.api.server import app
from vula.takeoff.boq_generator import BOQ, BOQGenerator, BOQItem, RateLookup
from vula.takeoff.plan_reader import (
    DoorWindow,
    DrawingSheet,
    ExtractedProject,
    MEPSchedule,
    Room,
    TitleBlock,
    geometry_reconciled,
)

client = TestClient(app)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _project(
    rooms: list | None = None,
    gfa: float = 200.0,
    doors: list | None = None,
    mep: list | None = None,
) -> ExtractedProject:
    return ExtractedProject(
        title_block=TitleBlock(project_name="Test Project", client="Acme"),
        rooms=rooms or [],
        doors_windows=doors or [],
        mep_schedules=mep or [],
        gross_floor_area=gfa,
        confidence_overall=0.85,
    )


def _room(name: str, area: float, wet: bool = False, floor_finish: str = "lvt",
          perimeter: float = 0.0) -> Room:
    return Room(
        name=name,
        area=area,
        perimeter=perimeter or (area ** 0.5) * 4,
        height=2.7,
        floor_finish=floor_finish,
        ceiling_finish="",
        is_wet=wet,
    )


# ── BOQ dataclass ─────────────────────────────────────────────────────────────

def test_boq_totals_correct():
    boq = BOQ(project_name="Test", tenant_id="t1", markup_pct=15.0)
    boq.items.append(BOQItem("A_001", "flooring", "LVT", "m²", 100, 280, 520, 400, 40000, "test", 90))
    boq.items.append(BOQItem("A_002", "painting", "Paint", "m²", 50, 65, 130, 97, 4875, "test", 92))
    assert boq.net_total == pytest.approx(44875.0)
    assert boq.markup_amount == pytest.approx(44875.0 * 0.15)
    assert boq.grand_total == pytest.approx(44875.0 * 1.15)


def test_boq_empty_totals_zero():
    boq = BOQ(project_name="Empty", tenant_id="t1")
    assert boq.net_total == 0.0
    assert boq.grand_total == 0.0


def test_boq_by_trade_groups_correctly():
    boq = BOQ(project_name="Test", tenant_id="t1")
    boq.items.append(BOQItem("F_001", "flooring", "LVT", "m²", 100, 280, 520, 400, 40000, "test", 90))
    boq.items.append(BOQItem("F_002", "flooring", "Carpet", "m²", 50, 220, 480, 350, 17500, "test", 88))
    boq.items.append(BOQItem("P_001", "painting", "Paint", "m²", 200, 65, 130, 97, 19500, "test", 92))
    trades = boq.by_trade
    assert len(trades["flooring"]) == 2
    assert len(trades["painting"]) == 1


def test_boq_specialist_items_filter():
    boq = BOQ(project_name="Test", tenant_id="t1")
    boq.items.append(BOQItem("A_001", "flooring", "Normal", "m²", 10, 280, 520, 400, 4000, "test", 90, requires_specialist=False))
    boq.items.append(BOQItem("A_002", "structural", "Mez steel", "tonne", 2, 38000, 55000, 46500, 93000, "test", 65, requires_specialist=True))
    assert len(boq.specialist_items) == 1
    assert boq.specialist_items[0].trade == "structural"


def test_boq_to_dict_structure():
    boq = BOQ(project_name="Test Project", tenant_id="acme")
    boq.items.append(BOQItem("D_001", "drywall", "Partition", "m²", 50, 420, 680, 550, 27500, "test", 85))
    d = boq.to_dict()
    assert d["project_name"] == "Test Project"
    assert "net_total" in d
    assert "grand_total" in d
    assert d["markup_pct"] == 15.0
    assert len(d["items"]) == 1
    assert d["items"][0]["trade"] == "drywall"


# ── RateLookup ────────────────────────────────────────────────────────────────

def test_rate_lookup_fallback_known_key():
    rl = RateLookup(db_path=None.__class__.__mro__[0].__module__ and  # trick to get non-existent path
                    __import__("pathlib").Path("/tmp/__nonexistent_db_xyz.db"))
    low, high, unit, source = rl.get("demolition_light")
    assert low == 120
    assert high == 280
    assert unit == "m²"
    assert "AECOM" in source


def test_rate_lookup_unknown_key_returns_zero():
    rl = RateLookup(db_path=__import__("pathlib").Path("/tmp/__nonexistent_db_xyz.db"))
    low, high, unit, source = rl.get("completely_unknown_trade_xyz")
    assert low == 0
    assert high == 0
    assert unit == "item"


def test_rate_lookup_all_fallback_keys_have_valid_rates():
    rl = RateLookup(db_path=__import__("pathlib").Path("/tmp/__nonexistent_db_xyz.db"))
    for key in RateLookup.FALLBACK:
        low, high, unit, source = rl.get(key)
        assert low > 0, f"{key}: low rate should be positive"
        assert high >= low, f"{key}: high rate should be >= low"
        assert unit, f"{key}: unit should not be empty"


# ── BOQGenerator — minimal project ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_returns_boq_with_items():
    project = _project(gfa=150.0)
    gen = BOQGenerator(project, tenant_id="t1")
    boq = await gen.generate()
    assert isinstance(boq, BOQ)
    assert len(boq.items) > 0


@pytest.mark.asyncio
async def test_generate_totals_positive():
    project = _project(gfa=200.0)
    gen = BOQGenerator(project, tenant_id="t1")
    boq = await gen.generate()
    assert boq.net_total > 0
    assert boq.grand_total > boq.net_total


@pytest.mark.asyncio
async def test_generate_includes_mandatory_trades():
    """Every BOQ must have at least: demolition, drywall, electrical, fire."""
    project = _project(gfa=300.0)
    gen = BOQGenerator(project, tenant_id="t1")
    boq = await gen.generate()
    trades = {item.trade for item in boq.items}
    for required in ("demolition", "drywall", "electrical", "fire"):
        assert required in trades, f"Missing trade: {required}"


@pytest.mark.asyncio
async def test_generate_markup_applied():
    project = _project(gfa=100.0)
    gen = BOQGenerator(project, tenant_id="t1", markup=20.0)
    boq = await gen.generate()
    assert boq.markup_pct == 20.0
    assert boq.grand_total == pytest.approx(boq.net_total * 1.20)


@pytest.mark.asyncio
async def test_generate_project_name_from_title_block():
    project = _project(gfa=100.0)
    project.title_block.project_name = "DIGG Interiors Phase 2"
    gen = BOQGenerator(project, tenant_id="digg")
    boq = await gen.generate()
    assert boq.project_name == "DIGG Interiors Phase 2"


# ── BOQGenerator — wet rooms ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_wet_room_adds_waterproofing():
    rooms = [
        _room("Office", 80.0, floor_finish="lvt"),
        _room("Ablutions", 20.0, wet=True, floor_finish="tile"),
    ]
    project = _project(rooms=rooms, gfa=100.0)
    gen = BOQGenerator(project, tenant_id="t1")
    boq = await gen.generate()
    trades = {item.trade for item in boq.items}
    assert "wet_works" in trades
    assert "tiling" in trades


@pytest.mark.asyncio
async def test_generate_no_wet_rooms_skips_waterproofing():
    rooms = [_room("Open Plan", 200.0, floor_finish="lvt")]
    project = _project(rooms=rooms, gfa=200.0)
    gen = BOQGenerator(project, tenant_id="t1")
    boq = await gen.generate()
    trades = {item.trade for item in boq.items}
    assert "wet_works" not in trades


# ── BOQGenerator — mezzanine ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_mezzanine_adds_structural():
    rooms = [
        _room("Mezzanine Office", 60.0),
        _room("Ground Floor", 140.0),
    ]
    project = _project(rooms=rooms, gfa=200.0)
    gen = BOQGenerator(project, tenant_id="t1")
    boq = await gen.generate()
    trades = {item.trade for item in boq.items}
    assert "concrete" in trades
    mez_items = [i for i in boq.items if "mezzanine" in i.description.lower()]
    assert len(mez_items) >= 2  # slab + steel


@pytest.mark.asyncio
async def test_generate_mezzanine_steel_requires_specialist():
    rooms = [_room("Mezzanine", 80.0)]
    project = _project(rooms=rooms, gfa=200.0)
    gen = BOQGenerator(project, tenant_id="t1")
    boq = await gen.generate()
    steel_items = [i for i in boq.items if "steel" in i.description.lower() and "mezzanine" in i.description.lower()]
    assert all(i.requires_specialist for i in steel_items)


# ── BOQGenerator — doors from schedule ───────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_door_schedule_creates_joinery_items():
    doors = [
        DoorWindow("D01", "hollow_core", 0.9, 2.1, count=4),
        DoorWindow("FD01", "fire_60", 0.9, 2.1, count=2, fire_rating="60min"),
    ]
    project = _project(doors=doors, gfa=200.0)
    gen = BOQGenerator(project, tenant_id="t1")
    boq = await gen.generate()
    joinery_items = [i for i in boq.items if i.trade == "joinery"]
    assert len(joinery_items) >= 2
    door_types = {i.description for i in joinery_items}
    assert any("Hollow Core" in d for d in door_types)
    assert any("Fire 60" in d for d in door_types)


@pytest.mark.asyncio
async def test_generate_no_door_schedule_estimates_from_gfa():
    project = _project(gfa=250.0)  # No doors
    gen = BOQGenerator(project, tenant_id="t1")
    boq = await gen.generate()
    joinery_items = [i for i in boq.items if i.trade == "joinery"]
    # Should have at least 1 estimated door item
    assert len(joinery_items) >= 1
    estimated = [i for i in joinery_items if "estimated" in i.description.lower()]
    assert len(estimated) >= 1


# ── BOQGenerator — kitchen ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_kitchen_room_adds_joinery_and_plumbing():
    rooms = [
        _room("Kitchen", 15.0, floor_finish="tile"),
        _room("Office", 185.0, floor_finish="lvt"),
    ]
    project = _project(rooms=rooms, gfa=200.0)
    gen = BOQGenerator(project, tenant_id="t1")
    boq = await gen.generate()
    kitchen_joinery = [i for i in boq.items if "kitchen" in i.description.lower() and i.trade == "joinery"]
    assert len(kitchen_joinery) >= 1
    kitchen_plumb = [i for i in boq.items if "kitchen" in i.description.lower() and i.trade == "plumbing"]
    assert len(kitchen_plumb) >= 1


# ── BOQGenerator — MEP from schedule ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_plumbing_from_schedule():
    mep = [
        MEPSchedule("plumbing", "WC suite complete", count=4),
        MEPSchedule("plumbing", "WHB vanity unit", count=4),
    ]
    project = _project(mep=mep, gfa=200.0)
    gen = BOQGenerator(project, tenant_id="t1")
    boq = await gen.generate()
    plumbing_items = [i for i in boq.items if i.trade == "plumbing"]
    assert len(plumbing_items) >= 2
    assert any("WC suite" in i.description for i in plumbing_items)


# ── BOQGenerator — floor finishes ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_multiple_floor_finishes():
    rooms = [
        _room("Boardroom", 40.0, floor_finish="carpet"),
        _room("Reception", 30.0, floor_finish="porcelain_tile"),
        _room("Open Plan", 130.0, floor_finish="lvt"),
    ]
    project = _project(rooms=rooms, gfa=200.0)
    gen = BOQGenerator(project, tenant_id="t1")
    boq = await gen.generate()
    floor_items = [i for i in boq.items if i.trade == "flooring" and "screed" not in i.description.lower()]
    descs = {i.description.lower() for i in floor_items}
    assert any("carpet" in d for d in descs)
    assert any("porcelain" in d for d in descs)
    assert any("lvt" in d or "vinyl" in d for d in descs)


@pytest.mark.asyncio
async def test_generate_specialist_floor_finish_flagged():
    rooms = [_room("Studio", 60.0, floor_finish="studio_floor")]
    project = _project(rooms=rooms, gfa=60.0)
    gen = BOQGenerator(project, tenant_id="t1")
    boq = await gen.generate()
    studio_items = [i for i in boq.items if "studio" in i.description.lower() and i.trade == "flooring"]
    # studio_floor should be flagged as specialist (confidence 60 < 70)
    assert all(i.requires_specialist for i in studio_items)


# ── BOQGenerator — ID generation ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_all_item_ids_unique():
    project = _project(
        rooms=[_room("Office A", 80.0), _room("Office B", 80.0), _room("Ablutions", 20.0, wet=True)],
        gfa=180.0,
    )
    gen = BOQGenerator(project, tenant_id="t1")
    boq = await gen.generate()
    ids = [i.id for i in boq.items]
    assert len(ids) == len(set(ids)), "Duplicate BOQ item IDs found"


# ── Takeoff API ───────────────────────────────────────────────────────────────

def test_takeoff_upload_returns_job_id(tmp_path):
    dummy_pdf = tmp_path / "test.pdf"
    dummy_pdf.write_bytes(b"%PDF-1.4 mock")
    with open(dummy_pdf, "rb") as f:
        resp = client.post(
            "/takeoff/upload",
            data={"tenant_id": "test-tenant", "markup": "15"},
            files={"file": ("test.pdf", f, "application/pdf")},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "job_id" in data
    assert data["status"] == "queued"


def test_takeoff_job_status_404_unknown():
    resp = client.get("/takeoff/nonexistent-job-id-xyz")
    assert resp.status_code == 404


def test_takeoff_suppliers_returns_list():
    resp = client.get("/takeoff/suppliers/list?tenant_id=test-tenant")
    assert resp.status_code == 200
    data = resp.json()
    assert "suppliers" in data
    assert isinstance(data["suppliers"], list)


def test_takeoff_boq_404_unknown_job():
    resp = client.get("/takeoff/nonexistent-job-id-xyz/boq")
    assert resp.status_code == 404


# ── geometry_reconciled (2026-08 accuracy audit) ────────────────────────────────
# gross_floor_area and the room schedule are extracted by two SEPARATE LLM calls and
# previously nothing checked they agreed — a hallucinated GFA (or a room schedule missing
# half the building) fed straight into BOQGenerator, which prices demolition/finishes
# directly off GFA, with "confidence" that was only ever a hardcoded per-extraction-method
# constant, never a measured signal.

def test_geometry_reconciled_when_room_sum_matches_gfa():
    project = _project(rooms=[_room("Office", 120), _room("Kitchen", 30)], gfa=180)
    ok, warnings = geometry_reconciled(project)
    assert ok is True
    assert warnings == []


def test_geometry_flags_room_sum_wildly_exceeding_gfa():
    """Rooms summing to well over 100% of GFA means one of the two numbers was misread —
    rooms can't physically occupy more area than the building they're inside."""
    project = _project(rooms=[_room("Office", 300), _room("Kitchen", 100)], gfa=180)
    ok, warnings = geometry_reconciled(project)
    assert ok is False
    assert any("likely misread" in w for w in warnings)


def test_geometry_flags_room_sum_far_below_gfa():
    project = _project(rooms=[_room("Small storeroom", 10)], gfa=500)
    ok, warnings = geometry_reconciled(project)
    assert ok is False
    assert any("may be missing" in w for w in warnings)


def test_geometry_flags_zero_rooms_despite_readable_sheets():
    project = _project(rooms=[], gfa=0)
    project.sheets = [DrawingSheet(sheet_number="A001", title="Floor Plan", scale="1:100",
                                    discipline="arch", raw_text="x", raw_markdown="x", status="read")]
    ok, warnings = geometry_reconciled(project)
    assert ok is False
    assert any("no rooms were extracted" in w for w in warnings)


def test_geometry_reconciled_when_gfa_missing_nothing_to_check():
    """No GFA extracted at all (only room schedule) — nothing to cross-check against, must
    not be treated as a failure."""
    project = _project(rooms=[_room("Office", 120)], gfa=0)
    ok, warnings = geometry_reconciled(project)
    assert ok is True
    assert warnings == []
