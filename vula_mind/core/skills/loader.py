"""
core/skills/loader.py

Maps skill names → skill instances. The HRM orchestrator decides WHICH
skill to use; this loader provides the actual callable implementation.

Skills that have a concrete implementation are registered here. Skills
listed in registry.json but not yet implemented fall back to ReasoningSkill.
"""
from __future__ import annotations

import logging
from typing import Dict

from core.skills.base import BaseSkill
from core.skills.reasoning import ReasoningSkill
from core.skills.memory_recall import MemoryRecallSkill
from core.skills.web_search import WebSearchSkill
from core.skills.file_parse import FileParseSkill
from core.skills.architecture_planning import ArchitecturePlanningSkill
from core.skills.commerce_assistant import CommerceAssistantSkill
from core.skills.commerce_admin import CommerceAdminSkill
from core.skills.clickup_admin import ClickUpAdminSkill
from core.skills.calculations import CalculationsSkill
from core.skills.standards_lookup import StandardsLookupSkill
from core.skills.google_admin import GoogleAdminSkill

logger = logging.getLogger(__name__)

# Singleton skill instances (stateless, safe to reuse)
_SKILLS: Dict[str, BaseSkill] = {
    "reasoning": ReasoningSkill(),
    "memory_recall": MemoryRecallSkill(),
    "web_search": WebSearchSkill(),
    "file_parse": FileParseSkill(),
    "architecture_planning": ArchitecturePlanningSkill(),
    "commerce_assistant": CommerceAssistantSkill(),
    "commerce_admin": CommerceAdminSkill(),
    "clickup_admin": ClickUpAdminSkill(),
    "calculations": CalculationsSkill(),
    "standards_lookup": StandardsLookupSkill(),
    "google_admin": GoogleAdminSkill(),
}

# Skills declared in registry.json but not yet implemented — route to reasoning
_FALLBACK_ALIASES = {
    "code_execution": "reasoning",
    "image_analysis": "file_parse",       # file_parse handles image OCR via pipeline
    "financial_reasoning": "architecture_planning",  # construction $ → arch skill
}


def get_skill(name: str) -> BaseSkill:
    """Return the skill instance for a name, falling back to reasoning."""
    if name in _SKILLS:
        return _SKILLS[name]
    alias = _FALLBACK_ALIASES.get(name)
    if alias and alias in _SKILLS:
        logger.debug("Skill '%s' not implemented — using '%s'", name, alias)
        return _SKILLS[alias]
    logger.debug("Skill '%s' unknown — falling back to reasoning", name)
    return _SKILLS["reasoning"]


def available_skills() -> list[str]:
    """List all directly-implemented skill names."""
    return list(_SKILLS.keys())
