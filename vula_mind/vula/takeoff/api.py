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
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from config import settings
from vula.takeoff.plan_reader import PlanReader
from vula.takeoff.boq_generator import BOQGenerator
from vula.takeoff.order_manager import OrderManager, SupplierDatabase
from vula.takeoff.construction_rates_scraper import ConstructionRatesScraper, RatesDatabase
from vula.takeoff.boq_export import generate_boq_excel, HAS_OPENPYXL

logger = logging.getLogger(__name__)

router = APIRouter(tags=["takeoff"])

UPLOAD_DIR = settings.takeoff_upload_dir
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# In-memory job store (replace with Redis in production)
_jobs: Dict[str, dict] = {}


# ─── Models ──────────────────────────────────────────────────────────────────

class SupplierUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    whatsapp: Optional[str] = None
    contact_name: Optional[str] = None
    discount_pct: Optional[float] = None
    preferred: Optional[bool] = None
    notes: Optional[str] = None


class SendRFQRequest(BaseModel):
    dry_run: bool = True
    selected_trades: Optional[List[str]] = None


# ─── Background job ───────────────────────────────────────────────────────────

async def _process_plans(job_id: str, file_path: Path, tenant_id: str, markup: float):
    """Full pipeline: read → BOQ → orders. Runs in background."""
    try:
        _jobs[job_id]["status"] = "reading_plans"
        reader = PlanReader(tenant_id=tenant_id)
        project = await reader.read(file_path)
        _jobs[job_id]["project"] = {
            "title": project.title_block.project_name,
            "sheets": len(project.sheets),
            "rooms": len(project.rooms),
            "gfa": project.gross_floor_area,
            "confidence": round(project.confidence_overall * 100, 1),
        }

        _jobs[job_id]["status"] = "generating_boq"
        generator = BOQGenerator(project, tenant_id=tenant_id, markup=markup)
        boq = await generator.generate()
        _jobs[job_id]["boq"] = boq.to_dict()

        _jobs[job_id]["status"] = "building_orders"
        order_mgr = OrderManager(boq, tenant_id=tenant_id)
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

    except Exception as e:
        logger.error(f"Takeoff job {job_id} failed: {e}")
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = str(e)


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
    return {"count": len(rates), "rates": rates}


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
    file_path = UPLOAD_DIR / f"{job_id}_{file.filename}"
    file_path.write_bytes(content)

    _jobs[job_id] = {
        "status": "queued",
        "filename": file.filename,
        "tenant_id": tenant_id,
        "markup": markup,
        "file_path": str(file_path),
    }

    background_tasks.add_task(_process_plans, job_id, file_path, tenant_id, markup)

    return {
        "job_id": job_id,
        "filename": file.filename,
        "status": "queued",
        "message": f"Plans uploaded. Processing takes 2–5 minutes. Poll /takeoff/{job_id} for status.",
    }


@router.get("/{job_id}")
async def get_job_status(job_id: str):
    if job_id not in _jobs:
        raise HTTPException(404, f"Job {job_id} not found")
    job = _jobs[job_id]
    return {
        "job_id": job_id,
        "status": job["status"],
        "filename": job.get("filename"),
        "project": job.get("project"),
        "error": job.get("error"),
    }


@router.get("/{job_id}/boq")
async def get_boq(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404)
    if job["status"] not in ("complete",):
        raise HTTPException(202, detail=f"BOQ not ready yet — status: {job['status']}")
    return {"job_id": job_id, "boq": job.get("boq")}


@router.get("/{job_id}/orders")
async def get_orders(job_id: str):
    job = _jobs.get(job_id)
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
async def download_boq_excel(job_id: str):
    """Download the BOQ as a formatted Excel workbook (.xlsx)."""
    job = _jobs.get(job_id)
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
async def send_rfqs(job_id: str, request: SendRFQRequest):
    """Send RFQs to suppliers. Set dry_run=false to actually send."""
    job = _jobs.get(job_id)
    if not job or job["status"] != "complete":
        raise HTTPException(404, "Job not found or not complete")

    # Reconstruct from stored data (in production: load from DB)
    return {
        "job_id": job_id,
        "dry_run": request.dry_run,
        "message": "In production, this sends emails/WhatsApp to all suppliers in the order package.",
        "orders_count": job.get("orders", {}).get("count", 0),
    }


@router.get("/suppliers/list")
async def list_suppliers(tenant_id: str = "default"):
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
async def update_supplier(supplier_id: str, update: SupplierUpdate, tenant_id: str = "default"):
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
    return {"status": "updated", "supplier_id": supplier_id}
