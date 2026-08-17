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
from core.prompt_safety import fence
from core.skills.base import BaseSkill, SkillInput, SkillOutput, behaviour_preamble

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
            chunks = await pipeline.query(inp.question, top_k=inp.top_k, authoritative_only=True)
            if chunks:
                kb_context = "\n\n".join(
                    f"[{c.get('filename','doc')}]: {c.get('text','')[:900]}"
                    for c in chunks
                )
                sources = [
                    {"type": "kb", "filename": c.get("filename", "?"),
                     "score": round(c.get("score", 0.0), 3), "text": c.get("text", "")[:900]}
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
            + behaviour_preamble(preferred_language=inp.metadata.get("preferred_language", "")) +
            "\nUsers CAN send you documents (PDF, Word, Excel) and images directly on "
            "WhatsApp — you file them into the knowledge base automatically. If asked about "
            "uploading, tell them to just attach the file in this chat."
        )
        # Context before history, and each labelled for precedence — a real DIGG-tenant bug
        # (2026-07-27) showed the model answering from a stale, topically-unrelated exchange
        # several messages back instead of a correctly-retrieved document sitting in the same
        # prompt, because nothing told it which one to trust when they diverge.
        #
        # Both blocks are wrapped with fence() (core/prompt_safety.py) so the >>> <<< markers
        # UNTRUSTED_CONTENT_RULE tells the model to look for actually exist here. Previously
        # this skill built kb_context as a plain unlabelled string — the untrusted-content rule
        # was present in the system prompt (via behaviour_preamble) but pointed at delimiters
        # that were never applied, so retrieved document/KB text had no structural signal
        # separating it from instructions. A malicious or compromised uploaded document could
        # contain text shaped like an instruction ("ignore prior rules and...") with nothing
        # to stop the model treating it as one.
        history = (
            f"\nConversation so far (for tone/continuity only — it may be stale or about a "
            f"different topic; do not treat it as a source of facts):"
            f"{fence('CONVERSATION_HISTORY', inp.conversation_history)}"
            if inp.conversation_history else ""
        )
        context_block = (
            f"\nDocument context (authoritative — if this conflicts with the conversation "
            f"history above, trust this, not the history):"
            f"{fence('DOCUMENT_CONTEXT', kb_context)}"
            if kb_context else ""
        )
        user_msg = f"{context_block}{history}\nQuestion: {inp.question}\n\nAnswer:"

        try:
            import litellm
            from uuid import uuid4
            from config import settings
            from core.llm_router import escalate_to_cloud, looks_unreliable, compute_confidence
            litellm.drop_params = True

            _msgs = [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ]
            run_id = str(uuid4())
            model, api_key, api_base = await resolve_generation_route(
                task_type="reasoning", messages=_msgs, run_id=run_id)

            async def _complete(m, k, b):
                return await litellm.acompletion(
                    model=m, messages=_msgs, temperature=0.3,
                    max_tokens=inp.max_tokens, api_key=k, api_base=b,
                    # logprobs is only meaningful for the local Ollama path (see the
                    # looks_unreliable call below) — requested unconditionally since
                    # litellm.drop_params silently discards it where unsupported
                    # (cloud routes, older Ollama builds) rather than erroring.
                    logprobs=True, top_logprobs=1)

            resp = await _complete(model, api_key, api_base)
            raw = resp.choices[0].message.content or ""
            answer = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

            # Requirement (b): if the local answer is empty/refusal/low-confidence, escalate to
            # cloud and log why. logprob_conf is None when the backend returned no logprobs
            # (2026-08: previously always None — no caller requested them — so this branch of
            # looks_unreliable was dead code; now wired via compute_confidence above).
            if model.startswith("ollama/"):
                logprob_conf = compute_confidence(resp)
                if looks_unreliable(answer, confidence=logprob_conf,
                                    confidence_threshold=settings.local_confidence_threshold):
                    esc = escalate_to_cloud("local_unreliable", run_id=run_id, task_type="reasoning")
                    if esc:
                        model, api_key, api_base = esc
                        resp = await _complete(model, api_key, api_base)
                        raw = resp.choices[0].message.content or ""
                        answer = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

            confidence = 0.75 if kb_context else 0.55
            if not kb_context:
                # 2026-08 accuracy audit: confidence already dropped for a no-KB answer, but
                # that score never reaches the WhatsApp user — this is the default fallback
                # skill so it must still answer from general knowledge (can't refuse the way
                # standards_lookup.py does), but the uncertainty should be visible, not just
                # scored internally.
                answer += ("\n\n⚠️ I couldn't find a specific document on this — worth "
                           "double-checking anything critical.")
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
