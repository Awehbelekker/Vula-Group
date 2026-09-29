"""
core/skills/memory_recall.py

"Do you remember / what did we discuss" questions. Answered by the reasoning skill (knowledge
search + conversation + a written reply); this skill only keeps the routing name.

Used when: "remember", "last time", "previously", "we discussed", etc.
"""
from __future__ import annotations

import logging

from core.skills.base import BaseSkill, SkillInput, SkillOutput

logger = logging.getLogger(__name__)


class MemoryRecallSkill(BaseSkill):
    name = "memory_recall"
    description = "Recalls past conversations and learned context from the tenant knowledge base"

    async def run(self, inp: SkillInput) -> SkillOutput:
        # 2026-09-29 (Judy, digg-demo): this used to search the knowledge base and send the raw
        # hits back as the answer — "Are you ok?" got "Relevant knowledge: [conv_Church_layout…]:
        # …". The reasoning skill searches the same knowledge, reads the conversation and writes
        # the reply (or says it has nothing), with its verification and no-fabrication guards.
        from core.skills.reasoning import ReasoningSkill
        out = await ReasoningSkill().run(inp)
        out.skill_name = self.name
        return out
