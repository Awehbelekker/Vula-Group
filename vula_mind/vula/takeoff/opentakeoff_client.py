"""
vula/takeoff/opentakeoff_client.py: measured geometry for PDF plans, from the OpenTakeoff sidecar.

docs/takeoff-audit.md item 1: for a PDF, every room area the takeoff priced was a number an LLM read
or guessed from the drawing text. The sidecar (services/opentakeoff: OpenTakeoff pinned to its last
Apache-2.0 commit) measures the plan instead. It sets each sheet's scale, traces each labelled room
from the plan's own linework, and returns area and perimeter with the engine's trace confidence.

This module:
- `measure_plan(path)` calls the sidecar. It returns None when OPENTAKEOFF_URL is unset or the
  sidecar fails (fail-open: the takeoff carries on exactly as before).
- `parse_response(raw)` converts the engine's units (SF / LF / ft per px) to metres, in one place.
- `apply_measurements(project, plan)` feeds those numbers into the EXISTING ExtractedProject, so
  BOQGenerator and everything downstream is unchanged. A measured room keeps the name and finishes
  the room schedule gave it; only its area and perimeter become the measured ones. The returned
  `agreements` (AI-read vs measured, per room) are the signal for measured confidence and the
  review gate. Those live in plan_reader / api (the other takeoff session); this module only
  reports them.

Never guesses: an unscaled sheet is reported, not measured, and a measured room that matches two
schedule rooms is left unmatched. The engine's scale is always unconfirmed until a person confirms
it, and that is recorded on every calibration.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from config import settings

logger = logging.getLogger(__name__)

M_PER_FT = 0.3048
M2_PER_SF = 0.09290304


@dataclass
class MeasuredRoom:
    label: str
    area_m2: float
    perimeter_m: Optional[float]
    page: int
    trace_confidence: Optional[float] = None       # the engine's own 0..1 score for this trace
    confidence_factors: List[str] = field(default_factory=list)
    raster_traced: bool = False                    # bounded by pixels (scan), not vector linework


@dataclass
class SheetCalibration:
    page: int
    sheet_number: Optional[str]
    detected_scale: Optional[str]                  # the scale note on the drawing, as drawn
    scale_label: Optional[str]                     # the scale actually applied
    method: Optional[str]                          # user_label | detected | None (unscaled)
    m_per_px: Optional[float]                      # real metres per engine image px
    confirmed: bool = False                        # a person confirmed it (never true from the engine)
    verified_by: Optional[str] = None
    warning: Optional[str] = None                  # e.g. several scale notes on one sheet
    withheld: Optional[Dict[str, Any]] = None      # rooms the engine skipped, and why
    error: Optional[str] = None


@dataclass
class MeasuredPlan:
    engine: Dict[str, str]
    sheets: List[SheetCalibration]
    rooms: List[MeasuredRoom]


@dataclass
class Agreement:
    room: str
    page: int
    ai_area_m2: float
    measured_area_m2: float
    delta_pct: Optional[float]                     # |ai − measured| / measured × 100; None if no AI figure
    trace_confidence: Optional[float]
    scale_label: Optional[str]
    scale_confirmed: bool


@dataclass
class MeasurementResult:
    agreements: List[Agreement] = field(default_factory=list)
    unmatched: List[MeasuredRoom] = field(default_factory=list)
    calibrations: List[SheetCalibration] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


# ─── units ────────────────────────────────────────────────────────────────────

def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def parse_response(raw: Dict[str, Any]) -> MeasuredPlan:
    """The sidecar's JSON → metric dataclasses. Rejects a response in units it doesn't declare."""
    units = raw.get("units") or {}
    if units and (units.get("area") != "sf" or units.get("length") != "lf"):
        raise ValueError(f"Unexpected OpenTakeoff units: {units}")
    sheets: List[SheetCalibration] = []
    rooms: List[MeasuredRoom] = []
    for s in raw.get("sheets") or []:
        page = int(s.get("page") or 0)
        sc = s.get("scale") or {}
        upp = _num(sc.get("upp_ft"))
        sheets.append(SheetCalibration(
            page=page, sheet_number=s.get("sheet_number"), detected_scale=s.get("detected_scale"),
            scale_label=sc.get("label") if sc else None, method=sc.get("source") if sc else None,
            m_per_px=round(upp * M_PER_FT, 9) if upp else None, confirmed=bool(sc.get("confirmed")),
            warning=sc.get("warning") if sc else None, withheld=s.get("withheld"), error=s.get("error")))
        for r in s.get("rooms") or []:
            area_sf = _num(r.get("area_sf"))
            if not area_sf or area_sf <= 0:
                continue
            per_lf = _num(r.get("perimeter_lf"))
            rooms.append(MeasuredRoom(
                label=str(r.get("label") or "").strip(), page=page,
                area_m2=round(area_sf * M2_PER_SF, 3),
                perimeter_m=round(per_lf * M_PER_FT, 3) if per_lf else None,
                trace_confidence=_num(r.get("confidence")),
                confidence_factors=list(r.get("confidence_factors") or []),
                raster_traced=bool(r.get("raster_traced"))))
    return MeasuredPlan(engine=dict(raw.get("engine") or {}), sheets=sheets, rooms=rooms)


# ─── the call ─────────────────────────────────────────────────────────────────

async def measure_plan(path: Path, scales: Optional[Dict[int, str]] = None,
                       sensitivity: Optional[float] = None) -> Optional[MeasuredPlan]:
    """Measure a PDF plan with the sidecar. `scales` overrides a sheet's scale by page (e.g. a
    person's calibration: {2: "1:50"}). None = not configured, not a PDF, or the sidecar failed;
    the caller carries on with the AI-read figures, as before."""
    base = (settings.opentakeoff_url or "").rstrip("/")
    if not base or path.suffix.lower() != ".pdf":
        return None
    params: Dict[str, str] = {}
    if scales:
        params["scales"] = json.dumps({str(k): v for k, v in scales.items()})
    if sensitivity is not None:
        params["sensitivity"] = str(sensitivity)
    try:
        async with httpx.AsyncClient(timeout=settings.opentakeoff_timeout_s) as client:
            resp = await client.post(f"{base}/measure", params=params or None, content=path.read_bytes(),
                                     headers={"content-type": "application/pdf",
                                              "x-api-key": settings.opentakeoff_api_key or ""})
            resp.raise_for_status()
            return parse_response(resp.json())
    except Exception as e:
        # Type only: the error text could echo plan content.
        logger.warning("OpenTakeoff measurement unavailable (%s) — using AI-read geometry", type(e).__name__)
        return None


# ─── feeding ExtractedProject ─────────────────────────────────────────────────

def _tokens(s: str) -> List[str]:
    return re.findall(r"[a-z]+|\d+[a-z]?", (s or "").lower())


def _matches(label: str, room_name: str) -> bool:
    """A measured label names this schedule room: the same words, a room number both carry
    ("104" ↔ "Office 104"), or every word of one inside the other ("OFFICE" ↔ "Office")."""
    lt, rt = _tokens(label), _tokens(room_name)
    if not lt or not rt:
        return False
    if lt == rt:
        return True
    nums_l = {t for t in lt if t[0].isdigit()}
    nums_r = {t for t in rt if t[0].isdigit()}
    if nums_l and nums_r:
        return bool(nums_l & nums_r)               # both numbered → the number decides
    return set(lt) <= set(rt) or set(rt) <= set(lt)


def apply_measurements(project: Any, plan: Optional[MeasuredPlan]) -> MeasurementResult:
    """Put measured geometry into `project` (an ExtractedProject) in place, and report how the
    AI-read and measured figures agree. A schedule room matched by exactly one measured room
    takes the measured area and perimeter. With no room schedule at all, the measured rooms
    become the schedule. Measured rooms that can't be matched unambiguously are returned, not
    guessed into the bill."""
    out = MeasurementResult()
    if plan is None:
        return out
    out.calibrations = plan.sheets
    from vula.takeoff.plan_reader import Room     # lazy: plan_reader is the caller

    rooms = list(project.rooms)
    if not rooms:
        for m in plan.rooms:
            project.rooms.append(Room(name=m.label or f"Room p{m.page}", area=m.area_m2,
                                      perimeter=m.perimeter_m or 0.0,
                                      notes="Measured from the plan (OpenTakeoff)"))
        out.unmatched = []
    else:
        claimed: Dict[int, MeasuredRoom] = {}
        ambiguous: set = set()
        for m in plan.rooms:
            hits = [i for i, r in enumerate(rooms) if _matches(m.label, r.name)]
            if len(hits) != 1 or hits[0] in claimed:
                if len(hits) == 1:
                    ambiguous.add(hits[0])        # two measured rooms claim one schedule room
                out.unmatched.append(m)
                continue
            claimed[hits[0]] = m
        for i in ambiguous:
            out.unmatched.append(claimed.pop(i))
        cal = {c.page: c for c in plan.sheets}
        for i, m in claimed.items():
            r = rooms[i]
            ai = float(r.area or 0)
            c = cal.get(m.page)
            out.agreements.append(Agreement(
                room=r.name, page=m.page, ai_area_m2=round(ai, 3), measured_area_m2=m.area_m2,
                delta_pct=round(abs(ai - m.area_m2) / m.area_m2 * 100, 2) if ai > 0 else None,
                trace_confidence=m.trace_confidence,
                scale_label=c.scale_label if c else None, scale_confirmed=bool(c and c.confirmed)))
            r.area = m.area_m2
            if m.perimeter_m:
                r.perimeter = m.perimeter_m

    measured = len(out.agreements) + (len(plan.rooms) if not rooms else 0)
    tag = " ".join(filter(None, [plan.engine.get("tag"), (plan.engine.get("commit") or "")[:8]]))
    out.notes.append(f"Measured {measured} room(s) from the plan linework (OpenTakeoff{' ' + tag if tag else ''}).")
    unscaled = [c for c in plan.sheets if not c.scale_label and not c.error]
    if unscaled:
        out.notes.append("No scale on sheet(s) " + ", ".join(str(c.sheet_number or c.page) for c in unscaled)
                         + " — not measured. Set each sheet's scale to measure it.")
    if any(c.scale_label and not c.confirmed for c in plan.sheets):
        out.notes.append("Sheet scales were read off the drawings and are not yet confirmed by a person.")
    if out.unmatched and rooms:
        out.notes.append(f"{len(out.unmatched)} measured room(s) didn't match the room schedule and were "
                         "left out of the bill: " + ", ".join(m.label or "?" for m in out.unmatched[:8]))
    for c in plan.sheets:
        if c.warning:
            out.notes.append(f"Sheet {c.sheet_number or c.page}: {c.warning}")
    project.extraction_notes.extend(out.notes)
    return out
