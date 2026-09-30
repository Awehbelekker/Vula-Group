"""
vula/training/seeder.py

Seeds the shared Vula construction knowledge base into Qdrant under
tenant_id="vula_training". Idempotent — safe to run repeatedly;
existing chunks are overwritten by their deterministic doc_id.

Usage (one-off CLI):
    cd vula_mind
    python -m vula.training.seeder

Via API:
    POST /v1/training/seed   (triggers async background job)
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass
from typing import List

import httpx

from vula.ingestion.pipeline import VulaIngestionPipeline

logger = logging.getLogger(__name__)


@dataclass
class SeedResult:
    total_documents: int
    total_chunks: int
    failed: List[str]
    duration_s: float


async def seed_training_kb(force: bool = False) -> SeedResult:
    """
    Ingest all training documents into the vula_training collection.

    force=True re-ingests even if the doc already exists.
    force=False (default) skips docs that are already seeded (by doc_id).
    """
    from vula.training.content import TRAINING_DOCUMENTS, TRAINING_TENANT_ID

    started = time.time()
    pipeline = VulaIngestionPipeline(tenant_id=TRAINING_TENANT_ID)
    total_chunks = 0
    failed: List[str] = []

    logger.info("Seeding Vula training KB: %d documents", len(TRAINING_DOCUMENTS))

    for doc in TRAINING_DOCUMENTS:
        doc_id = hashlib.md5(f"{TRAINING_TENANT_ID}:{doc.filename}".encode()).hexdigest()[:16]
        try:
            result = await pipeline.ingest_text(
                content=doc.content,
                filename=doc.filename,
                doc_id=doc_id,
            )
            if result.status == "success":
                total_chunks += result.chunks_stored
                logger.info("Seeded %s → %d chunks", doc.filename, result.chunks_stored)
            else:
                logger.error("Failed to seed %s: %s", doc.filename, result.error)
                failed.append(doc.filename)
        except Exception as exc:
            logger.error("Exception seeding %s: %s", doc.filename, exc)
            failed.append(doc.filename)

    duration = round(time.time() - started, 2)
    logger.info(
        "Training KB seeding complete: %d docs, %d chunks, %d failed, %.1fs",
        len(TRAINING_DOCUMENTS), total_chunks, len(failed), duration,
    )
    return SeedResult(
        total_documents=len(TRAINING_DOCUMENTS),
        total_chunks=total_chunks,
        failed=failed,
        duration_s=duration,
    )


async def training_kb_status() -> dict:
    """Return stats on the current state of the training KB collection."""
    from config import settings
    from vula.training.content import TRAINING_TENANT_ID

    collection = f"vula_{TRAINING_TENANT_ID}"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.qdrant_base}/collections/{collection}")
            if resp.status_code == 404:
                return {"seeded": False, "chunks": 0, "collection": collection}
            data = resp.json()
            points = data.get("result", {}).get("points_count", 0)
            return {"seeded": points > 0, "chunks": points, "collection": collection}
    except Exception as exc:
        return {"seeded": False, "chunks": 0, "error": str(exc)}


async def seed_business_kb(force: bool = False) -> SeedResult:
    """Ingest all general SA small-business documents into the business_basics collection.
    Mirrors seed_training_kb exactly — a separate corpus (general SME operations, not
    construction), same idempotent doc_id shape, never touches vula_training itself."""
    from vula.training.business_content import BUSINESS_TRAINING_DOCUMENTS, BUSINESS_TRAINING_TENANT_ID

    started = time.time()
    pipeline = VulaIngestionPipeline(tenant_id=BUSINESS_TRAINING_TENANT_ID)
    total_chunks = 0
    failed: List[str] = []

    logger.info("Seeding Vula business KB: %d documents", len(BUSINESS_TRAINING_DOCUMENTS))

    for doc in BUSINESS_TRAINING_DOCUMENTS:
        doc_id = hashlib.md5(f"{BUSINESS_TRAINING_TENANT_ID}:{doc.filename}".encode()).hexdigest()[:16]
        try:
            result = await pipeline.ingest_text(
                content=doc.content,
                filename=doc.filename,
                doc_id=doc_id,
            )
            if result.status == "success":
                total_chunks += result.chunks_stored
                logger.info("Seeded %s → %d chunks", doc.filename, result.chunks_stored)
            else:
                logger.error("Failed to seed %s: %s", doc.filename, result.error)
                failed.append(doc.filename)
        except Exception as exc:
            logger.error("Exception seeding %s: %s", doc.filename, exc)
            failed.append(doc.filename)

    duration = round(time.time() - started, 2)
    logger.info(
        "Business KB seeding complete: %d docs, %d chunks, %d failed, %.1fs",
        len(BUSINESS_TRAINING_DOCUMENTS), total_chunks, len(failed), duration,
    )
    return SeedResult(
        total_documents=len(BUSINESS_TRAINING_DOCUMENTS),
        total_chunks=total_chunks,
        failed=failed,
        duration_s=duration,
    )


async def seed_documents(tenant_id: str, docs) -> SeedResult:
    """Ingest a list of TrainingDocument into one shared collection (deterministic doc_id per
    filename, so re-seeding overwrites rather than duplicates)."""
    started = time.time()
    pipeline = VulaIngestionPipeline(tenant_id=tenant_id)
    total_chunks, failed = 0, []
    for doc in docs:
        doc_id = hashlib.md5(f"{tenant_id}:{doc.filename}".encode()).hexdigest()[:16]
        try:
            result = await pipeline.ingest_text(content=doc.content, filename=doc.filename, doc_id=doc_id)
            if result.status == "success":
                total_chunks += result.chunks_stored
            else:
                failed.append(doc.filename)
        except Exception as exc:
            logger.error("Exception seeding %s/%s: %s", tenant_id, doc.filename, exc)
            failed.append(doc.filename)
    return SeedResult(total_documents=len(docs), total_chunks=total_chunks, failed=failed,
                      duration_s=round(time.time() - started, 2))


def shared_kb_fingerprint() -> str:
    """Changes whenever any shared business or sector document changes — the trigger to re-seed."""
    from vula.training.business_content import BUSINESS_TRAINING_DOCUMENTS
    from vula.training.sector_content import SECTOR_DOCUMENTS
    h = hashlib.sha256()
    for d in BUSINESS_TRAINING_DOCUMENTS:
        h.update(f"business:{d.filename}:{d.content}".encode())
    for sector in sorted(SECTOR_DOCUMENTS):
        for d in SECTOR_DOCUMENTS[sector]:
            h.update(f"{sector}:{d.filename}:{d.content}".encode())
    return h.hexdigest()[:16]


_SEED_MARK = "shared_kb_seeded"


def _seeded_marker(fingerprint: str) -> bool:
    try:
        from vula.commerce import service
        rows = (service._client().table("vula_admin_audit").select("id").eq("action", _SEED_MARK)
                .contains("detail", {"fingerprint": fingerprint}).limit(1).execute().data or [])
        return bool(rows)
    except Exception as exc:
        logger.debug("shared KB marker check failed: %s", exc)
        return False


async def ensure_shared_kbs(force: bool = False) -> dict:
    """2026-09-30: the general business KB only existed if someone pressed Seed in Master ›
    Training — nothing loaded it. On boot (and daily) this seeds the business_basics corpus and
    every sector pack when they're missing or their content changed, then records a marker so a
    restart doesn't re-embed everything."""
    from vula.training.business_content import BUSINESS_TRAINING_DOCUMENTS, BUSINESS_TRAINING_TENANT_ID
    from vula.training.sector_content import SECTOR_DOCUMENTS, sector_tenant_id
    fp = shared_kb_fingerprint()
    status = await business_kb_status()
    if not force and status.get("seeded") and _seeded_marker(fp):
        return {"seeded": False, "reason": "up to date", "fingerprint": fp}
    results = {BUSINESS_TRAINING_TENANT_ID: await seed_documents(BUSINESS_TRAINING_TENANT_ID,
                                                                 BUSINESS_TRAINING_DOCUMENTS)}
    for sector, docs in SECTOR_DOCUMENTS.items():
        if docs:
            results[sector_tenant_id(sector)] = await seed_documents(sector_tenant_id(sector), docs)
    failed = {k: r.failed for k, r in results.items() if r.failed}
    if not failed:
        try:
            from vula.commerce import service
            service._client().table("vula_admin_audit").insert({
                "actor_email": "scheduler", "action": _SEED_MARK,
                "detail": {"fingerprint": fp,
                           "chunks": {k: r.total_chunks for k, r in results.items()}}}).execute()
        except Exception as exc:
            logger.warning("shared KB marker write failed: %s", exc)
    logger.info("shared KBs seeded: %s", {k: r.total_chunks for k, r in results.items()})
    return {"seeded": True, "fingerprint": fp, "failed": failed,
            "chunks": {k: r.total_chunks for k, r in results.items()}}


async def business_kb_status() -> dict:
    """Return stats on the current state of the shared business_basics collection."""
    from config import settings
    from vula.training.business_content import BUSINESS_TRAINING_TENANT_ID

    collection = f"vula_{BUSINESS_TRAINING_TENANT_ID}"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.qdrant_base}/collections/{collection}")
            if resp.status_code == 404:
                return {"seeded": False, "chunks": 0, "collection": collection}
            data = resp.json()
            points = data.get("result", {}).get("points_count", 0)
            return {"seeded": points > 0, "chunks": points, "collection": collection}
    except Exception as exc:
        return {"seeded": False, "chunks": 0, "error": str(exc)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    async def main():
        print("\n  Vula Training KB Seeder")
        print("  Seeding SA construction knowledge base...\n")
        result = await seed_training_kb()
        print(f"  Documents: {result.total_documents}")
        print(f"  Chunks:    {result.total_chunks}")
        print(f"  Failed:    {result.failed or 'none'}")
        print(f"  Duration:  {result.duration_s}s\n")

    asyncio.run(main())
