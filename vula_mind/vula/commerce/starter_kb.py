"""
vula/commerce/starter_kb.py — a small, pre-structured knowledge-base scaffold seeded per
business_type when a new tenant signs up, so a brand-new tenant's KB isn't empty on day one.

Real motivation (2026-08-24, chat-accuracy audit): a large share of this session's hallucination
findings trace back to the same root cause — an empty/near-empty KB gives a skill nothing to
ground on, so a model either fabricates or (with the new guards from this same audit) declines
more often than it should have to. A tenant starting from a small set of real, editable starter
documents — mirroring the page-builder's niche templates (VulaPages.jsx's
HOME_TEMPLATE_BY_BUSINESS_TYPE — a static per-business_type scaffold, no AI generation in the
base template itself) — has less empty-context risk from the very first real conversation.

Design discipline, same as page_copy.py: never invent a specific fact (price, phone number,
address, certification, opening hours) — write a clear [placeholder] instead so the owner knows
exactly what to fill in, and write everything else (structure, tone, general content) for real.

Best-effort throughout: a failure anywhere in this module must never block tenant/account
creation — every entry point catches and logs rather than raising.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List

log = logging.getLogger(__name__)

# business_type -> which starter documents to draft. Reuses vula.api.whatsapp._DOC_CATEGORIES'
# vocabulary (extended for this feature) so starter content and real filed documents share one
# taxonomy — no separate category scheme invented.
STARTER_SLOTS: Dict[str, List[Dict[str, str]]] = {
    "food": [
        {"category": "Booking Policy",
         "topic": "Delivery & collection policy — areas covered, delivery times/days, minimum "
                  "order, collection instructions."},
        {"category": "General Document",
         "topic": "Allergen handling & food safety — how the business handles allergen "
                  "information and food safety questions from customers."},
    ],
    "retail": [
        {"category": "General Document", "topic": "Returns & exchanges policy."},
        {"category": "Supplier Agreement", "topic": "Standard terms for working with suppliers."},
    ],
    "services": [
        {"category": "Fee Proposal / Schedule",
         "topic": "Standard terms & conditions for a client engagement."},
        {"category": "Programme / Schedule",
         "topic": "Typical project process — the stages a client goes through from enquiry to "
                  "delivery."},
    ],
    "trades": [
        {"category": "Fee Proposal / Schedule",
         "topic": "Standard terms & conditions for a project."},
        {"category": "Programme / Schedule",
         "topic": "Typical project process — from quote to completion."},
    ],
    "health": [
        {"category": "Booking Policy", "topic": "Booking, cancellation, and rescheduling policy."},
        {"category": "Health & Safety Policy", "topic": "Health & safety / hygiene practices."},
    ],
    "other": [
        {"category": "General Document",
         "topic": "A short FAQ covering common questions a customer might ask this business."},
    ],
}

_SYSTEM_PROMPT = (
    "You are drafting STARTER content for a new South African small business's knowledge base. "
    "Write a short, genuinely useful document (150-300 words) a business of this type would "
    "realistically have. Write it as if the owner will read and edit it before using it — sound "
    "professional but approachable, in plain English.\n\n"
    "CRITICAL: never invent specific facts — no prices, phone numbers, addresses, staff names, "
    "certifications, delivery areas, or opening hours you weren't given. Where a specific detail "
    "would normally go, write a clear placeholder in [square brackets] instead (e.g. "
    "'[delivery areas]', '[opening hours]') so the owner knows exactly what to fill in. Write "
    "everything else — the structure, the tone, the general content — for real, not as a "
    "placeholder."
)


def _resolve_business_type(business_type: str) -> str:
    from vula.api.tenants import BUSINESS_TYPES
    return business_type if business_type in BUSINESS_TYPES else "other"


async def generate_starter_kb(tenant_id: str, business_type: str,
                              description: str = "") -> List[Dict[str, Any]]:
    """Draft a small set of starter KB documents for a new tenant, one per STARTER_SLOTS entry
    for their business_type. Never raises. Returns a list of
    {"category", "topic", "filename", "content"} — callers ingest each one via
    VulaIngestionPipeline.ingest_text(..., source_type="starter", category=...).
    """
    from vula.api.tenants import BUSINESS_TYPES
    bt = _resolve_business_type(business_type)
    slots = STARTER_SLOTS.get(bt, STARTER_SLOTS["other"])
    results: List[Dict[str, Any]] = []
    try:
        import litellm
        from core.llm_router import resolve_generation_route
        litellm.drop_params = True
        model, api_key, api_base = await resolve_generation_route(task_type="starter_kb")
    except Exception as exc:
        log.warning("starter_kb: could not resolve a generation route for %s: %s", tenant_id, exc)
        return results

    for slot in slots:
        prompt = (
            f"Business type: {BUSINESS_TYPES[bt]['label']}\n"
            + (f"About this business: {description}\n" if description else "")
            + f"\nDraft: {slot['topic']}"
        )
        try:
            resp = await litellm.acompletion(
                model=model,
                messages=[{"role": "system", "content": _SYSTEM_PROMPT},
                          {"role": "user", "content": prompt}],
                temperature=0.4, max_tokens=500, api_key=api_key, api_base=api_base,
            )
            content = (resp.choices[0].message.content or "").strip()
            if not content:
                continue
            filename = "starter_" + slot["category"].lower().replace(" ", "_").replace("/", "") + ".md"
            results.append({"category": slot["category"], "topic": slot["topic"],
                            "filename": filename, "content": content})
        except Exception as exc:
            log.debug("starter_kb slot generation skipped (%s, %s): %s", tenant_id, slot["topic"], exc)
    return results


async def seed_starter_kb(tenant_id: str, business_type: str, description: str = "") -> int:
    """Generate + ingest the starter KB for a new tenant. Best-effort, never raises — call this
    right after a new tenant's account is created; a failure here must never block signup.
    Returns how many starter documents were actually stored (0 on any failure)."""
    try:
        docs = await generate_starter_kb(tenant_id, business_type, description)
        if not docs:
            return 0
        from vula.ingestion.pipeline import VulaIngestionPipeline
        pipeline = VulaIngestionPipeline(tenant_id=tenant_id)
        stored = 0
        for doc in docs:
            doc_id = "starter_" + hashlib.md5(f"{tenant_id}:{doc['filename']}".encode()).hexdigest()[:16]
            result = await pipeline.ingest_text(
                content=doc["content"], filename=doc["filename"], doc_id=doc_id,
                source_type="starter", category=doc["category"])
            if result.status == "success":
                stored += 1
        return stored
    except Exception as exc:
        log.warning("starter_kb seeding failed for %s: %s", tenant_id, exc)
        return 0
