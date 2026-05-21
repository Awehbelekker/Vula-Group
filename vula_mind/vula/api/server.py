"""
vula/api/server.py — Vula AI API Server

Run:
    cd vula_mind
    uvicorn vula.api.server:app --host 0.0.0.0 --port 7438 --reload

Endpoints:
    POST /ingest            — upload + ingest a document (async)
    POST /ingest/sync       — upload + ingest (sync, for testing)
    POST /query             — RAG question against tenant knowledge base
    POST /scrape/company    — company research
    POST /scrape/prices     — competitor price monitoring
    POST /scrape/digest     — industry news digest
    POST /scrape/tenders    — SA government tender monitoring
    POST /scrape/custom     — custom bulk scraper
    GET  /status            — health check (Ollama + Qdrant)
    GET  /documents/{tenant_id} — list tenant documents
"""
from __future__ import annotations

import logging
import re
import secrets
from typing import List, Optional

import httpx
from fastapi import Depends, FastAPI, HTTPException, Security, UploadFile, File, Form, BackgroundTasks, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel, field_validator

from config import settings
from vula.ingestion.pipeline import VulaIngestionPipeline
from vula.skills.web_scraper import VulaWebScraper
from vula.takeoff.api import router as takeoff_router

# ─── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("vula.api")

# ─── App ─────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Vula AI API",
    description="Unlock your business intelligence — by Vula Group",
    version="1.0.0",
    docs_url="/docs" if settings.debug else None,   # hide Swagger in production
    redoc_url="/redoc" if settings.debug else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list(),
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=True,
)

app.include_router(takeoff_router, prefix="/takeoff")

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
async def ingest_document(
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
async def query_knowledge_base(request: QueryRequest):
    """Ask a question against the tenant's ingested documents."""
    pipeline = VulaIngestionPipeline(tenant_id=request.tenant_id)
    sources = await pipeline.query(request.question, top_k=request.top_k)

    if not sources:
        return QueryResponse(
            answer="I don't have enough information in your documents yet. Try uploading more.",
            sources=[],
            tenant_id=request.tenant_id,
        )

    answer = await pipeline.answer(request.question)
    return QueryResponse(
        answer=answer,
        sources=[
            {"filename": s.get("filename"), "page": s.get("page_num"), "excerpt": s.get("text", "")[:200]}
            for s in sources
        ],
        tenant_id=request.tenant_id,
    )


# ─── Web Scraper Endpoints ────────────────────────────────────────────────────

@app.post("/scrape/company", dependencies=[Depends(require_auth)])
async def research_company(request: CompanyResearchRequest):
    scraper = VulaWebScraper(tenant_id=request.tenant_id)
    profile = await scraper.research_company(request.url)
    return {
        "url": profile.url, "name": profile.name, "description": profile.description,
        "services": profile.services, "contact_info": profile.contact_info,
        "social_links": profile.social_links, "key_facts": profile.key_facts,
        "pitch_angle": profile.suggested_pitch_angle,
    }


@app.post("/scrape/prices", dependencies=[Depends(require_auth)])
async def monitor_prices(request: PriceMonitorRequest):
    scraper = VulaWebScraper(tenant_id=request.tenant_id)
    results = await scraper.monitor_prices(request.urls)
    return {
        "monitored": len(results),
        "results": [{"url": r.url, "products": r.products, "scraped_at": r.scraped_at, "changes": r.changes_detected} for r in results],
    }


@app.post("/scrape/digest", dependencies=[Depends(require_auth)])
async def news_digest(request: DigestRequest):
    scraper = VulaWebScraper(tenant_id=request.tenant_id)
    digest = await scraper.industry_digest(request.topic, request.sources)
    return {"topic": digest.topic, "generated_at": digest.generated_at, "key_trends": digest.key_trends, "articles": digest.articles}


@app.post("/scrape/tenders", dependencies=[Depends(require_auth)])
async def monitor_tenders(request: TenderRequest):
    scraper = VulaWebScraper(tenant_id=request.tenant_id)
    tenders = await scraper.monitor_tenders(request.keywords)
    return {"keywords": request.keywords, "tenders_found": len(tenders), "tenders": tenders}


@app.post("/scrape/custom", dependencies=[Depends(require_auth)])
async def custom_scrape(request: ScrapeRequest):
    scraper = VulaWebScraper(tenant_id=request.tenant_id)
    results = await scraper.scrape_urls(request.urls, request.extract_prompt)
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
            resp = await client.get(f"{settings.qdrant_base}/collections")
            n = len(resp.json().get("result", {}).get("collections", []))
            checks["qdrant"] = {"status": "ok", "collections": n}
        except Exception as exc:
            checks["qdrant"] = {"status": "error", "detail": str(exc)}

    overall = "ok" if all(c["status"] == "ok" for c in checks.values()) else "degraded"
    return {"status": overall, "service": "vula-api", "version": "1.0.0", "checks": checks}


@app.get("/documents/{tenant_id}", dependencies=[Depends(require_auth)])
async def list_documents(tenant_id: str):
    validate_tenant(tenant_id)
    tenant_dir = UPLOAD_DIR / tenant_id
    if not tenant_dir.exists():
        return {"tenant_id": tenant_id, "documents": [], "count": 0}

    docs = [
        {"filename": f.name, "size_kb": f.stat().st_size // 1024, "type": f.suffix}
        for f in sorted(tenant_dir.iterdir())
        if f.is_file()
    ]
    return {"tenant_id": tenant_id, "documents": docs, "count": len(docs)}


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
