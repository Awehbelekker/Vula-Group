"""The takeoff benchmark (evals/takeoff_bench.py) runs in CI: every case that needs no model must
pass, and the scorer must catch the errors it exists to catch."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from evals import takeoff_bench as bench
from vula.takeoff.plan_reader import DXFReader, Room


def test_every_model_free_case_passes():
    report = asyncio.run(bench.run())
    failed = [(r["id"], r["checks"]) for r in report["results"] if not r.get("skipped") and not r["passed"]]
    assert report["summary"]["cases"] >= 4
    assert not failed, failed


def test_the_benchmark_catches_millimetres_read_as_metres(monkeypatch):
    # The pre-2026-10-31 reader ignored $INSUNITS. Put that back and the benchmark must fail.
    monkeypatch.setitem(DXFReader._INSUNITS, 4, ("mm", 1.0))
    r = asyncio.run(bench.run_case(bench.load_cases("dxf_mm_apartment")[0]))
    assert not r["passed"] and not r["checks"]["total_area"]


def test_score_catches_a_room_off_by_more_than_the_tolerance():
    truth = {"rooms": [{"name": "A", "area": 100.0}, {"name": "B", "area": 50.0}]}
    good = SimpleNamespace(rooms=[Room("A", 101.5), Room("B", 50.0)], review={})
    bad = SimpleNamespace(rooms=[Room("A", 103.0), Room("B", 50.0)], review={})
    assert bench.score(good, truth)["passed"]
    assert not bench.score(bad, truth)["passed"]


def test_score_catches_a_missing_room_even_when_the_total_matches():
    truth = {"rooms": [{"name": "A", "area": 100.0}, {"name": "B", "area": 50.0}]}
    merged = SimpleNamespace(rooms=[Room("A + B", 150.0)], review={})
    s = bench.score(merged, truth)
    assert not s["passed"] and s["missing"] == ["A", "B"]


def test_summary_groups_by_plan_type():
    s = bench.summarise([{"plan_type": "dxf", "passed": True, "total_area_err": 0.0},
                         {"plan_type": "dxf", "passed": False, "total_area_err": 0.04},
                         {"plan_type": "pdf_text", "passed": True, "total_area_err": 0.01}])
    assert s["by_plan_type"]["dxf"] == {"cases": 2, "passed": 1, "mean_abs_area_err": 0.02,
                                        "max_area_err": 0.04, "pass_pct": 50.0}
