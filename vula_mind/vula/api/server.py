"""
vula/api/server.py — Vula AI API Server

Run:
    cd vula_mind
    uvicorn vula.api.server:app --host 0.0.0.0 --port 7438 --reload

Endpoints:
    POST /ingest                        — upload + ingest a document (async)
    POST /ingest/sync                   — upload + ingest (sync, for testing)
    GET  /ingest/status/{tenant_id}     — list ingested docs + processing state
    POST /query                         — RAG question against tenant KB
    GET  /status                        — health check (Ollama + Qdrant)
    GET  /documents/{tenant_id}         — list tenant documents

    POST /v1/whatsapp/webhook           — inbound WhatsApp messages (Meta)
    GET  /v1/whatsapp/webhook           — Meta webhook verification

    POST /v1/onboard                    — register new tenant
    POST /v1/payfast/notify             — PayFast ITN payment callback

    POST /v1/chat/{tenant_id}/message   — chat with tenant KB
    GET  /v1/chat/{tenant_id}/history   — conversation history
    DELETE /v1/chat/{tenant_id}/history — clear history

    GET  /v1/training/status            — shared construction KB status
    POST /v1/training/seed              — (re)seed shared KB

    POST /v1/field/contractors                      — register/update contractor
    GET  /v1/field/contractors/{tenant_id}          — list contractors
    POST /v1/field/project/assign                   — assign contractor to project
    GET  /v1/field/project/{project_id}/team        — project team
    GET  /v1/field/project/{project_id}/status      — project status + task board
    POST /v1/field/task                             — create task
    POST /v1/field/task/assign                      — assign task + WhatsApp briefing
    POST /v1/field/task/{task_id}/complete-request  — chase contractor via WhatsApp
    GET  /v1/field/task/{task_id}                   — task detail + evidence
    POST /v1/field/walkthrough/start                — send photo checklist via WhatsApp
    POST /v1/field/walkthrough/{id}/approve         — architect sign-off
    GET  /v1/field/daily-tasks/{tenant_id}          — tasks due today (n8n cron)
    POST /v1/field/daily-tasks/{tenant_id}/dispatch — send morning briefings

    GET  /takeoff/rates                 — construction market rates
    POST /takeoff/upload                — upload drawing for BOQ takeoff
    GET  /takeoff/{job_id}/status       — job status
    GET  /takeoff/{job_id}/boq          — BOQ JSON
    GET  /takeoff/{job_id}/boq/excel    — BOQ Excel download

    POST /scrape/company    — company research
    POST /scrape/prices     — competitor price monitoring
    POST /scrape/digest     — industry news digest
    POST /scrape/tenders    — SA government tender monitoring
    POST /scrape/custom     — custom bulk scraper
"""
from __future__ import annotations

import logging
import re
import secrets
import uuid
from contextlib import asynccontextmanager
from typing import List, Optional

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Security, UploadFile, File, Form, BackgroundTasks, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from config import settings
from vula.ingestion.pipeline import VulaIngestionPipeline
from vula.skills.web_scraper import VulaWebScraper
from vula.takeoff.api import router as takeoff_router
from vula.api.onboarding import router as onboarding_router
from vula.api.whatsapp import router as whatsapp_router
from vula.api.training import router as training_router
from vula.api.chat import router as chat_router
from vula.api.field_ops import router as field_ops_router
from vula.api.commerce import router as commerce_router
from vula.api.yoco import router as yoco_router
from vula.api.whatsapp_connect import router as whatsapp_connect_router
from vula.api.yoco_connect import router as yoco_connect_router
from vula.api.draft import router as draft_router
from vula.api.agent import router as agent_router

# ─── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("vula.api")

# ─── Rate limiter ─────────────────────────────────────────────────────────────

limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])

# ─── Lifespan ─────────────────────────────────────────────────────────────────

async def _seed_training_on_boot() -> None:
    """Seed the shared construction KB once at startup if not already populated."""
    import asyncio as _asyncio
    await _asyncio.sleep(30)  # Wait for Qdrant to be ready
    try:
        from vula.training.seeder import training_kb_status, seed_training_kb
        status = await training_kb_status()
        if not status.get("seeded"):
            log.info("Training KB empty — seeding on boot...")
            result = await seed_training_kb()
            log.info("Training KB ready: %d chunks from %d docs", result.total_chunks, result.total_documents)
        else:
            log.info("Training KB already seeded: %d chunks", status.get("chunks", 0))
    except Exception as exc:
        log.warning("Training KB seed on boot failed: %s", exc)


async def _weekly_rates_loop() -> None:
    """Run construction rates scrape weekly. Starts 10 min after boot."""
    import asyncio as _asyncio
    await _asyncio.sleep(600)  # Let the server settle first
    while True:
        try:
            from vula.takeoff.construction_rates_scraper import ConstructionRatesScraper
            scraper = ConstructionRatesScraper()
            result = await scraper.run_full_update()
            log.info("Weekly rates update: %d new, %d updated in %.1fs",
                     result.new, result.updated, result.duration_s)
        except Exception as exc:
            log.warning("Weekly rates update failed: %s", exc)
        await _asyncio.sleep(7 * 24 * 3600)  # sleep one week


async def _daily_trial_expiry_loop() -> None:
    """Warn tenants whose free trial is expiring soon. Runs daily, starts 2h after boot."""
    import asyncio as _asyncio
    from datetime import datetime, timezone
    await _asyncio.sleep(7200)
    while True:
        try:
            from vula.api.onboarding import _supabase, _payfast_url
            from vula.api.email import send_trial_expiry_email
            today = datetime.now(timezone.utc).date()
            rows = await _supabase.select("vula_tenants", {"paid": "false", "status": "active"}) or []
            warned = 0
            for t in rows:
                trial_end_str = (t.get("trial_ends") or "")[:10]
                if not trial_end_str:
                    continue
                try:
                    days_left = (datetime.fromisoformat(trial_end_str).date() - today).days
                    if days_left in (7, 3, 1, 0):
                        payment_url = _payfast_url(
                            t["tenant_id"], t.get("plan", "starter"),
                            t.get("email", ""), t.get("contact_name", ""),
                        )
                        await send_trial_expiry_email(
                            to=t.get("email", ""),
                            first_name=(t.get("contact_name") or "there").split()[0],
                            company_name=t.get("company_name", ""),
                            days_left=days_left,
                            payment_url=payment_url,
                        )
                        warned += 1
                except Exception:
                    pass
            if warned:
                log.info("Trial expiry warnings sent: %d tenants", warned)
        except Exception as exc:
            log.warning("Trial expiry loop failed: %s", exc)
        await _asyncio.sleep(24 * 3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio as _asyncio
    settings.warn_missing()
    _asyncio.create_task(_seed_training_on_boot())
    _asyncio.create_task(_weekly_rates_loop())
    _asyncio.create_task(_daily_trial_expiry_loop())
    yield


# ─── App ─────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Vula AI API",
    description="Unlock your business intelligence — by Vula Group",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list(),
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=True,
)


import time as _time
_request_stats: dict = {"total": 0, "errors": 0, "latencies_ms": []}


@app.middleware("http")
async def request_middleware(request: Request, call_next):
    """Structured request logging with correlation ID, timing, and error tracking."""
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())[:8]
    request.state.request_id = request_id
    started = _time.monotonic()

    try:
        response = await call_next(request)
    except Exception as exc:
        _request_stats["errors"] += 1
        log.error("[%s] UNHANDLED %s %s — %s", request_id, request.method, request.url.path, exc)
        raise

    latency_ms = int((_time.monotonic() - started) * 1000)
    _request_stats["total"] += 1
    _request_stats["latencies_ms"].append(latency_ms)
    # Keep only last 1000 for rolling average
    if len(_request_stats["latencies_ms"]) > 1000:
        _request_stats["latencies_ms"] = _request_stats["latencies_ms"][-1000:]
    if response.status_code >= 500:
        _request_stats["errors"] += 1

    # Structured log line — parseable by log aggregators (Railway, Datadog, etc.)
    log.info(
        "req=%s method=%s path=%s status=%d latency_ms=%d",
        request_id, request.method, request.url.path,
        response.status_code, latency_ms,
    )

    response.headers["X-Request-ID"] = request_id
    response.headers["X-Latency-Ms"] = str(latency_ms)
    return response

app.include_router(takeoff_router, prefix="/takeoff")
app.include_router(onboarding_router, prefix="/v1")
app.include_router(whatsapp_router, prefix="/v1/whatsapp")
app.include_router(training_router, prefix="/v1")
app.include_router(chat_router, prefix="/v1")
app.include_router(field_ops_router, prefix="/v1/field")
app.include_router(commerce_router, prefix="/v1/commerce")
app.include_router(yoco_router, prefix="/v1/yoco")
app.include_router(yoco_connect_router, prefix="/v1/yoco")
app.include_router(whatsapp_connect_router, prefix="/v1/whatsapp")
app.include_router(draft_router, prefix="/v1")
app.include_router(agent_router, prefix="/v1")

UPLOAD_DIR = settings.upload_dir

# ─── Auth ────────────────────────────────────────────────────────────────────

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_auth(api_key: str | None = Security(_api_key_header)) -> None:
    """Require X-API-Key header when API_KEY is set in config."""
    if not settings.api_key:
        return  # no key configured — open (dev mode only)
    if not api_key or not secrets.compare_digest(api_key, settings.api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key. Set X-API-Key header.",
        )


# ─── Tenant validation ────────────────────────────────────────────────────────

_TENANT_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")


def validate_tenant(tenant_id: str) -> str:
    if not _TENANT_RE.match(tenant_id):
        raise HTTPException(
            status_code=422,
            detail="tenant_id must be 1–64 alphanumeric characters, underscores, or hyphens.",
        )
    return tenant_id


# ─── Request / Response Models ────────────────────────────────────────────────

class QueryRequest(BaseModel):
    tenant_id: str
    question: str
    top_k: int = 5

    @field_validator("tenant_id")
    @classmethod
    def _check_tenant(cls, v: str) -> str:
        return validate_tenant(v)

    @field_validator("question")
    @classmethod
    def _check_question(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("question cannot be empty")
        return v[:2000]  # cap length


class QueryResponse(BaseModel):
    answer: str
    sources: List[dict]
    tenant_id: str


class CompanyResearchRequest(BaseModel):
    tenant_id: str
    url: str


class PriceMonitorRequest(BaseModel):
    tenant_id: str
    urls: List[str]


class DigestRequest(BaseModel):
    tenant_id: str
    topic: str
    sources: Optional[List[str]] = None


class TenderRequest(BaseModel):
    tenant_id: str
    keywords: List[str]


class ScrapeRequest(BaseModel):
    tenant_id: str
    urls: List[str]
    extract_prompt: str


# ─── Document Ingestion ───────────────────────────────────────────────────────

@app.post("/ingest", dependencies=[Depends(require_auth)])
@limiter.limit("20/minute")
async def ingest_document(
    request: Request,
    background_tasks: BackgroundTasks,
    tenant_id: str = Form(...),
    file: UploadFile = File(...),
):
    """Upload a document and ingest it into the tenant knowledge base (async)."""
    validate_tenant(tenant_id)

    content = await file.read()
    size_mb = len(content) / (1024 * 1024)
    if size_mb > settings.max_file_mb:
        raise HTTPException(status_code=413, detail=f"File exceeds {settings.max_file_mb}MB limit")

    tenant_dir = UPLOAD_DIR / tenant_id
    tenant_dir.mkdir(parents=True, exist_ok=True)
    file_path = tenant_dir / file.filename
    file_path.write_bytes(content)

    async def _ingest() -> None:
        pipeline = VulaIngestionPipeline(tenant_id=tenant_id)
        result = await pipeline.ingest_file(file_path)
        log.info("Ingestion complete: %s → %d chunks (%s)", result.filename, result.chunks_stored, result.status)

    background_tasks.add_task(_ingest)

    return {
        "status": "queued",
        "filename": file.filename,
        "tenant_id": tenant_id,
        "size_mb": round(size_mb, 2),
        "message": f"'{file.filename}' is processing. Ready in 2–5 minutes.",
    }


@app.post("/ingest/sync", dependencies=[Depends(require_auth)])
async def ingest_document_sync(
    tenant_id: str = Form(...),
    file: UploadFile = File(...),
):
    """Synchronous ingestion — waits for completion. Use for testing."""
    validate_tenant(tenant_id)
    content = await file.read()
    tenant_dir = UPLOAD_DIR / tenant_id
    tenant_dir.mkdir(parents=True, exist_ok=True)
    file_path = tenant_dir / file.filename
    file_path.write_bytes(content)

    pipeline = VulaIngestionPipeline(tenant_id=tenant_id)
    result = await pipeline.ingest_file(file_path)

    return {
        "status": result.status,
        "filename": result.filename,
        "pages_processed": result.pages_processed,
        "chunks_stored": result.chunks_stored,
        "processing_time_s": result.processing_time_s,
        "error": result.error,
    }


# ─── Query / RAG ─────────────────────────────────────────────────────────────

@app.post("/query", response_model=QueryResponse, dependencies=[Depends(require_auth)])
@limiter.limit("30/minute")
async def query_knowledge_base(request: Request, body: QueryRequest):
    """Ask a question against the tenant's ingested documents."""
    try:
        pipeline = VulaIngestionPipeline(tenant_id=body.tenant_id)
        sources = await pipeline.query(body.question, top_k=body.top_k)

        if not sources:
            return QueryResponse(
                answer="I don't have enough information in your documents yet. Try uploading more.",
                sources=[],
                tenant_id=body.tenant_id,
            )

        answer = await pipeline.answer(body.question)
        return QueryResponse(
            answer=answer,
            sources=[
                {"filename": s.get("filename"), "page": s.get("page_num"), "excerpt": s.get("text", "")[:200]}
                for s in sources
            ],
            tenant_id=body.tenant_id,
        )
    except Exception as exc:
        log.exception("Query failed for tenant %s: %s", body.tenant_id, exc)
        raise HTTPException(status_code=500, detail=f"Query failed: {type(exc).__name__}: {exc}")


# ─── Web Scraper Endpoints ────────────────────────────────────────────────────

@app.post("/scrape/company", dependencies=[Depends(require_auth)])
@limiter.limit("10/minute")
async def research_company(request: Request, body: CompanyResearchRequest):
    scraper = VulaWebScraper(tenant_id=body.tenant_id)
    profile = await scraper.research_company(body.url)
    return {
        "url": profile.url, "name": profile.name, "description": profile.description,
        "services": profile.services, "contact_info": profile.contact_info,
        "social_links": profile.social_links, "key_facts": profile.key_facts,
        "pitch_angle": profile.suggested_pitch_angle,
    }


@app.post("/scrape/prices", dependencies=[Depends(require_auth)])
@limiter.limit("10/minute")
async def monitor_prices(request: Request, body: PriceMonitorRequest):
    scraper = VulaWebScraper(tenant_id=body.tenant_id)
    results = await scraper.monitor_prices(body.urls)
    return {
        "monitored": len(results),
        "results": [{"url": r.url, "products": r.products, "scraped_at": r.scraped_at, "changes": r.changes_detected} for r in results],
    }


@app.post("/scrape/digest", dependencies=[Depends(require_auth)])
@limiter.limit("10/minute")
async def news_digest(request: Request, body: DigestRequest):
    scraper = VulaWebScraper(tenant_id=body.tenant_id)
    digest = await scraper.industry_digest(body.topic, body.sources)
    return {"topic": digest.topic, "generated_at": digest.generated_at, "key_trends": digest.key_trends, "articles": digest.articles}


@app.post("/scrape/tenders", dependencies=[Depends(require_auth)])
@limiter.limit("10/minute")
async def monitor_tenders(request: Request, body: TenderRequest):
    scraper = VulaWebScraper(tenant_id=body.tenant_id)
    tenders = await scraper.monitor_tenders(body.keywords)
    return {"keywords": body.keywords, "tenders_found": len(tenders), "tenders": tenders}


@app.post("/scrape/custom", dependencies=[Depends(require_auth)])
@limiter.limit("10/minute")
async def custom_scrape(request: Request, body: ScrapeRequest):
    scraper = VulaWebScraper(tenant_id=body.tenant_id)
    results = await scraper.scrape_urls(body.urls, body.extract_prompt)
    return {
        "scraped": len(results),
        "results": [{"url": r.url, "title": r.title, "status": r.status, "data": r.extracted_data, "time_s": r.scrape_time_s} for r in results],
    }


# ─── Health & Status ──────────────────────────────────────────────────────────

@app.get("/status")
async def health_check():
    """Public health check — no auth required."""
    checks: dict = {}

    async with httpx.AsyncClient(timeout=3.0) as client:
        try:
            resp = await client.get(f"{settings.ollama_base}/api/tags")
            models = [m["name"] for m in resp.json().get("models", [])]
            checks["ollama"] = {"status": "ok", "models": models}
        except Exception as exc:
            checks["ollama"] = {"status": "error", "detail": str(exc)}

        try:
            qdrant_headers = {"api-key": settings.qdrant_api_key} if settings.qdrant_api_key else {}
            resp = await client.get(f"{settings.qdrant_base}/collections", headers=qdrant_headers)
            n = len(resp.json().get("result", {}).get("collections", []))
            checks["qdrant"] = {"status": "ok", "collections": n}
        except Exception as exc:
            checks["qdrant"] = {"status": "error", "detail": str(exc)}

        # Ollama on Railway routes to OpenRouter — check accordingly
        if checks.get("ollama", {}).get("status") == "error" and settings.openrouter_api_key:
            checks["ollama"] = {"status": "ok", "models": ["openrouter/cloud"], "note": "via OpenRouter"}

    overall = "ok" if all(c["status"] == "ok" for c in checks.values()) else "degraded"
    return {"status": overall, "service": "vula-api", "version": "1.0.0", "checks": checks}


@app.get("/metrics", dependencies=[Depends(require_auth)])
async def metrics():
    """Request performance metrics — total, errors, rolling average latency."""
    lats = _request_stats["latencies_ms"]
    avg_lat = int(sum(lats) / len(lats)) if lats else 0
    p95_lat = sorted(lats)[int(len(lats) * 0.95)] if lats else 0
    try:
        from core.memory.reflection import ReflectionAgent
        reflection_stats = ReflectionAgent().get_stats()
    except Exception:
        reflection_stats = {}
    return {
        "requests": {
            "total": _request_stats["total"],
            "errors": _request_stats["errors"],
            "error_rate": round(_request_stats["errors"] / max(_request_stats["total"], 1), 3),
            "avg_latency_ms": avg_lat,
            "p95_latency_ms": p95_lat,
        },
        "agent": reflection_stats,
        "service": "vula-api",
    }


@app.get("/ingest/status/{tenant_id}", dependencies=[Depends(require_auth)])
async def ingestion_status(tenant_id: str):
    """Return document ingestion status for a tenant."""
    validate_tenant(tenant_id)
    from vula.ingestion.pipeline import get_tracker
    docs = get_tracker().get_all(tenant_id)
    return {"tenant_id": tenant_id, "documents": docs, "count": len(docs)}


@app.get("/documents/{tenant_id}", dependencies=[Depends(require_auth)])
async def list_documents(tenant_id: str):
    validate_tenant(tenant_id)

    # Files physically uploaded to this Railway instance
    docs = []
    tenant_dir = UPLOAD_DIR / tenant_id
    if tenant_dir.exists():
        docs = [
            {"filename": f.name, "size_kb": f.stat().st_size // 1024, "type": f.suffix}
            for f in sorted(tenant_dir.iterdir())
            if f.is_file()
        ]

    # True KB size = vector count in the tenant's Qdrant collection.
    # This reflects all ingested knowledge even if files were ingested elsewhere.
    kb_chunks = 0
    try:
        collection = f"vula_{tenant_id.replace('-', '_')}"
        qdrant_headers = {"api-key": settings.qdrant_api_key} if settings.qdrant_api_key else {}
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{settings.qdrant_base}/collections/{collection}",
                headers=qdrant_headers,
            )
            if resp.status_code == 200:
                kb_chunks = resp.json().get("result", {}).get("points_count", 0) or 0
    except Exception as exc:
        log.debug("Qdrant chunk count failed for %s: %s", tenant_id, exc)

    return {
        "tenant_id": tenant_id,
        "documents": docs,
        "count": len(docs),
        "kb_chunks": kb_chunks,
    }


# ─── Entry Point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    print("\n  Vula AI Server — by Vula Group")
    print(f"  http://{settings.api_host}:{settings.api_port}\n")
    uvicorn.run(
        "vula.api.server:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.debug,
    )
