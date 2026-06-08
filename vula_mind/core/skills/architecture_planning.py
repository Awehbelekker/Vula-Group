"""
core/skills/architecture_planning.py

Construction & architecture planning skill — uses the advanced reasoning
model (DeepSeek R1 14B) with deep RAG context from tenant KB + shared
construction training KB.

Specialised for: SACAP stages, JBCC contract advice, NHBRC requirements,
SANS 10400 compliance, BOQ planning, programme planning, design strategy.
"""
from __future__ import annotations

import logging
import re

from config import settings
from core.llm_router import resolve_generation_route
from core.skills.base import BaseSkill, SkillInput, SkillOutput

logger = logging.getLogger(__name__)


class ArchitecturePlanningSkill(BaseSkill):
    name = "architecture_planning"
    description = "South African architecture and construction planning — SACAP, JBCC, NHBRC, SANS 10400, BOQ"

    async def run(self, inp: SkillInput) -> SkillOutput:
        # Retrieve from BOTH tenant KB and shared construction KB
        all_sources = []
        contexts: list[str] = []

        try:
            from vula.ingestion.pipeline import VulaIngestionPipeline
            from vula.training.content import TRAINING_TENANT_ID

            # Tenant's own context — past projects, fee schedules
            tenant_pipeline = VulaIngestionPipeline(tenant_id=inp.tenant_id)
            tenant_chunks = await tenant_pipeline.query(inp.question, top_k=inp.top_k)
            strong_tenant_hit = bool(tenant_chunks and tenant_chunks[0].get("score", 0) > 0.55)
            if tenant_chunks:
                contexts.append("## Your practice's knowledge\n" + "\n\n".join(
                    f"[{c.get('filename','doc')}]: {c.get('text','')[:500]}"
                    for c in tenant_chunks
                ))
                all_sources.extend([
                    {"type": "tenant_kb", "filename": c.get("filename", "?"),
                     "score": round(c.get("score", 0.0), 3)}
                    for c in tenant_chunks
                ])

            # Shared SA construction standards — only query when the tenant KB
            # didn't already give a strong hit (saves a tunnel round-trip).
            try:
                if not strong_tenant_hit:
                    training_pipeline = VulaIngestionPipeline(tenant_id=TRAINING_TENANT_ID)
                    training_chunks = await training_pipeline.query(inp.question, top_k=inp.top_k)
                else:
                    training_chunks = []
                if training_chunks:
                    contexts.append("## SA construction standards & rates\n" + "\n\n".join(
                        f"[{c.get('filename','standard')}]: {c.get('text','')[:400]}"
                        for c in training_chunks
                    ))
                    all_sources.extend([
                        {"type": "training_kb", "filename": c.get("filename", "?"),
                         "score": round(c.get("score", 0.0), 3)}
                        for c in training_chunks
                    ])
            except Exception as exc:
                logger.debug("Training KB retrieval skipped: %s", exc)

        except Exception as exc:
            logger.warning("Architecture skill KB retrieval failed: %s", exc)

        # Use the reasoner model for this complex domain
        system_msg = (
            "You are Vula, a senior South African architect and quantity surveyor advisor. "
            "Answer using SA construction standards: SACAP fee guidelines, JBCC and NEC contracts, "
            "NHBRC requirements, SANS 10400 building regulations, ASAQS measurement standards, "
            "CIDB grading, and current AECOM construction rates.\n\n"
            "Style: practical, specific, cite the relevant standard or clause where useful. "
            "All money in ZAR. Dates in DD Month YYYY. Phone numbers as 0XX XXX XXXX. "
            "If the question is outside SA construction practice, say so clearly."
        )
        context_block = "\n\n---\n\n".join(contexts) if contexts else ""
        user_msg = (
            f"{('Context:' + chr(10) + context_block + chr(10) + chr(10)) if context_block else ''}"
            f"Question: {inp.question}\n\nAnswer:"
        )

        try:
            import litellm
            litellm.drop_params = True

            # Use the worker model — on cloud (OpenRouter) the reasoner tier is
            # far more expensive and generates wasteful think-tokens. The worker
            # (llama-3.3-70b) is plenty capable for SA construction Q&A.
            model, api_key, api_base = await resolve_generation_route(model=settings.model_worker)

            resp = await litellm.acompletion(
                model=model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.2,
                max_tokens=inp.max_tokens,
                api_key=api_key,
                api_base=api_base,
            )
            raw = resp.choices[0].message.content or ""
            answer = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

            confidence = 0.85 if contexts else 0.6
            return SkillOutput(
                answer=answer,
                skill_name=self.name,
                confidence=confidence,
                sources=all_sources,
            )

        except Exception as exc:
            logger.error("Architecture planning skill failed: %s", exc)
            return SkillOutput(
                answer="",
                skill_name=self.name,
                confidence=0.0,
                error=str(exc),
            )
