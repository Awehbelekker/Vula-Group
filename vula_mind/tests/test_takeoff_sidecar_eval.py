"""The free public-plan takeoff corpus (evals/takeoff_plans) is intact, honest about its truth,
and the sidecar scorer grades correctly. No sidecar, model or network needed."""
import hashlib
import json
from pathlib import Path

import pytest

from evals.takeoff_sidecar import CORPUS, load_cases, score_case, summarise
from vula.takeoff.opentakeoff_client import MeasuredPlan, MeasuredRoom, SheetCalibration

TRUTH_KINDS = {"analytic", "reference_ring", "printed", "printed_nominal", "hand_count", "hand_measured"}


def test_corpus_has_every_case_and_its_plan_is_the_one_hashed():
    cases = load_cases()
    assert {c["case"] for c in cases} >= {
        "synthetic-four-room", "va-stcloud-af101", "porterville-adu-a1-101",
        "va-roseburg-a03a-nts", "va-dublin-a601-counts", "va-shreveport-c300-lengths"}
    for c in cases:
        pdf = Path(c["_dir"]) / c["file"]
        assert hashlib.sha256(pdf.read_bytes()).hexdigest() == c["sha256"], c["case"]
        assert pdf.read_bytes()[:5] == b"%PDF-"


def test_every_truth_figure_says_what_kind_of_truth_it_is():
    for c in load_cases():
        assert c["source"]["licence"] and c["source"]["via"].startswith("OpenTakeoff apache-2.0-final @ 84c7f48e")
        assert c["tolerances"] == {"area_pct": 2.0, "length_pct": 2.0, "count_exact": True}
        assert "human_reviewed" in c["review"]
        for row in c.get("rooms", []) + c.get("lengths", []) + c.get("counts", []):
            assert row["truth"] in TRUTH_KINDS, (c["case"], row)
        for r in c.get("rooms", []):
            assert r["area_m2"] > 0
        if not c["expect"]["measurable"]:
            assert not c.get("rooms") and c["scale"]["label"] is None


def test_attribution_ships_with_the_corpus():
    assert (CORPUS / "NOTICE.opentakeoff").read_text().startswith("OpenTakeoff")
    assert "84c7f48e" in (CORPUS / "README.md").read_text()


def test_metric_conversion_matches_a_hand_calculation():
    # synthetic OFFICE 101: 974 x 574 px at 36 px/ft = 431.40 SF = 40.077 m²
    t = next(c for c in load_cases() if c["case"] == "synthetic-four-room")
    assert t["rooms"][0]["area_m2"] == pytest.approx(974 * 574 / 36 ** 2 * 0.09290304, abs=1e-3)


def _plan(rooms, scaled=True):
    cal = SheetCalibration(page=1, sheet_number=None, detected_scale=None,
                           scale_label="1:100" if scaled else None, method="detected" if scaled else None,
                           m_per_px=None)
    return MeasuredPlan(engine={}, sheets=[cal], rooms=[MeasuredRoom(label=l, area_m2=a, perimeter_m=None, page=1)
                                                         for l, a in rooms])


def _case(name):
    return next(c for c in load_cases() if c["case"] == name)


def test_within_tolerance_passes_and_reports_error_per_room():
    t = _case("synthetic-four-room")
    s = score_case(t, _plan([("101", 40.5), ("102", 40.0), ("103", 39.5), ("104", 40.077)]))
    assert s.ok and [r.error_pct for r in s.rooms] == [1.06, 0.19, 1.44, 0.0]


def test_over_tolerance_or_missing_room_fails():
    t = _case("synthetic-four-room")
    assert not score_case(t, _plan([("101", 41.0), ("102", 40.0), ("103", 40.0), ("104", 40.0)])).ok   # 2.3%
    s = score_case(t, _plan([("101", 40.0), ("102", 40.0), ("103", 40.0)]))
    assert not s.ok and any("CORRIDOR 104: not measured" in n for n in s.notes)


def test_two_finish_room_is_scored_as_one_room():
    t = _case("va-stcloud-af101")
    total_134 = 25.877 + 5.335
    rooms = [("134", total_134), ("134A", 1.519), ("136", 12.042), ("137", 23.406), ("137A", 5.655)]
    s = score_case(t, _plan(rooms))
    assert s.ok and len(s.rooms) == 5
    assert any("not yet reviewed by a person" in n for n in s.notes)


def test_not_to_scale_sheet_must_come_back_unmeasured():
    t = _case("va-roseburg-a03a-nts")
    assert score_case(t, _plan([], scaled=False)).ok
    assert not score_case(t, _plan([("D104", 12.0)])).ok


def test_counts_and_lengths_are_never_reported_as_passed():
    s = score_case(_case("va-shreveport-c300-lengths"), _plan([]))
    assert s.not_covered == ['length: 8" sewer line MH to MH', 'length: 6" sewer service line']
    assert s.rooms == []


def test_sidecar_down_fails_and_summary_groups_by_plan_type():
    t = _case("synthetic-four-room")
    down = score_case(t, None)
    assert not down.ok
    up = score_case(t, _plan([("101", 40.5), ("102", 40.0), ("103", 39.5), ("104", 40.077)]))
    summ = summarise([down, up])
    assert summ["passed"] == 1
    assert summ["area_error_pct_by_plan_type"]["synthetic_control"]["max"] == 1.44
