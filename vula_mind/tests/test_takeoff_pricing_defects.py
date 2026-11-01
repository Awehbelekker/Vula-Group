"""Pricing defects from the 31 Oct takeoff review (docs/golive-and-takeoff-audit.md). Each test
failed on the old generator."""
from __future__ import annotations

import asyncio

import pytest

from vula.takeoff.boq_generator import BOQGenerator
from vula.takeoff.plan_reader import DoorWindow, ExtractedProject, MEPSchedule, Room, TitleBlock


def _gen(rooms=(), doors=(), mep=(), gfa=0.0, ext=0.0):
    p = ExtractedProject(title_block=TitleBlock(project_name="T"), rooms=list(rooms),
                         doors_windows=list(doors), mep_schedules=list(mep), gross_floor_area=gfa,
                         external_walls_m=ext, confidence_overall=1.0)
    return asyncio.run(BOQGenerator(p, tenant_id="t1").generate())


def _r(name, area, perim, **kw):
    return Room(name=name, area=area, perimeter=perim, height=kw.pop("height", 2.7), **kw)


def test_a_plumbing_fixture_is_a_point_not_a_whole_bathroom():
    boq = _gen(rooms=[_r("WC", 4, 8, is_wet=True)], mep=[MEPSchedule("plumbing", "WC", count=4)])
    line = next(i for i in boq.items if i.trade == "plumbing")
    assert line.supplier_key == "plumbing_drain_point" and line.qty == 4
    assert line.rate_used < 10000            # was R35k–R85k each as a full bathroom


def test_windows_are_priced_by_area_as_windows_not_as_doors():
    boq = _gen(rooms=[_r("Office", 20, 18)], doors=[DoorWindow("W1", "window", 1.2, 1.5, count=3)])
    w = next(i for i in boq.items if "W1" in i.description)
    assert (w.supplier_key, w.unit, w.qty) == ("window_alu_dg", "m²", pytest.approx(5.4))


def test_glass_is_priced_by_area_not_as_a_count():
    boq = _gen(rooms=[_r("Office", 20, 18)], doors=[DoorWindow("G1", "glass", 3.0, 2.7, count=2)])
    g = next(i for i in boq.items if "G1" in i.description)
    assert (g.supplier_key, g.unit, g.qty) == ("glass_partition", "m²", pytest.approx(16.2))


def test_wet_room_floor_tiles_are_priced_once():
    boq = _gen(rooms=[_r("Bathroom", 6, 10, is_wet=True, floor_finish="porcelain_tile")])
    floor_tile_lines = [i for i in boq.items if i.supplier_key == "tiles_porcelain_floor"]
    assert len(floor_tile_lines) == 1 and floor_tile_lines[0].qty == 6


def test_shared_walls_are_built_once():
    # Two 4×5 rooms side by side in a 8×5 shell: external walls 26 m, one 5 m shared wall.
    rooms = [_r("A", 20, 18), _r("B", 20, 18)]
    boq = _gen(rooms=rooms, ext=26.0)
    part = next(i for i in boq.items if "Partition" in i.description)
    assert part.qty == pytest.approx(5 * 2.7)        # was Σ perimeter × height = 97.2 m²
    paint = next(i for i in boq.items if i.trade == "painting")
    assert paint.qty == pytest.approx(36 * 2.7)      # every wall face is painted


def test_partitions_aren_t_guessed_without_the_external_walls():
    boq = _gen(rooms=[_r("A", 20, 18)])
    assert not [i for i in boq.items if "Partition" in i.description]
    assert any("Partition" in e for e in boq.excluded)


def test_exposed_ceilings_get_no_ceiling():
    boq = _gen(rooms=[_r("Office", 50, 30, ceiling_finish="standard"),
                      _r("Warehouse", 200, 60, ceiling_finish="exposed")], gfa=250)
    ceil = next(i for i in boq.items if "ceiling" in i.description.lower())
    assert ceil.qty == 50                             # was the full 250 m² GFA


def test_skirting_is_perimeter_less_door_openings():
    boq = _gen(rooms=[_r("Office", 20, 18)], doors=[DoorWindow("D1", "solid", 0.9, 2.1, count=1)])
    sk = next(i for i in boq.items if i.description == "Skirting")
    assert sk.qty == pytest.approx(18 - 2 * 0.9) and sk.unit == "m"


def test_a_line_with_no_rate_says_so():
    boq = _gen(rooms=[_r("Office", 20, 18)])
    sk = next(i for i in boq.items if i.description == "Skirting")
    assert sk.rate_used == 0 and "No rate on file" in sk.notes


def test_export_keeps_source_rate_range_and_trades():
    d = _gen(rooms=[_r("Office", 20, 18, floor_finish="carpet")]).to_dict()
    it = d["items"][0]
    assert {"source", "rate_low", "rate_high"} <= set(it)
    assert d["by_trade"] and sum(len(v) for v in d["by_trade"].values()) == len(d["items"])
    assert d["vat_cents"] == round(d["grand_total_cents"] * 0.15)
