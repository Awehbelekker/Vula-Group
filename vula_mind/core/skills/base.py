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


def behaviour_preamble(persona: str = "") -> str:
    """Assemble the shared behaviour policy. `persona` (optional, per-tenant) sets the
    voice/style; the rest enforces integrity, honesty, reasoning, and conversation rules."""
    head = (persona.strip() + "\n\n") if persona else ""
    return head + "\n".join([ETHICS_RULES, HONESTY_RULES, REASONING_RULES, CONVERSATION_RULES])


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

    @property
    def success(self) -> bool:
        return self.error is None and bool(self.answer)


class BaseSkill(ABC):
    name: str = "base"
    description: str = ""

    @abstractmethod
    async def run(self, inp: SkillInput) -> SkillOutput:
        """Execute the skill and return a SkillOutput."""

    async def __call__(self, inp: SkillInput) -> SkillOutput:
        started = time.monotonic()
        try:
            result = await self.run(inp)
            result.latency_ms = int((time.monotonic() - started) * 1000)
            result.skill_name = self.name
            return result
        except Exception as exc:
            latency = int((time.monotonic() - started) * 1000)
            return SkillOutput(
                answer="",
                skill_name=self.name,
                confidence=0.0,
                latency_ms=latency,
                error=str(exc),
            )
