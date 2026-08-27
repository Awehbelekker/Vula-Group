"""Confirms all 9 skills that guard against garbled LLM output (looks_degenerate) actually go
through the shared, logged/telemetered substitute_if_degenerate() (2026-08-27) rather than the
old unlogged inline pattern — a real gap found this session: none of the 9 call sites logged
the raw garbled text before substituting DEGENERATE_OUTPUT_FALLBACK, making the exact class of
incident that motivated looks_degenerate() itself (a ~1000-character '!!!!' WhatsApp reply,
2026-08-22) undiagnosable from Railway logs after the fact.

Source-inspection rather than a full run() integration test per skill — each skill's run/_loop
has a very different dependency surface to mock, and what actually matters here (confirmed by
core/llm_router.py's own thorough substitute_if_degenerate tests) is that every site was
migrated off the old inline pattern, not re-proving substitute_if_degenerate's own behavior
nine more times."""
import inspect

import pytest

SKILL_MODULES = [
    "core.skills.commerce_admin",
    "core.skills.commerce_assistant",
    "core.skills.calculations",
    "core.skills.clickup_admin",
    "core.skills.draft_admin",
    "core.skills.email_admin",
    "core.skills.finance_admin",
    "core.skills.google_admin",
    "core.skills.microsoft_admin",
]


@pytest.mark.parametrize("module_name", SKILL_MODULES)
def test_skill_uses_shared_substitute_if_degenerate(module_name):
    import importlib
    mod = importlib.import_module(module_name)
    source = inspect.getsource(mod)
    assert "substitute_if_degenerate" in source, (
        f"{module_name} no longer wired to the shared logged/telemetered guard")
    # The old inline pattern must be fully gone, not just supplemented — its whole point was
    # replacing every "if looks_degenerate(x): x = DEGENERATE_OUTPUT_FALLBACK" occurrence.
    assert "= DEGENERATE_OUTPUT_FALLBACK" not in source, (
        f"{module_name} still has the old unlogged inline substitution pattern")


@pytest.mark.parametrize("module_name", SKILL_MODULES)
def test_skill_no_longer_references_fallback_constant_directly(module_name):
    """Every one of the 9 skills now goes through substitute_if_degenerate() for both the check
    AND the substitution — none of them should still import/reference DEGENERATE_OUTPUT_FALLBACK
    directly (a leftover reference would be a dead import, or a sign the swap was only half-done)."""
    import importlib
    mod = importlib.import_module(module_name)
    source = inspect.getsource(mod)
    assert "DEGENERATE_OUTPUT_FALLBACK" not in source, (
        f"{module_name} still directly references DEGENERATE_OUTPUT_FALLBACK — should route "
        "through substitute_if_degenerate() instead")
