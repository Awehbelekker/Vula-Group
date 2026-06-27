"""
core/skills/standards_lookup.py — look up a standard/code from the master library.

A focused, citation-first retrieval over the tenant's authoritative knowledge
(the code library + uploaded reference docs). If the standard isn't in the library
it says so honestly and tells the user to upload the official document — it never
invents clause text or values.
"""
from __future__ import annotations

import logging
import re

from core.llm_router import resolve_generation_route
from core.skills.base import BaseSkill, SkillInput, SkillOutput, behaviour_preamble

logger = logging.getLogger(__name__)


class StandardsLookupSkill(BaseSkill):
    name = "standards_lookup"
    description = "Look up a SANS/SACAP/JBCC standard or code from the practice's master code library, with citations."

    async def run(self, inp: SkillInput) -> SkillOutput:
        chunks = []
        try:
            from vula.ingestion.pipeline import VulaIngestionPipeline
            # Tenant library first, then the shared SA construction KB.
            chunks = await VulaIngestionPipeline(tenant_id=inp.tenant_id).query(
                inp.question, top_k=inp.top_k, authoritative_only=True)
            if not chunks:
                from vula.training.content import TRAINING_TENANT_ID
                chunks = await VulaIngestionPipeline(tenant_id=TRAINING_TENANT_ID).query(
                    inp.question, top_k=inp.top_k, authoritative_only=True)
        except Exception as exc:
            logger.debug("standards_lookup retrieval skipped: %s", exc)

        if not chunks:
            return SkillOutput(
                answer=("I can't find that standard in your code library yet. Add it under "
                        "Projects → code library (or send me the official document) and I'll "
                        "be able to quote and apply it precisely."),
                skill_name=self.name, confidence=0.45)

        context = "\n\n".join(
            f"[{c.get('filename','doc')}]: {c.get('text','')[:500]}" for c in chunks)
        sources = [{"type": "kb", "filename": c.get("filename", "?"),
                    "score": round(c.get("score", 0.0), 3)} for c in chunks]
        system_msg = (
            "You are Vula, looking up standards/codes for a South African practice from the "
            "provided library extracts.\n\n" + behaviour_preamble() +
            "\nAnswer ONLY from the extracts below. Quote the relevant part and cite the source "
            "file. If the extracts don't actually cover what was asked, say so and suggest "
            "uploading the official standard — do not fill the gap from memory."
        )
        history = f"\nConversation so far:\n{inp.conversation_history}\n" if inp.conversation_history else ""
        user_msg = f"Library extracts:\n{context}\n{history}\nQuestion: {inp.question}\n\nAnswer:"
        try:
            import litellm
            litellm.drop_params = True
            model, api_key, api_base = await resolve_generation_route()
            resp = await litellm.acompletion(
                model=model,
                messages=[{"role": "system", "content": system_msg},
                          {"role": "user", "content": user_msg}],
                temperature=0.2, max_tokens=inp.max_tokens, api_key=api_key, api_base=api_base)
            answer = re.sub(r"<think>.*?</think>", "", resp.choices[0].message.content or "",
                            flags=re.DOTALL).strip()
            return SkillOutput(answer=answer, skill_name=self.name, confidence=0.8, sources=sources)
        except Exception as exc:
            logger.error("standards_lookup failed: %s", exc)
            return SkillOutput(answer="", skill_name=self.name, confidence=0.0, error=str(exc))
