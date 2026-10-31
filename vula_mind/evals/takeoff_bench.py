"""Takeoff accuracy benchmark (2026-10-31, docs/golive-and-takeoff-audit.md Phase 4a).

Same shape as evals/golive.py: cases → score → report, with a pass mark. Each case is a plan plus
the quantities a person measured by hand (`truth.json`). The harness reads the plan with the
real takeoff reader and reports the error per room, per plan type, against tolerances:

    area  — 2% per room and in total (AREA_TOL in vula/takeoff/accuracy.py)
    GFA   — 2%
    counts (doors, windows) — exact

Cases live in evals/takeoff_plans/<case>/truth.json:

    {"plan_type": "dxf" | "pdf_text" | "pdf_scanned" | "image",
     "file": "plan.dxf",                     # a real plan next to truth.json, OR
     "synthetic": {...},                     # a plan this harness draws (see _build)
     "rooms": [{"name": "Lounge", "area": 48.0}, ...],
     "gfa": 80.0, "counts": {"doors": 2, "windows": 3},
     "note": "where the truth came from"}

Modes:
    default — no model. DXF cases run the full reader (it never calls a model). PDF cases run
              the text extraction + deterministic room-schedule reader. This is what CI runs
              (tests/test_takeoff_bench.py) and every synthetic case must pass.
    --live  — the full PlanReader, AI reader included (needs Ollama/OpenRouter). Use it on real
              plans to measure the AI reader; that number decides Phase 4e (the sidecar).

    cd vula_mind
    python -m evals.takeoff_bench                 # all cases, no model
    python -m evals.takeoff_bench --live --store  # + AI reader, saved to vula_eval_reports
    python -m evals.takeoff_bench --case dxf_mm_apartment --json

`--store` writes one vula_eval_reports row per plan type (model='takeoff_bench', skill=<plan type>,
report.mean_abs_area_err). vula/takeoff/accuracy.bench_history reads it back, so a plan type
that benchmarks badly can't report high confidence in production.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from vula.takeoff.accuracy import AREA_TOL

CASES_DIR = Path(__file__).parent / "takeoff_plans"


# ── synthetic plans ───────────────────────────────────────────────────────────

def _build(spec: Dict[str, Any], out_dir: Path) -> Path:
    kind = spec.get("kind", "dxf")
    if kind == "dxf":
        import ezdxf
        doc = ezdxf.new()
        doc.header["$INSUNITS"] = int(spec.get("insunits", 4))
        msp = doc.modelspace()
        if spec.get("outline"):
            msp.add_lwpolyline(spec["outline"], close=True)
        for r in spec.get("rooms", []):
            msp.add_lwpolyline(r["pts"], close=True)
            if r.get("label", True):
                at = r.get("label_at") or [sum(p[0] for p in r["pts"]) / len(r["pts"]),
                                           sum(p[1] for p in r["pts"]) / len(r["pts"])]
                msp.add_text(r["name"], dxfattribs={"insert": tuple(at)})
        for b in spec.get("blocks", []):
            if b["name"] not in doc.blocks:
                doc.blocks.new(name=b["name"])
            for at in b["at"]:
                msp.add_blockref(b["name"], tuple(at))
        path = out_dir / "plan.dxf"
        doc.saveas(path)
        return path
    if kind == "pdf_text":
        import fitz
        doc = fitz.open()
        page = doc.new_page(width=842, height=595)
        y = 60
        for line in spec["lines"]:
            page.insert_text((60, y), line, fontsize=10)
            y += 16
        path = out_dir / "plan.pdf"
        doc.save(path)
        return path
    raise ValueError(f"unknown synthetic kind {kind!r}")


# ── reading ───────────────────────────────────────────────────────────────────

async def _read(path: Path, live: bool):
    from vula.takeoff.plan_reader import ExtractedProject, PDFReader, PlanReader, Room, assess
    from vula.takeoff.accuracy import schedule_rooms_from_text
    if live or path.suffix.lower() == ".dxf":
        return await PlanReader(tenant_id="bench").read(path), "full" if live else "deterministic"
    if path.suffix.lower() == ".pdf":
        sheets = await PDFReader().read(path)
        text = "\n".join(s.raw_markdown or s.raw_text for s in sheets)
        measured = schedule_rooms_from_text(text)
        p = ExtractedProject(sheets=sheets, rooms=[Room(name=n, area=a) for n, a in measured],
                             deterministic_rooms=measured)
        return assess(p, "bench", use_history=False), "deterministic"
    raise ValueError(f"{path.suffix} needs --live (the AI reader)")


# ── scoring ───────────────────────────────────────────────────────────────────

def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def _err(got: float, want: float) -> float:
    return abs(got - want) / want if want else (0.0 if not got else 1.0)


def score(project: Any, truth: Dict[str, Any], tol: float = AREA_TOL) -> Dict[str, Any]:
    """Compare a read project with the hand-measured truth. Pure — no I/O."""
    got = {}
    for r in getattr(project, "rooms", []) or []:
        got.setdefault(_norm(r.name), 0.0)
        got[_norm(r.name)] += float(r.area or 0)
    rooms, missing = [], []
    for t in truth.get("rooms", []):
        k = _norm(t["name"])
        if k not in got:
            missing.append(t["name"])
            continue
        e = _err(got.pop(k), float(t["area"]))
        rooms.append({"name": t["name"], "want": t["area"], "error": round(e, 4), "ok": e <= tol})
    extra = sorted(got)
    want_total = sum(float(t["area"]) for t in truth.get("rooms", []))
    got_total = sum(float(r.area or 0) for r in getattr(project, "rooms", []) or [])
    total_err = _err(got_total, want_total) if want_total else 0.0
    checks = {"rooms": all(r["ok"] for r in rooms) and not missing,
              "total_area": total_err <= tol}
    gfa_err = None
    if truth.get("gfa"):
        gfa_err = _err(float(getattr(project, "gross_floor_area", 0) or 0), float(truth["gfa"]))
        checks["gfa"] = gfa_err <= tol
    counts = {}
    if truth.get("counts"):
        dw = getattr(project, "doors_windows", []) or []
        have = {"doors": sum(int(x.count or 1) for x in dw if not str(x.ref).upper().startswith("W")),
                "windows": sum(int(x.count or 1) for x in dw if str(x.ref).upper().startswith("W"))}
        for k, v in truth["counts"].items():
            counts[k] = {"want": v, "got": have.get(k, 0)}
        checks["counts"] = all(c["want"] == c["got"] for c in counts.values())
    if "expect_review" in truth:
        checks["review"] = (project.review.get("status") == "pending") == bool(truth["expect_review"])
    return {"passed": all(checks.values()), "checks": checks, "total_area_err": round(total_err, 4),
            "gfa_err": None if gfa_err is None else round(gfa_err, 4), "rooms": rooms,
            "missing": missing, "extra": extra, "counts": counts,
            "confidence": getattr(project, "confidence_overall", None),
            "review": (getattr(project, "review", None) or {}).get("status")}


def load_cases(only: Optional[str] = None, cases_dir: Path = CASES_DIR) -> List[Dict[str, Any]]:
    out = []
    for f in sorted(cases_dir.glob("**/truth.json")):
        case = json.loads(f.read_text())
        case["id"] = f.parent.name
        case["dir"] = f.parent
        if only and case["id"] != only:
            continue
        out.append(case)
    return out


async def run_case(case: Dict[str, Any], live: bool = False) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory() as td:
        if case.get("synthetic"):
            path = _build(case["synthetic"], Path(td))
        else:
            path = case["dir"] / case["file"]
        if path.suffix.lower() not in (".dxf", ".pdf") and not live:
            return {"id": case["id"], "plan_type": case.get("plan_type"), "skipped": "needs --live"}
        t0 = time.time()
        project, reader = await _read(path, live)
        res = score(project, case)
    return {"id": case["id"], "plan_type": case.get("plan_type", "unknown"), "reader": reader,
            "seconds": round(time.time() - t0, 2), **res}


def summarise(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_type: Dict[str, Dict[str, Any]] = {}
    for r in results:
        if r.get("skipped"):
            continue
        t = by_type.setdefault(r["plan_type"], {"cases": 0, "passed": 0, "errs": []})
        t["cases"] += 1
        t["passed"] += int(r["passed"])
        t["errs"].append(r["total_area_err"])
    for t in by_type.values():
        errs = t.pop("errs")
        t["mean_abs_area_err"] = round(sum(errs) / len(errs), 4) if errs else None
        t["max_area_err"] = round(max(errs), 4) if errs else None
        t["pass_pct"] = round(100 * t["passed"] / t["cases"], 1) if t["cases"] else 0.0
    ran = [r for r in results if not r.get("skipped")]
    return {"tolerance": AREA_TOL, "cases": len(ran), "passed": sum(int(r["passed"]) for r in ran),
            "by_plan_type": by_type}


async def run(only: Optional[str] = None, live: bool = False,
              cases_dir: Path = CASES_DIR) -> Dict[str, Any]:
    results = [await run_case(c, live) for c in load_cases(only, cases_dir)]
    return {"live": live, "results": results, "summary": summarise(results)}


def store(report: Dict[str, Any], by: str = "takeoff_bench") -> int:
    """One vula_eval_reports row per plan type — what accuracy.bench_history reads."""
    from vula import durable
    n = 0
    for plan_type, s in report["summary"]["by_plan_type"].items():
        ok = durable.upsert("vula_eval_reports", {
            "model": "takeoff_bench", "skill": plan_type, "status": "done",
            "passed": s["passed"], "total": s["cases"], "created_by": by,
            "report": {**s, "live": report["live"],
                       "cases": [r for r in report["results"] if r.get("plan_type") == plan_type]}})
        n += int(ok)
    return n


def _print(report: Dict[str, Any]) -> None:
    print(f"Takeoff benchmark ({'AI reader' if report['live'] else 'no model'}), "
          f"tolerance {AREA_TOL * 100:.0f}%")
    for r in report["results"]:
        if r.get("skipped"):
            print(f"  -    {r['id']:<28} skipped: {r['skipped']}")
            continue
        mark = "PASS" if r["passed"] else "FAIL"
        bad = [k for k, v in r["checks"].items() if not v]
        print(f"  {mark} {r['id']:<28} {r['plan_type']:<12} area err {r['total_area_err'] * 100:5.2f}%"
              f"  conf {r['confidence']}  review {r['review']}" + (f"  failed: {', '.join(bad)}" if bad else ""))
        for m in r["missing"]:
            print(f"         missing room: {m}")
    s = report["summary"]
    print(f"\n{s['passed']}/{s['cases']} cases pass")
    for t, v in s["by_plan_type"].items():
        print(f"  {t:<12} {v['passed']}/{v['cases']}  mean area err {v['mean_abs_area_err'] * 100:.2f}%"
              f"  max {v['max_area_err'] * 100:.2f}%")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--case")
    ap.add_argument("--live", action="store_true", help="run the AI reader too (needs a model)")
    ap.add_argument("--store", action="store_true", help="save to vula_eval_reports")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dir", type=Path, default=CASES_DIR, help="cases folder (e.g. real plans)")
    a = ap.parse_args(argv)
    report = asyncio.run(run(a.case, a.live, a.dir))
    print(json.dumps(report, default=str, indent=2)) if a.json else _print(report)
    if a.store:
        print(f"stored {store(report)} report row(s)")
    s = report["summary"]
    return 0 if s["passed"] == s["cases"] else 1


if __name__ == "__main__":
    sys.exit(main())
