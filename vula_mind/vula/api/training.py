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

from vula.training.business_content import BUSINESS_TRAINING_DOCUMENTS
from vula.training.content import TRAINING_DOCUMENTS
from vula.training.seeder import (
    business_kb_status,
    seed_business_kb,
    seed_training_kb,
    training_kb_status,
)

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


# 2026-08-28: mirrors the three routes above exactly, for the separate shared general SA
# small-business corpus (vat/tax/HR/customer-service/marketing basics — see
# vula/training/business_content.py) that core/skills/commerce_admin.py's lookup_business_info
# tool falls back to when a tenant's own KB has nothing relevant.

@router.post("/training/business/seed")
async def seed_business(background_tasks: BackgroundTasks, force: bool = False) -> dict:
    """Seed the shared general SA small-business knowledge base into Qdrant.

    Safe to call multiple times — doc_ids are deterministic so existing
    chunks are overwritten in place. Runs asynchronously. Check GET
    /v1/training/business/status for progress.
    """
    async def _run():
        try:
            result = await seed_business_kb(force=force)
            logger.info(
                "Business KB seeded: %d docs, %d chunks, %d failed in %.1fs",
                result.total_documents, result.total_chunks, len(result.failed), result.duration_s,
            )
        except Exception as exc:
            logger.error("Business KB seed failed: %s", exc)

    background_tasks.add_task(_run)
    return {
        "status": "started",
        "force": force,
        "message": "Business KB seeding running in background. Check /v1/training/business/status "
                   "in a minute or two.",
    }


@router.get("/training/business/status")
async def business_status() -> dict:
    """Return the current state of the shared general SA small-business knowledge base."""
    status = await business_kb_status()
    return {
        **status,
        "expected_documents": len(BUSINESS_TRAINING_DOCUMENTS),
        "topics": [d.topic for d in BUSINESS_TRAINING_DOCUMENTS],
    }


@router.get("/training/business/topics")
async def list_business_topics() -> dict:
    """List all general-business training topics and their document names."""
    return {
        "topics": [
            {"filename": d.filename, "topic": d.topic, "chars": len(d.content)}
            for d in BUSINESS_TRAINING_DOCUMENTS
        ],
        "total": len(BUSINESS_TRAINING_DOCUMENTS),
    }
