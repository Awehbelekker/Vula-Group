"""Score the OpenTakeoff sidecar against the free public-plan corpus (evals/takeoff_plans/).

    OPENTAKEOFF_URL=http://localhost:8790 OPENTAKEOFF_API_KEY=... \
        python -m evals.takeoff_sidecar            # every case
        python -m evals.takeoff_sidecar --case va-stcloud-af101 --json

Not a pytest: it needs a running sidecar (services/opentakeoff). The scoring is pure and unit-tested
(tests/test_takeoff_sidecar_eval.py). For each plan it reports the area error % per room and per
plan type, against the corpus tolerance (2% area). It also checks a not-to-scale sheet comes back
unmeasured. Counts and lengths are in the corpus but the sidecar's bridge doesn't measure them
yet, so they show as "not covered", never as passed.

Every truth figure says what it is (analytic, printed on the sheet, hand count, or an
agent-traced reference ring not yet reviewed by a person). A pass against an unreviewed ring
proves agreement with that ring, not accuracy. The report says so per case.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

CORPUS = Path(__file__).resolve().parent / "takeoff_plans"


@dataclass
class RoomScore:
    label: str
    truth_m2: float
    measured_m2: Optional[float]
    error_pct: Optional[float]
    ok: bool
    truth_kind: str


@dataclass
class CaseScore:
    case: str
    plan_type: str
    ok: bool
    rooms: List[RoomScore] = field(default_factory=list)
    extra_measured: List[str] = field(default_factory=list)
    not_covered: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    human_reviewed: bool = False


def load_cases(only: Optional[str] = None) -> List[dict]:
    cases = []
    for f in sorted(CORPUS.glob("*/truth.json")):
        t = json.loads(f.read_text())
        if only and t["case"] != only:
            continue
        t["_dir"] = str(f.parent)
        cases.append(t)
    return cases


def score_case(truth: dict, plan) -> CaseScore:
    """`plan` is a MeasuredPlan (vula.takeoff.opentakeoff_client) or None (the sidecar failed)."""
    from vula.takeoff.opentakeoff_client import _matches

    tol = truth["tolerances"]["area_pct"]
    cs = CaseScore(case=truth["case"], plan_type=truth["plan_type"], ok=True,
                   human_reviewed=bool(truth.get("review", {}).get("human_reviewed")))
    cs.not_covered = [f"length: {x['label']}" for x in truth.get("lengths", [])] + \
                     [f"count: {x['label']}" for x in truth.get("counts", [])]
    if plan is None:
        cs.ok = False
        cs.notes.append("sidecar returned nothing (unreachable, refused, or failed)")
        return cs

    if not truth["expect"]["measurable"]:
        if plan.rooms:
            cs.ok = False
            cs.notes.append(f"measured {len(plan.rooms)} room(s) on a not-to-scale sheet; it must refuse")
        else:
            cs.notes.append("correctly left unmeasured: " + truth["expect"].get("why", ""))
        return cs

    # A room split into two finish rings in the truth is one room to a detector: compare totals.
    want: Dict[str, dict] = {}
    for r in truth.get("rooms", []):
        w = want.setdefault(r["label"], {"m2": 0.0, "kind": r["truth"]})
        w["m2"] += r["area_m2"]
    used = set()
    for label, w in want.items():
        hits = [i for i, m in enumerate(plan.rooms) if i not in used and _matches(m.label, label)]
        if len(hits) != 1:
            cs.ok = False
            cs.rooms.append(RoomScore(label, round(w["m2"], 3), None, None, False, w["kind"]))
            cs.notes.append(f"{label}: {'not measured' if not hits else f'{len(hits)} measured rooms match'}")
            continue
        used.add(hits[0])
        got = plan.rooms[hits[0]].area_m2
        err = round(abs(got - w["m2"]) / w["m2"] * 100, 2)
        cs.rooms.append(RoomScore(label, round(w["m2"], 3), got, err, err <= tol, w["kind"]))
        cs.ok &= err <= tol
    cs.extra_measured = [m.label for i, m in enumerate(plan.rooms) if i not in used]
    if not cs.human_reviewed and any(r.truth_kind == "reference_ring" for r in cs.rooms):
        cs.notes.append("truth rings are agent-traced and not yet reviewed by a person")
    return cs


def summarise(scores: List[CaseScore]) -> dict:
    by_type: Dict[str, List[float]] = {}
    for s in scores:
        by_type.setdefault(s.plan_type, []).extend(r.error_pct for r in s.rooms if r.error_pct is not None)
    return {
        "cases": len(scores), "passed": sum(s.ok for s in scores),
        "area_error_pct_by_plan_type": {
            t: {"rooms": len(v), "mean": round(sum(v) / len(v), 2), "max": max(v)} if v else {"rooms": 0}
            for t, v in by_type.items()},
    }


async def _run(only: Optional[str]) -> List[CaseScore]:
    from vula.takeoff.opentakeoff_client import measure_plan
    out = []
    for t in load_cases(only):
        label = t["scale"].get("label")
        plan = await measure_plan(Path(t["_dir"]) / t["file"], scales={t["page"]: label} if label else None)
        out.append(score_case(t, plan))
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--case")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    from config import settings
    if not settings.opentakeoff_url:
        print("OPENTAKEOFF_URL is not set: start services/opentakeoff and point this at it.", file=sys.stderr)
        return 2
    scores = asyncio.run(_run(a.case))
    if a.json:
        print(json.dumps({"summary": summarise(scores), "cases": [asdict(s) for s in scores]}, indent=2))
    else:
        for s in scores:
            print(f"{'PASS' if s.ok else 'FAIL'}  {s.case}  ({s.plan_type}{'' if s.human_reviewed else ', truth unreviewed'})")
            for r in s.rooms:
                got = f"{r.measured_m2:.2f}" if r.measured_m2 is not None else "—"
                err = f"{r.error_pct:.2f}%" if r.error_pct is not None else "missing"
                print(f"      {r.label:30} truth {r.truth_m2:8.2f} m²  measured {got:>8}  {err}")
            for n in s.notes + ([f"not covered by the sidecar yet: {', '.join(s.not_covered)}"] if s.not_covered else []):
                print(f"      · {n}")
        print(json.dumps(summarise(scores), indent=2))
    return 0 if all(s.ok for s in scores) else 1


if __name__ == "__main__":
    sys.exit(main())
