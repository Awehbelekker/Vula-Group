"""
vula/api/server.py

Vula API Server

HTTP API that connects:
- Document ingestion pipeline
- Web scraper skill
- RAG query answering
- TwoSoul mobile thin client

Run: uvicorn vula.api.server:app --reload --port 7438

Endpoints:
    POST /ingest          — upload and ingest a document
    POST /query           — ask a question against tenant's knowledge base
    POST /scrape/company  — research a company URL
    POST /scrape/prices   — monitor competitor prices
    POST /scrape/digest   — daily news digest
    POST /scrape/tenders  — SA tender monitoring
    GET  /status          — health check
    GET  /docs/{tenant_id} — list ingested documents
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from vula.ingestion.pipeline import VulaIngestionPipeline, IngestionResult
from vula.skills.web_scraper import VulaWebScraper

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Vula AI API",
    description="Unlock your business intelligence — by Aweh Be Lekker",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path("/tmp/vula_uploads")
UPLOAD_DIR.mkdir(exist_ok=True)


# ─── Request / Response Models ────────────────────────────────────────────────

class QueryRequest(BaseModel):
    tenant_id: str
    question: str
    top_k: int = 5

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

@app.post("/ingest", response_model=dict)
async def ingest_document(
    background_tasks: BackgroundTasks,
    tenant_id: str = Form(...),
    file: UploadFile = File(...),
):
    """
    Upload a document and ingest it into the tenant's knowledge base.
    Returns immediately — ingestion runs in background.
    """
    # Validate file size
    content = await file.read()
    size_mb = len(content) / (1024 * 1024)
    if size_mb > 50:
        raise HTTPException(status_code=413, detail="File exceeds 50MB limit")

    # Save to temp dir
    tenant_dir = UPLOAD_DIR / tenant_id
    tenant_dir.mkdir(exist_ok=True)
    file_path = tenant_dir / file.filename
    file_path.write_bytes(content)

    # Run ingestion in background
    async def _ingest():
        pipeline = VulaIngestionPipeline(tenant_id=tenant_id)
        result = await pipeline.ingest_file(file_path)
        logger.info(f"Background ingestion complete: {result.filename} → {result.chunks_stored} chunks")

    background_tasks.add_task(_ingest)

    return {
        "status": "queued",
        "filename": file.filename,
        "tenant_id": tenant_id,
        "size_mb": round(size_mb, 2),
        "message": f"'{file.filename}' is being processed. Your AI will be ready to answer questions about it in 2–5 minutes.",
    }


@app.post("/ingest/sync", response_model=dict)
async def ingest_document_sync(
    tenant_id: str = Form(...),
    file: UploadFile = File(...),
):
    """Synchronous ingestion — waits for completion. Use for testing."""
    content = await file.read()
    tenant_dir = UPLOAD_DIR / tenant_id
    tenant_dir.mkdir(exist_ok=True)
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

@app.post("/query", response_model=QueryResponse)
async def query_knowledge_base(request: QueryRequest):
    """
    Ask a question against the tenant's ingested documents.
    Returns a grounded answer with sources.
    """
    pipeline = VulaIngestionPipeline(tenant_id=request.tenant_id)

    # Get relevant chunks for sources
    sources = await pipeline.query(request.question, top_k=request.top_k)

    if not sources:
        return QueryResponse(
            answer="I don't have enough information in your business documents to answer that yet. Try uploading more documents.",
            sources=[],
            tenant_id=request.tenant_id,
        )

    # Generate grounded answer
    answer = await pipeline.answer(request.question)

    return QueryResponse(
        answer=answer,
        sources=[{"filename": s.get("filename"), "page": s.get("page_num"), "excerpt": s.get("text", "")[:200]} for s in sources],
        tenant_id=request.tenant_id,
    )


# ─── Web Scraper Endpoints ────────────────────────────────────────────────────

@app.post("/scrape/company")
async def research_company(request: CompanyResearchRequest):
    """Research a company from their website before a sales call."""
    scraper = VulaWebScraper(tenant_id=request.tenant_id)
    profile = await scraper.research_company(request.url)
    return {
        "url": profile.url,
        "name": profile.name,
        "description": profile.description,
        "services": profile.services,
        "contact_info": profile.contact_info,
        "social_links": profile.social_links,
        "key_facts": profile.key_facts,
        "pitch_angle": profile.suggested_pitch_angle,
    }


@app.post("/scrape/prices")
async def monitor_prices(request: PriceMonitorRequest):
    """Monitor competitor or supplier prices."""
    scraper = VulaWebScraper(tenant_id=request.tenant_id)
    results = await scraper.monitor_prices(request.urls)
    return {
        "monitored": len(results),
        "results": [
            {
                "url": r.url,
                "products": r.products,
                "scraped_at": r.scraped_at,
                "changes": r.changes_detected,
            }
            for r in results
        ],
    }


@app.post("/scrape/digest")
async def news_digest(request: DigestRequest):
    """Generate a daily news digest for an industry topic."""
    scraper = VulaWebScraper(tenant_id=request.tenant_id)
    digest = await scraper.industry_digest(request.topic, request.sources)
    return {
        "topic": digest.topic,
        "generated_at": digest.generated_at,
        "key_trends": digest.key_trends,
        "articles": digest.articles,
    }


@app.post("/scrape/tenders")
async def monitor_tenders(request: TenderRequest):
    """Monitor South African government tenders."""
    scraper = VulaWebScraper(tenant_id=request.tenant_id)
    tenders = await scraper.monitor_tenders(request.keywords)
    return {"keywords": request.keywords, "tenders_found": len(tenders), "tenders": tenders}


@app.post("/scrape/custom")
async def custom_scrape(request: ScrapeRequest):
    """Generic bulk URL scraper with custom extraction prompt."""
    scraper = VulaWebScraper(tenant_id=request.tenant_id)
    results = await scraper.scrape_urls(request.urls, request.extract_prompt)
    return {
        "scraped": len(results),
        "results": [
            {
                "url": r.url,
                "title": r.title,
                "status": r.status,
                "data": r.extracted_data,
                "time_s": r.scrape_time_s,
            }
            for r in results
        ],
    }


# ─── Health & Status ──────────────────────────────────────────────────────────

@app.get("/status")
async def status():
    """Health check — also verifies Ollama and Qdrant connectivity."""
    checks = {}

    # Ollama check
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get("http://localhost:11434/api/tags")
            models = [m["name"] for m in resp.json().get("models", [])]
            checks["ollama"] = {"status": "ok", "models": models}
    except Exception as e:
        checks["ollama"] = {"status": "error", "detail": str(e)}

    # Qdrant check
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get("http://localhost:6333/collections")
            checks["qdrant"] = {"status": "ok", "collections": len(resp.json().get("result", {}).get("collections", []))}
    except Exception as e:
        checks["qdrant"] = {"status": "error", "detail": str(e)}

    overall = "ok" if all(c.get("status") == "ok" for c in checks.values()) else "degraded"
    return {"status": overall, "service": "vula-api", "version": "1.0.0", "checks": checks}


@app.get("/docs/{tenant_id}")
async def list_documents(tenant_id: str):
    """List documents uploaded by a tenant."""
    tenant_dir = UPLOAD_DIR / tenant_id
    if not tenant_dir.exists():
        return {"tenant_id": tenant_id, "documents": [], "count": 0}

    docs = [
        {
            "filename": f.name,
            "size_kb": f.stat().st_size // 1024,
            "type": f.suffix,
        }
        for f in tenant_dir.iterdir()
        if f.is_file()
    ]
    return {"tenant_id": tenant_id, "documents": docs, "count": len(docs)}


# ─── Import for status endpoint ───────────────────────────────────────────────
import httpx

if __name__ == "__main__":
    import uvicorn
    print("\n🌿 Vula AI Server")
    print("   Unlock your business — by Aweh Be Lekker")
    print("   http://localhost:7438\n")
    uvicorn.run(app, host="0.0.0.0", port=7438, reload=True)
