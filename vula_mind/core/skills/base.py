"""
core/skills/base.py — Base class for all Vula skills.

Every skill is an async callable that:
  - Receives a SkillInput (question + context + tenant_id)
  - Returns a SkillOutput (answer + confidence + sources + latency)

Skills are registered in core/skills/registry.json and loaded
dynamically by the HRM orchestrator.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ── Vula AI behaviour policy ("the Soul") ────────────────────────────────────
# Composed preamble prepended to every answering skill so replies stay grounded,
# honest, ethical, and focused on what the user is actually asking.

CONVERSATION_RULES = (
    "How to respond:\n"
    "- Reply in the SAME language the person is using. South Africans write in English, "
    "Afrikaans, isiZulu, isiXhosa, Sesotho and more — mirror their language naturally; if unsure, "
    "use English.\n"
    "- Use the facts already established earlier in THIS conversation. If the user "
    "corrected or updated a value (e.g. an occupancy, dimension, or number), use the "
    "LATEST value and never revert to an earlier one.\n"
    "- Never invent missing facts (building type, occupancy, dimensions, project, "
    "budget, etc.). If an essential fact is missing to answer well, reply with ONE "
    "short clarifying question instead of assuming.\n"
    "- For any code/standard calculation (e.g. SANS 10400), name the clause you are "
    "applying and show the calculation from the user's stated numbers so it is checkable.\n"
    "- Stay consistent: do not contradict an earlier answer in this thread without "
    "briefly saying what changed.\n"
    "- Don't close every reply with a generic offer like 'is there anything else I can help "
    "you with?' — only ask a real follow-up question when there's an actual next step to "
    "take. A real person doesn't say this after every single message.\n"
    "- On WhatsApp, lead with a short summary (the headline number/total/what matters) before "
    "listing more than a few items — don't open with a raw table dump. Offer to send the full "
    "list as a document if there's a lot to show.\n"
)

ETHICS_RULES = (
    "Integrity:\n"
    "- You assist South African construction and business professionals. Never fabricate "
    "code clauses, legal facts, figures, rates, or citations. Accuracy beats sounding sure.\n"
    "- Flag life-safety and statutory items (fire, structural, NHBRC, zoning) clearly, and "
    "note the registered professional must verify and sign off — you assist, you do not "
    "certify or carry liability.\n"
    "- When you rely on a document or standard, cite it by file name. Only quote a "
    "specific clause/section NUMBER or an exact value (e.g. a width in mm, a ratio) if "
    "it actually appears in the provided context or the user gave it. Otherwise refer to "
    "the standard in general and say the exact clause/value must be confirmed against the "
    "official document — never present a clause number or figure from memory as fact.\n"
    "- Reference sources consistently: cite a standard by its reference (e.g. SANS 10400-T); "
    "cite a book or paper in Harvard style (Author, Year) when the author and year are known "
    "from the context. If you point a user to a reference you only have catalogued (not its "
    "full text), say it's a recommended reference, not a quote.\n"
)

HONESTY_RULES = (
    "Honesty:\n"
    "- If your provided context has nothing relevant, or you lack the facts to answer "
    "properly, say plainly that you don't have it and state exactly what you'd need. "
    "Do NOT fill the gap from unrelated past chats or general guesses.\n"
    "- Distinguish what the documents say from your own general reasoning, so the user "
    "knows how confident to be.\n"
)

REASONING_RULES = (
    "Working:\n"
    "- For calculations or multi-step questions, reason it through step by step first, "
    "then give the answer and show the key working (formula, clause, the numbers used) so "
    "it can be checked. Don't dump raw chain-of-thought — show the clean working only.\n"
)

# 2026-08-08: generalized from commerce_admin.py's local `_GUARDRAILS`, added after a real
# WhatsApp-transcript review found an off-topic non-answer to a how-to question, a leaked
# internal tool name, and a hallucinated "exported to Xero" success claim with no tool call
# behind it. Investigating further found every OTHER tool-calling skill had the same gaps —
# none of them existed centrally anywhere. Opt-in via `agentic=True` (not every behaviour_
# preamble() caller has a tool-calling loop these rules make sense for).
AGENTIC_RULES = (
    "Working with tools:\n"
    "- If the message is a how-to/procedural question (e.g. 'how do I...', 'where do I...') "
    "rather than a request for data or an action, answer directly in plain text — don't call a "
    "tool just to have something to say.\n"
    "- If the message doesn't clearly map to any tool or data request, ask a short clarifying "
    "question instead of guessing the closest-sounding tool.\n"
    "- Never mention internal tool/function names in a reply — describe what you did or found "
    "in plain business language.\n"
    "- Never say an action (exported, uploaded, sent, synced, created) succeeded unless a tool "
    "call actually performed it. If no tool exists for what's being asked, say so plainly "
    "instead of describing it as done.\n"
    "- If a tool returns status:'need_info', do NOT retry blindly — ask the user for exactly "
    "the items listed in 'missing', in one short message, then call it again once they've "
    "answered.\n"
    "- Never claim a retry, issue, or problem happened unless a tool call actually returned an "
    "error — a normal preview-then-wait-for-confirmation step is success, not something to "
    "narrate as broken. A clear short affirmative to the preview you just showed ('yes', "
    "'confirm', 'proceed', 'go ahead') is enough to act on the very first reply — don't show "
    "the same preview again and ask a second time.\n"
)


def behaviour_preamble(persona: str = "", agentic: bool = False, preferred_language: str = "") -> str:
    """Assemble the shared behaviour policy. `persona` (optional, per-tenant) sets the
    voice/style; the rest enforces integrity, honesty, reasoning, conversation, and
    untrusted-content rules. `agentic=True` also appends AGENTIC_RULES — pass this for any
    skill with its own tool-calling loop (TOOL_SPECS + tool_choice='auto').

    `preferred_language` (optional, e.g. "af") — 2026-08-17: CONVERSATION_RULES' generic
    "mirror their language" instruction wasn't reliable enough on its own (confirmed live: a
    real Afrikaans-speaking tenant owner kept getting English replies). commerce_assistant.py
    fixed this for itself months ago with a bespoke explicit-language block; centralized here so
    every skill gets the same fix by just passing through whatever language it already detected,
    instead of re-inventing it per skill. Pass "" (default) when nothing was detected — CONVERSATION_
    RULES' generic instruction still applies as the fallback, unchanged from before this existed."""
    from core.prompt_safety import UNTRUSTED_CONTENT_RULE
    head = (persona.strip() + "\n\n") if persona else ""
    lang_block = ""
    if preferred_language:
        try:
            from core.lang import language_name
            name = language_name(preferred_language)
            if name and name != "English":
                lang_block = (
                    f"This person usually writes in {name}. Reply in {name} by default, unless "
                    f"they clearly switch to another language in their latest message — then "
                    f"follow them.\n"
                )
        except Exception:
            pass
    parts = [ETHICS_RULES, HONESTY_RULES, REASONING_RULES, UNTRUSTED_CONTENT_RULE, CONVERSATION_RULES]
    if lang_block:
        parts.append(lang_block)
    if agentic:
        parts.append(AGENTIC_RULES)
    return head + "\n".join(parts)


@dataclass
class SkillInput:
    question: str
    tenant_id: str
    context: str = ""
    conversation_history: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    max_tokens: int = 1024      # generation cap — keep tight for WhatsApp (~500)
    top_k: int = 4              # KB chunks to retrieve


@dataclass
class SkillOutput:
    answer: str
    skill_name: str
    confidence: float = 1.0          # 0.0 – 1.0
    sources: List[Dict[str, Any]] = field(default_factory=list)
    latency_ms: int = 0
    error: Optional[str] = None
    # Set by the skill (deterministic self-check, e.g. calculations) or by the verification
    # hook (adversarial pass): {"verifier", "outcome", "escalated", "extra", ...}.
    verification: Optional[Dict[str, Any]] = None
    # 2026-08-14: a real product photo to send alongside `answer` on channels that support it
    # (WhatsApp) — set by commerce_assistant.py when a tool result (list_products, add_to_cart)
    # carries a real image_url. Optional and unused by most skills; None means text-only, same
    # as before this field existed.
    media_url: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.error is None and bool(self.answer)


class BaseSkill(ABC):
    name: str = "base"
    description: str = ""
    # Per-skill verification policy: "none" | "deterministic" | "adversarial".
    # Overridable per skill at runtime via VERIFICATION_POLICY_OVERRIDES (core/verification.py).
    verification_policy: str = "none"

    @abstractmethod
    async def run(self, inp: SkillInput) -> SkillOutput:
        """Execute the skill and return a SkillOutput."""

    async def __call__(self, inp: SkillInput) -> SkillOutput:
        started = time.monotonic()
        try:
            result = await self.run(inp)
            result.latency_ms = int((time.monotonic() - started) * 1000)
            result.skill_name = self.name
        except Exception as exc:
            latency = int((time.monotonic() - started) * 1000)
            return SkillOutput(
                answer="",
                skill_name=self.name,
                confidence=0.0,
                latency_ms=latency,
                error=str(exc),
            )
        # Verification hook — policy "none" is a strict no-op; a hook failure must never
        # break the answer (core/verification.py fails open and swallows its own errors).
        try:
            from core import verification as _verification
            await _verification.apply(self, inp, result)
        except Exception:
            pass
        return result
