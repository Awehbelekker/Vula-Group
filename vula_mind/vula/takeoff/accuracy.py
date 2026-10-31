"""
vula/takeoff/accuracy.py — how sure Vula is of a takeoff, and whether a person must look first.

2026-10-31 (docs/golive-and-takeoff-audit.md, Phase 4). Before this, a takeoff's "confidence"
was a constant per reading method (0.90 for a text PDF, 0.70 for OCR, 0.85 for DXF) and
geometry_reconciled() only warned. Pricing ran straight off whatever the model read.

Now:
- A second, deterministic reading sits next to the AI reader wherever one exists: DXF geometry
  measured in its declared units, or the room schedule's printed areas parsed straight from the
  PDF text (`schedule_rooms_from_text`). No model is involved in either.
- Confidence is *measured*: how closely the independent readings agree, whether the room
  schedule fits the floor area, whether the drawing units were declared, and how this plan
  type has scored on the benchmark (evals/takeoff_bench.py). It is the weakest of those
  signals, not an average, so one bad signal can't be hidden by good ones. A reading with
  nothing to check it against is never "confident".
- `review_reasons` lists why a person must confirm the quantities before pricing. Any reason
  holds the job at `needs_review` (vula/takeoff/api.py). Nothing unreviewed reaches
  BOQGenerator.generate, which refuses a project whose review is pending or rejected.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Area agreement tolerance: two readings within 2% of each other agree (the benchmark's pass
# mark too). Confidence falls linearly to 0 at 10× this.
AREA_TOL = 0.02
# Below this, a job is held for review even if nothing else is wrong.
REVIEW_BELOW = 0.90
# Shown when there is nothing to check a reading against.
UNCORROBORATED = 0.5

_AREA = r"(\d{1,5}(?:[.,]\d{1,2})?)\s*(?:m²|m2|sq\.?\s?m|sqm|m\^2)"
_ROW = re.compile(r"^\s*(?:[A-Z]{0,3}\d{0,3}[.\-]?\s+)?([A-Za-z][A-Za-z0-9 /&'\-().]{1,60}?)\s*[|:\-–]?\s*"
                  + _AREA + r"\s*(?:\||$)", re.IGNORECASE | re.MULTILINE)
_TOTALS = re.compile(r"\b(total|gfa|gross|nett?|subtotal|sum|area schedule|nfa|overall)\b", re.IGNORECASE)


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def schedule_rooms_from_text(text: str) -> List[Tuple[str, float]]:
    """(room name, area m²) for each line of a printed room schedule: a name, then an area in
    m². Totals and GFA lines are skipped. Deterministic — the number is the one printed."""
    out: List[Tuple[str, float]] = []
    seen = set()
    for m in _ROW.finditer(text or ""):
        name = re.sub(r"\s+", " ", m.group(1)).strip(" |-:")
        if not name or _TOTALS.search(name):
            continue
        area = _num(m.group(2))
        if area <= 0:
            continue
        key = (name.lower(), area)
        if key in seen:                 # same row repeated on several sheets
            continue
        seen.add(key)
        out.append((name, area))
    return out


def parse_scale(scale: str) -> Optional[float]:
    """'1:100' → 100.0. None when there isn't one."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)", scale or "")
    if not m or float(m.group(1)) == 0:
        return None
    return float(m.group(2)) / float(m.group(1))


def _agreement(a: float, b: float, tol: float = AREA_TOL) -> Tuple[float, float]:
    """(agreement 0..1, relative error) of two independent measurements of the same thing."""
    if a <= 0 or b <= 0:
        return 0.0, 1.0
    err = abs(a - b) / max(a, b)
    if err <= tol:
        return 1.0, err
    return max(0.0, 1.0 - (err - tol) / (9 * tol)), err


def _schedule_fit(room_sum: float, gfa: float) -> float:
    """Net room area against gross floor area. Rooms normally sum to 75–100% of GFA (walls,
    shafts, circulation not scheduled). The existing geometry_reconciled bounds (35%, 115%)
    are where agreement reaches 0."""
    r = room_sum / gfa
    if 0.75 <= r <= 1.0:
        return 1.0
    if r < 0.75:
        return max(0.0, (r - 0.35) / 0.40)
    return max(0.0, (1.15 - r) / 0.15)


def bench_history(plan_type: str) -> Optional[float]:
    """Mean absolute area error (fraction) of this plan type on the latest benchmark run, from
    vula_eval_reports (model='takeoff_bench'). None when there's no run on file."""
    try:
        from vula import durable
        rows = durable.select("vula_eval_reports", "report", order="created_at", desc=True,
                              limit=1, model="takeoff_bench", skill=plan_type)
        if rows and isinstance(rows[0].get("report"), dict):
            v = rows[0]["report"].get("mean_abs_area_err")
            return float(v) if v is not None else None
    except Exception:  # noqa: BLE001 — history is a hint, never a reason to fail
        return None
    return None


def plan_type(project: Any) -> str:
    """dxf | pdf_text | pdf_scanned | image — what the benchmark groups results by."""
    sheets = getattr(project, "sheets", []) or []
    if any(getattr(s, "scale_method", "") in ("dxf_units", "inferred_from_extent") for s in sheets):
        return "dxf"
    if any(getattr(s, "sheet_number", "").startswith("IMG") for s in sheets):
        return "image"
    if sheets and all(getattr(s, "source", "") == "ocr" for s in sheets):
        return "pdf_scanned"
    return "pdf_text"


def measured_confidence(project: Any, history: Optional[float] = None,
                        use_history: bool = True) -> Tuple[float, Dict[str, Dict[str, float]]]:
    """(confidence 0..1, the signals it came from). Confidence is the weakest signal."""
    signals: Dict[str, Dict[str, float]] = {}
    ai = sum(r.area for r in getattr(project, "ai_rooms", None) or [])
    det = sum(a for _, a in getattr(project, "deterministic_rooms", None) or [])
    if ai > 0 and det > 0:
        agree, err = _agreement(ai, det)
        signals["ai_vs_measured"] = {"agreement": round(agree, 3), "error": round(err, 4),
                                     "ai_m2": round(ai, 2), "measured_m2": round(det, 2)}
    rooms_sum = sum(r.area for r in getattr(project, "rooms", []) or [])
    gfa = getattr(project, "gross_floor_area", 0) or 0
    if gfa > 0 and rooms_sum > 0:
        signals["rooms_vs_gfa"] = {"agreement": round(_schedule_fit(rooms_sum, gfa), 3),
                                   "ratio": round(rooms_sum / gfa, 3)}
    for s in getattr(project, "sheets", []) or []:
        if getattr(s, "scale_method", "") == "inferred_from_extent":
            signals["units"] = {"agreement": 0.5, "verified": 0.0}
            break
        if getattr(s, "scale_method", "") == "dxf_units":
            signals["units"] = {"agreement": 1.0, "verified": 1.0}
    if use_history:
        h = history if history is not None else bench_history(plan_type(project))
        if h is not None:
            signals["benchmark"] = {"agreement": round(max(0.0, 1.0 - max(0.0, h - AREA_TOL) / (9 * AREA_TOL)), 3),
                                    "mean_abs_area_err": round(h, 4)}
    corroborated = "ai_vs_measured" in signals or "rooms_vs_gfa" in signals or (
        "units" in signals and signals["units"]["verified"] == 1.0)
    if not corroborated:
        return 0.0 if not rooms_sum else UNCORROBORATED, signals
    return round(min(v["agreement"] for v in signals.values()), 3), signals


def review_reasons(project: Any, geometry_warnings: Iterable[str] = ()) -> List[str]:
    """Why a person must confirm these quantities before anything is priced. Empty = none."""
    reasons = list(geometry_warnings)
    if not (getattr(project, "rooms", None) or []):
        if not reasons:
            reasons.append("No rooms were read from the drawings.")
        return reasons
    sig = getattr(project, "confidence_signals", {}) or {}
    a = sig.get("ai_vs_measured")
    if a and a["error"] > AREA_TOL:
        reasons.append(f"The AI reading ({a['ai_m2']:.1f} m²) and the measured/printed areas "
                       f"({a['measured_m2']:.1f} m²) differ by {a['error'] * 100:.1f}% "
                       f"(tolerance {AREA_TOL * 100:.0f}%).")
    u = sig.get("units")
    if u and not u.get("verified"):
        reasons.append("The drawing doesn't say its units; they were inferred from its size. "
                       "Confirm one known dimension.")
    if not ("ai_vs_measured" in sig or "rooms_vs_gfa" in sig or (u and u.get("verified"))):
        reasons.append("Only one reading of the quantities — nothing independent to check it "
                       "against. Confirm the room areas.")
    # A room with no floor finish gets no flooring line at all (boq_generator skips it), so a DXF
    # read — geometry, no finishes — would be priced with its flooring silently missing.
    bare = [r.name for r in project.rooms if not (getattr(r, "floor_finish", "") or "").strip()]
    if bare:
        reasons.append(f"{len(bare)} of {len(project.rooms)} room(s) have no floor finish, so their "
                       f"flooring won't be priced. Add finishes or approve without flooring.")
    conf = getattr(project, "confidence_overall", 0) or 0
    if conf < REVIEW_BELOW and not reasons:
        reasons.append(f"Measured confidence is {conf * 100:.0f}% (below {REVIEW_BELOW * 100:.0f}%).")
    return reasons
