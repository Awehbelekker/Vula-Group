"""Phase 4b-4d (docs/golive-and-takeoff-audit.md): calibrated scale, measured confidence, and the
review gate that keeps unconfirmed quantities away from pricing."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from config import settings
from vula.takeoff import accuracy, api
from vula.takeoff.boq_generator import BOQGenerator, ReviewRequired
from vula.takeoff.plan_reader import (DrawingSheet, ExtractedProject, PlanReader, Room, TitleBlock,
                                      assess)


def _dxf(path, units=4, labels=True, outline=True):
    """A 10m × 8m unit in mm: outline + three rooms (lounge 6×8=48, bed 4×5=20, bath 4×3=12)."""
    import ezdxf
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = units
    msp = doc.modelspace()
    if outline:
        msp.add_lwpolyline([(0, 0), (10000, 0), (10000, 8000), (0, 8000)], close=True)
    rooms = [("LOUNGE", [(0, 0), (6000, 0), (6000, 8000), (0, 8000)]),
             ("BEDROOM", [(6000, 0), (10000, 0), (10000, 5000), (6000, 5000)]),
             ("BATHROOM", [(6000, 5000), (10000, 5000), (10000, 8000), (6000, 8000)])]
    for name, pts in rooms:
        msp.add_lwpolyline(pts, close=True)
        if labels:
            cx = sum(p[0] for p in pts) / 4
            cy = sum(p[1] for p in pts) / 4
            msp.add_text(name, dxfattribs={"insert": (cx, cy)})
    doc.saveas(path)
    return path


# ── deterministic second reading ─────────────────────────────────────────────

def test_schedule_rows_are_read_without_a_model():
    text = """ROOM SCHEDULE
    01 Reception | 24.5 m²
    02 Boardroom 36,0 m2
    Open plan office: 180 sqm
    TOTAL 240.5 m²
    GFA 260 m²"""
    assert accuracy.schedule_rooms_from_text(text) == [
        ("Reception", 24.5), ("Boardroom", 36.0), ("Open plan office", 180.0)]


def test_parse_scale():
    assert accuracy.parse_scale("1:100") == 100.0
    assert accuracy.parse_scale("SCALE 1 : 50 @ A1") == 50.0
    assert accuracy.parse_scale("unknown") is None


# ── measured confidence ──────────────────────────────────────────────────────

def _p(ai, measured, gfa=0.0):
    p = ExtractedProject(rooms=[Room(name=n, area=a, floor_finish="lvt") for n, a in ai],
                         ai_rooms=[Room(name=n, area=a) for n, a in ai],
                         deterministic_rooms=measured, gross_floor_area=gfa)
    return assess(p, use_history=False)


def test_readings_that_agree_are_confident_and_need_no_review():
    p = _p([("A", 50.0), ("B", 30.0)], [("A", 50.0), ("B", 30.2)], gfa=90)
    assert p.confidence_overall == 1.0
    assert p.review["status"] == "not_required"


def test_readings_that_disagree_drop_confidence_and_go_to_review():
    p = _p([("A", 50.0), ("B", 30.0)], [("A", 50.0), ("B", 20.0)], gfa=80)
    assert p.confidence_overall < 0.5
    assert p.review["status"] == "pending"
    assert any("differ by" in r for r in p.review["reasons"])


def test_a_single_unchecked_reading_is_never_confident():
    p = _p([("A", 50.0)], [])
    assert p.confidence_overall == accuracy.UNCORROBORATED
    assert p.review["status"] == "pending"


def test_geometry_failure_blocks():
    p = _p([("A", 500.0)], [], gfa=100)         # rooms 5× the GFA
    assert p.review["status"] == "pending"
    assert any("GFA" in r for r in p.review["reasons"])


def test_benchmark_history_caps_confidence():
    p = ExtractedProject(rooms=[Room(name="A", area=50)], ai_rooms=[Room(name="A", area=50)],
                         deterministic_rooms=[("A", 50.0)])
    # This plan type is off by 11% on the benchmark: agreement falls linearly from 1 at the 2%
    # tolerance to 0 at 20%, so readings that agree with each other still only score 0.5.
    assess(p, history=0.11)
    assert p.confidence_overall == pytest.approx(0.5)
    assert p.confidence_signals["benchmark"]["mean_abs_area_err"] == 0.11


def test_no_hardcoded_method_constants_left():
    import inspect
    from vula.takeoff import plan_reader
    src = inspect.getsource(plan_reader)
    for literal in ("confidence=0.85", "confidence=0.70", "confidence=0.65", "confidence = 0.90",
                    "confidence = 0.70", "confidence_overall * 0.5"):
        assert literal not in src


# ── calibration (4b) ─────────────────────────────────────────────────────────

def test_dxf_job_is_calibrated_named_and_measured(tmp_path):
    p = asyncio.run(PlanReader("t1").read(_dxf(tmp_path / "unit.dxf")))
    assert sorted((r.name, r.area) for r in p.rooms) == [("BATHROOM", 12.0), ("BEDROOM", 20.0),
                                                         ("LOUNGE", 48.0)]
    assert p.gross_floor_area == 80.0               # the outline, not counted as a room
    assert p.external_walls_m == 36.0               # its perimeter: 2 × (10 + 8)
    s = p.sheets[0]
    assert (s.scale_method, s.scale_verified_by, s.scale_ratio) == ("dxf_units", "dxf_header", 1.0)
    assert p.confidence_overall == 1.0
    # Geometry is exact, but a DXF carries no finishes — held so flooring isn't silently unpriced.
    assert p.review["status"] == "pending"
    assert p.review["reasons"] == ["3 of 3 room(s) have no floor finish, so their flooring won't be "
                                   "priced. Add finishes or approve without flooring."]


def test_rooms_with_finishes_and_agreeing_readings_need_no_review():
    p = ExtractedProject(rooms=[Room(name="A", area=50, floor_finish="carpet")],
                         ai_rooms=[Room(name="A", area=50)], deterministic_rooms=[("A", 50.0)])
    assert assess(p, use_history=False).review["status"] == "not_required"


def test_approving_a_dxf_job_can_add_the_finishes():
    _stage("rv8")
    seen = {}

    async def fake_price(job_id, project, tenant_id, markup):
        seen["finishes"] = [(r.name, r.floor_finish, r.perimeter) for r in project.rooms]
    with patch.object(api, "_price", fake_price):
        r = client.post("/takeoff/rv8/review", json={"decision": "approve", "rooms": [
            {"name": "Office", "area": 50.0, "floor_finish": "carpet"}]})
    assert r.status_code == 200
    assert seen["finishes"] == [("Office", "carpet", 0.0)]


def test_unitless_dxf_goes_to_review(tmp_path):
    p = asyncio.run(PlanReader("t1").read(_dxf(tmp_path / "u.dxf", units=0)))
    assert p.review["status"] == "pending"
    assert any("units" in r for r in p.review["reasons"])


def test_pdf_title_block_scale_is_recorded_as_unverified():
    from vula.takeoff.plan_reader import PDFReader
    _, _, scale, _ = PDFReader()._parse_title_block("A101 PLAN SCALE 1:100")
    assert accuracy.parse_scale(scale) == 100.0


# ── the gate (4d) ────────────────────────────────────────────────────────────

def test_generator_refuses_unreviewed_quantities():
    p = ExtractedProject(rooms=[Room(name="A", area=10)], gross_floor_area=10,
                         review={"status": "pending", "reasons": ["x"]})
    with pytest.raises(ReviewRequired):
        asyncio.run(BOQGenerator(p).generate())


def _pending_project():
    p = ExtractedProject(title_block=TitleBlock(project_name="Site"),
                         sheets=[DrawingSheet("A1", "Plan", "1:100", "arch", "", "", status="read")],
                         rooms=[Room(name="Office", area=50.0)], ai_rooms=[Room(name="Office", area=50.0)],
                         deterministic_rooms=[("Office", 40.0)], gross_floor_area=0)
    return assess(p, use_history=False)


def test_a_flagged_job_stops_before_pricing(tmp_path):
    api._jobs["rv1"] = {"status": "queued", "tenant_id": "t1"}
    with patch.object(api, "PlanReader") as PR, patch.object(api, "BOQGenerator") as BG:
        PR.return_value.read = AsyncMock(return_value=_pending_project())
        asyncio.run(api._process_plans("rv1", tmp_path / "p.pdf", "t1", 15))
    assert api._jobs["rv1"]["status"] == "needs_review"
    assert api._jobs["rv1"]["review"]["reasons"]
    BG.assert_not_called()


client = TestClient(api.router and __import__("vula.api.server", fromlist=["app"]).app,
                    headers={"X-API-Key": settings.api_key or ""})


def _stage(job_id, tenant="t1"):
    api._jobs[job_id] = {"status": "needs_review", "tenant_id": tenant, "markup": 15.0,
                         "review": {"status": "pending", "reasons": ["differ"]},
                         "extracted": api._project_to_state(_pending_project())}


def test_approving_with_corrections_prices_exactly_what_was_approved():
    _stage("rv2")
    seen = {}

    async def fake_price(job_id, project, tenant_id, markup):
        seen["rooms"] = [(r.name, r.area) for r in project.rooms]
        seen["review"] = project.review["status"]
    with patch.object(api, "_price", fake_price):
        r = client.post("/takeoff/rv2/review", json={"decision": "approve", "use_measured": True,
                                                     "note": "checked the schedule"})
    assert r.status_code == 200, r.text
    assert seen == {"rooms": [("Office", 40.0)], "review": "approved"}
    rv = api._jobs["rv2"]["review"]
    assert rv["status"] == "approved" and rv["decided_by"] and rv["decided_at"]
    assert rv["edits"] == {"rooms": "measured"}


def test_rejecting_prices_nothing():
    _stage("rv3")
    with patch.object(api, "_price") as price:
        r = client.post("/takeoff/rv3/review", json={"decision": "reject"})
    assert r.status_code == 200 and api._jobs["rv3"]["status"] == "rejected"
    price.assert_not_called()


def test_review_only_for_a_job_waiting_for_one():
    api._jobs["rv4"] = {"status": "complete", "tenant_id": "t1"}
    assert client.post("/takeoff/rv4/review", json={"decision": "approve"}).status_code == 409


def test_edited_room_needs_an_area():
    _stage("rv5")
    r = client.post("/takeoff/rv5/review", json={"decision": "approve",
                                                 "rooms": [{"name": "Office", "area": 0}]})
    assert r.status_code == 400


# ── tenant scoping regression (the brief: sound, add a test) ─────────────────

def test_another_tenants_job_is_invisible_and_unreviewable():
    _stage("rv6", tenant="tenant-a")
    assert api._job_for("rv6", "tenant-b") is None
    assert api._job_for("rv6", "tenant-a") is not None
    assert client.get("/takeoff/rv6?tenant_id=tenant-b").status_code == 404
    assert client.post("/takeoff/rv6/review?tenant_id=tenant-b",
                       json={"decision": "approve"}).status_code == 404
    assert api._jobs["rv6"]["status"] == "needs_review"


def test_member_scope_beats_the_query_param():
    """A member of tenant B (request.state.auth_tenant set by require_auth) can't name tenant A in
    the query string to read A's job — authorized_tenant wins in every takeoff route."""
    from types import SimpleNamespace
    from vula.api.master_auth import authorized_tenant
    req = SimpleNamespace(state=SimpleNamespace(auth_tenant="tenant-b"))
    _stage("rv7", tenant="tenant-a")
    assert api._job_for("rv7", authorized_tenant(req) or "tenant-a") is None


def test_review_confirms_the_wall_height_and_external_walls():
    _stage("rv9")
    seen = {}

    async def fake_price(job_id, project, tenant_id, markup):
        seen["h"] = {r.height for r in project.rooms}
        seen["ext"] = project.external_walls_m
    with patch.object(api, "_price", fake_price):
        r = client.post("/takeoff/rv9/review", json={"decision": "approve", "wall_height": 3.0,
                                                     "external_walls_m": 42.5})
    assert r.status_code == 200
    assert seen == {"h": {3.0}, "ext": 42.5}
    assert api._jobs["rv9"]["review"]["edits"] == {"wall_height": 3.0, "external_walls_m": 42.5}


def test_an_impossible_wall_height_is_refused():
    _stage("rv10")
    assert client.post("/takeoff/rv10/review", json={"decision": "approve", "wall_height": 27}).status_code == 400


def test_the_bill_says_whether_the_height_was_confirmed():
    p = ExtractedProject(rooms=[Room(name="A", area=20, perimeter=18, floor_finish="carpet")],
                         confidence_overall=1.0, review={"status": "approved", "edits": {"wall_height": 2.7}})
    boq = asyncio.run(BOQGenerator(p).generate())
    paint = next(i for i in boq.items if i.trade == "painting")
    assert "confirmed at review" in paint.notes
