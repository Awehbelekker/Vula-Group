"""
vula/takeoff/api.py

Vula Takeoff API — FastAPI routes

Mount onto main server:
    from vula.takeoff.api import router as takeoff_router
    app.include_router(takeoff_router, prefix="/takeoff")

Endpoints:
    POST /takeoff/upload          — upload plans, trigger reading
    GET  /takeoff/{job_id}        — poll job status
    GET  /takeoff/{job_id}/boq    — get generated BOQ
    GET  /takeoff/{job_id}/orders — get order package
    POST /takeoff/{job_id}/send   — send RFQs to suppliers
    GET  /takeoff/suppliers       — list tenant suppliers
    PUT  /takeoff/suppliers/{id}  — update supplier (discount, contact)
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from config import settings
from vula.api.master_auth import authorized_tenant
from vula import durable
from vula.takeoff.plan_reader import PlanReader, Room
from vula.takeoff.boq_generator import BOQGenerator
from vula.takeoff.order_manager import OrderManager, SupplierDatabase
from vula.takeoff.construction_rates_scraper import ConstructionRatesScraper, RatesDatabase
from vula.takeoff.boq_export import generate_boq_excel, HAS_OPENPYXL

logger = logging.getLogger(__name__)

router = APIRouter(tags=["takeoff"])

UPLOAD_DIR = settings.takeoff_upload_dir
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# In-memory job store, written through to Supabase vula_takeoff_jobs (migration 180): memory alone
# lost every job on each Railway deploy, so a finished BOQ 404'd the moment the service restarted.
_jobs: Dict[str, dict] = {}
_DONE = ("complete", "failed", "needs_review", "rejected")


def _save_job(job_id: str) -> None:
    job = _jobs.get(job_id)
    if job:
        durable.upsert("vula_takeoff_jobs", {"id": job_id, "tenant_id": job.get("tenant_id"),
                                             "status": job.get("status"), "state": job},
                       on_conflict="id")


def _load_job(job_id: str) -> Optional[dict]:
    rows = durable.select("vula_takeoff_jobs", "state", limit=1, id=job_id)
    if not rows or not isinstance(rows[0].get("state"), dict):
        return None
    job = rows[0]["state"]
    if job.get("status") not in _DONE:
        # The process that was working on it is gone — say so rather than "in progress" forever.
        job["status"], job["error"] = "failed", "Interrupted by a server restart — please upload the plans again."
    _jobs[job_id] = job
    return job


# ─── Models ──────────────────────────────────────────────────────────────────

class SupplierUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    whatsapp: Optional[str] = None
    contact_name: Optional[str] = None
    discount_pct: Optional[float] = None
    preferred: Optional[bool] = None
    notes: Optional[str] = None


class ReviewRoom(BaseModel):
    name: str
    area: float
    floor_finish: Optional[str] = None
    ceiling_finish: Optional[str] = None
    wall_finish: Optional[str] = None
    is_wet: Optional[bool] = None


class ReviewDecision(BaseModel):
    decision: str                                   # approve | reject
    rooms: Optional[List[ReviewRoom]] = None        # the corrected room schedule, if edited
    gross_floor_area: Optional[float] = None
    use_measured: bool = False                      # take the deterministic reading's rooms
    scale_confirmed: bool = False                   # the person checked a known dimension
    wall_height: Optional[float] = None             # m, floor to ceiling, confirmed for every room
    external_walls_m: Optional[float] = None        # m, so internal partitions can be measured
    note: str = ""


class ToProject(BaseModel):
    project: str
    replace: bool = False


class SendRFQRequest(BaseModel):
    dry_run: bool = True
    selected_trades: Optional[List[str]] = None


# ─── Background job ───────────────────────────────────────────────────────────

def _project_summary(project) -> dict:
    return {
        "title": project.title_block.project_name,
        "sheets": len(project.sheets),
        "rooms": len(project.rooms),
        # The rooms themselves — the dashboard's room schedule showed demo rooms for every
        # real job because only the count was returned.
        "room_list": [{"name": r.name, "area": round(r.area or 0, 1), "floor": r.floor_finish,
                       "ceiling": r.ceiling_finish, "walls": r.wall_finish}
                      for r in project.rooms],
        "gfa": project.gross_floor_area,
        "external_walls_m": getattr(project, "external_walls_m", 0) or 0,
        "confidence": round((project.confidence_overall or 0) * 100, 1),
        "confidence_signals": getattr(project, "confidence_signals", {}) or {},
        "measured_rooms": [{"name": n, "area": a} for n, a in (getattr(project, "deterministic_rooms", None) or [])],
        "ai_rooms": [{"name": r.name, "area": round(r.area or 0, 2)} for r in (getattr(project, "ai_rooms", None) or [])],
        "scale": [{"sheet": getattr(s, "sheet_number", ""), "scale": getattr(s, "scale", ""),
                   "method": getattr(s, "scale_method", ""),
                   "verified_by": getattr(s, "scale_verified_by", "")} for s in project.sheets],
        "notes": project.extraction_notes,
    }


def _project_to_state(project) -> dict:
    from dataclasses import asdict
    return asdict(project)


def _project_from_state(d: dict):
    """An ExtractedProject back from a job row — what was read, so an approval prices exactly the
    quantities the person saw (no second read of the drawings)."""
    from vula.takeoff.plan_reader import (DoorWindow, DrawingSheet, ExtractedProject, MEPSchedule,
                                          Room, TitleBlock)
    d = dict(d)
    p = ExtractedProject(**{k: v for k, v in d.items()
                            if k not in ("title_block", "sheets", "rooms", "doors_windows",
                                         "mep_schedules", "ai_rooms", "deterministic_rooms")})
    p.title_block = TitleBlock(**(d.get("title_block") or {}))
    p.sheets = [DrawingSheet(**s) for s in d.get("sheets") or []]
    p.rooms = [Room(**r) for r in d.get("rooms") or []]
    p.ai_rooms = [Room(**r) for r in d.get("ai_rooms") or []]
    p.doors_windows = [DoorWindow(**x) for x in d.get("doors_windows") or []]
    p.mep_schedules = [MEPSchedule(**x) for x in d.get("mep_schedules") or []]
    p.deterministic_rooms = [tuple(x) for x in d.get("deterministic_rooms") or []]
    return p


async def _price(job_id: str, project, tenant_id: str, markup: float):
    """BOQ → orders, for a project whose quantities need no review or have been approved."""
    _jobs[job_id]["status"] = "generating_boq"
    _save_job(job_id)
    generator = BOQGenerator(project, tenant_id=tenant_id, markup=markup)
    boq = await generator.generate()
    _jobs[job_id]["boq"] = boq.to_dict()

    _jobs[job_id]["status"] = "building_orders"
    _save_job(job_id)
    order_mgr = OrderManager(boq, tenant_id=tenant_id, job_id=job_id)
    package = order_mgr.build_orders()
    _jobs[job_id]["orders"] = {
        "count": len(package.orders),
        "total_estimated": round(package.total_estimated),
        "total_net": round(package.total_net_discounted),
        "total_savings": round(package.total_savings),
        "orders": [
            {
                "po_number": o.po_number,
                "supplier": o.supplier.name,
                "trade": o.supplier.category,
                "discount_pct": o.supplier.discount_pct,
                "lines": len(o.lines),
                "estimated_total": round(o.estimated_total),
                "net_after_discount": round(o.net_after_discount),
                "status": o.status,
            }
            for o in package.orders
        ],
    }
    _jobs[job_id]["order_summary"] = order_mgr.order_summary_text(package)
    _jobs[job_id]["status"] = "complete"
    _save_job(job_id)


async def _process_plans(job_id: str, file_path: Path, tenant_id: str, markup: float):
    """Full pipeline: read → (review, when needed) → BOQ → orders. Runs in background."""
    try:
        _jobs[job_id]["status"] = "reading_plans"
        _save_job(job_id)
        reader = PlanReader(tenant_id=tenant_id)
        project = await reader.read(file_path)
        _jobs[job_id]["project"] = _project_summary(project)
        _jobs[job_id]["review"] = dict(getattr(project, "review", None) or {"status": "not_required"})

        if _jobs[job_id]["review"].get("status") == "pending":
            # Phase 4d — the propose-confirm gate (same pattern as migration 137's automation
            # firings: staged, a person approves or rejects, decided_at + who recorded). Nothing
            # is priced or ordered until POST /takeoff/{job_id}/review approves it.
            _jobs[job_id]["extracted"] = _project_to_state(project)
            _jobs[job_id]["status"] = "needs_review"
            _save_job(job_id)
            return

        await _price(job_id, project, tenant_id, markup)

    except Exception as e:
        logger.error(f"Takeoff job {job_id} failed: {e}")
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = str(e)
        _save_job(job_id)


# ─── Routes ───────────────────────────────────────────────────────────────────

# ─── Construction rates (must come before /{job_id} to avoid shadowing) ──────

@router.get("/rates")
async def get_rates(changed_only: bool = False, threshold_pct: float = 5.0):
    """Return current construction rates from the local DB.

    Pass ?changed_only=true to see only rates that moved significantly
    since the last scrape.
    """
    db = RatesDatabase()
    if changed_only:
        rates = db.get_changes(threshold_pct=threshold_pct)
    else:
        rates = db.get_all()
    # 2026-09-11: a source silently producing 0 rates for weeks was only visible by reading
    # Railway logs by hand — surface it here so an admin can see it without that.
    failed_sources = [s for s in db.get_source_status() if s["status"] != "ok"]
    return {"count": len(rates), "rates": rates, "failed_sources": failed_sources}


@router.post("/rates/update")
async def trigger_rates_update(background_tasks: BackgroundTasks):
    """Trigger an immediate construction rates scrape.

    Runs asynchronously — returns immediately. Check /takeoff/rates
    after 2-5 minutes for updated data.

    In production this endpoint is called by a weekly cron / n8n workflow.
    Protect with API_KEY when not using the default open-dev mode.
    """
    async def _run():
        try:
            scraper = ConstructionRatesScraper()
            result = await scraper.run_full_update()
            logger.info("Rates update complete: %s new, %s updated in %.1fs",
                        result.new, result.updated, result.duration_s)
        except Exception as exc:
            logger.error("Rates update failed: %s", exc)

    background_tasks.add_task(_run)
    return {
        "status": "started",
        "message": "Construction rates scrape running in background. Check /takeoff/rates in 2-5 minutes.",
    }


@router.post("/upload")
async def upload_plans(
    background_tasks: BackgroundTasks,
    tenant_id: str = Form(default="default"),
    markup: float = Form(default=15.0),
    file: UploadFile = File(...),
):
    """
    Upload architectural drawings. Supports PDF, DXF, DWG, PNG, JPG.
    Returns job_id — poll /takeoff/{job_id} for status.
    """
    allowed = {".pdf", ".dxf", ".dwg", ".png", ".jpg", ".jpeg", ".tiff"}
    suffix = Path(file.filename).suffix.lower()
    if suffix not in allowed:
        raise HTTPException(400, f"Unsupported file type: {suffix}. Supported: {allowed}")

    content = await file.read()
    if len(content) > 100 * 1024 * 1024:  # 100MB limit
        raise HTTPException(413, "File too large (max 100MB)")

    job_id = str(uuid.uuid4())[:12]
    file_path = UPLOAD_DIR / f"{job_id}_{_safe_name(file.filename)}"
    file_path.write_bytes(content)

    _jobs[job_id] = {
        "status": "queued",
        "filename": file.filename,
        "tenant_id": tenant_id,
        "markup": markup,
        "file_path": str(file_path),
        "uploaded_at": time.time(),
    }

    _save_job(job_id)
    background_tasks.add_task(_process_plans, job_id, file_path, tenant_id, markup)

    return {
        "job_id": job_id,
        "filename": file.filename,
        "status": "queued",
        "message": f"Plans uploaded. Processing takes 2–5 minutes. Poll /takeoff/{job_id} for status.",
    }


def _safe_name(filename: Optional[str]) -> str:
    """The upload's name as a single safe path component (no directories, no odd characters)."""
    name = Path(filename or "plan").name
    stem, suffix = Path(name).stem, Path(name).suffix.lower()
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._")[:80] or "plan"
    return f"{stem}{suffix}"


def _delete_job(job_id: str, job: Optional[dict]) -> int:
    """Delete a job's plan file, its durable row and its memory copy. Returns files removed."""
    removed = 0
    fp = (job or {}).get("file_path")
    if fp:
        p = Path(fp)
        try:
            if p.resolve().parent == UPLOAD_DIR.resolve() and p.exists():
                p.unlink()
                removed = 1
        except OSError as exc:
            logger.warning("Takeoff %s: could not delete plan file: %s", job_id, exc)
    durable.delete("vula_takeoff_jobs", id=job_id)
    _jobs.pop(job_id, None)
    return removed


def purge_expired(days: Optional[int] = None, now: Optional[float] = None) -> Dict[str, int]:
    """POPIA retention (Phase 4g): plans and their takeoff jobs older than
    settings.takeoff_retention_days are deleted — the file on the volume, the vula_takeoff_jobs
    row and the in-memory copy. Files are judged by their modified time, rows by updated_at, so
    a file whose row is already gone (or the other way round) is still cleared."""
    import time as _time
    from datetime import datetime, timezone
    days = settings.takeoff_retention_days if days is None else days  # run by vula/retention.py
    if not days or days <= 0:
        return {"files": 0, "jobs": 0}
    now = now if now is not None else _time.time()
    cutoff = now - days * 86400
    files = jobs = 0
    for f in UPLOAD_DIR.glob("*"):
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
                files += 1
        except OSError:
            continue
    for job_id, job in list(_jobs.items()):
        if (job.get("uploaded_at") or now) < cutoff:
            _jobs.pop(job_id, None)
    try:
        from vula.commerce.service import _client
        iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
        res = _client().table("vula_takeoff_jobs").delete().lt("updated_at", iso).execute()
        jobs = len(res.data or [])
    except Exception as exc:  # noqa: BLE001 — no Supabase (tests/dev): files still cleared
        logger.info("Takeoff retention: job rows not purged (%s)", exc)
    return {"files": files, "jobs": jobs}


def _job_for(job_id: str, tenant_id: Optional[str]) -> Optional[dict]:
    """The job, or None when it doesn't exist or belongs to another tenant. ?tenant_id= is how a
    signed-in tenant member authenticates on these id-only routes (master_auth.require_auth,
    applied to this whole router in server.py) — the job must then be that tenant's."""
    job = _jobs.get(job_id) or _load_job(job_id)
    if not job or (tenant_id and job.get("tenant_id") != tenant_id):
        return None
    return job


@router.get("/{job_id}")
async def get_job_status(job_id: str, request: Request, tenant_id: Optional[str] = None):
    job = _job_for(job_id, authorized_tenant(request) or tenant_id)
    if not job:
        raise HTTPException(404, f"Job {job_id} not found")
    return {
        "job_id": job_id,
        "status": job["status"],
        "filename": job.get("filename"),
        "project": job.get("project"),
        "review": job.get("review"),
        "error": job.get("error"),
    }


@router.delete("/{job_id}")
async def delete_takeoff(job_id: str, request: Request, tenant_id: Optional[str] = None):
    """Delete a takeoff now: the uploaded plan, the job and everything read from it (POPIA —
    a data subject's or the owner's request, without waiting for the retention period)."""
    job = _job_for(job_id, authorized_tenant(request) or tenant_id)
    if not job:
        raise HTTPException(404, "Job not found")
    removed = _delete_job(job_id, job)
    return {"job_id": job_id, "deleted": True, "plan_file_removed": bool(removed)}


@router.post("/{job_id}/review")
async def review_takeoff(job_id: str, body: ReviewDecision, request: Request,
                         background_tasks: BackgroundTasks, tenant_id: Optional[str] = None):
    """Approve (optionally with corrected quantities) or reject a takeoff held at needs_review.
    Only an approved takeoff is priced; the decision, who made it, when, and any edits are kept
    on the job."""
    from datetime import datetime, timezone
    from vula.api.commerce import _actor
    job = _job_for(job_id, authorized_tenant(request) or tenant_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.get("status") != "needs_review" or not job.get("extracted"):
        raise HTTPException(409, f"This takeoff isn't waiting for review (status: {job.get('status')}).")
    decision = body.decision.strip().lower()
    if decision not in ("approve", "reject"):
        raise HTTPException(400, "decision must be approve or reject")
    by = _actor(request)
    review = dict(job.get("review") or {})
    review.update({"decided_by": by, "decided_at": datetime.now(timezone.utc).isoformat(),
                   "note": body.note[:500]})
    if decision == "reject":
        review["status"] = "rejected"
        job["review"], job["status"] = review, "rejected"
        _save_job(job_id)
        return {"job_id": job_id, "status": "rejected"}

    project = _project_from_state(job["extracted"])
    edits: Dict[str, object] = {}
    if body.use_measured and project.deterministic_rooms:
        project.rooms = [Room(name=n, area=a) for n, a in project.deterministic_rooms]
        edits["rooms"] = "measured"
    if body.rooms is not None:
        bad = [r.name for r in body.rooms if not (r.area and r.area > 0)]
        if bad:
            raise HTTPException(400, f"Every room needs an area above 0 m² ({', '.join(bad[:5])}).")
        old = {r.name: r for r in project.rooms}
        project.rooms = [Room(name=r.name, area=float(r.area),
                              floor_finish=r.floor_finish if r.floor_finish is not None else getattr(old.get(r.name), "floor_finish", ""),
                              ceiling_finish=r.ceiling_finish if r.ceiling_finish is not None else getattr(old.get(r.name), "ceiling_finish", ""),
                              wall_finish=r.wall_finish if r.wall_finish is not None else getattr(old.get(r.name), "wall_finish", ""),
                              is_wet=r.is_wet if r.is_wet is not None else bool(getattr(old.get(r.name), "is_wet", False)),
                              perimeter=getattr(old.get(r.name), "perimeter", 0.0))
                         for r in body.rooms]
        edits["rooms"] = [{"name": r.name, "area": r.area} for r in project.rooms]
    if body.gross_floor_area is not None:
        if body.gross_floor_area <= 0:
            raise HTTPException(400, "Gross floor area must be above 0 m².")
        project.gross_floor_area = float(body.gross_floor_area)
        edits["gross_floor_area"] = project.gross_floor_area
    if body.wall_height is not None:
        if not (1.8 <= body.wall_height <= 12):
            raise HTTPException(400, "Wall height must be between 1.8 m and 12 m.")
        for r in project.rooms:
            r.height = float(body.wall_height)
        edits["wall_height"] = float(body.wall_height)
    if body.external_walls_m is not None:
        if body.external_walls_m <= 0:
            raise HTTPException(400, "External wall length must be above 0 m.")
        project.external_walls_m = float(body.external_walls_m)
        edits["external_walls_m"] = project.external_walls_m
    if body.scale_confirmed:
        for s in project.sheets:
            s.scale_method = s.scale_method if s.scale_method == "dxf_units" else "dimension_check"
            s.scale_verified_by = by
        edits["scale_confirmed_by"] = by
    review.update({"status": "approved", "edits": edits})
    project.review = review
    job["review"] = review
    job["project"] = _project_summary(project)
    job["status"] = "approved"
    _save_job(job_id)

    async def _run():
        try:
            await _price(job_id, project, job.get("tenant_id") or "default", float(job.get("markup") or 15.0))
        except Exception as e:  # noqa: BLE001
            logger.error("Takeoff job %s failed after review: %s", job_id, e)
            job["status"], job["error"] = "failed", str(e)
            _save_job(job_id)
    background_tasks.add_task(_run)
    return {"job_id": job_id, "status": "approved", "edits": edits}


@router.get("/{job_id}/boq")
async def get_boq(job_id: str, request: Request, tenant_id: Optional[str] = None):
    job = _job_for(job_id, authorized_tenant(request) or tenant_id)
    if not job:
        raise HTTPException(404)
    if job["status"] not in ("complete",):
        raise HTTPException(202, detail=f"BOQ not ready yet — status: {job['status']}")
    return {"job_id": job_id, "boq": job.get("boq")}


@router.post("/{job_id}/to-project")
async def takeoff_to_project(job_id: str, body: ToProject, request: Request, tenant_id: Optional[str] = None):
    """Make a priced takeoff a project's working BOQ (vula_boq_drafts, migration 227) — one bill,
    edited, printed and used as the budget in the online BOQ, priced from the business's own rate
    library there. An existing bill is only replaced when asked."""
    import asyncio
    from vula.commerce import boq_online
    job = _job_for(job_id, authorized_tenant(request) or tenant_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.get("status") != "complete":
        raise HTTPException(409, f"The takeoff isn't priced yet (status: {job.get('status')}).")
    tenant = job.get("tenant_id") or ""
    if not tenant or tenant == "default" or not body.project.strip():
        raise HTTPException(400, "Say which business and project.")
    current = await asyncio.to_thread(boq_online.load, tenant, body.project)
    if (current or {}).get("lines") and not body.replace:
        raise HTTPException(409, "That project already has a BOQ. Send replace=true to replace it.")
    lines = boq_online.from_takeoff(job.get("boq") or {})
    from vula.api.commerce import _actor
    saved = await asyncio.to_thread(boq_online.save, tenant, body.project, lines, f"takeoff:{_actor(request)}")
    return {"job_id": job_id, "project": saved.get("project", body.project), "lines": len(lines),
            "total_cents": saved.get("total_cents")}


@router.get("/{job_id}/orders")
async def get_orders(job_id: str, request: Request, tenant_id: Optional[str] = None):
    job = _job_for(job_id, authorized_tenant(request) or tenant_id)
    if not job:
        raise HTTPException(404)
    if job["status"] != "complete":
        raise HTTPException(202, detail=f"Orders not ready — status: {job['status']}")
    return {
        "job_id": job_id,
        "orders": job.get("orders"),
        "summary": job.get("order_summary"),
    }


@router.get("/{job_id}/boq/excel")
async def download_boq_excel(job_id: str, request: Request, tenant_id: Optional[str] = None):
    """Download the BOQ as a formatted Excel workbook (.xlsx)."""
    job = _job_for(job_id, authorized_tenant(request) or tenant_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job["status"] != "complete":
        raise HTTPException(202, detail=f"BOQ not ready yet — status: {job['status']}")
    if not HAS_OPENPYXL:
        raise HTTPException(501, "openpyxl not installed on this server")
    boq = job.get("boq", {})
    buf = generate_boq_excel(boq, boq.get("project_name", job_id))
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="BOQ_{job_id}.xlsx"'},
    )


@router.post("/{job_id}/send")
async def send_rfqs(job_id: str, request: SendRFQRequest, http_request: Request, tenant_id: Optional[str] = None):
    """Send RFQs to suppliers. Set dry_run=false to actually send."""
    job = _job_for(job_id, authorized_tenant(http_request) or tenant_id)
    if not job or job["status"] != "complete":
        raise HTTPException(404, "Job not found or not complete")

    if not request.dry_run:
        # Sending RFQs reaches suppliers outside the business: it needs the confirm-before-send
        # gate every other outward message has. Not built — said plainly, not faked.
        raise HTTPException(501, "Sending RFQs to suppliers isn't available yet. Download the order "
                                 "sheet and send it yourself.")
    return {
        "job_id": job_id,
        "dry_run": True,
        "would_send": job.get("orders", {}).get("count", 0),
        "message": "Dry run: nothing was sent.",
    }


def _supplier_tenant(request: Request, tenant_id: Optional[str]) -> str:
    """The business whose suppliers these are. Required: without it a server/master caller edited
    the shared defaults every business sees."""
    t = authorized_tenant(request) or (tenant_id or "")
    if not t or t == "default":
        raise HTTPException(400, "Say which business: ?tenant_id=")
    return t


@router.get("/suppliers/list")
async def list_suppliers(request: Request, tenant_id: Optional[str] = None):
    tenant_id = _supplier_tenant(request, tenant_id)
    db = SupplierDatabase()
    suppliers = db.get_all(tenant_id)
    return {
        "suppliers": [
            {
                "id": s.id,
                "name": s.name,
                "category": s.category,
                "email": s.email,
                "whatsapp": s.whatsapp,
                "discount_pct": s.discount_pct,
                "preferred": s.preferred,
                "notes": s.notes,
            }
            for s in suppliers
        ]
    }


@router.put("/suppliers/{supplier_id}")
async def update_supplier(supplier_id: str, update: SupplierUpdate, request: Request,
                          tenant_id: Optional[str] = None):
    tenant_id = _supplier_tenant(request, tenant_id)
    db = SupplierDatabase()
    suppliers = db.get_all(tenant_id)
    supplier = next((s for s in suppliers if s.id == supplier_id), None)
    if not supplier:
        raise HTTPException(404, "Supplier not found")

    if update.name is not None:
        supplier.name = update.name
    if update.email is not None:
        supplier.email = update.email
    if update.whatsapp is not None:
        supplier.whatsapp = update.whatsapp
    if update.contact_name is not None:
        supplier.contact_name = update.contact_name
    if update.discount_pct is not None:
        supplier.discount_pct = update.discount_pct
    if update.preferred is not None:
        supplier.preferred = update.preferred
    if update.notes is not None:
        supplier.notes = update.notes

    db.upsert_supplier(supplier, tenant_id)
    return {"status": "updated", "supplier_id": supplier.id}
