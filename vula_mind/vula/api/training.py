"""
vula/api/training.py

Vula Training KB API — manage the shared SA construction knowledge base.

Endpoints:
    POST /v1/training/seed    — seed (or re-seed) the training KB
    GET  /v1/training/status  — check seeding status + chunk count
    GET  /v1/training/topics  — list training topics available
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks

from vula.training.content import TRAINING_DOCUMENTS
from vula.training.seeder import seed_training_kb, training_kb_status

logger = logging.getLogger(__name__)

router = APIRouter(tags=["training"])


@router.post("/training/seed")
async def seed_training(background_tasks: BackgroundTasks, force: bool = False) -> dict:
    """Seed the shared Vula construction knowledge base into Qdrant.

    Safe to call multiple times — doc_ids are deterministic so existing
    chunks are overwritten in place.  Set ?force=true to force full re-seed.
    Runs asynchronously. Check GET /v1/training/status for progress.
    """
    async def _run():
        try:
            result = await seed_training_kb(force=force)
            logger.info(
                "Training KB seeded: %d docs, %d chunks, %d failed in %.1fs",
                result.total_documents, result.total_chunks, len(result.failed), result.duration_s,
            )
        except Exception as exc:
            logger.error("Training KB seed failed: %s", exc)

    background_tasks.add_task(_run)
    return {
        "status": "started",
        "force": force,
        "message": "Training KB seeding running in background. Check /v1/training/status in 2–5 minutes.",
    }


@router.get("/training/status")
async def training_status() -> dict:
    """Return the current state of the shared construction knowledge base."""
    status = await training_kb_status()
    return {
        **status,
        "expected_documents": len(TRAINING_DOCUMENTS),
        "topics": [d.topic for d in TRAINING_DOCUMENTS],
    }


@router.get("/training/topics")
async def list_topics() -> dict:
    """List all training topics and their document names."""
    return {
        "topics": [
            {"filename": d.filename, "topic": d.topic, "chars": len(d.content)}
            for d in TRAINING_DOCUMENTS
        ],
        "total": len(TRAINING_DOCUMENTS),
    }
