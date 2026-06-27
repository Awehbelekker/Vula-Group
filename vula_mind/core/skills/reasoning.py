"""
core/skills/reasoning.py

General-purpose reasoning skill. The default fallback when no
specialised skill matches. Uses the worker model (DeepSeek R1 7B/8B
locally, or via OpenRouter on Railway) with RAG context if available.
"""
from __future__ import annotations

import logging
import re

from core.llm_router import resolve_generation_route
from core.skills.base import BaseSkill, SkillInput, SkillOutput, CONVERSATION_RULES

logger = logging.getLogger(__name__)


class ReasoningSkill(BaseSkill):
    name = "reasoning"
    description = "General-purpose reasoning and analysis — the default skill for open questions"

    async def run(self, inp: SkillInput) -> SkillOutput:
        # Retrieve KB context if tenant has one
        kb_context = ""
        sources = []
        try:
            from vula.ingestion.pipeline import VulaIngestionPipeline
            pipeline = VulaIngestionPipeline(tenant_id=inp.tenant_id)
            chunks = await pipeline.query(inp.question, top_k=inp.top_k)
            if chunks:
                kb_context = "\n\n".join(
                    f"[{c.get('filename','doc')}]: {c.get('text','')[:400]}"
                    for c in chunks
                )
                sources = [
                    {"type": "kb", "filename": c.get("filename", "?"),
                     "score": round(c.get("score", 0.0), 3)}
                    for c in chunks
                ]
        except Exception as exc:
            logger.debug("Reasoning skill KB retrieval skipped: %s", exc)

        # Build prompt
        system_msg = (
            "You are Vula, an AI assistant for South African business and construction. "
            "Be concise and practical — answer in 1-3 short paragraphs suitable for WhatsApp. "
            "Lead with the answer, skip preamble. "
            "Always work in ZAR for money, use SA conventions for dates and phone numbers.\n\n"
            + CONVERSATION_RULES +
            "\nUsers CAN send you documents (PDF, Word, Excel) and images directly on "
            "WhatsApp — you file them into the knowledge base automatically. If asked about "
            "uploading, tell them to just attach the file in this chat."
        )
        history = (
            f"\nConversation so far:\n{inp.conversation_history}\n"
            if inp.conversation_history else ""
        )
        context_block = (
            f"\nRelevant context:\n{kb_context}\n"
            if kb_context else ""
        )
        user_msg = f"{history}{context_block}\nQuestion: {inp.question}\n\nAnswer:"

        try:
            import litellm
            litellm.drop_params = True

            model, api_key, api_base = await resolve_generation_route()

            resp = await litellm.acompletion(
                model=model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.3,
                max_tokens=inp.max_tokens,
                api_key=api_key,
                api_base=api_base,
            )
            raw = resp.choices[0].message.content or ""
            answer = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

            confidence = 0.75 if kb_context else 0.55
            return SkillOutput(
                answer=answer,
                skill_name=self.name,
                confidence=confidence,
                sources=sources,
            )

        except Exception as exc:
            logger.error("Reasoning skill failed: %s", exc)
            return SkillOutput(
                answer="",
                skill_name=self.name,
                confidence=0.0,
                error=str(exc),
            )
