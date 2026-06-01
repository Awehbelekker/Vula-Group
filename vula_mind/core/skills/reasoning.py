"""
core/skills/reasoning.py

General-purpose reasoning skill. The default fallback when no
specialised skill matches. Uses the worker model (DeepSeek R1 7B/8B
locally, or via OpenRouter on Railway) with RAG context if available.
"""
from __future__ import annotations

import logging
import re

from config import settings
from core.skills.base import BaseSkill, SkillInput, SkillOutput

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
            chunks = await pipeline.query(inp.question, top_k=4)
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
            "Reason carefully, be concise, and prioritise practical answers. "
            "Always work in ZAR for money, use SA conventions for dates and phone numbers."
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

            if settings.openrouter_api_key:
                model = f"openrouter/{settings.model_worker}"
                api_key = settings.openrouter_api_key
                api_base = "https://openrouter.ai/api/v1"
            else:
                model = f"ollama/{settings.model_worker}"
                api_key = None
                api_base = settings.ollama_base

            resp = await litellm.acompletion(
                model=model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.3,
                max_tokens=1200,
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
