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
