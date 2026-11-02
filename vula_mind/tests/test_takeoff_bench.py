"""The takeoff benchmark (evals/takeoff_bench.py) runs in CI: every case that needs no model must
pass, and the scorer must catch the errors it exists to catch."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from evals import takeoff_bench as bench
from vula.takeoff.plan_reader import DXFReader, Room


def test_every_model_free_case_passes():
    report = asyncio.run(bench.run())
    covered = [r for r in report["results"] if not r.get("skipped") and r.get("covered", True)]
    failed = [(r["id"], r["checks"]) for r in covered if not r["passed"]]
    assert report["summary"]["cases"] >= 5
    assert not failed, failed
    assert not report["summary"]["skipped"]         # every real plan matches its sha256


def test_the_not_to_scale_sheet_comes_back_unmeasured():
    r = asyncio.run(bench.run_case(bench.load_cases("va-roseburg-a03a-nts")[0]))
    assert r["covered"] and r["passed"] and r["checks"] == {"unmeasured": True}


def test_real_plans_are_intact_and_licensed():
    import hashlib
    real = [c for c in bench.load_cases() if c.get("file")]
    assert len(real) >= 6
    for c in real:
        assert hashlib.sha256((c["dir"] / c["file"]).read_bytes()).hexdigest() == c["sha256"], c["id"]
        assert (c.get("source") or {}).get("licence"), c["id"]
        assert c.get("tolerances", {}).get("area_pct") == 2.0, c["id"]


def test_an_uncovered_case_is_never_a_pass():
    truth = {"rooms": [{"label": "LIVING", "area_m2": 23.854}]}
    s = bench.score(SimpleNamespace(rooms=[], review={"status": "pending"}), truth)
    assert s["covered"] is False and s["passed"] is False


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
