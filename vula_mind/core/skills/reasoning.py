"""
core/skills/reasoning.py

General-purpose reasoning skill. The default fallback when no
specialised skill matches. Uses the worker model (DeepSeek R1 7B/8B
locally, or via OpenRouter on Railway) with RAG context if available.
"""
from __future__ import annotations

import logging
import re
import time

from core.llm_router import resolve_generation_route
from core.prompt_safety import fence
from core.skills.base import (
    BaseSkill, SkillInput, SkillOutput, behaviour_preamble, format_kb_chunks,
    looks_like_tenant_data_question,
)
from core.verification import NO_GROUNDING_CAVEAT, WEB_FALLBACK_CAVEAT

logger = logging.getLogger(__name__)


# 2026-08-18: this skill's hard-decline guard was added after pulling DIGG's real chat history
# and finding a confirmed, zero-backing hallucination — "Logg as expense" got "I've logged the
# screen bricks... for R70,400.00" with no such expense ever created (this skill has no tools;
# it cannot actually log anything). A question that names an invoice/expense/BOQ/project/
# payment/total etc. with no matching KB document is asking about THIS tenant's own real
# records — there is no legitimate "general knowledge" answer to that, unlike a standards/code
# question, which the model can reasonably speak to from training data. 2026-08-24: the marker
# regex + this guard's shape were centralized into core/skills/base.py (English-only was a real
# gap given Vula's multi-language promise, and architecture_planning.py needed the same guard
# but couldn't reuse a reasoning.py-local function) — see looks_like_tenant_data_question there.
_LOCAL_TIMEOUT_S = 20.0
_CLOUD_TIMEOUT_S = 30.0

# 2026-09-17: real DIGG transcript — "can I colour a cast iron fireplace?" had no KB match, so
# this skill guessed from training data and answered confidently wrong. web_search.py already
# exists (free, DuckDuckGo-backed) and is already used as a fallback-when-empty pattern in
# commerce_assistant.py's _exec_research_product — this borrows the same pattern here. Bounded
# independently of web_search.py's own internal timeouts (it does up to 3 sequential 12s page
# fetches + its own LLM call) so a slow web search fails open into today's plain-caveat path
# rather than blocking the reply. This whole reply path already runs backgrounded off the
# WhatsApp webhook ACK (_run_bg in vula/api/whatsapp.py), so the cost is WhatsApp reply
# latency, not a webhook retry risk.
_WEB_FALLBACK_TIMEOUT_S = 25.0
# web_search.py's confidence scale: ~0.7 = real synthesis over fetched page text (useful
# grounding), 0.4 = DDG hits but no page text fetched (the "answer" is just a bulleted list of
# link titles/URLs — useless as prose to reason over). 0.5 admits only the real synthesized
# case; deliberately stricter than commerce_assistant.py's >= 0.3 bar, which returns the raw
# web result as ITS final answer rather than feeding it into a second generation pass.
_WEB_FALLBACK_MIN_CONFIDENCE = 0.5


class ReasoningSkill(BaseSkill):
    name = "reasoning"
    description = "General-purpose reasoning and analysis — the default skill for open questions"
    # 2026-08-18 accuracy audit: core/verification.py's adversarial checker was explicitly
    # written anticipating this skill as a groundable "kb" source, but the one-line activation
    # was never done — this is the default fallback skill for DIGG's whole general chat, so it
    # had zero fact-checking while commerce_admin.py/finance_admin.py already had it.
    verification_policy = "adversarial"

    async def run(self, inp: SkillInput) -> SkillOutput:
        # Retrieve KB context if tenant has one
        kb_context = ""
        sources = []
        try:
            from vula.ingestion.pipeline import VulaIngestionPipeline
            pipeline = VulaIngestionPipeline(tenant_id=inp.tenant_id)
            chunks = await pipeline.query(inp.question, top_k=inp.top_k, authoritative_only=True)
            if chunks:
                kb_context = await format_kb_chunks(inp.tenant_id, chunks)
                sources = [
                    {"type": "kb", "filename": c.get("filename", "?"),
                     "score": round(c.get("score", 0.0), 3), "text": c.get("text", "")[:900]}
                    for c in chunks
                ]
        except Exception as exc:
            logger.debug("Reasoning skill KB retrieval skipped: %s", exc)

        # 2026-08-18: a question naming an invoice/expense/BOQ/project/payment etc. with no
        # matching document is asking about THIS tenant's own real records — this skill has no
        # tools, so it can never actually check or perform anything, and there is no legitimate
        # "general knowledge" answer to fall back on (unlike a standards/code question). Decline
        # BEFORE spending an LLM call, rather than generating an answer and hoping the prompt
        # stops it from guessing — this is exactly the shape of the confirmed R70,400 "logged"
        # fabrication found in DIGG's real chat history, which had zero backing tool call.
        if not kb_context and looks_like_tenant_data_question(inp.question):
            return SkillOutput(
                answer=("I don't have a document on file for that, so I don't want to guess at "
                        "a figure or confirm something happened without seeing it. Could you "
                        "attach the relevant document (invoice, receipt, BOQ, etc.) or tell me "
                        "more so I can find it?"),
                skill_name=self.name,
                confidence=0.3,
                sources=sources,
            )

        # 2026-09-17: before ever guessing OR spending a web search, check Vula's own curated
        # shared knowledge — general SA construction (vula_training) and general SA small-
        # business (business_basics) content, already proven safe and already consulted by
        # architecture_planning.py, just never wired into this skill despite it being the
        # default fallback for DIGG's whole general chat. Free (no LLM call), ~0.5s, zero
        # privacy risk (developer-authored, not tenant content) — tried before web search so a
        # hit here skips the web call entirely.
        if not kb_context:
            try:
                from vula.ingestion.pipeline import VulaIngestionPipeline as _Pipeline
                from vula.training.content import TRAINING_TENANT_ID
                from vula.training.business_content import BUSINESS_TRAINING_TENANT_ID
                from vula.training.network import NETWORK_TENANT_ID
                for shared_id, label in (
                    (TRAINING_TENANT_ID, "SA construction standards & rates"),
                    (BUSINESS_TRAINING_TENANT_ID, "General SA small-business knowledge"),
                    # Consumption is open to every tenant — only CONTRIBUTING into this
                    # collection is gated on the source tenant's own opt-in (see
                    # vula/training/network.py's docstring and vula/api/master.py's promote
                    # endpoint). A tenant reading this never needs to have opted in itself.
                    (NETWORK_TENANT_ID, "Shared knowledge from other Vula businesses (opted in)"),
                ):
                    shared_pipeline = _Pipeline(tenant_id=shared_id)
                    shared_chunks = await shared_pipeline.query(
                        inp.question, top_k=inp.top_k, authoritative_only=True)
                    if shared_chunks:
                        kb_context = f"## {label}\n" + "\n\n".join(
                            f"[{c.get('filename','doc')}]: {c.get('text','')[:900]}"
                            for c in shared_chunks)
                        sources = [
                            {"type": "training_kb", "filename": c.get("filename", "?"),
                             "score": round(c.get("score", 0.0), 3), "text": c.get("text", "")[:900]}
                            for c in shared_chunks
                        ]
                        break
            except Exception as exc:
                logger.debug("Reasoning shared-KB check skipped: %s", exc)

        # 2026-09-17: no KB match (tenant OR shared), and not asking about the tenant's own
        # records — a genuine general-knowledge question the model would otherwise just guess
        # at from training data. Try web search first; a real citation beats a guess. Fails
        # open (empty web_context) on any error/timeout/low-confidence result, falling through
        # to the existing plain-caveat behaviour below.
        web_context = ""
        web_sources: list = []
        if not kb_context:
            import os as _os
            if _os.environ.get("REASONING_WEB_FALLBACK_DISABLED", "false").lower() != "true":
                try:
                    import asyncio as _asyncio
                    from core.skills.loader import get_skill  # lazy: loader imports this module
                    web_result = await _asyncio.wait_for(
                        get_skill("web_search")(
                            SkillInput(question=inp.question, tenant_id=inp.tenant_id)),
                        timeout=_WEB_FALLBACK_TIMEOUT_S)
                    if web_result.success and web_result.confidence >= _WEB_FALLBACK_MIN_CONFIDENCE:
                        web_context = web_result.answer
                        # raw confidence (not the flattened 0.6 reply-confidence below) is what
                        # Part E's automated research-promotion gate reads in verification.py
                        web_sources = [{"type": "web", "text": web_context[:900],
                                        "confidence": web_result.confidence}]
                except Exception as exc:
                    logger.debug("Reasoning web fallback skipped: %s", exc)

        # Build prompt
        # 2026-09-17: added the "doesn't actually answer the specific question" guard below
        # after a real DIGG transcript — "can I colour a cast iron fireplace?" got answered
        # "no" by citing a retrieved Canal West HOA rule about flue MATERIAL (steel vs stainless
        # steel), a different question with no rule on colour/finish anywhere in the context.
        # Being on-topic isn't the same as answering what was asked.
        system_msg = (
            "You are Vula, an AI assistant for South African business and construction. "
            "Be concise and practical — answer in 1-3 short paragraphs suitable for WhatsApp. "
            "Lead with the answer, skip preamble. "
            "Always work in ZAR for money, use SA conventions for dates and phone numbers.\n\n"
            "You CANNOT perform actions yourself in this chat — you have no tools here, so you "
            "can't log an expense, create a document, send anything, or save anything. If "
            "someone asks you to DO something, say plainly you can't do that directly here and "
            "tell them the real next step (e.g. 'attach the receipt photo and it'll be logged "
            "automatically', or 'ask an admin to do that'). NEVER describe an action as done "
            "unless the document context below actually shows it already happened — never "
            "invent a confirmation, an amount, or an ID.\n\n"
            "If the document context below is about the same property/topic but doesn't "
            "actually state a rule or fact that answers the SPECIFIC question asked, say so "
            "plainly ('I found a rule on X but nothing on Y — worth checking directly') "
            "instead of drawing a conclusion from a different, merely nearby clause. A related "
            "document being present is not the same as it answering what was asked.\n\n"
            + behaviour_preamble(preferred_language=inp.metadata.get("preferred_language", ""),
                                 caller_name=inp.metadata.get("caller_name") or "",
                                 caller_role=inp.metadata.get("caller_role") or "") +
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
        # 2026-09-17: this wording is load-bearing, not boilerplate — shaped against a real
        # comparison. Judy (DIGG's owner) got a wrong flat "no" from Vula, then pasted back a
        # detailed answer she'd researched herself: several concrete brand/product options, and
        # a hedge on the ONE specific thing that was actually uncertain (stock/pricing), not a
        # blanket disclaimer. That's the bar this instructs the model toward.
        web_block = (
            f"\nLive web search results (NOT one of this business's own documents — say so if "
            f"you rely on it, and only cite a source URL raw, never as a markdown link). Give "
            f"concrete options from what was actually found (brands/products/suppliers), not "
            f"one flat verdict — and if something specific in the results is uncertain (stock, "
            f"current price, availability), say THAT plainly instead of a generic disclaimer:"
            f"{fence('WEB_CONTEXT', web_context)}"
            if web_context else ""
        )
        user_msg = f"{context_block}{web_block}{history}\nQuestion: {inp.question}\n\nAnswer:"

        started = time.monotonic()
        try:
            import asyncio
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

            async def _complete(m, k, b, timeout_s):
                # 2026-08-18: no timeout existed here at all — same class of gap already fixed
                # for GLM-OCR. resolve_generation_route()'s local-first check is only a cheap
                # 1.5s health ping, not a real speed measurement; if the shared local GPU is
                # slow under load, this used to just wait however long that took. A timed-out
                # local call now escalates to cloud the same way an unreliable answer already
                # did, instead of leaving the customer waiting indefinitely.
                return await asyncio.wait_for(litellm.acompletion(
                    model=m, messages=_msgs, temperature=0.3,
                    max_tokens=inp.max_tokens, api_key=k, api_base=b,
                    # logprobs is only meaningful for the local Ollama path (see the
                    # looks_unreliable call below) — requested unconditionally since
                    # litellm.drop_params silently discards it where unsupported
                    # (cloud routes, older Ollama builds) rather than erroring.
                    logprobs=True, top_logprobs=1), timeout=timeout_s)

            escalated = False
            try:
                resp = await _complete(model, api_key, api_base, _LOCAL_TIMEOUT_S)
                raw = resp.choices[0].message.content or ""
                answer = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

                # Requirement (b): if the local answer is empty/refusal/low-confidence, escalate
                # to cloud and log why. logprob_conf is None when the backend returned no
                # logprobs (2026-08: previously always None — no caller requested them — so this
                # branch of looks_unreliable was dead code; now wired via compute_confidence).
                unreliable = False
                if model.startswith("ollama/"):
                    logprob_conf = compute_confidence(resp)
                    unreliable = looks_unreliable(
                        answer, confidence=logprob_conf,
                        confidence_threshold=settings.local_confidence_threshold)
            except asyncio.TimeoutError:
                logger.warning("Reasoning local completion timed out after %.0fs — escalating",
                               _LOCAL_TIMEOUT_S)
                answer, unreliable = "", True

            if unreliable:
                esc = escalate_to_cloud("local_unreliable", run_id=run_id, task_type="reasoning")
                if esc:
                    model, api_key, api_base = esc
                    resp = await _complete(model, api_key, api_base, _CLOUD_TIMEOUT_S)
                    raw = resp.choices[0].message.content or ""
                    answer = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
                    escalated = True

            # 2026-08-18: the true "asking about our own records with nothing to ground it"
            # case now short-circuits before generation (above) — what reaches here without KB
            # is a genuinely general question (e.g. a standards/code lookup), which the model can
            # reasonably speak to from training data. Confidence is still marked down from the
            # KB-grounded case so it's visibly less certain, without being pushed low enough to
            # noisily human-escalate every ordinary general-knowledge question.
            # 2026-09-17: web_context (a real citation, third-party not tenant-authoritative)
            # sits between those two — 0.6, above escalation.py's should_escalate floor (0.4),
            # same as the plain no-grounding case already was.
            confidence = 0.75 if kb_context else (0.6 if web_context else 0.5)
            if not kb_context and not web_context:
                answer += NO_GROUNDING_CAVEAT
            elif web_context:
                answer += WEB_FALLBACK_CAVEAT
            if web_sources:
                sources = sources or web_sources

            latency_ms = int((time.monotonic() - started) * 1000)
            try:
                from core.reasoning_telemetry import emit as _emit
                _emit(system="vula-reasoning", task="reasoning_reply", tenant_id=inp.tenant_id,
                     outcome="ok", escalated=escalated,
                     extra={"latency_ms": latency_ms, "confidence": confidence,
                            "had_kb": bool(kb_context), "had_web": bool(web_context)})
            except Exception:
                pass

            return SkillOutput(
                answer=answer,
                skill_name=self.name,
                confidence=confidence,
                sources=sources,
            )

        except Exception as exc:
            logger.error("Reasoning skill failed: %s", exc)
            try:
                from core.reasoning_telemetry import emit as _emit
                _emit(system="vula-reasoning", task="reasoning_reply", tenant_id=inp.tenant_id,
                     outcome="error", reason=str(exc)[:200],
                     extra={"latency_ms": int((time.monotonic() - started) * 1000)})
            except Exception:
                pass
            return SkillOutput(
                answer="",
                skill_name=self.name,
                confidence=0.0,
                error=str(exc),
            )
