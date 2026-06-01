"""
core/skills/memory_recall.py

Queries the tenant's Qdrant KB + SQLite reflection store for relevant
past context. Combines semantic search with recency-weighted scoring.

Used when: "remember", "last time", "previously", "we discussed", etc.
"""
from __future__ import annotations

import logging
from typing import List

from core.skills.base import BaseSkill, SkillInput, SkillOutput

logger = logging.getLogger(__name__)


class MemoryRecallSkill(BaseSkill):
    name = "memory_recall"
    description = "Recalls past conversations and learned context from the tenant knowledge base"

    async def run(self, inp: SkillInput) -> SkillOutput:
        chunks: List[dict] = []
        sources = []

        # 1. Semantic search across tenant's Qdrant KB
        try:
            from vula.ingestion.pipeline import VulaIngestionPipeline
            pipeline = VulaIngestionPipeline(tenant_id=inp.tenant_id)
            chunks = await pipeline.query(inp.question, top_k=5)
            for c in chunks:
                sources.append({
                    "type": "kb",
                    "filename": c.get("filename", "unknown"),
                    "score": round(c.get("score", 0.0), 3),
                    "excerpt": c.get("text", "")[:200],
                })
        except Exception as exc:
            logger.warning("Memory recall KB search failed: %s", exc)

        # 2. Reflection store — routing hints + past outcomes
        reflection_context = ""
        try:
            from core.memory.reflection import ReflectionAgent
            agent = ReflectionAgent()
            hints = agent.get_routing_hints(inp.question)
            if hints:
                reflection_context = "\n".join(
                    f"- Previously: {h.get('skill','?')} worked well ({h.get('score',0):.0%}) "
                    f"for '{h.get('goal_preview','?')}'"
                    for h in hints[:3]
                )
        except Exception:
            pass

        if not chunks and not reflection_context:
            return SkillOutput(
                answer="I don't have any stored memory relevant to this question yet.",
                skill_name=self.name,
                confidence=0.1,
            )

        # Build context string
        context_parts = []
        if chunks:
            context_parts.append("Relevant knowledge:\n" + "\n\n".join(
                f"[{c.get('filename','?')}]: {c.get('text','')[:400]}" for c in chunks
            ))
        if reflection_context:
            context_parts.append(f"Past experience:\n{reflection_context}")

        context = "\n\n".join(context_parts)
        confidence = min(1.0, len(chunks) * 0.18 + 0.1)

        return SkillOutput(
            answer=context,
            skill_name=self.name,
            confidence=round(confidence, 2),
            sources=sources,
        )
