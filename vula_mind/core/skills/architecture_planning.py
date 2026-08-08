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
from core.prompt_safety import fence
from core.skills.base import BaseSkill, SkillInput, SkillOutput, behaviour_preamble

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
            tenant_chunks = await tenant_pipeline.query(inp.question, top_k=inp.top_k, authoritative_only=True)
            if tenant_chunks:
                contexts.append("## Your practice's knowledge\n" + "\n\n".join(
                    f"[{c.get('filename','doc')}]: {c.get('text','')[:900]}"
                    for c in tenant_chunks
                ))
                all_sources.extend([
                    {"type": "tenant_kb", "filename": c.get("filename", "?"),
                     "score": round(c.get("score", 0.0), 3), "text": c.get("text", "")[:900]}
                    for c in tenant_chunks
                ])

            # Shared SA construction standards — always consulted; they complement
            # the tenant's own data (cloud embeddings make this ~0.5s).
            try:
                training_pipeline = VulaIngestionPipeline(tenant_id=TRAINING_TENANT_ID)
                training_chunks = await training_pipeline.query(inp.question, top_k=inp.top_k, authoritative_only=True)
                if training_chunks:
                    contexts.append("## SA construction standards & rates\n" + "\n\n".join(
                        f"[{c.get('filename','standard')}]: {c.get('text','')[:900]}"
                        for c in training_chunks
                    ))
                    all_sources.extend([
                        {"type": "training_kb", "filename": c.get("filename", "?"),
                         "score": round(c.get("score", 0.0), 3), "text": c.get("text", "")[:900]}
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
            "If the question is outside SA construction practice, say so clearly.\n\n"
            + behaviour_preamble() +
            "\nCapability: users CAN send you documents (PDF, Word, Excel) and images "
            "directly on WhatsApp — you automatically file them into the knowledge base "
            "and can then answer questions about them. If asked, tell them to just attach "
            "the file here."
        )
        # Context before history, and each labelled for precedence — a real DIGG-tenant bug
        # (2026-07-27) showed the model answering from a stale, topically-unrelated exchange
        # several messages back instead of a correctly-retrieved document sitting in the same
        # prompt, because nothing told it which one to trust when they diverge.
        context_block = "\n\n---\n\n".join(contexts) if contexts else ""
        history_block = (
            f"Conversation so far (for tone/continuity only — it may be stale or about a "
            f"different topic; do not treat it as a source of facts):\n{inp.conversation_history}\n\n"
            if inp.conversation_history else ""
        )
        context_label = ("Context (authoritative - if this conflicts with the conversation "
                         "history below, trust this, not the history):")
        # Fenced (core/prompt_safety.py) — same gap as reasoning.py/standards_lookup.py/
        # calculations.py: behaviour_preamble's UNTRUSTED_CONTENT_RULE describes >>> <<<
        # delimiters that KB context previously never actually had here.
        context_prefix = f"{context_label}{fence('KB_CONTEXT', context_block)}\n\n" if context_block else ""
        user_msg = (
            f"{context_prefix}"
            f"{history_block}"
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
            if not contexts:
                # 2026-08 accuracy audit: this skill is a domain-expert advisor (SACAP/JBCC/
                # SANS knowledge), not a document-lookup skill — it can't refuse the way
                # standards_lookup.py does when nothing is retrieved. Confidence already drops,
                # but that score never reaches the WhatsApp user; make the uncertainty visible.
                answer += ("\n\n⚠️ No specific document/standard was found for this — worth "
                           "confirming against the actual source before relying on it.")
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
